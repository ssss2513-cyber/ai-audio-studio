"""Add the requested style to the verified 2.7.6 release; no runtime replacement."""
import argparse
import ast
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import pprint
import re
import subprocess
import sys
import tempfile
import zipfile

BASE_SHA = '648ee832595e8f4b669a1400e31e4e22c1abd6e8d42fb27756c6a8198aa2187b'
VERSION = '2.7.7'
STYLE_ID = 'joseon_girl_character_sheet_cel'
REFERENCE = 'REFERENCE_04_파스텔한복_세미SD.jpg'
NAME = '소녀형 한복 캐릭터시트 2D 셀 웹툰'
SPEC = {
 'line_art': 'cute semi-chibi Korean Joseon-era 2D character illustration, fine clean dark-brown closed contours with delicate interior lines, softly rounded silhouettes, tidy simplified hanbok folds, refined drawn details rather than photographic texture',
 'face_design': 'rounded oval face shapes with gently full cheeks and a small rounded chin, large rounded almond eyes with dark brown irises, clear pupils and two small controlled highlights, thin softly curved brows, a tiny simplified nose tip, a small clearly drawn mouth with delicate corners, smooth opaque skin with a restrained dusty-pink cheek-blush patch, softly grouped dark hair strands and clean period-appropriate hair silhouettes; preserve each scripted character age, gender, identity, face proportions and body build, adapting the stylization without turning adults or seniors into children or changing men into women',
 'expression_direction': 'readable gentle expressions through eyelid opening, gaze direction, slight inner-brow changes and small mouth-corner movements; quiet smiles, curiosity and surprise stay natural to the current scene, never a forced smile during grief; keep facial landmarks consistent with the approved character assets',
 'shading': 'low-contrast two-tone cel shading with small clean shadow shapes under the fringe, chin and hanbok folds, softly perceived value contrast but distinct painted regions, fully legible faces, no heavy black facial shadows and no painterly blending',
 'palette': 'muted pastel cream, dusty rose, sage green and neutral taupe accents, balanced neutral skin colors and restrained saturation, preserve each approved costume color; use the actual scripted scene setting and time, without replacing backgrounds by a blank backdrop or forcing daytime'
}
TAIL = ', '.join(SPEC.values()) + ', one coherent image per requested view or scene, no split panels, no collage, no duplicate character views within a scene, no photorealism, no live action, no 3D render, no watercolor texture, no painterly blending, no gradients, no red or orange color cast, no text, no watermark'
NOTE = '''# V2.7.7 추가 화풍 — 소녀형 한복 캐릭터시트 2D 셀 웹툰

사용자가 올린 한복 참고 그림의 둥근 얼굴·큰 눈·작은 코와 입·볼홍조·얇은 갈색 선화·파스텔 색감을 추가했습니다.
- STYLE_BANK의 joseon_girl_character_sheet_cel 또는 REFERENCE_04를 선택합니다. 추천 후보일 뿐 자동 확정하지 않습니다.
- 기존 9종 화풍과 V2.7.6 기능은 유지합니다. 장면마다 화풍을 자동 변경하지 않습니다.
- 그림체와 인물 설정을 분리합니다. 대본의 나이·성별·체형·신분·머리·복식을 유지하며 모든 인물을 소녀로 바꾸지 않습니다.
- 캐릭터시트는 참고 이미지의 구성입니다. 본편은 한 컷짜리 장면이며, 여섯 각도를 한 화면에 복제하지 않습니다.
- 전신 2장 + 각도 4장은 기존대로 여섯 개 독립 파일로 생성합니다. 실제 에셋 이미지 참조와 완성 후 생성 확인 절차를 유지합니다.
- 회색 배경은 UPLOAD 기준 이미지에만 적용합니다. 본편 배경·인원·시간은 해당 대본에 맞춥니다.
- 기본/절약형 모두 상세 얼굴·선·표정·명암·팔레트 원문 전체를 매 프롬프트에 유지합니다.
- 본편 파일명 숫자_ 제거, 원문 앞 30자, 에셋 호출, 서사/해설/교육 가치·불쾌감 검토는 그대로입니다.
- REFERENCE_04는 첨부 스크린샷의 그림 영역만 추출한 미리보기입니다. 얼굴 동일성을 복제하는 인물 에셋이 아닙니다.
- 자동 검증은 텍스트·파일·선택 경로 검사이며 생성 이미지의 시각적 일치나 유튜브 승인을 보증하지 않습니다.

'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def zipped(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, raw in sorted(files.items()):
            assert Path(name).name == name and name not in ('.', '..')
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 26, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, raw)
    return out.getvalue()


def build(base_path, ref_path, output):
    raw = Path(base_path).read_bytes()
    assert sha(raw) == BASE_SHA, 'Base package changed; inspect and merge again'
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(set(z.namelist()))
        original = {n: z.read(n) for n in z.namelist() if not n.endswith('/')}
    files = dict(original)
    bank = json.loads(files['STYLE_BANK.json'])
    old_styles = copy.deepcopy(bank['styles'])
    assert len(old_styles) == 9 and not any(s['id'] == STYLE_ID for s in old_styles)
    new_style = dict(id=STYLE_ID, name=NAME, family=bank['family'], base_score=0,
        triggers=['귀여운','세미SD','세미 SD','파스텔','소녀형','큰 눈','둥근 얼굴','순수','따뜻함','캐릭터시트','가족'],
        scene_triggers=['family','tender','affection','reaction'],
        style_summary_ko='둥근 얼굴·큰 눈·작은 코와 입·볼홍조·얇은 갈색 선과 파스텔 한복 채색. 인물의 나이와 성별은 대본대로 유지합니다.',
        visual_spec=SPEC, style_tail=TAIL, reference_files=[REFERENCE])
    bank['styles'].append(new_style)
    bank['version'] = VERSION
    files['STYLE_BANK.json'] = dump(bank)
    catalog = json.loads(files['REFERENCE_CATALOG.json'])
    catalog['version'] = VERSION
    catalog['files'].append(dict(file=REFERENCE, use_for=[STYLE_ID], default_style_id=STYLE_ID,
        aliases=['REFERENCE_04','소녀형 한복','파스텔 한복 세미SD'],
        note='사용자 첨부 그림 영역 미리보기. 화풍만 참고하며 나이·성별·체형·복식·단일 장면 구도는 대본과 에셋 계획을 따른다.'))
    files['REFERENCE_CATALOG.json'] = dump(catalog)
    image = Path(ref_path).read_bytes()
    assert image[:2] == b'\xff\xd8' and image[-2:] == b'\xff\xd9'
    files[REFERENCE] = image
    files['REFERENCE_04_STYLE_TAIL_원문전체.txt'] = (TAIL + '\n').encode()
    files['화풍추가_2.7.7.md'] = NOTE.encode()
    for name in ['CLAUDE_PROJECT_INSTRUCTIONS.md','STYLE_SELECTION_GUIDE.md','절약형_핵심지침.md','guide.txt','README_처음읽기.txt','READ_ME.txt']:
        files[name] = NOTE.encode() + files[name]
    files['화풍상세목록_V2.5.md'] += ('\n\n## 10. '+NAME+'\n- ID: '+STYLE_ID+'\n'+ ''.join('- '+k+': '+v+'\n' for k,v in SPEC.items())+'- STYLE_TAIL: '+TAIL+'\n').encode()
    files['setup_check.py'] = files['setup_check.py'].replace(b"VERSION = '2.7.6'", b"VERSION = '2.7.7'")
    share = json.loads(files['share_manifest.json'])
    share.update(version=VERSION, title='야담 이미지 제작 2.7.7 · 파스텔 한복 세미SD 화풍 추가', replacement_for='2.7.6', base_package='야담_이미지제작_2.7.7.zip')
    files['share_manifest.json'] = dump(share)
    manifest = json.loads(files['PACKAGE_MANIFEST.json'])
    manifest.update(version=VERSION, replacement_for='2.7.6')
    names = set(manifest['files']) | {REFERENCE, 'REFERENCE_04_STYLE_TAIL_원문전체.txt', '화풍추가_2.7.7.md', '화풍상세목록_V2.5.md', 'share_manifest.json'}
    manifest['files'] = {n: dict(sha256=sha(files[n]), size=len(files[n])) for n in sorted(names)}
    manifest['optional_reference_images'].append(REFERENCE)
    files['PACKAGE_MANIFEST.json'] = dump(manifest)
    recovery_name = '실행파일복구_2.7.2수정본.txt'
    recovery = files[recovery_name].decode('utf-8').replace('2.7.6','2.7.7').replace('v276','v277')
    payload = zipped({n:files[n] for n in names | {'PACKAGE_MANIFEST.json'}})
    recovery, a = re.subn(r"^PAYLOAD_SHA256 = '[^']*'$", 'PAYLOAD_SHA256 = '+repr(sha(payload)), recovery, flags=re.M)
    recovery, b = re.subn(r"^PAYLOAD = '[^']*'$", 'PAYLOAD = '+repr(base64.b64encode(payload).decode()), recovery, flags=re.M)
    assert a == b == 1
    files[recovery_name] = recovery.encode()
    core = [n for n in original if n.endswith('.py') and not n.startswith('test_') and n != 'setup_check.py']
    assert all(files[n] == original[n] for n in core), 'Unrequested runtime change'
    assert bank['styles'][:-1] == old_styles
    with tempfile.TemporaryDirectory() as td:
        target = Path(td)
        for n, content in files.items():
            (target/n).write_bytes(content)
        for n in files:
            if n.endswith('.py'):
                ast.parse(files[n].decode('utf-8-sig'), filename=n)
        subprocess.run([sys.executable, '-B', str(target/'setup_check.py')], check=True, capture_output=True)
        check = '''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import recommend,style_integrity,assemble
p=Path(sys.argv[1]); bank=json.loads((p/'STYLE_BANK.json').read_text())['styles']; sid='joseon_girl_character_sheet_cel'
selected={'status':'SELECTED','choice':'recommended','recommended':{'style_id':sid}}
a=recommend.resolve_selection(selected,bank,None)
b=recommend.resolve_selection({'status':'SELECTED','choice':'reference','reference':{'reference_file':'REFERENCE_04'}},bank,None)
assert a['id']==b['id']==sid
assert style_integrity.full_text(a)==style_integrity.full_text(b)
for mode in ('basic','economy'):
 r=recommend.build_candidates({'title':'귀여운 세미SD 파스텔 한복','genre_primary':'가족','tone':['큰 눈','둥근 얼굴'],'global_flags':{}},bank,3,mode)
 assert r['status']=='WAITING_STYLE_SELECTION' and r['auto_style_lock'] is False
 assert sid in [c['id'] for c in r['candidates']]
try: recommend.resolve_selection({},bank,None)
except ValueError: pass
else: raise AssertionError('auto-selected')
lock=style_integrity.make_lock(a,selected,'style-test')
state={'TITLE':'style-test','STYLE_TAIL':lock['STYLE_TAIL_FULL'],'style_lock':lock,'ingredients':[{'id':'adult','name':'성인','age':30,'role':'man','text':'a 30-year-old Korean man, square face, blue hanbok'},{'id':'senior','name':'노인','age':70,'role':'woman','text':'a 70-year-old Korean woman, oval face, grey hanbok'}]}
portraits=assemble.character_prompts(state)
assert len(portraits)==2
assert '30-year-old Korean man' in portraits[0]['upload'] and '70-year-old Korean woman' in portraits[1]['upload']
for x in portraits: assert not style_integrity.prompt_errors(x['upload'],lock)
assert 'front side three-quarter and back views' not in lock['STYLE_TAIL_FULL']
assert 'no split panels' in lock['STYLE_TAIL_FULL']
assert all(s['visual_spec'][k] in style_integrity.full_text(s) for s in bank for k in style_integrity.SPEC_KEYS)
print('STYLE_SELECTION_AND_FULL_TEXT_OK')
'''
        subprocess.run([sys.executable,'-B','-c',check,str(target)], check=True, capture_output=True)
        restored = target/'restore-check'
        subprocess.run([sys.executable,'-B',str(target/recovery_name),'--target',str(restored)], check=True, capture_output=True)
        assert json.loads((restored/'SETUP_REPORT.json').read_text())['status']=='READY'
        assert (restored/'STYLE_BANK.json').read_bytes() == files['STYLE_BANK.json']
        assert (restored/REFERENCE).read_bytes() == image
    report = {'version': VERSION, 'status':'PASS', 'base_sha256':BASE_SHA,
        'style_count':10, 'preserved_styles':9, 'unchanged_runtime_modules':core,
        'checks':['base ZIP hash and CRC','Python syntax','setup hash/import readiness','unchanged previous styles',
                  'recommended selection','reference alias selection','basic/economy candidates wait for user',
                  'no automatic selection','complete style components','adult/senior character text preservation',
                  'no forced multi-panel layout','standalone recovery READY with new style and reference'],
        'scope':'Text, file and selection-path checks only. Existing scene-generation code unchanged. No generated-image visual verification.'}
    files['TEST_REPORT_2.7.7.json'] = dump(report)
    result = zipped(files)
    with zipfile.ZipFile(io.BytesIO(result)) as z:
        assert z.testzip() is None and len(z.namelist())==len(files)
    output = Path(output); output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(result)
    report.update(package_sha256=sha(result), package_size=len(result), file_count=len(files))
    output.with_suffix('.json').write_bytes(dump(report))
    print(json.dumps(report,ensure_ascii=False))
    return report


def update_catalog(path, report):
    path=Path(path); text=path.read_text(); tree=ast.parse(text)
    assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PACKAGES' for t in n.targets))
    before=ast.literal_eval(assignment.value)
    node=next(n for n in assignment.value.elts if ast.literal_eval(n).get('id')=='yadam-image-prompts')
    item=ast.literal_eval(node)
    assert item['version']=='2.7.6' and item['sha256']==BASE_SHA, 'Catalogue changed; re-merge required'
    item.update(asset='yadam-image-prompts-v2.7.7.zip', filename='야담_이미지제작_2.7.7.zip', version=VERSION, updated='2026-10-26',
        description='2.7.6의 이미지 생성 확인·전신 2장+각도 4장·실제 에셋 참조를 유지하고, 둥근 얼굴·큰 눈·볼홍조·파스텔 한복의 세미SD 화풍을 추가했습니다. 화풍은 사용자가 선택하며 나이·성별·본편 단일 구도를 유지합니다.',
        features=['신규 파스텔 한복 세미SD · REFERENCE_04 포함','화풍 10종 · 사용자 선택 후 적용','전신 2장 + 각도 4장 · 실제 에셋 참조 유지','숫자_ 없는 저장명 · 원문 30자 · 화풍 원문 유지'],
        fileCount=report['file_count'],sha256=report['package_sha256'])
    lines=text.splitlines(keepends=True)
    replacement='\n'.join('    '+s for s in pprint.pformat(item,sort_dicts=False,width=110).splitlines())+',\n'
    lines[node.lineno-1:node.end_lineno]=[replacement]
    updated=''.join(lines); parsed=ast.parse(updated)
    after=ast.literal_eval(next(n.value for n in parsed.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PACKAGES' for t in n.targets)))
    assert [x for x in before if x['id']!='yadam-image-prompts']==[x for x in after if x['id']!='yadam-image-prompts']
    path.write_text(updated)


if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--base',required=True); ap.add_argument('--reference',required=True); ap.add_argument('--output',required=True); ap.add_argument('--catalog')
    args=ap.parse_args(); report=build(args.base,args.reference,args.output)
    if args.catalog: update_catalog(args.catalog,report)
