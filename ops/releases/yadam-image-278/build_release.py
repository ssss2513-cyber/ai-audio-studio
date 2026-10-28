"""Reconstruct the exact owner-supplied 2.7.8 ZIP from the committed 2.7.7.
No credentials, network, generated story changes, or hosting-provider actions.
"""
import argparse
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile
import zlib


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive_bytes(members, entries, level=6):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=level) as archive:
        for name, stamp, system, attributes, compression, extra, comment in entries:
            if Path(name).name != name or name in ('.', '..'):
                raise ValueError('Unsafe release member path')
            item = zipfile.ZipInfo(name, tuple(stamp))
            item.create_system = system
            item.external_attr = attributes
            item.compress_type = compression
            item.extra = bytes.fromhex(extra)
            item.comment = bytes.fromhex(comment)
            archive.writestr(item, members[name], compresslevel=level)
    return stream.getvalue()


def build(base_path, recipe_path, output_path):
    recipe = json.loads(zlib.decompress(base64.b64decode(recipe_path.read_text().strip(), validate=True)))
    base = base_path.read_bytes()
    if sha(base) != recipe['base_sha256']:
        raise ValueError('Base archive checksum differs from approved 2.7.7')
    with zipfile.ZipFile(io.BytesIO(base)) as archive:
        if archive.testzip() is not None:
            raise ValueError('Base archive CRC failed')
        files = {i.filename: archive.read(i) for i in archive.infolist() if not i.is_dir()}
    for name, change in recipe['changes'].items():
        if 'content' in change:
            if name in files:
                raise ValueError('New release member already exists')
            files[name] = change['content'].encode('utf-8')
            continue
        if sha(files[name]) != change['base_sha256']:
            raise ValueError('Source member hash mismatch: ' + name)
        text = files[name].decode('utf-8')
        for start, end, replacement in reversed(change['ops']):
            text = text[:start] + replacement + text[end:]
        files[name] = text.encode('utf-8')
    recovery = recipe['recovery']
    inner = {name: files[name] for name in recovery['files']}
    inner['PACKAGE_MANIFEST.json'] = recovery['manifest'].encode('utf-8')
    payload = archive_bytes(inner, recovery['entries'])
    text = recovery['template'].replace('__PAYLOAD_SHA256__', repr(sha(payload)))
    text = text.replace('__PAYLOAD__', repr(base64.b64encode(payload).decode('ascii')))
    files[recovery['name']] = text.encode('utf-8')
    result = archive_bytes(files, recipe['archive_entries'])
    if len(result) != recipe['size'] or sha(result) != recipe['package_sha256']:
        raise ValueError('Reconstructed ZIP does not exactly match the owner-supplied download: ' + sha(result))
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        if archive.testzip() is not None:
            raise ValueError('Release archive CRC failed')
    manifest = json.loads(files['PACKAGE_MANIFEST.json'])
    for name, item in manifest['files'].items():
        if sha(files[name]) != item['sha256'] or len(files[name]) != item['size']:
            raise ValueError('Package manifest mismatch: ' + name)
    for name, raw in files.items():
        if name.endswith('.py'):
            ast.parse(raw.decode('utf-8-sig'), filename=name)
    instructions = files['CLAUDE_PROJECT_INSTRUCTIONS.md'].decode('utf-8')
    if '영문ID_캐릭터시트.png' not in instructions or '6분할 통합 캐릭터시트 1장' not in instructions:
        raise ValueError('Unified character-sheet instructions missing')
    if '다음 6개의 독립 이미지 파일을 먼저 만듭니다' in instructions:
        raise ValueError('Retired six-file instructions are active')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output_path.parent, prefix='.image278-', suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(result)
    try:
        temporary.replace(output_path)
    finally:
        temporary.unlink(missing_ok=True)
    instruction_path = output_path.parent / '야담_프로젝트지침_2.7.8.txt'
    instruction_path.write_bytes(files['CLAUDE_PROJECT_INSTRUCTIONS.md'])
    report = {'version': '2.7.8', 'status': 'PASS', 'size': len(result),
              'sha256': sha(result), 'file_count': len(files),
              'checks': ['exact owner ZIP bytes', 'ZIP CRC', 'package manifest', 'Python syntax', 'unified character-sheet instructions'],
              'scope': 'Release-file integrity only; no generated-image or live-deployment verification'}
    output_path.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--recipe', type=Path, default=Path(__file__).with_name('recipe.b64'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.recipe, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
