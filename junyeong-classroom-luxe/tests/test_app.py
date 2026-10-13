"""Local regression tests. RUN_LARGE_UPLOAD=1 includes synthetic 2 GiB transfer.
Synthetic payload transfer is not real video codec or smartphone playback verification.
"""
import atexit, hashlib, os, shutil, sys, tempfile, time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
WORK=tempfile.mkdtemp(prefix='jc-tests-'); atexit.register(shutil.rmtree,WORK,ignore_errors=True)
os.environ['CLASSROOM_DATA_DIR']=WORK; os.environ['PUBLIC_ORIGIN']='http://testserver'
import app as a

@pytest.fixture(autouse=True)
def clean():
    with a.db() as c:
        for t in ['sessions','progress','parts','uploads','videos','members','audits','rates']:
            c.execute('DELETE FROM '+t)
        c.execute('UPDATE settings SET value=? WHERE id=1',(a.json.dumps(a.DEFAULT),))
    for name in ['media','parts','posters']:
        shutil.rmtree(a.DATA/name,ignore_errors=True); (a.DATA/name).mkdir()

def client():
    return TestClient(a.app,base_url='http://testserver',headers={'Origin':'http://testserver'})
def auth(c,out):
    assert out.status_code==200,out.text
    c.headers['X-CSRF-Token']=out.json()['csrf']
    return out.json()['member']
def admin():
    with a.db() as db:
        mid=a.uid();now=time.time()
        db.execute("INSERT INTO members(id,name,phone,lookup,password,role,state,created,consent,consent_text) VALUES(?,?,?,?,?,'admin','approved',?,?,?)",
                   (mid,a.enc('테스트 운영자'),a.enc('operator@example.test'),a.digest('operator@example.test'),
                    a.hash_password('test-admin-only'),now,now,'{}'))
    c=client()
    auth(c,c.post('/api/login',json={'identifier':'operator@example.test','password':'test-admin-only','admin':True}))
    return c
def member(number='01000000001',pwd='abcdefgh'):
    c=client()
    r=c.post('/api/join',json={'name':'테스트 회원','phone':number,'password':pwd,'confirmation':pwd,'consent':True})
    return c,auth(c,r)
def info(c,mid):return c.get('/api/admin/members/'+mid).json()
def change(c,m,action,**extra):
    return c.post('/api/admin/members/'+m['id'],json={'action':action,'revision':m['revision'],**extra})
def make_upload(c,size=256,raw=None):
    r=c.post('/api/admin/uploads',json={'title':'테스트 영상','description':'검증용','filename':'test.mp4',
        'size':size,'mime':'video/mp4','fingerprint':'a'*64,'rights':True,'duration':10})
    assert r.status_code==200,r.text
    u=r.json();data=raw or b'\x00\x00\x00\x18ftypisom'+bytes(size-12)
    r=c.put(f"/api/admin/uploads/{u['id']}/parts/0",content=data,
            headers={'X-Part-SHA256':hashlib.sha256(data).hexdigest()})
    assert r.status_code==200,r.text
    return u,data
def video(c):
    u,data=make_upload(c)
    r=c.post('/api/admin/uploads/'+u['id']+'/finish',json={});assert r.status_code==200,r.text
    v=c.get('/api/admin/videos').json()['items'][0]
    r=c.post('/api/admin/videos/'+v['id'],json={'action':'publish','revision':v['revision']})
    assert r.status_code==200
    return v['id'],data

def test_password_boundaries_and_phone_not_verified():
    admin();m=client()
    r=m.post('/api/join',json={'name':'테스트','phone':'01000000001','password':'1234567','confirmation':'1234567','consent':True})
    assert r.status_code==422
    user,out=member()
    assert out['state']=='pending' and out['verified'] is False
    assert user.get('/api/session').json()['member']['id']==out['id']
    with a.db() as db:
        stored=db.execute('SELECT * FROM members WHERE id=?',(out['id'],)).fetchone()
        assert 'abcdefgh' not in stored['password'] and stored['password'].startswith('$argon2id$')
        assert '01000000001' not in stored['phone'] and '테스트 회원' not in stored['name']

def test_access_before_approval_and_permission_transitions():
    c=admin();vid,data=video(c);m,user=member()
    assert client().get('/api/videos/'+vid+'/stream').status_code==401
    assert m.get('/api/videos/'+vid+'/stream').status_code==403
    assert change(c,user,'approve').status_code==200
    r=m.get('/api/videos/'+vid+'/stream',headers={'Range':'bytes=4-7'})
    assert r.status_code==206 and r.content==b'ftyp'
    assert change(c,info(c,user['id']),'revoke').status_code==200
    assert m.get('/api/videos/'+vid+'/stream').status_code==401
    auth(m,m.post('/api/login',json={'identifier':'01000000001','password':'abcdefgh'}))
    assert m.get('/api/videos/'+vid+'/stream').status_code==403

def test_block_remove_restore_auto_approval_does_not_revive():
    c=admin();m,user=member()
    assert change(c,user,'block',reason='테스트').status_code==200
    assert m.post('/api/login',json={'identifier':'01000000001','password':'abcdefgh'}).status_code==403
    s=c.get('/api/meta').json()
    fields={k:s[k] for k in ['title','contact','retention','open','revision']}
    assert c.post('/api/admin/settings',json={**fields,'autoApprove':True}).status_code==200
    assert info(c,user['id'])['state']=='blocked'
    assert change(c,info(c,user['id']),'approve').status_code==409
    assert change(c,info(c,user['id']),'unblock').status_code==200
    assert info(c,user['id'])['state']=='pending'
    assert change(c,info(c,user['id']),'remove',reason='회원 해제',confirmation='테스트 회원').status_code==200
    assert m.post('/api/login',json={'identifier':'01000000001','password':'abcdefgh'}).status_code==403
    assert m.post('/api/join',json={'name':'다시가입','phone':'01000000001','password':'abcdefgh','confirmation':'abcdefgh','consent':True}).status_code==409
    assert change(c,info(c,user['id']),'restore').status_code==200
    assert info(c,user['id'])['state']=='pending'
    _,normal=member('01000000002')
    assert normal['state']=='approved'

def test_restriction_invalidates_all_sessions():
    c=admin();m,user=member();second=client()
    auth(second,second.post('/api/login',json={'identifier':'01000000001','password':'abcdefgh'}))
    assert change(c,user,'block',reason='테스트').status_code==200
    assert m.get('/api/session').json()['member'] is None
    assert second.get('/api/session').json()['member'] is None

def test_csrf_origin_admin_permissions_and_conflict():
    c=admin();m,user=member()
    assert m.get('/api/admin/members').status_code==403
    assert c.post('/api/admin/members/'+user['id'],json={'action':'approve','revision':1},
                  headers={'Origin':'https://wrong.example'}).status_code==403
    assert c.post('/api/admin/members/'+user['id'],json={'action':'approve','revision':1},
                  headers={'X-CSRF-Token':'wrong'}).status_code==403
    assert change(c,user,'approve').status_code==200
    assert change(c,user,'revoke').status_code==409
    assert c.get('/api/session').headers['Cache-Control'].startswith('private, no-store')

def test_metadata_edit_and_trash_restore_purge():
    c=admin();vid,data=video(c);m,user=member();change(c,user,'approve')
    def v():return c.get('/api/admin/videos').json()['items'][0]
    assert c.post('/api/admin/videos/'+vid,json={'action':'private','revision':v()['revision']}).status_code==200
    assert m.get('/api/videos/'+vid+'/stream').status_code==403
    assert c.post('/api/admin/videos/'+vid,json={'action':'trash','revision':v()['revision']}).status_code==200
    assert (a.DATA/'media'/vid).exists()
    assert c.post('/api/admin/videos/'+vid,json={'action':'publish','revision':v()['revision']}).status_code==409
    assert c.post('/api/admin/videos/'+vid,json={'action':'restore','revision':v()['revision']}).status_code==200
    assert v()['state']=='private'
    assert c.post('/api/admin/videos/'+vid,json={'action':'trash','revision':v()['revision']}).status_code==200
    assert c.post('/api/admin/videos/'+vid,json={'action':'purge','revision':v()['revision'],'confirmation':'틀림'}).status_code==422
    assert c.post('/api/admin/videos/'+vid,json={'action':'purge','revision':v()['revision'],'confirmation':'테스트 영상'}).status_code==200
    assert not (a.DATA/'media'/vid).exists()

def test_byte_range_validation():
    c=admin();vid,data=video(c)
    for value,expected in [('bytes=0-3',data[:4]),('bytes=-10',data[-10:]),('bytes=4-',data[4:])]:
        r=c.get('/api/videos/'+vid+'/stream',headers={'Range':value})
        assert r.status_code==206 and r.content==expected
    for value in ['bytes=9-2','bytes=999999-','bytes=0-2,4-6','bytes=-0','garbage']:
        assert c.get('/api/videos/'+vid+'/stream',headers={'Range':value}).status_code==416
    assert c.head('/api/videos/'+vid+'/stream',headers={'Range':'bytes=4-7'}).headers['Content-Length']=='4'

def test_upload_retry_and_integrity_checks():
    c=admin();u,data=make_upload(c)
    assert c.get('/api/admin/uploads/'+u['id']).json()['completed']==[0]
    headers={'X-Part-SHA256':hashlib.sha256(data).hexdigest()}
    r=c.put('/api/admin/uploads/'+u['id']+'/parts/0',content=data,headers=headers)
    assert r.status_code==200 and r.json()['alreadyUploaded']
    assert c.put('/api/admin/uploads/'+u['id']+'/parts/0',content=data,headers={'X-Part-SHA256':'0'*64}).status_code==409
    assert c.post('/api/admin/uploads/'+u['id']+'/finish',json={}).status_code==200
    assert c.post('/api/admin/uploads/'+u['id']+'/finish',json={}).status_code==200
    assert c.get('/api/admin/videos').json()['items'][0]['state']=='private'

def test_private_storage_not_exposed_and_site_closed():
    c=admin();vid,_=video(c);m,user=member();change(c,user,'approve')
    assert client().get('/data/media/'+vid).status_code==404
    assert client().get('/assets/../data/secret.key').status_code==404
    s=c.get('/api/meta').json()
    fields={k:s[k] for k in ['title','contact','retention','autoApprove','revision']}
    assert c.post('/api/admin/settings',json={**fields,'open':False}).status_code==200
    assert m.get('/api/videos/'+vid+'/stream').status_code==403
    assert client().get('/api/catalogue').json()['items']==[]

def test_member_erasure_confirmation_and_audit():
    c=admin();m,user=member();info(c,user['id'])
    assert change(c,user,'erase',confirmation='테스트 회원').status_code==422
    assert change(c,user,'remove',reason='요청',confirmation='테스트 회원').status_code==200
    detail=info(c,user['id'])
    assert change(c,detail,'erase',confirmation='테스트 회원').status_code==200
    assert c.get('/api/admin/members/'+user['id']).status_code==404
    with a.db() as db:
        assert not db.execute('SELECT 1 FROM sessions WHERE member=?',(user['id'],)).fetchone()
        assert db.execute("SELECT 1 FROM audits WHERE action='erase' AND subject=?",(user['id'],)).fetchone()

def test_progress_and_session_cookie():
    c=admin();vid,_=video(c);m,user=member();change(c,user,'approve')
    assert m.post('/api/videos/'+vid+'/progress',json={'seconds':5}).status_code==200
    assert m.get('/api/videos').json()['items'][0]['seconds']==5
    r=m.post('/api/login',json={'identifier':'01000000001','password':'abcdefgh'})
    assert 'httponly' in r.headers['set-cookie'].lower()
    assert 'samesite=lax' in r.headers['set-cookie'].lower()

@pytest.mark.skipif(os.getenv('RUN_LARGE_UPLOAD')!='1',reason='Opt-in 2 GiB synthetic local transfer')
def test_2gib_resumable_transfer():
    c=admin();size=2*1024**3;chunk=a.CHUNK
    r=c.post('/api/admin/uploads',json={'title':'2 GiB 합성 전송 검증','filename':'synthetic.mp4',
      'size':size,'mime':'video/mp4','fingerprint':'b'*64,'rights':True})
    assert r.status_code==200,r.text
    ident=r.json()['id'];block=bytes(chunk);first=b'\x00\x00\x00\x18ftypisom'+bytes(chunk-12)
    zero_hash=hashlib.sha256(block).hexdigest();full=hashlib.sha256();started=time.perf_counter()
    for i in range(size//chunk):
        data=first if i==0 else block
        sha=hashlib.sha256(first).hexdigest() if i==0 else zero_hash
        res=c.put(f'/api/admin/uploads/{ident}/parts/{i}',content=data,headers={'X-Part-SHA256':sha})
        assert res.status_code==200,(i,res.text)
        full.update(data)
        if i==31:
            assert len(c.get('/api/admin/uploads/'+ident).json()['completed'])==32
            new=client();new.cookies.update(c.cookies)
            new.headers.update({'X-CSRF-Token':c.headers['X-CSRF-Token']});c=new
    assert c.post('/api/admin/uploads/'+ident+'/finish',json={}).status_code==200
    path=a.DATA/'media'/ident;assert path.stat().st_size==size
    h=hashlib.sha256()
    with path.open('rb') as f:
        while b:=f.read(1024**2):h.update(b)
    assert h.hexdigest()==full.hexdigest()
    print(f'2 GiB synthetic payload: 256 chunks, resume, assembly and SHA256 PASS; {time.perf_counter()-started:.1f}s')
