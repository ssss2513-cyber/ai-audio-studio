"""Junyeong Class · Rose Atelier 1.0. Single-process, private-disk video library.
No external identity provider, public object URLs or client-side authorization.
Run: python manage.py create-admin; python -m uvicorn app:app --host 127.0.0.1
"""
from __future__ import annotations
import csv, hashlib, hmac, io, json, math, os, re, secrets, shutil, sqlite3
import threading, time, uuid, weakref
from contextlib import contextmanager, asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv('CLASSROOM_DATA_DIR', str(ROOT / 'data'))).resolve()
ORIGIN = os.getenv('PUBLIC_ORIGIN', 'http://127.0.0.1:8000').rstrip('/')
SECURE = ORIGIN.startswith('https://')
COOKIE = '__Host-jc_session' if SECURE else 'jc_local_session'
CHUNK = 8 * 1024 * 1024
MAX_SIZE = 5 * 1024**3
for folder in [DATA, DATA/'media', DATA/'parts', DATA/'posters']:
    folder.mkdir(parents=True, exist_ok=True)
    try: folder.chmod(0o700)
    except OSError: pass
key_file = DATA/'secret.key'
if not key_file.exists() and not os.getenv('CLASSROOM_DATA_KEY'):
    fd = os.open(key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as f: f.write(Fernet.generate_key())
KEY = os.getenv('CLASSROOM_DATA_KEY', '').encode() or key_file.read_bytes().strip()
CIPHER = Fernet(KEY)
PH = PasswordHasher()
HASH_SLOTS = threading.BoundedSemaphore(2)
DUMMY = PH.hash(secrets.token_urlsafe(24))
LOCKS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
LOCK_GUARD = threading.Lock()
EXTERNAL_STREAMER = None
# Manual member approval must never be bypassed by the retired TEMP_OPEN_ACCESS flag.
# Phone/SMS verification is an independent, opt-in feature; ordinary members
# can instead supply a phone number without proving ownership.
REQUIRE_PHONE_VERIFICATION = os.getenv('REQUIRE_PHONE_VERIFICATION','false').strip().lower() in ('1','true','yes','on')

@contextmanager
def db():
    c = sqlite3.connect(DATA/'classroom.sqlite3', timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        yield c
        c.commit()
    except BaseException:
        c.rollback(); raise
    finally: c.close()

DEFAULT = dict(title='준영 클래스', contact='ssss2513@gmail.com', open=True,
               autoApprove=False, retention='회원 탈퇴 또는 개인정보 삭제 요청 처리 시까지', revision=1)
with db() as c:
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL,
      lookup TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
      state TEXT NOT NULL DEFAULT 'pending', revision INTEGER NOT NULL DEFAULT 1, epoch INTEGER NOT NULL DEFAULT 1,
      created REAL NOT NULL, consent REAL NOT NULL, consent_text TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, member TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
      epoch INTEGER NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS audits(id INTEGER PRIMARY KEY, actor TEXT NOT NULL, subject TEXT NOT NULL,
      action TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS rates(key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
      size INTEGER NOT NULL, mime TEXT NOT NULL, duration REAL NOT NULL DEFAULT 0, theme TEXT NOT NULL DEFAULT 'rose',
      state TEXT NOT NULL DEFAULT 'private', position INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
      poster INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
      filename TEXT NOT NULL, fingerprint TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT NOT NULL, duration REAL NOT NULL,
      state TEXT NOT NULL DEFAULT 'uploading', created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS parts(upload TEXT NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
      number INTEGER NOT NULL, size INTEGER NOT NULL, sha TEXT NOT NULL, PRIMARY KEY(upload,number));
    CREATE TABLE IF NOT EXISTS progress(member TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
      video TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE, seconds REAL NOT NULL, updated REAL NOT NULL,
      PRIMARY KEY(member,video));
    CREATE TABLE IF NOT EXISTS playlists(
      name TEXT PRIMARY KEY,
      description TEXT NOT NULL DEFAULT '',
      position INTEGER NOT NULL DEFAULT 0,
      created REAL NOT NULL
    );
        CREATE INDEX IF NOT EXISTS sessions_member ON sessions(member);
    CREATE INDEX IF NOT EXISTS audit_subject ON audits(subject);
    ''')
    c.execute('INSERT OR IGNORE INTO settings VALUES(1,?)', (json.dumps(DEFAULT,ensure_ascii=False),))
    # Backward-compatible columns for externally stored videos such as private Google Drive files.
    cols={row[1] for row in c.execute("PRAGMA table_info(videos)")}
    if 'source' not in cols: c.execute("ALTER TABLE videos ADD COLUMN source TEXT NOT NULL DEFAULT 'local'")
    if 'external_id' not in cols: c.execute("ALTER TABLE videos ADD COLUMN external_id TEXT NOT NULL DEFAULT ''")
    if 'playlist' not in cols: c.execute("ALTER TABLE videos ADD COLUMN playlist TEXT NOT NULL DEFAULT '전체 클래스'")
    if 'playlist_description' not in cols: c.execute("ALTER TABLE videos ADD COLUMN playlist_description TEXT NOT NULL DEFAULT ''")
    c.execute("INSERT OR IGNORE INTO playlists(name,description,position,created) VALUES('전체 클래스','',0,?)",(time.time(),))
    for row in c.execute("SELECT DISTINCT playlist,playlist_description FROM videos WHERE TRIM(COALESCE(playlist,''))<>''"):
        c.execute("INSERT OR IGNORE INTO playlists(name,description,position,created) VALUES(?,?,0,?)",(row[0],row[1] or '',time.time()))
    mcols={row[1] for row in c.execute("PRAGMA table_info(members)")}
    if 'verified' not in mcols: c.execute("ALTER TABLE members ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
    if 'verified_at' not in mcols: c.execute("ALTER TABLE members ADD COLUMN verified_at REAL")
    if 'verification_id' not in mcols: c.execute("ALTER TABLE members ADD COLUMN verification_id TEXT NOT NULL DEFAULT ''")
    if 'verification_di' not in mcols: c.execute("ALTER TABLE members ADD COLUMN verification_di TEXT NOT NULL DEFAULT ''")
    if 'phone_verified' not in mcols: c.execute("ALTER TABLE members ADD COLUMN phone_verified INTEGER NOT NULL DEFAULT 0")
    if 'phone_verified_at' not in mcols: c.execute("ALTER TABLE members ADD COLUMN phone_verified_at REAL")
    c.execute("""CREATE TABLE IF NOT EXISTS sms_otps(
      member TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
      code_hash TEXT NOT NULL, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
      sends INTEGER NOT NULL DEFAULT 1, last_sent REAL NOT NULL
    )""")
    # A single worker is required. Interrupted assembly can be safely repeated from intact parts.



def fail(code: int, message: str): raise HTTPException(code, message)
def enc(value: str): return CIPHER.encrypt(value.encode()).decode()
def dec(value: str): return CIPHER.decrypt(value.encode()).decode()
def digest(value: str): return hmac.new(KEY, value.encode(), hashlib.sha256).hexdigest()
def token_hash(value: str): return hashlib.sha256(value.encode()).hexdigest()
def uid(): return uuid.uuid4().hex

def settings():
    with db() as c: return json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
def audit(c, actor, subject, action, reason=''):
    c.execute('INSERT INTO audits(actor,subject,action,reason,created) VALUES(?,?,?,?,?)',
              (actor,subject,action,reason[:300],time.time()))
def phone(value):
    value = re.sub(r'[\s()-]', '', value)
    if value.startswith('+82'): value = '0'+value[3:]
    if not re.fullmatch(r'01[016789][0-9]{7,8}', value): fail(422,'휴대폰 번호를 확인해 주세요. 예: 01012345678')
    return value

def password_ok(value):
    if not 8 <= len(value) <= 128: fail(422,'비밀번호는 8~128자로 입력해 주세요.')
def hash_password(value):
    password_ok(value)
    if not HASH_SLOTS.acquire(False): fail(429,'요청이 많습니다. 잠시 뒤 다시 시도해 주세요.')
    try: return PH.hash(value)
    finally: HASH_SLOTS.release()
def verify_password(hashed, value):
    if not HASH_SLOTS.acquire(False): fail(429,'요청이 많습니다. 잠시 뒤 다시 시도해 주세요.')
    try:
        try: return PH.verify(hashed, value)
        except (VerificationError, InvalidHashError): return False
    finally: HASH_SLOTS.release()

def rate(req, scope, limit=30):
    now=time.time(); bucket=int(now//900)
    key=digest(f'{scope}:{req.client.host if req.client else "unknown"}:{bucket}')
    with db() as c:
        c.execute('DELETE FROM rates WHERE expires<?',(now,))
        c.execute('INSERT INTO rates VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1',(key,now+900))
        n=c.execute('SELECT count FROM rates WHERE key=?',(key,)).fetchone()[0]
    if n>limit: fail(429,'요청 횟수가 많습니다. 15분 뒤 다시 시도해 주세요.')

def get_session(req, required=True):
    raw=req.cookies.get(COOKIE,'')
    with db() as c:
        row=c.execute('''SELECT m.*,s.csrf,s.expires,s.token FROM sessions s JOIN members m ON s.member=m.id
          WHERE s.token=? AND s.expires>? AND s.epoch=m.epoch''',(token_hash(raw),time.time())).fetchone()
    if not row:
        if required: fail(401,'로그인이 만료되었거나 회원 권한이 변경되었습니다. 다시 로그인해 주세요.')
        return None
    if row['state'] in ('blocked','removed'): fail(403,'이용이 제한되었거나 회원 자격이 해제되었습니다.')
    return row

def require(req, admin=False, watch=False, write=False):
    m=get_session(req)
    if admin and m['role']!='admin': fail(403,'운영자 전용 기능입니다.')
    if watch and m['role']!='admin' and (m['state']!='approved' or not settings()['open']):
        fail(403,'운영자 승인 후 시청할 수 있습니다. 영상관 운영 상태도 확인해 주세요.')
    if write and not secrets.compare_digest(req.headers.get('x-csrf-token',''),m['csrf']):
        fail(403,'화면을 새로고침한 뒤 다시 시도해 주세요.')
    return m

def present(m, full=False):
    p=dec(m['phone']); n=dec(m['name'])
    return dict(id=m['id'],name=n,phone=p if full else (p[:3]+'-****-'+p[-4:] if '@' not in p else '운영자'),
                state=m['state'],role=m['role'],revision=m['revision'],created=m['created'],consent=m['consent'],verified=bool(m['verified']),verifiedAt=m['verified_at'],phoneVerified=bool(m['phone_verified']),phoneVerifiedAt=m['phone_verified_at'])

def session_response(m, remember=False):
    raw=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(24); ttl=(30 if remember else 1)*86400
    with db() as c:
        c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(token_hash(raw),m['id'],m['epoch'],csrf,time.time()+ttl))
        c.execute('DELETE FROM sessions WHERE member=? AND token NOT IN (SELECT token FROM sessions WHERE member=? ORDER BY expires DESC LIMIT 5)',(m['id'],m['id']))
    r=JSONResponse({'member':present(m,True),'csrf':csrf})
    r.set_cookie(COOKIE,raw,httponly=True,secure=SECURE,samesite='lax',max_age=ttl if remember else None,path='/')
    return r

@asynccontextmanager
async def lifespan(app):
    with db() as c: c.execute("UPDATE uploads SET state='uploading' WHERE state='assembling'")
    yield

app=FastAPI(lifespan=lifespan,title='준영 클래스 · Rose Atelier',version='1.0.0',docs_url=None,redoc_url=None,openapi_url=None)
@app.middleware('http')
async def security(req: Request, call_next):
    if req.method in ('POST','PUT','PATCH','DELETE'):
        if req.headers.get('origin','').rstrip('/')!=ORIGIN:
            return JSONResponse({'detail':'허용되지 않은 요청 출처입니다.'},403)
        try: size=int(req.headers.get('content-length','0'))
        except ValueError: return JSONResponse({'detail':'잘못된 요청 길이입니다.'},400)
        if size<0 or size>CHUNK or (not '/parts/' in req.url.path and not req.url.path.endswith('/poster') and size>32768):
            return JSONResponse({'detail':'요청 크기가 너무 큽니다.'},413)
        if '/parts/' not in req.url.path and not req.url.path.endswith('/poster'):
            # Metadata/auth requests must have a bounded, explicit length.
            if 'content-length' not in req.headers: return JSONResponse({'detail':'요청 길이가 필요합니다.'},411)
    response=await call_next(req)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' https://images.unsplash.com https://i.ytimg.com blob: data:; media-src 'self' blob:; frame-src https://www.youtube.com https://www.youtube-nocookie.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if req.url.path.startswith('/api/'): response.headers['Cache-Control']='private, no-store, max-age=0'
    if SECURE: response.headers['Strict-Transport-Security']='max-age=31536000'
    return response

class Join(BaseModel):
    name: str=Field(min_length=1,max_length=60)
    phone: str=Field(max_length=30)
    password: str=Field(min_length=8,max_length=128)
    confirmation: str=Field(max_length=128)
    consent: bool
class Login(BaseModel):
    identifier: str=Field(max_length=200)
    password: str=Field(max_length=128)
    remember: bool=False
    admin: bool=False

@app.get('/api/meta')
def meta():
    s=settings()
    with db() as c: ready=bool(c.execute("SELECT 1 FROM members WHERE role='admin'").fetchone())
    sms_ready=REQUIRE_PHONE_VERIFICATION and all(os.getenv(k,'').strip() for k in ('SOLAPI_API_KEY','SOLAPI_API_SECRET','SOLAPI_SENDER'))
    return {**s,'ready':ready,'maxUploadBytes':MAX_SIZE,'version':'1.1.1','smsReady':sms_ready,'phoneVerificationRequired':sms_ready,'temporaryOpenAccess':False}
@app.post('/api/join')
def join(body: Join, req: Request):
    rate(req,'signup',8)
    s=settings()
    with db() as c:
        if not c.execute("SELECT 1 FROM members WHERE role='admin'").fetchone(): fail(503,'운영자가 초기 설정을 준비하고 있습니다.')
    if not s['open']: fail(403,'현재 신규 가입을 받고 있지 않습니다.')
    if not body.consent: fail(422,'개인정보 수집·이용 동의가 필요합니다.')
    if body.password!=body.confirmation: fail(422,'비밀번호가 서로 다릅니다.')
    n=body.name.strip()
    if not n or any(ord(x)<32 for x in n): fail(422,'이름을 확인해 주세요.')
    p=phone(body.phone); ident=uid(); now=time.time(); hp=hash_password(body.password)
    with db() as c:
        if c.execute('SELECT 1 FROM members WHERE lookup=?',(digest(p),)).fetchone(): fail(409,'해당 번호로 가입할 수 없습니다. 기존 계정 로그인 또는 운영자 문의를 이용해 주세요.')
        c.execute('''INSERT INTO members(id,name,phone,lookup,password,state,created,consent,consent_text)
          VALUES(?,?,?,?,?,?,?,?,?)''',(ident,enc(n),enc(p),digest(p),hp,'approved' if s['autoApprove'] else 'pending',now,now,json.dumps({'version':'1.0','purpose':'회원 관리 및 영상 접근 관리','contact':s['contact'],'retention':s['retention']},ensure_ascii=False)))
        m=c.execute('SELECT * FROM members WHERE id=?',(ident,)).fetchone()
        audit(c,ident,ident,'join')
    return session_response(m)
@app.post('/api/login')
def login(body: Login, req: Request):
    rate(req,'login',40)
    ident=body.identifier.strip().lower() if body.admin else phone(body.identifier)
    rate(req,'account:'+digest(ident),12)
    with db() as c: m=c.execute('SELECT * FROM members WHERE lookup=?',(digest(ident),)).fetchone()
    valid=verify_password(m['password'] if m else DUMMY, body.password)
    if not m or not valid or (body.admin and m['role']!='admin'): fail(401,'로그인 정보가 일치하지 않습니다.')
    if m['state']=='blocked': fail(403,'현재 이용이 제한되어 있습니다. 운영자에게 문의해 주세요.')
    if m['state']=='removed': fail(403,'회원 자격이 해제되었습니다. 운영자에게 문의해 주세요.')
    with db() as c:
        if PH.check_needs_rehash(m['password']): c.execute('UPDATE members SET password=? WHERE id=?',(hash_password(body.password),m['id']))
        audit(c,m['id'],m['id'],'login')
    return session_response(m,body.remember)
@app.get('/api/session')
def session(req: Request):
    m=get_session(req,False)
    return {'member':present(m,True) if m else None,'csrf':m['csrf'] if m else None}
@app.post('/api/logout')
def logout(req: Request):
    m=require(req,write=True)
    with db() as c: c.execute('DELETE FROM sessions WHERE token=?',(m['token'],))
    r=JSONResponse({'ok':True}); r.delete_cookie(COOKIE,path='/',secure=SECURE,httponly=True,samesite='lax'); return r



class SmsCode(BaseModel):
    code: str=Field(min_length=6,max_length=6,pattern='^[0-9]{6}$')

def _sms_ready():
    return all(os.getenv(k,'').strip() for k in ('SOLAPI_API_KEY','SOLAPI_API_SECRET','SOLAPI_SENDER'))

def _otp_hash(member_id: str, code: str):
    return hmac.new(KEY, f'sms:{member_id}:{code}'.encode(), hashlib.sha256).hexdigest()

def _send_solapi_sms(to_phone: str, message: str):
    if not _sms_ready(): fail(503,'문자 인증 서비스가 아직 연결되지 않았습니다.')
    import requests as _requests, datetime as _datetime
    api_key=os.getenv('SOLAPI_API_KEY','').strip()
    api_secret=os.getenv('SOLAPI_API_SECRET','').strip()
    sender=os.getenv('SOLAPI_SENDER','').strip()
    salt=secrets.token_hex(16)
    date=_datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    signature=hmac.new(api_secret.encode(), (date+salt).encode(), hashlib.sha256).hexdigest()
    auth=f'HMAC-SHA256 apiKey={api_key}, date={date}, salt={salt}, signature={signature}'
    payload={'messages':[{'to':to_phone,'from':sender,'text':message,'type':'SMS','autoTypeDetect':False}]}
    r=_requests.post('https://api.solapi.com/messages/v4/send-many/detail',json=payload,headers={'Authorization':auth,'Content-Type':'application/json'},timeout=15)
    if r.status_code not in (200,201):
        print('SOLAPI_SEND status='+str(r.status_code)+' body='+r.text[:500], flush=True)
        try:
            detail=r.json()
            msg=detail.get('errorMessage') or detail.get('message') or 'SOLAPI 발송 오류'
        except Exception:
            msg='SOLAPI 발송 오류'
        fail(502,'인증 문자를 보내지 못했습니다: '+str(msg)[:180])

def _solapi_diag_once():
    if not _sms_ready():
        print('SOLAPI_DIAG ready=false', flush=True)
        return
    try:
        import requests as _requests, datetime as _datetime
        api_key=os.getenv('SOLAPI_API_KEY','').strip()
        api_secret=os.getenv('SOLAPI_API_SECRET','').strip()
        sender=os.getenv('SOLAPI_SENDER','').strip()
        def _auth():
            salt=secrets.token_hex(16)
            date=_datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
            signature=hmac.new(api_secret.encode(), (date+salt).encode(), hashlib.sha256).hexdigest()
            return f'HMAC-SHA256 apiKey={api_key}, date={date}, salt={salt}, signature={signature}'
        rb=_requests.get('https://api.solapi.com/cash/v1/balance',headers={'Authorization':_auth()},timeout=12)
        rs=_requests.get('https://api.solapi.com/senderid/v1/numbers',headers={'Authorization':_auth()},timeout=12)
        sender_status='not-found'
        if rs.status_code==200:
            try:
                for x in rs.json().get('senderIds',[]):
                    if str(x.get('phoneNumber','')).replace('-','')==sender.replace('-',''):
                        sender_status=str(x.get('status','unknown'))
                        break
            except Exception:
                sender_status='parse-error'
        def _err(resp):
            if resp.status_code < 400: return 'ok'
            try:
                j=resp.json()
                return str(j.get('errorCode') or j.get('code') or '')+':'+str(j.get('errorMessage') or j.get('message') or '')[:120]
            except Exception:
                return ('text:'+resp.text[:120]).replace('\n',' ')
        print(f'SOLAPI_DIAG balance_http={rb.status_code} balance_err={_err(rb)} sender_http={rs.status_code} sender_err={_err(rs)} sender_status={sender_status}', flush=True)
        rl=_requests.get('https://api.solapi.com/messages/v4/list?limit=5',headers={'Authorization':_auth()},timeout=12)
        if rl.status_code==200:
            try:
                ml=rl.json().get('messageList',{})
                rows=[]
                for x in ml.values():
                    rows.append({'status':x.get('status'),'statusCode':x.get('statusCode'),'reason':x.get('reason'),'networkName':x.get('networkName'),'dateUpdated':x.get('dateUpdated')})
                print('SOLAPI_RECENT '+json.dumps(rows,ensure_ascii=False), flush=True)
            except Exception as e:
                print('SOLAPI_RECENT parse='+type(e).__name__, flush=True)
        else:
            print('SOLAPI_RECENT http='+str(rl.status_code)+' err='+_err(rl), flush=True)
    except Exception as e:
        print('SOLAPI_DIAG exception='+type(e).__name__, flush=True)

_solapi_diag_once()

@app.get('/api/sms/status')
def sms_status(req: Request):
    m=require(req)
    return {'ready':_sms_ready(),'verified':bool(m['phone_verified'])}

@app.post('/api/sms/send')
def sms_send(req: Request):
    m=require(req,write=True)
    if m['phone_verified']: return {'ok':True,'alreadyVerified':True}
    rate(req,'sms-send',8)
    now=time.time()
    with db() as c:
        old=c.execute('SELECT * FROM sms_otps WHERE member=?',(m['id'],)).fetchone()
        if old and now-old['last_sent']<60: fail(429,'인증 문자는 1분 후 다시 요청할 수 있습니다.')
        if old and old['sends']>=5 and now-old['last_sent']<3600: fail(429,'문자 요청 횟수가 많습니다. 1시간 뒤 다시 시도해 주세요.')
    code=f'{secrets.randbelow(1000000):06d}'
    _send_solapi_sms(dec(m['phone']),f'[메종 루미엘] 인증번호는 {code} 입니다. 5분 안에 입력해 주세요.')
    with db() as c:
        sends=(old['sends']+1) if old and now-old['last_sent']<3600 else 1
        c.execute("""INSERT INTO sms_otps(member,code_hash,expires,attempts,sends,last_sent)
          VALUES(?,?,?,?,?,?) ON CONFLICT(member) DO UPDATE SET
          code_hash=excluded.code_hash,expires=excluded.expires,attempts=0,sends=excluded.sends,last_sent=excluded.last_sent""",
          (m['id'],_otp_hash(m['id'],code),now+300,0,sends,now))
        audit(c,m['id'],m['id'],'sms_code_sent')
    return {'ok':True,'expiresIn':300}

@app.post('/api/sms/verify')
def sms_verify(body: SmsCode, req: Request):
    m=require(req,write=True)
    rate(req,'sms-verify',20)
    now=time.time()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM sms_otps WHERE member=?',(m['id'],)).fetchone()
        if not row: fail(404,'먼저 인증번호를 요청해 주세요.')
        if row['expires']<now:
            c.execute('DELETE FROM sms_otps WHERE member=?',(m['id'],))
            fail(410,'인증번호가 만료되었습니다. 새 인증번호를 요청해 주세요.')
        if row['attempts']>=5: fail(429,'인증번호 입력 횟수를 초과했습니다. 새 인증번호를 요청해 주세요.')
        if not secrets.compare_digest(row['code_hash'],_otp_hash(m['id'],body.code)):
            c.execute('UPDATE sms_otps SET attempts=attempts+1 WHERE member=?',(m['id'],))
            fail(422,'인증번호가 일치하지 않습니다.')
        c.execute('UPDATE members SET phone_verified=1,phone_verified_at=?,revision=revision+1 WHERE id=?',(now,m['id']))
        c.execute('DELETE FROM sms_otps WHERE member=?',(m['id'],))
        audit(c,m['id'],m['id'],'phone_verified')
        fresh=c.execute('SELECT * FROM members WHERE id=?',(m['id'],)).fetchone()
    return {'ok':True,'member':present(fresh,True)}

TRANSITIONS={'approve':({'pending'},'approved'),'revoke':({'approved'},'pending'),
 'block':({'pending','approved'},'blocked'),'unblock':({'blocked'},'pending'),
 'remove':({'pending','approved','blocked'},'removed'),'restore':({'removed'},'pending')}
class Change(BaseModel):
    action: str=Field(max_length=25)
    revision: int
    reason: str=Field(default='',max_length=300)
    confirmation: str=Field(default='',max_length=120)
@app.get('/api/admin/members')
def members(req: Request,q: str='',state: str='',page: int=1):
    require(req,admin=True)
    page=max(1,page); q=q[:60].lower()
    with db() as c: rows=c.execute("SELECT * FROM members WHERE role='member' ORDER BY created DESC").fetchall()
    result=[present(m) for m in rows if (not state or m['state']==state) and (not q or q in dec(m['name']).lower() or q in dec(m['phone']))]
    return {'items':result[(page-1)*30:page*30],'total':len(result),'page':page}
@app.get('/api/admin/members/{ident}')
def member_detail(ident: str,req: Request):
    a=require(req,admin=True)
    with db() as c:
        m=c.execute("SELECT * FROM members WHERE id=? AND role='member'",(ident,)).fetchone()
        if not m: fail(404,'회원을 찾을 수 없습니다.')
        audit(c,a['id'],ident,'view_contact')
    return {**present(m,True),'consentText':json.loads(m['consent_text'])}
@app.post('/api/admin/members/{ident}')
def change_member(ident: str,body: Change,req: Request):
    a=require(req,admin=True,write=True)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        m=c.execute("SELECT * FROM members WHERE id=? AND role='member'",(ident,)).fetchone()
        if not m: fail(404,'회원을 찾을 수 없습니다.')
        if m['revision']!=body.revision: fail(409,'다른 화면에서 변경되었습니다. 새로고침 후 다시 확인해 주세요.')
        if body.action=='erase':
            if m['state']!='removed' or body.confirmation!=dec(m['name']): fail(422,'먼저 멤버 해제 후 이름을 정확히 입력해 주세요.')
            c.execute('DELETE FROM members WHERE id=?',(ident,))
            c.execute("UPDATE audits SET reason='' WHERE subject=?",(ident,))
            audit(c,a['id'],ident,'erase')
            return {'ok':True}
        if body.action not in TRANSITIONS: fail(422,'지원하지 않는 동작입니다.')
        if body.action=='approve' and REQUIRE_PHONE_VERIFICATION and all(os.getenv(k,'').strip() for k in ('SOLAPI_API_KEY','SOLAPI_API_SECRET','SOLAPI_SENDER')) and not m['phone_verified']:
            fail(409,'휴대폰 번호 인증을 완료한 회원만 시청 승인할 수 있습니다.')
        allowed,target=TRANSITIONS[body.action]
        if m['state'] not in allowed: fail(409,'현재 회원 상태에서는 처리할 수 없습니다.')
        if body.action in ('block','remove') and not body.reason.strip(): fail(422,'처리 사유를 입력해 주세요.')
        if body.action=='remove' and body.confirmation!=dec(m['name']): fail(422,'해제할 회원 이름을 정확히 입력해 주세요.')
        revoke=target!='approved'
        c.execute('UPDATE members SET state=?,revision=revision+1,epoch=epoch+? WHERE id=?',(target,int(revoke),ident))
        if revoke: c.execute('DELETE FROM sessions WHERE member=?',(ident,))
        audit(c,a['id'],ident,body.action,body.reason)
        out=present(c.execute('SELECT * FROM members WHERE id=?',(ident,)).fetchone())
    return {'ok':True,'member':out}
def _solapi_recent_admin(limit: int=8):
    if not _sms_ready(): return []
    try:
        import requests as _requests, datetime as _datetime
        api_key=os.getenv('SOLAPI_API_KEY','').strip()
        api_secret=os.getenv('SOLAPI_API_SECRET','').strip()
        salt=secrets.token_hex(16)
        dt=_datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
        sig=hmac.new(api_secret.encode(),(dt+salt).encode(),hashlib.sha256).hexdigest()
        auth=f'HMAC-SHA256 apiKey={api_key}, date={dt}, salt={salt}, signature={sig}'
        r=_requests.get('https://api.solapi.com/messages/v4/list?limit='+str(max(1,min(20,limit))),headers={'Authorization':auth},timeout=12)
        if r.status_code!=200: return [{'status':'조회 실패','statusCode':str(r.status_code),'reason':'SOLAPI 발송 결과를 불러오지 못했습니다.','to':'','dateUpdated':''}]
        j=r.json()
        raw=j.get('messageList',{})
        vals=list(raw.values()) if isinstance(raw,dict) else (raw if isinstance(raw,list) else [])
        out=[]
        for x in vals[:limit]:
            to=str(x.get('to') or '')
            masked=(to[:3]+'****'+to[-4:]) if len(to)>=7 else ''
            code=str(x.get('statusCode') or '')
            status=str(x.get('status') or '')
            reason=str(x.get('reason') or x.get('statusMessage') or '')
            if code=='4000': label='수신 완료'
            elif code.startswith('3'): label='발송 실패'
            elif status.lower() in ('sent','completed','success'): label='발송 처리'
            else: label=status or '처리 중'
            out.append({'status':label,'statusCode':code,'reason':reason,'to':masked,'networkName':str(x.get('networkName') or ''),'dateUpdated':str(x.get('dateUpdated') or '')})
        return out
    except Exception:
        return [{'status':'조회 실패','statusCode':'','reason':'발송 결과 조회 중 오류가 발생했습니다.','to':'','dateUpdated':''}]

@app.get('/api/admin/overview')
def overview(req: Request):
    require(req,admin=True)
    with db() as c:
        counts={m['state']:m['n'] for m in c.execute("SELECT state,COUNT(*) n FROM members WHERE role='member' GROUP BY state")}
        videos=c.execute("SELECT COUNT(*) FROM videos WHERE state!='trash'").fetchone()[0]
        recent=[dict(x) for x in c.execute('SELECT action,created FROM audits ORDER BY id DESC LIMIT 8')]
    return {'counts':counts,'videos':videos,'recent':recent,'smsDeliveries':_solapi_recent_admin(8)}
@app.get('/api/admin/audits')
def audits(req: Request,page: int=1):
    require(req,admin=True)
    with db() as c:
        rows=[dict(x) for x in c.execute('SELECT * FROM audits ORDER BY id DESC LIMIT 50 OFFSET ?',((max(1,page)-1)*50,))]
    return {'items':rows}
@app.post('/api/admin/export')
def export(req: Request):
    a=require(req,admin=True,write=True); out=io.StringIO(); w=csv.writer(out)
    w.writerow(['이름','휴대폰 번호','상태','가입 시각(UTC)','본인인증'])
    with db() as c:
        for m in c.execute("SELECT * FROM members WHERE role='member'"):
            values=[dec(m['name']),dec(m['phone']),m['state'],time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime(m['created'])),'안 함']
            w.writerow(["'"+v if v.startswith(('=','+','-','@','\t','\r','\n')) else v for v in values])
        audit(c,a['id'],'members','export_csv')
    return Response('\ufeff'+out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="members.csv"'})
class SettingsChange(BaseModel):
    title: str=Field(min_length=1,max_length=50)
    contact: str=Field(min_length=3,max_length=200)
    retention: str=Field(min_length=5,max_length=300)
    autoApprove: bool
    open: bool
    revision: int
@app.post('/api/admin/settings')
def update_settings(body: SettingsChange,req: Request):
    a=require(req,admin=True,write=True)
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',body.contact): fail(422,'문의 이메일을 확인해 주세요.')
    with db() as c:
        c.execute('BEGIN IMMEDIATE'); s=json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
        if s['revision']!=body.revision: fail(409,'설정이 변경되었습니다. 화면을 새로고침해 주세요.')
        value=body.model_dump(); value['revision']+=1
        c.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(value,ensure_ascii=False),))
        audit(c,a['id'],'settings','update_settings')
    return {'ok':True,**value}

class UploadStart(BaseModel):
    title: str=Field(min_length=1,max_length=120)
    description: str=Field(default='',max_length=1000)
    filename: str=Field(min_length=1,max_length=200)
    size: int=Field(ge=32,le=MAX_SIZE)
    mime: str
    fingerprint: str=Field(pattern=r'^[0-9a-f]{64}$')
    duration: float=Field(default=0,ge=0,le=86400,allow_inf_nan=False)
    rights: bool

def upload_lock(ident):
    with LOCK_GUARD:
        lock=LOCKS.get(ident)
        if lock is None: lock=threading.Lock(); LOCKS[ident]=lock
        return lock

def get_upload(ident,owner):
    with db() as c: row=c.execute('SELECT * FROM uploads WHERE id=? AND owner=?',(ident,owner)).fetchone()
    if not row: fail(404,'업로드 작업을 찾을 수 없습니다.')
    return row
@app.post('/api/admin/uploads')
def start_upload(body: UploadStart,req: Request):
    a=require(req,admin=True,write=True)
    if not body.rights: fail(422,'영상 제공 권한 확인이 필요합니다.')
    if body.mime not in ('video/mp4','video/webm'): fail(422,'MP4 또는 WebM을 선택해 주세요.')
    with db() as c:
        active=c.execute("SELECT COUNT(*) FROM uploads WHERE state IN ('uploading','assembling')").fetchone()[0]
    if active>=20: fail(409,'미완료 업로드가 많습니다. 먼저 취소하거나 완료해 주세요.')
    if shutil.disk_usage(DATA).free<body.size*2+128*1024**2: fail(507,'저장 공간이 부족합니다. 영상 용량의 두 배 이상이 필요합니다.')
    ident=uid()
    with db() as c:
        c.execute('INSERT INTO uploads(id,owner,title,description,filename,fingerprint,size,mime,duration,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (ident,a['id'],body.title,body.description,body.filename,body.fingerprint,body.size,body.mime,body.duration,time.time()))
        audit(c,a['id'],ident,'upload_start')
    (DATA/'parts'/ident).mkdir()
    return {'id':ident,'chunkSize':CHUNK,'completed':[],'fingerprint':body.fingerprint,'size':body.size}
@app.get('/api/admin/uploads')
def active_uploads(req: Request):
    a=require(req,admin=True)
    with db() as c: items=[dict(x) for x in c.execute("SELECT id,title,filename,size,fingerprint,state,created FROM uploads WHERE owner=? AND state!='complete' ORDER BY created DESC",(a['id'],))]
    return {'items':items}
@app.get('/api/admin/uploads/{ident}')
def upload_status(ident: str,req: Request):
    a=require(req,admin=True); u=get_upload(ident,a['id'])
    with db() as c: completed=[x[0] for x in c.execute('SELECT number FROM parts WHERE upload=? ORDER BY number',(ident,))]
    return {**dict(u),'chunkSize':CHUNK,'completed':completed}
@app.put('/api/admin/uploads/{ident}/parts/{number}')
async def put_part(ident: str,number: int,req: Request):
    a=require(req,admin=True,write=True); u=get_upload(ident,a['id']); lock=upload_lock(ident)
    if not lock.acquire(False): fail(409,'같은 업로드가 처리 중입니다. 잠시 뒤 재시도해 주세요.')
    tmp=None
    try:
        u=get_upload(ident,a['id'])
        if u['state']!='uploading': fail(409,'업로드할 수 없는 작업 상태입니다.')
        if not 0<=number<math.ceil(u['size']/CHUNK): fail(422,'잘못된 조각 번호입니다.')
        expected=min(CHUNK,u['size']-number*CHUNK)
        if req.headers.get('content-length')!=str(expected): fail(422,'전송 조각 크기가 다릅니다.')
        expected_hash=req.headers.get('x-part-sha256','')
        if not re.fullmatch('[0-9a-f]{64}',expected_hash): fail(422,'조각 검증값이 필요합니다.')
        with db() as c: old=c.execute('SELECT sha FROM parts WHERE upload=? AND number=?',(ident,number)).fetchone()
        if old:
            if old[0]!=expected_hash: fail(409,'기존 조각과 다른 파일입니다. 같은 원본 파일을 선택해 주세요.')
            return {'ok':True,'alreadyUploaded':True}
        folder=DATA/'parts'/ident; folder.mkdir(exist_ok=True)
        tmp=folder/(str(number)+'.'+uid()+'.tmp'); total=0; h=hashlib.sha256()
        with tmp.open('wb') as f:
            async for chunk in req.stream():
                total+=len(chunk)
                if total>expected: fail(413,'조각 용량이 초과되었습니다.')
                h.update(chunk); f.write(chunk)
            f.flush(); os.fsync(f.fileno())
        if total!=expected or h.hexdigest()!=expected_hash: fail(422,'전송 검증에 실패했습니다. 해당 조각을 재시도해 주세요.')
        os.replace(tmp,folder/str(number)); tmp=None
        with db() as c: c.execute('INSERT INTO parts VALUES(?,?,?,?)',(ident,number,total,expected_hash))
        return {'ok':True}
    finally:
        if tmp: tmp.unlink(missing_ok=True)
        lock.release()
@app.post('/api/admin/uploads/{ident}/finish')
def finish_upload(ident: str,req: Request):
    a=require(req,admin=True,write=True); u=get_upload(ident,a['id']); lock=upload_lock(ident)
    if not lock.acquire(False): fail(409,'업로드가 처리 중입니다. 잠시 뒤 재시도해 주세요.')
    temp=DATA/'media'/(ident+'.assembling')
    try:
        u=get_upload(ident,a['id'])
        if u['state']=='complete': return {'ok':True,'id':ident}
        with db() as c:
            parts=c.execute('SELECT * FROM parts WHERE upload=? ORDER BY number',(ident,)).fetchall()
            if [p['number'] for p in parts]!=list(range(math.ceil(u['size']/CHUNK))): fail(409,'아직 전송하지 않은 조각이 있습니다.')
            if sum(p['size'] for p in parts)!=u['size']: fail(409,'업로드 크기를 다시 확인해 주세요.')
            c.execute("UPDATE uploads SET state='assembling' WHERE id=?",(ident,))
        with (DATA/'parts'/ident/'0').open('rb') as first: header=first.read(16)
        if (u['mime']=='video/mp4' and header[4:8]!=b'ftyp') or (u['mime']=='video/webm' and not header.startswith(b'\x1a\x45\xdf\xa3')):
            fail(422,'선택한 파일이 지원하는 영상 컨테이너가 아닙니다.')
        if shutil.disk_usage(DATA).free<u['size']+64*1024**2: fail(507,'마무리에 필요한 저장 공간이 부족합니다.')
        with temp.open('wb') as out:
            for p in parts:
                h=hashlib.sha256()
                with (DATA/'parts'/ident/str(p['number'])).open('rb') as f:
                    while chunk:=f.read(1024*1024): h.update(chunk); out.write(chunk)
                if h.hexdigest()!=p['sha']: fail(409,'저장된 조각 검증에 실패했습니다. 업로드를 취소 후 다시 시작해 주세요.')
            out.flush(); os.fsync(out.fileno())
        os.replace(temp,DATA/'media'/ident)
        with db() as c:
            c.execute('INSERT OR IGNORE INTO videos(id,title,description,size,mime,duration,created) VALUES(?,?,?,?,?,?,?)',
                      (ident,u['title'],u['description'],u['size'],u['mime'],u['duration'],time.time()))
            c.execute("UPDATE uploads SET state='complete' WHERE id=?",(ident,)); audit(c,a['id'],ident,'upload_finish')
        shutil.rmtree(DATA/'parts'/ident,ignore_errors=True)
        return {'ok':True,'id':ident}
    except BaseException:
        temp.unlink(missing_ok=True)
        with db() as c: c.execute("UPDATE uploads SET state='uploading' WHERE id=? AND state!='complete'",(ident,))
        raise
    finally: lock.release()
@app.delete('/api/admin/uploads/{ident}')
def cancel_upload(ident: str,req: Request):
    a=require(req,admin=True,write=True); u=get_upload(ident,a['id']); lock=upload_lock(ident)
    if not lock.acquire(False): fail(409,'전송 처리가 끝난 후 다시 취소해 주세요.')
    try:
        if u['state']=='complete': fail(409,'완료된 영상은 영상 관리에서 삭제해 주세요.')
        with db() as c: c.execute('DELETE FROM uploads WHERE id=?',(ident,)); audit(c,a['id'],ident,'upload_cancel')
        shutil.rmtree(DATA/'parts'/ident,ignore_errors=True)
        (DATA/'media'/(ident+'.assembling')).unlink(missing_ok=True)
        return {'ok':True}
    finally: lock.release()

def video_row(ident):
    with db() as c: v=c.execute('SELECT * FROM videos WHERE id=?',(ident,)).fetchone()
    if not v: fail(404,'영상을 찾을 수 없습니다.')
    return v

def watch(req,ident):
    m=require(req,watch=True); v=video_row(ident)
    if v['state'] in ('trash','purging') or (m['role']!='admin' and v['state']!='public'): fail(403,'현재 시청할 수 없는 영상입니다.')
    return m,v
@app.get('/api/catalogue')
def catalogue():
    if not settings()['open']: return {'items':[]}
    with db() as c: return {'items':[dict(v) for v in c.execute("SELECT v.id,v.title,v.description,v.duration,v.theme,v.poster,v.created,v.playlist,v.playlist_description FROM videos v LEFT JOIN playlists pl ON pl.name=v.playlist WHERE v.state='public' ORDER BY COALESCE(pl.position,0),v.position,v.created DESC")]}
@app.get('/api/videos')
def videos(req: Request):
    m=require(req,watch=True)
    with db() as c:
        rows=c.execute("SELECT v.*,COALESCE(pg.seconds,0) seconds FROM videos v LEFT JOIN progress pg ON pg.video=v.id AND pg.member=? LEFT JOIN playlists pl ON pl.name=v.playlist WHERE v.state='public' ORDER BY COALESCE(pl.position,0),v.position,v.created DESC",(m['id'],)).fetchall()
    return {'items':[dict(x) for x in rows]}
@app.get('/api/admin/playlists')
def admin_playlists(req: Request):
    require(req,admin=True)
    with db() as c:
        rows=c.execute("""SELECT p.name,p.description,p.position,p.created,
          COUNT(v.id) video_count
          FROM playlists p LEFT JOIN videos v ON v.playlist=p.name AND v.state!='purging'
          GROUP BY p.name,p.description,p.position,p.created
          ORDER BY p.position,p.created,p.name""").fetchall()
    return {'items':[dict(x) for x in rows]}

class PlaylistChange(BaseModel):
    action: str
    name: str=Field(default='',max_length=80)
    new_name: str=Field(default='',max_length=80)
    description: str=Field(default='',max_length=300)
    position: int=Field(default=0,ge=-100000,le=100000)

@app.post('/api/admin/playlists')
def change_playlist(body: PlaylistChange,req: Request):
    a=require(req,admin=True,write=True)
    name=body.name.strip()
    new_name=body.new_name.strip()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if body.action=='create':
            target=(name or new_name)
            if not target: fail(422,'재생목록 이름을 입력해 주세요.')
            if c.execute('SELECT 1 FROM playlists WHERE name=?',(target,)).fetchone(): fail(409,'같은 이름의 재생목록이 이미 있습니다.')
            c.execute('INSERT INTO playlists(name,description,position,created) VALUES(?,?,?,?)',(target,body.description.strip(),body.position,time.time()))
            audit(c,a['id'],target,'playlist_create')
        elif body.action=='edit':
            if not name: fail(422,'수정할 재생목록을 확인해 주세요.')
            target=(new_name or name)
            if name=='전체 클래스' and target!='전체 클래스': fail(409,'기본 재생목록 이름은 변경할 수 없습니다.')
            if target!=name and c.execute('SELECT 1 FROM playlists WHERE name=?',(target,)).fetchone(): fail(409,'같은 이름의 재생목록이 이미 있습니다.')
            if not c.execute('SELECT 1 FROM playlists WHERE name=?',(name,)).fetchone(): fail(404,'재생목록을 찾을 수 없습니다.')
            c.execute('UPDATE playlists SET name=?,description=?,position=? WHERE name=?',(target,body.description.strip(),body.position,name))
            c.execute('UPDATE videos SET playlist=?,playlist_description=?,revision=revision+1 WHERE playlist=?',(target,body.description.strip(),name))
            audit(c,a['id'],name,'playlist_edit',target)
        elif body.action=='delete':
            if name=='전체 클래스': fail(409,'기본 재생목록은 삭제할 수 없습니다.')
            if not c.execute('SELECT 1 FROM playlists WHERE name=?',(name,)).fetchone(): fail(404,'재생목록을 찾을 수 없습니다.')
            c.execute("UPDATE videos SET playlist='전체 클래스',playlist_description='',revision=revision+1 WHERE playlist=?",(name,))
            c.execute('DELETE FROM playlists WHERE name=?',(name,))
            audit(c,a['id'],name,'playlist_delete')
        else:
            fail(422,'지원하지 않는 동작입니다.')
    return {'ok':True}

class PlaylistMove(BaseModel):
    playlist: str=Field(min_length=1,max_length=80)

@app.post('/api/admin/videos/{ident}/playlist')
def move_video_playlist(ident: str,body: PlaylistMove,req: Request):
    a=require(req,admin=True,write=True)
    target=body.playlist.strip()
    with db() as c:
        v=c.execute('SELECT * FROM videos WHERE id=?',(ident,)).fetchone()
        if not v: fail(404,'영상을 찾을 수 없습니다.')
        p=c.execute('SELECT * FROM playlists WHERE name=?',(target,)).fetchone()
        if not p: fail(404,'재생목록을 찾을 수 없습니다.')
        tail=c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM videos WHERE playlist=? AND state NOT IN ('trash','purging')",(target,)).fetchone()[0]
        c.execute('UPDATE videos SET playlist=?,playlist_description=?,position=?,revision=revision+1 WHERE id=?',(target,p['description'],tail,ident))
        audit(c,a['id'],ident,'video_move_playlist',target)
    return {'ok':True}


class PlaylistVideoOrder(BaseModel):
    ids: list[str]=Field(min_length=1,max_length=1500)

@app.post('/api/admin/playlists/{playlist_name}/order')
def save_playlist_order(playlist_name: str, body: PlaylistVideoOrder, req: Request):
    manager=require(req,admin=True,write=True)
    name=playlist_name.strip()
    if not name or len(name)>80: fail(422,'재생목록 이름을 확인해 주세요.')
    if len(body.ids)!=len(set(body.ids)) or any(not re.fullmatch(r'[0-9a-f]{32}',x) for x in body.ids):
        fail(422,'영상 번호에 중복 또는 잘못된 항목이 있습니다.')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM playlists WHERE name=?',(name,)).fetchone():
            fail(404,'재생목록을 찾을 수 없습니다.')
        current=c.execute("SELECT id FROM videos WHERE playlist=? AND state NOT IN ('trash','purging')",(name,)).fetchall()
        if len(body.ids)!=len(current) or set(body.ids)!={r['id'] for r in current}:
            fail(409,'재생목록에 영상이 변경됐습니다. 다시 열고 저장해 주세요.')
        for n,ident in enumerate(body.ids):
            c.execute("UPDATE videos SET position=?,revision=revision+1 WHERE id=? AND playlist=?",(n,ident,name))
        audit(c,manager['id'],name,'playlist_video_order',str(len(body.ids)))
    return {'ok':True,'count':len(body.ids)}

@app.get('/api/admin/videos')
def admin_videos(req: Request):
    require(req,admin=True)
    with db() as c: return {'items':[dict(v) for v in c.execute('SELECT v.* FROM videos v LEFT JOIN playlists pl ON pl.name=v.playlist ORDER BY COALESCE(pl.position,0),v.position,v.created DESC')]}
class VideoChange(BaseModel):
    action: str
    revision: int
    title: str=Field(default='',max_length=120)
    description: str=Field(default='',max_length=1000)
    playlist: str=Field(default='전체 클래스',max_length=80)
    playlist_description: str=Field(default='',max_length=300)
    theme: str='rose'
    position: int=Field(default=0,ge=-100000,le=100000)
    confirmation: str=Field(default='',max_length=120)
@app.post('/api/admin/videos/{ident}')
def edit_video(ident: str,body: VideoChange,req: Request):
    a=require(req,admin=True,write=True)
    with db() as c:
        c.execute('BEGIN IMMEDIATE'); v=c.execute('SELECT * FROM videos WHERE id=?',(ident,)).fetchone()
        if not v: fail(404,'영상을 찾을 수 없습니다.')
        if v['revision']!=body.revision: fail(409,'영상 정보가 변경되었습니다. 다시 확인해 주세요.')
        if body.action=='edit':
            if not body.title.strip() or body.theme not in ('rose','lilac','sage'): fail(422,'제목과 테마를 확인해 주세요.')
            playlist=(body.playlist.strip() or '전체 클래스')
            c.execute('UPDATE videos SET title=?,description=?,playlist=?,playlist_description=?,theme=?,position=?,revision=revision+1 WHERE id=?',(body.title,body.description,playlist,body.playlist_description.strip(),body.theme,body.position,ident))
        else:
            states={'publish':'public','private':'private','trash':'trash','restore':'private','purge':'purging'}
            if body.action not in states: fail(422,'지원하지 않는 동작입니다.')
            if body.action in ('restore','purge') and v['state'] not in ('trash','purging'): fail(409,'휴지통의 영상만 처리할 수 있습니다.')
            if body.action in ('publish','private') and v['state'] in ('trash','purging'): fail(409,'먼저 영상을 복원해 주세요.')
            if body.action=='purge' and body.confirmation!=v['title']: fail(422,'영구 삭제할 영상 제목을 정확히 입력해 주세요.')
            c.execute('UPDATE videos SET state=?,revision=revision+1 WHERE id=?',(states[body.action],ident))
        audit(c,a['id'],ident,'video_'+body.action)
    if body.action=='purge':
        # Removing a Drive-backed video only removes it from this site; the original Drive file stays untouched.
        if v['source']=='local':
            (DATA/'media'/ident).unlink(missing_ok=True)
        (DATA/'posters'/(ident+'.jpg')).unlink(missing_ok=True)
        with db() as c:
            c.execute('DELETE FROM videos WHERE id=?',(ident,))
            c.execute('DELETE FROM uploads WHERE id=?',(ident,))
    return {'ok':True}
@app.post('/api/admin/videos/{ident}/poster')
async def poster_upload(ident: str,req: Request):
    a=require(req,admin=True,write=True); video_row(ident)
    buf=bytearray()
    async for chunk in req.stream():
        buf.extend(chunk)
        if len(buf)>2*1024**2: fail(413,'썸네일은 2MB 이하로 선택해 주세요.')
    try:
        with Image.open(io.BytesIO(buf)) as image:
            if image.width*image.height>20_000_000: fail(422,'썸네일 해상도가 너무 큽니다.')
            image=image.convert('RGB'); image.thumbnail((1280,720))
            image.save(DATA/'posters'/(ident+'.jpg'),'JPEG',quality=85)
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError): fail(422,'지원하는 그림 파일이 아닙니다.')
    with db() as c: c.execute('UPDATE videos SET poster=1,revision=revision+1 WHERE id=?',(ident,)); audit(c,a['id'],ident,'poster')
    return {'ok':True}
@app.get('/api/posters/{ident}')
def poster(ident: str,req: Request):
    v=video_row(ident)
    if v['state']!='public' or not settings()['open']: require(req,admin=True)
    p=DATA/'posters'/(v['id']+'.jpg')
    if not p.exists(): fail(404,'썸네일이 없습니다.')
    return FileResponse(p,media_type='image/jpeg')
@app.get('/api/videos/{ident}/access')
def access(ident: str,req: Request):
    watch(req,ident); return {'ok':True}
class Position(BaseModel):
    seconds: float=Field(ge=0,le=86400,allow_inf_nan=False)
@app.post('/api/videos/{ident}/progress')
def progress(ident: str,body: Position,req: Request):
    require(req,write=True); m,v=watch(req,ident)
    with db() as c: c.execute('INSERT INTO progress VALUES(?,?,?,?) ON CONFLICT(member,video) DO UPDATE SET seconds=excluded.seconds,updated=excluded.updated',(m['id'],ident,min(body.seconds,v['duration'] or 86400),time.time()))
    return {'ok':True}

def byte_range(value,size):
    if not value: return 0,size-1,200
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
    if not match or not any(match.groups()): raise ValueError()
    a,b=match.groups()
    if not a:
        suffix=int(b)
        if suffix<=0: raise ValueError()
        start=max(0,size-suffix); end=size-1
    else:
        start=int(a); end=min(int(b) if b else size-1,size-1)
    if start>=size or end<start: raise ValueError()
    return start,end,206
@app.api_route('/api/videos/{ident}/stream',methods=['GET','HEAD'])
def stream(ident: str,req: Request):
    m,v=watch(req,ident)
    if v['source']!='local':
        if EXTERNAL_STREAMER is None: fail(503,'외부 영상 저장소 연결을 확인해 주세요.')
        return EXTERNAL_STREAMER(req,v)
    path=DATA/'media'/v['id']
    if not path.exists(): fail(404,'영상 원본을 찾을 수 없습니다.')
    size=path.stat().st_size
    try: start,end,status=byte_range(req.headers.get('range',''),size)
    except ValueError: return Response(status_code=416,headers={'Content-Range':f'bytes */{size}'})
    headers={'Accept-Ranges':'bytes','Content-Length':str(end-start+1),'Cache-Control':'private, no-store','Content-Disposition':'inline','X-Accel-Buffering':'no'}
    if status==206: headers['Content-Range']=f'bytes {start}-{end}/{size}'
    if req.method=='HEAD': return Response(status_code=status,media_type=v['mime'],headers=headers)
    def chunks():
        with path.open('rb') as f:
            f.seek(start); remaining=end-start+1
            while remaining>0:
                # Check the live DB and session on every emitted block, not only at player launch.
                try: watch(req,ident)
                except HTTPException: return
                block=f.read(min(256*1024,remaining))
                if not block: return
                remaining-=len(block); yield block
    return StreamingResponse(chunks(),status_code=status,media_type=v['mime'],headers=headers)

app.mount('/assets',StaticFiles(directory=ROOT/'web'),name='assets')
@app.get('/')
@app.get('/admin')
def index(): return FileResponse(ROOT/'web'/'index.html',headers={'Cache-Control':'no-cache'})
