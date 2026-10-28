"""Offline 2.7.9 package builder; no network or hosting-provider operations."""
from pathlib import Path
import base64
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import zipfile

BASE_ASSET = 'yadam-image-prompts-v2.7.7.zip'
BASE_SHA256 = 'd7b536127b5c7cfe5ab6f00e40f8a206d6f198d03d0b23d3456799d21b2c5fd6'
ASSET = 'yadam-image-prompts-v2.7.9.zip'
STAMP = (2026, 10, 28, 12, 0, 0)
_LOCK = threading.Lock()


def _sha(raw): return hashlib.sha256(raw).hexdigest()

def _json(value): return json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')

def _zip(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name in sorted(files):
            info=zipfile.ZipInfo(name,STAMP);info.create_system=3;info.external_attr=0o600<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,files[name],compresslevel=6)
    return out.getvalue()


def patched_members(base, policy, guard):
    if _sha(base)!=BASE_SHA256: raise ValueError('2.7.7 base checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(base)) as z:
        if z.testzip(): raise ValueError('Bad base ZIP')
        files={i.filename:z.read(i) for i in z.infolist() if not i.is_dir()}
    def patch(name,old,new):
        text=files[name].decode('utf-8-sig')
        if text.count(old)!=1: raise ValueError('Patch mismatch: '+name+' / '+old[:50])
        files[name]=text.replace(old,new,1).encode('utf-8')
    def change(name,old,new):
        files[name]=files[name].decode('utf-8-sig').replace(old,new).encode('utf-8')
    policy=policy.decode('utf-8-sig'); guard=guard.decode('utf-8-sig')
    files['WORKFLOW_RULES.md']=policy.encode('utf-8')
    files['workflow_guard.py']=guard.encode('utf-8')
    install='''\n\n## 실행 파일 준비\n실제 첨부 ZIP을 도구 폴더에 풀고 `python setup_check.py`로 무결성을 확인합니다.\n복구 TXT가 있으면 해당 파일은 대본이 아닌 Python 복구 도구입니다. 실행하여 준비합니다.\n사용자에게 내부 JSON을 작성하게 하지 않습니다. 담당 AI가 원문과 실제 사용자 답변으로 작성합니다.\n`gates.py --bind-script` → 화풍 선택 기록 → `--bind-style` → `--plan` → `--cast` → 청크 검사 → `--bind-values --reviewer 검토자` → `--final` 순서입니다.\n화풍 방식 질문·사용자 선택은 분석/실행보다 먼저입니다. 최종 프롬프트 생성은 이미지 생성 동의와 별개입니다.\n코드 실행이 안 되면 실제 제한을 알리고 실행·검증·이미지 생성 완료를 주장하지 않습니다.\n구버전 테스트/시뮬레이션은 과거 형식 자료이며 현재 규칙이 아닙니다. 새 검증은 WORKFLOW_RULES.md와 workflow_guard.py를 사용합니다.\n'''
    files['CLAUDE_PROJECT_INSTRUCTIONS.md']=(policy+install).encode('utf-8')
    files['GPT_이미지생성_에셋참조_필수지침.md']=policy.encode('utf-8')
    files['guide.txt']=(policy+install).encode('utf-8')
    files['README_처음읽기.txt']=('야담 이미지제작 2.7.9\n화풍 방식 먼저 선택 → 프롬프트 → 생성 동의 → 에셋 총 18장 → 실제 참조로 본편 생성.\n'+install).encode('utf-8')
    files['READ_ME.txt']=files['README_처음읽기.txt']
    files['대본분석_화풍추천_사용법.txt']=files['CLAUDE_PROJECT_INSTRUCTIONS.md']
    files['00_모드선택_필독.md']=('''# 2.7.9 시작 순서\n화풍 방식 질문이 최우선입니다: 추천 화풍 / 참조 이미지 / 쓰고 싶은 화풍 직접 입력.\n사용자 답변 전에 대본 전체 분석이나 이미지 생성을 시작하지 않습니다.\n그다음 기존 기본/절약형을 선택합니다. 이미 선택한 값은 재질문하지 않습니다.\n두 모드 모두 WORKFLOW_RULES.md 전체를 지키며, 에셋 총 18장·통합 시트·ID 일치·생성 동의·상세 화풍은 동일합니다.\n''').encode('utf-8')
    files['절약형_핵심지침.md']=('''# 2.7.9 절약형\nWORKFLOW_RULES.md가 우선입니다. 화풍 방식 먼저 질문하고 실제 선택을 기다립니다.\n원문 전체와 선택 화풍 전체, 에셋 18개 레지스트리·컷별 호출 ID를 캐시에 보존합니다.\n반복 분석만 줄이며 화풍 문구·30자 매칭키·ID 검사·생성 동의·실제 참조 검사는 생략하지 않습니다.\n프롬프트 완성 후 “에셋 이미지 생성 후 본편 이미지 만들까요?”라고 질문합니다.\n동의 전에는 생성하지 않으며 동의 후에도 에셋 18장 모두 준비·검토된 다음 본편을 생성합니다.\n''').encode('utf-8')
    files['STYLE_SELECTION_GUIDE.md']=('''# 2.7.9 화풍 선택\n대본 접수 즉시 추천/참조 이미지/직접 입력 중 방식부터 묻습니다. 전체 분석 뒤에 처음 질문하는 순서는 폐기합니다.\n추천 방식이면 그 다음 대본을 분석해 후보를 제시하고 사용자 선택을 기다립니다.\n참조 방식은 실제 첨부의 선·얼굴·표정·명암·팔레트를 분석합니다. 이미 올린 이미지를 다시 요구하지 않습니다.\n직접 방식이면 입력한 화풍을 상세화합니다. 실제 사용자 선택 없이 자동 고정하지 않습니다.\n최종 STYLE_SELECTION.json 구조와 상세 5항목 원문 보존은 그대로입니다. 인물의 나이·성별·체형은 바꾸지 않습니다.\n화풍 선택은 이미지 생성 동의가 아닙니다. 생성은 완성 프롬프트에 대한 별도 동의 후 진행합니다.\n''').encode('utf-8')
    # Retain technical reference, remove obsolete duplicated image workflow blocks.
    rule=files['RULES.md'].decode('utf-8-sig')
    marker='# 재구현 검증 도구 규칙'
    if marker in rule: rule=rule[rule.index(marker):]
    rule=rule.replace('컷 수 기본 40.','컷 수 기본 100.').replace('One UPLOAD portrait','One combined character sheet')
    files['RULES.md']=('# 2.7.9\nWORKFLOW_RULES.md의 순서·18장·동의·ID 규칙을 먼저 적용합니다. 아래는 기존 구조 검사 참고입니다.\n\n'+rule).encode('utf-8')
    for name in ('화풍추가_2.7.7.md','수정내역_V2.7.6.txt'):
        if name in files:
            txt=files[name].decode('utf-8-sig')
            txt=re.sub(r'^.*여섯 개 독립 파일.*$', '인물 한 명당 여섯 모습을 담은 통합 시트 한 장. 전체 에셋은 배경·소품을 합해 18장.',txt,flags=re.M)
            if name.startswith('수정내역'):txt='과거 변경 기록. 현재 제작 규칙은 WORKFLOW_RULES.md만 따릅니다.\n'+txt
            files[name]=txt.encode('utf-8')
    for name in ('ANALYSIS_SCHEMA.md','화풍원문_필독.md','대본원문_필독.md'):
        txt=files[name].decode('utf-8-sig')
        txt=txt.replace('대본 분석 뒤 사용자에게 추천/레퍼런스/직접 화풍 중 선택받는 절차를 유지합니다.', '대본 접수 즉시 화풍 방식을 먼저 선택받고 WORKFLOW_RULES.md 순서로 진행합니다.')
        files[name]=('# 2.7.9: WORKFLOW_RULES.md 우선. 아래 자료의 순서와 충돌하면 현재 순서를 따릅니다.\n'+txt).encode('utf-8')
    files['수정내역_2.7.9.txt']=('2.7.9: 화풍 방식 먼저 질문; 인물·배경·소품 합계 18장; 통합 시트; 프롬프트 완성 후 생성 동의; 실제 참조 ID/파일/해시 확인.\n과거 보고서의 PASS는 이번 버전의 실행 결과가 아닙니다.\n').encode('utf-8')
    template={'version':'2.7.9','workflow':{'style_mode':None,'style_mode_user_message':'','style_selected_user_message':''},
              'assets':[], 'chapter_asset_fields':{'asset_ids':[],'asset_evidence':{}},
              'note':'Fill from actual manuscript and actual user choices. Empty template never passes.'}
    files['WORKFLOW_TEMPLATE.json']=_json(template)
    # Wire source/ID gate directly into the final export.
    patch('gates.py','import scene_value_guard\n','import scene_value_guard\nimport workflow_guard\n')
    change('gates.py',"VERSION = '2.7.5'","VERSION = '2.7.9'")
    change('gates.py',"s.get('expected_count', 40)","s.get('expected_count', 100)")
    change('gates.py','(default 40)','(default 100)')
    change('gates.py','def split_chapters(body, count=40, want=None):','def split_chapters(body, count=100, want=None):')
    start=files['gates.py'].decode('utf-8').index('def scene_asset_tokens(')
    end=files['gates.py'].decode('utf-8').index('\ndef format_scene_prompt_line',start)
    text=files['gates.py'].decode('utf-8')
    text=text[:start]+'''def scene_asset_tokens(state, row):
    ids=state['chapters'][row['n']-1].get('asset_ids')
    if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):
        raise ValueError('ASSET: exact registered per-cut asset_ids required')
    known={a['id'] for a in state.get('assets',[])}
    if set(ids)-known: raise ValueError('ASSET: unregistered call ID')
    return ['@['+key+']' for key in ids]

'''+text[end:]
    files['gates.py']=text.encode('utf-8')
    patch('gates.py','def write_bundle(root, state, body, rows, lines):\n',
          'def write_bundle(root, state, body, rows, lines):\n    workflow_guard.require_plan(state, body, rows, lines)\n')
    patch('gates.py',"            if args.final and not errors:\n", "            if args.final and not errors:\n                workflow_guard.require_plan(s, body, rows, lines)\n")
    patch('gates.py',"    return output_path\n",'''    asset_records=assemble.asset_prompts(state)
    save_json(root/'output'/'asset-plan.json',asset_records)
    (root/'output'/'asset-prompts.txt').write_text('\n'.join(
        r['id']+' | '+r['filename']+' | '+r['prompt'] for r in asset_records)+'\n',encoding='utf-8-sig')
    workflow_guard.prepare_question(root,state,body)
    print(workflow_guard.GENERATION_QUESTION)
    return output_path
'''.replace("'\n'", "'\\n'"))
    # An asset prompt is one six-view sheet, not six independent images.
    text=files['assemble.py'].decode('utf-8')
    a=text.index("            view=anchor.get(");b=text.index('            errors=style_integrity.prompt_errors',a)
    text=text[:a]+'''            upload=('Single composite character sheet of '+description+', one character only on one image canvas, '
                    'exactly six separated non-overlapping views: full-body front from head to shoes, '
                    'full-body back from head to shoes, face and upper-body front, face and upper-body left 45 degrees, '
                    'face and upper-body right 45 degrees, face and upper-body side profile 90 degrees, '
                    'same face, age, gender, body proportions, hairstyle, outfit, colors and accessories in all six views, '
                    'neutral calm expressions, plain flat neutral gray background, no scene props, no labels or captions, '+style)
'''+text[b:]
    text+='''\n\ndef asset_prompts(state):
    """Produce all 18 reference-image prompts without generating image bytes."""
    style=style_integrity.state_style(state)
    characters={r['id']:r for r in character_prompts(state)}
    assets=state.get('assets',[])
    if len(assets)!=18: raise ValueError('ASSET: exactly 18 assets required')
    result=[]
    for asset in assets:
        key=asset['id'];kind=asset['kind']
        if kind=='character': prompt=characters[key]['upload']
        elif kind=='background': prompt='Single background reference of '+asset['description']+', no people, one scene only, '+style
        elif kind=='prop': prompt='Single isolated prop reference of '+asset['description']+', plain neutral background, no people, '+style
        else: raise ValueError('ASSET: invalid kind')
        errors=style_integrity.prompt_errors(prompt,state['style_lock'],'ASSET '+key)
        if errors: raise ValueError('; '.join(errors))
        result.append(dict(id=key,kind=kind,filename=asset['filename'],prompt=prompt))
    return result
'''
    files['assemble.py']=text.encode('utf-8')
    patch('style_integrity.py',"if not prompt.startswith('Single figure portrait of '):", "if not prompt.startswith('Single composite character sheet of '):")
    change('style_integrity.py','one Single figure portrait per character is required','one combined six-view character sheet per character is required')
    # CLI stops before analysis when the style method is missing.
    patch('대본분석_화풍추천.py','def main():\n','def main():\n    import workflow_guard\n')
    patch('대본분석_화풍추천.py',"    args=ap.parse_args()\n", "    ap.add_argument('--style-mode',choices=['recommended','reference','manual'])\n    ap.add_argument('--style-mode-user-message')\n    args=ap.parse_args()\n")
    patch('대본분석_화풍추천.py',"        if args.script:\n            a=auto_analyze_script(args.script,args.scenes,args.title); source=str(args.script)",
          "        if args.script:\n            workflow={'style_mode':args.style_mode,'style_mode_user_message':args.style_mode_user_message}\n            workflow_guard.require_mode(workflow)\n            a=auto_analyze_script(args.script,args.scenes,args.title); a['workflow']=workflow; source=str(args.script)")
    change('대본분석_화풍추천.py',"ap.add_argument('--scenes',type=int,default=40)","ap.add_argument('--scenes',type=int,default=100)")
    change('대본분석_화풍추천.py','def auto_analyze_script(script_path, scene_count=40, title=None):','def auto_analyze_script(script_path, scene_count=100, title=None):')
    patch('recommend.py','import scene_value_guard\n','import scene_value_guard\nimport workflow_guard\n')
    patch('recommend.py',"def build_candidates(a,styles,top_k=3,mode='basic'):\n", "def build_candidates(a,styles,top_k=3,mode='basic'):\n    workflow_guard.require_mode(a.get('workflow'))\n")
    patch('recommend.py',"def build_final(a,styles,types,selection,mode='basic'):\n", "def build_final(a,styles,types,selection,mode='basic'):\n    workflow_guard.require_mode(a.get('workflow'), selected=True)\n")
    # Local recomender outputs are planning documents; the final Flow is exported by gates --final.
    change('recommend.py',"VERSION='2.7.2-r1'","VERSION='2.7.9'")
    change('대본분석_화풍추천.py',"VERSION = '2.7.2-r1'","VERSION = '2.7.9'")
    change('setup_check.py',"VERSION = '2.7.7'","VERSION = '2.7.9'")
    # Existing double-click entry should ask the mode instead of using an implicit style.
    files['대본분석_화풍추천_실행.cmd']=b'@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\necho See CLAUDE_PROJECT_INSTRUCTIONS.md: choose recommended, reference or manual before analysis.\r\npython workflow_guard.py --help\r\npause\r\n'
    share=json.loads(files['share_manifest.json'])
    share.update(version='2.7.9',title='야담 이미지 제작 2.7.9 · 화풍 먼저 선택 · 에셋 18장',base_package='야담_이미지제작_2.7.9.zip',
                 asset_count=18,asset_count_scope='characters_backgrounds_props_total',style_selection='ASK_METHOD_BEFORE_ANALYSIS',
                 image_generation_confirmation='ASK_AFTER_PROMPTS_WAIT_FOR_APPROVAL',generation_question='에셋 이미지 생성 후 본편 이미지 만들까요?',
                 character_reference_policy='ONE_SIX_VIEW_SHEET_PER_CHARACTER',live_deployed=False)
    files['share_manifest.json']=_json(share)
    # Manifest includes active runtime/knowledge only. Recovery data never restores obsolete instructions.
    active=[n for n in files if (n.endswith('.py') and not n.startswith('test_')) or n in (
        'WORKFLOW_RULES.md','WORKFLOW_TEMPLATE.json','CLAUDE_PROJECT_INSTRUCTIONS.md','GPT_이미지생성_에셋참조_필수지침.md',
        '00_모드선택_필독.md','절약형_핵심지침.md','STYLE_SELECTION_GUIDE.md','RULES.md','guide.txt','READ_ME.txt','README_처음읽기.txt',
        'STYLE_BANK.json','IMAGE_TYPE_BANK.json','REFERENCE_CATALOG.json','REFERENCE_STYLE_TEMPLATE.json','STYLE_SELECTION_TEMPLATE.json',
        'ANALYSIS_SCHEMA.md','ANALYSIS_TEMPLATE.json','share_manifest.json','화풍원문_필독.md','대본원문_필독.md',
        'REFERENCE_02_STYLE_TAIL_원문전체.txt','REFERENCE_04_STYLE_TAIL_원문전체.txt')]
    manifest=json.loads(files['PACKAGE_MANIFEST.json'])
    manifest.update(version='2.7.9',replacement_for='2.7.8')
    manifest['files']={n:{'size':len(files[n]),'sha256':_sha(files[n])} for n in sorted(active)}
    manifest['import_modules']=sorted(set(manifest['import_modules'])|{'workflow_guard'})
    files['PACKAGE_MANIFEST.json']=_json(manifest)
    recovery_name='실행파일복구_2.7.2수정본.txt'
    recovery=files[recovery_name].decode('utf-8-sig')
    recovery=recovery.replace('2.7.7','2.7.9').replace('2.7.8','2.7.9').replace('yadam_tools_v277','yadam_tools_v279').replace('yadam_tools_v278','yadam_tools_v279')
    payload=_zip({n:files[n] for n in sorted(active)+['PACKAGE_MANIFEST.json']})
    recovery=re.sub(r"^PAYLOAD_SHA256\s*=.*$",lambda m:'PAYLOAD_SHA256 = '+repr(_sha(payload)),recovery,flags=re.M)
    recovery=re.sub(r"^PAYLOAD\s*=.*$",lambda m:'PAYLOAD = '+repr(base64.b64encode(payload).decode('ascii')),recovery,flags=re.M)
    files[recovery_name]=recovery.encode('utf-8')
    return files


def rebuild_archive(base, policy, guard):
    return _zip(patched_members(base,policy,guard))


def build_image279_package(package_root, data_root):
    """Build only from checked-in base+overlays and write atomically."""
    package_root=Path(package_root);directory=Path(data_root)/'shared-tools';path=directory/ASSET
    with _LOCK:
        directory.mkdir(parents=True,exist_ok=True)
        policy=(package_root/'image279-policy.md').read_bytes()
        guard=(package_root/'image279-workflow_guard.py').read_bytes()
        data=rebuild_archive((package_root/BASE_ASSET).read_bytes(),policy,guard)
        if path.is_file() and _sha(path.read_bytes())==_sha(data):return path
        fd,name=tempfile.mkstemp(prefix='image279-',suffix='.tmp',dir=directory)
        temp=Path(name)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            temp.chmod(0o600);temp.replace(path)
        finally:temp.unlink(missing_ok=True)
        return path
