"""Cloud entry: durable private data, one-time administrator setup, object-storage video."""
import os, hashlib, secrets, time, json
import app as core
from fastapi import Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
app=core.app
app.title='메종 루미엘 · Maison Lumiel'
marker=core.DATA/'lumiel-v2'
if not marker.exists():
 with core.db() as c:
  s=json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
  s['title']='메종 루미엘'; s['contact']='ssss2513@gmail.com'
  s['autoApprove']=False; s['revision']+=1
  c.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(s,ensure_ascii=False),))
 marker.write_text('2')

class Setup(BaseModel):
 token: str=Field(min_length=32,max_length=150)
 password: str=Field(min_length=8,max_length=128)
 confirmation: str=Field(max_length=128)

@app.get('/setup')
def setup_page():
 return FileResponse(core.ROOT/'web'/'setup.html',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

@app.post('/api/setup')
def bootstrap(body: Setup,req: Request):
 core.rate(req,'setup',8)
 wanted=os.getenv('LUMIEL_SETUP_HASH','')
 actual=hashlib.sha256(body.token.encode()).hexdigest()
 if not wanted or not secrets.compare_digest(actual,wanted): core.fail(403,'운영자 전용 초기 설정 링크가 필요합니다.')
 if body.password!=body.confirmation: core.fail(422,'비밀번호가 서로 다릅니다.')
 with core.db() as c:
  if c.execute("SELECT 1 FROM members WHERE role='admin'").fetchone(): core.fail(409,'운영자 등록이 이미 완료되었습니다. 관리자 로그인으로 들어가 주세요.')
 hp=core.hash_password(body.password); ident=core.uid(); now=time.time()
 email=os.getenv('ADMIN_OWNER_EMAIL','ssss2513@gmail.com').strip().lower()
 with core.db() as c:
  c.execute('BEGIN IMMEDIATE')
  if c.execute("SELECT 1 FROM members WHERE role='admin'").fetchone(): core.fail(409,'운영자 등록이 이미 완료되었습니다.')
  c.execute("INSERT INTO members(id,name,phone,lookup,password,role,state,created,consent,consent_text) VALUES(?,?,?,?,?,'admin','approved',?,?,?)",(ident,core.enc('운영자'),core.enc(email),core.digest(email),hp,now,now,'{}'))
  core.audit(c,ident,ident,'initial_setup')
  member=c.execute('SELECT * FROM members WHERE id=?',(ident,)).fetchone()
 return core.session_response(member)

@app.get('/api/health')
def health():
 with core.db() as c: c.execute('SELECT 1').fetchone()
 return {'ok':True,'brand':'Maison Lumiel','version':'2.0.0','storage':'private-object-storage' if os.getenv('S3_BUCKET') else 'private-disk'}

if os.getenv('S3_BUCKET'):
 import cloud_storage
 cloud_storage.install(core)
