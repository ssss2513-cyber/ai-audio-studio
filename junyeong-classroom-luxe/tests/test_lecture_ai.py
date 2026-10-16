"""Isolated auth, durable checkpoint, evidence, and actual range/FFmpeg checks. No paid AI calls."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

TEMP=tempfile.TemporaryDirectory()
os.environ['CLASSROOM_DATA_DIR']=TEMP.name
os.environ['PUBLIC_ORIGIN']='http://testserver'
os.environ.pop('GEMINI_API_KEY',None)
import app as core
from fastapi.testclient import TestClient
from lecture_ai.routes import install
from lecture_ai.worker import DriveSource,make_clip,probe
from lecture_ai.store import clip_windows
from lecture_ai.provider import ProviderError

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
    key='test'
    calls=[]
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
        with patch('lecture_ai.routes.Gemini.models',return_value={'generation':[],'embedding':[]}):
            r=self.admin.post('/api/admin/ai/settings',json={'api_key':'a-private-key'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertNotIn('a-private-key',r.text)
        self.assertNotIn('a-private-key',lib.config()['key_encrypted'])
        self.assertEqual(worker.provider().key,'a-private-key')

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


if __name__=='__main__':unittest.main(verbosity=2)
