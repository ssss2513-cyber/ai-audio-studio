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

ROOT = Path(__file__).resolve().parent.parent
LOCK = threading.RLock()
WORKERS = {}
ACTIVE = {'uploading', 'dataset_ready', 'submitting', 'queued', 'running', 'receiving', 'checking'}
TERMINAL_REMOTE = {'complete', 'error', 'failed', 'canceled', 'cancelled'}
REF_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+/voice-studio-[a-z0-9-]+$')
OPERATION_LABELS = {'auth': '계정 연결', 'create_dataset': '대본 전송',
    'dataset_status': '대본 준비 확인', 'push': '생성 요청',
    'status': '실행 상태 확인', 'kernel_info': '작업 등록 확인', 'pull': '결과 받기',
    'inspect_job': '기존 계정·작업 확인', 'authentication': '인증정보 확인',
    'account_check': '내 계정 접근 확인', 'job_lookup': '내 작업 주소 확인'}


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
        state.update(status='failed', submission='not_created',
            message='계정 인증은 정상이며, 캐글에 생성 작업이 등록되지 않은 것을 확인했습니다.',
            error=('토큰 인증은 통과했습니다. 기존 생성 작업이 없어 생성 제한을 해제했습니다. '
                   '위의 생성 또는 미리듣기 버튼을 다시 눌러주세요.'
                   + ('\n\n최초 생성 요청 오류: ' + reason if reason else '')))
        _save(work_dir, state)
        return 'missing'
    if info.get('exists'):
        _adopt_job_ref(state, info.get('ref'), credentials['username'])
        _save(work_dir, state)
        return 'found'
    state.update(status='needs_check',
        message='계정 인증은 정상입니다. 기존 작업의 접근 상태를 확인해야 합니다.',
        error='기존 토큰으로 계정 인증은 통과했지만 해당 작업에 접근하지 못했습니다. '
              '토큰을 다시 발급받지 마세요. 아래 ‘내 캐글 작업 열기’에서 작업 주소를 확인해주세요.')
    state['diagnostic'] = dict(operation='inspect_job', stage='kernel_info',
        http_status=info.get('http_status'), reason=info.get('reason', ''))
    _save(work_dir, state)
    return 'unknown'


def _state_path(work_dir):
    return Path(work_dir) / 'kaggle_job.json'


def _save(work_dir, state):
    state['updated'] = time.time()
    target = _state_path(work_dir)
    # If the user cleared their workspace, never recreate it from a worker.
    if not target.parent.is_dir():
        raise RuntimeError('현재 접속의 작업 공간이 지워졌습니다.')
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
                if submit:
                    _submit(work_dir, state, credentials)
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
        old = get_job(work_dir)
        if monitoring(work_dir) or is_running(work_dir):
            raise ValueError('현재 캐글 작업이 진행 중입니다. 완료 후 다음 작업을 시작해주세요.')
        if old and old.get('status') == 'needs_check':
            raise ValueError('캐글 작업 상태를 확인해야 합니다. 아래 ‘상태·결과 다시 확인’을 누르면 진행 중인 작업은 이어받고, 등록되지 않은 작업은 생성 제한을 해제합니다.')
        signature = fingerprint(plan)
        if not force and old and old.get('signature') == signature and old.get('status') == 'complete':
            result_key = 'clips_archive' if plan.get('site_parallel_shard') else 'full_audio'
            if Path(old.get('result', {}).get(result_key, '')).is_file():
                return old
        job_id = time.strftime('%Y%m%d%H%M%S', time.gmtime()) + '-' + secrets.token_hex(4)
        slug = 'voice-studio-' + job_id
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
        metadata = dict(id=ref, title=slug, licenses=[{'name': 'other'}],
            description='Private voice studio input. Reference audio and dialogue for this job only.')
        (data / 'dataset-metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
        (code / 'voice_job.py').write_text(_make_script(notebook_name, job_id, signature), encoding='utf-8')
        metadata = dict(id=ref, title=slug, code_file='voice_job.py', language='python',
            kernel_type='script', is_private=True, enable_gpu=True, enable_internet=True,
            machine_shape='NvidiaTeslaT4', dataset_sources=[ref], competition_sources=[], kernel_sources=[])
        (code / 'kernel-metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
        state = dict(id=job_id, signature=signature, ref=ref, folder=str(folder),
            status='uploading', message='대본과 목소리 설정을 내 캐글 계정에 비공개로 전송합니다.',
            total=len(plan['items']), done=0, started=time.time(), preview=preview, error='',
            parallel_shard=bool(plan.get('site_parallel_shard')),
            submission='not_sent')
        _save(work_dir, state)
        _launch(work_dir, state, deepcopy(credentials), submit=True)
        return state


def _submit(work_dir, state, credentials):
    folder = Path(state['folder'])
    api_call(credentials, 'create_dataset', timeout=600, folder=str(folder / 'input'))
    state.update(status='dataset_ready', message='캐글에서 대본 업로드를 마무리하고 있습니다.')
    _save(work_dir, state)
    deadline = time.monotonic() + 600
    while True:
        response = api_call(credentials, 'dataset_status', ref=state['ref'])
        status = response['status'].lower()
        if status == 'ready':
            break
        if status in ('error', 'failed') or time.monotonic() > deadline:
            state.update(status='failed', error='캐글 대본 업로드가 완료되지 않았습니다: ' + status)
            _save(work_dir, state)
            raise KaggleError(state['error'])
        threading.Event().wait(8)
    # Persist the expected reference BEFORE the one non-idempotent GPU submit.
    state.update(status='submitting', submission='unknown', message='캐글 GPU 2개에 생성 작업을 요청합니다.')
    _save(work_dir, state)
    try:
        result = api_call(credentials, 'push', timeout=180, folder=str(folder / 'code'))
    except KaggleError as exc:
        # A conflict/timeout can follow an accepted push. Reconcile the exact
        # unique job through reads, never repeat the non-idempotent GPU submit.
        _remember_error(state, exc)
        state.update(status='checking', submission_error=_error_message(exc),
            message='생성 요청 응답을 확인 중입니다. 실제 작업이 시작됐는지 자동으로 확인합니다.', error='')
        _save(work_dir, state)
        return
    state.update(status='queued', submission='accepted', version=result.get('version'),
        message='캐글 GPU 배정을 기다리고 있습니다.', error='')
    _adopt_job_ref(state, result.get('url'), credentials['username'])
    _save(work_dir, state)


def readable_logs(raw):
    try:
        entries = json.loads(raw)
        if isinstance(entries, list):
            return ''.join(str(row.get('data', row.get('text', ''))) if isinstance(row, dict) else str(row) for row in entries)
    except (ValueError, TypeError):
        pass
    return raw


def _monitor(work_dir, state, credentials):
    misses = 0
    account_checked = False
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
        state['logs'] = logs[-10000:]
        if status in TERMINAL_REMOTE:
            state.update(status='receiving', error='', message='캐글 결과를 공유 사이트로 가져오고 있습니다.')
            _save(work_dir, state)
            for attempt in range(6):
                try:
                    _receive(work_dir, state, credentials, status, response.get('error', ''),
                        wait_for_files=attempt < 5)
                    return
                except KaggleError as exc:
                    _remember_error(state, exc)
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
        state.update(status='failed', error=info.get('error') or errors or remote_error or
            '캐글 작업이 끝나기 전에 중단됐습니다. 저장된 완료분이 있으면 다음 생성에서 이어서 사용합니다.')
    _save(work_dir, state)


def reconnect(work_dir, credentials):
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
    # Reconnect only polls/downloads. It NEVER repeats a dataset or kernel push.
    state.update(status='checking', error='', conflict_recovery_version=1, access_recovery_version=1,
        message='기존 캐글 작업 상태와 결과를 확인합니다.')
    state.pop('account_authenticated', None)
    _save(work_dir, state)
    return _launch(work_dir, state, deepcopy(credentials))


def restore(work_dir, reference, credentials):
    reference = reference.strip().removeprefix('https://www.kaggle.com/code/').strip('/')
    reference = reference.split('?')[0].rstrip('/')
    if not REF_PATTERN.fullmatch(reference):
        raise ValueError('이 사이트에서 만든 캐글 작업 주소를 입력해주세요. /code/사용자/voice-studio-… 형식입니다.')
    if reference.split('/')[0].lower() != credentials['username'].lower():
        raise ValueError('연결한 내 캐글 계정의 작업만 불러올 수 있습니다.')
    if is_running(work_dir) or monitoring(work_dir):
        raise ValueError('현재 캐글 작업이 끝난 뒤 불러와주세요.')
    job_id = reference.split('/voice-studio-', 1)[1]
    folder = Path(work_dir) / 'kaggle_jobs' / job_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = dict(id=job_id, ref=reference, folder=str(folder), signature='', total=0,
        done=0, status='checking', started=time.time(), error='', message='이전 캐글 작업을 불러옵니다.')
    _save(work_dir, state)
    _launch(work_dir, state, deepcopy(credentials))
