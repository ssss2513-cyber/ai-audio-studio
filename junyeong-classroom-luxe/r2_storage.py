"""Private Cloudflare R2 video backend (optional; inactive without R2 env settings).

Video bytes go directly browser <-> Cloudflare R2. The authenticated server only
creates short-lived upload signatures and redirects approved video GET requests
to short-lived R2 signed GET URLs. This avoids sending multi-GB streams through
Railway. Signed links are bearer capabilities until expiry: not DRM.
"""
from __future__ import annotations

import math
import os
import re
import time
from datetime import datetime
from uuid import uuid4

CHUNK_SIZE = 16 * 1024 * 1024
DEFAULT_BUDGET = 9_000_000_000  # Below R2 Standard 10 GB-month free allowance.
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
        return max(0, min(DEFAULT_BUDGET, int(env.get("R2_FREE_BUDGET_BYTES", DEFAULT_BUDGET))))
    except (ValueError, TypeError):
        return DEFAULT_BUDGET


def configured(env=None) -> bool:
    env = env or os.environ
    return all(str(env.get(k, "")).strip() for k in
        ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"))


def install(core, drive_streamer):
    """Attach optional private R2 upload & playback support to the existing app."""
    from fastapi import Request
    from fastapi.responses import RedirectResponse
    from pydantic import BaseModel, Field
    from botocore.config import Config
    import boto3

    app = core.app
    with core.db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS r2_uploads(
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
        CREATE INDEX IF NOT EXISTS r2_upload_lookup ON r2_uploads(owner,fingerprint,state);
        CREATE TABLE IF NOT EXISTS r2_backup_sources(
          video_id TEXT PRIMARY KEY,
          old_source TEXT NOT NULL,
          old_key TEXT NOT NULL,
          old_size INTEGER NOT NULL,
          old_mime TEXT NOT NULL,
          old_duration REAL NOT NULL,
          created REAL NOT NULL
        );
        """)

    def cfg():
        if not configured():
            core.fail(503, "R2 무료 저장소 연결 전입니다. 관리자에게 문의해 주세요.")
        account = os.environ["R2_ACCOUNT_ID"].strip()
        if not re.fullmatch(r"[0-9a-fA-F]{32}", account):
            core.fail(503, "R2 계정 식별자를 확인해 주세요.")
        return {
            "account": account,
            "bucket": os.environ["R2_BUCKET"].strip(),
            "key_id": os.environ["R2_ACCESS_KEY_ID"].strip(),
            "secret": os.environ["R2_SECRET_ACCESS_KEY"].strip(),
        }

    def client():
        c = cfg()
        return boto3.client(
            "s3", endpoint_url=f"https://{c['account']}.r2.cloudflarestorage.com",
            region_name="auto",
            aws_access_key_id=c["key_id"],
            aws_secret_access_key=c["secret"],
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          retries={"mode": "standard", "max_attempts": 3}),
        ), c["bucket"]

    def storage_error(exc):
        # Never echo S3 credentials, signed URLs, or raw provider exception bodies.
        core.fail(503, "Cloudflare R2 저장소 연결에 실패했습니다. 관리자 설정을 확인해 주세요.")

    def verify_owner(req, ident, write=False):
        owner = core.require(req, admin=True, write=write)
        with core.db() as db:
            row=db.execute("SELECT * FROM r2_uploads WHERE id=? AND owner=?", (ident, owner["id"])).fetchone()
        if not row:
            core.fail(404,"R2 업로드 작업을 찾을 수 없습니다.")
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
                core.fail(503,"R2 업로드 조각 목록을 확인할 수 없습니다.")
            marker=batch[-1]["PartNumber"]
        return result

    def free_capacity(size:int):
        with core.db() as db:
            existing=db.execute("SELECT COALESCE(SUM(size),0) FROM videos WHERE source='r2' AND state!='purging'").fetchone()[0]
            active=db.execute("SELECT COALESCE(SUM(size),0) FROM r2_uploads WHERE state='uploading'").fetchone()[0]
        if existing+active+size > budget_for():
            core.fail(409,"R2 무료 저장 예산을 초과할 수 있습니다. 불필요한 파일을 먼저 정리해 주세요.")

    @app.get("/api/admin/r2/status")
    def r2_status(req:Request):
        core.require(req,admin=True)
        with core.db() as db:
            used=db.execute("SELECT COALESCE(SUM(size),0) FROM videos WHERE source='r2' AND state!='purging'").fetchone()[0]
            uploading=db.execute("SELECT COUNT(*) FROM r2_uploads WHERE state='uploading'").fetchone()[0]
        return {"connected": configured(), "freeBudgetBytes": budget_for(),
                "registeredVideoBytes": used, "unfinishedUploads": uploading,
                "note":"실제 R2 청구는 계정 전체 사용량 기준입니다. 이 화면은 앱에 등록된 크기만 셉니다."}

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

    @app.post("/api/admin/r2/uploads")
    def start_r2(body:Start, req:Request):
        a=core.require(req,admin=True,write=True)
        core.rate(req,"r2-start",30)
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
            prev=db.execute("""SELECT * FROM r2_uploads
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
            db.execute("""INSERT INTO r2_uploads(
                id,owner,title,description,filename,fingerprint,size,mime,duration,key,upload_id,replace_video,state,created)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ident,a["id"],body.title.strip(),body.description,body.filename,body.fingerprint,
                 body.size,body.mime,body.duration,key,remote["UploadId"],body.replaceVideoId,
                 "uploading",time.time()))
            core.audit(db,a["id"],ident,"r2_upload_start")
        return {"id":ident,"chunkSize":CHUNK_SIZE,"size":body.size,
                "fingerprint":body.fingerprint,"resumed":False}

    @app.get("/api/admin/r2/uploads/{ident}")
    def status_r2(ident:str,req:Request):
        _,row=verify_owner(req,ident)
        try:
            s3,bucket=client()
            numbers=[int(p["PartNumber"]) for p in list_parts(s3,bucket,row)]
        except Exception as exc:storage_error(exc)
        return {"id":ident,"chunkSize":CHUNK_SIZE,"size":row["size"],
                "completed":numbers,"fingerprint":row["fingerprint"],"state":row["state"]}

    @app.post("/api/admin/r2/uploads/{ident}/parts/{number}")
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

    @app.post("/api/admin/r2/uploads/{ident}/finish")
    def finish_r2(ident:str,req:Request):
        a,row=verify_owner(req,ident,write=True)
        if row["state"]=="complete":
            return {"ok":True,"id":row["replace_video"] or ident}
        if row["state"]!="uploading":core.fail(409,"업로드를 마칠 수 없는 상태입니다.")
        try:
            s3,bucket=client()
            parts=list_parts(s3,bucket,row)
            count=part_count(row["size"])
            if len(parts)!=count or [p["PartNumber"] for p in parts]!=list(range(1,count+1)):
                core.fail(409,"아직 전송되지 않은 영상 조각이 있습니다.")
            for part in parts:
                if int(part["Size"])!=expected_part_bytes(row["size"],int(part["PartNumber"])):
                    core.fail(409,"일부 영상 조각의 용량이 일치하지 않습니다.")
            # Complete only server-enumerated ETags, never accept client-submitted ETags.
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
            latest=db.execute("SELECT * FROM r2_uploads WHERE id=? AND owner=?",
                               (ident,a["id"])).fetchone()
            if not latest or latest["state"]!="uploading":
                core.fail(409,"업로드 처리 상태가 변경됐습니다.")
            video_id=latest["replace_video"] or ident
            if latest["replace_video"]:
                previous=db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
                if not previous or previous["source"] not in ("drive","local"):
                    core.fail(409,"교체할 영상의 상태가 변경됐습니다.")
                db.execute("""INSERT OR REPLACE INTO r2_backup_sources(
                    video_id,old_source,old_key,old_size,old_mime,old_duration,created)
                    VALUES(?,?,?,?,?,?,?)""",
                    (video_id,previous["source"],previous["external_id"],previous["size"],
                     previous["mime"],previous["duration"],time.time()))
                db.execute("""UPDATE videos SET source='r2',external_id=?,size=?,mime=?,
                    duration=CASE WHEN ? > 0 THEN ? ELSE duration END, revision=revision+1
                    WHERE id=?""",
                    (latest["key"],latest["size"],latest["mime"],
                     latest["duration"],latest["duration"],video_id))
            else:
                db.execute("""INSERT INTO videos(id,title,description,size,mime,duration,created,source,external_id)
                    VALUES(?,?,?,?,?,?,?,'r2',?)""",
                    (video_id,latest["title"],latest["description"],latest["size"],
                     latest["mime"],latest["duration"],time.time(),latest["key"]))
            db.execute("UPDATE r2_uploads SET state='complete' WHERE id=?", (ident,))
            core.audit(db,a["id"],video_id,"r2_video_replace" if latest["replace_video"] else "r2_video_add")
        return {"ok":True,"id":video_id,"replaced":bool(row["replace_video"])}

    @app.post("/api/admin/r2/uploads/{ident}/cancel")
    def cancel_r2(ident:str,req:Request):
        a,row=verify_owner(req,ident,write=True)
        if row["state"]!="uploading":core.fail(409,"완료된 영상은 영상 관리에서 삭제해 주세요.")
        try:
            s3,bucket=client()
            s3.abort_multipart_upload(Bucket=bucket,Key=row["key"],UploadId=row["upload_id"])
        except Exception as exc:storage_error(exc)
        with core.db() as db:
            db.execute("UPDATE r2_uploads SET state='cancelled' WHERE id=?", (ident,))
            core.audit(db,a["id"],ident,"r2_upload_cancel")
        return {"ok":True}

    @app.post("/api/admin/r2/videos/{video_id}/rollback")
    def rollback_r2(video_id:str,req:Request):
        a=core.require(req,admin=True,write=True)
        with core.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old=db.execute("SELECT * FROM r2_backup_sources WHERE video_id=?", (video_id,)).fetchone()
            v=db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
            if not old or not v or v["source"]!="r2":
                core.fail(409,"복원할 기존 영상 정보가 없습니다.")
            db.execute("""UPDATE videos SET source=?,external_id=?,size=?,mime=?,duration=?,
                revision=revision+1 WHERE id=?""",
                (old["old_source"],old["old_key"],old["old_size"],old["old_mime"],
                 old["old_duration"],video_id))
            db.execute("DELETE FROM r2_backup_sources WHERE video_id=?",(video_id,))
            core.audit(db,a["id"],video_id,"r2_video_rollback")
        return {"ok":True}

    def stream_r2(req,v):
        # Request is authenticated and authorized by core.watch() before this call.
        key=v["external_id"]
        if not _KEY_PATTERN.fullmatch(key or ""):
            core.fail(404,"R2 영상 정보가 올바르지 않습니다.")
        try:
            s3,bucket=client()
            method="HEAD" if req.method=="HEAD" else "GET"
            signed=s3.generate_presigned_url(
                "head_object" if method=="HEAD" else "get_object",
                Params={"Bucket":bucket,"Key":key}, ExpiresIn=120,HttpMethod=method)
        except Exception as exc:storage_error(exc)
        # The original /api/videos/{id}/stream remains the video element's URL.
        # Every new Range request gets a fresh signed R2 redirect and re-checks
        # the current membership. Signed URLs can still be shared until expiry.
        return RedirectResponse(signed,status_code=307,headers={
            "Cache-Control":"private, no-store","Referrer-Policy":"no-referrer",
        })

    def delete_r2(v):
        key=v["external_id"]
        if not _KEY_PATTERN.fullmatch(key or ""):
            core.fail(404,"R2 영상 정보가 올바르지 않습니다.")
        try:
            s3,bucket=client()
            s3.delete_object(Bucket=bucket,Key=key)
        except Exception as exc:storage_error(exc)

    def dispatch(req,v):
        if v["source"]=="r2":
            return stream_r2(req,v)
        return drive_streamer(req,v)

    core.EXTERNAL_STREAMER=dispatch
    core.R2_DELETE_VIDEO=delete_r2
