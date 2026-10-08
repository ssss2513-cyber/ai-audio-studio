"""Per-session Kaggle batch jobs; only explicit starts submit GPU work.

Status/recovery calls only read an existing job. API credentials are kept in
memory and per-child environments, never process-global environment variables.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from urllib.parse import urlparse
from . import kaggle_history

ROOT = Path(__file__).resolve().parent.parent
LOCK = threading.RLock()
WORKERS = {}
ACTIVE = {'uploading', 'dataset_ready', 'submitting', 'queued', 'running', 'receiving', 'checking', 'stopping'}
CANCELED_REMOTE = {'canceled', 'cancelled', 'cancel_acknowledged'}
TERMINAL_REMOTE = {'complete', 'error', 'failed', *CANCELED_REMOTE}
REF_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+/voice-studio-[a-z0-9-]+$')
DATASET_REF_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+/voice-input-[a-z0-9-]+$')
OPERATION_LABELS = {'auth': '계정 연결', 'create_dataset': '대본 전송',
    'dataset_status': '대본 준비 확인', 'push': '생성 요청',
    'status': '실행 상태 확인', 'kernel_info': '작업 등록 확인', 'pull': '결과 받기',
    'inspect_job': '기존 계정·작업 확인', 'authentication': '인증정보 확인',
    'account_check': '내 계정 접근 확인', 'job_lookup': '내 작업 주소 확인',
    'list_jobs': '내 캐글 작업 목록 조회'}


class KaggleError(RuntimeError):
    def __init__(self, message, http_status=None, operation='', stage=''):
        super().__init__(message)
        self.http_status = http_status
        self.operation = operation
        self.stage = stage or operation


class KaggleOutputPending(KaggleError):
    """A successful output listing has not exposed all expected files yet."""


def api_call(credentials, operation, timeout=90, **arguments):
    if sys.version_info < (3, 11):
        raise KaggleError('사이트 Python을 3.11 이상으로 설정해야 캐글 연결을 사용할 수 있습니다.')
    # This directory is deliberately empty. Do not inherit any operator account.
    with tempfile.TemporaryDirectory(prefix='voice-kaggle-auth-') as config:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('KAGGLE_') and key not in ('GOOGLE_APPLICATION_CREDENTIALS',)}
        env.update(KAGGLE_CONFIG_DIR=config, PYTHONUNBUFFERED='1')
        if credentials.get('token'):
            env['KAGGLE_API_TOKEN'] = credentials['token']
        else:
            env['KAGGLE_USERNAME'] = credentials.get('username', '')
            env['KAGGLE_KEY'] = credentials.get('key', '')
        try:
            process = subprocess.run([sys.executable, str(ROOT / 'core/kaggle_api_bridge.py')],
                input=json.dumps(dict(operation=operation, **arguments)), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=config, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise KaggleError('캐글 응답 시간이 초과되었습니다. 작업을 재전송하지 않고 상태부터 확인합니다.',
                operation=operation) from None
    try:
        response = json.loads(process.stdout)
    except (ValueError, TypeError):
        raise KaggleError('캐글 연결 모듈을 시작하지 못했습니다. 사이트 업데이트 완료 후 다시 연결해주세요.',
            operation=operation) from None
    if not response.get('ok'):
        message = response.get('error') or '캐글 인증 또는 요청을 처리하지 못했습니다.'
        for key in ('key', 'token'):
            if credentials.get(key):
                message = message.replace(credentials[key], '[인증정보]')
        if "No module named 'kaggle'" in message:
            message = '캐글 연결 모듈을 설치하는 중입니다. 사이트 배포 완료 후 다시 연결해주세요.'
        raise KaggleError(message, response.get('http_status'), operation, response.get('stage', ''))
    return response['result']


def _remember_error(state, exc):
    state['diagnostic'] = dict(operation=getattr(exc, 'operation', ''),
        stage=getattr(exc, 'stage', ''), http_status=getattr(exc, 'http_status', None), reason=str(exc))
    if getattr(exc, 'operation', '') == 'push':
        state['submission_diagnostic'] = dict(state['diagnostic'])


def _error_message(exc):
    operation = OPERATION_LABELS.get(getattr(exc, 'stage', '') or getattr(exc, 'operation', ''), '요청 처리')
    code = getattr(exc, 'http_status', None)
    reason = str(exc)
    if code == 409:
        return f'캐글 {operation} 중 충돌(409)이 발생했습니다. 캐글 응답: {reason}'
    if code in (401, 403):
        return f'캐글 {operation}에서 접근이 거부되었습니다({code}). 이 코드만으로 토큰 만료라고 판단할 수 없습니다. 캐글 응답: {reason}'
    if code == 429:
        return f'캐글이 요청을 제한했습니다. 잠시 후 다시 확인해주세요. 캐글 응답: {reason}'
    return f'캐글 {operation}: {reason}'


def authenticate(credentials):
    result = api_call(credentials, 'auth')
    username = result.get('username', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', username):
        raise KaggleError('캐글 사용자 이름을 확인하지 못했습니다.')
    return dict(credentials, username=username)


def _adopt_job_ref(state, candidate, username):
    """Trust only a Kaggle-returned Voice Studio reference owned by this user."""
    if not isinstance(candidate, str) or not candidate:
        return
    if candidate.startswith('https://'):
        parsed = urlparse(candidate)
        if parsed.hostname not in ('www.kaggle.com', 'kaggle.com'):
            return
        candidate = parsed.path.removeprefix('/code/').strip('/')
    if not REF_PATTERN.fullmatch(candidate) or candidate.split('/')[0].lower() != username.lower():
        return
    if candidate != state['ref']:
        state.setdefault('requested_ref', state['ref'])
        state['ref'] = candidate


def _confirm_job(work_dir, state, credentials):
    """Read account + metadata before blaming a token or unlocking generation."""
    info = api_call(credentials, 'inspect_job', timeout=120, ref=state['ref'])
    state['account_authenticated'] = bool(info.get('account_authenticated'))
    if info.get('exists') is False:
        reason = state.get('submission_error', '')
        state.update(status='failed', submission='not_created', registration_confirmed=False,
            message='계정 인증은 정상이며, 캐글에 생성 작업이 등록되지 않은 것을 확인했습니다.',
            error=('토큰 인증은 통과했습니다. 기존 생성 작업이 없어 생성 제한을 해제했습니다. '
                   '위의 생성 또는 미리듣기 버튼을 다시 눌러주세요.'
                   + ('\n\n최초 생성 요청 오류: ' + reason if reason else '')))
        _save(work_dir, state)
        return 'missing'
    if info.get('exists'):
        _adopt_job_ref(state, info.get('ref'), credentials['username'])
        state['registration_confirmed'] = True
        _save(work_dir, state)
        return 'found'
    state.update(status='needs_check',
        message='계정 인증은 정상입니다. 캐글 작업의 등록·접근 상태는 확인되지 않았습니다.',
        error='계정 인증은 통과했지만 캐글 작업 조회가 거부되었습니다. '
              '아래 ‘오류 기록 TXT 받기’에서 최초 생성 요청과 조회 오류를 함께 확인할 수 있습니다.')
    state['diagnostic'] = dict(operation='inspect_job', stage='kernel_info',
        http_status=info.get('http_status'), reason=info.get('reason', ''))
    _save(work_dir, state)
    return 'unknown'


def job_registered(state):
    """A locally prepared reference is not proof of a registered Kaggle job."""
    if state.get('submission') in ('not_sent', 'not_created'):
        return False
    return bool(state.get('registration_confirmed') or state.get('submission') == 'accepted'
                or state.get('remote_status') or state.get('status') == 'complete')


def is_dataset_title_conflict(state):
    """Recognize only Kaggle's explicit rejection of this exact requested title."""
    if not state or state.get('kind') == 'parallel_cosy':
        return False
    if (state.get('registration_confirmed') or state.get('submission') == 'accepted'
            or state.get('remote_status') or state.get('version') or state.get('status') == 'complete'):
        return False
    detail = state.get('submission_diagnostic') or {}
    if detail.get('operation') != 'push' or detail.get('http_status') != 409:
        return False
    ref = state.get('requested_ref') or state.get('ref', '')
    if not REF_PATTERN.fullmatch(ref):
        return False
    match = re.search(r'The requested title "([^"]+)" is already in use by a dataset\.',
                      str(detail.get('reason', '')), re.IGNORECASE)
    return bool(match and match.group(1) == ref.split('/', 1)[1])


def _record_title_rejection(work_dir, state):
    state.update(status='failed', submission='not_created', registration_confirmed=False,
        failure_kind='dataset_title_conflict',
        message='데이터셋 이름 충돌로 실행 요청이 거절됐습니다. 생성 제한을 해제했습니다.',
        error='대본 데이터와 실행 작업의 이름이 겹쳐 캐글이 생성 요청을 거절했습니다. '
              '이름을 분리하도록 수정했으므로 미리듣기 또는 생성 버튼을 다시 눌러주세요.')
    state['diagnostic'] = deepcopy(state['submission_diagnostic'])
    state.pop('verify_registration', None)
    _save(work_dir, state)


def release_title_conflict(work_dir, credentials):
    """Repair a saved, explicitly rejected request; never submit or query a job."""
    if not credentials:
        return False
    with LOCK:
        if monitoring(work_dir):
            return False
        state = get_job(work_dir)
        if not state or kaggle_history.owner(state) != credentials['username'].lower():
            return False
        if state.get('kind') == 'parallel_cosy':
            if state.get('status') == 'complete':
                return False
            changed = False
            for engine in ('cosyvoice', 'cosyvoice3'):
                changed = release_title_conflict(Path(state['folder']) / engine, credentials) or changed
            children = {engine: get_job(Path(state['folder']) / engine)
                        for engine in ('cosyvoice', 'cosyvoice3')}
            live = any(is_running(Path(state['folder']) / engine)
                       or monitoring(Path(state['folder']) / engine) for engine in children)
            settled = all(child and child.get('status') in ('complete', 'failed')
                          for child in children.values())
            rejected = any(child and child.get('failure_kind') == 'dataset_title_conflict'
                           and is_dataset_title_conflict(child) for child in children.values())
            stale_parent = (state.get('status') in ACTIVE or state.get('status') == 'needs_check')
            changed = changed or (stale_parent and rejected and settled and not live)
            if changed:
                state['children'] = children
                if not live and settled:
                    state.update(status='failed',
                        message='이름 충돌로 거절된 작업의 생성 제한을 해제했습니다. 생성 버튼을 다시 누르세요.',
                        error='\n\n'.join(('코지2' if engine == 'cosyvoice' else '코지3') + ': ' + child['error']
                                         for engine, child in children.items() if child and child.get('error')))
                _save(work_dir, state)
            return changed
        if not is_dataset_title_conflict(state):
            return False
        if state.get('failure_kind') == 'dataset_title_conflict' and state.get('status') == 'failed':
            return False
        _record_title_rejection(work_dir, state)
        return True


def _state_path(work_dir):
    return Path(work_dir) / 'kaggle_job.json'


def _save(work_dir, state):
    with LOCK:
        state['updated'] = time.time()
        target = _state_path(work_dir)
        # If the user cleared their workspace, never recreate it from a worker.
        if not target.parent.is_dir():
            raise RuntimeError('현재 접속의 작업 공간이 지워졌습니다.')
        previous = get_job(work_dir)
        try:
            if previous and previous.get('folder') != state.get('folder'):
                kaggle_history.remember(work_dir, previous)
            kaggle_history.remember(work_dir, state)
            state.pop('history_warning', None)
        except (OSError, ValueError, TypeError):
            # A history-write problem must not discard a submitted GPU job.
            state['history_warning'] = '작업 이력 저장을 완료하지 못했습니다. 현재 작업 주소를 보관해주세요.'
        temp = target.with_name('kaggle_job.' + secrets.token_hex(4) + '.tmp')
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temp.chmod(0o600)
        os.replace(temp, target)


def get_job(work_dir):
    try:
        result = json.loads(_state_path(work_dir).read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else None
    except (OSError, ValueError):
        return None


def is_running(work_dir):
    state = get_job(work_dir)
    if state and state.get('kind') == 'parallel_cosy':
        return state.get('status') in ACTIVE or any(
            is_running(Path(state['folder']) / engine) or monitoring(Path(state['folder']) / engine)
            for engine in ('cosyvoice', 'cosyvoice3'))
    return bool(state and state.get('status') in ACTIVE)


def monitoring(work_dir):
    with LOCK:
        return str(Path(work_dir).resolve()) in WORKERS


def clear_job(work_dir):
    with LOCK:
        if is_running(work_dir) or monitoring(work_dir):
            raise ValueError('캐글 생성 중에는 작업을 지울 수 없습니다.')
        _state_path(work_dir).unlink(missing_ok=True)
        shutil.rmtree(Path(work_dir) / 'kaggle_jobs', ignore_errors=True)


def fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _make_script(notebook_name, job_id, signature):
    notebook = json.loads((ROOT / notebook_name).read_text(encoding='utf-8'))
    cells = [''.join(row['source']) for row in notebook['cells'] if row['cell_type'] == 'code']
    # First three code cells bootstrap, install and run the unchanged dual-GPU
    # runner. No API credentials are embedded in this private notebook/script.
    body = '\n'.join(cells[:3])
    prefix = 'import json, traceback\nfrom pathlib import Path\n'
    prefix += 'SITE_JOB_ID = ' + repr(job_id) + '\nSITE_SIGNATURE = ' + repr(signature) + '\n'
    prefix += 'site_error = ""\ntry:\n'
    suffix = '''
except BaseException as exc:
    site_error = str(exc)[-1500:]
    traceback.print_exc()
finally:
    result = dict(format='voice-studio-site-v1', job_id=SITE_JOB_ID,
                  signature=SITE_SIGNATURE, status='error', error=site_error)
    latest = Path('/kaggle/working/voice_studio_results/latest.json')
    if latest.is_file():
        info = json.loads(latest.read_text())
        folder = Path(info['folder'])
        progress = json.loads((folder / 'progress.json').read_text())
        plan = json.loads((folder / 'plan.json').read_text())
        result.update(status=info['status'], total=progress['total'], done=progress['done'],
                      folder=folder.relative_to('/kaggle/working').as_posix(),
                      parallel_shard=bool(plan.get('site_parallel_shard')),
                      errors=progress.get('errors', []))
    Path('/kaggle/working/voice_studio_site_result.json').write_text(
        json.dumps(result, ensure_ascii=False), encoding='utf-8')
    print('VOICE_STUDIO_SITE_RESULT', result['status'], flush=True)
'''
    return prefix + '\n'.join('    ' + line if line else '' for line in body.splitlines()) + suffix


def _launch(work_dir, state, credentials, submit=False):
    key = str(Path(work_dir).resolve())
    with LOCK:
        if key in WORKERS:
            return False
        def target():
            try:
                if submit and _submit(work_dir, state, credentials) is False:
                    return
                _monitor(work_dir, state, credentials)
            except Exception as exc:
                # An input-upload error occurred before any GPU request.
                # Only ambiguous submit / read / download failures need polling.
                before_submit = state.get('status') in ('uploading', 'dataset_ready', 'failed')
                _remember_error(state, exc)
                if getattr(exc, 'stage', '') in ('authentication', 'account_check'):
                    state.pop('account_authenticated', None)
                state.update(status='failed' if before_submit else 'needs_check', error=_error_message(exc),
                    message='대본 전송을 완료하지 못했습니다.' if before_submit else
                    '캐글 작업 상태를 다시 확인해주세요. 같은 작업을 자동으로 다시 제출하지 않았습니다.')
                try:
                    _save(work_dir, state)
                except (OSError, RuntimeError):
                    pass
            finally:
                with LOCK:
                    WORKERS.pop(key, None)
        thread = threading.Thread(target=target, daemon=True, name='voice-kaggle-monitor')
        WORKERS[key] = thread
        thread.start()
        return True


def start_job(work_dir, plan, archive, credentials, notebook_name, force=False, preview=False):
    if not credentials:
        raise ValueError('왼쪽 ‘캐글 연결’에서 먼저 내 계정을 연결해주세요.')
    with LOCK:
        release_title_conflict(work_dir, credentials)
        old = get_job(work_dir)
        if monitoring(work_dir) or is_running(work_dir):
            raise ValueError('현재 캐글 작업이 진행 중입니다. 완료 후 다음 작업을 시작해주세요.')
        if old and old.get('status') == 'needs_check':
            raise ValueError('이전 캐글 작업의 상태 확인이 필요합니다. ‘이전 작업 상태 확인·결과 받기’를 눌러주세요.')
        signature = fingerprint(plan)
        if not force and old and old.get('signature') == signature and old.get('status') == 'complete':
            result_key = 'clips_archive' if plan.get('site_parallel_shard') else 'full_audio'
            if Path(old.get('result', {}).get(result_key, '')).is_file():
                return old
        job_id = time.strftime('%Y%m%d%H%M%S', time.gmtime()) + '-' + secrets.token_hex(4)
        slug = 'voice-studio-' + job_id
        dataset_slug = 'voice-input-' + job_id
        folder = Path(work_dir) / 'kaggle_jobs' / job_id
        data, code = folder / 'input', folder / 'code'
        data.mkdir(parents=True, mode=0o700)
        code.mkdir(mode=0o700)
        resume = old.get('resume') if old and not force and old.get('signature') == signature else None
        input_file = data / 'project.zip'
        if resume and Path(resume).is_file():
            shutil.copyfile(resume, input_file)
        else:
            input_file.write_bytes(archive)
        input_file.chmod(0o600)
        ref = credentials['username'] + '/' + slug
        dataset_ref = credentials['username'] + '/' + dataset_slug
        metadata = dict(id=dataset_ref, title=dataset_slug, licenses=[{'name': 'other'}],
            description='Private voice studio input. Reference audio and dialogue for this job only.')
        (data / 'dataset-metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
        (code / 'voice_job.py').write_text(_make_script(notebook_name, job_id, signature), encoding='utf-8')
        metadata = dict(id=ref, title=slug, code_file='voice_job.py', language='python',
            kernel_type='script', is_private=True, enable_gpu=True, enable_internet=True,
            machine_shape='NvidiaTeslaT4', dataset_sources=[dataset_ref], competition_sources=[], kernel_sources=[])
        (code / 'kernel-metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
        state = dict(id=job_id, signature=signature, ref=ref, dataset_ref=dataset_ref, folder=str(folder),
            status='uploading', message='대본과 목소리 설정을 내 캐글 계정에 비공개로 전송합니다.',
            total=len(plan['items']), done=0, started=time.time(), preview=preview, error='',
            parallel_shard=bool(plan.get('site_parallel_shard')),
            submission='not_sent')
        state['engines'] = sorted({item['engine'] for item in plan['items']})
        state['label'] = ('미리듣기 · ' + str(plan['items'][0].get('speaker', '')) if preview
                          else f"전체 생성 · {len(plan['items'])}개 대사")
        if preview:
            state['preview_speaker'] = str(plan['items'][0].get('speaker', ''))
            state['preview_text'] = plan['items'][0]['text']
        _save(work_dir, state)
        _launch(work_dir, state, deepcopy(credentials), submit=True)
        return state


def _submit(work_dir, state, credentials):
    folder = Path(state['folder'])
    result = api_call(credentials, 'create_dataset', timeout=600, folder=str(folder / 'input'))
    state.update(status='dataset_ready', dataset_created=True,
        dataset_create_status=result.get('status', ''),
        dataset_requested_ref=state['dataset_ref'],
        message='캐글이 대본 생성 요청을 접수했습니다. 실제 데이터 주소와 준비 상태를 확인합니다.')
    _save(work_dir, state)
    _adopt_dataset_ref(state, result, credentials['username'])
    metadata_path = folder / 'code' / 'kernel-metadata.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    metadata['dataset_sources'] = [state['dataset_ref']]
    metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
    _save(work_dir, state)
    _wait_for_dataset(work_dir, state, credentials)
    # Persist the expected reference BEFORE the one non-idempotent GPU submit.
    state.update(status='submitting', submission='unknown', message='캐글 GPU 2개에 생성 작업을 요청합니다.')
    _save(work_dir, state)
    try:
        result = api_call(credentials, 'push', timeout=180, folder=str(folder / 'code'))
    except KaggleError as exc:
        # A conflict/timeout can follow an accepted push. Reconcile the exact
        # unique job through reads, never repeat the non-idempotent GPU submit.
        _remember_error(state, exc)
        state['submission_error'] = _error_message(exc)
        if is_dataset_title_conflict(state):
            _record_title_rejection(work_dir, state)
            return False
        state.update(status='checking',
            message='생성 요청 응답을 확인 중입니다. 실제 작업이 시작됐는지 자동으로 확인합니다.', error='')
        _save(work_dir, state)
        return True
    state.update(status='queued', submission='accepted', version=result.get('version'),
        message='캐글 GPU 배정을 기다리고 있습니다.', error='')
    _adopt_job_ref(state, result.get('url'), credentials['username'])
    _save(work_dir, state)
    return True


def _adopt_dataset_ref(state, result, username):
    """Keep Kaggle's creation reference, restricted to this user's unique input."""
    expected = state['dataset_requested_ref']
    expected_slug = expected.split('/')[-1]
    candidates = []
    for name in ('ref', 'url'):
        candidate = result.get(name)
        if not candidate:
            continue
        if not isinstance(candidate, str):
            raise KaggleError('캐글이 반환한 대본 주소 형식이 올바르지 않습니다.', operation='create_dataset')
        if candidate.startswith('https://'):
            parsed = urlparse(candidate)
            if (parsed.hostname not in ('www.kaggle.com', 'kaggle.com')
                    or not parsed.path.startswith('/datasets/')):
                raise KaggleError('캐글이 반환한 대본 주소를 확인하지 못했습니다.', operation='create_dataset')
            candidate = parsed.path.removeprefix('/datasets/').strip('/')
        if (not DATASET_REF_PATTERN.fullmatch(candidate)
                or candidate.split('/')[0].lower() != username.lower()
                or not re.fullmatch(re.escape(expected_slug) + r'(?:-\d+)?', candidate.split('/')[1])):
            raise KaggleError('캐글이 반환한 대본 주소가 현재 계정·작업과 다릅니다. GPU 요청을 보내지 않았습니다.',
                              operation='create_dataset')
        candidates.append(candidate)
    if len({candidate.lower() for candidate in candidates}) > 1:
        raise KaggleError('캐글이 반환한 두 대본 주소가 서로 다릅니다. GPU 요청을 보내지 않았습니다.',
                          operation='create_dataset')
    state['dataset_ref'] = candidates[0] if candidates else expected
    state['dataset_address_source'] = 'create_response' if candidates else 'request_metadata'


def _wait_for_dataset(work_dir, state, credentials):
    """Poll only the just-created dataset; a denied read never counts as ready.

    Creating a private dataset is asynchronous. One immediate 403/404 does not
    distinguish an unavailable dataset from a permanent access problem. Allow
    at most 120 seconds for these reads, within the existing 600-second upload
    deadline. Never recreate the dataset or submit a GPU job from recovery.
    """
    started = time.monotonic()
    deadline, access_deadline = started + 600, started + 120
    request_deadline = deadline
    last_error = None
    attempt = 0

    def expired():
        state['dataset_wait_seconds'] = elapsed = int(time.monotonic() - started)
        return KaggleError(
            f'대본 생성 접수 후 {elapsed}초 동안 {attempt}회 조회했지만 준비 상태를 확인하지 못했습니다. '
            f'대본 주소: {state["dataset_ref"]}. GPU 요청은 보내지 않았습니다. '
            '작업 기록의 ‘대본 데이터 열기’에서 같은 계정의 접근 가능 여부를 확인해주세요. '
            f'마지막 캐글 응답: {last_error or state.get("dataset_remote_status", "응답 없음")}',
            http_status=getattr(last_error, 'http_status', None), operation='dataset_status')

    while True:
        remaining = request_deadline - time.monotonic()
        if remaining <= 0:
            raise expired()
        attempt += 1
        state['dataset_check_attempts'] = attempt
        try:
            response = api_call(credentials, 'dataset_status', ref=state['dataset_ref'],
                timeout=min(60, remaining))
        except KaggleError as exc:
            now = time.monotonic()
            elapsed = int(now - started)
            state['dataset_wait_seconds'] = elapsed
            if last_error and now >= request_deadline:
                raise expired() from None
            retryable = (exc.stage == 'dataset_status'
                         and exc.http_status in (403, 404, 409, 429, 500, 502, 503, 504))
            if not retryable:
                raise
            last_error = exc
            if exc.http_status in (403, 404):
                request_deadline = min(request_deadline, access_deadline)
            if now >= request_deadline:
                raise expired() from None
            _remember_error(state, exc)
            state.update(error='', message=
                f'대본 생성 접수 완료 · 준비 상태 {attempt}회 확인 중 ({elapsed}초, 응답 {exc.http_status}). '
                + ('최대 120초 동안 접근 상태를 다시 확인합니다.' if exc.http_status in (403, 404)
                   else '대본을 다시 올리지 않고 잠시 후 조회합니다.'))
            _save(work_dir, state)
            threading.Event().wait(max(0, min(8, request_deadline - time.monotonic())))
            continue
        request_deadline, last_error = deadline, None
        state['dataset_wait_seconds'] = int(time.monotonic() - started)
        state['dataset_remote_status'] = status = str(response.get('status', '')).lower()
        state.pop('diagnostic', None)
        if status == 'ready':
            state.update(message='대본 준비 완료 · 캐글 GPU 요청을 준비합니다.', error='')
            _save(work_dir, state)
            return
        if status in ('error', 'failed') or time.monotonic() >= deadline:
            raise KaggleError('캐글 대본 업로드가 완료되지 않았습니다. 마지막 상태: ' + (status or '없음'),
                              operation='dataset_status')
        state.update(error='', message=
            f'캐글이 대본을 준비 중입니다 · {state["dataset_wait_seconds"]}초 · 상태: {status or "확인 중"}')
        _save(work_dir, state)
        threading.Event().wait(max(0, min(8, deadline - time.monotonic())))


def readable_logs(raw):
    try:
        entries = json.loads(raw)
        if isinstance(entries, list):
            return ''.join(str(row.get('data', row.get('text', ''))) if isinstance(row, dict) else str(row) for row in entries)
    except (ValueError, TypeError):
        pass
    return raw


def _monitor(work_dir, state, credentials):
    if is_dataset_title_conflict(state):
        _record_title_rejection(work_dir, state)
        return
    misses = 0
    account_checked = False
    if state.pop('verify_registration', False):
        _save(work_dir, state)
        outcome = _confirm_job(work_dir, state, credentials)
        if outcome != 'found':
            return
        account_checked = True
    while True:
        try:
            response = api_call(credentials, 'status', ref=state['ref'])
            misses = 0
        except KaggleError as exc:
            misses += 1
            _remember_error(state, exc)
            if (exc.http_status in (401, 403) and exc.stage == 'status'
                    and not account_checked):
                account_checked = True
                state.update(status='checking', error='',
                    message='기존 토큰으로 계정 인증과 작업 주소를 따로 확인합니다.')
                _save(work_dir, state)
                outcome = _confirm_job(work_dir, state, credentials)
                if outcome != 'found':
                    return
                threading.Event().wait(5)
                continue
            if misses >= 5 or exc.http_status in (401, 403):
                # Missing session != missing notebook. Confirm the job itself
                # is absent before allowing a new GPU submission.
                if exc.http_status in (404, 409):
                    previous_ref = state['ref']
                    outcome = _confirm_job(work_dir, state, credentials)
                    if outcome != 'found':
                        return
                    if state['ref'] != previous_ref:
                        misses = 0
                        continue
                raise
            state.update(status='checking', error='', message=
                '캐글의 일시적인 충돌을 확인하고 있습니다. 생성 요청은 중복 전송하지 않습니다.'
                if exc.http_status == 409 else '캐글 상태 응답을 기다립니다. 생성 요청은 다시 보내지 않습니다.')
            _save(work_dir, state)
            threading.Event().wait(20)
            continue
        status = response['status']
        state.update(submission='accepted', remote_status=status)
        state.pop('diagnostic', None)
        state.pop('submission_error', None)
        state.pop('submission_diagnostic', None)
        logs = readable_logs(response.get('logs', ''))
        counts = re.findall(r'✅\s+(\d+)/(\d+)\s+·\s+GPU', logs)
        if counts:
            state['done'], state['total'] = map(int, counts[-1])
        if logs:
            state['logs'] = logs[-10000:]
        state['log_error'] = response.get('log_error', '')
        if status in TERMINAL_REMOTE:
            state.update(status='receiving', error='', message='캐글 결과를 공유 사이트로 가져오고 있습니다.')
            _save(work_dir, state)
            attempts = 1 if status in CANCELED_REMOTE else 6
            for attempt in range(attempts):
                try:
                    _receive(work_dir, state, credentials, status, response.get('error', ''),
                        wait_for_files=attempt < attempts - 1)
                    return
                except KaggleError as exc:
                    _remember_error(state, exc)
                    if status in CANCELED_REMOTE:
                        state.update(status='cancelled', error='',
                            message='캐글에서 중지 완료를 확인했습니다. 종료된 실행의 결과 파일은 받지 못했습니다.',
                            output_error=_error_message(exc))
                        _save(work_dir, state)
                        return
                    # Kaggle can report completion before its output API is
                    # ready. Retry downloading; never rerun synthesis.
                    if not isinstance(exc, KaggleOutputPending) and exc.http_status not in (404, 409, 429, 500, 502, 503, 504):
                        raise
                    if attempt == 5:
                        if status != 'complete':
                            state.update(status='failed', error=response.get('error') or
                                '캐글 실행이 중단되어 결과 파일이 없습니다. 아래 실행 기록을 확인한 뒤 다시 생성해주세요.',
                                message='캐글 작업 종료를 확인했습니다. 다시 생성할 수 있습니다.')
                            _save(work_dir, state)
                            return
                        raise
                    state.update(message='캐글에서 결과 파일을 정리 중입니다. 결과 수신을 자동으로 다시 확인합니다.')
                    _save(work_dir, state)
                    threading.Event().wait(20)
        if status == 'cancel_requested':
            state.update(status='stopping', error='',
                message='캐글이 중지 요청을 처리 중입니다. 실제 종료 확인을 기다립니다.')
            _save(work_dir, state)
            threading.Event().wait(5)
            continue
        message = '캐글 GPU 배정을 기다리고 있습니다.'
        if status == 'running':
            if 'MP3 하나로 저장' in logs:
                message = '음성 생성 완료 · 대본 순서대로 MP3 하나로 합치고 있습니다.'
            elif counts:
                message = '두 GPU가 대사를 나누어 생성하고 있습니다.'
            elif '독립 모델 준비' in logs:
                message = 'GPU에 코지 음성 모델을 불러오고 있습니다.'
            else:
                message = '캐글에서 실행 환경·모델 파일을 준비하고 있습니다.'
        state.update(status='running' if status == 'running' else 'queued', error='', message=message)
        _save(work_dir, state)
        threading.Event().wait(15)


def _receive(work_dir, state, credentials, remote_status, remote_error, wait_for_files=False):
    output = Path(state['folder']) / 'output'
    api_call(credentials, 'pull', ref=state['ref'], folder=str(output), timeout=900)
    manifest = output / 'voice_studio_site_result.json'
    if not manifest.is_file():
        if wait_for_files:
            raise KaggleOutputPending('캐글 결과 목록에 완료 정보가 아직 반영되지 않았습니다.', operation='pull')
        if remote_status in CANCELED_REMOTE:
            state.update(status='cancelled', error='',
                message='캐글 중지 완료 · 이 실행은 결과 파일을 저장하기 전에 종료됐습니다.')
        else:
            state.update(status='failed', error=remote_error or
                '캐글 실행이 결과를 저장하기 전에 종료됐습니다. 아래 실행 기록과 캐글 작업 링크를 확인해주세요.')
        _save(work_dir, state)
        return
    info = json.loads(manifest.read_text(encoding='utf-8'))
    if info.get('format') != 'voice-studio-site-v1' or info.get('job_id') != state['id']:
        raise KaggleError('다른 작업의 결과가 반환되어 가져오기를 중단했습니다.')
    if state.get('signature') and info.get('signature') != state['signature']:
        raise KaggleError('대본 설정과 결과가 일치하지 않아 수신을 중단했습니다.')
    state['signature'] = info.get('signature', '')
    relative = info.get('folder', '')
    if relative and not re.fullmatch(r'voice_studio_results/[a-f0-9]+', relative):
        raise KaggleError('결과 폴더 형식이 올바르지 않습니다.')
    folder = output / relative
    resume = folder / 'resume.zip'
    if resume.is_file():
        state['resume'] = str(resume)
    state.update(total=info.get('total', state['total']), done=info.get('done', 0))
    if info.get('status') == 'complete' and info.get('parallel_shard'):
        if not resume.is_file():
            raise KaggleOutputPending('모델별 원본 음성 묶음이 아직 준비되지 않았습니다.', operation='pull')
        with zipfile.ZipFile(resume) as archive:
            if not {'plan.json', 'clip_index.json'}.issubset(archive.namelist()):
                raise KaggleError('원본 음성 순번 정보가 누락됐습니다. 결과 다시 확인을 눌러주세요.')
        state.update(status='complete', error='', parallel_shard=True,
            message='모델별 원본 음성 생성 완료 · 전체 대본 합치기 준비 완료',
            result={'clips_archive': str(resume)})
        _save(work_dir, state)
        return
    bundle = folder / 'complete_audio.zip'
    if info.get('status') == 'complete' and not bundle.is_file() and wait_for_files:
        raise KaggleOutputPending('캐글에서 완성 음성 파일을 준비할 때까지 기다리고 있습니다.', operation='pull')
    if info.get('status') == 'complete' and bundle.is_file():
        expected = {'full_audio.mp3', 'subtitles.srt', 'subtitles.vtt', 'timing.json', 'VOICE_ATTRIBUTION.txt'}
        with zipfile.ZipFile(bundle) as archive:
            if not expected.issubset(archive.namelist()):
                raise KaggleError('완성 음성 또는 자막이 누락되어 결과를 다시 받아야 합니다.')
            if sum(archive.getinfo(name).file_size for name in expected) > 1024**3:
                raise KaggleError('결과 파일이 사이트 수신 한도를 넘습니다.')
            for name in expected:
                target = folder / name
                with archive.open(name) as src, target.open('wb') as dst:
                    os.chmod(target, 0o600)
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        from .generation_jobs import valid_audio
        if not valid_audio(folder / 'full_audio.mp3'):
            raise KaggleError('받은 MP3를 읽을 수 없습니다. 결과 다시 받기를 눌러주세요.')
        state.update(status='complete', message='전체 대사를 합친 MP3 한 파일이 준비되었습니다.', error='',
            result={key: str(folder / name) for key, name in (
                ('full_audio', 'full_audio.mp3'), ('srt', 'subtitles.srt'),
                ('vtt', 'subtitles.vtt'), ('main_zip', 'complete_audio.zip'))})
    else:
        errors = '; '.join(str(row.get('message', '')) for row in info.get('errors', []) if isinstance(row, dict))
        if remote_status in CANCELED_REMOTE:
            state.update(status='cancelled', error='', message='캐글 중지 완료 · '
                + ('이어하기 파일을 보관했습니다.' if resume.is_file() else '저장된 이어하기 파일이 없습니다.'))
        else:
            state.update(status='failed', error=info.get('error') or errors or remote_error or
                '캐글 작업이 끝나기 전에 중단됐습니다. 저장된 완료분이 있으면 다음 생성에서 이어서 사용합니다.')
    _save(work_dir, state)


def reconnect(work_dir, credentials):
    release_title_conflict(work_dir, credentials)
    state = get_job(work_dir)
    if not state or not credentials:
        return False
    if state.get('kind') == 'parallel_cosy':
        from .kaggle_parallel import reconnect as reconnect_parallel
        return reconnect_parallel(work_dir, state, credentials)
    if state['ref'].split('/')[0].lower() != credentials['username'].lower():
        raise ValueError('이 작업을 만든 캐글 계정으로 연결해주세요.')
    if monitoring(work_dir):
        return False
    if state.get('failure_kind') == 'dataset_title_conflict' and state.get('status') == 'failed':
        return True
    if state.get('submission') == 'not_sent':
        # _submit persists submission='unknown' BEFORE calling kernels_push.
        # A stopped worker still marked not_sent never requested GPU work.
        state.update(status='failed', submission='not_sent',
            message='GPU 생성 요청 전 단계에서 중단됐습니다. 미리듣기 또는 생성 버튼을 다시 누를 수 있습니다.')
        _save(work_dir, state)
        return True
    diagnostic = state.get('diagnostic') or {}
    state['verify_registration'] = (
        state.get('status') == 'needs_check'
        and (state.get('submission') != 'accepted' or diagnostic.get('http_status') in (401, 403, 404, 409)))
    # Reconnect only polls/downloads. It NEVER repeats a dataset or kernel push.
    state.update(status='checking', error='', conflict_recovery_version=1, access_recovery_version=1,
        message='기존 캐글 작업 상태와 결과를 확인합니다.')
    state.pop('account_authenticated', None)
    _save(work_dir, state)
    return _launch(work_dir, state, deepcopy(credentials))


def recent_jobs(credentials, page_token=None):
    if not credentials:
        raise ValueError('내 캐글 계정을 먼저 연결해주세요.')
    return api_call(credentials, 'list_jobs', timeout=45, page_token=page_token)


def restore_saved(work_dir, key, credentials):
    """Select a preserved local result or resume reads; never submit GPU work."""
    if not credentials:
        raise ValueError('내 캐글 계정을 먼저 연결해주세요.')
    with LOCK:
        if is_running(work_dir) or monitoring(work_dir):
            raise ValueError('현재 작업 상태 확인이 끝난 뒤 다른 기록을 열어주세요.')
        state = kaggle_history.read(work_dir, key, credentials['username'])
        _save(work_dir, state)
        result_key = 'clips_archive' if state.get('parallel_shard') else 'full_audio'
        result = Path(state.get('result', {}).get(result_key, '')).resolve()
        if (state.get('status') == 'complete' and result.is_relative_to(Path(work_dir).resolve())
                and result.is_file()):
            return state
        reconnect(work_dir, credentials)
        return get_job(work_dir)


def restore(work_dir, reference, credentials):
    reference = reference.strip()
    if reference.startswith('https://'):
        parsed = urlparse(reference)
        if parsed.hostname not in ('www.kaggle.com', 'kaggle.com') or not parsed.path.startswith('/code/'):
            raise ValueError('내 캐글 작업 주소를 입력해주세요.')
        reference = parsed.path.removeprefix('/code/').strip('/')
    reference = reference.split('?')[0].rstrip('/')
    if not REF_PATTERN.fullmatch(reference):
        raise ValueError('이 사이트에서 만든 캐글 작업 주소를 입력해주세요. /code/사용자/voice-studio-… 형식입니다.')
    if reference.split('/')[0].lower() != credentials['username'].lower():
        raise ValueError('연결한 내 캐글 계정의 작업만 불러올 수 있습니다.')
    current = get_job(work_dir)
    if current and current.get('ref') == reference:
        return reconnect(work_dir, credentials)
    if is_running(work_dir) or monitoring(work_dir):
        raise ValueError('현재 캐글 작업이 끝난 뒤 불러와주세요.')
    job_id = reference.split('/voice-studio-', 1)[1]
    folder = Path(work_dir) / 'kaggle_jobs' / job_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (folder / 'site_job_state.json').is_file():
        return restore_saved(work_dir, job_id, credentials)
    state = dict(id=job_id, ref=reference, folder=str(folder), signature='', total=0,
        done=0, status='checking', started=time.time(), error='', message='이전 캐글 작업을 불러옵니다.')
    _save(work_dir, state)
    _launch(work_dir, state, deepcopy(credentials))
