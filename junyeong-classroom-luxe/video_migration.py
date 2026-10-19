"""Operator-enabled, restartable migration; originals and video identities are retained.

Only verified complete objects are switched into the catalogue. No multi-GB
temporary files, user impersonation, or publicly accessible maintenance routes.
"""
import base64
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from uuid import uuid4

import requests
from fastapi import Request
from railway_s3 import client, configured, configure_cors, redirect, signed_url

PART = 16 * 1024 * 1024
PUBLIC_ASSETS = {
    '1Mk-XP_VHX4BW0R0p7vFoECr_dxsdxU_H': ('background-music.mp3', 'audio/mpeg'),
    '1du6wZx1Ga94vLo0zPGF-ekRwObRWCDwF': ('brand-intro.mp4', 'video/mp4'),
    '13ayyUIBfCoWuTH9ghVCwHDNvrxhWJ4AP': ('brand-poster.jpg', 'image/jpeg'),
}


class MigrationError(Exception):
    pass


def report(event, **values):
    # Do not log source URLs, titles, credentials, provider bodies, or signed URLs.
    print('VIDEO_MIGRATION ' + json.dumps(dict(event=event, **values)), flush=True)


class Migration:
    def __init__(self, core, auth_headers):
        self.core = core
        self.auth_headers = auth_headers
        self.stop = threading.Event()
        self.thread = None
        with core.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS railway_migrations(
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL,
              source_key TEXT NOT NULL, object_key TEXT NOT NULL UNIQUE,
              size INTEGER NOT NULL DEFAULT 0, mime TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
              upload_id TEXT NOT NULL DEFAULT '', transferred INTEGER NOT NULL DEFAULT 0,
              state TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
              retry_at REAL NOT NULL DEFAULT 0, updated REAL NOT NULL);
            ''')

    def update(self, ident, **fields):
        fields['updated'] = time.time()
        with self.core.db() as db:
            db.execute('UPDATE railway_migrations SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',
                       (*fields.values(), ident))

    def status(self):
        with self.core.db() as db:
            rows = db.execute('SELECT id,kind,size,state,transferred,error,retry_at FROM railway_migrations').fetchall()
            sources = dict(db.execute('SELECT source,COUNT(*) FROM videos WHERE state!=? GROUP BY source',('purging',)).fetchall())
        return dict(total=len(rows), complete=sum(r['state']=='complete' for r in rows),
                    bytes=sum(r['size'] for r in rows),
                    completeBytes=sum(r['size'] for r in rows if r['state']=='complete'),
                    sources=sources, items=[dict(r) for r in rows])

    def inventory(self):
        with self.core.db() as db:
            videos=db.execute("SELECT * FROM videos WHERE source IN ('drive','local') AND state!='purging'").fetchall()
            for v in videos:
                key='lessons/'+uuid4().hex+('.webm' if v['mime']=='video/webm' else '.mp4')
                db.execute('''INSERT OR IGNORE INTO railway_migrations
                    (id,kind,source,source_key,object_key,size,mime,updated) VALUES(?,?,?,?,?,?,?,?)''',
                    (v['id'],'lesson',v['source'],v['external_id'],key,v['size'],v['mime'],time.time()))
            for file_id, (name,mime) in PUBLIC_ASSETS.items():
                db.execute('''INSERT OR IGNORE INTO railway_migrations
                    (id,kind,source,source_key,object_key,mime,updated) VALUES(?,?,?,?,?,?,?)''',
                    ('public:'+name,'public','drive',file_id,'public/'+name,mime,time.time()))
        status=self.status()
        report('inventory', total=status['total'], registeredBytes=status['bytes'], sources=status['sources'])

    def backup(self):
        folder=self.core.DATA/'backups'
        folder.mkdir(mode=0o700, exist_ok=True)
        target=folder/'before-railway-migration.sqlite3'
        if target.exists():return
        temporary=target.with_suffix('.tmp')
        with self.core.db() as source, sqlite3.connect(temporary) as destination:
            source.backup(destination)
        temporary.chmod(0o600)
        temporary.replace(target)
        report('database_backup_complete')

    def metadata(self, row):
        if row['source']=='local':
            path=self.core.DATA/'media'/row['id']
            digest=hashlib.md5()
            with path.open('rb') as source:
                for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
            return dict(size=path.stat().st_size,mimeType=row['mime'],md5Checksum=digest.hexdigest())
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,200}',row['source_key']):
            raise MigrationError('invalid_source_id')
        with requests.get('https://www.googleapis.com/drive/v3/files/'+row['source_key'],
                params={'fields':'id,size,mimeType,md5Checksum,version,modifiedTime,trashed,videoMediaMetadata',
                        'supportsAllDrives':'true'},headers=self.auth_headers(),timeout=(15,60)) as response:
            if response.status_code!=200:
                raise MigrationError('drive_metadata_http_'+str(response.status_code))
            data=response.json()
        if data.get('trashed'):raise MigrationError('source_in_trash')
        if not int(data.get('size') or 0) or not re.fullmatch('[0-9a-f]{32}',data.get('md5Checksum','')):
            raise MigrationError('missing_size_or_checksum')
        if data.get('mimeType')!=row['mime']:
            raise MigrationError('source_type_changed')
        return data

    @contextlib.contextmanager
    def source(self, row, offset):
        if row['source']=='local':
            with (self.core.DATA/'media'/row['id']).open('rb') as stream:
                stream.seek(offset)
                yield stream
            return
        headers=self.auth_headers()
        if offset:headers['Range']=f'bytes={offset}-'
        with requests.get('https://www.googleapis.com/drive/v3/files/'+row['source_key'],
                params={'alt':'media'},headers=headers,stream=True,timeout=(15,90)) as response:
            if response.status_code not in (200,206):
                code='drive_http_'+str(response.status_code)
                if response.status_code==403:
                    # Respect quota errors: pause rather than trying alternate accounts or URLs.
                    try:
                        reasons={e.get('reason','') for e in response.json().get('error',{}).get('errors',[])}
                        if 'downloadQuotaExceeded' in reasons:code='drive_download_quota'
                    except (ValueError,TypeError,AttributeError):pass
                raise MigrationError(code)
            if offset and (response.status_code!=206 or
                    not response.headers.get('Content-Range','').startswith(f'bytes {offset}-')):
                raise MigrationError('source_range_mismatch')
            response.raw.decode_content=True
            yield response.raw

    def list_parts(self,s3,bucket,row):
        parts=[];marker=0
        while True:
            data=s3.list_parts(Bucket=bucket,Key=row['object_key'],UploadId=row['upload_id'],
                               PartNumberMarker=marker,MaxParts=1000)
            batch=data.get('Parts',[]);parts.extend(batch)
            if not data.get('IsTruncated'):return parts
            if not batch:raise MigrationError('invalid_part_listing')
            marker=batch[-1]['PartNumber']

    def check_stopped(self):
        if self.stop.is_set():raise MigrationError('stopped')

    def verify_object(self,s3,bucket,row,meta):
        head=s3.head_object(Bucket=bucket,Key=row['object_key'])
        size=int(meta['size'])
        if head['ContentLength']!=size or head.get('Metadata',{}).get('source-md5')!=meta['md5Checksum']:
            raise MigrationError('destination_metadata_mismatch')
        digest=hashlib.md5();count=0
        remote=s3.get_object(Bucket=bucket,Key=row['object_key'])['Body']
        try:
            for block in iter(lambda:remote.read(1024*1024),b''):
                self.check_stopped();digest.update(block);count+=len(block)
        finally:remote.close()
        if count!=size or digest.hexdigest()!=meta['md5Checksum']:
            raise MigrationError('destination_checksum_mismatch')
        # Verify real signed delivery, seeking and browser origin support as well.
        for start in (0,max(0,size-1024)):
            with requests.get(signed_url(row['object_key']),headers={
                    'Range':f'bytes={start}-{min(start+1023,size-1)}','Origin':self.core.ORIGIN},
                    stream=True,timeout=(15,60)) as response:
                if response.status_code!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {start}-'):
                    raise MigrationError('signed_range_verification_failed')
                if response.headers.get('Access-Control-Allow-Origin') not in (self.core.ORIGIN,'*'):
                    raise MigrationError('storage_cors_verification_failed')
        # Bucket must remain private when no signature or credentials are supplied.
        from railway_s3 import origin
        with requests.head(origin()+'/'+row['object_key'],timeout=(15,30)) as response:
            if response.status_code not in (401,403,404):
                raise MigrationError('unsigned_object_accessible')

    def transfer(self,row):
        meta=self.metadata(row)
        size=int(meta['size'])
        if size>100*1024**3:raise MigrationError('file_above_migration_limit')
        previous=json.loads(row['metadata'])
        if previous and (previous.get('md5Checksum')!=meta['md5Checksum'] or int(previous['size'])!=size):
            raise MigrationError('source_changed_since_migration_started')
        self.update(row['id'],size=size,metadata=json.dumps(meta),state='uploading',error='',retry_at=0)
        row=dict(row,size=size,metadata=json.dumps(meta))
        s3,bucket=client()
        exists=False
        try:
            head=s3.head_object(Bucket=bucket,Key=row['object_key'])
            exists=True
        except Exception as error:
            if getattr(error,'response',{}).get('ResponseMetadata',{}).get('HTTPStatusCode')!=404:raise
        if not exists:
            parts=[]
            if row['upload_id']:
                try:parts=self.list_parts(s3,bucket,row)
                except Exception as error:
                    if getattr(error,'response',{}).get('Error',{}).get('Code')!='NoSuchUpload':raise
                    row['upload_id']=''
            if not row['upload_id']:
                upload=s3.create_multipart_upload(Bucket=bucket,Key=row['object_key'],ContentType=row['mime'],
                    Metadata={'source-md5':meta['md5Checksum']})
                row['upload_id']=upload['UploadId']
                self.update(row['id'],upload_id=row['upload_id'])
            offset=0
            for number,p in enumerate(parts,1):
                expected=min(PART,size-offset)
                if p['PartNumber']!=number or p['Size']!=expected:raise MigrationError('multipart_resume_mismatch')
                offset+=p['Size']
            report('transfer_start',id=row['id'],bytes=size,resumeBytes=offset)
            last_report=time.monotonic()
            if offset<size:
                with self.source(row,offset) as source:
                    while offset<size:
                        self.check_stopped()
                        length=min(PART,size-offset)
                        body=bytearray()
                        while len(body)<length:
                            chunk=source.read(length-len(body))
                            if not chunk:raise MigrationError('source_ended_early')
                            body.extend(chunk)
                        payload=bytes(body);del body
                        number=len(parts)+1
                        result=s3.upload_part(Bucket=bucket,Key=row['object_key'],UploadId=row['upload_id'],
                            PartNumber=number,Body=payload,
                            ContentMD5=base64.b64encode(hashlib.md5(payload).digest()).decode('ascii'))
                        parts.append(dict(PartNumber=number,ETag=result['ETag'],Size=length))
                        offset+=length;del payload
                        self.update(row['id'],transferred=offset)
                        if time.monotonic()-last_report>=25 or offset==size:
                            report('progress',id=row['id'],transferred=offset,bytes=size)
                            last_report=time.monotonic()
                    if source.read(1):raise MigrationError('source_larger_than_metadata')
            latest=self.metadata(row)
            if latest['md5Checksum']!=meta['md5Checksum'] or int(latest['size'])!=size:
                raise MigrationError('source_changed_during_transfer')
            s3.complete_multipart_upload(Bucket=bucket,Key=row['object_key'],UploadId=row['upload_id'],
                MultipartUpload={'Parts':[{'PartNumber':p['PartNumber'],'ETag':p['ETag']} for p in parts]})
        self.update(row['id'],state='verifying',transferred=size)
        report('verify_start',id=row['id'],bytes=size)
        self.verify_object(s3,bucket,row,meta)
        with self.core.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if row['kind']=='lesson':
                v=db.execute('SELECT * FROM videos WHERE id=?',(row['id'],)).fetchone()
                if not v or v['source']!=row['source'] or v['external_id']!=row['source_key'] or v['state']=='purging':
                    raise MigrationError('catalogue_source_changed')
                db.execute('''INSERT OR IGNORE INTO railway_backup_sources
                    (video_id,old_source,old_key,old_size,old_mime,old_duration,created) VALUES(?,?,?,?,?,?,?)''',
                    (v['id'],v['source'],v['external_id'],v['size'],v['mime'],v['duration'],time.time()))
                # Preserve title, playlist, visibility, order, posters and all foreign keys.
                db.execute("UPDATE videos SET source='railway',external_id=?,size=?,revision=revision+1 WHERE id=?",
                           (row['object_key'],size,row['id']))
                self.core.audit(db,'operator-migration',row['id'],'video_storage_migrated')
            db.execute("UPDATE railway_migrations SET state='complete',error='',retry_at=0,updated=? WHERE id=?",
                       (time.time(),row['id']))
        report('complete',id=row['id'],kind=row['kind'],bytes=size,checksumVerified=True,rangeVerified=True)

    def run(self):
        # Protect the journal from overlapping workers during a restart/deployment.
        import fcntl
        with (self.core.DATA/'railway-migration.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            try:
                configure_cors(self.core.ORIGIN)
                report('storage_connected')
                self.backup();self.inventory()
                while not self.stop.is_set():
                    with self.core.db() as db:
                        rows=[dict(r) for r in db.execute("""SELECT * FROM railway_migrations
                            WHERE state!='complete' AND retry_at<=? ORDER BY kind DESC,size,id""",(time.time(),))]
                    for row in rows:
                        if self.stop.is_set():return
                        try:self.transfer(row)
                        except Exception as error:
                            if self.stop.is_set():return
                            code=str(error) if isinstance(error,MigrationError) else type(error).__name__
                            retry=time.time()+(24*3600 if 'quota' in code or 'drive_http_403' in code else 6*3600)
                            self.update(row['id'],state='blocked',error=code,retry_at=retry)
                            report('blocked',id=row['id'],reason=code,retryAt=retry)
                    status=self.status()
                    report('summary',total=status['total'],complete=status['complete'],bytes=status['bytes'],
                           completeBytes=status['completeBytes'],sources=status['sources'])
                    if status['complete']==status['total']:return
                    self.stop.wait(60)
            except Exception as error:
                report('worker_error',reason=type(error).__name__)

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop.clear()
        self.thread=threading.Thread(target=self.run,name='video-migration',daemon=True)
        self.thread.start()

    def identity(self, video):
        if video['source']!='railway':return video['external_id']
        with self.core.db() as db:
            row=db.execute("SELECT * FROM railway_migrations WHERE id=? AND object_key=? AND state='complete'",
                           (video['id'],video['external_id'])).fetchone()
        return row['source_key'] if row and row['source']=='drive' else video['external_id']

    def object_metadata(self,video):
        s3,bucket=client()
        head=s3.head_object(Bucket=bucket,Key=video['external_id'])
        if int(head['ContentLength'])!=int(video['size']):raise RuntimeError('저장소 영상 크기가 변경되었습니다.')
        with self.core.db() as db:
            row=db.execute("SELECT metadata FROM railway_migrations WHERE id=? AND object_key=? AND state='complete'",
                           (video['id'],video['external_id'])).fetchone()
        meta=json.loads(row['metadata']) if row else dict(size=head['ContentLength'],mimeType=video['mime'],
            version=head['ETag'],videoMediaMetadata={'durationMillis':int(video['duration']*1000)})
        meta['sourceIdentity']=self.identity(video)
        return meta

    def public_media(self,req,file_id):
        if file_id not in PUBLIC_ASSETS:return None
        with self.core.db() as db:
            row=db.execute("SELECT object_key FROM railway_migrations WHERE source_key=? AND kind='public' AND state='complete'",
                           (file_id,)).fetchone()
        return redirect(row['object_key'],req.method) if row else None


def install(core,auth_headers):
    migration=Migration(core,auth_headers)
    core.MEDIA_IDENTITY=migration.identity
    core.OBJECT_MEDIA_METADATA=migration.object_metadata
    core.OBJECT_MEDIA_URL=lambda video:signed_url(video['external_id'])
    core.PUBLIC_OBJECT_MEDIA=migration.public_media
    core.app.state.video_migration=migration

    @core.app.get('/api/admin/railway/migration')
    def migration_status(req:Request):
        core.require(req,admin=True)
        return migration.status()

    previous=core.app.router.lifespan_context
    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with previous(app):
            if configured() and os.getenv('VIDEO_MIGRATION_ENABLED','').lower()=='true':migration.start()
            try:yield
            finally:migration.stop.set()
    core.app.router.lifespan_context=lifespan
