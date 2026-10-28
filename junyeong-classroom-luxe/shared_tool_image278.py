"""Rebuild the approved 2.7.8 download from the bundled 2.7.7 ZIP.
No network or hosting-provider calls. Preserve existing runtime and references.
"""
from pathlib import Path
import hashlib
import io
import json
import os
import tempfile
import threading
import zipfile

BASE_ASSET = 'yadam-image-prompts-v2.7.7.zip'
ASSET = 'yadam-image-prompts-v2.7.8.zip'
BASE_SHA256 = 'd7b536127b5c7cfe5ab6f00e40f8a206d6f198d03d0b23d3456799d21b2c5fd6'
SHA256 = '03c86af947b1a4a8974dcc5cd3c0a9d0745aadd57592af17ec421790d9a3f18d'
SIZE = 5924766
FILE_COUNT = 75
STAMP = (2026, 10, 28, 11, 51, 18)
_LOCK = threading.Lock()
_VERIFIED = {}

DOCS = ('00_모드선택_필독.md', 'CLAUDE_PROJECT_INSTRUCTIONS.md',
        'GPT_이미지생성_에셋참조_필수지침.md', 'README_처음읽기.txt', 'READ_ME.txt',
        'RULES.md', 'STYLE_SELECTION_GUIDE.md', 'guide.txt',
        '대본분석_화풍추천_사용법.txt', '수정내역_V2.7.6.txt',
        '절약형_핵심지침.md', '화풍추가_2.7.7.md')
LONG_DOCS = {'CLAUDE_PROJECT_INSTRUCTIONS.md', 'GPT_이미지생성_에셋참조_필수지침.md'}
OLD_SECTION = '## 2. 인물별 기준 에셋은 전신 2장 + 각도 4장 = 총 6장'
COMMON_SECTION = '''## 2. 인물별 기준 에셋은 6분할 통합 캐릭터시트 1장
- 등장인물 1명당 이미지 파일 1장을 제작합니다. 여섯 모습을 독립 파일로 나누지 않습니다.
- 한 장 안에 다음 여섯 모습을 모두 배치합니다.
  1. 전신 정면: 머리부터 발끝까지, 키·체형·의상·신발 확인.
  2. 전신 후면: 머리부터 발끝까지, 뒷머리·복식 뒷모습 확인.
  3. 얼굴·상반신 정면.
  4. 얼굴·상반신 왼쪽 45도.
  5. 얼굴·상반신 오른쪽 45도.
  6. 얼굴·상반신 측면 90도.
- 여섯 모습은 같은 인물이며 얼굴·나이·성별·체형·머리·의상·색상·장신구·선택 화풍을 유지합니다.
- 흰색 또는 연회색 단순 배경에서 여섯 뷰가 서로 겹치지 않도록 배치합니다.
- 인물 3명이면 캐릭터시트 3장입니다. 배경·소품은 별도이며 인물 시트 안에 끼워 넣지 않습니다.
- 파일명은 `영문ID_캐릭터시트.png`입니다. 앞에 숫자_를 붙이지 않습니다.
- 기존 STEP 1 UPLOAD 단일 초상은 Flow 인물 정의용입니다. 이것만 만들고 캐릭터시트 제작을 완료했다고 처리하지 않습니다.
- 본편 생성에는 해당 인물의 통합 캐릭터시트를 실제 참조 이미지로 전달합니다. 본편 장면은 6분할이 아닌 장면 하나로 생성합니다.

'''
LONG_SECTION = '''## 2. 인물별 기준 에셋은 6분할 통합 캐릭터시트 1장
- 장면을 만들기 전에 등장인물별로 **한 장의 통합 캐릭터시트**를 먼저 만듭니다. 독립 파일 6개로 나누지 않습니다.
- 한 장 안에는 다음 6개 모습이 모두 들어가야 합니다.
  1. 전신 정면: 머리부터 발끝까지, 키·체형·의상·신발 확인.
  2. 전신 후면: 머리부터 발끝까지, 뒷머리·복식 뒷모습 확인.
  3. 얼굴/상반신 정면.
  4. 얼굴/상반신 왼쪽 45도.
  5. 얼굴/상반신 오른쪽 45도.
  6. 얼굴/상반신 측면 90도.
- 1장 안의 6개 모습 모두 얼굴, 나이, 성별, 키·체형·몸무게 인상, 얼굴 폭, 어깨·허리 비율, 머리 모양, 복식 형태·색상, 장신구 및 선택 화풍을 동일하게 유지합니다. 날씬한 인물을 장면마다 통통하게 바꾸거나 그 반대가 되면 재생성합니다.
- 캐릭터시트는 흰색 또는 연회색의 단순한 배경 위에 각 뷰가 또렷하게 분리되어 보여야 하며, 한 장만 보아도 동일 인물임을 즉시 확인할 수 있어야 합니다.
- 인물 수만큼 캐릭터시트 파일을 만듭니다. 인물 3명이면 인물 에셋은 3장입니다. 배경·소품 에셋은 별도이며, 이를 인물 캐릭터시트 안에 끼워 넣지 않습니다.
- 기존 STEP 1 UPLOAD 단일 초상 프롬프트는 Flow 인물 정의용입니다. 이것만 만들고 인물 에셋 제작을 완료했다고 처리하지 않습니다. 실제 에셋 단계에서는 위 6분할 캐릭터시트 규칙을 따릅니다.
- 파일명은 `영문ID_캐릭터시트.png`를 사용합니다. 앞에 숫자_를 붙이지 않습니다.

'''
CHANGELOG = '''야담 이미지제작 2.7.8
인물 1명당 통합 캐릭터시트 1장에 전신 정면·후면과 얼굴·상반신 정면·왼쪽 45도·오른쪽 45도·측면 90도를 배치합니다.
파일명: 영문ID_캐릭터시트.png
본편은 단일 장면이며 캐릭터시트를 실제 참조합니다. 배경·소품은 별도입니다.
이번 수정 범위는 제작 지침이며 기존 실행 코드는 유지했습니다. 라이브 업로드 완료를 의미하지 않습니다.
'''


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    return json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')


def patched_members(base):
    if _sha(base) != BASE_SHA256:
        raise ValueError('Image tool 2.7.7 source checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(base)) as archive:
        if archive.testzip() is not None:
            raise ValueError('Image tool source ZIP is damaged')
        files = {i.filename: archive.read(i.filename) for i in archive.infolist() if not i.is_dir()}
    for name in DOCS:
        text = files[name].decode('utf-8')
        text = text.replace('인물 6장 에셋', '인물별 통합 캐릭터시트 에셋')
        if OLD_SECTION in text:
            start = text.index(OLD_SECTION)
            end = text.index('\n## 3.', start)
            text = text[:start] + (LONG_SECTION if name in LONG_DOCS else COMMON_SECTION) + text[end:]
        text = text.replace(
            '- 캐릭터시트는 참고 이미지의 구성입니다. 본편은 한 컷짜리 장면이며, 여섯 각도를 한 화면에 복제하지 않습니다.',
            '- 캐릭터시트는 인물 에셋 제작 규칙입니다. 본편은 한 컷짜리 장면이며, 본편 장면 자체를 6분할로 만들지 않습니다.' if name in LONG_DOCS else
            '- 캐릭터 에셋은 한 장의 통합 시트로 제작합니다. 본편 장면 자체를 6분할로 만들지는 않습니다.')
        text = text.replace(
            '- 전신 2장 + 각도 4장은 기존대로 여섯 개 독립 파일로 생성합니다. 실제 에셋 이미지 참조와 완성 후 생성 확인 절차를 유지합니다.',
            '- 전신 2컷 + 얼굴·상반신 4컷은 **한 장의 6분할 통합 캐릭터시트**로 생성합니다. 실제 에셋 이미지 참조와 완성 후 생성 확인 절차를 유지합니다.' if name in LONG_DOCS else
            '- 전신 2컷 + 얼굴·상반신 4컷은 한 장의 6분할 통합 캐릭터시트로 생성합니다. 실제 에셋 이미지 참조와 완성 후 생성 확인 절차를 유지합니다.')
        files[name] = text.encode('utf-8')
    share = json.loads(files['share_manifest.json'])
    share.update(version='2.7.8', title='야담 이미지 제작 2.7.8 · 통합 캐릭터시트', base_package='야담_이미지제작_2.7.8.zip')
    files['share_manifest.json'] = _json(share)
    files['수정내역_V2.7.8.txt'] = CHANGELOG.encode('utf-8')
    manifest = json.loads(files['PACKAGE_MANIFEST.json'])
    manifest['version'] = '2.7.8'
    manifest['files'] = {name: {'sha256': _sha(files[name]), 'size': len(files[name])}
                         for name in sorted(files) if name != 'PACKAGE_MANIFEST.json'}
    files['PACKAGE_MANIFEST.json'] = _json(manifest)
    return files


def rebuild_archive(base):
    files = patched_members(base)
    if len(files) != FILE_COUNT:
        raise ValueError('Image tool package member count mismatch')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            item = zipfile.ZipInfo(name, STAMP)
            item.create_system = 3
            item.external_attr = 0o600 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, files[name], compresslevel=6)
    data = output.getvalue()
    if len(data) != SIZE or _sha(data) != SHA256:
        raise ValueError('Image tool 2.7.8 differs from the approved ZIP')
    return data


def build_image278_package(package_root, data_root):
    """Atomically stage the exact approved ZIP; keep existing downloads on failure."""
    directory = Path(data_root) / 'shared-tools'
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
        fd, temporary = tempfile.mkstemp(prefix='image278-', suffix='.tmp', dir=directory)
        temp = Path(temporary)
        try:
            with os.fdopen(fd, 'wb') as stream:
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
