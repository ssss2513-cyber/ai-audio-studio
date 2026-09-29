"""Self-contained GPT-SoVITS v4 Colab runner; upstream model code stays unmodified."""
import argparse
import base64
from collections import OrderedDict
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import urllib.parse
import urllib.error
import zipfile

SOURCE_REVISION = '48b1a0169a28582a8984402f82cf438d3bfa6aca'
MODEL_REVISION = '336b2ec4e8d4ac74740798dd40af44e74659ecaf'
ROOT = Path('/content/ai_voice_sovits_v4_1')
SOURCE = ROOT / 'GPT-SoVITS'
PYTHON = ROOT / 'venv/bin/python'
MODEL = SOURCE / 'GPT_SoVITS/pretrained_models'
STATE = ROOT / 'state.json'
SERVICE = 'ai-voice-studio-gpt-sovits'
SERVER_VERSION = '2.8.6'
REFERENCE_CACHE_CAPABILITY = 'reference_cache_v284'
NLTK_DATA_REVISION = '550b6625bcef1f2abff2ff770a5a0d272c9c6b2a'
# Official nltk_data/index.xml at the revision above supplies sizes and SHA-256.
NLTK_PACKAGES = (
    ('taggers', 'averaged_perceptron_tagger', 2526731,
     'e1f13cf2532daadfd6f3bc481a49859f0b8ea6432ccdcd83e6a49a5f19008de9'),
    ('taggers', 'averaged_perceptron_tagger_eng', 1539115,
     '6025f530624335c67d6547d44757b357b4e79bae030a0383e9887a92c1718f0b'),
    ('corpora', 'cmudict', 896069,
     'd07cca47fd72ad32ea9d8ad1219f85301eeaf4568f8b6b73747506a71fb5afd6'),
    ('tokenizers', 'punkt', 13905355,
     '51c3078994aeaf650bfc8e028be4fb42b4a0d177d41c012b6a983979653660ec'),
    ('tokenizers', 'punkt_tab', 4319076,
     'e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106'),
)


class _NoDictionaryRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('발음 사전 다운로드 주소가 변경되었습니다. 최신 코랩 파일을 사용해주세요.')


def prepare_nltk_data():
    """Install fixed, verified official archives without the remote NLTK index.

    This follows NLTK's manual installation route. Proxy and TLS settings are
    preserved; no NLTK security opt-outs or dependency downgrades are applied.
    """
    data_root = ROOT / 'nltk_data'
    data_root.mkdir(parents=True, exist_ok=True)
    os.environ['NLTK_DATA'] = str(data_root)
    opener = urllib.request.build_opener(_NoDictionaryRedirect())
    for number, (subdir, name, size, digest) in enumerate(NLTK_PACKAGES, 1):
        destination = data_root / subdir
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / name
        archive_path = destination / (name + '.zip')
        archive_bytes = archive_path.read_bytes() if archive_path.is_file() else b''
        archive_valid = (len(archive_bytes) == size
                         and hashlib.sha256(archive_bytes).hexdigest() == digest)
        marker = data_root / (name + '.sha256')
        if (archive_valid and target.is_dir() and marker.is_file()
                and marker.read_text().strip() == digest):
            print(f'✅ 발음 사전 {number}/{len(NLTK_PACKAGES)} 준비됨: {name}', flush=True)
            continue
        print(f'▶ 발음 사전 {number}/{len(NLTK_PACKAGES)} 다운로드: {name}', flush=True)
        url = (f'https://raw.githubusercontent.com/nltk/nltk_data/{NLTK_DATA_REVISION}'
               f'/packages/{subdir}/{name}.zip')
        if not archive_valid:
            with opener.open(url, timeout=60) as response:
                archive_bytes = response.read(size + 1)
        if len(archive_bytes) != size or hashlib.sha256(archive_bytes).hexdigest() != digest:
            raise RuntimeError(f'발음 사전 파일 검증 실패: {name}. 다운로드를 다시 확인해주세요.')
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = archive.infolist()
            if sum(member.file_size for member in members) > 100 * 1024 * 1024:
                raise RuntimeError(f'발음 사전 압축 크기 오류: {name}')
            for member in members:
                path = PurePosixPath(member.filename)
                if (path.is_absolute() or '..' in path.parts or not path.parts
                        or path.parts[0] != name or '\\' in member.filename
                        or stat.S_ISLNK(member.external_attr >> 16)):
                    raise RuntimeError(f'발음 사전 압축 경로 오류: {name}')
            with tempfile.TemporaryDirectory(prefix='nltk-', dir=data_root) as stage:
                archive.extractall(stage)
                extracted = Path(stage) / name
                if not extracted.is_dir():
                    raise RuntimeError(f'발음 사전 압축 내용 오류: {name}')
                if target.is_dir():
                    shutil.rmtree(target)
                extracted.replace(target)
        # g2p_en checks for the .zip itself before deciding whether to download.
        temporary_archive = archive_path.with_suffix('.zip.tmp')
        temporary_archive.write_bytes(archive_bytes)
        temporary_archive.replace(archive_path)
        marker.write_text(digest, encoding='ascii')
        print(f'✅ 발음 사전 {number}/{len(NLTK_PACKAGES)} 설치 완료: {name}', flush=True)


def run(args, label):
    print('\n▶ ' + label, flush=True)
    with (ROOT / 'setup.log').open('a', encoding='utf-8') as log:
        log.write('\n▶ ' + label + '\n')
        log.flush()
        process = subprocess.Popen([str(x) for x in args], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            for line in process.stdout:
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
            code = process.wait()
        except BaseException:
            process.terminate()
            raise
    if code:
        raise RuntimeError(f'{label} 실패 (코드 {code}). 바로 위 오류를 확인해주세요.')


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def server_status(base):
    with urllib.request.urlopen(base + '/health', timeout=5) as response:
        status = json.load(response)
    if not isinstance(status, dict):
        raise ValueError('음성 서버 상태 응답 형식이 올바르지 않습니다.')
    return status


def compatible_server(status):
    return (status.get('service') == SERVICE and status.get('ready') is True
            and status.get('model_version') == 'v4' and status.get('sample_rate') == 48000
            and status.get('api_version') == 1)


def health(base, require_current=False):
    # API compatibility determines connectivity. An exact release number must
    # never make an otherwise usable v4 server appear to have stopped.
    status = server_status(base)
    return (compatible_server(status) and (not require_current
            or REFERENCE_CACHE_CAPABILITY in status.get('capabilities', [])))


def connection_status(base):
    try:
        status = server_status(base)
    except urllib.error.HTTPError as exc:
        return {'ready': False, 'detail': f'서버 상태 확인: HTTP {exc.code}. 저장된 연결 주소의 서버가 준비되지 않았습니다.'}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {'ready': False, 'detail': '서버 상태 요청에 응답이 없습니다. 아래 실행 상태와 마지막 로그를 확인해주세요.'}
    except ValueError:
        return {'ready': False, 'detail': '서버가 정상적인 상태 정보를 보내지 않았습니다.'}
    if not compatible_server(status):
        return {'ready': False, 'detail': '응답은 있지만 GPT-SoVITS v4 · 48kHz API가 준비된 상태가 아닙니다.'}
    return {'ready': True, 'server_version': str(status.get('server_version') or '이전 버전'),
            'reference_cache': REFERENCE_CACHE_CAPABILITY in status.get('capabilities', []),
            'detail': 'GPT-SoVITS v4 · 48kHz 서버 응답 정상'}


def print_connection_diagnostics(state, report):
    """Show evidence without exposing the private connection token."""
    print('\n【연결 확인 결과】\n' + report['detail'], flush=True)
    pid = state.get('pid')
    if isinstance(pid, int) and pid > 1:
        try:
            process_state = (Path('/proc') / str(pid) / 'stat').read_text().split(') ', 1)[1][0]
            running = process_state not in ('Z', 'X')
        except (OSError, IndexError):
            running = False
        print('기록된 서버 프로세스: ' + ('실행 중이나 준비 응답을 받지 못함' if running else
              '종료됨 · 음성 서버가 실행되지 않는 상태'), flush=True)
    logfile = ROOT / 'server.log'
    if logfile.is_file():
        with logfile.open('rb') as handle:
            handle.seek(max(0, logfile.stat().st_size - 8000))
            tail = '\n'.join(handle.read().decode('utf-8', errors='replace').splitlines()[-25:])
        tail = re.sub(r'/v1/[A-Za-z0-9_-]+', '/v1/[연결코드 숨김]', tail)
        print('\n【음성 서버 마지막 로그】\n' + tail, flush=True)


def write_state(state):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=ROOT, delete=False) as handle:
        json.dump(state, handle)
        temporary = Path(handle.name)
    temporary.chmod(0o600)
    temporary.replace(STATE)


def worker_state(state):
    """Do not confuse a recycled PID with this notebook's model worker."""
    pid = state.get('pid')
    if not isinstance(pid, int) or pid <= 1:
        return 'missing'
    proc = Path('/proc') / str(pid)
    if not proc.exists():
        return 'stopped'
    try:
        if (proc / 'stat').read_text().split(') ', 1)[1][0] in ('Z', 'X'):
            return 'stopped'
        args = [part.decode(errors='replace') for part in (proc / 'cmdline').read_bytes().split(b'\0') if part]
        owned = (args and Path(args[0]).absolute() == PYTHON.absolute()
                 and str(Path(__file__).resolve()) in args
                 and '--serve' in args and '--port' in args
                 and args[args.index('--port') + 1] == str(state.get('port')))
        return 'running' if owned else 'unknown'
    except (OSError, IndexError):
        return 'stopped' if not proc.exists() else 'unknown'


def wait_for_server(state, timeout=600):
    started = time.monotonic()
    next_notice = 0
    while time.monotonic() - started < timeout:
        report = connection_status(state['base'])
        if report['ready']:
            latest = read_state()
            if latest.get('pid') == state.get('pid') and latest.get('base') == state.get('base'):
                state = latest
            state['status'] = 'ready'
            write_state(state)
            print(f'✅ GPT 음성 서버 준비 완료 · {report["server_version"]} · 48kHz', flush=True)
            return state
        process = worker_state(state)
        if process != 'running':
            print_connection_diagnostics(state, report)
            raise RuntimeError('음성 서버가 준비되기 전에 종료되었거나 실행 정보를 확인할 수 없습니다. '
                               '위 마지막 로그를 확인해주세요. 자동 재시작을 반복하지 않았습니다.')
        elapsed = int(time.monotonic() - started)
        if elapsed >= next_notice:
            label = '모델을 GPU에 불러오는 중' if state.get('status') == 'starting' else '음성 서버의 준비 응답을 기다리는 중'
            print(f'{label}… {elapsed}초 · 패키지 재설치 없음', flush=True)
            next_notice = elapsed + 15
        time.sleep(2)
    print_connection_diagnostics(state, report)
    # A readiness timeout must not itself shut down a model that just loaded.
    # Its recorded detached worker can be checked by running cell 4 again.
    raise RuntimeError('모델 준비 확인 시간이 초과되었습니다. 서버를 강제로 종료하지 않았습니다. '
                       '위 마지막 로그를 확인하고, 로딩 중이면 잠시 뒤 4번으로 상태를 다시 확인해주세요.')


def start_server():
    """Start the already-installed model exactly once, without installing anything."""
    import fcntl
    if not ROOT.is_dir():
        raise RuntimeError('이 런타임에는 설치된 GPT 환경이 없습니다. 1번을 완료한 같은 코랩에서 실행해주세요.')
    with (ROOT / 'startup.lock').open('a+') as startup_lock:
        try:
            fcntl.flock(startup_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('다른 셀에서 서버를 시작하고 있습니다. 해당 셀의 준비 완료를 기다려주세요.') from None
        state = read_state()
        if state.get('base') and connection_status(state['base'])['ready']:
            return state
        process = worker_state(state)
        if process == 'running':
            print('이미 시작 중인 서버를 기다립니다. 모델을 중복으로 실행하지 않습니다.', flush=True)
            return wait_for_server(state)
        if process == 'unknown':
            raise RuntimeError('기록된 실행 정보를 확인할 수 없어 중복 실행을 막았습니다. 런타임 상태를 확인해주세요.')
        required = [PYTHON, ROOT / 'tts-v4.json', MODEL / 's1v3.ckpt',
                    MODEL / 'gsv-v4-pretrained/s2Gv4.pth', MODEL / 'gsv-v4-pretrained/vocoder.pth']
        if any(not path.is_file() for path in required):
            raise RuntimeError('이 런타임에는 설치 파일이나 모델이 빠져 있습니다. '
                               '1번을 완료한 같은 런타임인지 확인해주세요. 자동 재설치하지 않았습니다.')
        env = os.environ.copy()
        env['MPLBACKEND'] = 'Agg'
        # Cell 4 does not call prepare_nltk_data(), so restore its data path.
        env['NLTK_DATA'] = str(ROOT / 'nltk_data')
        env.update(USE_TF='0', TRANSFORMERS_NO_TF='1', TOKENIZERS_PARALLELISM='false',
                   SOVITS_ACCESS_TOKEN=secrets.token_urlsafe(24))
        env['PYTHONPATH'] = os.pathsep.join([str(SOURCE), str(SOURCE / 'GPT_SoVITS')])
        libs = [str(path) for path in (ROOT / 'venv/lib/python3.10/site-packages/nvidia').glob('*/lib')]
        env['LD_LIBRARY_PATH'] = os.pathsep.join(libs + [env.get('LD_LIBRARY_PATH', '')])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}/v1/{env["SOVITS_ACCESS_TOKEN"]}'
        with (ROOT / 'server.log').open('a', encoding='utf-8') as log:
            log.write(f'\n▶ GPT 서버 v{SERVER_VERSION} 시작 · 기존 설치 사용\n')
            log.flush()
            worker = subprocess.Popen(
                [str(PYTHON), '-u', str(Path(__file__).resolve()), '--serve', '--port', str(port)],
                cwd=SOURCE, env=env, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
        # Record the PID before waiting: a stopped notebook cell must not lose
        # the worker and start a second GPU copy the next time it is run.
        state = {'base': base, 'port': port, 'pid': worker.pid, 'status': 'starting',
                 'model_version': 'v4', 'sample_rate': 48000, 'server_version': SERVER_VERSION}
        write_state(state)
        return wait_for_server(state)


def setup():
    if not Path('/content').is_dir():
        raise RuntimeError('이 파일은 Google Colab에서 실행해주세요.')
    ROOT.mkdir(parents=True, exist_ok=True)
    state = read_state()
    existing = connection_status(state['base']) if state.get('base') else {'ready': False}
    if existing['ready']:
        print(f'✅ 기존 GPT-SoVITS v4 서버 준비 완료 · {existing["server_version"]} · 48kHz', flush=True)
        if not existing['reference_cache']:
            print('기존 서버로 연결할 수 있습니다. 참고 음성 재사용 기능은 아직 적용되지 않은 서버입니다. '
                  '속도 개선 서버로 전환하려면 생성이 끝난 뒤 런타임을 다시 시작하고 1번을 실행해주세요.', flush=True)
        return
    if worker_state(state) == 'running':
        wait_for_server(state)
        return
    if worker_state(state) == 'unknown':
        raise RuntimeError('기록된 서버의 실행 정보를 확인할 수 없습니다. 다른 서버와 중복 실행하지 않도록 중단했습니다.')
    if not shutil.which('nvidia-smi'):
        raise RuntimeError('런타임 → 런타임 유형 변경 → T4 GPU를 선택해주세요.')
    run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'], 'GPU 확인')
    run(['apt-get', 'update', '-qq'], '패키지 목록 갱신')
    run(['apt-get', 'install', '-y', '-qq', 'ffmpeg', 'git', 'build-essential',
         'cmake', 'libopencc-dev', 'libsndfile1'], '오디오 도구 설치')
    if not PYTHON.is_file():
        run([sys.executable, '-m', 'pip', 'install', 'uv'], '독립 Python 환경 준비')
        run([sys.executable, '-m', 'uv', 'python', 'install', '3.10'], 'Python 3.10 설치')
        run([sys.executable, '-m', 'uv', 'venv', '--python', '3.10', '--seed', ROOT / 'venv'], '환경 생성')
    if not (SOURCE / '.git').is_dir():
        run(['git', 'clone', '--filter=blob:none', 'https://github.com/RVC-Boss/GPT-SoVITS.git', SOURCE], '공식 소스 다운로드')
    run(['git', '-C', SOURCE, 'checkout', '--detach', SOURCE_REVISION], '확인한 공식 소스 선택')
    # Pin the CUDA pair and Transformers; keep the upstream Korean pronunciation packages.
    pins = ROOT / 'constraints.txt'
    pins.write_text('torch==2.6.0\ntorchaudio==2.6.0\ntorchvision==0.21.0\n'
                    'numpy==1.26.4\ntransformers==4.57.3\nhuggingface-hub==0.36.0\n')
    stamp = hashlib.sha256((SOURCE / 'requirements.txt').read_bytes() + pins.read_bytes()).hexdigest()
    marker = ROOT / 'dependencies.ok'
    if not marker.is_file() or marker.read_text() != stamp:
        run([PYTHON, '-m', 'pip', 'install', 'pip<26', 'setuptools<81', 'wheel'], '설치 도구 준비')
        run([PYTHON, '-m', 'pip', 'install', 'torch==2.6.0', 'torchaudio==2.6.0', 'torchvision==0.21.0',
             '--index-url', 'https://download.pytorch.org/whl/cu124'], 'GPU 패키지 설치')
        run([PYTHON, '-m', 'pip', 'install', '-r', SOURCE / 'requirements.txt', '-c', pins,
             'soundfile', 'requests', 'huggingface-hub==0.36.0'], '공식 의존성 설치 (한국어 발음 변환 포함)')
        run([PYTHON, '-m', 'pip', 'check'], '의존성 확인')
        marker.write_text(stamp)
    prepare_nltk_data()
    run([PYTHON, '-c',
         "import nltk; from nltk.corpus import cmudict; "
         "assert cmudict.words(); "
         "assert nltk.pos_tag(['voice', 'studio']); "
         "assert nltk.word_tokenize('Voice studio is ready.'); "
         "print('발음 사전 읽기 정상')"], '발음 사전 읽기 확인')
    files = ['s1v3.ckpt', 'gsv-v4-pretrained/s2Gv4.pth', 'gsv-v4-pretrained/vocoder.pth',
             'chinese-roberta-wwm-ext-large/*', 'chinese-hubert-base/*']
    run([PYTHON, '-c', 'from huggingface_hub import snapshot_download; '
         f'snapshot_download("lj1995/GPT-SoVITS", revision={MODEL_REVISION!r}, '
         f'local_dir={str(MODEL)!r}, allow_patterns={files!r})'], 'v4 모델 및 48kHz 보코더 다운로드')
    for name in files[:3]:
        if not (MODEL / name).is_file():
            raise RuntimeError('모델 다운로드 누락: ' + name)
    config = {'custom': {
        'device': 'cuda', 'is_half': False, 'version': 'v4',
        't2s_weights_path': str(MODEL / 's1v3.ckpt'),
        'vits_weights_path': str(MODEL / 'gsv-v4-pretrained/s2Gv4.pth'),
        'bert_base_path': str(MODEL / 'chinese-roberta-wwm-ext-large'),
        'cnhuhbert_base_path': str(MODEL / 'chinese-hubert-base'),
    }}
    (ROOT / 'tts-v4.json').write_text(json.dumps(config))
    start_server()


def create_app(pipeline, access_token):
    """Only this wrapper is public. Model loading and model-switch endpoints are not exposed."""
    import numpy as np
    import soundfile as sf
    from fastapi import Body, FastAPI, HTTPException, Response
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    lock = threading.Lock()
    # Keep the pinned upstream's reference cache per voice, including its raw
    # audio and text features. Never share mutable lists between voice entries.
    # Eight references bound extra GPU memory and temporary files per server.
    reference_cache = OrderedDict()

    def copy_prompt_cache(cache):
        return {key: list(value) if isinstance(value, list) else value
                for key, value in cache.items()}

    empty_prompt_cache = copy_prompt_cache(pipeline.prompt_cache)

    def authorize(token):
        if not secrets.compare_digest(token, access_token):
            raise HTTPException(404, '연결 주소를 확인해주세요.')

    @app.get('/v1/{token}/health')
    def status(token: str):
        authorize(token)
        return {'service': SERVICE, 'ready': True, 'model_version': pipeline.configs.version,
                'sample_rate': pipeline.vocoder_configs['sr'], 'korean_g2p': True, 'api_version': 1,
                'server_version': SERVER_VERSION, 'capabilities': [REFERENCE_CACHE_CAPABILITY],
                'busy': lock.locked()}

    @app.post('/v1/{token}/tts')
    def generate(token: str, req: dict = Body(...)):
        authorize(token)
        text = str(req.get('text') or '').strip()
        prompt = str(req.get('prompt_text') or '').strip()
        if not text or not prompt or len(text) > 2000 or len(prompt) > 2000:
            raise HTTPException(422, '생성 대사와 참조 음성의 정확한 대사를 입력해주세요 (각 2000자 이내).')
        try:
            params = {}
            for key, default, lo, hi in [('speed_factor', 1., .5, 2.), ('temperature', 1., .1, 1.),
                                         ('top_p', 1., .05, 1.), ('top_k', 15, 1, 100),
                                         ('fragment_interval', .3, .01, 1.)]:
                value = float(req.get(key, default))
                if not math.isfinite(value) or not lo <= value <= hi:
                    raise ValueError(f'{key} 범위: {lo}~{hi}')
                params[key] = int(value) if key == 'top_k' else value
            steps = int(req.get('sample_steps', 32))
            if steps not in (4, 8, 16, 32):
                raise ValueError('v4 생성 단계는 4, 8, 16, 32 중에서 선택해주세요.')
            split = req.get('text_split_method', 'cut5')
            if split not in ('cut0', 'cut1', 'cut2', 'cut3', 'cut4', 'cut5'):
                raise ValueError('문장 분할 설정을 확인해주세요.')
            langs = {'auto', 'auto_yue', 'en', 'ko', 'ja', 'zh', 'yue', 'all_ko', 'all_ja', 'all_zh', 'all_yue'}
            for key in ('text_lang', 'prompt_lang'):
                params[key] = req.get(key, 'ko')
                if params[key] not in langs:
                    raise ValueError('언어 설정을 확인해주세요.')
            encoded = req.get('ref_audio_base64', '')
            if not isinstance(encoded, str) or not encoded or len(encoded) > 14 * 1024 * 1024:
                raise ValueError('3~10초, 10MB 이하의 참조 음성을 등록해주세요.')
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > 10 * 1024 * 1024:
                raise ValueError('참조 음성은 10MB 이하여야 합니다.')
        except (ValueError, TypeError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if not lock.acquire(blocking=False):
            raise HTTPException(409, '앞선 음성을 생성 중입니다. 코랩 로그에서 완료 여부를 확인해주세요.')
        # The transcript AND its language are part of the key. Upstream alone
        # checks prompt text but not a changed language when reusing its cache.
        identity = json.dumps([hashlib.sha256(raw).hexdigest(), prompt, params['prompt_lang'],
                               SERVER_VERSION], ensure_ascii=False)
        reference_id = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        pending_folder = None
        started = time.monotonic()
        try:
            cached = reference_cache.get(reference_id)
            reference_hit = cached is not None
            print(f'▶ GPT 음성 생성 시작 · {len(text)}자 · {steps}단계', flush=True)
            if reference_hit:
                prepared = Path(cached['folder'].name) / 'reference.wav'
                pipeline.prompt_cache = copy_prompt_cache(cached['prompt_cache'])
                reference_cache.move_to_end(reference_id)
                print('✅ 참고 음성과 대사 분석 결과 재사용', flush=True)
            else:
                pending_folder = tempfile.TemporaryDirectory(prefix='sovits_ref_')
                source = Path(pending_folder.name) / 'input.audio'
                prepared = Path(pending_folder.name) / 'reference.wav'
                source.write_bytes(raw)
                result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source),
                                         '-t', '10.1', '-vn', '-ac', '1', '-c:a', 'pcm_s16le', str(prepared)],
                                        capture_output=True, timeout=30)
                if result.returncode:
                    raise HTTPException(422, '참조 음성을 읽지 못했습니다. WAV 또는 MP3를 확인해주세요.')
                audio, rate = sf.read(prepared)
                duration = len(audio) / rate
                if not 3.0 <= duration <= 10.0:
                    raise HTTPException(422, '참조 음성은 3~10초여야 합니다. 문장이 끝나는 지점에서 직접 잘라주세요.')
                if not np.isfinite(audio).all() or np.max(np.abs(audio)) < .001:
                    raise HTTPException(422, '참조 음성이 무음이거나 너무 작습니다.')
                source.unlink()
                pipeline.prompt_cache = copy_prompt_cache(empty_prompt_cache)
                print('✅ 참고 음성 변환·검사 완료 · 첫 분석 후 다음 대사부터 재사용', flush=True)
            params.update(text=text, prompt_text=prompt, ref_audio_path=str(prepared),
                          sample_steps=steps, text_split_method=split, batch_size=1, split_bucket=False,
                          parallel_infer=False, streaming_mode=False, return_fragment=False,
                          seed=-1, repetition_penalty=1.35, super_sampling=False)
            generator = pipeline.run(params)
            try:
                rate, audio = next(generator)
            finally:
                generator.close()
            audio = np.asarray(audio)
            if rate != 48000 or not audio.size or not np.isfinite(audio).all() or np.max(np.abs(audio)) < .00001:
                raise RuntimeError('v4에서 유효한 48kHz 음성이 생성되지 않았습니다. 코랩 로그를 확인해주세요.')
            output = io.BytesIO()
            sf.write(output, audio, rate, format='WAV', subtype='PCM_16')
            if not reference_hit:
                while len(reference_cache) >= 8:
                    _, discarded = reference_cache.popitem(last=False)
                    discarded['folder'].cleanup()
                    del discarded
                reference_cache[reference_id] = {
                    'folder': pending_folder,
                    'prompt_cache': copy_prompt_cache(pipeline.prompt_cache),
                }
                pending_folder = None
            elapsed = time.monotonic() - started
            print(f'✅ GPT 생성 완료 · 음성 {len(audio) / rate:.1f}초 · 처리 {elapsed:.1f}초', flush=True)
            return Response(output.getvalue(), media_type='audio/wav', headers={
                'X-Server-Version': SERVER_VERSION,
                'X-Reference-Cache': 'hit' if reference_hit else 'miss',
            })
        except HTTPException:
            raise
        except Exception as exc:
            invalid = reference_cache.pop(reference_id, None)
            if invalid is not None:
                invalid['folder'].cleanup()
            pipeline.prompt_cache = copy_prompt_cache(empty_prompt_cache)
            logging.exception('GPT-SoVITS generation failed')
            raise HTTPException(500, str(exc)[:800]) from exc
        finally:
            if pending_folder is not None:
                pending_folder.cleanup()
            lock.release()
    return app


def serve(port):
    import torch
    import uvicorn
    from text.korean import _g2p, g2p
    from TTS_infer_pack.TTS import TTS, TTS_Config
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA GPU가 없습니다.')
    # Do not swallow Mecab / pronunciation errors or replace phonemes with raw text.
    if not _g2p('국물이 맑고 꽃이 피었습니다.') or not g2p('국물이 맑고 꽃이 피었습니다.'):
        raise RuntimeError('한국어 발음 변환에 실패했습니다.')
    pipeline = TTS(TTS_Config(str(ROOT / 'tts-v4.json')))
    if pipeline.configs.version != 'v4' or pipeline.vocoder_configs.get('sr') != 48000:
        raise RuntimeError('요청한 v4 모델과 48kHz 보코더가 로딩되지 않았습니다.')
    app = create_app(pipeline, os.environ['SOVITS_ACCESS_TOKEN'])
    uvicorn.run(app, host='127.0.0.1', port=port, access_log=False)


def tunnel():
    state = read_state()
    if not state.get('base'):
        raise RuntimeError('이 런타임에는 1번의 준비 기록이 없습니다. 1번을 완료한 같은 코랩의 같은 런타임에서 '
                           '4번을 실행해주세요. 이 셀에서는 설치를 시작하지 않습니다.')
    print('음성 서버의 실제 응답을 확인합니다…', flush=True)
    for attempt in range(3):
        report = connection_status(state['base'])
        if report['ready']:
            break
        if attempt < 2:
            print(f'서버 응답 재확인 {attempt + 1}/2…', flush=True)
            time.sleep(2)
    if not report['ready']:
        process = worker_state(state)
        if process == 'stopped':
            print('종료된 음성 서버를 다시 시작합니다. 기존 설치와 모델 파일을 그대로 사용합니다.', flush=True)
            state = start_server()
        elif process == 'running':
            print('서버가 실행 중입니다. 모델 로딩 완료를 기다립니다.', flush=True)
            state = wait_for_server(state)
        else:
            print_connection_diagnostics(state, report)
            raise RuntimeError('기존 서버의 실행 기록을 확인할 수 없어 자동 복구를 중단했습니다. '
                               '1번을 완료한 같은 런타임인지 확인해주세요. 패키지 재설치는 하지 않았습니다.')
        report = connection_status(state['base'])
        if not report['ready']:
            print_connection_diagnostics(state, report)
            raise RuntimeError('재시작한 서버의 응답이 끊겼습니다. 위 마지막 로그를 확인해주세요.')
    print(f'✅ {report["detail"]} · 서버 {report["server_version"]}', flush=True)
    if not report['reference_cache']:
        print('기존 서버 연결을 유지합니다. 이 서버는 참고 음성 재사용 기능이 아직 적용되지 않았습니다.', flush=True)
    if state.get('public_base'):
        try:
            if health(state['public_base']):
                print('프로그램 연결 주소:\n' + state['public_base'] + '/tts', flush=True)
                return
        except Exception:
            pass
    binary = ROOT / 'cloudflared'
    if not binary.exists():
        urllib.request.urlretrieve('https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64', binary)
        binary.chmod(0o755)
    logfile = ROOT / 'tunnel.log'
    with logfile.open('w') as log:
        child = subprocess.Popen([str(binary), 'tunnel', '--no-autoupdate', '--url', f'http://127.0.0.1:{state["port"]}'],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    try:
        for _ in range(60):
            if child.poll() is not None:
                raise RuntimeError('연결 주소 생성 실패. tunnel.log를 확인해주세요.')
            matches = re.findall(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', logfile.read_text(errors='replace'))
            if matches:
                public = matches[0] + urllib.parse.urlsplit(state['base']).path
                try:
                    ready = health(public)
                except Exception:
                    ready = False
                if ready:
                    state.update(public_base=public, tunnel_pid=child.pid)
                    write_state(state)
                    print('✅ GPT-SoVITS v4 프로그램 연결 주소:\n' + public + '/tts', flush=True)
                    return
            time.sleep(1)
        raise RuntimeError('공개 연결 확인에 실패했습니다. 코랩 안에서는 3번 셀로 생성할 수 있습니다.')
    except BaseException:
        child.terminate()
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--setup', action='store_true')
    mode.add_argument('--serve', action='store_true')
    mode.add_argument('--tunnel', action='store_true')
    parser.add_argument('--port', type=int, default=9880)
    args = parser.parse_args()
    if args.setup:
        try:
            setup()
        except Exception:
            ROOT.mkdir(parents=True, exist_ok=True)
            with (ROOT / 'setup.log').open('a', encoding='utf-8') as log:
                log.write('\n' + traceback.format_exc())
            raise
    elif args.serve:
        serve(args.port)
    else:
        tunnel()
