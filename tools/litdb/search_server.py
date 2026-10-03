"""Loopback-only UI and JSON API for the local literature search engine."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .paths import LitDBPaths
from .search import SearchError, ZvecBridge, index_lock, lookup_connection, read_json, search, search_home, status, write_json
from .search_export import UnknownPaperIds, build_export, load_export_records

ASSETS=Path(__file__).parent/'search_web'
MAX_EXPORT_QUERY_BYTES=8192
FILES={'/':('index.html','text/html'),'/index.html':('index.html','text/html'),
       '/app.js':('app.js','application/javascript'),'/style.css':('style.css','text/css')}


def serve(paths: LitDBPaths, port=8765, open_browser=False):
    home=search_home(paths);home.mkdir(parents=True,exist_ok=True)
    token=secrets.token_hex(32)
    instance=secrets.token_hex(16)
    stopping=threading.Event()
    bridge=ZvecBridge(home,read_json(home/'state.json').get('model','local/potion-multilingual-128m'))

    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'

        def log_message(self, fmt, *args):
            # Avoid putting a research idea or query string in access logs.
            sys.stderr.write(f'{self.log_date_time_string()} {self.command} {urlsplit(self.path).path}\n')

        def send(self,code,body,content_type='application/json'):
            data=json.dumps(body,ensure_ascii=False).encode() if content_type=='application/json' else body
            self.send_response(code)
            self.send_header('Content-Type',content_type+'; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            try:self.end_headers();self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass

        def send_download(self,data,content_type,filename):
            self.send_response(200)
            self.send_header('Content-Type',content_type+'; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            try:self.end_headers();self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass

        def export_download(self,identities,export_format):
            with index_lock(home,False):
                records=load_export_records(home,identities)
                data,content_type,filename=build_export(records,export_format)
            self.send_download(data,content_type,filename)

        def permitted(self):
            allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                self.send(403,{'error':'仅允许本机访问。'});return False
            origin=self.headers.get('Origin')
            if origin and origin not in {f'http://{host}' for host in allowed}:
                self.send(403,{'error':'跨站请求不被允许。'});return False
            return True

        def do_GET(self):
            if not self.permitted():return
            url=urlsplit(self.path)
            try:
                if url.path=='/api/health':
                    self.send(200,dict(service='litdb-search',pid=os.getpid(),instance_id=instance,home=str(paths.home),stopping=stopping.is_set()))
                elif url.path=='/api/status':self.send(200,status(paths))
                elif url.path=='/api/export':
                    if len(url.query.encode('utf-8'))>MAX_EXPORT_QUERY_BYTES:
                        self.send(414,{'error':'导出请求过长。'});return
                    query=parse_qs(url.query,keep_blank_values=True)
                    formats=query.get('format',[])
                    if len(formats)!=1:raise ValueError('导出格式参数无效。')
                    self.export_download(query.get('id',[]),formats[0])
                elif url.path=='/api/paper':
                    identity=parse_qs(url.query).get('id',[''])[0]
                    with lookup_connection(home) as c:
                        row=c.execute('SELECT payload_json FROM papers WHERE id=?',(identity,)).fetchone()
                    self.send(200,json.loads(row[0])) if row else self.send(404,{'error':'未找到论文。'})
                elif url.path in FILES:
                    filename,content_type=FILES[url.path]
                    self.send(200,(ASSETS/filename).read_bytes(),content_type)
                elif url.path=='/favicon.ico':self.send(204,b'','image/x-icon')
                else:self.send(404,{'error':'未找到页面。'})
            except UnknownPaperIds as exc:self.send(404,{'error':str(exc)})
            except (ValueError,TypeError) as exc:self.send(400,{'error':str(exc)})
            except SearchError as exc:self.send(503,{'error':str(exc)})

        def do_POST(self):
            if not self.permitted():return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=32768:raise ValueError('请求过大或内容为空。')
                if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
                    raise ValueError('请求格式应为 JSON。')
                body=json.loads(self.rfile.read(length))
                path=urlsplit(self.path).path
                if path=='/api/search':self.send(200,search(paths,body,bridge))
                elif path=='/api/export':
                    if not isinstance(body,dict):raise ValueError('请求内容格式无效。')
                    self.export_download(body.get('ids'),body.get('format'))
                elif path=='/api/shutdown':
                    provided=body.get('token') if isinstance(body,dict) else None
                    if not isinstance(provided,str) or not hmac.compare_digest(provided,token):
                        self.send(403,{'error':'Invalid shutdown token'});return
                    stopping.set()
                    self.send(200,{'stopping':True})
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                else:self.send(404,{'error':'未找到接口。'})
            except UnknownPaperIds as exc:self.send(404,{'error':str(exc)})
            except (ValueError,TypeError) as exc:self.send(400,{'error':str(exc)})
            except SearchError as exc:self.send(503,{'error':str(exc)})
            except Exception:
                import traceback
                traceback.print_exc(file=sys.stderr)
                self.send(500,{'error':'检索出现错误，请查看本地 search/server.log。'})

        def do_OPTIONS(self):self.send(403,{'error':'Cross-origin access is disabled.'})

    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    url=f'http://127.0.0.1:{server.server_port}'
    metadata=dict(pid=os.getpid(),url=url,port=server.server_port,instance_id=instance,token=token,home=str(paths.home))
    write_json(home/'server.json',metadata)
    (home/'server.json').chmod(0o600)
    print(json.dumps(dict(url=url,pid=os.getpid()),ensure_ascii=False),flush=True)
    if open_browser:webbrowser.open(url)
    def shutdown(*_):
        stopping.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    old_signals={sig:signal.signal(sig,shutdown) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close();bridge.close()
        for sig,handler in old_signals.items():signal.signal(sig,handler)
        saved=read_json(home/'server.json')
        if saved.get('instance_id')==instance:(home/'server.json').unlink(missing_ok=True)


def local_request(port:int,path:str,body=None,timeout=3):
    url=f'http://127.0.0.1:{port}{path}'
    request=urllib.request.Request(url,data=None if body is None else json.dumps(body).encode(),
        headers={} if body is None else {'Content-Type':'application/json'})
    # Loopback service must not accidentally pass through a configured proxy.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=timeout) as response:return json.load(response)


def running(paths:LitDBPaths):
    info=read_json(search_home(paths)/'server.json')
    if not isinstance(info.get('port'),int) or not 1<=info['port']<=65535:return None
    try:health=local_request(info['port'],'/api/health')
    except (OSError,ValueError,urllib.error.URLError):return None
    if health.get('service')!='litdb-search' or health.get('instance_id')!=info.get('instance_id') or health.get('home')!=str(paths.home):return None
    info['stopping']=bool(health.get('stopping'))
    return info


def start(paths:LitDBPaths,port=8765,open_browser=False):
    info=running(paths)
    if info and info.get('stopping'):
        raise SearchError('本地服务正在关闭，请稍后重启。')
    if not info:
        home=search_home(paths);home.mkdir(parents=True,exist_ok=True)
        entry=Path(__file__).resolve().parents[1]/'litdb.py'
        with (home/'server.log').open('a') as log:
            process=subprocess.Popen([sys.executable,str(entry),'search','serve','--home',str(paths.home),'--port',str(port)],
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        for _ in range(60):
            if process.poll() is not None:raise SearchError('本地服务启动失败，请查看 search/server.log 或选择其它端口。')
            info=running(paths)
            if info:break
            time.sleep(0.1)
        if not info:
            process.terminate()
            raise SearchError('本地服务未能及时启动。')
    if open_browser:webbrowser.open(info['url'])
    return {k:v for k,v in info.items() if k!='token'}


def stop(paths:LitDBPaths):
    info=running(paths)
    if not info:return {'stopped':True,'already_stopped':True}
    local_request(info['port'],'/api/shutdown',{'token':info['token']})
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        saved=read_json(search_home(paths)/'server.json')
        if saved.get('instance_id')!=info['instance_id']:
            return {'stopped':True,'url':info['url']}
        time.sleep(0.05)
    raise SearchError('服务仍在结束活动检索，请稍后检查状态。')
