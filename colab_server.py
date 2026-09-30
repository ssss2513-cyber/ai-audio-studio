# -*- coding: utf-8 -*-
"""Self-contained CosyVoice 2 Colab runner. No edits to upstream model code.

--setup: install an isolated Python 3.10 environment and start the local API.
--tunnel: optionally expose the ready API to AI Voice Studio.
--serve: internal worker, launched with the isolated Python interpreter.
"""
import argparse
from collections import OrderedDict
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.request

SOURCE_REVISION = '074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc'
COSY_MODEL_REVISION = 'eec1ae6c79877dbd9379285cf8789c9e0879293d'

# Retain the existing installation/cache path so an interrupted install can resume.
ENGINE = 'cosyvoice'
LABEL = 'CosyVoice 2'
ROOT = Path('/content/ai_voice_dual_v1/cosyvoice')
SOURCE = ROOT / 'CosyVoice'
PYTHON = ROOT / 'venv/bin/python'
MODEL = SOURCE / 'pretrained_models/CosyVoice2-0.5B'
STATE = ROOT / 'state.json'
SERVICE = 'ai-voice-studio-cosyvoice'
SERVER_VERSION = '2.9.6'
GENERATION_CAPABILITY = 'validated_generation_v293'
REFERENCE_CACHE_CAPABILITY = 'reference_cache_v294'
REFERENCE_TRANSPORT_CAPABILITY = 'reference_transport_v295'
DURATION_GUARD_CAPABILITY = 'reference_duration_guard_v296'
MODEL_REVISION = COSY_MODEL_REVISION


def run(args, label):
    print('\n▶ ' + label, flush=True)
    with (ROOT / 'setup.log').open('a', encoding='utf-8') as log:
        log.write('\n▶ ' + label + '\n')
        log.flush()
        proc = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            for line in proc.stdout:
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
            code = proc.wait()
        except BaseException:
            proc.terminate()
            raise
    if code:
        raise RuntimeError(f'{label} 실패 (종료 코드 {code}). 바로 위 오류를 확인해주세요.')


def read_state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def health(base, require_current=False):
    with urllib.request.urlopen(base + '/health', timeout=5) as response:
        data = json.load(response)
    return (data.get('service') == SERVICE and data.get('ready') is True
            and (not require_current or (data.get('server_version') == SERVER_VERSION
                 and GENERATION_CAPABILITY in data.get('capabilities', [])
                 and REFERENCE_CACHE_CAPABILITY in data.get('capabilities', []))))


def stop_previous_worker(state):
    """Replace only this runner's recorded, idle worker; retain model downloads."""
    import fcntl
    pid = state.get('pid')
    if not isinstance(pid, int) or pid <= 1:
        raise RuntimeError('기존 서버의 실행 정보를 확인할 수 없습니다. 런타임을 다시 시작한 뒤 1번을 실행해주세요.')
    proc_dir = Path('/proc') / str(pid)
    try:
        already_exited = (proc_dir / 'stat').read_text().split(') ', 1)[1].startswith('Z')
    except (OSError, IndexError):
        already_exited = not proc_dir.exists()
    if already_exited:
        STATE.unlink(missing_ok=True)
        return
    try:
        args = (proc_dir / 'cmdline').read_bytes().split(b'\0')
        args = [arg.decode(errors='replace') for arg in args if arg]
        script_index = 2 if len(args) > 1 and args[1] == '-u' else 1
        owned = (len(args) > script_index and Path(args[0]).absolute() == PYTHON.absolute()
                 and Path(args[script_index]).absolute() == Path(__file__).absolute()
                 and '--serve' in args and '--port' in args
                 and args[args.index('--port') + 1] == str(state.get('port')))
    except (OSError, IndexError):
        owned = False
    if not owned:
        raise RuntimeError('기존 서버를 안전하게 교체할 수 없습니다. 런타임을 다시 시작한 뒤 1번을 실행해주세요.')
    with (ROOT.parent / 'gpu.lock').open('a+') as gpu_lock:
        try:
            fcntl.flock(gpu_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('음성을 생성 중입니다. 완료된 뒤 1번을 실행하면 새 서버로 바뀝니다.') from None
        print('이전 CosyVoice 서버를 수정 버전으로 교체합니다. 설치와 모델 파일은 유지합니다.', flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            STATE.unlink(missing_ok=True)
            return
        for _ in range(50):
            try:
                # An exited child can remain as a zombie until its notebook reaps it.
                exited = (proc_dir / 'stat').read_text().split(') ', 1)[1].startswith('Z')
            except (OSError, IndexError):
                exited = True
            if exited:
                STATE.unlink(missing_ok=True)
                return
            time.sleep(0.2)
    raise RuntimeError('이전 서버가 아직 종료 중입니다. 잠시 뒤 1번을 다시 실행해주세요.')


def normalize_speech_text(value, label):
    value = unicodedata.normalize('NFC', value)
    value = re.sub('[\u200b\u200c\u200d\ufeff]', '', value)
    value = re.sub(r'\s+', ' ', value).strip()
    if not any(char.isalnum() for char in value):
        raise ValueError(label + '에 실제로 읽을 문장을 입력해주세요.')
    if '<|' in value or '|>' in value:
        raise ValueError(label + '에는 모델 제어 기호 없이 실제 대사만 입력해주세요.')
    return value


def speech_units(text):
    """A deliberately loose duration estimate, not speech recognition."""
    return sum(1 for char in text if char.isalnum() and not char.isascii()) + sum(
        max(1, len(word) / 3) for word in re.findall(r'[A-Za-z]+', text)) + sum(
        2 * len(number) for number in re.findall(r'[0-9]+', text))


def duration_limits(text, reference_units, reference_seconds):
    """Loose plausibility bounds, with reference pace and number pronunciation.

    Duration alone cannot verify the spoken words. Keep the bound finite even
    with a slow reference, without treating all speakers as equally paced.
    """
    units = speech_units(text)
    reference_rate = reference_units / max(reference_seconds, 0.8)
    reference_rate = min(10.0, max(1.5, reference_rate))
    pauses = min(4.0, len(re.findall(r'[,.!?，。！？]', text)) * 0.3)
    upper = min(90.0, max(10.0, units * 0.8 + 4.0,
                          units / reference_rate * 1.8 + 4.0 + pauses))
    return max(0.15, units / 30.0), upper


class GeneratedAudioValidationError(RuntimeError):
    pass


def synthesis_chunks(text, prompt_text, tokenizer):
    """Pack short sentences together, with a token budget for Korean inputs."""
    def tokens(value):
        return len(tokenizer.encode(value, allowed_special='all'))

    # The upstream frontend uses 60-80 tokens. Keep Korean out of its English
    # normalizer, but use a comparable token budget instead of 180 characters.
    chunks, current = [], ''
    for word in text.split():
        candidate = (current + ' ' + word).strip()
        if current and tokens(candidate) > 80:
            chunks.append(current)
            current = ''
        # A script without spaces must also respect the token budget.
        while tokens(word) > 80:
            low, high = 1, len(word)
            while low < high:
                middle = (low + high + 1) // 2
                if tokens(word[:middle]) <= 80:
                    low = middle
                else:
                    high = middle - 1
            chunks.append(word[:low])
            word = word[low:]
        current = (current + ' ' + word).strip()
        if (re.search(r'[.!?。！？]["”\']?$', current) and tokens(current) >= 60
                and len(current) >= len(prompt_text) / 2):
            chunks.append(current)
            current = ''
    if current:
        chunks.append(current)
    # Do not synthesize a tiny final fragment on its own if it fits the previous
    # chunk with a small, bounded extension to the normal budget.
    if len(chunks) > 1 and (tokens(chunks[-1]) < 25 or len(chunks[-1]) < len(prompt_text) / 2):
        joined = chunks[-2] + ' ' + chunks[-1]
        if tokens(joined) <= 100:
            chunks[-2:] = [joined]
    return chunks


def setup():
    if not Path('/content').is_dir():
        raise RuntimeError('이 파일은 Google Colab에서 실행해주세요.')
    ROOT.mkdir(parents=True, exist_ok=True)
    state = read_state()
    existing_ready = False
    try:
        existing_ready = bool(state.get('base')) and health(state['base'], require_current=False)
    except Exception:
        pass
    if existing_ready:
        if health(state['base'], require_current=True):
            print(f'✅ {LABEL} v{SERVER_VERSION} 준비 완료. 사이트 연결은 4번을 실행하세요.', flush=True)
            return
        stop_previous_worker(state)
    elif isinstance(state.get('pid'), int) and (Path('/proc') / str(state['pid'])).exists():
        stop_previous_worker(state)
    # A healthy earlier worker proves this installation already loaded. A
    # runner upgrade can reuse it without reinstalling packages/models.
    if existing_ready and PYTHON.is_file() and all((MODEL / name).is_file() for name in
            ('cosyvoice2.yaml', 'llm.pt', 'flow.pt', 'hift.pt', 'campplus.onnx', 'speech_tokenizer_v2.onnx')):
        print('기존 설치와 모델을 그대로 사용해 수정된 CosyVoice 서버를 시작합니다.', flush=True)
        start_server()
        return
    if shutil.which('nvidia-smi') is None:
        raise RuntimeError('GPU가 없습니다. 런타임 → 런타임 유형 변경 → T4 GPU를 선택해주세요.')
    run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'], 'GPU 확인')
    run(['apt-get', 'update', '-qq'], '시스템 패키지 목록 갱신')
    run(['apt-get', 'install', '-y', '-qq', 'ffmpeg', 'sox', 'libsox-dev',
         'libsndfile1', 'build-essential', 'git'], '오디오 도구 설치')
    if not PYTHON.is_file():
        run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', 'uv'], 'Python 환경 도구 설치')
        run([sys.executable, '-m', 'uv', 'python', 'install', '3.10'], '별도 Python 3.10 설치')
        run([sys.executable, '-m', 'uv', 'venv', '--python', '3.10', '--seed', ROOT / 'venv'], '독립 실행 환경 생성')
    if not (SOURCE / '.git').is_dir():
        run(['git', 'clone', '--filter=blob:none', 'https://github.com/FunAudioLLM/CosyVoice.git', SOURCE], '공식 CosyVoice 소스 다운로드')
    run(['git', '-C', SOURCE, 'checkout', '--detach', SOURCE_REVISION], '확인한 소스 버전 선택')
    run(['git', '-C', SOURCE, 'submodule', 'update', '--init', '--recursive'], 'Matcha-TTS 소스 준비')
    # Keep upstream versions except the documented inference-only changes below.
    requirements = []
    for line in (SOURCE / 'requirements.txt').read_text().splitlines():
        if line.startswith(('deepspeed', 'tensorrt', 'gradio', 'fastapi-cli')):
            continue  # optional acceleration/training/UI; this server uses none of these
        if line.startswith('diffusers=='):
            line = 'diffusers==0.32.2'  # compatible with modern huggingface_hub (no cached_download import)
        requirements.append(line)
    requirements += ['huggingface-hub==0.30.2', 'python-multipart>=0.0.18,<0.1', 'setuptools<81']
    req_file = ROOT / 'inference-requirements.txt'
    req_file.write_text('\n'.join(requirements) + '\n')
    # Whisper's pinned source distribution imports pkg_resources while building.
    # The runtime setuptools pin alone does not apply inside pip's isolated build.
    build_constraints = ROOT / 'build-constraints.txt'
    build_constraints.write_text('setuptools<81\n')
    stamp = hashlib.sha256(req_file.read_bytes() + build_constraints.read_bytes()).hexdigest()
    marker = ROOT / 'dependencies.ok'
    if not marker.exists() or marker.read_text() != stamp:
        run([PYTHON, '-m', 'pip', 'install', 'pip>=25.3,<26', 'setuptools<81', 'wheel', 'Cython<4'], '설치 도구 준비')
        # Install the matching CUDA pair first; do not touch Colab's own Torch.
        run([PYTHON, '-m', 'pip', 'install', 'torch==2.3.1', 'torchaudio==2.3.1',
             '--index-url', 'https://download.pytorch.org/whl/cu121'], 'GPU용 Torch 및 오디오 패키지 설치')
        run([PYTHON, '-m', 'pip', 'install', '-r', req_file,
             '--build-constraint', build_constraints], 'CosyVoice 의존성 설치')
        run([PYTHON, '-m', 'pip', 'check'], '의존성 충돌 확인')
        marker.write_text(stamp)
    download = (
        'from huggingface_hub import snapshot_download; '
        f'snapshot_download("FunAudioLLM/CosyVoice2-0.5B", revision={MODEL_REVISION!r}, '
        f'local_dir={str(MODEL)!r}, allow_patterns=["cosyvoice2.yaml", "llm.pt", "flow.pt", '
        '"hift.pt", "campplus.onnx", "speech_tokenizer_v2.onnx", "CosyVoice-BlankEN/*"])'
    )
    run([PYTHON, '-c', download], 'CosyVoice 2 모델 준비 (첫 실행 시 다운로드)')
    missing = [name for name in ('cosyvoice2.yaml', 'llm.pt', 'flow.pt', 'hift.pt',
                                'campplus.onnx', 'speech_tokenizer_v2.onnx') if not (MODEL / name).is_file()]
    if missing:
        raise RuntimeError('모델 다운로드가 완전하지 않습니다: ' + ', '.join(missing))
    start_server()


def start_server():
    """Start the unchanged FP32 model from an existing installation."""
    env = os.environ.copy()
    # The isolated worker renders no notebook plots and may not have matplotlib_inline installed.
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONPATH'] = os.pathsep.join([str(SOURCE), str(SOURCE / 'third_party/Matcha-TTS')])
    libs = [str(p) for p in (ROOT / 'venv/lib/python3.10/site-packages/nvidia').glob('*/lib')]
    env['LD_LIBRARY_PATH'] = os.pathsep.join(libs + [env.get('LD_LIBRARY_PATH', '')])
    env['TOKENIZERS_PARALLELISM'] = 'false'
    env['COSY_ACCESS_TOKEN'] = secrets.token_urlsafe(24)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}/v1/{env["COSY_ACCESS_TOKEN"]}'
    log_path = ROOT / 'server.log'
    with log_path.open('w') as log:
        worker = subprocess.Popen([str(PYTHON), '-u', str(Path(__file__).resolve()), '--serve', '--engine', ENGINE, '--port', str(port)],
                                  cwd=SOURCE, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, start_new_session=True)
    print('\n▶ 모델 준비 기록을 아래에 실시간으로 표시합니다. 최대 10분 후에도 준비되지 않으면 중단합니다.', flush=True)
    ready = False
    started = time.monotonic()
    next_notice = started + 30
    token = env['COSY_ACCESS_TOKEN']
    live_log = log_path.open(encoding='utf-8', errors='replace')

    def show_new_logs():
        output = live_log.read()
        if output:
            print(output.replace(token, '[접속 키 숨김]'), end='' if output.endswith('\n') else '\n', flush=True)

    def error_tail():
        return log_path.read_text(errors='replace')[-7000:].replace(token, '[접속 키 숨김]')

    try:
        while time.monotonic() - started < 600:
            show_new_logs()
            if worker.poll() is not None:
                raise RuntimeError('모델 시작 실패:\n' + error_tail())
            try:
                ready = health(base)
            except Exception:
                pass
            if ready:
                state = {'base': base, 'pid': worker.pid, 'port': port, 'model': LABEL, 'engine': ENGINE}
                STATE.write_text(json.dumps(state))
                STATE.chmod(0o600)
                show_new_logs()
                print(f'✅ {LABEL} v{SERVER_VERSION} 준비 완료. 사이트 연결은 4번을 실행하세요.', flush=True)
                return
            now = time.monotonic()
            if now >= next_notice:
                elapsed = int(now - started)
                print(f'  ⏳ 준비 대기 {elapsed // 60}분 {elapsed % 60:02d}초 / 최대 10분 — 아직 준비 완료가 아닙니다. 위 마지막 단계와 기록을 확인해주세요.', flush=True)
                next_notice = now + 30
            time.sleep(2)
        show_new_logs()
        raise RuntimeError('모델 준비가 10분 안에 완료되지 않아 중단했습니다. 아래 실제 기록을 보내주세요:\n' + error_tail())
    finally:
        live_log.close()
        if not ready and worker.poll() is None:
            worker.terminate()


def serve(port):
    import faulthandler
    # A live process is not proof of progress. Expose where startup is waiting.
    faulthandler.enable()
    faulthandler.dump_traceback_later(120, repeat=True)
    print('[모델 준비 1/4] 오디오·Torch 실행 환경을 불러옵니다.', flush=True)
    import numpy as np
    import soundfile as sf
    import torch
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import Response
    import uvicorn
    import fcntl
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA GPU를 사용할 수 없습니다. T4 GPU 런타임인지 확인해주세요.')
    print('[모델 준비 2/4] GPU 확인 완료. CosyVoice 실행 코드를 불러옵니다.', flush=True)
    from cosyvoice.cli.cosyvoice import CosyVoice2
    print('[모델 준비 3/4] 음성 모델·토크나이저를 불러옵니다. 세부 기록이 이어집니다.', flush=True)
    model = CosyVoice2(model_dir=str(MODEL), load_jit=False, load_trt=False, fp16=False)
    faulthandler.cancel_dump_traceback_later()
    print('[모델 준비 4/4] 모델 로딩 완료. 연결 서버를 시작합니다.', flush=True)
    sample_rate = model.sample_rate
    if sample_rate != 24000:
        raise RuntimeError('CosyVoice 2 출력 설정이 올바르지 않습니다. 모델 설정을 다시 확인해주세요.')
    access_token = os.environ['COSY_ACCESS_TOKEN']
    model_lock = threading.Lock()
    # Cache only this personal server's reference features, under model_lock.
    # Entries are never saved to disk and are bounded to limit GPU memory use.
    reference_cache = OrderedDict()
    reference_cache_limit = 16
    instance_id = secrets.token_hex(12)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(token):
        if not secrets.compare_digest(token, access_token):
            raise HTTPException(403, '연결 주소가 올바르지 않습니다. 새 주소 전체를 복사해주세요.')

    @app.get('/')
    def index():
        return {'service': SERVICE, 'message': LABEL + ' 실행 중. 코랩에 표시된 전체 연결 주소를 사용하세요.'}

    @app.get('/v1/{token}')
    @app.get('/v1/{token}/health')
    def status(token: str):
        authorize(token)
        return {'service': SERVICE, 'api_version': 1, 'ready': True, 'model': LABEL, 'engine': ENGINE, 'cuda': True,
                'server_version': SERVER_VERSION, 'sample_rate': sample_rate, 'instance_id': instance_id,
                'capabilities': ['style_instruction', GENERATION_CAPABILITY, REFERENCE_CACHE_CAPABILITY,
                                 REFERENCE_TRANSPORT_CAPABILITY, DURATION_GUARD_CAPABILITY]}

    @app.post('/v1/{token}/synthesize')
    def synthesize(token: str, text: str = Form(...), prompt_text: str = Form(''),
                   speed: float = Form(1.0), reference: UploadFile | None = File(None),
                   style_instruction: str = Form(''), reference_id: str = Form('')):
        authorize(token)
        try:
            text = normalize_speech_text(text, '생성할 대사')
            prompt_text = normalize_speech_text(prompt_text, '참조 오디오 실제 대사')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if len(text) > 2000:
            raise HTTPException(422, '한 번에 2,000자 이하로 나눠 생성해주세요.')
        if len(prompt_text) > 1000:
            raise HTTPException(422, '참조 대사에는 3~30초 참고 음성에서 말한 내용만 입력해주세요.')
        if not np.isfinite(speed) or not 0.5 <= speed <= 2:
            raise HTTPException(422, '속도는 0.5~2.0 사이여야 합니다.')
        style_instruction = style_instruction.strip()
        if len(style_instruction) > 800 or '<|' in style_instruction or '|>' in style_instruction:
            raise HTTPException(422, '스타일 지시문 형식이 올바르지 않습니다.')
        if not model_lock.acquire(blocking=False):
            raise HTTPException(409, '다른 음성을 생성 중입니다. 완료 후 다시 실행해주세요.')
        gpu_lock = None
        try:
            gpu_lock = (ROOT.parent / 'gpu.lock').open('a+')
            try:
                fcntl.flock(gpu_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HTTPException(409, '다른 엔진이 음성을 생성 중입니다. 완료 후 실행해주세요.')
            instruction = ('Speak in Korean. ' + style_instruction + '<|endofprompt|>') if style_instruction else ''
            payload = None
            if reference is not None:
                payload = reference.file.read(10 * 1024 * 1024 + 1)
                if not payload or len(payload) > 10 * 1024 * 1024:
                    raise HTTPException(422, '참조 음성은 10MB 이하 WAV 또는 MP3를 사용해주세요.')
                identity = json.dumps([hashlib.sha256(payload).hexdigest(), prompt_text,
                                       instruction, SERVER_VERSION], ensure_ascii=False)
                reference_id = 'studio_' + hashlib.sha256(identity.encode('utf-8')).hexdigest()
            elif not re.fullmatch(r'studio_[a-f0-9]{64}', reference_id):
                raise HTTPException(422, '참고 음성을 등록해주세요.')
            cached = reference_cache.get(reference_id)
            reference_hit = (cached is not None and reference_id in model.frontend.spk2info
                             and cached.get('prompt_text') == prompt_text
                             and cached.get('instruction') == instruction)
            if payload is None and not reference_hit:
                # This response is strictly before ANY model call. The client
                # may restore the reference bytes without duplicating speech.
                raise HTTPException(428, {'code': 'reference_required', 'synthesis_started': False,
                                          'message': '참고 음성 정보를 다시 전송해주세요.'})
            with tempfile.TemporaryDirectory(prefix='cosy_ref_') as folder:
                # Include the conditioning text and mode: instruction-mode voices
                # must never reuse a basic-mode transcript (upstream issue #1400).
                preparation_started = time.monotonic()
                if reference_hit:
                    reference_cache.move_to_end(reference_id)
                    duration = cached['duration']
                    reference_spoken_seconds = cached['spoken_seconds']
                    reference_units = cached['speech_units']
                    print('참고 목소리 분석 결과를 재사용합니다.', flush=True)
                else:
                    print('참고 목소리를 분석합니다. 같은 음성은 다음 대사부터 재사용합니다.', flush=True)
                    original = Path(folder) / 'reference.audio'
                    original.write_bytes(payload)
                    prepared = Path(folder) / 'reference.wav'
                    result = subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(original),
                                             '-t', '31', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_f32le', str(prepared)],
                                            capture_output=True, text=True, timeout=60)
                    if result.returncode:
                        raise HTTPException(422, '참조 오디오를 읽을 수 없습니다. WAV 또는 MP3를 확인해주세요.')
                    audio, sr = sf.read(prepared, dtype='float32')
                    duration = len(audio) / sr
                    if not 3 <= duration <= 30:
                        raise HTTPException(422, f'참조 음성은 3~30초여야 합니다. 현재 {duration:.1f}초입니다.')
                    if not np.all(np.isfinite(audio)) or np.max(np.abs(audio)) < 0.00001:
                        raise HTTPException(422, '참조 음성에 들리는 목소리가 없습니다.')

                    # Remove only nearly silent outer padding; never cut spoken audio
                    # to a fixed length without also aligning its transcript.
                    frame = int(sr * 0.02)
                    padded = np.pad(audio, (0, (-len(audio)) % frame))
                    levels = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1))
                    active = np.flatnonzero(levels > max(0.0001, float(levels.max()) * 0.015))
                    if active.size == 0 or active.size * 0.02 < 0.8:
                        raise HTTPException(422, '참고 음성의 실제 발화가 너무 짧거나 조용합니다. 배경음 없이 한 사람이 문장을 말하는 녹음을 사용해주세요.')
                    spoken_seconds = active.size * 0.02
                    units = speech_units(prompt_text)
                    if (spoken_seconds > 6 and units / spoken_seconds < 0.75) or units / spoken_seconds > 25:
                        raise HTTPException(422, f'참고 음성 길이({duration:.1f}초)와 입력한 참고 대사 길이가 크게 다릅니다. 새로 만들 대사가 아니라 녹음 전체의 실제 대사를 입력해주세요.')
                    margin = int(sr * 0.15)
                    start = max(0, int(active[0]) * frame - margin)
                    end = min(len(audio), (int(active[-1]) + 1) * frame + margin)
                    audio = audio[start:end]
                    peak = float(np.max(np.abs(audio)))
                    # Leave ordinary recordings untouched and avoid amplifying noise.
                    if peak > 0.95:
                        audio = audio * (0.95 / peak)
                    elif peak < 0.1:
                        audio = audio * min(4.0, 0.1 / peak)
                    sf.write(prepared, audio, sr, subtype='FLOAT')
                    if len(reference_cache) >= reference_cache_limit:
                        oldest, _ = reference_cache.popitem(last=False)
                        model.frontend.spk2info.pop(oldest, None)
                    with torch.inference_mode():
                        model.add_zero_shot_spk(instruction or prompt_text, str(prepared), reference_id)
                    reference_cache[reference_id] = {'duration': duration, 'prompt_text': prompt_text,
                                                     'instruction': instruction,
                                                     'spoken_seconds': spoken_seconds, 'speech_units': units}
                    reference_spoken_seconds = spoken_seconds
                    reference_units = units
                preparation_seconds = time.monotonic() - preparation_started

                pieces = []
                chunks = synthesis_chunks(text, prompt_text, model.frontend.tokenizer)
                request_id = secrets.token_hex(4)
                logging.info('request=%s mode=%s ref_seconds=%.2f text_chars=%d chunks=%d',
                             request_id, 'style' if style_instruction else 'zero_shot', duration, len(text), len(chunks))
                synthesis_started = time.monotonic()
                # One recovery attempt per entire request, not an unbounded
                # per-chunk loop. No model/precision/voice/speed change on retry.
                recovery_remaining = 1
                with torch.inference_mode():
                    for index, chunk in enumerate(chunks, 1):
                        chunk_started = time.monotonic()
                        print(f'음성 생성 {index}/{len(chunks)} 구간 처리 중…', flush=True)
                        lower, upper = duration_limits(chunk, reference_units, reference_spoken_seconds)
                        while True:
                            if style_instruction:
                                generated = model.inference_instruct2(chunk, instruction, '',
                                                                     zero_shot_spk_id=reference_id,
                                                                     stream=False, speed=1.0, text_frontend=False)
                            else:
                                generated = model.inference_zero_shot(chunk, prompt_text, '',
                                                                      zero_shot_spk_id=reference_id,
                                                                      stream=False, speed=1.0, text_frontend=False)
                            chunk_pieces = []
                            for item in generated:
                                chunk_pieces.append(item['tts_speech'].detach().cpu().numpy().reshape(-1))
                            if not chunk_pieces or sum(part.size for part in chunk_pieces) == 0:
                                raise GeneratedAudioValidationError(f'{index}번째 구간에서 빈 음성이 반환되어 저장하지 않았습니다.')
                            speech = np.concatenate(chunk_pieces)
                            seconds = speech.size / sample_rate
                            if not np.all(np.isfinite(speech)) or float(np.sqrt(np.mean(speech ** 2))) < 0.0001:
                                raise GeneratedAudioValidationError(f'{index}번째 구간의 음성이 무음이거나 손상되어 저장하지 않았습니다.')
                            if lower <= seconds <= upper:
                                break
                            logging.warning('request=%s chunk=%d duration=%.2f bounds=%.2f..%.2f units=%.1f ref_units=%.1f ref_spoken=%.2f',
                                            request_id, index, seconds, lower, upper, speech_units(chunk),
                                            reference_units, reference_spoken_seconds)
                            if recovery_remaining:
                                recovery_remaining -= 1
                                print(f'{index}번째 구간 결과 길이({seconds:.1f}초)가 검사 범위를 벗어나 해당 구간만 한 번 다시 생성합니다. 음질 설정은 유지합니다.', flush=True)
                                del speech, chunk_pieces
                                continue
                            raise GeneratedAudioValidationError(
                                f'{index}번째 구간이 길이 검사를 통과하지 못했습니다 '
                                f'(생성 {seconds:.1f}초, 검사 범위 {lower:.1f}~{upper:.1f}초). '
                                '추가 생성은 요청당 한 번까지만 하며, 통과하지 않은 결과는 저장하지 않습니다. '
                                '길이만으로 참고 대사 오류 여부를 확정할 수 없습니다. 이전 완료 대사는 유지됩니다.')
                        logging.info('request=%s chunk=%d/%d chars=%d seconds=%.2f',
                                     request_id, index, len(chunks), len(chunk), seconds)
                        print(f'음성 생성 {index}/{len(chunks)} 완료 · 처리 {time.monotonic() - chunk_started:.1f}초 · 음성 {seconds:.1f}초', flush=True)
                        pieces.append(speech)
                        if index < len(chunks):
                            pieces.append(np.zeros(int(sample_rate * 0.12), dtype=np.float32))
                if not pieces or sum(x.size for x in pieces) == 0:
                    raise RuntimeError('모델이 빈 음성을 반환했습니다. 참조 음성과 실제 대사를 확인해주세요.')
                speech = np.concatenate(pieces)
                if not np.all(np.isfinite(speech)) or np.max(np.abs(speech)) < 0.000001:
                    raise RuntimeError('모델 출력이 무음이거나 손상되었습니다. server.log를 확인해주세요.')
                peak = float(np.max(np.abs(speech)))
                if peak > 0.98:
                    speech = speech * (0.98 / peak)
                if speed != 1.0:
                    # Keep acoustic generation at its native rate. FFmpeg changes
                    # tempo afterwards instead of interpolating the model's mel.
                    native = Path(folder) / 'generated.wav'
                    adjusted = Path(folder) / 'tempo.wav'
                    sf.write(native, speech, sample_rate, subtype='FLOAT')
                    result = subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(native),
                                             '-af', f'atempo={speed}', '-c:a', 'pcm_f32le', str(adjusted)],
                                            capture_output=True, text=True, timeout=60)
                    if result.returncode:
                        raise RuntimeError('말하기 속도 조절에 실패했습니다. 파일을 저장하지 않았습니다.')
                    speech, _ = sf.read(adjusted, dtype='float32')
                    if not speech.size or not np.all(np.isfinite(speech)):
                        raise RuntimeError('속도 조절 결과가 비어 있거나 손상되었습니다.')
                    peak = float(np.max(np.abs(speech)))
                    if peak > 0.98:
                        speech = speech * (0.98 / peak)
                output = io.BytesIO()
                sf.write(output, speech, sample_rate, format='WAV', subtype='PCM_16')
                print(f'생성 완료 · 참고 분석 {preparation_seconds:.1f}초 · 음성 처리 {time.monotonic() - synthesis_started:.1f}초', flush=True)
                return Response(output.getvalue(), media_type='audio/wav', headers={
                    'X-CosyVoice-Version': SERVER_VERSION, 'X-Request-ID': request_id,
                    'X-Audio-Duration': f'{len(speech) / sample_rate:.3f}',
                    'X-Text-Chunks': str(len(chunks)),
                    'X-Reference-Cache': 'hit' if reference_hit else 'miss',
                    'X-Reference-ID': reference_id,
                    'X-Reference-Upload': 'sent' if payload is not None else 'skipped',
                    'X-Reference-Seconds': f'{preparation_seconds:.3f}',
                    'X-Synthesis-Seconds': f'{time.monotonic() - synthesis_started:.3f}',
                })
        except GeneratedAudioValidationError as exc:
            logging.warning('%s output validation failed: %s', LABEL, exc)
            raise HTTPException(502, {'code': 'audio_validation_failed', 'message': str(exc),
                                     'synthesis_started': True, 'automatic_retry': False}) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logging.exception('%s synthesis failed', LABEL)
            raise HTTPException(500, f'{type(exc).__name__}: {str(exc)[:600]}') from exc
        finally:
            if reference is not None:
                reference.file.close()
            if gpu_lock is not None:
                gpu_lock.close()
            model_lock.release()

    uvicorn.run(app, host='127.0.0.1', port=port, access_log=False)


def tunnel():
    state = read_state()
    print('음성 서버 준비 상태를 확인합니다…', flush=True)
    try:
        ready = bool(state.get('base')) and health(state['base'])
    except Exception:
        ready = False
    if not ready:
        raise RuntimeError('음성 서버가 아직 준비되지 않았거나 중지됐습니다. 1번 셀에서 준비 완료 메시지가 나온 뒤 4번을 실행해주세요.')
    if state.get('public_base'):
        try:
            if health(state['public_base']):
                print(LABEL + ' 프로그램 연결 주소:\n' + state['public_base'], flush=True)
                return
        except Exception:
            pass
    executable = ROOT / 'cloudflared'
    if not executable.is_file():
        print('연결 도구 다운로드 중…', flush=True)
        temp = executable.with_suffix('.download')
        urllib.request.urlretrieve('https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64', temp)
        temp.chmod(0o755)
        temp.replace(executable)
    log_path = ROOT / 'tunnel.log'
    print('외부 연결을 여는 중입니다. 연결 주소가 확인될 때까지 기다려주세요 (최대 약 2분).', flush=True)
    with log_path.open('w') as log:
        proc = subprocess.Popen([str(executable), 'tunnel', '--no-autoupdate', '--url',
                                 f'http://127.0.0.1:{state["port"]}'], stdout=log, stderr=subprocess.STDOUT)
    success = False
    try:
        deadline = time.monotonic() + 120
        next_notice = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError('연결 도구가 종료되었습니다:\n' + log_path.read_text(errors='replace')[-2000:])
            match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', log_path.read_text(errors='replace'))
            if match:
                public = match.group(0) + state['base'].split(str(state['port']), 1)[1]
                try:
                    if health(public):
                        state.update(public_base=public, tunnel_pid=proc.pid)
                        STATE.write_text(json.dumps(state))
                        print('\n✅ ' + LABEL + ' 프로그램 연결 준비 완료\n프로그램 연결 주소:\n' + public, flush=True)
                        print(f'위 주소 전체를 AI Voice Studio의 {LABEL} 접속 주소에 붙여 넣으세요.', flush=True)
                        success = True
                        return
                except Exception:
                    pass
            if time.monotonic() >= next_notice:
                print('연결 주소를 확인하는 중… 아직 연결 완료가 아닙니다.', flush=True)
                next_notice = time.monotonic() + 15
            time.sleep(2)
        raise RuntimeError('외부 연결을 열지 못했습니다. 코랩 내부의 2~3번 셀은 계속 사용할 수 있습니다.')
    finally:
        if not success and proc.poll() is None:
            proc.terminate()


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--setup', action='store_true')
    modes.add_argument('--serve', action='store_true')
    modes.add_argument('--tunnel', action='store_true')
    parser.add_argument('--port', type=int, default=50000)
    parser.add_argument('--engine', choices=['cosyvoice'], default='cosyvoice')
    args = parser.parse_args()
    if args.setup:
        try:
            setup()
        except Exception:
            if ROOT.is_dir():
                with (ROOT / 'setup.log').open('a', encoding='utf-8') as log:
                    traceback.print_exc(file=log)
            raise
    elif args.serve:
        serve(args.port)
    else:
        tunnel()


if __name__ == '__main__':
    main()
