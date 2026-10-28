"""Offline image-tool 2.7.10 builder; no network or hosting-provider operations."""
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

ASSET = 'yadam-image-prompts-v2.7.10.zip'
VERSION = '2.7.10'
BASE_SHA256 = '2955e6b342fb62886d1f9a3d0724b5b91ccecbec2286987aa30442fcf710e240'
STAMP = (2026, 10, 28, 12, 0, 0)
_LOCK = threading.Lock()

INSTALL = '''\n\n## 실행파일 준비
첨부 ZIP을 새 도구 폴더에 풀고 `python setup_check.py`를 실행합니다. 원본 대본은 `--bind-script`로 별도 보존합니다. 대본 분석 → 사용자 화풍 선택 → `--bind-style` → 계획·배역·청크 검토 → `--bind-values` → `--final` 순서입니다. 에셋·본편 그림 생성은 프롬프트 완료 뒤의 명시적 승인 이후입니다.
'''
SAVE_FUNCTIONS = '''def asset_filename(key,kind):
    suffix={'character':'캐릭터시트','background':'배경','prop':'소품'}
    if not isinstance(key,str) or not ID_RE.fullmatch(key) or kind not in suffix:
        raise ValueError('ASSET: valid registered ID and kind required for saving')
    return key+'_'+suffix[kind]+'.png'


def save_plan(state,body):
    require_plan(state,body)
    from image_filenames import image_filename
    used=set()
    assets=[{'id':a['id'],'kind':a['kind'],'filename':a['filename'],
             'relative_path':'assets/'+a['filename']} for a in state['assets']]
    scenes=[]
    for n,c in enumerate(state['chapters'],1):
        original=body[c['start_idx']];key=original[:30]
        filename=image_filename(key,used)
        scenes.append({'n':n,'start_idx':c['start_idx'],'end_idx':c['end_idx'],
            'matching_key':key,'source_first_sentence':original,'filename':filename,
            'relative_path':'scenes/'+filename,'asset_ids':list(c['asset_ids'])})
    return {'version':VERSION,'image_status':'NOT_GENERATED','output_root':'output',
            'asset_count':len(assets),'scene_count':len(scenes),'assets':assets,'scenes':scenes,
            'scope':'Save plan only; actual image files are created after explicit generation approval'}


'''
GENERATION_QUESTION = '프롬프트가 완성되었습니다. 에셋 이미지를 먼저 생성한 뒤, 그 에셋을 참조해 본편 이미지를 생성할까요?'

def _sha(raw): return hashlib.sha256(raw).hexdigest()
def _json(value): return json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')

def _zip(files):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name in sorted(files):
            if Path(name).name!=name:raise ValueError('Unsafe archive member')
            info=zipfile.ZipInfo(name,STAMP);info.create_system=3;info.external_attr=0o600<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,files[name],compresslevel=6)
    return stream.getvalue()

def patched_members(base,policy):
    if _sha(base)!=BASE_SHA256:raise ValueError('Approved 2.7.9 ZIP checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(base)) as z:
        if z.testzip():raise ValueError('Base ZIP CRC failed')
        files={i.filename:z.read(i) for i in z.infolist() if not i.is_dir()}
    policy=policy.decode('utf-8-sig')
    def patch(name,before,after):
        text=files[name].decode('utf-8-sig')
        if text.count(before)!=1:raise ValueError('Source patch mismatch: '+name+' / '+before[:70])
        files[name]=text.replace(before,after,1).encode('utf-8')
    patch('recommend.py',"    workflow_guard.require_mode(a.get('workflow'))\n",'')
    patch('대본분석_화풍추천.py',"            workflow_guard.require_mode(workflow)\n",'')
    patch('workflow_guard.py',"VERSION = '2.7.9'","VERSION = '2.7.10'")
    patch('workflow_guard.py',
          "STYLE_QUESTION = '화풍은 어떤 방식으로 정할까요?\\n1. 추천 화풍\\n2. 참조 이미지\\n3. 쓰고 싶은 화풍 직접 입력'",
          "STYLE_QUESTION = '대본 분석이 끝났습니다. 화풍은 어떤 방식으로 정할까요?\\n1. 대본에 어울리는 추천 화풍\\n2. 원하는 화풍 직접 입력\\n3. 참조 이미지의 화풍 사용'")
    patch('workflow_guard.py',"GENERATION_QUESTION = '에셋 이미지 생성 후 본편 이미지 만들까요?'",
          'GENERATION_QUESTION = '+repr(GENERATION_QUESTION))
    patch('workflow_guard.py',"    anchors = state.get('ingredients', []) + state.get('subanchors', [])",
          "    if {a.get('kind') for a in registry.values()} != {'character','background','prop'}:\n"
          "        errors.append('ASSET: all three kinds character/background/prop are required within the total of 18')\n"
          "    for key, asset in registry.items():\n"
          "        if asset.get('kind') in ('background','prop') and asset.get('filename') != asset_filename(key,asset['kind']):\n"
          "            errors.append('ASSET '+key+': filename must match registered ID and kind')\n"
          "    anchors = state.get('ingredients', []) + state.get('subanchors', [])")
    patch('workflow_guard.py','def bundle_hash(root, state, body):',SAVE_FUNCTIONS+'def bundle_hash(root, state, body):')
    patch('workflow_guard.py',
          r"    if re.search(r'거절|하지\s*마|프롬프트만|안\s*돼|아니|\bno\b',user_message,re.I):",
          r"    if re.search(r'거절|하지\s*마|프롬프트만|안\s*돼|아니|취소|나중에|보류|규칙|지침|파일\s*(수정|적용)|\bno\b',user_message,re.I):")
    patch('gates.py',"    workflow_guard.prepare_question(root,state,body)",
          "    save_json(root/'output'/'image-save-plan.json',workflow_guard.save_plan(state,body))\n"
          "    workflow_guard.prepare_question(root,state,body)")
    for name in ('gates.py','recommend.py','대본분석_화풍추천.py','setup_check.py'):
        text=files[name].decode('utf-8-sig')
        files[name]=text.replace("VERSION='2.7.9'","VERSION='2.7.10'").replace("VERSION = '2.7.9'","VERSION = '2.7.10'").encode('utf-8')
    for name in ('WORKFLOW_RULES.md','GPT_이미지생성_에셋참조_필수지침.md'):
        files[name]=policy.encode('utf-8')
    for name in ('CLAUDE_PROJECT_INSTRUCTIONS.md','guide.txt','대본분석_화풍추천_사용법.txt'):
        files[name]=(policy+INSTALL).encode('utf-8')
    readme='야담 이미지제작 2.7.10\n대본 분석 후 화풍 선택 → 에셋18·본편 프롬프트 → 승인 질문 → 에셋 생성·검수 → 실제 참조 본편 생성.\n인물 6뷰는 시트 1장. 에셋은 ID별 저장, 본편은 원문30자 숫자접두 없는 이름.\n상세 규칙은 WORKFLOW_RULES.md, 저장 계획은 output/image-save-plan.json.\n'+INSTALL
    for name in ('README_처음읽기.txt','READ_ME.txt'):files[name]=readme.encode('utf-8')
    files['00_모드선택_필독.md']=('# 2.7.10 시작 순서\n대본 전체를 먼저 분석한 뒤 추천/직접 입력/참조 이미지 중 화풍 선택을 받습니다. 선택 전 자동 확정 금지. 기본/절약형은 기존 선택을 유지하고 미선택이면 선택을 받습니다. 에셋은 세 종류 합계18장, 생성은 프롬프트 완료 후 별도 승인입니다. WORKFLOW_RULES.md를 따릅니다.\n').encode('utf-8')
    files['절약형_핵심지침.md']=('# 2.7.10 절약형\nWORKFLOW_RULES.md 순서를 따릅니다. 대본 전체 분석 후 화풍 선택을 받습니다. 반복 분석만 줄이고 원문/상세화풍/에셋18개/컷별ID/승인/실제참조검사는 생략하지 않습니다. 인물당 통합시트 한 장. 대본목록은 첫30자이며 실제 본편 저장명은 숫자접두가 없습니다.\n').encode('utf-8')
    files['STYLE_SELECTION_GUIDE.md']=('# 2.7.10 화풍 선택\n대본 분석을 먼저 하고 1)추천 2)원하는 화풍 직접입력 3)참조이미지 중 선택을 받습니다. 추천이면 후보를 선택받고, 참조는 실제이미지를 읽어 다섯상세항목을 작성합니다. 이미 지정된 화풍/이미지는 재질문하지 않습니다. STYLE_SELECTION.json이 SELECTED이기 전에 최종프롬프트를 제작하지 않습니다. 화풍선택은 이미지 생성 승인이 아닙니다. 상세화풍 원문은 매에셋/장면 전체 유지합니다.\n').encode('utf-8')
    for name in ('RULES.md','ANALYSIS_SCHEMA.md','화풍원문_필독.md','대본원문_필독.md'):
        text=files[name].decode('utf-8-sig').replace('# 2.7.9','# 2.7.10')
        text=text.replace('대본 접수 즉시 화풍 방식을 먼저 선택받고 WORKFLOW_RULES.md 순서로 진행합니다.',
                          '대본 전체 분석 후 화풍 방식을 선택받고 WORKFLOW_RULES.md 순서로 진행합니다.')
        files[name]=text.encode('utf-8')
    template=json.loads(files['WORKFLOW_TEMPLATE.json']);template['version']=VERSION
    template['note']='대본 전체 분석 후 사용자의 실제 선택으로 작성. 세 종류 합계18개 에셋이 필요하며 이 빈 템플릿은 통과하지 않음.'
    files['WORKFLOW_TEMPLATE.json']=_json(template)
    share=json.loads(files['share_manifest.json'])
    share.update(version=VERSION,title='야담 이미지 제작 2.7.10 · 대본 분석 후 화풍 선택 · 에셋18',
                 base_package='야담_이미지제작_2.7.10.zip',replacement_for='2.7.9',
                 style_selection='ASK_AFTER_SCRIPT_ANALYSIS',generation_question=GENERATION_QUESTION,
                 image_storage={'assets':'output/assets','scenes':'output/scenes','mapping':'output/image-save-plan.json'},
                 live_deployed=False)
    files['share_manifest.json']=_json(share)
    files['수정내역_2.7.10.txt']=('2.7.10: 대본 분석 후 화풍 선택. 세 종류 합계18개 에셋. 프롬프트 후 생성 승인. 통합시트/실제참조/ID일치 유지. 에셋ID별 저장명과 본편 원문30자 저장명 대응을 image-save-plan.json에 기록.\n').encode('utf-8')
    manifest=json.loads(files['PACKAGE_MANIFEST.json']);manifest.update(version=VERSION,replacement_for='2.7.9')
    active=set(manifest['files'])
    manifest['files']={n:{'size':len(files[n]),'sha256':_sha(files[n])} for n in sorted(active)}
    files['PACKAGE_MANIFEST.json']=_json(manifest)
    recovery_name='실행파일복구_2.7.2수정본.txt'
    recovery=files[recovery_name].decode('utf-8-sig').replace('2.7.9',VERSION).replace('yadam_tools_v279','yadam_tools_v2710')
    payload=_zip({n:files[n] for n in sorted(active)+['PACKAGE_MANIFEST.json']})
    recovery=re.sub(r'^PAYLOAD_SHA256\s*=.*$',lambda m:'PAYLOAD_SHA256 = '+repr(_sha(payload)),recovery,flags=re.M)
    recovery=re.sub(r'^PAYLOAD\s*=.*$',lambda m:'PAYLOAD = '+repr(base64.b64encode(payload).decode('ascii')),recovery,flags=re.M)
    files[recovery_name]=recovery.encode('utf-8')
    for name,raw in files.items():
        if name.endswith('.py'):ast.parse(raw.decode('utf-8-sig'),filename=name)
    return files

def rebuild_archive(base,policy):return _zip(patched_members(base,policy))

def build_image2710_package(package_root,data_root):
    from shared_tool_image279 import build_image279_package
    package_root=Path(package_root);directory=Path(data_root)/'shared-tools';path=directory/ASSET
    with _LOCK:
        directory.mkdir(parents=True,exist_ok=True)
        base=build_image279_package(package_root,data_root).read_bytes()
        data=rebuild_archive(base,(package_root/'image2710-policy.md').read_bytes())
        if path.is_file() and _sha(path.read_bytes())==_sha(data):return path
        fd,name=tempfile.mkstemp(prefix='image2710-',suffix='.tmp',dir=directory);temp=Path(name)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            temp.chmod(0o600);temp.replace(path)
        finally:temp.unlink(missing_ok=True)
        return path
