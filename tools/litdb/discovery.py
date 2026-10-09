"""Multiple local recall routes followed by bounded Kev relevance decisions."""
from __future__ import annotations

import json
import math
import os
import time

from . import keyword_search
from .kev_client import KevClient
from .search import (SearchError, ZvecBridge, catalog_signature, evidence_excerpt,
                     index_lock, lookup_connection, matches_scope, matching_editions, read_json,
                     scope_clause, search, search_home, validate_query)


def _strings(values, name, *, count, length, total):
    if not isinstance(values, list) or len(values)>count or any(
            not isinstance(s,str) or not s.strip() or len(s)>length or '\x00' in s for s in values):
        raise ValueError(f'{name} must contain at most {count} nonempty strings of at most {length} characters.')
    result=list(dict.fromkeys(s.strip() for s in values))
    if sum(map(len,result))>total:raise ValueError(f'{name} exceeds {total} characters.')
    return result


def validate_options(options):
    base=validate_query(options)
    variants=_strings(options.get('retrieval_queries',[]),'retrieval_queries',count=5,length=6000,total=24000)
    keywords=_strings(options.get('keywords',[]),'keywords',count=32,length=500,total=6000)
    retriever=options.get('retriever','combined')
    if retriever not in {'combined','zvec','keyword'}:raise ValueError('Unknown discovery retriever.')
    if retriever!='zvec' and not keywords:
        raise ValueError('Provide --keyword literal phrases for combined/keyword retrieval; choose technical terms and synonyms for the topic.')
    limit=options.get('candidate_limit',50)
    if isinstance(limit,bool) or not isinstance(limit,int) or not base['limit']<=limit<=500:
        raise ValueError('candidate_limit must be an integer between the result limit and 500.')
    threshold=options.get('unrelated_threshold',0.6)
    if isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or not 0.5<=threshold<=1:
        raise ValueError('unrelated_threshold must be between 0.5 and 1.')
    return dict(**base,retriever=retriever,keywords=keywords,candidate_limit=limit,
                retrieval_queries=list(dict.fromkeys([base['query'],*variants])),
                unrelated_threshold=float(threshold),
                kev_url=options.get('kev_url') or os.environ.get('LITDB_KEV_URL','http://127.0.0.1:8019'),
                kev_timeout=options.get('kev_timeout',180))


def discover(paths, options, bridge=None, client=None):
    options=validate_options(options)
    # Validate the endpoint before any potentially expensive index work.
    client=client or KevClient(options['kev_url'],timeout=options['kev_timeout'])
    started=time.perf_counter();home=search_home(paths);timings={};warnings=[]
    candidates={};route_counts={};eligible=0
    own_bridge=bridge is None

    def add(paper, provenance, weight):
        identity=paper['id']
        if identity not in candidates:
            candidates[identity]={**paper,'retrieval_score':0.0,'retrieved_by':[]}
        item=candidates[identity]
        item['retrieval_score']+=weight/(60+provenance['rank'])
        item['retrieved_by'].append(provenance)

    try:
        # All routes see one lookup snapshot, including alternate-edition filters.
        with index_lock(home,False):
            state=read_json(home/'state.json')
            if not state.get('ready'):raise SearchError('搜索索引尚未就绪，请先运行 litdb search index。')
            if state.get('catalog_signature')!=catalog_signature(paths):
                warnings.append('数据库已更新；本次使用上次索引快照，请刷新索引。')
            with lookup_connection(home) as lookup:
                valid={r[0] for r in lookup.execute('SELECT DISTINCT venue FROM editions')}
                if set(options['venues'])-valid:raise ValueError('未知 venue。')
                clause,args=scope_clause(options)
                eligible=lookup.execute(f'SELECT count(DISTINCT e.paper_id) FROM editions e WHERE {clause}',args).fetchone()[0]
                if eligible and options['retriever'] in {'combined','zvec'}:
                    t=time.perf_counter()
                    bridge=bridge or ZvecBridge(home,state['model'])
                    variants=options['retrieval_queries']
                    for index,query in enumerate(variants):
                        result=search(paths,{**options,'query':query,'mode':'hybrid'},bridge,
                                      candidate_limit=options['candidate_limit'])
                        route_counts[f'zvec:{index}']=len(result['results'])
                        for rank,paper in enumerate(result['results'],1):
                            add(paper,dict(route='zvec',query=query,rank=rank,
                                          score=paper['score'],matched_by=paper.get('matched_by')),1/len(variants))
                    timings['zvec_ms']=round((time.perf_counter()-t)*1000,2)
                if eligible and options['retriever'] in {'combined','keyword'}:
                    t=time.perf_counter()
                    try:
                        hits=keyword_search.query(home,options['keywords'],options,options['candidate_limit'])
                    except keyword_search.KeywordSearchError as exc:
                        raise SearchError(str(exc)) from exc
                    route_counts['keyword']=len(hits)
                    for rank,hit in enumerate(hits,1):
                        row=lookup.execute('SELECT payload_json FROM papers WHERE id=?',(hit['id'],)).fetchone()
                        if row:
                            paper=json.loads(row[0])
                            if matches_scope(paper,options):
                                add(paper,dict(route='keyword',phrases=hit['matched_phrases'],rank=rank,
                                              score=hit['score']),1)
                    timings['keyword_ms']=round((time.perf_counter()-t)*1000,2)
    finally:
        if own_bridge and bridge is not None:bridge.close()

    # Average query variants within zvec so query expansion does not give that
    # retriever more total weight than the independent keyword route.
    recalled=sorted(candidates.values(),key=lambda p:(-p['retrieval_score'],p['id']))
    selected=recalled[:options['candidate_limit']]
    timings['retrieval_ms']=round((time.perf_counter()-started)*1000,2)
    kept=[];filtered=[];uncertain=0
    kev=dict(model=None,endpoint=None)
    if selected:
        t=time.perf_counter()
        kev=client.rerank(options['query'],selected)
        timings['kev_ms']=round((time.perf_counter()-t)*1000,2)
        rows=kev['results']
        if len(rows)!=len(selected) or [r['paper_id'] for r in rows]!=[p['id'] for p in selected]:
            raise SearchError('Kev response does not match the candidate papers.')
        for rank,(paper,decision) in enumerate(zip(selected,rows),1):
            probabilities=decision['probabilities']
            confidence=sorted(probabilities.values(),reverse=True)
            has_abstract=bool((paper.get('abstract') or '').strip())
            needs_review=confidence[0]<0.6 or confidence[0]-confidence[1]<0.1 or not has_abstract
            removed=has_abstract and decision['choice']=='unrelated' and probabilities['unrelated']>=options['unrelated_threshold']
            paper.update(original_rank=rank,score=probabilities['direct']+0.5*probabilities['background'],
                relevance=dict(choice=decision['choice'],probabilities=probabilities,
                               uncertain=needs_review,filtered=removed),
                evidence_scope='title_and_abstract' if has_abstract else 'title_only',
                matched_editions=matching_editions(paper,options))
            if not has_abstract:paper['abstract']=None
            paper['evidence'],paper['matched_terms']=evidence_excerpt(paper,' '.join([*options['retrieval_queries'],*options['keywords']]))
            if removed:
                filtered.append(dict(id=paper['id'],title=paper['title'],relevance=paper['relevance']))
            else:
                kept.append(paper)
                uncertain+=int(needs_review)
        kept.sort(key=lambda p:(-p['score'],-p['retrieval_score'],p['id']))
    else:
        timings['kev_ms']=0
    warnings.append('Kev 仅判断本次召回的标题和摘要；模型分布未经本领域校准，待核验结果请阅读摘要确认。')
    if any(not (p.get('abstract') or '').strip() for p in selected):warnings.append('部分候选缺少摘要，判断仅依据标题，已标为待核验。')
    counts=dict(by_route=route_counts,retrieved_unique=len(recalled),reranked=len(selected),
                candidate_limit=options['candidate_limit'],filtered=len(filtered),retained=len(kept),
                uncertain_retained=uncertain,returned=min(len(kept),options['limit']))
    return dict(query=options['query'],results=kept[:options['limit']],filtered=filtered,
                total_candidates=len(recalled),eligible_papers=eligible,counts=counts,
                timings=timings,elapsed_ms=round((time.perf_counter()-started)*1000,2),
                mode='discovery',retriever=options['retriever'],retrieval_queries=options['retrieval_queries'],
                keywords=options['keywords'],warnings=warnings,scope='titles_and_abstracts',
                model=state.get('model'),index_updated_at=state.get('updated_at'),
                kev={k:v for k,v in kev.items() if k!='results'},
                ranking=dict(fusion='RRF k=60; mean of zvec query variants + keyword rank',
                             score='p(direct) + 0.5 * p(background)',
                             unrelated_threshold=options['unrelated_threshold']))
