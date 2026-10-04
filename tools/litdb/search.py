"""Local research-idea retrieval using a read-only catalog and zvec-grep.

The catalog is the source of truth. Search files, vectors and the lookup SQLite
database are derived artifacts under <home>/search, never catalog mutations.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import selectors
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .paths import LitDBPaths

DEFAULT_MODEL = 'local/potion-multilingual-128m'
RUNTIME = Path(__file__).resolve().parents[1] / 'search_runtime'
SCHEMA_VERSION = 1


class SearchError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


def search_home(paths: LitDBPaths) -> Path:
    return paths.home / 'search'


def catalog_signature(paths: LitDBPaths) -> list:
    result=[]
    for path in (paths.catalog,Path(str(paths.catalog)+'-wal')):
        if path.exists():
            stat=path.stat()
            if path.name.endswith('-wal') and stat.st_size==0:continue
            result.append([path.name,stat.st_size,stat.st_mtime_ns])
    return result


def safe_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username else None
    except ValueError:
        return None


def document_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def corpus_text(paper: dict) -> str:
    # One file per canonical paper prevents chunks mixing unrelated abstracts.
    return f"Title: {paper['title']}\n\nAbstract: {paper.get('abstract') or ''}\n"


@contextlib.contextmanager
def catalog_connection(paths: LitDBPaths):
    connection = sqlite3.connect(paths.catalog.as_uri() + '?mode=ro', uri=True)
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    try:
        yield connection
    finally:
        connection.close()


@contextlib.contextmanager
def index_lock(home: Path, exclusive: bool):
    home.mkdir(parents=True, exist_ok=True)
    with (home / '.build.lock').open('a') as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SearchError('搜索索引正在使用或更新，请稍后重试。') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def export_catalog(paths: LitDBPaths, home: Path) -> dict:
    corpus = home / 'corpus'
    corpus.mkdir(parents=True, exist_ok=True)
    lookup = sqlite3.connect(home / 'papers.sqlite')
    lookup.executescript('''
        CREATE TABLE IF NOT EXISTS papers (
          id TEXT PRIMARY KEY, canonical_key TEXT NOT NULL UNIQUE,
          path TEXT NOT NULL UNIQUE, title TEXT NOT NULL, abstract TEXT,
          year INTEGER NOT NULL, venue TEXT NOT NULL,
          payload_json TEXT NOT NULL, content_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS editions (
          paper_id TEXT NOT NULL, venue TEXT NOT NULL, year INTEGER NOT NULL,
          PRIMARY KEY(paper_id,venue,year));
        CREATE INDEX IF NOT EXISTS editions_scope ON editions(venue,year,paper_id);
        CREATE INDEX IF NOT EXISTS papers_title ON papers(lower(title));
    ''')
    old = {key: (path, sha) for key, path, sha in lookup.execute('SELECT id,path,content_hash FROM papers')}
    seen, directories = set(), set()
    counts = defaultdict(int)
    fingerprint = hashlib.sha256()
    try:
        lookup.execute('BEGIN IMMEDIATE')
        lookup.execute('DELETE FROM editions')
        with catalog_connection(paths) as catalog:
            sources = defaultdict(list)
            for row in catalog.execute("""SELECT
                json_extract(payload_json,'$.canonical_key'),
                json_extract(payload_json,'$.source_item_id'),
                json_extract(payload_json,'$.venue_id'),
                json_extract(payload_json,'$.year'),
                json_extract(payload_json,'$.landing_url'),
                json_extract(payload_json,'$.pdf_url'),
                json_extract(payload_json,'$.article_url')
                FROM source_item WHERE json_extract(payload_json,'$.inclusion_decision')='include'"""):
                sources[row[0]].append(row[1:])
            total = catalog.execute('SELECT count(*) FROM canonical_work').fetchone()[0]
            for raw, in catalog.execute('SELECT payload_json FROM canonical_work ORDER BY id'):
                work = json.loads(raw)
                key = work['canonical_key']
                editions = sources.get(key)
                if not editions:
                    counts['skipped_no_included_source'] += 1
                    continue
                editions.sort(key=lambda e:e[0] != work['primary_source_item_id'])
                identity = document_id(key)
                venue = work['venue_id']
                if not re.fullmatch(r'[a-z0-9-]+', venue):
                    raise SearchError(f'Invalid venue path: {venue}')
                year = int(work['publication_year'])
                if not 1900 <= year <= 2200:
                    raise SearchError('Invalid publication year')
                title = str(work.get('title') or '').strip()
                if not title:
                    raise SearchError(f'Paper has no title: {key}')
                paper = dict(id=identity, canonical_key=key, title=title,
                    abstract=work.get('abstract'),
                    authors=[a.get('name','') if isinstance(a,dict) else str(a) for a in work.get('authors',[])],
                    year=year,venue=venue,doi=work.get('doi'),
                    venues=[{'id':v,'year':y} for v,y in sorted({(e[1],int(e[2])) for e in editions})])
                for field, offset in [('landing_url',3),('pdf_url',4),('article_url',5)]:
                    paper[field] = next((safe_url(e[offset]) for e in editions if safe_url(e[offset])), None)
                rel = f'papers/{venue}/{year}/{identity}.txt'
                content = corpus_text(paper)
                sha = hashlib.sha256(content.encode()).hexdigest()
                path = corpus / rel
                if path.parent not in directories:
                    path.parent.mkdir(parents=True,exist_ok=True)
                    directories.add(path.parent)
                if old.get(identity) != (rel,sha) or not path.is_file():
                    tmp = path.with_suffix('.tmp')
                    tmp.write_text(content)
                    tmp.replace(path)
                    counts['modified' if identity in old else 'added'] += 1
                    if identity in old and old[identity][0] != rel:
                        (corpus / old[identity][0]).unlink(missing_ok=True)
                else:
                    counts['unchanged'] += 1
                payload = json.dumps(paper,ensure_ascii=False,sort_keys=True)
                lookup.execute('INSERT OR REPLACE INTO papers VALUES(?,?,?,?,?,?,?,?,?)',
                               (identity,key,rel,title,paper['abstract'],year,venue,payload,sha))
                lookup.executemany('INSERT INTO editions VALUES(?,?,?)',
                                   [(identity,e['id'],e['year']) for e in paper['venues']])
                seen.add(identity)
                fingerprint.update(identity.encode()+b'\0'+payload.encode()+b'\n')
            assert len(seen)+counts.get('skipped_no_included_source',0) == total
        for identity in old.keys() - seen:
            (corpus / old[identity][0]).unlink(missing_ok=True)
            lookup.execute('DELETE FROM papers WHERE id=?',(identity,))
            counts['deleted'] += 1
        lookup.commit()
        return dict(papers=len(seen),catalog_canonical_count=total,counts=dict(counts),fingerprint=fingerprint.hexdigest(),exported_at=now())
    finally:
        lookup.close()


class ZvecBridge:
    """Serialized JSON-line RPC; queries reuse the local model between requests."""
    def __init__(self, home: Path, model: str = DEFAULT_MODEL):
        self.home, self.model = home, model
        self.process = None
        self.mutex = threading.Lock()
        self.log = None
        self.serial = 0
        self.buffer = b''

    def _start(self):
        node = shutil.which('node')
        if not node or not (RUNTIME/'node_modules/@zvec/zvec-grep/package.json').is_file():
            raise SearchError('搜索运行环境未安装。请运行 npm install --prefix tools/search_runtime。')
        self.home.mkdir(parents=True,exist_ok=True)
        self.log = (self.home/'engine.log').open('a')
        self.process = subprocess.Popen([node,str(RUNTIME/'bridge.mjs'),str(self.home/'corpus'),self.model],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,bufsize=0)

    def call(self, request: dict, timeout: float = 180) -> dict:
        with self.mutex:
            requested_model = request.get('model', self.model)
            if requested_model != self.model:
                self._dispose()
                self.model = requested_model
            if self.process is None or self.process.poll() is not None:
                self._dispose()
                self._start()
            self.serial += 1
            request = {**request,'id':self.serial}
            try:
                self.process.stdin.write((json.dumps(request,ensure_ascii=False)+'\n').encode())
                self.process.stdin.flush()
                deadline = time.monotonic()+timeout
                with selectors.DefaultSelector() as selector:
                    selector.register(self.process.stdout,selectors.EVENT_READ)
                    while True:
                        if b'\n' not in self.buffer:
                            wait = deadline-time.monotonic()
                            if wait<=0 or not selector.select(wait):
                                raise SearchError('本地检索超时，请稍后重试；详细记录见 search/engine.log。')
                            chunk = os.read(self.process.stdout.fileno(),65536)
                            if not chunk:
                                try:code=self.process.wait(timeout=1)
                                except subprocess.TimeoutExpired:code=self.process.poll()
                                self.log.write(f'Engine process exited: {code}\n');self.log.flush()
                                raise SearchError('本地搜索进程已退出；详细记录见 search/engine.log。')
                            self.buffer += chunk
                            continue
                        raw,self.buffer=self.buffer.split(b'\n',1)
                        line=raw.decode('utf-8',errors='replace')
                        if not line.startswith('LITDB_JSON '):
                            continue
                        response = json.loads(line[len('LITDB_JSON '):])
                        if response.get('id') != self.serial:
                            continue
                        if not response['ok']:
                            raise SearchError(response.get('error','Search engine error'))
                        return response['result']
            except (OSError,ValueError,SearchError):
                self._dispose()
                raise

    def _dispose(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill();self.process.wait(timeout=5)
            for stream in (self.process.stdin,self.process.stdout):
                if stream:stream.close()
        if self.log:self.log.close()
        self.process,self.log = None,None
        self.buffer = b''

    def close(self):
        with self.mutex:
            self._dispose()


def build_index(paths: LitDBPaths, *, model=DEFAULT_MODEL, rebuild=False) -> dict:
    if not model.startswith('local/'):
        raise SearchError('Only local embedding models are permitted.')
    home=search_home(paths)
    with index_lock(home,True):
        previous=read_json(home/'state.json')
        if previous.get('model') and previous['model']!=model and not rebuild:
            raise SearchError('Changing the embedding model requires --rebuild.')
        state=dict(schema_version=SCHEMA_VERSION,ready=False,phase='exporting',model=model,
                   started_at=now(),catalog=str(paths.catalog),pid=os.getpid(),
                   catalog_signature=catalog_signature(paths))
        write_json(home/'state.json',state)
        bridge=ZvecBridge(home,model)
        try:
            exported=export_catalog(paths,home)
            state.update(phase='indexing',export=exported)
            write_json(home/'state.json',state)
            result=bridge.call({'op':'index','rebuild':rebuild},timeout=24*3600)
            info=bridge.call({'op':'status'},timeout=600)
            status=info.get('status') or {}
            if result.get('filesFailed') or status.get('filesFailed') or status.get('filesPending') or status.get('filesIndexed')!=exported['papers']:
                raise SearchError(f'Incomplete index: {status.get("filesIndexed")} / {exported["papers"]} papers')
            state.update(ready=True,phase='ready',updated_at=now(),indexed_papers=exported['papers'],
                         index_result=result,index_status=status,engine_version='0.2.2')
            write_json(home/'state.json',state)
            return state
        except BaseException as exc:
            state.update(ready=False,phase='failed',error=str(exc),failed_at=now())
            write_json(home/'state.json',state)
            raise
        finally:
            bridge.close()


VENUE_LABELS = {
    'iclr':'ICLR','icml':'ICML','neurips':'NeurIPS','aaai':'AAAI','ijcai':'IJCAI',
    'cvpr':'CVPR','iccv':'ICCV','eccv':'ECCV','acl':'ACL',
    'asp-dac':'ASP-DAC','iscas':'ISCAS','dac':'DAC','date':'DATE','iccad':'ICCAD',
    'iccd':'ICCD','tcad':'TCAD','tvlsi':'TVLSI','todaes':'TODAES','jetc':'JETC',
    'trets':'TRETS','ieee-design-test':'IEEE Design & Test',
    'embedded-systems-letters':'IEEE Embedded Systems Letters',
    'nature':'Nature', 'nature-methods':'Nature Methods',
    'nature-machine-intelligence':'Nature Machine Intelligence',
    'nature-computational-science':'Nature Computational Science',
}
STOPWORDS = set('a an the and or to of in for on with from by at as is are be that this it i we how what why can using use used research paper papers study studies want related find about improve new method methods approach based'.split())


@contextlib.contextmanager
def lookup_connection(home: Path):
    path=home/'papers.sqlite'
    if not path.is_file():
        raise SearchError('尚未建立搜索索引，请先运行 litdb search index。')
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    try:yield connection
    finally:connection.close()


def status(paths: LitDBPaths) -> dict:
    home=search_home(paths)
    state=read_json(home/'state.json')
    result=dict(ready=bool(state.get('ready')),indexed_papers=state.get('indexed_papers',0),
        model=state.get('model',DEFAULT_MODEL),updated_at=state.get('updated_at'),
        phase=state.get('phase','missing'),venues=[],scope='titles_and_abstracts',
        stale=bool(state.get('ready') and state.get('catalog_signature')!=catalog_signature(paths)))
    if state.get('error'):result['error']=state['error']
    if (home/'papers.sqlite').is_file():
        with lookup_connection(home) as c:
            result['venues']=[dict(id=r[0],label=VENUE_LABELS.get(r[0],r[0]),count=r[1])
                for r in c.execute('SELECT venue,count(DISTINCT paper_id) FROM editions GROUP BY venue ORDER BY venue')]
            result['year_from'],result['year_to']=c.execute('SELECT min(year),max(year) FROM editions').fetchone()
    if not result['ready']:
        result['progress']=read_json(home/'index_progress.json')
    return result


def validate_query(options: dict) -> dict:
    if not isinstance(options,dict):raise ValueError('搜索请求必须是对象。')
    query=options.get('query')
    if not isinstance(query,str) or not query.strip():raise ValueError('请输入研究主题或 idea。')
    query=query.strip()
    if len(query)>6000:raise ValueError('研究描述请控制在 6000 个字符以内。')
    limit=options.get('limit',10)
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=50:
        raise ValueError('结果数量必须在 1–50 之间。')
    mode=options.get('mode','hybrid')
    if mode not in {'hybrid','semantic','keyword'}:raise ValueError('未知检索模式。')
    venues=options.get('venues', [])
    if not isinstance(venues,list) or any(not isinstance(v,str) or not re.fullmatch(r'[a-z0-9-]+',v) for v in venues):
        raise ValueError('venue 筛选格式错误。')
    years=[]
    for field in ('year_from','year_to'):
        value=options.get(field)
        if value is not None and (isinstance(value,bool) or not isinstance(value,int) or not 1900<=value<=2200):
            raise ValueError('年份应为 1900–2200 之间的整数。')
        years.append(value)
    if all(y is not None for y in years) and years[0]>years[1]:raise ValueError('起始年份不能晚于结束年份。')
    return dict(query=query,limit=limit,mode=mode,venues=list(dict.fromkeys(venues)),year_from=years[0],year_to=years[1])


def scope_clause(options: dict, prefix='e') -> tuple[str,list]:
    clauses,args=[],[]
    if options['venues']:
        clauses.append(f'{prefix}.venue IN ({",".join("?" for _ in options["venues"])})');args.extend(options['venues'])
    if options['year_from'] is not None:clauses.append(f'{prefix}.year>=?');args.append(options['year_from'])
    if options['year_to'] is not None:clauses.append(f'{prefix}.year<=?');args.append(options['year_to'])
    return ' AND '.join(clauses) or '1',args


def matches_scope(paper: dict, options: dict) -> bool:
    return any((not options['venues'] or e['id'] in options['venues'])
        and (options['year_from'] is None or e['year']>=options['year_from'])
        and (options['year_to'] is None or e['year']<=options['year_to']) for e in paper['venues'])


def evidence_excerpt(paper: dict, query: str) -> tuple[str,list[str]]:
    text=paper.get('abstract') or paper['title']
    terms=list(dict.fromkeys(t for t in re.findall(r'[a-zA-Z][a-zA-Z0-9+_-]{1,}',query.lower()) if t not in STOPWORDS))
    matched=[t for t in terms if t in (paper['title']+' '+text).lower()][:12]
    sentences=re.split(r'(?<=[.!?。])\s+',text)
    selected=max(enumerate(sentences),key=lambda item:(sum(term in item[1].lower() for term in matched),-item[0]))[1]
    return selected[:650]+('…' if len(selected)>650 else ''),matched


def search(paths: LitDBPaths, options: dict, bridge: ZvecBridge | None = None) -> dict:
    options=validate_query(options)
    started=time.perf_counter();home=search_home(paths)
    own_bridge=bridge is None
    with index_lock(home,False):
        state=read_json(home/'state.json')
        if not state.get('ready'):raise SearchError('搜索索引尚未就绪，请先建立或完成更新。')
        warnings=[]
        if state.get('catalog_signature')!=catalog_signature(paths):
            warnings.append('数据库已更新；当前结果来自上次索引快照，请刷新索引以纳入最新论文。')
        with lookup_connection(home) as c:
            valid_venues={r[0] for r in c.execute('SELECT DISTINCT venue FROM editions')}
            if set(options['venues'])-valid_venues:raise ValueError('未知 venue。')
            clause,args=scope_clause(options)
            eligible=c.execute(f'SELECT count(DISTINCT e.paper_id) FROM editions e WHERE {clause}',args).fetchone()[0]
            if not eligible:
                return dict(query=options['query'],results=[],total_candidates=0,eligible_papers=0,
                            elapsed_ms=round((time.perf_counter()-started)*1000),mode=options['mode'],warnings=warnings)
            include_paths=None
            if options['venues'] or options['year_from'] is not None or options['year_to'] is not None:
                include_paths=[f'papers/{r[0]}/{r[1]}' for r in c.execute(f'SELECT DISTINCT e.venue,e.year FROM editions e WHERE {clause}',args)]
                # A paper presented in two years is still one result. Include its
                # primary file when its alternate edition passes the filter.
                include_paths.extend(r[0] for r in c.execute(f'''SELECT DISTINCT p.path FROM papers p
                    JOIN editions e ON e.paper_id=p.id WHERE {clause} AND (p.year!=e.year OR p.venue!=e.venue)''',args))
            routes=[]
            if options['mode'] in {'hybrid','semantic'}:routes.append(dict(mode='vector',query=options['query']))
            if options['mode'] in {'hybrid','keyword'}:routes.append(dict(mode='fts',query=options['query']))
            bridge=bridge or ZvecBridge(home,state['model'])
            try:
                raw=bridge.call(dict(op='query',query=options['query'],routes=routes,
                    limit=min(max(options['limit']*3,30),150),include_paths=include_paths,model=state['model']))
            finally:
                if own_bridge:bridge.close()
            ranked=[];seen=set()
            for hit in raw.get('items',[]):
                relative=hit.get('file',{}).get('relativePath','')
                match=re.fullmatch(r'papers/[a-z0-9-]+/\d{4}/([a-f0-9]{64})\.txt',relative)
                if not match or match[1] in seen:continue
                row=c.execute('SELECT payload_json FROM papers WHERE id=? AND path=?',(match[1],relative)).fetchone()
                if not row:continue
                paper=json.loads(row[0])
                if not matches_scope(paper,options):continue
                paper['score']=float(hit.get('score') or 0)
                paper['matched_by']=hit.get('matchedBy')
                paper['evidence'],paper['matched_terms']=evidence_excerpt(paper,options['query'])
                ranked.append(paper);seen.add(paper['id'])
            exact=c.execute('SELECT payload_json FROM papers WHERE lower(title)=lower(?)',(options['query'],)).fetchall()
            for row in reversed(exact):
                paper=json.loads(row[0])
                if not matches_scope(paper,options):continue
                existing=next((p for p in ranked if p['id']==paper['id']),None)
                if existing:
                    ranked.remove(existing);paper=existing
                else:
                    paper.update(score=0,matched_terms=[],evidence=paper.get('abstract') or paper['title'])
                paper['matched_by']='exact_title';ranked.insert(0,paper)
            return dict(query=options['query'],results=ranked[:options['limit']],total_candidates=len(ranked),
                eligible_papers=eligible,elapsed_ms=round((time.perf_counter()-started)*1000),
                mode=options['mode'],warnings=warnings,model=state['model'],scope='titles_and_abstracts',
                index_updated_at=state['updated_at'])


def markdown_results(result: dict) -> str:
    lines=[f"检索主题：{result['query']}", '']
    for warning in result.get('warnings',[]):lines.extend([warning,''])
    for i,p in enumerate(result['results'],1):
        title=p['title'].replace('[','\\[').replace(']','\\]')
        url=p.get('article_url') or p.get('landing_url')
        title=f'[{title}]({url})' if url else title
        lines.extend([f"{i}. **{title}** — {VENUE_LABELS.get(p['venue'],p['venue'])} {p['year']}",
                      '   '+', '.join(p['authors']), '   '+p['evidence']])
        if p.get('pdf_url'):lines.append(f"   [PDF]({p['pdf_url']})")
        lines.append('')
    if not result['results']:lines.append('当前范围内没有找到匹配论文。')
    return '\n'.join(lines)
