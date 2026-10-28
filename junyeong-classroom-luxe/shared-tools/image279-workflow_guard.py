"""2.7.9 workflow/asset gates. Local evidence checks, not an image service.
Never calls a provider. APPROVED is a supplied review, not an automatic verdict.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re

VERSION = '2.7.9'
ASSET_COUNT = 18
STYLE_QUESTION = '화풍은 어떤 방식으로 정할까요?\n1. 추천 화풍\n2. 참조 이미지\n3. 쓰고 싶은 화풍 직접 입력'
GENERATION_QUESTION = '에셋 이미지 생성 후 본편 이미지 만들까요?'
VIEWS = ['front_full', 'back_full', 'front_face', 'left45', 'right45', 'profile90']
ID_RE = re.compile(r'^[A-Za-z가-힣][A-Za-z0-9_가-힣]*$')
REF_RE = re.compile(r'@\[([^\[\]\r\n]+)\]|@([A-Za-z][A-Za-z0-9_]*)')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def refs(text):
    return [a or b for a, b in REF_RE.findall(text)]


def require_mode(workflow, selected=False):
    if not isinstance(workflow, dict) or workflow.get('style_mode') not in ('recommended', 'reference', 'manual'):
        raise ValueError('WAITING_STYLE_METHOD: ' + STYLE_QUESTION)
    if not isinstance(workflow.get('style_mode_user_message'), str) or not workflow['style_mode_user_message'].strip():
        raise ValueError('WAITING_STYLE_METHOD: actual user choice is required')
    if selected and not str(workflow.get('style_selected_user_message', '')).strip():
        raise ValueError('WAITING_STYLE_SELECTION: actual user style selection is required')


def plan_errors(state, body, rows=None, lines=None):
    errors = []
    try: require_mode(state.get('workflow'), selected=True)
    except ValueError as exc: errors.append(str(exc))
    if not isinstance(body, list) or not body or any(not isinstance(x, str) for x in body):
        return errors + ['ASSET: full source body required']
    assets = state.get('assets')
    if not isinstance(assets, list) or len(assets) != ASSET_COUNT:
        return errors + ['ASSET: exactly 18 registered assets required, including characters/backgrounds/props']
    all_text = '\n'.join(body); registry = {}; filenames = set()
    for asset in assets:
        if not isinstance(asset, dict):
            errors.append('ASSET: invalid registry record'); continue
        key = asset.get('id')
        if not isinstance(key, str) or not ID_RE.fullmatch(key):
            errors.append('ASSET: invalid exact ID'); continue
        if key in registry: errors.append('ASSET: duplicate ID ' + key)
        registry[key] = asset
        if asset.get('kind') not in ('character', 'background', 'prop'):
            errors.append('ASSET ' + key + ': invalid kind')
        if not str(asset.get('description', '')).strip(): errors.append('ASSET ' + key + ': missing description')
        quote = asset.get('source_evidence')
        if not isinstance(quote, str) or not quote.strip() or quote not in all_text:
            errors.append('ASSET ' + key + ': source evidence absent from manuscript')
        filename = asset.get('filename')
        if (not isinstance(filename, str) or not filename or Path(filename).name != filename
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', filename) or filename in ('.','..')):
            errors.append('ASSET ' + key + ': unsafe filename')
        elif filename.casefold() in filenames: errors.append('ASSET ' + key + ': duplicate filename')
        else: filenames.add(filename.casefold())
        if asset.get('kind') == 'character':
            if filename != key + '_캐릭터시트.png': errors.append('ASSET ' + key + ': one combined sheet filename required')
            if asset.get('views') != VIEWS: errors.append('ASSET ' + key + ': six views on ONE canvas required')
    anchors = state.get('ingredients', []) + state.get('subanchors', [])
    char_ids = {a.get('id') for a in anchors}
    registered_chars = {k for k, a in registry.items() if a.get('kind') == 'character'}
    if char_ids != registered_chars: errors.append('ASSET: character registry and cast IDs must match exactly')
    chapters = state.get('chapters', [])
    if not chapters: return errors + ['ASSET: chapter plan required']
    if rows is not None and (len(rows) != len(chapters) or [r.get('n') for r in rows] != list(range(1,len(chapters)+1))):
        return errors + ['ASSET: authored rows must be ordered 1..N']
    if lines is not None and len(lines) != len(chapters): return errors + ['ASSET: prompt count mismatch']
    used = set()
    for i, chapter in enumerate(chapters, 1):
        label = 'ASSET CUT ' + str(i) + ': '
        ids = chapter.get('asset_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(k,str) for k in ids):
            errors.append(label + 'asset_ids required'); continue
        if len(ids) != len(set(ids)): errors.append(label + 'duplicate calls')
        if set(ids) - registry.keys(): errors.append(label + 'unregistered IDs: ' + ','.join(sorted(set(ids)-registry.keys())))
        used.update(ids)
        a, b = chapter.get('start_idx'), chapter.get('end_idx')
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(body):
            errors.append(label + 'invalid original source range'); continue
        source = '\n'.join(body[a:b]); evidence = chapter.get('asset_evidence', {})
        if not isinstance(evidence, dict) or set(evidence) != set(ids):
            errors.append(label + 'evidence keys must equal invoked IDs'); evidence = {}
        for key in ids:
            quote = evidence.get(key)
            if not isinstance(quote, str) or not quote.strip() or quote not in source:
                errors.append(label + key + ' has no exact evidence in this cut')
        background = chapter.get('set_id')
        if background == 'oneoff' or background not in ids or registry.get(background, {}).get('kind') != 'background':
            errors.append(label + 'actual background asset must match set_id; unregistered oneoff is blocked')
        if rows is not None:
            row = rows[i-1]; row_ids = row.get('tags', []) + row.get('subanchors', [])
            planned_chars = {k for k in ids if registry.get(k,{}).get('kind') == 'character'}
            if set(row_ids) != planned_chars or len(row_ids) != len(set(row_ids)):
                errors.append(label + 'cast call IDs do not match asset IDs')
        if lines is not None:
            prompt = lines[i-1]
            called = set(refs(prompt))
            if called - set(ids): errors.append(label + 'prompt calls an asset outside this cut')
            if registered_chars.intersection(ids) - called: errors.append(label + 'visible character ID missing from prompt')
            if re.search(r"(?:@\[[^\]\n]+\]|@[A-Za-z][A-Za-z0-9_]*)['’]s\b",prompt):
                errors.append(label + 'possessive suffix attached to ID')
    if registry.keys() - used: errors.append('ASSET: unused filler assets are not allowed: ' + ','.join(sorted(registry.keys()-used)))
    return errors


def require_plan(state, body, rows=None, lines=None):
    errors = plan_errors(state, body, rows, lines)
    if errors: raise ValueError('; '.join(errors))


def bundle_hash(root, state, body):
    root = Path(root)
    files = {}
    for name in ('prompts.txt', 'asset-prompts.txt'):
        path = root/'output'/name
        if not path.is_file() or not path.stat().st_size: raise ValueError('PROMPTS: final ' + name + ' is missing')
        files[name] = sha(path.read_bytes())
    stable = {k:state.get(k) for k in ('TITLE','workflow','assets','chapters','ingredients','subanchors','STYLE_TAIL','style_lock')}
    return fingerprint({'state':stable,'source':body,'files':files})


def prepare_question(root, state, body):
    require_plan(state, body)
    result = {'version':VERSION,'status':'WAITING_USER_APPROVAL','question':GENERATION_QUESTION,
              'asset_count':18,'scene_count':len(state['chapters']),'plan_sha256':bundle_hash(root,state,body)}
    write(Path(root)/'GENERATION_REQUEST.json',result)
    return result


def approve(root, state, body, user_message):
    require_plan(state, body)
    question = read(Path(root)/'GENERATION_REQUEST.json')
    current = bundle_hash(root,state,body)
    if question.get('question') != GENERATION_QUESTION or question.get('plan_sha256') != current:
        raise ValueError('CONSENT: ask again for the current prompt bundle')
    if not isinstance(user_message,str) or not user_message.strip(): raise ValueError('CONSENT: actual approval reply required')
    if re.search(r'거절|하지\s*마|프롬프트만|안\s*돼|아니|\bno\b',user_message,re.I):
        raise ValueError('CONSENT: negative reply cannot approve generation')
    if not re.match(r'^(네|예|응|진행|시작|만들어|생성|제작|좋아|좋습니다|yes|okay|ok)(?:\b|[ ,.!]|$)', user_message.strip(), re.I):
        raise ValueError('CONSENT: explicit affirmative generation reply required; editing rules is not approval')
    result = {'version':VERSION,'approved':True,'question':GENERATION_QUESTION,
              'user_message':user_message,'plan_sha256':current,'scope':'assets_then_scenes'}
    write(Path(root)/'GENERATION_CONSENT.json',result)
    return result


def require_consent(root, state, body):
    value = read(Path(root)/'GENERATION_CONSENT.json')
    if (value.get('approved') is not True or value.get('scope') != 'assets_then_scenes'
            or value.get('question') != GENERATION_QUESTION or not str(value.get('user_message','')).strip()
            or value.get('plan_sha256') != bundle_hash(root,state,body)):
        raise ValueError('CONSENT: missing or stale generation permission')


def image_records(root, state):
    items = read(Path(root)/'ASSET_IMAGES.json')
    if not isinstance(items,list) or len(items)!=18: raise ValueError('IMAGES: all 18 assets must be completed first')
    records = {}; paths = set(); image_refs = set()
    expected = {a['id']:a for a in state['assets']}
    for item in items:
        key = item.get('id')
        if key not in expected or key in records: raise ValueError('IMAGES: duplicate or unregistered ID')
        if item.get('review_status') != 'APPROVED': raise ValueError('IMAGES '+key+': visual review is pending')
        path = Path(item.get('path',''))
        if not path.is_absolute(): path=Path(root)/path
        path=path.resolve()
        if path.name!=expected[key]['filename'] or not path.is_file() or path in paths:
            raise ValueError('IMAGES '+key+': missing, reused or wrong asset file')
        raw=path.read_bytes()
        png=raw.startswith(b'\x89PNG\r\n\x1a\n'); jpg=raw.startswith(b'\xff\xd8\xff'); webp=raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'
        if len(raw)<32 or not (png or jpg or webp): raise ValueError('IMAGES '+key+': not a recognized image header')
        if sha(raw)!=item.get('sha256'): raise ValueError('IMAGES '+key+': image hash mismatch')
        image_ref=item.get('image_ref')
        if not isinstance(image_ref,str) or not image_ref.strip() or image_ref in image_refs:
            raise ValueError('IMAGES '+key+': missing or reused provider reference')
        paths.add(path); image_refs.add(image_ref); records[key]=item
    if set(records)!=set(expected): raise ValueError('IMAGES: ID set does not match the 18 planned assets')
    return records


def preflight(root, state, body, stage, scene=None, request=None):
    require_plan(state,body)
    require_consent(root,state,body)
    if stage=='assets': return {'status':'READY_FOR_ASSETS','scope':'validation only; no image request sent'}
    records=image_records(root,state)
    if type(scene) is not int or not 1<=scene<=len(state['chapters']): raise ValueError('SCENE: invalid scene number')
    expected=state['chapters'][scene-1]['asset_ids']
    if not isinstance(request,dict) or request.get('asset_ids') != expected:
        raise ValueError('SCENE: outgoing reference IDs must exactly match the planned order')
    if request.get('image_refs') != [records[k]['image_ref'] for k in expected]:
        raise ValueError('SCENE: outgoing actual image references do not match asset IDs')
    return {'status':'READY_FOR_SCENE','scene':scene,'asset_ids':expected,
            'scope':'file/hash/declared-request check; caller must pass these images to the generation tool'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--stage',choices=['prompts','question','approve','assets','scene'],required=True)
    parser.add_argument('--user-message'); parser.add_argument('--scene',type=int); parser.add_argument('--request',type=Path)
    args=parser.parse_args()
    try:
        state=read(args.root/'state.json');body=read(args.root/'body.json')
        # Validate the bound original, not only a possibly edited body.json.
        import script_integrity
        errors=script_integrity.source_errors(args.root,body)
        if errors: raise ValueError('; '.join(errors))
        if args.stage=='prompts':
            require_plan(state,body); result={'status':'PROMPT_PLAN_VALID','image_review':'NOT_PERFORMED'}
        elif args.stage=='question': result=prepare_question(args.root,state,body)
        elif args.stage=='approve': result=approve(args.root,state,body,args.user_message)
        else: result=preflight(args.root,state,body,args.stage,args.scene,read(args.request) if args.request else None)
    except (OSError,ValueError,TypeError,KeyError,IndexError) as exc:
        result={'status':'FAIL','error':str(exc)}
    write(args.root/'workflow-validation-last-run.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result['status']=='FAIL' else 0


if __name__=='__main__': raise SystemExit(main())
