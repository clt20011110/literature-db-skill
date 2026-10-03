#!/usr/bin/env python3
import argparse, gzip, hashlib, json, re, time, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen
import ssl
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()
from html import unescape

UA = 'Mozilla/5.0 (compatible; ICML-metadata-runner/1.0)'
SCHEMA = 'literature-metadata-staging-v1'

def meta(html, name):
    p = re.compile(r'<meta[^>]+(?:name|property)=["\']'+re.escape(name)+r'["\'][^>]*content=["\']([^"\']*)', re.I)
    m = p.search(html) or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:name|property)=["\']'+re.escape(name)+r'["\']', html, re.I)
    return unescape(m.group(1)).strip() if m else None

def metas(html, name):
    return [unescape(x).strip() for x in re.findall(r'<meta[^>]+(?:name|property)=["\']'+re.escape(name)+r'["\'][^>]*content=["\']([^"\']*)', html, re.I)]

def clean(s):
    return re.sub(r'\\s+', ' ', re.sub(r'<[^>]+>', ' ', unescape(s or ''))).strip()

def fetch(url):
    req = Request(url, headers={'User-Agent': UA, 'Accept': 'text/html'})
    try:
        with urlopen(req, timeout=45, context=SSL_CTX) as r:
            return r.read().decode('utf-8', 'replace')
    except Exception:
        return subprocess.run(['curl','--fail','--silent','--show-error','--location','--max-time','45','-A',UA,url], check=True, capture_output=True).stdout.decode('utf-8','replace')

def row(e, observed):
    url = e['landing_url']; h = fetch(url)
    title = meta(h, 'citation_title') or clean((re.search(r'<h1[^>]*>(.*?)</h1>', h, re.I|re.S) or [None,''])[1])
    authors = metas(h, 'citation_author')
    abstract = meta(h, 'citation_abstract')
    if not abstract:
        m = re.search(r'<div[^>]+class=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.*?)</div>', h, re.I|re.S)
        abstract = clean(m.group(1)) if m else None
    canonical = meta(h, 'citation_abstract_html_url') or url
    doi = meta(h, 'citation_doi')
    if doi and not re.fullmatch(r'10\.\d{4,9}/\S+', doi): doi = None
    pdf = meta(h, 'citation_pdf_url')
    pdf_ok = pdf if pdf and re.match(r'https://proceedings\.mlr\.press/', pdf) else None
    typ = 'position-paper' if (title or '').startswith('Position:') else 'research-paper'
    miss = {}
    if not doi: miss['doi'] = 'not_assigned' if not meta(h,'citation_doi') else 'invalid_source_value'
    if not pdf_ok: miss['pdf_url'] = 'source_unavailable'
    prov = {}
    for f,v in [('source_native_id',e['source_item_id']),('title',title),('authors',authors),('abstract',abstract),('year',e['year']),('document_type',typ),('landing_url',canonical),('publication_date',meta(h,'citation_publication_date'))]:
        prov[f]={'source_url':url,'observed_at':observed,'method':'official_pmlr_article_html','status':'present' if v else 'missing'}
    prov['doi']={'source_url':url,'observed_at':observed,'method':'official_pmlr_article_html','status':'present' if doi else 'missing','reason':miss.get('doi')}
    prov['pdf_discovery_status']={'source_url':url,'observed_at':observed,'method':'official_pmlr_article_html','status':'present' if pdf_ok else 'metadata_only','reason':miss.get('pdf_url')}
    return {'schema_version':SCHEMA,'catalog_ready':False,'venue_id':'icml','source_native_id':e['source_item_id'],'title':title,'authors':authors,'year':e['year'],'volume':e.get('volume'),'issue':None,'pages':e.get('listing_info','').split(':')[-1] if e.get('listing_info') else None,'document_type':typ,'inclusion_decision':'include','landing_url':canonical,'source_url':url,'abstract':abstract,'doi':doi,'publication_date':meta(h,'citation_publication_date'),'pdf_url':pdf_ok,'pdf_discovery_status':'official_pmlr_html' if pdf_ok else 'metadata_only','observed_at':observed,'missing_fields':miss,'field_provenance':prov,'reuse_evidence':{'expected_manifest':'official_pmlr_listing','detail_fetch':'fresh_official_html','detail_method':'official_pmlr_article_html'},'listing_position':e.get('listing_position'),'source_grade':'A'}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--expected-root',type=Path,required=True); ap.add_argument('--run-root',type=Path,required=True); ap.add_argument('--batch-size',type=int,default=100); ap.add_argument('--workers',type=int,default=4); ap.add_argument('--delay',type=float,default=.25); ap.add_argument('--loop',action='store_true'); ap.add_argument('--log',type=Path); args=ap.parse_args()
    out=args.run_root/'metadata_staging.jsonl'; ck=args.run_root/'detail_checkpoint.json'; eq=args.run_root/'error_queue.json'; out.parent.mkdir(parents=True,exist_ok=True)
    done={json.loads(x)['source_native_id'] for x in out.read_text().splitlines() if x.strip()} if out.exists() else set(); expected=[]
    for p in sorted(args.expected_root.glob('*.jsonl.gz')):
        with gzip.open(p,'rt') as f: expected += [json.loads(x) for x in f if x.strip()]
    queue=json.loads(eq.read_text()) if eq.exists() else {}
    while True:
        retry_ids=[i for i,v in queue.items() if v.get('status')!='unresolved']
        retry=[e for e in expected if e['source_item_id'] in retry_ids and e['source_item_id'] not in done]
        rest=[e for e in expected if e['source_item_id'] not in done and e['source_item_id'] not in retry_ids]
        batch=(retry+rest)[:args.batch_size]
        if not batch: break
        errors=[]; now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()); results={}
        with ThreadPoolExecutor(max_workers=max(1,args.workers)) as pool:
            jobs={pool.submit(row,e,now):e for e in batch}
            for fut in as_completed(jobs):
                e=jobs[fut]
                try: results[e['source_item_id']]=fut.result()
                except Exception as ex:
                    sid=e['source_item_id']; old=queue.get(sid,{}); attempt=int(old.get('attempt',0))+1
                    queue[sid]={'source_native_id':sid,'attempt':attempt,'status':'unresolved' if attempt>=5 else 'retry','last_error':str(ex),'last_observed_at':now}
                    errors.append(queue[sid])
        with out.open('a') as f:
            for e in batch:
                r=results.get(e['source_item_id'])
                if r: f.write(json.dumps(r,ensure_ascii=False)+'\n'); done.add(e['source_item_id']); queue.pop(e['source_item_id'],None)
            f.flush()
        state={'venue_id':'icml','generated_at':now,'status':'PARTIAL' if len(done)<len(expected) else 'COMPLETE','records_written':len(done),'expected_records':len(expected),'pending_expected_records':len(expected)-len(done),'last_batch':[e['source_item_id'] for e in batch],'errors':errors,'staging_sha256':hashlib.sha256(out.read_bytes()).hexdigest()}
        eq.write_text(json.dumps(queue,indent=2,ensure_ascii=False)+'\n'); state['unresolved_count']=sum(v.get('status')=='unresolved' for v in queue.values()); ck.write_text(json.dumps(state,indent=2)+'\n'); line=json.dumps(state)
        print(line,flush=True)
        if args.log: args.log.parent.mkdir(parents=True,exist_ok=True); args.log.open('a').write(line+'\n')
        if not args.loop: break
        if errors and len(done)==0 and all(v.get('status')=='unresolved' for v in queue.values()): break
        time.sleep(args.delay)
if __name__=='__main__': main()
