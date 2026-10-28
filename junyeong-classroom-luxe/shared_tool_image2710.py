"""Offline 2.7.10 image-tool upgrade. Preserves other tools and hosting settings."""
from pathlib import Path
import ast
import base64
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import zipfile
import zlib

ASSET = 'yadam-image-prompts-v2.7.10.zip'
VERSION = '2.7.10'
BASE_SHA256 = '2955e6b342fb62886d1f9a3d0724b5b91ccecbec2286987aa30442fcf710e240'
STAMP = (2026, 10, 28, 12, 0, 0)
_LOCK = threading.Lock()

def _sha(raw): return hashlib.sha256(raw).hexdigest()
def _json(value): return json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')

def _zip(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name in sorted(files):
            if Path(name).name!=name: raise ValueError('Unsafe member path')
            info=zipfile.ZipInfo(name,STAMP);info.create_system=3;info.external_attr=0o600<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,files[name],compresslevel=6)
    return out.getvalue()

def rebuild_archive(base, policy, updates):
    if _sha(base)!=BASE_SHA256: raise ValueError('Approved 2.7.9 base checksum mismatch')
    # Updates are UTF-8 text deltas, compressed only to keep the checked-in overlay compact.
    if not updates.lstrip().startswith(b'{'):
        updates=zlib.decompress(base64.b64decode(updates.strip(),validate=True))
    recipe=json.loads(updates.decode('utf-8-sig'))
    if recipe['version']!=VERSION or recipe['base_sha256']!=BASE_SHA256: raise ValueError('Unexpected update recipe')
    policy=policy.decode('utf-8-sig')
    with zipfile.ZipFile(io.BytesIO(base)) as z:
        if z.testzip():raise ValueError('Base ZIP CRC failed')
        files={i.filename:z.read(i) for i in z.infolist() if not i.is_dir()}
    for name,change in recipe['changes'].items():
        if _sha(files[name])!=change['base_sha256']:raise ValueError('Source changed: '+name)
        text=files[name].decode('utf-8-sig')
        for start,end,replacement in reversed(change['ops']):text=text[:start]+replacement+text[end:]
        files[name]=text.encode('utf-8')
    for name,change in recipe['policy_bindings'].items():
        if _sha(files[name])!=change['base_sha256']:raise ValueError('Policy source changed: '+name)
        files[name]=(policy+change['suffix']).encode('utf-8')
    for name,text in recipe['new_files'].items():
        if name in files:raise ValueError('New member exists: '+name)
        files[name]=text.encode('utf-8')
    manifest=json.loads(files['PACKAGE_MANIFEST.json'])
    manifest.update(version=VERSION,replacement_for='2.7.9')
    active=set(manifest['files'])
    manifest['files']={n:{'size':len(files[n]),'sha256':_sha(files[n])} for n in sorted(active)}
    files['PACKAGE_MANIFEST.json']=_json(manifest)
    recovery_name='실행파일복구_2.7.2수정본.txt'
    recovery=files[recovery_name].decode('utf-8-sig')
    recovery=recovery.replace('2.7.9',VERSION).replace('yadam_tools_v279','yadam_tools_v2710')
    payload=_zip({n:files[n] for n in sorted(active)+['PACKAGE_MANIFEST.json']})
    recovery=re.sub(r'^PAYLOAD_SHA256\s*=.*$',lambda m:'PAYLOAD_SHA256 = '+repr(_sha(payload)),recovery,flags=re.M)
    recovery=re.sub(r'^PAYLOAD\s*=.*$',lambda m:'PAYLOAD = '+repr(base64.b64encode(payload).decode('ascii')),recovery,flags=re.M)
    files[recovery_name]=recovery.encode('utf-8')
    for name,raw in files.items():
        if name.endswith('.py'):ast.parse(raw.decode('utf-8-sig'),filename=name)
    result=_zip(files)
    with zipfile.ZipFile(io.BytesIO(result)) as z:
        if z.testzip():raise ValueError('Output ZIP CRC failed')
    return result

def build_image2710_package(package_root,data_root):
    # Reuse the existing local 2.7.9 builder; no network/provider operations.
    from shared_tool_image279 import build_image279_package
    package_root=Path(package_root);directory=Path(data_root)/'shared-tools';path=directory/ASSET
    with _LOCK:
        directory.mkdir(parents=True,exist_ok=True)
        base=build_image279_package(package_root,data_root).read_bytes()
        data=rebuild_archive(base,(package_root/'image2710-policy.md').read_bytes(),
                             (package_root/'image2710-updates.json.zlib.b64').read_bytes())
        if path.is_file() and _sha(path.read_bytes())==_sha(data):return path
        fd,name=tempfile.mkstemp(prefix='image2710-',suffix='.tmp',dir=directory);temp=Path(name)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            temp.chmod(0o600);temp.replace(path)
        finally:temp.unlink(missing_ok=True)
        return path
