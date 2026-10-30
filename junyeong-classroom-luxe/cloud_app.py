"""Cloud entry: durable private data, one-time administrator setup, owner recovery."""
import os, hashlib, secrets, time, json, threading, logging
from drive_streaming import capped_range, known_reason, should_retry, content_range_start
import app as core
from yadam_trends import install as install_yadam_trends
from fastapi import Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app=core.app
from shared_tools import install_shared_tools
install_shared_tools(core)
app.title='메종 루미엘 · Maison Lumiel'
install_yadam_trends(core)
marker=core.DATA/'lumiel-v2'
if not marker.exists():
 with core.db() as c:
  s=json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
  s['title']='메종 루미엘'; s['contact']='ssss2513@gmail.com'
  s['autoApprove']=False; s['revision']+=1
  c.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(s,ensure_ascii=False),))
 marker.write_text('2')


# Owner-requested change: new memberships must be pending until manually approved.
# Applied ONCE to the durable SQLite settings, without resetting existing members,
# their approval states, video records, or any other service preferences.
approval_migration = core.DATA/'manual-approval-20261017.v1'
if not approval_migration.exists():
 with core.db() as c:
  c.execute('BEGIN IMMEDIATE')
  row=c.execute('SELECT value FROM settings WHERE id=1').fetchone()
  current=json.loads(row['value'])
  if current.get('autoApprove',False):
   current['autoApprove']=False
   current['revision']=int(current.get('revision',0))+1
   c.execute('UPDATE settings SET value=? WHERE id=1',
             (json.dumps(current,ensure_ascii=False),))
  core.audit(c,'operator-request','settings','disable_auto_approval')
 approval_migration.write_text('manual-approval-enabled',encoding='utf-8')

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
 return {'ok':True,'brand':'Maison Lumiel','version':'2.3.0','lectureAssistant':True,'database':('postgresql' if core.DATABASE_URL else 'ephemeral-sqlite'),'storage':'railway-bucket' if os.getenv('VIDEO_S3_BUCKET') else 'google-drive' if os.getenv('GOOGLE_DRIVE_FOLDER_ID') else ('private-object-storage' if os.getenv('S3_BUCKET') else 'private-disk')}

if os.getenv('S3_BUCKET'):
 import cloud_storage
 cloud_storage.install(core)


# ---- Private Google Drive video library ---------------------------------
DRIVE_FOLDER_ID=os.getenv('GOOGLE_DRIVE_FOLDER_ID','').strip()
_drive_credentials=None
_drive_lock=threading.RLock()
_drive_log=logging.getLogger("maison.video.storage")
_DRIVE_QUOTA_HOLD = {}
_DRIVE_QUOTA_MUTEX = threading.Lock()
_DRIVE_QUOTA_HOLD_SECONDS = 15 * 60

def _mark_drive_quota(file_id):
 # Small in-process cooldown to avoid storming the Drive API on 403 quota errors.
 with _DRIVE_QUOTA_MUTEX:
  now=time.time()
  for stale,until in list(_DRIVE_QUOTA_HOLD.items()):
   if until<=now:_DRIVE_QUOTA_HOLD.pop(stale,None)
  _DRIVE_QUOTA_HOLD[file_id]=now+_DRIVE_QUOTA_HOLD_SECONDS
  if len(_DRIVE_QUOTA_HOLD)>500:
   earliest=min(_DRIVE_QUOTA_HOLD,key=_DRIVE_QUOTA_HOLD.get)
   _DRIVE_QUOTA_HOLD.pop(earliest,None)

def _drive_check_access(v):
 file_id=v['external_id']
 if not file_id:return
 with _DRIVE_QUOTA_MUTEX:
  until=_DRIVE_QUOTA_HOLD.get(file_id,0)
 if until>time.time():
  core.fail(503,'Google Drive 영상 다운로드 한도 초과로 재생이 일시 중단되었습니다. 원본 파일 보관에는 문제가 없으며, 운영자가 전용 영상 저장소 연결을 확인하고 있습니다.')



def _drive_creds():
 global _drive_credentials
 raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON','').strip()
 if not raw: core.fail(503,'Google Drive 서비스 계정 연결이 아직 완료되지 않았습니다.')
 try:
  from google.oauth2 import service_account
  from google.auth.transport.requests import Request as GoogleAuthRequest
  # One cached credentials object must never be concurrently refreshed. A token can
  # otherwise momentarily be None even while other worker threads are serving video.
  with _drive_lock:
   if _drive_credentials is None:
    _drive_credentials=service_account.Credentials.from_service_account_info(
     json.loads(raw),scopes=['https://www.googleapis.com/auth/drive.readonly'])
   if not _drive_credentials.valid or not _drive_credentials.token:
    _drive_credentials.refresh(GoogleAuthRequest())
   if not _drive_credentials.token:
    raise RuntimeError('Drive access token not available after refresh')
   return _drive_credentials
 except Exception as exc:
  _drive_log.warning('Drive service token unavailable: %s',type(exc).__name__)
  core.fail(503,'Google Drive 인증 연결이 일시적으로 지연되고 있습니다. 잠시 후 다시 시도해 주세요.')

def _drive_auth_headers():
 creds=_drive_creds()
 token=creds.token
 if not isinstance(token,str) or not token:
  core.fail(503,'Google Drive 인증 연결을 다시 확인해 주세요.')
 return {'Authorization':'Bearer '+token}

@app.get('/api/admin/drive/status')
def drive_status(req: Request):
 core.require(req,admin=True)
 return {'connected':bool(os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON','').strip() and DRIVE_FOLDER_ID),'folderId':DRIVE_FOLDER_ID}

@app.post('/api/admin/drive/sync')
def drive_sync(req: Request):
 a=core.require(req,admin=True,write=True)
 if not DRIVE_FOLDER_ID: core.fail(503,'Google Drive 폴더 ID가 설정되지 않았습니다.')
 import requests
 params={
  'q':f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
  'fields':'nextPageToken,files(id,name,mimeType,size,modifiedTime,videoMediaMetadata)',
  'pageSize':'1000',
  'orderBy':'name', 'supportsAllDrives':'true', 'includeItemsFromAllDrives':'true'
 }
 files=[]
 while True:
  r=requests.get('https://www.googleapis.com/drive/v3/files',headers=_drive_auth_headers(),params=params,timeout=30)
  if r.status_code!=200: core.fail(502,'Google Drive 영상 목록을 읽지 못했습니다. 폴더 공유와 Drive API 설정을 확인해 주세요.')
  page=r.json();files.extend(page.get('files',[]))
  token=page.get('nextPageToken')
  if not token:break
  params['pageToken']=token
 added=0; updated=0
 with core.db() as c:
  for f in files:
   mime=f.get('mimeType','')
   if mime not in ('video/mp4','video/webm'): continue
   drive_id=f['id']; name=f.get('name') or 'Drive 영상'
   # Previously migrated originals remain in Drive as backups, not new lessons.
   migrated=c.execute("SELECT 1 FROM railway_backup_sources WHERE old_source='drive' AND old_key=?",(drive_id,)).fetchone()
   if migrated:continue
   size=int(f.get('size') or 0)
   duration_ms=((f.get('videoMediaMetadata') or {}).get('durationMillis') or '0')
   try: duration=float(duration_ms)/1000
   except Exception: duration=0
   old=c.execute("SELECT * FROM videos WHERE source='drive' AND external_id=?",(drive_id,)).fetchone()
   if old:
    c.execute('UPDATE videos SET title=?,size=?,mime=?,duration=?,revision=revision+1 WHERE id=?',(name,size,mime,duration,old['id']))
    updated+=1
   else:
    ident=core.uid()
    c.execute("""INSERT INTO videos(id,title,description,size,mime,duration,theme,state,position,revision,poster,created,source,external_id)
      VALUES(?,?,?,?,?,?,'rose','private',0,1,0,?,'drive',?)""",(ident,name,'Google Drive에서 연결된 영상',size,mime,duration,time.time(),drive_id))
    core.audit(c,a['id'],ident,'drive_video_add')
    added+=1
 return {'ok':True,'added':added,'updated':updated,'found':len(files)}

def _drive_stream(req: Request,v):
 """Serve bounded, verifiable Drive byte ranges to authorized video viewers.

 A 2 GiB browser request such as Range: bytes=0- is shortened to 4 MiB. This
 avoids leaving large Drive streams open and lets players safely resume. We do
 not make video objects public or put OAuth tokens in the browser.
 """
 import requests
 file_id=v['external_id']
 if not file_id: core.fail(404,'Google Drive 파일 ID가 없습니다.')
 _drive_check_access(v)
 size=int(v['size'] or 0)
 raw_range=req.headers.get('range','')
 try: window=capped_range(raw_range,size)
 except ValueError:
  return core.Response(status_code=416,headers={'Content-Range':f'bytes */{size}','Accept-Ranges':'bytes'})

 if req.method=='HEAD':
  # Video element metadata probes should not start a multi-gigabyte Drive download.
  headers={'Accept-Ranges':'bytes','Cache-Control':'private, no-store','Content-Disposition':'inline',
           'Content-Length':str((window[1]-window[0]+1) if window else size)}
  if window: headers['Content-Range']=f'bytes {window[0]}-{window[1]}/{size}'
  return core.Response(status_code=206 if window else 200,media_type=v['mime'],headers=headers)

 headers=_drive_auth_headers()
 if window:headers['Range']=f'bytes={window[0]}-{window[1]}'
 upstream=None
 for attempt in range(3):
  try:
   upstream=requests.get(f'https://www.googleapis.com/drive/v3/files/{file_id}',
     params={'alt':'media'},headers=headers,stream=True,timeout=(9,45))
  except requests.RequestException as exc:
   _drive_log.warning('Drive video network failure attempt=%d type=%s',
                      attempt+1,type(exc).__name__)
   if attempt<2:
    time.sleep(0.5*2**attempt)
    continue
   core.fail(502,'영상 저장소 연결이 잠시 지연되고 있습니다. 잠시 후 다시 시도해 주세요.')
  if upstream.status_code in (200,206):
   if window and upstream.status_code==206:
    got=content_range_start(upstream.headers.get('Content-Range',''))
    if got!=window[0]:
     _drive_log.warning('Drive video range mismatch requested_start=%s received_start=%s',
                        window[0],got)
     upstream.close()
     core.fail(502,'영상 범위 정보를 다시 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.')
   if window and upstream.status_code==200 and window[0]>0:
    # Some proxies ignore Range, in which case a nonzero seek would show wrong bytes.
    _drive_log.warning('Drive ignored nonzero video range, seek_start=%s',window[0])
    upstream.close()
    core.fail(502,'영상의 탐색 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.')
   break
  try:
   reason=known_reason(upstream.json())
  except (ValueError,requests.RequestException):
   reason='unknown'
  status=upstream.status_code
  upstream.close()
  _drive_log.warning('Drive video upstream rejected status=%s reason=%s attempt=%s ranged=%s',
                     status,reason,attempt+1,bool(window))
  if status==403 and reason in ('downloadQuotaExceeded','dailyLimitExceeded','quotaExceeded'):
   _mark_drive_quota(file_id)
   core.fail(503,'Google Drive 영상 다운로드 한도 초과로 재생이 일시 중단되었습니다. 운영자가 전용 영상 저장소 연결을 확인하고 있습니다.')
  if status in (401,403,404) and not should_retry(status,reason):
   core.fail(502,'Google Drive 파일 접근 권한 또는 다운로드 허용 여부를 확인해 주세요.')
  if attempt<2 and should_retry(status,reason):
   time.sleep(0.5*2**attempt)
   continue
  core.fail(502,'Google Drive 영상 요청이 거절되었습니다. 잠시 후 다시 시도해 주세요.')

 out_headers={'Accept-Ranges':'bytes','Cache-Control':'private, no-store',
              'Content-Disposition':'inline','X-Accel-Buffering':'no'}
 for k in ('Content-Length','Content-Range'):
  if upstream.headers.get(k):out_headers[k]=upstream.headers[k]
 # If a proxy ignores Range: bytes=0-4095, HTTP 200 with the entire
 # representation is valid. Forward its actual full-length headers unchanged.
 def chunks():
  try:
   try:
    for block in upstream.iter_content(128*1024):
     if not block:continue
     try:core.watch(req,v['id'])
     except core.HTTPException:return
     yield block
   except requests.RequestException as exc:
    _drive_log.warning('Drive video stream interrupted: %s',type(exc).__name__)
  finally:
   upstream.close()
 return core.StreamingResponse(chunks(),status_code=upstream.status_code,
                               media_type=v['mime'],headers=out_headers)

core.EXTERNAL_STREAMER=_drive_stream
# Cloudflare R2 support is optional and does nothing until its four
# environment values are configured by the operator. Existing Drive videos,
# encryption keys, SQLite data and sessions are preserved unchanged.
from r2_storage import install as install_r2_storage
install_r2_storage(core, _drive_stream)

core.EXTERNAL_ACCESS_CHECKER=_drive_check_access

from railway_s3 import origin as railway_storage_origin
from railway_storage import install as install_railway_storage
from video_migration import install as install_video_migration
core.OBJECT_STORAGE_ORIGIN=railway_storage_origin()
install_railway_storage(core, core.EXTERNAL_STREAMER)
install_video_migration(core, _drive_auth_headers)



# Public homepage soundtrack. Route must be outside the /assets StaticFiles mount.
# Browsers control actual playback through an explicit user action.
@app.api_route('/api/public/background-music.mp3',methods=['GET','HEAD'])
def background_music(req: Request):
 return _public_brand_media(req,'1Mk-XP_VHX4BW0R0p7vFoECr_dxsdxU_H','audio/mpeg',3600)


# Owner-supplied 27.7-second welcome film. Public only; private lessons remain access-controlled.
def _public_brand_media(req: Request, file_id: str, mime: str, maxage: int):
 migrated=core.PUBLIC_OBJECT_MEDIA(req,file_id)
 if migrated is not None:return migrated
 import requests
 headers=_drive_auth_headers()
 range_value=req.headers.get('range','')
 if range_value:
  if len(range_value)>120 or not range_value.startswith('bytes='): core.fail(416,'잘못된 범위 요청입니다.')
  headers['Range']=range_value
 upstream=requests.get('https://www.googleapis.com/drive/v3/files/'+file_id,
     params={'alt':'media'},headers=headers,stream=True,timeout=60)
 if upstream.status_code not in (200,206):
  upstream.close()
  core.fail(502,'메종 루미엘 소개 영상을 불러오지 못했습니다.')
 out={'Accept-Ranges':'bytes','Cache-Control':f'public, max-age={maxage}',
      'X-Content-Type-Options':'nosniff','Content-Disposition':'inline'}
 for k in ('Content-Length','Content-Range','ETag'):
  if upstream.headers.get(k):out[k]=upstream.headers[k]
 if req.method=='HEAD':
  upstream.close()
  return core.Response(status_code=upstream.status_code,media_type=mime,headers=out)
 def stream_media():
  try:
   for block in upstream.iter_content(128*1024):
    if block:yield block
  finally:upstream.close()
 return core.StreamingResponse(stream_media(),status_code=upstream.status_code,media_type=mime,headers=out)

@app.api_route('/api/public/brand-intro.mp4',methods=['GET','HEAD'])
def brand_intro(req: Request):
 return FileResponse(core.ROOT/'public_media'/'brand-intro.mp4',media_type='video/mp4',headers={'Cache-Control':'public, max-age=3600'})

@app.api_route('/api/public/brand-poster.jpg',methods=['GET','HEAD'])
def brand_intro_poster(req: Request):
 return FileResponse(core.ROOT/'public_media'/'brand-poster.jpg',media_type='image/jpeg',headers={'Cache-Control':'public, max-age=86400'})



# Drive-backed cumulative lecture assistant, using the existing member sessions.
from lecture_ai.routes import install as install_lecture_ai
install_lecture_ai(core, _drive_auth_headers)

