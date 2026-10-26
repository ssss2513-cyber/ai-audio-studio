"""Non-destructive V13.11 + type-4 release assembler for Maison Lumiel.

The owner-supplied V13.11 ZIP is never modified or overwritten. Only the
new story-mode documents are prepended to the two project-facing instructions
and added to the replacement member-download archive.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import zipfile
from pathlib import Path

SOURCE_ZIP = 'yadam-generator-v13.11.zip'
SOURCE_SHA256 = '92a034d0089d2cd3999b99726ae257004748282a96f8c2ae971ed1431c79e9a5'
OUTPUT_ZIP = 'yadam-generator-v13.11.1-type4.zip'
EXTRA_FOLDER = 'yadam-type4'
MODE_FILE = '00_유형선택_4번_우선규칙.txt'
GUIDE_FILE = '07_유형4_밤마실야담_작법_V1.0.md'
NOTICE = ('[V13.11.1 · 4번 밤마실야담 작법 추가 / 2026-10-26]\n'
          '기존 V13.11 기능을 유지하고 4. 밤마실야담 작법을 신규 선택지로 제공합니다. '
          '상세 규칙은 07_유형4_밤마실야담_작법_V1.0.md를 따릅니다.\n\n')
_LOCK = threading.Lock()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_archive(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path, 'r') as z:
            names = z.namelist()
            return (z.testzip() is None
                    and len(names) == len(set(names))
                    and GUIDE_FILE in names
                    and MODE_FILE in names)
    except (OSError, zipfile.BadZipFile):
        return False


def build_type4_package(package_root: Path, data_root: Path) -> Path:
    package_root = Path(package_root)
    data_root = Path(data_root)
    original = package_root / SOURCE_ZIP
    rules_dir = package_root / EXTRA_FOLDER
    mode = (rules_dir / MODE_FILE).read_bytes()
    guide = (rules_dir / GUIDE_FILE).read_bytes()
    source = original.read_bytes()
    if _hash(source) != SOURCE_SHA256:
        raise ValueError('Original V13.11 ZIP differs from the checked release. No replacement attempted.')
    expected = _hash(source + mode + guide)
    output_dir = data_root / 'shared-tools'
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / OUTPUT_ZIP
    stamp = output_dir / (OUTPUT_ZIP + '.source.sha256')
    with _LOCK:
        if target.is_file() and stamp.is_file() and stamp.read_text().strip() == expected and _check_archive(target):
            return target
        fd, temporary_name = tempfile.mkstemp(prefix='yadam-type4-', suffix='.zip.tmp', dir=output_dir)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(original, 'r') as src, zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as dst:
                if src.testzip() is not None:
                    raise ValueError('Original V13.11 ZIP has invalid member CRC.')
                source_names = set(src.namelist())
                for member in src.infolist():
                    if member.is_dir():
                        dst.writestr(member, b'')
                        continue
                    content = src.read(member.filename)
                    basename = member.filename.rsplit('/', 1)[-1]
                    if basename.startswith('00_프로젝트지침란에_복사할내용') or basename.startswith('01_야담_대본_프롬프트'):
                        header = (mode.decode('utf-8').rstrip() + '\n\n').encode('utf-8')
                        if not content.startswith(b'[\xec\x9c\xa0\xed\x98\x95 4 '):
                            content = header + content
                    elif basename.startswith('00_먼저읽기_설치방법'):
                        if not content.startswith(NOTICE.encode('utf-8')):
                            content = NOTICE.encode('utf-8') + content
                    dst.writestr(member, content)
                if MODE_FILE not in source_names:
                    dst.writestr(MODE_FILE, mode, compress_type=zipfile.ZIP_DEFLATED)
                if GUIDE_FILE not in source_names:
                    dst.writestr(GUIDE_FILE, guide, compress_type=zipfile.ZIP_DEFLATED)
            if not _check_archive(temporary):
                raise ValueError('Generated V13.11.1 ZIP did not pass integrity checks.')
            temporary.replace(target)
            stamp_tmp = stamp.with_suffix(stamp.suffix + '.tmp')
            stamp_tmp.write_text(expected, encoding='utf-8')
            stamp_tmp.replace(stamp)
            return target
        finally:
            temporary.unlink(missing_ok=True)