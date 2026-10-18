#!/usr/bin/env python3
"""Convert an existing Drive-linked video record to a verified local file.

This does NOT download from Google Drive or modify original Drive files. The
original member/course metadata stays in the migrated private SQLite database.
Use only on a separately provisioned OCI instance, never against live Railway.
Run inside the app container after copying a rights-cleared ORIGINAL video to
/data/staging/, which must be on the OCI block-volume mount.

Example:
docker compose exec app python /opt/tools/import_drive_video.py \
  --id 0123456789abcdef0123456789abcdef --file /data/staging/lecture01.mp4
docker compose exec app python /opt/tools/import_drive_video.py \
  --id 0123456789abcdef0123456789abcdef --file /data/staging/lecture01.mp4 --apply
"""
from __future__ import annotations
import argparse
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/app")
from app import DATA, db


def main():
    parser = argparse.ArgumentParser(description="Private OCI video media importer")
    parser.add_argument("--id", required=True, help="Existing internal 32-character video ID")
    parser.add_argument("--file", required=True, help="Path to original MP4/WebM in /data/staging")
    parser.add_argument("--apply", action="store_true", help="Perform the change after dry run")
    args = parser.parse_args()
    if not re.fullmatch("[a-f0-9]{32}", args.id):
        parser.error("Video ID must be a 32-character lowercase hexadecimal ID")
    staged = (DATA / "staging").resolve()
    source = Path(args.file).resolve(strict=True)
    if staged not in source.parents or not source.is_file() or source.is_symlink():
        parser.error("Source must be a regular file inside /data/staging")
    with db() as c:
        row = c.execute("SELECT * FROM videos WHERE id=?", (args.id,)).fetchone()
    if row is None:
        parser.error("Video record missing. Restore the matching private database first.")
    if row["source"] != "drive":
        parser.error("Video is not Drive-backed; refusing to overwrite local media")
    if row["state"] in ("trash", "purging"):
        parser.error("Video is in the trash; restore it in the admin UI before importing")
    size = source.stat().st_size
    if size != row["size"]:
        parser.error(f"Video size does not match the existing record ({size} vs {row['size']}). Verify the right ORIGINAL file.")
    if source.suffix.lower() not in (".mp4", ".webm"):
        parser.error("Only MP4 and WebM original files are supported")
    with source.open("rb") as f:
        header = f.read(16)
    if row["mime"] == "video/mp4" and header[4:8] != b"ftyp":
        parser.error("Not a valid MP4 container header")
    if row["mime"] == "video/webm" and not header.startswith(b"\x1a\x45\xdf\xa3"):
        parser.error("Not a valid WebM container header")
    destination = DATA / "media" / args.id
    if destination.exists():
        parser.error("Target already exists: refusing to overwrite any media")
    print(f"Matched: ID {args.id}; title {row['title']}; {size:,} bytes; existing source {row['source']}")
    if not args.apply:
        print("DRY RUN ONLY. Rerun with --apply to copy video and update existing SQLite record.")
        return
    if shutil.disk_usage(DATA).free < size + 64 * 1024 * 1024:
        parser.error("Not enough free OCI block disk space to copy this file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(args.id + "." + uuid.uuid4().hex + ".import")
    try:
        with source.open("rb") as src, temporary.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        if temporary.stat().st_size != size:
            parser.error("Incomplete file copy")
        os.replace(temporary, destination)
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute("SELECT source,size FROM videos WHERE id=?", (args.id,)).fetchone()
            if not current or current["source"] != "drive" or current["size"] != size:
                raise RuntimeError("Video record changed while importing; database was not switched")
            c.execute("UPDATE videos SET source='local', revision=revision+1 WHERE id=?", (args.id,))
            c.execute("INSERT INTO audits(actor,subject,action,reason,created) VALUES(?,?,?,?,strftime('%s','now'))",
                      ("local-operator", args.id, "oci_video_import", "Original private Drive ID retained for rollback"))
        print("SUCCESS: local video is now served from OCI disk. The Drive original and staged copy were not deleted.")
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
