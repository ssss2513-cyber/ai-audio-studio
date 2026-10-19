from __future__ import annotations
import contextlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, wait, FIRST_COMPLETED
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import requests
from .store import now
from .provider import Gemini, ProviderError, ProviderCancelled, FALLBACK_MODEL, ANALYSIS_SCHEMA, ANALYSIS_SYSTEM
from .key_pool import KeyPool, PoolUnavailable

RETRY_DELAYS=(120,300,600,1200,1800,3600)
CLIP_MAX_BYTES=192*1024**2
CLIP_FREE_BYTES=2*CLIP_MAX_BYTES+64*1024**2


def validate_segments(raw, start, end):
    if not isinstance(raw, dict) or not isinstance(raw.get('segments'), list):
        raise ValueError('분석 결과 형식을 확인하지 못했습니다. 이어서 분석을 눌러 주세요.')
    result=[];invalid_times=[]
    for s in raw['segments'][:200]:
        if not isinstance(s,dict):continue
        try:a,b=float(s.get('start')),float(s.get('end'))
        except (TypeError,ValueError):a,b=None,None
        valid=a is not None and b is not None and math.isfinite(a+b) and 0<=a<b and a<end-start and b<=end-start+2
        body=str(s.get('text','')).strip()
        if body:
            # Preserve useful content without inventing a timestamp or moving it to a wrong moment.
            # Missing/invalid times are explicitly unknown in the existing citation UI.
            result.append(dict(start=start+a if valid else None,end=min(end,start+b) if valid else None,title=str(s.get('title',''))[:200],
                text=body[:10000],transcript=str(s.get('transcript',''))[:10000],
                screen_text=str(s.get('screen_text',''))[:6000],timing='estimated' if valid else 'unknown'))
            if not valid:
                invalid_times.append([v if v is not None and math.isfinite(v) else None for v in (a,b)])
    if not result:
        raise ValueError('이 구간의 분석 결과가 없습니다. 이어서 분석을 눌러 주세요.')
    if invalid_times:
        print('LECTURE_TIMING '+json.dumps(dict(clip_seconds=end-start,unknown=len(invalid_times),bounds=invalid_times[:3])),flush=True)
    return sorted(result,key=lambda x:(x['start'] is None,x['start'] or 0))


def probe(source):
    proc=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',source],
                        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=90)
    try:
        n=float(json.loads(proc.stdout)['format']['duration'])
        if proc.returncode or not math.isfinite(n) or not 0<n<=48*3600: raise ValueError()
        return n
    except (ValueError,KeyError,TypeError):
        raise ValueError('영상 길이를 읽지 못했습니다. MP4/WebM 원본을 확인해 주세요.') from None


def make_clip(source, path, start, end, cancelled=None):
    # A single encoder remains active; cancellation also stops speculative preparation.
    cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-threads','1',
         '-filter_threads','1','-rw_timeout','60000000','-ss',str(start),'-i',source,
         '-t',str(end-start),'-map','0:v:0','-map','0:a:0?',
         '-vf','fps=1,scale=min(1280\\,iw):-2','-c:v','libx264','-preset','veryfast',
         '-crf','25','-maxrate','600k','-bufsize','1200k','-threads','1',
         '-c:a','aac','-b:a','48k','-ac','1','-ar','24000','-fs',str(CLIP_MAX_BYTES),
         '-movflags','+faststart',str(path)]
    if cancelled and cancelled():raise ProviderCancelled()
    process=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    deadline=time.monotonic()+1800
    try:
        while True:
            if cancelled and cancelled():raise ProviderCancelled()
            if time.monotonic()>=deadline:
                raise RuntimeError('영상 준비 시간이 초과되었습니다. 이어서 분석해 주세요.')
            try:
                code=process.wait(timeout=.5);break
            except subprocess.TimeoutExpired:pass
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    if code or not path.exists() or path.stat().st_size<100:
        raise RuntimeError('영상 구간을 불러오지 못했습니다. 연결 상태를 확인한 뒤 이어서 분석해 주세요.')
    if cancelled and cancelled():raise ProviderCancelled()
    if abs(probe(str(path))-(end-start))>2.5:
        raise RuntimeError('영상 구간이 완전히 준비되지 않았습니다. 이어서 분석을 눌러 주세요.')


class ClipPrefetch:
    """Prepare only one upcoming clip while the current clip waits on the remote AI."""
    def __init__(self,source,windows,folder,lid,cancelled):
        self.source=source;self.windows=windows;self.folder=folder;self.lid=lid
        self.cancelled=cancelled;self.stopping=threading.Event()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='lecture-clip')
        self.pending=None;self.paths=set()

    def stopped(self):return self.stopping.is_set() or self.cancelled()

    def prepare(self,index,path):
        if self.stopped():raise ProviderCancelled()
        if shutil.disk_usage(self.folder).free<CLIP_FREE_BYTES:
            raise RuntimeError('영상 준비 공간이 부족합니다. 관리자에게 문의해 주세요.')
        started=time.monotonic()
        make_clip(self.source,path,*self.windows[index],cancelled=self.stopped)
        print('LECTURE_PREPARE '+json.dumps(dict(lecture_id=self.lid,part=index+1,
            seconds=round(time.monotonic()-started,1))),flush=True)
        return path

    def start(self,index):
        if self.stopped():raise ProviderCancelled()
        if self.pending:
            if self.pending[0]==index:return
            raise RuntimeError('다음 영상 준비 순서를 확인하지 못했습니다.')
        path=self.folder/f'{self.lid}-{index}.mp4';self.paths.add(path)
        self.pending=(index,self.pool.submit(self.prepare,index,path))

    def take(self,index):
        self.start(index)
        future=self.pending[1];started=time.monotonic()
        while True:
            if self.stopped():raise ProviderCancelled()
            try:path=future.result(timeout=.5);break
            except FutureTimeout:
                if future.done():raise
        self.pending=None
        print('LECTURE_PIPELINE '+json.dumps(dict(lecture_id=self.lid,part=index+1,
            prepare_wait_seconds=round(time.monotonic()-started,1))),flush=True)
        return path

    def __enter__(self):return self

    def __exit__(self,*args):
        self.stopping.set()
        self.pool.shutdown(wait=True,cancel_futures=True)
        for path in self.paths:path.unlink(missing_ok=True)


class DriveSource:
    """Loopback-only authenticated Range proxy; Google credentials never enter ffmpeg argv."""
    def __init__(self, file_id, auth_headers, size, version_check, source_url=None):
        self.file_id=file_id
        self.source_url=source_url
        self.auth_headers=auth_headers
        self.size=size
        self.version_check=version_check
        self.server=None

    def __enter__(self):
        outer=self
        path='/'+secrets.token_urlsafe(32)
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args): pass
            def do_HEAD(self): self.serve(True)
            def do_GET(self): self.serve(False)
            def serve(self, head):
                if self.path!=path:
                    self.send_error(404);return
                upstream=None
                try:
                    headers={} if outer.source_url else outer.auth_headers()
                    value=self.headers.get('Range','')
                    if value:
                        if not re.fullmatch(r'bytes=\d+-\d*',value):
                            self.send_error(416);return
                        headers['Range']=value
                    upstream=requests.get(outer.source_url() if outer.source_url else 'https://www.googleapis.com/drive/v3/files/'+outer.file_id,
                        params={} if outer.source_url else {'alt':'media'},headers=headers,stream=True,timeout=(15,60))
                    if upstream.status_code not in (200,206):
                        self.send_error(502);return
                    # Refuse ignored nonzero seeks; silently decoding the wrong interval corrupts citations.
                    if value and not value.startswith('bytes=0-') and upstream.status_code!=206:
                        self.send_error(502);return
                    self.send_response(upstream.status_code)
                    self.send_header('Accept-Ranges','bytes')
                    self.send_header('Connection','close')
                    for key in ('Content-Length','Content-Range','Content-Type'):
                        if upstream.headers.get(key): self.send_header(key,upstream.headers[key])
                    self.end_headers()
                    if not head:
                        for chunk in upstream.iter_content(128*1024):
                            if chunk:self.wfile.write(chunk)
                except (BrokenPipeError,ConnectionResetError): pass
                except Exception:
                    self.close_connection=True
                finally:
                    if upstream is not None:upstream.close()
                    self.close_connection=True
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        return f'http://127.0.0.1:{self.server.server_port}{path}'

    def __exit__(self,*args):
        if self.server:
            self.server.shutdown();self.server.server_close()


class Worker:
    def __init__(self, core, library, auth_headers):
        self.core=core;self.lib=library;self.auth_headers=auth_headers
        self.stop=threading.Event();self.wake=threading.Event();self.lock=threading.RLock()
        self.active=None;self.thread=None
        self.inflight=set()
        self.keys=KeyPool(core,library,lambda key:self.provider(key))
        self.temp=Path(tempfile.gettempdir())/'maison-lecture-ai'
        self.temp.mkdir(mode=0o700,exist_ok=True)

    def provider(self,key=None):
        cfg=self.lib.config()
        if key is None:
            records=self.keys.records()
            key=self.core.dec(records[0]['secret']) if records else ''
        return Gemini(key,cfg.get('model','gemini-3.8-flash'),cfg.get('embedding_model','gemini-embedding-2'),self.usage,
            fallback_model=cfg.get('fallback_model',FALLBACK_MODEL))

    def phase(self,lid,text,**details):
        self.lib.update(lid,phase=text,phase_at=now(),**details)
        print('LECTURE_JOB '+json.dumps(dict(lecture_id=lid,phase=text,**details),ensure_ascii=False),flush=True)

    def provider_event(self,lid,event,stage,details):
        if stage=='임시 영상 정리':return
        if event=='retry':
            self.phase(lid,f"{stage} · {details['seconds']}초 뒤 자동 재시도 ({details['attempt']}/{details['attempts']})")
        elif event=='fallback':
            self.phase(lid,'AI 서버 응답 지연 · 다른 모델 연결 중',model_used=details['model'])
            previous=self.lib.lecture(lid)['warning'] or ''
            previous=re.sub(r'(?:기본 모델의|AI) 서버 오류로 [^\n]+? 모델을 사용합니다\.', '', previous).strip()
            notes=[previous] if previous else []
            self.lib.update(lid,warning='\n'.join([f"AI 서버 오류로 {details['model']} 모델을 사용합니다.",*notes]).strip())
        elif event=='request':self.phase(lid,stage)

    def defer_error(self,lid,error):
        """Durable bounded retries across restarts; completed parts are never resubmitted."""
        if not isinstance(error,ProviderError) or not error.retryable:return False
        with self.lock:
            row=self.lib.lecture(lid)
            if row['pause']:
                self.lib.update(lid,status='paused',retry_at=0,phase='일시정지');return True
            count=int(row['retry_count'] or 0)
            if count>=len(RETRY_DELAYS):
                self.lib.configure(queue_blocked='AI 연결 오류가 계속되어 자동 재개 6회 후 멈췄습니다. 저장된 구간은 유지됩니다. '+str(error))
                return False
            delay=max(RETRY_DELAYS[count],min(error.retry_after or 0,3600))
            resume_at=time.time()+delay
            self.lib.update(lid,status='retrying',error=str(error)[:1000],retry_at=resume_at,retry_count=count+1)
            self.phase(lid,f'AI 연결 대기 · {round(delay/60)}분 뒤 자동 재개 ({count+1}/{len(RETRY_DELAYS)})')
            # Cooling down the provider also stops other queued videos from hammering the same API.
            self.lib.configure(provider_retry_at=resume_at)
            print('LECTURE_RETRY '+json.dumps(dict(lecture_id=lid,code=error.code,retry_at=resume_at,
                retry_count=count+1,completed=row['completed'])),flush=True)
            return True

    def usage(self,kind,data):
        with self.lib.connect() as c:
            c.execute('INSERT INTO usage(at,kind,input_tokens,output_tokens) VALUES(?,?,?,?)',
                (now(),kind,data.get('promptTokenCount',0),data.get('candidatesTokenCount',0)))

    def metadata(self, video):
        if video['source']=='railway':return self.core.OBJECT_MEDIA_METADATA(video)
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,200}',video['external_id']):
            raise ValueError('Drive 파일 연결 정보가 올바르지 않습니다.')
        try:
            response=requests.get('https://www.googleapis.com/drive/v3/files/'+video['external_id'],
                params={'fields':'id,size,mimeType,md5Checksum,version,modifiedTime,trashed,videoMediaMetadata',
                        'supportsAllDrives':'true'},headers=self.auth_headers(),timeout=(15,60))
            if response.status_code!=200:raise RuntimeError()
            data=response.json()
        except Exception:
            raise RuntimeError('Drive 원본 정보를 읽지 못했습니다. 파일 공유와 연결을 확인해 주세요.') from None
        if data.get('trashed'):raise ValueError('Drive 휴지통으로 이동한 영상입니다.')
        if data.get('mimeType') not in ('video/mp4','video/webm'):
            raise ValueError('MP4 또는 WebM 강의만 분석할 수 있습니다.')
        return data

    def video(self,lid):
        with self.core.db() as c:v=c.execute('SELECT * FROM videos WHERE id=?',(lid,)).fetchone()
        if not v or v['state'] in ('trash','purging') or v['source'] not in ('drive','railway'):
            raise ValueError('분석할 강의가 없거나 휴지통으로 이동했습니다.')
        return dict(v)

    @staticmethod
    def version(data):
        return str(data.get('md5Checksum') or data.get('version') or data.get('modifiedTime'))+':'+str(data.get('size'))

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop.clear()
        self.keys.records()
        if self.keys.repair_legacy_project_waits():self.wake_available_keys()
        self.keys.report('startup')
        cfg=self.lib.config()
        if not cfg.get('key_pool_queue_migrated'):
            # Preserve known exhaustion on the imported key; the pool now schedules its reset.
            blocked=cfg.get('queue_blocked','')
            if '429' in blocked and '일일' in blocked:
                with self.lib.connect() as c:
                    c.execute("UPDATE lectures SET status='queued' WHERE pause=0 AND status='error' AND error LIKE '%429%'")
                self.lib.configure(queue_blocked='',provider_retry_at=0)
            self.lib.configure(key_pool_queue_migrated=True)
        # Reclaim only our derivative clips after an interrupted deployment.
        for p in self.temp.glob('*.mp4'):p.unlink(missing_ok=True)
        with self.lib.connect() as c:
            c.execute("UPDATE lectures SET status=CASE WHEN pause=1 THEN 'paused' ELSE 'queued' END WHERE status='analyzing'")
            pending=[dict(r) for r in c.execute("SELECT id,duration FROM lectures WHERE kind='video' AND status!='ready' AND duration>0 AND analysis_windows=''")]
        # Replan even while quota-blocked, so the UI immediately shows the actual remaining work.
        # This does not clear a quota block, change pause/status, or resubmit stored analysis.
        for row in pending:
            try:self.lib.analysis_plan(row['id'],row['duration'])
            except (ValueError,TypeError):
                self.lib.update(row['id'],warning='분석 구간 변경 전 원본 길이와 저장 자료 확인이 필요합니다.')
        if not self.lib.config().get('recovery_20261016_v2'):
            # Resume one previously requested job once after this repair; never override a user pause.
            with self.lib.connect() as c:
                row=c.execute("SELECT id FROM lectures WHERE status='error' AND pause=0 AND (error LIKE 'AI 연결 오류 (503)%' OR error LIKE 'AI 연결 오류 (429)%') ORDER BY created_at LIMIT 1").fetchone()
                if row:c.execute("UPDATE lectures SET status='queued',error='',phase='연결 복구 후 이어서 분석' WHERE id=?",(row[0],))
            self.lib.configure(recovery_20261016_v2=True)
        if not self.lib.config().get('recovery_20261016_timing'):
            with self.lib.connect() as c:
                row=c.execute("SELECT id FROM lectures WHERE status='error' AND pause=0 AND error LIKE '분석 시간 정보가 올바르지%' ORDER BY created_at LIMIT 1").fetchone()
                if row:c.execute("UPDATE lectures SET status='queued',error='',phase='분석 결과 저장 복구' WHERE id=?",(row[0],))
            self.lib.configure(recovery_20261016_timing=True)
        if not self.lib.config().get('recovery_20261016_backoff'):
            with self.lib.connect() as c:
                c.execute("UPDATE lectures SET status='retrying',retry_at=0,retry_count=0,phase='저장된 다음 구간부터 자동 재개' WHERE status='error' AND pause=0 AND (error LIKE '%HTTP 503%' OR error LIKE 'AI 연결 오류 (503)%')")
            self.lib.configure(recovery_20261016_backoff=True)
        self.thread=threading.Thread(target=self.run,daemon=True,name='maison-lecture-worker')
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            cfg=self.lib.config()
            if cfg.get('queue_blocked') or float(cfg.get('provider_retry_at') or 0)>time.time():
                # A key can be added while a previous request is returning its pool wait.
                # Re-evaluate the pool instead of allowing that stale global wait to block new keys.
                if cfg.get('provider_retry_scope') and self.keys.has_available(cfg['provider_retry_scope']):
                    self.wake_available_keys()
                else:
                    self.wake.wait(15);self.wake.clear();continue
            with self.lock:
                with self.lib.connect() as c:
                    row=c.execute("SELECT id FROM lectures WHERE pause=0 AND (status='queued' OR (status='retrying' AND retry_at<=?)) ORDER BY created_at LIMIT 1",(time.time(),)).fetchone()
                self.active=row[0] if row else None
                if row:self.lib.update(row[0],status='analyzing',error='')
            if row:
                try:self.process(row[0])
                except ProviderCancelled:
                    paused=bool(self.lib.lecture(row[0])['pause'])
                    self.lib.update(row[0],status='paused' if paused else 'queued',
                        phase='일시정지' if paused else '서버 재시작 후 이어서 분석',phase_at=now())
                except PoolUnavailable as error:
                    paused=bool(self.lib.lecture(row[0])['pause'])
                    self.lib.update(row[0],status='paused' if paused else 'retrying' if error.retry_at else 'error',
                        error=str(error),retry_at=0 if paused else error.retry_at,
                        phase='일시정지' if paused else '사용 가능한 API 키 대기',phase_at=now())
                    if not paused:
                        self.lib.configure(provider_retry_at=error.retry_at,provider_retry_scope=error.scope,queue_blocked='' if error.retry_at else str(error))
                    self.keys.report('waiting')
                except Exception as err:
                    safe=str(err) if isinstance(err,(ProviderError,ValueError,RuntimeError)) else '분석 연결이 중단되었습니다. 이어서 분석을 눌러 주세요.'
                    if self.defer_error(row[0],err):continue
                    self.lib.update(row[0],status='error',error=safe[:1000])
                    self.phase(row[0],'처리 중단')
                    print('LECTURE_FAILURE '+json.dumps(dict(lecture_id=row[0],error=safe[:1000],type=type(err).__name__),ensure_ascii=False),flush=True)
                    if isinstance(err,ProviderError) and err.code in (401,403,429):
                        # Avoid repeating the same account-wide failure for every queued lecture.
                        self.lib.configure(queue_blocked=safe[:1000])
                finally:
                    with self.lock:self.active=None
            else:self.wake.wait(3);self.wake.clear()

    def wake_available_keys(self):
        self.lib.configure(queue_blocked='',provider_retry_at=0,provider_retry_scope='')
        with self.lib.connect() as c:
            c.execute("UPDATE lectures SET status='queued',retry_at=0,retry_count=0 WHERE pause=0 AND (status='retrying' OR (status='error' AND (error LIKE '%API 키%' OR error LIKE '%429%' OR error LIKE '%401%' OR error LIKE '%403%'))) ")
        self.wake.set()

    def note(self,lid,text):
        with self.lock:
            previous=self.lib.lecture(lid)['warning'] or ''
            if text not in previous:self.lib.update(lid,warning=(previous+' '+text).strip())

    def process_part(self,lid,index,window,clip,video,version,cancelled):
        start,end=window;cache=self.lib.root/'temp'/f'{lid}-{index}.json'
        model_used=''
        with self.lock:self.inflight.add((lid,index))
        try:
            if cache.exists():segments=json.loads(cache.read_text('utf-8'))
            else:
                def analyze(api):
                    remote=None
                    try:
                        if cancelled():raise ProviderCancelled()
                        self.video(lid)
                        if self.version(self.metadata(video))!=version:
                            raise ValueError('분석 중 영상 원본이 변경되었습니다. 다시 분석을 눌러 주세요.')
                        remote=api.upload(clip)
                        raw=api.generate([{'fileData':{'fileUri':remote['uri'],'mimeType':'video/mp4'}},
                            {'text':f'이 클립은 {end-start:.2f}초입니다. 시작부터 끝까지 상세하게 기록하세요.'}],
                            ANALYSIS_SYSTEM,ANALYSIS_SCHEMA,'analysis')
                        segments=validate_segments(raw,start,end)
                        # Save paid analysis before embedding or cancellation can interrupt the task.
                        tmp=cache.with_suffix('.tmp');tmp.write_text(json.dumps(segments,ensure_ascii=False),encoding='utf-8');os.replace(tmp,cache)
                        return segments,api.effective_model
                    finally:
                        # Files belong to their uploading project: always remove with the same key.
                        if remote:api.remove(remote['name'])
                segments,model_used=self.keys.call(analyze,cancelled=cancelled,validate=True,
                    on_event=lambda event,stage,details:self.provider_event(lid,event,f'{index+1}구간 · '+stage,details))
            if any(s.get('timing')=='unknown' for s in segments):
                self.note(lid,'일부 설명은 AI 시간값을 확인하지 못해 시간 링크 없이 저장했습니다. 답변 내용과 원본 강의는 확인할 수 있습니다.')
            vectors=None;embedding_model=self.lib.config().get('embedding_model','gemini-embedding-2')
            try:
                vectors,embedding_model=self.keys.call(lambda api:(api.embed([s['title']+'\n'+s['text']+'\n'+s.get('screen_text','') for s in segments]),api.embedding_model),
                    scope='embedding',cancelled=cancelled)
            except ProviderError:
                self.note(lid,'일부 의미 검색 색인이 없습니다. 키워드 검색으로 답하며, 색인 보완으로 다시 처리할 수 있습니다.')
            self.lib.save_part(lid,index,segments,embedding_model,vectors)
            with self.lock:
                row=self.lib.lecture(lid)
                details={'model_used':model_used} if model_used else {}
                self.phase(lid,f"{row['completed']}/{row['total']}구간 저장 완료",**details)
            cache.unlink(missing_ok=True)
        finally:
            if clip:clip.unlink(missing_ok=True)
            with self.lock:self.inflight.discard((lid,index))

    def process(self,lid):
        cancelled=lambda:self.stop.is_set() or bool(self.lib.lecture(lid)['pause'])
        old=self.lib.lecture(lid)
        # Avoid encoding a large clip when every credential is already known to be unavailable.
        available=self.keys.acquire('embedding' if old['kind']=='reindex' else 'generation',cancelled,set())
        self.keys.release(available['id'])
        self.phase(lid,'영상 원본 정보 확인')
        video=self.video(lid);meta=self.metadata(video);version=self.version(meta)
        if old['sha256'] and old['sha256']!=version:
            raise ValueError('영상 원본이 변경되었습니다. 이 강의의 다시 분석을 눌러 주세요.')
        if old['media'] and old['media']!=self.core.MEDIA_IDENTITY(video):
            raise ValueError('연결된 원본이 변경되었습니다. 이 강의의 다시 분석을 눌러 주세요.')
        self.lib.update(lid,sha256=version,media=self.core.MEDIA_IDENTITY(video))
        if old['kind']=='reindex':
            from .store import normalize
            model=self.lib.config().get('embedding_model','gemini-embedding-2')
            with self.lib.connect() as c:
                rows=[dict(r) for r in c.execute('SELECT id,title,text,screen_text FROM chunks WHERE lecture_id=? AND (embedding IS NULL OR embedding_model!=?)',(lid,model))]
            for at in range(0,len(rows),16):
                group=rows[at:at+16]
                vecs,used_model=self.keys.call(lambda api:(api.embed([r['title']+'\n'+r['text']+'\n'+r['screen_text'] for r in group]),api.embedding_model),scope='embedding',cancelled=cancelled)
                with self.lib.connect() as c:
                    c.executemany('UPDATE chunks SET embedding=?,embedding_model=? WHERE id=?',[(normalize(v),used_model,r['id']) for r,v in zip(group,vecs)])
            self.lib.update(lid,status='ready',kind='video',warning='',error='');return
        with DriveSource(video['external_id'],self.auth_headers,int(meta.get('size') or 0),None,
                source_url=(lambda:self.core.OBJECT_MEDIA_URL(video)) if video['source']=='railway' else None) as source:
            length=float((meta.get('videoMediaMetadata') or {}).get('durationMillis') or 0)/1000 or probe(source)
            windows=self.lib.analysis_plan(lid,length);done=self.lib.parts_done(lid)
            missing=[i for i in range(len(windows)) if i not in done]
            parallelism=self.keys.capacity();pending={};errors=[]
            self.phase(lid,f'최대 {parallelism}개 구간 병렬 분석 준비')
            def collect(block=False):
                if not pending:return
                completed,_=wait(pending,timeout=.5 if block else 0,return_when=FIRST_COMPLETED)
                for future in completed:
                    pending.pop(future)
                    try:future.result()
                    except Exception as error:errors.append(error)
            with ClipPrefetch(source,windows,self.temp,lid,cancelled) as clips:
                with ThreadPoolExecutor(max_workers=parallelism,thread_name_prefix='lecture-analysis') as pool:
                    try:
                        for position,index in enumerate(missing):
                            while len(pending)>=parallelism and not errors:
                                if cancelled():raise ProviderCancelled()
                                collect(True)
                            collect()
                            if errors:break
                            if cancelled():raise ProviderCancelled()
                            if shutil.disk_usage(self.lib.root).free<64*1024**2:
                                raise RuntimeError('분석 자료를 저장할 공간이 부족합니다.')
                            clip=None
                            if not (self.lib.root/'temp'/f'{lid}-{index}.json').exists():
                                self.phase(lid,f'{index+1}/{len(windows)}구간 · 영상 준비 중')
                                clip=clips.take(index)
                                collect()
                                if errors:break
                                for following in missing[position+1:]:
                                    if not (self.lib.root/'temp'/f'{lid}-{following}.json').exists():
                                        clips.start(following);break
                            future=pool.submit(self.process_part,lid,index,windows[index],clip,video,version,cancelled)
                            pending[future]=index
                        while pending:
                            collect(True)
                            if errors:clips.stopping.set()
                    except BaseException:
                        clips.stopping.set()
                        raise
                if errors:
                    # Successful parallel parts have already committed even when another key failed.
                    raise next((e for e in errors if not isinstance(e,PoolUnavailable)),errors[0])
        self.video(lid)
        if self.version(self.metadata(video))!=version:
            raise ValueError('영상 원본이 변경되었습니다. 다시 분석을 눌러 주세요.')
        if len(self.lib.parts_done(lid))!=len(windows):
            raise RuntimeError('일부 구간이 남아 있습니다. 저장된 다음 구간부터 이어서 분석해 주세요.')
        self.lib.update(lid,status='ready',error='',pause=0,phase='분석 완료',phase_at=now())

