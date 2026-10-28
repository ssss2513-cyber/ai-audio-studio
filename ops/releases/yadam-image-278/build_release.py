"""Register the exact owner-supplied 2.7.8 ZIP; no network or hosting calls."""
import argparse
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile

BASE_SHA256 = 'd7b536127b5c7cfe5ab6f00e40f8a206d6f198d03d0b23d3456799d21b2c5fd6'
SHA256 = '66077b98a3e25dfafac1b1c233e4b0b6de07e415376f5770bb92cdbdefd14112'
SIZE = 5919916
STAMP = (2026, 10, 28, 11, 51, 40)
RECOVERY = '실행파일복구_2.7.2수정본.txt'
CHANGELOG = '''인물 1명 = 전신 정면·후면 + 얼굴 정면·왼쪽45도·오른쪽45도·측면90도, 총 6개 모습을 담은 시트 1장.
저장명: 영문ID_캐릭터시트.png
본편에 인물별 통합 시트를 실제 참조로 연결. 본편은 단일 장면.
화풍 선택, 원문30자 표시, 기존 본편 출력 코드 유지.
검사: 파일 존재·ZIP CRC·문서 충돌·복구 데이터 일치. 실제 이미지 생성 및 사이트 업로드는 미수행.
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')


def archive_bytes(members, names):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in names:
            if Path(name).name != name or name in ('.', '..'):
                raise ValueError('Unsafe release member path')
            item = zipfile.ZipInfo(name, STAMP)
            item.create_system = 3
            item.external_attr = 0o600 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, members[name], compresslevel=6)
    return output.getvalue()


def recovery_payload(text):
    result = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign):
            key = node.targets[0]
            if isinstance(key, ast.Name) and key.id in ('PAYLOAD', 'PAYLOAD_SHA256'):
                result[key.id] = ast.literal_eval(node.value)
    if set(result) != {'PAYLOAD', 'PAYLOAD_SHA256'}:
        raise ValueError('Invalid original recovery template')
    return result


def build(base_path, changes_path, output_path):
    raw = base_path.read_bytes()
    if sha(raw) != BASE_SHA256:
        raise ValueError('Unexpected base ZIP')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.testzip() is not None:
            raise ValueError('Base ZIP CRC failed')
        files = {i.filename: archive.read(i) for i in archive.infolist() if not i.is_dir()}
    changes = json.loads(changes_path.read_text(encoding='utf-8'))
    for name, indices in changes['files'].items():
        text = files[name].decode('utf-8')
        for index in indices:
            before, after = changes['pairs'][index]
            if text.count(before) != 1:
                raise ValueError('Document patch does not match: ' + name)
            text = text.replace(before, after, 1)
        files[name] = text.encode('utf-8')
    share = json.loads(files['share_manifest.json'])
    share.update(version='2.7.8', title='야담 이미지 제작 2.7.8 · 통합 캐릭터시트',
                 base_package='야담_이미지제작_2.7.8.zip', live_deployed=False)
    files['share_manifest.json'] = json_bytes(share)
    files['수정내역_2.7.8.txt'] = CHANGELOG.encode('utf-8')
    manifest = json.loads(files['PACKAGE_MANIFEST.json'])
    manifest.update(version='2.7.8', replacement_for='2.7.7')
    for name in manifest['files']:
        manifest['files'][name] = {'sha256': sha(files[name]), 'size': len(files[name])}
    files['PACKAGE_MANIFEST.json'] = json_bytes(manifest)
    template = files[RECOVERY].decode('utf-8')
    old = recovery_payload(template)
    core_names = list(manifest['files']) + ['PACKAGE_MANIFEST.json']
    payload = archive_bytes(files, core_names)
    template = template.replace(repr(old['PAYLOAD']), repr(base64.b64encode(payload).decode('ascii')))
    template = template.replace(repr(old['PAYLOAD_SHA256']), repr(sha(payload)))
    template = template.replace('2.7.7', '2.7.8').replace('yadam_tools_v277', 'yadam_tools_v278')
    files[RECOVERY] = template.encode('utf-8')
    result = archive_bytes(files, files)
    if len(result) != SIZE or sha(result) != SHA256:
        raise ValueError('Result differs from the owner ZIP: ' + sha(result))
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        if archive.testzip() is not None:
            raise ValueError('Output ZIP CRC failed')
    for name, raw in files.items():
        if name.endswith('.py'):
            ast.parse(raw.decode('utf-8-sig'), filename=name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output_path.parent, prefix='.image278-', suffix='.tmp', delete=False) as stream:
        temp = Path(stream.name)
        stream.write(result)
    try:
        temp.replace(output_path)
    finally:
        temp.unlink(missing_ok=True)
    (output_path.parent / '야담_프로젝트지침_2.7.8.txt').write_bytes(files['CLAUDE_PROJECT_INSTRUCTIONS.md'])
    report = dict(version='2.7.8', status='PASS', size=SIZE, sha256=SHA256, file_count=len(files),
                  checks=['exact owner ZIP bytes', 'ZIP CRC', 'Python syntax', 'synchronized recovery payload'],
                  scope='Release file verification only; no generated-image or live-deployment verification')
    output_path.with_suffix('.json').write_bytes(json_bytes(report) + b'\n')
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--changes', type=Path, default=Path(__file__).with_name('changes.json'))
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    print(json.dumps(build(args.base, args.changes, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
