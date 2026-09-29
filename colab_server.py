# -*- coding: utf-8 -*-
"""Self-contained CosyVoice 2 Colab runner. No edits to upstream model code.

--setup: install an isolated Python 3.10 environment and start the local API.
--tunnel: optionally expose the ready API to AI Voice Studio.
--serve: internal worker, launched with the isolated Python interpreter.
"""
import argparse
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
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
SERVER_VERSION = '2.9.1'
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


def health(base, require_style=True):
    with urllib.request.urlopen(base + '/health', timeout=5) as response:
        data = json.load(response)
    return (data.get('service') == SERVICE and data.get('ready') is True
            and (not require_style or 'style_instruction' in data.get('capabilities', [])))


def setup():
    if not Path('/content').is_dir():
        raise RuntimeError('이 파일은 Google Colab에서 실행해주세요.')
    ROOT.mkdir(parents=True, exist_ok=True)
    state = read_state()
    existing_ready = False
    try:
        existing_ready = bool(state.get('base')) and health(state['base'], require_style=False)
    except Exception:
        pass
    if existing_ready:
        if health(state['base']):
            print(f'✅ {LABEL} v{SERVER_VERSION} 준비 완료. 아래 음성 생성 셀을 실행하세요.', flush=True)
            return
        raise RuntimeError('이전 CosyVoice 서버가 실행 중입니다. 런타임 → 세션 다시 시작 후 이 노트북의 1번을 실행해주세요. 설치한 모델은 같은 런타임에 유지됩니다.')
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
                                  cwd=SOURCE, env=env, stdout=log, stderr=subprocess.STDOUT)
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
    access_token = os.environ['COSY_ACCESS_TOKEN']
    model_lock = threading.Lock()
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
                'server_version': SERVER_VERSION, 'capabilities': ['style_instruction']}

    @app.post('/v1/{token}/synthesize')
    def synthesize(token: str, text: str = Form(...), prompt_text: str = Form(''),
                   speed: float = Form(1.0), reference: UploadFile = File(...),
                   style_instruction: str = Form('')):
        authorize(token)
        if not text.strip():
            raise HTTPException(422, '생성할 대사를 입력해주세요.')
        if not prompt_text.strip():
            raise HTTPException(422, 'CosyVoice는 참조 음성의 실제 대사도 입력해야 합니다.')
        if len(text) > 2000:
            raise HTTPException(422, '한 번에 2,000자 이하로 나눠 생성해주세요.')
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
            payload = reference.file.read(10 * 1024 * 1024 + 1)
            if not payload or len(payload) > 10 * 1024 * 1024:
                raise HTTPException(422, '참조 음성은 10MB 이하 WAV 또는 MP3를 사용해주세요.')
            with tempfile.TemporaryDirectory(prefix='cosy_ref_') as folder:
                original = Path(folder) / 'reference.audio'
                original.write_bytes(payload)
                prepared = Path(folder) / 'reference.wav'
                result = subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(original),
                                         '-t', '31', '-ac', '1', '-ar', '24000', str(prepared)],
                                        capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise HTTPException(422, '참조 오디오를 읽을 수 없습니다. WAV 또는 MP3를 확인해주세요.')
                audio, sr = sf.read(prepared, dtype='float32')
                duration = len(audio) / sr
                if not 3 <= duration <= 30:
                    raise HTTPException(422, f'참조 음성은 3~30초여야 합니다. 현재 {duration:.1f}초입니다.')
                if not np.all(np.isfinite(audio)) or np.max(np.abs(audio)) < 0.00001:
                    raise HTTPException(422, '참조 음성에 들리는 목소리가 없습니다.')
                pieces = []
                sentences = re.split(r'(?<=[.!?。！？])\s+|\n+', text.strip())
                chunks = []
                for sentence in sentences:
                    sentence = sentence.strip()
                    while len(sentence) > 180:
                        cut = sentence.rfind(' ', 60, 180)
                        cut = cut if cut >= 60 else 180
                        chunks.append(sentence[:cut])
                        sentence = sentence[cut:].strip()
                    if sentence:
                        chunks.append(sentence)
                with torch.inference_mode():
                    for chunk in chunks:
                        if style_instruction:
                            # The pinned upstream example adds this delimiter at the caller.
                            # Instruction and reference transcript must never be concatenated.
                            instruction = 'Speak in Korean. ' + style_instruction + '<|endofprompt|>'
                            generated = model.inference_instruct2(chunk, instruction, str(prepared),
                                                                 stream=False, speed=speed, text_frontend=False)
                        else:
                            generated = model.inference_zero_shot(chunk, prompt_text.strip(), str(prepared),
                                                                  stream=False, speed=speed, text_frontend=False)
                        for item in generated:
                            pieces.append(item['tts_speech'].detach().cpu().numpy().reshape(-1))
                if not pieces or sum(x.size for x in pieces) == 0:
                    raise RuntimeError('모델이 빈 음성을 반환했습니다. 참조 음성과 실제 대사를 확인해주세요.')
                speech = np.concatenate(pieces)
                if not np.all(np.isfinite(speech)) or np.max(np.abs(speech)) < 0.000001:
                    raise RuntimeError('모델 출력이 무음이거나 손상되었습니다. server.log를 확인해주세요.')
                output = io.BytesIO()
                sf.write(output, speech, sample_rate, format='WAV', subtype='PCM_16')
                return Response(output.getvalue(), media_type='audio/wav')
        except HTTPException:
            raise
        except Exception as exc:
            logging.exception('%s synthesis failed', LABEL)
            raise HTTPException(500, f'{type(exc).__name__}: {str(exc)[:600]}') from exc
        finally:
            reference.file.close()
            if gpu_lock is not None:
                gpu_lock.close()
            model_lock.release()

    uvicorn.run(app, host='127.0.0.1', port=port, access_log=False)


def tunnel():
    state = read_state()
    if not state.get('base') or not health(state['base']):
        raise RuntimeError('먼저 1번 설치 셀을 실행해주세요.')
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
    with log_path.open('w') as log:
        proc = subprocess.Popen([str(executable), 'tunnel', '--no-autoupdate', '--url',
                                 f'http://127.0.0.1:{state["port"]}'], stdout=log, stderr=subprocess.STDOUT)
    success = False
    try:
        deadline = time.monotonic() + 120
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
