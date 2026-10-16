"""Isolated auth, durable checkpoint, evidence, and actual range/FFmpeg checks. No paid AI calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest.mock import Mock,patch

TEMP=tempfile.TemporaryDirectory()
os.environ['CLASSROOM_DATA_DIR']=TEMP.name
os.environ['PUBLIC_ORIGIN']='http://testserver'
os.environ.pop('GEMINI_API_KEY',None)
import app as core
from fastapi.testclient import TestClient
from lecture_ai.routes import install
from lecture_ai.worker import DriveSource,make_clip,probe,validate_segments
from lecture_ai.store import Library,clip_windows
from lecture_ai.provider import Gemini, ProviderError, ProviderCancelled, FALLBACK_MODEL

worker=install(core,lambda:{'Authorization':'Bearer test-only'})
lib=worker.lib


def seed_member(role,state='approved'):
    mid=core.uid();raw=core.uid();csrf=core.uid()
    with core.db() as c:
        c.execute('INSERT INTO members(id,name,phone,lookup,password,role,state,created,consent,consent_text) VALUES(?,?,?,?,?,?,?,?,?,?)',
          (mid,core.enc('test'),core.enc(mid),mid,'unused',role,state,time.time(),time.time(),'{}'))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(core.token_hash(raw),mid,1,csrf,time.time()+3600))
    cl=TestClient(core.app);cl.cookies.set(core.COOKIE,raw)
    cl.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
    return cl,mid


def seed_video(state='public'):
    vid=core.uid()
    with core.db() as c:
        c.execute('INSERT INTO videos(id,title,description,size,mime,duration,state,created,source,external_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (vid,'테스트 '+vid[:5],'',1000,'video/mp4',601,state,time.time(),'drive','file_'+vid))
    return vid


def seed_knowledge(vid,text='화면 설정은 환경설정에서 해상도를 선택합니다.'):
    with lib.connect() as c:
        c.execute("INSERT INTO lectures(id,title,lecture_date,created_at,filename,status,kind) VALUES(?,?,?,?,?,'ready','video')",(vid,'강의','2026-10-16','2026-10-16','test'))
    lib.save_part(vid,0,[dict(start=20,end=40,title='설정',text=text,transcript=text,screen_text='',timing='estimated')],model='gemini-embedding-2',vectors=[[1,0]])


class FakeProvider:
    embedding_model='gemini-embedding-2'
    model='gemini-3.8-flash'
    effective_model=model
    key='test'
    calls=[]
    def validate_models(self):return {}
    def check_connection(self):return self.model
    def check_cancelled(self):pass
    def embed(self,texts,query=False):return [[1,0] for _ in texts]
    def generate(self,parts,system,schema,kind='answer'):
        data=json.loads(parts[0]['text']);self.calls.append(data)
        return {'supported':True,'paragraphs':[{'text':'환경설정에서 해상도를 선택하세요.','sources':[x['id'] for x in data['EVIDENCE']]}],'followups':[]}


class LectureTests(unittest.TestCase):
    def setUp(self):
        with lib.connect() as c:
            c.execute('DELETE FROM search_index');c.execute('DELETE FROM lectures');c.execute('DELETE FROM config')
        with core.db() as c:c.execute('DELETE FROM videos');c.execute('DELETE FROM sessions');c.execute('DELETE FROM members')
        self.admin,self.aid=seed_member('admin');self.student,self.sid=seed_member('member')
        FakeProvider.calls=[]

    def configure(self):lib.configure(key_encrypted=core.enc('test-key'))

    def test_access_and_csrf(self):
        anon=TestClient(core.app)
        self.assertEqual(anon.get('/api/ai/lectures').status_code,401)
        self.assertEqual(self.student.get('/api/admin/ai/settings').status_code,403)
        self.admin.headers.pop('X-CSRF-Token')
        self.assertEqual(self.admin.post('/api/admin/ai/queue-new',json={}).status_code,403)

    def test_key_encrypted_and_never_returned(self):
        self.assertFalse(self.admin.get('/api/admin/ai/settings').json()['configured'])
        with patch('lecture_ai.routes.Gemini.validate_models'),patch('lecture_ai.routes.Gemini.check_connection',return_value='gemini-3.8-flash'):
            r=self.admin.post('/api/admin/ai/settings',json={'api_key':'a-private-key'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertNotIn('a-private-key',r.text)
        self.assertNotIn('a-private-key',lib.config()['key_encrypted'])
        self.assertEqual(worker.provider().key,'a-private-key')

    def test_key_saved_even_when_actual_model_hits_quota(self):
        with patch('lecture_ai.routes.Gemini.validate_models'),patch('lecture_ai.routes.Gemini.check_connection',side_effect=ProviderError('분당 한도',code=429)):
            r=self.admin.post('/api/admin/ai/settings',json={'api_key':'a-private-key'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['configured'])
        self.assertFalse(r.json()['connection']['ok'])
        self.assertEqual(worker.provider().key,'a-private-key')
        self.assertEqual(lib.config()['queue_blocked'],'분당 한도')
        # Empty input preserves the encrypted key; a working check can unblock pending work.
        with patch('lecture_ai.routes.Gemini.validate_models'),patch('lecture_ai.routes.Gemini.check_connection',return_value='gemini-3.8-flash'):
            r=self.admin.post('/api/admin/ai/settings',json={'api_key':''})
        self.assertTrue(r.json()['connection']['ok'])
        self.assertEqual(worker.provider().key,'a-private-key')
        self.assertFalse(lib.config()['queue_blocked'])

    def test_quota_stops_other_queued_lectures(self):
        self.configure();first=seed_video();second=seed_video()
        self.admin.post('/api/admin/ai/queue-new',json={})
        worker.stop.clear();worker.wake.clear()
        def stop_wait(*args):worker.stop.set()
        try:
            with patch.object(worker,'process',side_effect=ProviderError('허용량 0',code=429,retryable=False)) as process,patch.object(worker.wake,'wait',side_effect=stop_wait):
                worker.run()
            self.assertEqual(process.call_count,1)
            self.assertTrue(lib.config()['queue_blocked'])
            self.assertEqual(sorted(r['status'] for r in lib.list()),['error','queued'])
        finally:worker.stop.clear()

    def test_one_time_recovery_preserves_user_pause(self):
        self.configure();first=seed_video();paused=seed_video();recent=seed_video()
        self.admin.post('/api/admin/ai/queue-new',json={})
        lib.update(first,status='error',error='AI 연결 오류 (503): retry')
        lib.update(paused,status='error',error='강의 분석 실패 (HTTP 503 / UNAVAILABLE)',pause=1)
        lib.update(recent,status='error',error='강의 분석 실패 (HTTP 503 / UNAVAILABLE)')
        try:
            with patch('lecture_ai.worker.threading.Thread'):
                worker.thread=None;worker.start()
                self.assertEqual(lib.lecture(first)['status'],'queued')
                self.assertEqual(lib.lecture(paused)['status'],'error')
                self.assertEqual(lib.lecture(recent)['status'],'retrying')
                lib.update(first,status='error')
                lib.update(recent,status='error')
                worker.thread=None;worker.start()
                self.assertEqual(lib.lecture(first)['status'],'error')
                self.assertEqual(lib.lecture(recent)['status'],'error')
        finally:worker.thread=None

    def test_transient_failure_persists_and_cools_down_the_whole_queue(self):
        self.configure();first=seed_video();second=seed_video()
        self.admin.post('/api/admin/ai/lectures/'+first,json={'action':'start'})
        self.admin.post('/api/admin/ai/lectures/'+second,json={'action':'start'})
        lib.save_part(first,0,[dict(text='저장한 내용')])
        worker.stop.clear();worker.wake.clear()
        def stop_wait(*args):worker.stop.set()
        try:
            with patch.object(worker,'process',side_effect=ProviderError('일시 서버 오류',code=503)) as process,patch.object(worker.wake,'wait',side_effect=stop_wait):
                worker.run()
            process.assert_called_once_with(first)
            persisted=Library(lib.root)
            row=persisted.lecture(first)
            self.assertEqual(row['status'],'retrying')
            self.assertEqual(row['retry_count'],1)
            self.assertGreater(row['retry_at'],time.time()+110)
            self.assertEqual(persisted.config()['provider_retry_at'],row['retry_at'])
            self.assertEqual(persisted.parts_done(first),{0})
            self.assertEqual(persisted.lecture(second)['status'],'queued')
            data=self.admin.get('/api/ai/lectures').json()
            self.assertEqual(data['queue_retry_at'],row['retry_at'])
            self.assertEqual(next(v for v in data['items'] if v['id']==first)['retry_at'],row['retry_at'])
            worker.stop.clear()
            def finish(lid):
                self.assertEqual(lib.parts_done(lid),{0})
                lib.update(lid,status='ready');worker.stop.set()
            with patch('lecture_ai.worker.time.time',return_value=row['retry_at']+1),patch.object(worker,'process',side_effect=finish) as resumed:
                worker.run()
            resumed.assert_called_once_with(first)
        finally:worker.stop.clear()

    def test_user_pause_cancels_scheduled_resume(self):
        self.configure();vid=seed_video()
        self.admin.post('/api/admin/ai/lectures/'+vid,json={'action':'start'})
        self.assertTrue(worker.defer_error(vid,ProviderError('일시 오류',code=503)))
        due=lib.lecture(vid)['retry_at']
        self.assertEqual(self.admin.post('/api/admin/ai/lectures/'+vid,json={'action':'pause'}).status_code,200)
        self.assertEqual(lib.lecture(vid)['status'],'paused')
        self.assertEqual(lib.lecture(vid)['retry_at'],0)
        worker.stop.clear()
        try:
            with patch('lecture_ai.worker.time.time',return_value=due+1),patch.object(worker,'process') as process,patch.object(worker.wake,'wait',side_effect=lambda *a:worker.stop.set()):
                worker.run()
            process.assert_not_called()
        finally:worker.stop.clear()

    def test_retry_budget_resets_after_progress_and_is_bounded(self):
        self.configure();vid=seed_video()
        self.admin.post('/api/admin/ai/lectures/'+vid,json={'action':'start'})
        error=ProviderError('서버 일시 오류',code=503,retry_after=180)
        self.assertTrue(worker.defer_error(vid,error))
        self.assertGreater(lib.lecture(vid)['retry_at'],time.time()+170)
        lib.save_part(vid,0,[dict(text='저장 완료')])
        self.assertEqual(lib.lecture(vid)['retry_count'],0)
        self.assertTrue(worker.defer_error(vid,error))
        self.assertEqual(lib.lecture(vid)['retry_count'],1)
        lib.update(vid,retry_count=6)
        self.assertFalse(worker.defer_error(vid,error))
        self.assertIn('6회',lib.config()['queue_blocked'])
        self.assertFalse(worker.defer_error(vid,ProviderError('사용량 소진',code=429,retryable=False)))

    def test_cumulative_queue_and_resume_preserves_parts(self):
        self.configure();v1=seed_video();v2=seed_video()
        self.assertEqual(self.admin.post('/api/admin/ai/queue-new',json={}).json()['added'],2)
        self.assertEqual(self.admin.post('/api/admin/ai/queue-new',json={}).json()['added'],0)
        lib.save_part(v1,0,[dict(start=0,end=10,title='test',text='보존할 설명',transcript='',screen_text='',timing='estimated')])
        lib.update(v1,status='error')
        self.assertEqual(self.admin.post('/api/admin/ai/lectures/'+v1,json={'action':'start'}).status_code,200)
        self.assertEqual(lib.parts_done(v1),{0})
        v3=seed_video()
        self.assertEqual(self.admin.post('/api/admin/ai/queue-new',json={}).json()['added'],1)
        self.assertEqual(len(lib.list()),3)
        self.assertEqual(len(clip_windows(6*3600)),72)

    def test_private_material_filtered_before_provider(self):
        self.configure();pub=seed_video();private=seed_video('private')
        seed_knowledge(pub);seed_knowledge(private,'외부에 공개하지 않은 내부 비밀 설정')
        with patch.object(worker,'provider',return_value=FakeProvider()):
            r=self.student.post('/api/ai/ask',json={'question':'화면 설정 방법'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['supported'])
        self.assertEqual({s['lecture_id'] for s in r.json()['sources']},{pub})
        self.assertNotIn('내부 비밀',json.dumps(FakeProvider.calls,ensure_ascii=False))
        self.assertEqual(self.student.post('/api/ai/ask',json={'question':'설정 방법','lecture_id':private}).status_code,403)

    def test_permission_change_during_answer(self):
        self.configure();vid=seed_video();seed_knowledge(vid)
        class Changing(FakeProvider):
            def generate(self,*a,**kw):
                out=super().generate(*a,**kw)
                with core.db() as c:c.execute("UPDATE videos SET state='private' WHERE id=?",(vid,))
                return out
        with patch.object(worker,'provider',return_value=Changing()):
            self.assertEqual(self.student.post('/api/ai/ask',json={'question':'화면 설정'}).status_code,403)

    def test_unsupported_or_forged_source_not_displayed(self):
        self.configure();vid=seed_video();seed_knowledge(vid)
        api=FakeProvider()
        with patch.object(worker,'provider',return_value=api),patch.object(api,'generate',return_value={'supported':True,'paragraphs':[{'text':'근거 없는 답변','sources':[999999]}]}):
            data=self.student.post('/api/ai/ask',json={'question':'화면 설정'}).json()
        self.assertFalse(data['supported']);self.assertNotIn('근거 없는 답변',json.dumps(data,ensure_ascii=False))

    def test_checkpoint_resume_only_missing_part(self):
        self.configure();vid=seed_video()
        self.admin.post('/api/admin/ai/lectures/'+vid,json={'action':'start'})
        video=worker.video(vid)
        meta={'id':video['external_id'],'size':1000,'md5Checksum':'same','videoMediaMetadata':{'durationMillis':'601000'}}
        class Analysis(FakeProvider):
            count=0
            fail=True
            def upload(self,path):return {'name':'files/mock','uri':'mock'}
            def remove(self,name):pass
            def generate(self,*a,**kw):
                self.count+=1
                if self.fail and self.count==2:raise ProviderError('retry')
                return {'segments':[dict(start=0,end=2,title='설명',text='누적 보존',transcript='누적 보존',screen_text='')]}
        api=Analysis()
        with patch.object(worker,'provider',return_value=api),patch.object(worker,'metadata',return_value=meta),patch('lecture_ai.worker.DriveSource',return_value=contextlib.nullcontext('loopback')),patch('lecture_ai.worker.make_clip'):
            with self.assertRaises(ProviderError):worker.process(vid)
            self.assertEqual(lib.parts_done(vid),{0})
            api.fail=False;worker.process(vid)
        self.assertEqual(api.count,4)
        self.assertEqual(lib.parts_done(vid),{0,1,2})
        self.assertEqual(lib.lecture(vid)['status'],'ready')
        meta['md5Checksum']='changed'
        with patch.object(worker,'provider',return_value=api),patch.object(worker,'metadata',return_value=meta):
            with self.assertRaisesRegex(ValueError,'변경'):worker.process(vid)

    def test_real_ffmpeg_range_proxy(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.mp4';target=Path(folder)/'part.mp4'
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=320x180:r=10','-f','lavfi','-i','sine=frequency=440:sample_rate=24000','-t','6','-c:v','libx264','-threads','1','-c:a','aac',str(source)],check=True)
            data=source.read_bytes();ranges=[]
            class Response:
                def __init__(self,headers):
                    value=headers.get('Range');ranges.append(value)
                    start,end=0,len(data)-1
                    if value:
                        a,b=value.removeprefix('bytes=').split('-');start=int(a);end=min(int(b) if b else end,end)
                    self.status_code=206 if value else 200
                    self.headers={'Content-Length':str(end-start+1),'Content-Type':'video/mp4'}
                    if value:self.headers['Content-Range']=f'bytes {start}-{end}/{len(data)}'
                    self.data=data[start:end+1]
                def iter_content(self,n):
                    for i in range(0,len(self.data),n):yield self.data[i:i+n]
                def close(self):pass
            with patch('lecture_ai.worker.requests.get',side_effect=lambda *a,**kw:Response(kw['headers'])):
                with DriveSource('test-file',lambda:{'Authorization':'test'},len(data),None) as url:
                    make_clip(url,target,2,5)
            self.assertAlmostEqual(probe(str(target)),3,delta=.2)
            self.assertTrue(any(ranges))

    def test_prefetch_overlaps_analysis_and_preserves_saved_parts(self):
        self.configure();vid=seed_video()
        self.admin.post('/api/admin/ai/lectures/'+vid,json={'action':'start'})
        lib.save_part(vid,0,[dict(text='이미 저장된 구간')])
        video=worker.video(vid)
        meta={'id':video['external_id'],'size':1000,'md5Checksum':'same','videoMediaMetadata':{'durationMillis':'601000'}}
        next_started=threading.Event();release_next=threading.Event()
        prepared=[];active=0;peak=0;guard=threading.Lock()
        def prepare(source,path,start,end,cancelled=None):
            nonlocal active,peak
            index=int(path.stem.rsplit('-',1)[1])
            with guard:active+=1;peak=max(peak,active)
            try:
                prepared.append(index);path.write_bytes(b'prepared')
                if index==2:
                    next_started.set()
                    if not release_next.wait(2):raise AssertionError('Analysis did not overlap preparation')
            finally:
                with guard:active-=1
        case=self
        class Analysis(FakeProvider):
            count=0
            def upload(self,path):return {'name':'files/mock','uri':'mock'}
            def remove(self,name):pass
            def generate(self,*a,**kw):
                self.count+=1
                if self.count==1:
                    case.assertTrue(next_started.wait(2),'Next clip was not prepared during analysis')
                    release_next.set()
                return {'segments':[dict(start=0,end=2,title='설명',text='기록',transcript='기록',screen_text='')]}
        api=Analysis()
        with patch.object(worker,'provider',return_value=api),patch.object(worker,'metadata',return_value=meta),patch('lecture_ai.worker.DriveSource',return_value=contextlib.nullcontext('loopback')),patch('lecture_ai.worker.make_clip',side_effect=prepare),patch.object(api,'check_connection',side_effect=AssertionError('Extra generation probe')) as check:
            worker.process(vid)
        check.assert_not_called()
        self.assertEqual(prepared,[1,2]);self.assertEqual(peak,1)
        self.assertEqual(api.count,2);self.assertEqual(lib.parts_done(vid),{0,1,2})
        self.assertEqual(lib.lecture(vid)['status'],'ready')
        self.assertFalse(list(worker.temp.glob(vid+'-*.mp4')))

    def test_failure_cancels_prefetch_without_losing_progress(self):
        self.configure();vid=seed_video()
        self.admin.post('/api/admin/ai/lectures/'+vid,json={'action':'start'})
        lib.save_part(vid,0,[dict(text='유지할 구간')])
        video=worker.video(vid)
        meta={'id':video['external_id'],'size':1000,'md5Checksum':'same','videoMediaMetadata':{'durationMillis':'601000'}}
        next_started=threading.Event();next_cancelled=threading.Event()
        def prepare(source,path,start,end,cancelled=None):
            path.write_bytes(b'prepared')
            if path.stem.endswith('-2'):
                next_started.set();deadline=time.monotonic()+2
                while not cancelled():
                    if time.monotonic()>deadline:raise AssertionError('Prefetch did not stop')
                    next_cancelled.wait(.01)
                next_cancelled.set();raise ProviderCancelled()
        case=self
        class Analysis(FakeProvider):
            def upload(self,path):return {'name':'files/mock','uri':'mock'}
            def remove(self,name):pass
            def generate(self,*a,**kw):
                case.assertTrue(next_started.wait(2))
                raise ProviderError('temporary',code=503)
        with patch.object(worker,'provider',return_value=Analysis()),patch.object(worker,'metadata',return_value=meta),patch('lecture_ai.worker.DriveSource',return_value=contextlib.nullcontext('loopback')),patch('lecture_ai.worker.make_clip',side_effect=prepare):
            with self.assertRaises(ProviderError) as error:worker.process(vid)
        self.assertEqual(error.exception.code,503)
        self.assertTrue(next_cancelled.is_set())
        self.assertEqual(lib.parts_done(vid),{0})
        self.assertFalse(list(worker.temp.glob(vid+'-*.mp4')))

    def test_cancelling_clip_terminates_encoder(self):
        process=Mock();process.poll.return_value=None
        process.wait.side_effect=[subprocess.TimeoutExpired('ffmpeg',.5),0]
        cancelled=Mock(side_effect=[False,False,True])
        with patch('lecture_ai.worker.subprocess.Popen',return_value=process),patch('lecture_ai.worker.probe') as check:
            with self.assertRaises(ProviderCancelled):make_clip('test',Path('/tmp/not-written.mp4'),0,5,cancelled)
        process.terminate.assert_called_once();check.assert_not_called()


class ProviderTests(unittest.TestCase):
    def test_invalid_timestamp_keeps_text_without_fabricating_citation(self):
        raw={'segments':[dict(start=0,end=25,text='정상 설명'),dict(start=250,end=600,text='시간 오류 설명'),
                         dict(start=None,end=None,text='시간 없는 설명')]}
        segments=validate_segments(raw,300,603)
        self.assertEqual(len(segments),3)
        self.assertEqual((segments[0]['start'],segments[0]['end']),(300,325))
        for segment in segments[1:]:
            self.assertIsNone(segment['start']);self.assertIsNone(segment['end'])
            self.assertEqual(segment['timing'],'unknown')

    def test_http_retry_and_secret_free_diagnostics(self):
        api=Gemini('secret-key')
        class Response(io.BytesIO):headers={}
        def error():
            return urllib.error.HTTPError('https://generativelanguage.googleapis.com/?key=secret-key',503,'Unavailable',{},
                io.BytesIO(json.dumps({'error':{'status':'UNAVAILABLE','message':'High demand secret-key private lecture text'}}).encode()))
        out=io.StringIO()
        with contextlib.redirect_stdout(out),patch('urllib.request.urlopen',side_effect=[error(),Response(b'{}')]) as request,patch.object(api,'wait') as wait:
            api.request('/v1beta/test',stage='영상 업로드 준비')
        self.assertEqual(request.call_count,2);wait.assert_called_once()
        self.assertIn('503',out.getvalue())
        self.assertIn('영상 업로드 준비',out.getvalue())
        self.assertNotIn('secret-key',out.getvalue());self.assertNotIn('private lecture',out.getvalue())

    def test_zero_or_daily_quota_is_not_retried(self):
        for value,quota_id in [('0','GenerateRequestsPerMinute'),('20','GenerateRequestsPerDay')]:
            with self.subTest(quota_id=quota_id):
                api=Gemini('test')
                error=urllib.error.HTTPError('https://generativelanguage.googleapis.com',429,'Quota',{},io.BytesIO(json.dumps({'error':{
                    'status':'RESOURCE_EXHAUSTED','message':'quota exceeded','details':[{'violations':[{'quotaId':quota_id,'quotaValue':value}]}]}}).encode()))
                with patch('urllib.request.urlopen',side_effect=error) as request,patch.object(api,'wait') as wait:
                    with self.assertRaises(ProviderError) as raised:api.request('/v1beta/test',stage='강의 분석')
                self.assertEqual(request.call_count,1);wait.assert_not_called()
                self.assertFalse(raised.exception.retryable)
                self.assertIn(quota_id,str(raised.exception))

    def test_upload_retry_uses_new_session_or_recovers_completed_file(self):
        for recover in (False,True):
            with self.subTest(recover=recover),tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'part.mp4';path.write_bytes(b'video')
                api=Gemini('test');starts=[];sent=[]
                def request(url,payload=None,**kwargs):
                    if url=='/upload/v1beta/files':
                        starts.append(payload['file']['name'])
                        return {},{'x-goog-upload-url':f'https://generativelanguage.googleapis.com/upload/{len(starts)}'}
                    if kwargs.get('binary'):
                        sent.append(url)
                        if len(sent)==1:raise ProviderError('transient',code=503)
                        return {'file':{'name':starts[-1],'uri':'video','state':'ACTIVE'}},{}
                    if kwargs.get('method')=='DELETE':return {},{}
                    if recover:return {'name':starts[0],'uri':'video','state':'ACTIVE'},{}
                    raise ProviderError('not found',code=404)
                with patch.object(api,'request',side_effect=request),patch.object(api,'wait'):
                    file=api.upload(path)
                self.assertEqual(file['state'],'ACTIVE')
                self.assertEqual(len(starts),1 if recover else 2)
                self.assertEqual(len(set(sent)),len(sent))

    def test_fallback_only_on_server_errors(self):
        for code in (503,429,403):
            with self.subTest(code=code):
                api=Gemini('test');api._models={'generation':[api.model,FALLBACK_MODEL]}
                calls=[]
                def request(url,*args,**kwargs):
                    calls.append(url)
                    if api.model in url:raise ProviderError('failure',code=code)
                    return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"ok":true}'}]}}]},{}
                with patch.object(api,'request',side_effect=request):
                    if code==503:
                        self.assertTrue(api.generate([], '', {})['ok'])
                        self.assertEqual(api.effective_model,FALLBACK_MODEL)
                        self.assertEqual(len(calls),2)
                    else:
                        with self.assertRaises(ProviderError):api.generate([], '', {})
                        self.assertEqual(len(calls),1)

    def test_pause_interrupts_backoff(self):
        api=Gemini('test',cancelled=lambda:True)
        with self.assertRaises(ProviderCancelled):api.wait(30)

    def test_fallback_can_return_to_primary_without_looping(self):
        for both_fail in (False,True):
            with self.subTest(both_fail=both_fail):
                api=Gemini('test');api.effective_model=FALLBACK_MODEL
                api._models={'generation':[api.model,FALLBACK_MODEL]}
                calls=[]
                def request(url,*args,**kwargs):
                    calls.append(url)
                    if FALLBACK_MODEL in url or both_fail:raise ProviderError('일시 오류',code=503)
                    return {'candidates':[{'content':{'parts':[{'text':'{"ok":true}'}]}}]},{}
                with patch.object(api,'request',side_effect=request):
                    if both_fail:
                        with self.assertRaises(ProviderError):api.generate([], '', {})
                    else:
                        self.assertTrue(api.generate([], '', {})['ok'])
                        self.assertEqual(api.effective_model,api.model)
                self.assertEqual(len(calls),2)


if __name__=='__main__':unittest.main(verbosity=2)
