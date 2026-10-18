"""Switch an existing authorized lecture video to local Oracle block storage.

Only run this script after the encrypted member SQLite DB and its original
secret.key have been securely migrated. It never changes memberships, video
IDs, or playback progress. Dry run is the default.
"""
from __future__ import annotations
import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

DATA = Path(os.environ.get("CLASSROOM_DATA_DIR", "/data")).resolve()
DB = DATA / "classroom.sqlite3"
MEDIA = DATA / "media"
BUF = 8 * 1024 * 1024


def abort(message):
    raise SystemExit("중단: " + message)


def connect():
    if not DB.is_file():
        abort("기존 회원/영상 DB를 찾지 못했습니다. 먼저 Railway 데이터 이전을 완료하세요.")
    c = sqlite3.connect("file:" + str(DB) + "?mode=rw", uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def detect_media(path, mime):
    with path.open("rb") as f:
        header = f.read(16)
    if mime == "video/mp4" and header[4:8] != b"ftyp":
        abort("선택한 파일이 MP4 형식이 아닙니다.")
    if mime == "video/webm" and not header.startswith(b"\x1a\x45\xdf\xa3"):
        abort("선택한 파일이 WebM 형식이 아닙니다.")
    if mime not in ("video/mp4", "video/webm"):
        abort("MP4/WebM 파일만 지원합니다.")


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(BUF)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def list_videos():
    with connect() as c:
        for row in c.execute("SELECT id,source,size,state FROM videos ORDER BY created"):
            # Deliberately do not print member identities or secret video titles.
            print(row["id"], row["source"], row["size"], row["state"])


def attach(video_id, path, apply):
    if not re.fullmatch(r"[0-9a-f]{32}", video_id):
        abort("32자리 영상 식별자를 확인해 주세요.")
    source = Path(path).resolve(strict=True)
    if not source.is_file():
        abort("선택한 경로가 파일이 아닙니다.")
    if source.is_symlink() or source.stat().st_size <= 0:
        abort("심볼릭 링크 또는 비어 있는 파일은 지원하지 않습니다.")
    with connect() as c:
        video = c.execute(
            "SELECT id,source,size,mime,state FROM videos WHERE id=?",
            (video_id,),
        ).fetchone()
        if video is None:
            abort("기존 영상 목록에서 해당 ID를 찾지 못했습니다.")
        expected = int(video["size"] or 0)
        mime = video["mime"]
        if expected <= 0:
            abort("기존 영상 크기 정보가 없습니다. 원본과 메타데이터를 확인하세요.")
        if video["source"] not in ("drive", "local"):
            abort("이 도구는 Google Drive 또는 기존 로컬 영상을 대상으로 합니다.")
    if source.stat().st_size != expected:
        abort("기존 영상 DB 크기와 선택한 파일 용량이 다릅니다. 잘못된 강의를 지정했을 수 있습니다.")
    detect_media(source, mime)
    destination = MEDIA / video_id
    if MEDIA.is_symlink() or not str(destination.resolve()).startswith(str(DATA) + os.sep):
        abort("미디어 저장 위치가 안전하지 않습니다.")
    if destination.exists() and source != destination:
        abort("대상 영상 파일이 이미 존재합니다. 덮어쓰지 않습니다.")
    print("영상 ID:", video_id)
    print("기존 상태:", video["source"], video["state"])
    print("검증 크기:", expected, "bytes")
    print("로컬 대상:", destination)
    if not apply:
        print("검증만 완료했습니다. 실제 변경하려면 --apply를 추가하세요.")
        return
    MEDIA.mkdir(parents=True, exist_ok=True, mode=0o700)
    if source != destination:
        if not os.access(MEDIA, os.W_OK):
            abort("영상 저장 디렉터리 쓰기 권한이 없습니다.")
        temp = MEDIA / (video_id + ".part")
        if temp.exists():
            abort("미완료 복사 파일이 있습니다. 관리자에게 먼저 확인받으세요.")
        try:
            with source.open("rb") as src, temp.open("xb") as dst:
                while True:
                    block = src.read(BUF)
                    if not block:
                        break
                    dst.write(block)
                dst.flush()
                os.fsync(dst.fileno())
            temp.chmod(0o600)
            if temp.stat().st_size != expected or sha256_file(source) != sha256_file(temp):
                abort("복사본의 크기 또는 SHA256 검증 실패")
            os.replace(temp, destination)
        finally:
            temp.unlink(missing_ok=True)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        present = c.execute(
            "SELECT source,size FROM videos WHERE id=?", (video_id,)
        ).fetchone()
        if present is None or int(present["size"]) != expected:
            abort("복사 중 영상 DB가 변경되었습니다. 저장소 상태를 다시 확인하세요.")
        c.execute(
            "UPDATE videos SET source='local',revision=revision+1 WHERE id=?",
            (video_id,),
        )
        c.commit()
    print("완료: 회원·승인·영상 ID·시청 기록은 변경하지 않고 영상 저장 위치만 local로 전환했습니다.")


def main():
    p = argparse.ArgumentParser(description="기존 강의 파일의 Oracle 로컬 저장소 이전")
    sp = p.add_subparsers(dest="command", required=True)
    sp.add_parser("list", help="영상 ID·기존 저장소·용량만 목록으로 표시")
    a = sp.add_parser("attach", help="기존 영상 ID에 동일한 MP4/WebM 파일 연결")
    a.add_argument("--id", required=True)
    a.add_argument("--file", required=True)
    a.add_argument("--apply", action="store_true", help="실제 파일 복사 및 DB 변경")
    args = p.parse_args()
    if args.command == "list":
        list_videos()
    else:
        attach(args.id, args.file, args.apply)


if __name__ == "__main__":
    main()
