"""Build the approved image-tool 2.7.8 ZIP from the bundled 2.7.7 release.

No network, hosting-provider calls, credentials, or edits to existing releases.
A checked text patch retains every unchanged executable and reference image.
The final archive must match the exact ZIP already supplied to the owner.
"""
from pathlib import Path
import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import zipfile
import zlib

BASE_ASSET = "yadam-image-prompts-v2.7.7.zip"
ASSET = "yadam-image-prompts-v2.7.8.zip"
BASE_SHA256 = "d7b536127b5c7cfe5ab6f00e40f8a206d6f198d03d0b23d3456799d21b2c5fd6"
SHA256 = "03c86af947b1a4a8974dcc5cd3c0a9d0745aadd57592af17ec421790d9a3f18d"
SIZE = 5924766
FILE_COUNT = 75
STAMP = (2026, 10, 28, 11, 51, 18)
_LOCK = threading.Lock()
_VERIFIED = {}
_PATCH = "eNrtXG1zG9d5/Ss7zDcX1tz3F3/T2IxHrWxr9JJpJsogd++Lo9aWNJLsNslkRrKgDGMytRKLNh2TKt3ItuzKDSxRFtXQ/eCf0o/cxX/oubsgCZEAYbtJm2SgGYkisNh79nk55zwXwP5sjpBu9end6u3Vurc+uL7WHSz3qrduHHk1zD3zgx+QDu3Mfaf4Hjuij6iifrha/7aP44r6i1vb/atF/fHV+vHd4r+v3iqeP3G6qNc2qt9v4sGivr5W9z4vBu8t12ubXz3CP9W9repBrxj84uFg+ZOi/s+b1UefDXr805Z"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def rebuild_archive(base):
    """Apply bounded line edits only to a checksum-verified base ZIP."""
    if _sha(base) != BASE_SHA256:
        raise ValueError("Image tool 2.7.7 source checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(base)) as archive:
        if archive.testzip() is not None:
            raise ValueError("Image tool source ZIP is damaged")
        files = {i.filename: archive.read(i.filename) for i in archive.infolist() if not i.is_dir()}
    # Release patch is stored separately for a reviewable, integrity-checked update.
    patch_path = Path(__file__).with_name("image278_patch.json")
    patch_bytes = patch_path.read_bytes()
    patch = json.loads(patch_bytes)
    edits = patch["edits"]
    for name, operations in edits.items():
        if Path(name).name != name:
            raise ValueError("Invalid package member name")
        lines = files.get(name, b"").decode("utf-8").splitlines(keepends=True)
        previous = len(lines) + 1
        for start, end, replacement in reversed(operations):
            if not 0 <= start <= end <= len(lines) or end > previous:
                raise ValueError("Invalid package patch range")
            lines[start:end] = replacement.splitlines(keepends=True)
            previous = start
        files[name] = "".join(lines).encode("utf-8")
    if len(files) != FILE_COUNT:
        raise ValueError("Image tool package member count mismatch")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            item = zipfile.ZipInfo(name, STAMP)
            item.create_system = 3
            item.external_attr = 0o600 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, files[name], compresslevel=6)
    data = output.getvalue()
    if len(data) != SIZE or _sha(data) != SHA256:
        raise ValueError("Image tool 2.7.8 differs from the approved ZIP")
    return data


def build_image278_package(package_root, data_root):
    """Atomically stage one verified ZIP; never overwrite a valid file with a failed build."""
    directory = Path(data_root) / "shared-tools"
    path = directory / ASSET
    with _LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            stat = path.stat()
            fingerprint = (stat.st_size, stat.st_mtime_ns)
            if _VERIFIED.get(str(path)) == fingerprint:
                return path
            if stat.st_size == SIZE and _sha(path.read_bytes()) == SHA256:
                _VERIFIED[str(path)] = fingerprint
                return path
        data = rebuild_archive((Path(package_root) / BASE_ASSET).read_bytes())
        fd, temporary = tempfile.mkstemp(prefix="image278-", suffix=".tmp", dir=directory)
        temp = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temp.chmod(0o600)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        stat = path.stat()
        _VERIFIED[str(path)] = (stat.st_size, stat.st_mtime_ns)
        return path
