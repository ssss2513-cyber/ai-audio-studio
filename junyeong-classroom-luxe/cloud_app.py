"""Cloud entry: durable private data, one-time administrator setup, owner recovery."""
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

class Recover(Setup):
 pass

def token_matches(env_name: str, value: str):
 wanted=os.getenv(env_name,'')
 actual=hashlib.sha256(value.encode()).hexdigest()
 return bool(wanted) and secrets.compare_digest(actual,wanted)

@app.get('/setup')
def setup_page():
 return FileResponse(core.ROOT/'web'/'setup.html',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

@app.post('/api/setup')
def bootstrap(body: Setup,req: Request):
 core.rate(req,'setup',8)
 if not token_matches('LUMIEL_SETUP_HASH',body.token): core.fail(403,'운영자 전용 초기 설정 링크가 필요합니다.')
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

@app.get('/recover')
def recover_page():
 return FileResponse(core.ROOT/'web'/'recover.html',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

@app.post('/api/recover')
def recover_admin(body: Recover, req: Request):
 core.rate(req,'owner-recovery',6)
 if not token_matches('LUMIEL_SETUP_HASH',body.token): core.fail(403,'운영자 전용 복구 링크가 필요합니다.')
 if body.password!=body.confirmation: core.fail(422,'비밀번호가 서로 다릅니다.')
 email=os.getenv('ADMIN_OWNER_EMAIL','ssss2513@gmail.com').strip().lower()
 hp=core.hash_password(body.password)
 with core.db() as c:
  c.execute('BEGIN IMMEDIATE')
  member=c.execute("SELECT * FROM members WHERE role='admin' AND lookup=?",(core.digest(email),)).fetchone()
  if not member: core.fail(404,'운영자 계정이 아직 없습니다. 초기 설정부터 진행해 주세요.')
  c.execute('UPDATE members SET password=?,epoch=epoch+1,revision=revision+1 WHERE id=?',(hp,member['id']))
  c.execute('DELETE FROM sessions WHERE member=?',(member['id'],))
  core.audit(c,'owner-recovery',member['id'],'password_reset')
 return {'ok':True}

@app.get('/api/health')
def health():
 with core.db() as c: c.execute('SELECT 1').fetchone()
 return {'ok':True,'brand':'Maison Lumiel','version':'2.1.0','storage':'private-object-storage' if os.getenv('S3_BUCKET') else 'private-disk'}

if os.getenv('S3_BUCKET'):
 import cloud_storage
 cloud_storage.install(core)
