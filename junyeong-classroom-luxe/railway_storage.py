"""Railway private multipart uploads and authorized direct playback."""
import math
import os
import re
import time
from datetime import datetime
from uuid import uuid4

CHUNK_SIZE = 16 * 1024 * 1024
DEFAULT_BUDGET = 1_000_000_000_000  # Operational ceiling; usage is billed by Railway.
MAX_FILE = 5 * 1024 ** 3
_EXTENSIONS = {"video/mp4": ".mp4", "video/webm": ".webm"}
_KEY_PATTERN = re.compile(r"^lessons/[0-9a-f]{32}\.(?:mp4|webm)$")


def part_count(total: int, part_size: int = CHUNK_SIZE) -> int:
    if not 1 <= total <= MAX_FILE:
        raise ValueError("unsupported video size")
    return math.ceil(total / part_size)


def expected_part_bytes(total: int, number: int) -> int:
    if not 1 <= number <= part_count(total):
        raise ValueError("invalid part number")
    return min(CHUNK_SIZE, total - (number-1) * CHUNK_SIZE)


def budget_for(env=None) -> int:
    env = env or os.environ
    try:
        return max(0, min(DEFAULT_BUDGET, int(env.get("VIDEO_STORAGE_LIMIT_BYTES", DEFAULT_BUDGET))))
    except (ValueError, TypeError):
        return DEFAULT_BUDGET


from railway_s3 import configured, client, cfg


def install(core, drive_streamer):
    """Attach optional private Railway upload & playback support to the existing app."""
    from fastapi import Request
    from fastapi.responses import RedirectResponse
    from pydantic import BaseModel, Field

    app = core.app
    with core.db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS railway_uploads(
          id TEXT PRIMARY KEY,
          owner TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT NOT NULL,
          filename TEXT NOT NULL,
          fingerprint TEXT NOT NULL,
          size INTEGER NOT NULL,
          mime TEXT NOT NULL,
          duration REAL NOT NULL DEFAULT 0,
          key TEXT NOT NULL UNIQUE,
          upload_id TEXT NOT NULL,
          replace_video TEXT NOT NULL DEFAULT '',
          state TEXT NOT NULL DEFAULT 'uploading',
          created REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS railway_upload_lookup ON railway_uploads(owner,fingerprint,state);
        CREATE TABLE IF NOT EXISTS railway_backup_sources(
          video_id TEXT PRIMARY KEY,
          old_source TEXT NOT NULL,
          old_key TEXT NOT NULL,
          old_size INTEGER NOT NULL,
          old_mime TEXT NOT NULL,
          old_duration REAL NOT NULL,
          created REAL NOT NULL
        );
        """)

    def storage_error(exc):
        # Never echo S3 credentials, signed URLs, or raw provider exception bodies.
        core.fail(503, "Railway 저장소 연결에 실패했습니다. 관리자 설정을 확인해 주세요.")

    def verify_owner(req, ident, write=False):
        owner = core.require(req, admin=True, write=write)
        with core.db() as db:
            row=db.execute("SELECT * FROM railway_uploads WHERE id=? AND owner=?", (ident, owner["id"])).fetchone()
        if not row:
            core.fail(404,"Railway 업로드 작업을 찾을 수 없습니다.")
        return owner,row

    def list_parts(s3,bucket,row):
        result=[]
        marker=0
        while True:
            data=s3.list_parts(Bucket=bucket,Key=row["key"],UploadId=row["upload_id"],
                PartNumberMarker=marker,MaxParts=1000)
            batch=data.get("Parts",[])
            result.extend(batch)
            if not data.get("IsTruncated"):
                break
            if not batch:
                core.fail(503,"Railway 업로드 조각 목록을 확인할 수 없습니다.")
            marker=batch[-1]["PartNumber"]
        return result

    def free_capacity(size:int):
        with core.db() as db:
            existing=db.execute("SELECT COALESCE(SUM(size),0) FROM videos WHERE source='railway' AND state!='purging'").fetchone()[0]
            active=db.execute("SELECT COALESCE(SUM(size),0) FROM railway_uploads WHERE state='uploading'").fetchone()[0]
        if existing+active+size > budget_for():
            core.fail(409,"Railway 설정된 저장 한도을 초과할 수 있습니다. 운영자에게 저장 한도 설정을 확인해 주세요.")

    @app.get("/api/admin/railway/status")
    def railway_status(req:Request):
        core.require(req,admin=True)
        with core.db() as db:
            used=db.execute("SELECT COALESCE(SUM(size),0) FROM videos WHERE source='railway' AND state!='purging'").fetchone()[0]
            uploading=db.execute("SELECT COUNT(*) FROM railway_uploads WHERE state='uploading'").fetchone()[0]
        migration=getattr(core.app.state,'video_migration',None)
        progress=migration.status() if migration else None
        return {"connected": configured(), "storageLimitBytes": budget_for(), "migration":progress,
                "registeredVideoBytes": used, "unfinishedUploads": uploading,
                "note":"실제 Railway 청구는 저장 용량과 서비스 사용량 기준입니다. 이 화면은 앱에 등록된 크기만 셉니다."}

    class Start(BaseModel):
        title: str=Field(min_length=1,max_length=120)
        description: str=Field(default="",max_length=1000)
        filename: str=Field(min_length=1,max_length=200)
        mime: str
        size: int=Field(ge=1,le=MAX_FILE)
        fingerprint: str=Field(pattern=r"^[a-f0-9]{64}$")
        duration: float=Field(ge=0,le=86400,allow_inf_nan=False)
        replaceVideoId: str=Field(default="",max_length=64)
        rights: bool

    @app.post("/api/admin/railway/uploads")
    def start_railway(body:Start, req:Request):
        a=core.require(req,admin=True,write=True)
        core.rate(req,"railway-start",30)
        if not body.rights:core.fail(422,"영상 제공 권한 확인이 필요합니다.")
        if body.mime not in _EXTENSIONS:core.fail(422,"MP4 또는 WebM을 선택해 주세요.")
        if not body.filename.lower().endswith(_EXTENSIONS[body.mime]):
            core.fail(422,"파일 형식과 확장자가 다릅니다.")
        if body.replaceVideoId:
            with core.db() as db:
                v=db.execute("SELECT * FROM videos WHERE id=?", (body.replaceVideoId,)).fetchone()
            if not v or v["source"] not in ("drive","local"):
                core.fail(409,"기존 Google Drive 또는 서버 영상만 교체할 수 있습니다.")
        # A matching interrupted upload is continued with the exact same file fingerprint.
        with core.db() as db:
            prev=db.execute("""SELECT * FROM railway_uploads
               WHERE owner=? AND fingerprint=? AND size=? AND replace_video=? AND state='uploading'
               ORDER BY created DESC LIMIT 1""",
               (a["id"],body.fingerprint,body.size,body.replaceVideoId)).fetchone()
        if prev:
            return {"id":prev["id"],"chunkSize":CHUNK_SIZE,"size":prev["size"],
                    "fingerprint":prev["fingerprint"],"resumed":True}
        free_capacity(body.size)
        ident=uuid4().hex
        key=f"lessons/{ident}{_EXTENSIONS[body.mime]}"
        try:
            s3,bucket=client()
            remote=s3.create_multipart_upload(Bucket=bucket,Key=key,ContentType=body.mime)
        except Exception as exc:storage_error(exc)
        with core.db() as db:
            db.execute("""INSERT INTO railway_uploads(
                id,owner,title,description,filename,fingerprint,size,mime,duration,key,upload_id,replace_video,state,created)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ident,a["id"],body.title.strip(),body.description,body.filename,body.fingerprint,
                 body.size,body.mime,body.duration,key,remote["UploadId"],body.replaceVideoId,
                 "uploading",time.time()))
            core.audit(db,a["id"],ident,"railway_upload_start")
        return {"id":ident,"chunkSize":CHUNK_SIZE,"size":body.size,
                "fingerprint":body.fingerprint,"resumed":False}

    @app.get("/api/admin/railway/uploads/{ident}")
    def status_railway(ident:str,req:Request):
        _,row=verify_owner(req,ident)
        try:
            s3,bucket=client()
            numbers=[int(p["PartNumber"]) for p in list_parts(s3,bucket,row)]
        except Exception as exc:storage_error(exc)
        return {"id":ident,"chunkSize":CHUNK_SIZE,"size":row["size"],
                "completed":numbers,"fingerprint":row["fingerprint"],"state":row["state"]}

    @app.post("/api/admin/railway/uploads/{ident}/parts/{number}")
    def sign_part(ident:str,number:int,req:Request):
        _,row=verify_owner(req,ident,write=True)
        if row["state"]!="uploading":core.fail(409,"업로드가 끝났거나 취소됐습니다.")
        try:expected_part_bytes(row["size"],number)
        except ValueError:core.fail(422,"잘못된 분할 번호입니다.")
        try:
            s3,bucket=client()
            signed=s3.generate_presigned_url("upload_part",Params={
                "Bucket":bucket,"Key":row["key"],"UploadId":row["upload_id"],"PartNumber":number,
            },ExpiresIn=900,HttpMethod="PUT")
        except Exception as exc:storage_error(exc)
        return {"url":signed,"part":number,"expiresIn":900}

    @app.post("/api/admin/railway/uploads/{ident}/finish")
    def finish_railway(ident:str,req:Request):
        a,row=verify_owner(req,ident,write=True)
        if row["state"]=="complete":
            return {"ok":True,"id":row["replace_video"] or ident}
        if row["state"]!="uploading":core.fail(409,"업로드를 마칠 수 없는 상태입니다.")
        try:
            s3,bucket=client()
            try:
                parts=list_parts(s3,bucket,row)
            except Exception:
                # A previous finish may have completed in Railway just before the
                # site's response was lost. Only accept that state after HEAD
                # confirms the exact object size; never fabricate completion.
                try:
                    current=s3.head_object(Bucket=bucket,Key=row["key"])
                    if int(current.get("ContentLength", -1))!=row["size"]:
                        raise ValueError("Railway object mismatch")
                    parts=None
                except Exception:
                    raise
            if parts is not None:
                count=part_count(row["size"])
                if len(parts)!=count or [p["PartNumber"] for p in parts]!=list(range(1,count+1)):
                    core.fail(409,"아직 전송되지 않은 영상 조각이 있습니다.")
                for part in parts:
                    if int(part["Size"])!=expected_part_bytes(row["size"],int(part["PartNumber"])):
                        core.fail(409,"일부 영상 조각의 용량이 일치하지 않습니다.")
                # Complete using only provider-enumerated ETags.
                s3.complete_multipart_upload(
                    Bucket=bucket,Key=row["key"],UploadId=row["upload_id"],
                    MultipartUpload={"Parts":[{"ETag":p["ETag"],"PartNumber":p["PartNumber"]} for p in parts]},
                )
            head=s3.head_object(Bucket=bucket,Key=row["key"])
            if int(head.get("ContentLength", -1))!=row["size"]:
                core.fail(502,"저장소 영상 크기 확인에 실패했습니다. 운영자에게 문의해 주세요.")
        except Exception as exc:
            if isinstance(exc, core.HTTPException):raise
            storage_error(exc)
        with core.db() as db:
            db.execute("BEGIN IMMEDIATE")
            latest=db.execute("SELECT * FROM railway_uploads WHERE id=? AND owner=?",
                               (ident,a["id"])).fetchone()
            if not latest or latest["state"]!="uploading":
                core.fail(409,"업로드 처리 상태가 변경됐습니다.")
            video_id=latest["replace_video"] or ident
            if latest["replace_video"]:
                previous=db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
                if not previous or previous["source"] not in ("drive","local"):
                    core.fail(409,"교체할 영상의 상태가 변경됐습니다.")
                db.execute("""INSERT OR REPLACE INTO railway_backup_sources(
                    video_id,old_source,old_key,old_size,old_mime,old_duration,created)
                    VALUES(?,?,?,?,?,?,?)""",
                    (video_id,previous["source"],previous["external_id"],previous["size"],
                     previous["mime"],previous["duration"],time.time()))
                db.execute("""UPDATE videos SET source='railway',external_id=?,size=?,mime=?,
                    duration=CASE WHEN ? > 0 THEN ? ELSE duration END, revision=revision+1
                    WHERE id=?""",
                    (latest["key"],latest["size"],latest["mime"],
                     latest["duration"],latest["duration"],video_id))
            else:
                db.execute("""INSERT INTO videos(id,title,description,size,mime,duration,created,source,external_id)
                    VALUES(?,?,?,?,?,?,?,'railway',?)""",
                    (video_id,latest["title"],latest["description"],latest["size"],
                     latest["mime"],latest["duration"],time.time(),latest["key"]))
            db.execute("UPDATE railway_uploads SET state='complete' WHERE id=?", (ident,))
            core.audit(db,a["id"],video_id,"railway_video_replace" if latest["replace_video"] else "railway_video_add")
        return {"ok":True,"id":video_id,"replaced":bool(row["replace_video"])}

    @app.post("/api/admin/railway/uploads/{ident}/cancel")
    def cancel_railway(ident:str,req:Request):
        a,row=verify_owner(req,ident,write=True)
        if row["state"]!="uploading":core.fail(409,"완료된 영상은 영상 관리에서 삭제해 주세요.")
        try:
            s3,bucket=client()
            s3.abort_multipart_upload(Bucket=bucket,Key=row["key"],UploadId=row["upload_id"])
        except Exception as exc:storage_error(exc)
        with core.db() as db:
            db.execute("UPDATE railway_uploads SET state='cancelled' WHERE id=?", (ident,))
            core.audit(db,a["id"],ident,"railway_upload_cancel")
        return {"ok":True}

    @app.post("/api/admin/railway/videos/{video_id}/rollback")
    def rollback_railway(video_id:str,req:Request):
        a=core.require(req,admin=True,write=True)
        with core.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old=db.execute("SELECT * FROM railway_backup_sources WHERE video_id=?", (video_id,)).fetchone()
            v=db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
            if not old or not v or v["source"]!="railway":
                core.fail(409,"복원할 기존 영상 정보가 없습니다.")
            db.execute("""UPDATE videos SET source=?,external_id=?,size=?,mime=?,duration=?,
                revision=revision+1 WHERE id=?""",
                (old["old_source"],old["old_key"],old["old_size"],old["old_mime"],
                 old["old_duration"],video_id))
            db.execute("DELETE FROM railway_backup_sources WHERE video_id=?",(video_id,))
            core.audit(db,a["id"],video_id,"railway_video_rollback")
        return {"ok":True}

    def stream_railway(req,v):
        # Request is authenticated and authorized by core.watch() before this call.
        key=v["external_id"]
        if not _KEY_PATTERN.fullmatch(key or ""):
            core.fail(404,"Railway 영상 정보가 올바르지 않습니다.")
        try:
            s3,bucket=client()
            method="HEAD" if req.method=="HEAD" else "GET"
            signed=s3.generate_presigned_url(
                "head_object" if method=="HEAD" else "get_object",
                Params={"Bucket":bucket,"Key":key}, ExpiresIn=300,HttpMethod=method)
        except Exception as exc:storage_error(exc)
        # The original /api/videos/{id}/stream remains the video element's URL.
        # Every new Range request gets a fresh signed Railway redirect and re-checks
        # the current membership. Signed URLs can still be shared until expiry.
        return RedirectResponse(signed,status_code=307,headers={
            "Cache-Control":"private, no-store","Referrer-Policy":"no-referrer",
        })

    def delete_railway(v):
        key=v["external_id"]
        if not _KEY_PATTERN.fullmatch(key or ""):
            core.fail(404,"Railway 영상 정보가 올바르지 않습니다.")
        try:
            s3,bucket=client()
            s3.delete_object(Bucket=bucket,Key=key)
        except Exception as exc:storage_error(exc)

    def dispatch(req,v):
        if v["source"]=="railway":
            return stream_railway(req,v)
        return drive_streamer(req,v)

    core.EXTERNAL_STREAMER=dispatch
    core.RAILWAY_DELETE_VIDEO=delete_railway

