"""Independent CosyVoice 3 Colab service; never starts or changes CosyVoice 2."""
import argparse
import base64
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

from cosy3_voicebank_catalog import (MODEL_ID, MODEL_REVISION, SOURCE_REVISION,
    SERVICE, PROTOCOL, BANK_REVISION, VOICEBANK, ATTRIBUTION)

ROOT = Path('/content/voice_studio_cosy3_v1')
SOURCE = ROOT / 'CosyVoice'
PYTHON = ROOT / 'venv/bin/python'
MODEL = SOURCE / 'pretrained_models/Fun-CosyVoice3-0.5B'
STATE = ROOT / 'state.json'
VERSION = '1.0.0'
MAX_REQUEST_BYTES = 128 * 1024
MAX_REFERENCE_BYTES = 10 * 1024 * 1024


def save_json(path, data):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def run(args, label):
    print('\n▶ ' + label, flush=True)
    with (ROOT / 'setup.log').open('a', encoding='utf-8') as log:
        log.write('\n▶ ' + label + '\n')
        process = subprocess.Popen([str(arg) for arg in args], stdout=subprocess.PIPE,
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
        raise RuntimeError(f'{label} 실패 (종료 코드 {code}). 위 오류를 확인해주세요.')


def local_request(path, payload=None, timeout=15):
    state = read_state()
    if not state.get('base'):
        raise RuntimeError('먼저 2번 셀에서 CosyVoice 3 서버를 준비해주세요.')
    req = urllib.request.Request(state['base'] + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def process_alive(pid):
    if not isinstance(pid, int):
        return False
    try:
        return not Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].startswith('Z')
    except (OSError, IndexError):
        return False


def setup():
    if not Path('/content').is_dir():
        raise RuntimeError('이 파일은 Google Colab에서 실행해주세요.')
    ROOT.mkdir(parents=True, exist_ok=True)
    marker = ROOT / 'installed.json'
    if marker.is_file() and PYTHON.is_file():
        print('✅ CosyVoice 3 설치 파일을 재사용합니다. 2번 서버 준비를 실행하세요.', flush=True)
        return
    if shutil.which('nvidia-smi') is None:
        raise RuntimeError('런타임 → 런타임 유형 변경 → T4 GPU를 먼저 선택해주세요.')
    run(['apt-get', 'update', '-qq'], '오디오 도구 준비')
    run(['apt-get', 'install', '-y', '-qq', 'ffmpeg', 'sox', 'libsox-dev',
         'libsndfile1', 'build-essential', 'git'], '오디오 도구 설치')
    if not PYTHON.is_file():
        run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', 'uv'], '독립 Python 준비')
        run([sys.executable, '-m', 'uv', 'python', 'install', '3.10'], 'Python 3.10 설치')
        run([sys.executable, '-m', 'uv', 'venv', '--python', '3.10', '--seed', ROOT / 'venv'], '버전 3 전용 실행 환경')
    if not (SOURCE / '.git').is_dir():
        run(['git', 'clone', '--filter=blob:none', 'https://github.com/QwenAudio/CosyVoice.git', SOURCE], '공식 소스 받기')
    run(['git', '-C', SOURCE, 'checkout', '--detach', SOURCE_REVISION], '소스 버전 고정')
    run(['git', '-C', SOURCE, 'submodule', 'update', '--init', '--recursive'], 'Matcha-TTS 준비')
    requirements = []
    for line in (SOURCE / 'requirements.txt').read_text().splitlines():
        if line.startswith(('deepspeed', 'tensorrt', 'gradio', 'fastapi-cli')):
            continue
        if line.startswith('diffusers=='):
            line = 'diffusers==0.32.2'
        requirements.append(line)
    requirements += ['huggingface-hub==0.30.2', 'setuptools<81']
    req = ROOT / 'inference-requirements.txt'
    req.write_text('\n'.join(requirements) + '\n')
    build = ROOT / 'build-constraints.txt'
    build.write_text('setuptools<81\n')
    run([PYTHON, '-m', 'pip', 'install', 'pip>=25.3,<26', 'setuptools<81', 'wheel', 'Cython<4'], '설치 도구 준비')
    run([PYTHON, '-m', 'pip', 'install', 'torch==2.3.1', 'torchaudio==2.3.1',
         '--index-url', 'https://download.pytorch.org/whl/cu121'], 'GPU 오디오 라이브러리 설치')
    run([PYTHON, '-m', 'pip', 'install', '-r', req, '--build-constraint', build], 'CosyVoice 3 라이브러리 설치')
    download = ('from huggingface_hub import snapshot_download; '
        f'snapshot_download({MODEL_ID!r}, revision={MODEL_REVISION!r}, local_dir={str(MODEL)!r}, '
        'allow_patterns=["cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt", "campplus.onnx", '
        '"speech_tokenizer_v3.onnx", "CosyVoice-BlankEN/*"])')
    run([PYTHON, '-c', download], 'CosyVoice 3 모델 다운로드 · 첫 설치 때만 필요')
    (ROOT / 'VOICE_ATTRIBUTION.txt').write_text(ATTRIBUTION, encoding='utf-8')
    save_json(marker, {'version': VERSION, 'model': MODEL_ID, 'revision': MODEL_REVISION})
    print('✅ 1번 설치 완료. 2번 서버 준비를 실행하세요.', flush=True)


def start():
    if not (ROOT / 'installed.json').is_file() or not PYTHON.is_file():
        raise RuntimeError('먼저 1번 설치를 끝까지 실행해주세요.')
    state = read_state()
    if process_alive(state.get('pid')):
        print('▶ 이미 실행한 CosyVoice 3 서버 준비를 이어서 확인합니다.', flush=True)
        wait_ready(state['pid'])
        return
    # Distinct folders do not make two GPU models independent on the same VM.
    for other in ('/content/ai_voice_dual_v1/cosyvoice', '/content/voice_studio_qwen_bank_v1',
                  '/content/ai_voice_sovits_v4_1'):
        path = Path(other) / 'state.json'
        if path.is_file():
            try:
                if process_alive(json.loads(path.read_text()).get('pid')):
                    raise RuntimeError('이 런타임에 다른 음성 엔진이 실행 중입니다. CosyVoice 3는 별도의 GPU 런타임에서 열어주세요.')
            except (ValueError, OSError):
                pass
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    state = {'port': port, 'token': token, 'base': f'http://127.0.0.1:{port}/v1/{token}',
             'service': SERVICE, 'version': VERSION}
    env = dict(os.environ, PYTHONUNBUFFERED='1', MPLBACKEND='Agg', TOKENIZERS_PARALLELISM='false',
               OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1', OMP_WAIT_POLICY='PASSIVE')
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(SOURCE), str(SOURCE / 'third_party/Matcha-TTS')])
    libs = [str(p) for p in (ROOT / 'venv/lib/python3.10/site-packages/nvidia').glob('*/lib')]
    env['LD_LIBRARY_PATH'] = os.pathsep.join(libs + [env.get('LD_LIBRARY_PATH', '')])
    save_json(STATE, state)
    with (ROOT / 'server.log').open('w') as log:
        proc = subprocess.Popen([str(PYTHON), '-u', str(Path(__file__).resolve()), 'serve'],
            cwd=SOURCE, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    state['pid'] = proc.pid
    save_json(STATE, state)
    wait_ready(proc.pid)


def wait_ready(pid):
    began, last = time.monotonic(), ''
    print('▶ 모델 준비 중입니다. 음성은 생성하지 않습니다.', flush=True)
    while time.monotonic() - began < 1800:
        try:
            info = local_request('/health', timeout=5)
            if info.get('ready') and info.get('service') == SERVICE and info.get('protocol') == PROTOCOL:
                print(f"✅ CosyVoice 3 v{VERSION} 준비 완료 · {info['gpu']} · FP32 · 목소리 20종", flush=True)
                print('사이트를 연결하려면 4번을, 코랩에서 직접 만들려면 3번을 실행하세요.', flush=True)
                return
        except (OSError, ValueError):
            pass
        log = (ROOT / 'server.log').read_text(errors='replace')[-2400:]
        if log != last:
            print(log if not last else log[-1000:], flush=True)
            last = log
        if not process_alive(pid):
            raise RuntimeError('CosyVoice 3 서버가 중단되었습니다. 위 실제 오류 로그를 확인해주세요.')
        time.sleep(3)
    raise RuntimeError('서버 준비가 30분을 넘었습니다. 5번 로그에서 마지막 단계를 확인해주세요.')


def tunnel():
    info = local_request('/health')
    if not info.get('ready') or info.get('service') != SERVICE:
        raise RuntimeError('먼저 2번 서버 준비를 끝내주세요.')
    state = read_state()
    if process_alive(state.get('tunnel_pid')) and state.get('public_url'):
        print('✅ CosyVoice 3 프로그램 연결 주소:\n' + state['public_url'], flush=True)
        return
    binary = ROOT / 'cloudflared'
    if not binary.is_file():
        urllib.request.urlretrieve('https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64', binary)
        binary.chmod(0o700)
    log_path = ROOT / 'tunnel.log'
    with log_path.open('w') as log:
        process = subprocess.Popen([str(binary), 'tunnel', '--url', f"http://127.0.0.1:{state['port']}",
            '--no-autoupdate'], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    state['tunnel_pid'] = process.pid
    save_json(STATE, state)
    for _ in range(60):
        match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', log_path.read_text(errors='replace'))
        if match:
            state['public_url'] = match.group() + '/v1/' + state['token']
            save_json(STATE, state)
            print('✅ CosyVoice 3 프로그램 연결 주소:\n' + state['public_url'], flush=True)
            print('사이트의 CosyVoice 3 주소 칸에 넣으세요. CosyVoice 2 주소와 별개입니다.', flush=True)
            return
        if process.poll() is not None:
            break
        time.sleep(1)
    raise RuntimeError('외부 연결 주소를 만들지 못했습니다. 5번 로그를 확인해주세요. 모델은 준비되어 있습니다.')


def serve():
    import soundfile as sf
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse
    import uvicorn
    from cosy3_model import Cosy3Model, spoken
    state = read_state()
    model = Cosy3Model(MODEL, ROOT)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    jobs, lock, pending = {}, threading.RLock(), queue.Queue(maxsize=32)
    job_dir, ref_dir = ROOT / 'jobs', ROOT / 'references'
    job_dir.mkdir(exist_ok=True)
    ref_dir.mkdir(exist_ok=True)

    def authorize(token):
        if not secrets.compare_digest(token, state['token']):
            raise HTTPException(404, '주소를 확인해주세요.')

    def public(job):
        return {key: value for key, value in job.items() if key not in ('payload', 'identity', 'file')}

    def persist(job_id):
        save_json(job_dir / (job_id + '.json'), jobs[job_id])

    def worker():
        while True:
            job_id = pending.get()
            try:
                with lock:
                    job = jobs[job_id]
                    if job['status'] == 'cancelled':
                        continue
                    job.update(status='generating', phase='음성 계산', started=time.time())
                    persist(job_id)
                    payload = dict(job['payload'])
                def progress(done, total, cached):
                    with lock:
                        job.update(chunks_done=done, chunks=total, reference_cached=cached)
                started = time.monotonic()
                reference = ref_dir / (payload.pop('reference_id') + '.wav') if payload.get('reference_id') else None
                payload.pop('reference_id', None)
                audio, sample_rate = model.generate(**payload, ref_path=str(reference) if reference else '', progress=progress)
                audio_path = job_dir / (job_id + '.wav')
                temp = audio_path.with_suffix('.part.wav')
                sf.write(temp, audio, sample_rate, subtype='PCM_16')
                os.replace(temp, audio_path)
                with lock:
                    job.update(status='done', phase='완료', file=str(audio_path),
                        synthesis_seconds=time.monotonic() - started, audio_seconds=len(audio) / sample_rate,
                        sample_rate=sample_rate, finished=time.time())
                    persist(job_id)
            except Exception as exc:
                traceback.print_exc()
                with lock:
                    jobs[job_id].update(status='error', error_code='memory' if 'out of memory' in str(exc).lower() else 'generation',
                        phase='오류', message=str(exc)[:500], finished=time.time())
                    persist(job_id)
            finally:
                pending.task_done()

    # One model compute lane; queued next line starts before HTTP download/save.
    # Avoid claiming unmeasured GPU parallelism or changing inference precision.
    threading.Thread(target=worker, name='cosy3-inference', daemon=True).start()

    @app.get('/v1/{token}/health')
    def health(token: str):
        authorize(token)
        return dict(service=SERVICE, protocol=PROTOCOL, version=VERSION, model=MODEL_ID,
            model_revision=MODEL_REVISION, bank_revision=BANK_REVISION, ready=True,
            gpu=model.gpu, precision='FP32', sample_rate=model.sample_rate,
            voice_count=len(VOICEBANK), voices=VOICEBANK, gpu_concurrency=1,
            queued=pending.qsize(), max_requests=4)

    async def body(request, maximum):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > maximum:
                raise HTTPException(413, '요청 파일이 너무 큽니다.')
        return bytes(data)

    @app.post('/v1/{token}/references/{reference_id}')
    async def upload_reference(token: str, reference_id: str, request: Request):
        authorize(token)
        if not re.fullmatch(r'[a-f0-9]{64}', reference_id):
            raise HTTPException(400, '참고 음성 ID 오류')
        data = await body(request, MAX_REFERENCE_BYTES)
        if hashlib.sha256(data).hexdigest() != reference_id:
            raise HTTPException(400, '참고 음성 파일이 완전하지 않습니다.')
        path = ref_dir / (reference_id + '.wav')
        if path.is_file():
            return dict(reference_id=reference_id)
        import io
        import numpy as np
        try:
            audio, rate = sf.read(io.BytesIO(data), dtype='float32', always_2d=True)
            if not 3 <= len(audio) / rate <= 30 or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 0.001:
                raise ValueError('3~30초의 또렷한 녹음이 필요합니다.')
        except Exception:
            raise HTTPException(400, '참고 음성을 읽지 못했습니다. 3~30초 WAV 또는 FLAC 파일을 사용해주세요.') from None
        # Normalize the container only; preserve reference sample rate and values.
        temporary = path.with_name('.' + reference_id + '.' + secrets.token_hex(4) + '.wav')
        sf.write(temporary, audio, rate, subtype='FLOAT')
        os.replace(temporary, path)
        return dict(reference_id=reference_id)

    def get_job(job_id):
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise HTTPException(400, '작업 ID 오류')
        if job_id not in jobs:
            path = job_dir / (job_id + '.json')
            if path.is_file():
                saved = json.loads(path.read_text())
                if saved.get('status') in ('queued', 'generating'):
                    saved.update(status='error', error_code='interrupted', message='서버 재시작으로 중단된 작업입니다.')
                jobs[job_id] = saved
        return jobs.get(job_id)

    @app.post('/v1/{token}/jobs/{job_id}')
    async def submit(token: str, job_id: str, request: Request):
        authorize(token)
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise HTTPException(400, '작업 ID 오류')
        try:
            raw = json.loads(await body(request, MAX_REQUEST_BYTES))
            if not isinstance(raw, dict):
                raise ValueError('요청 형식 오류')
            voice = str(raw.get('voice', ''))
            if voice not in VOICEBANK and voice != 'custom':
                raise ValueError('목소리를 다시 선택해주세요.')
            speed = float(raw.get('speed', 1))
            if not math.isfinite(speed) or not 0.8 <= speed <= 1.2:
                raise ValueError('속도 범위 오류')
            style = str(raw.get('style', ''))
            if len(style) > 1200 or '<|' in style or '|>' in style:
                raise ValueError('스타일 지시 오류')
            payload = dict(text=spoken(raw.get('text', '')), voice=voice, speed=speed, style=style,
                           prompt_text='', reference_id='')
            if voice == 'custom':
                ref_id = str(raw.get('reference_id', ''))
                if not re.fullmatch(r'[a-f0-9]{64}', ref_id) or not (ref_dir / (ref_id + '.wav')).is_file():
                    raise ValueError('참고 음성을 먼저 등록해주세요.')
                payload.update(reference_id=ref_id, prompt_text=spoken(raw.get('prompt_text', ''), 2000))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from None
        identity = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with lock:
            old = get_job(job_id)
            if old:
                if old.get('identity') != identity:
                    raise HTTPException(409, '같은 작업 ID에 다른 대사가 전달됐습니다.')
                return public(old)
            if pending.full() or shutil.disk_usage(ROOT).free < 512 * 1024 * 1024:
                raise HTTPException(429, '대기 작업 또는 저장 공간이 부족합니다. 완료 작업을 저장한 뒤 이어서 생성해주세요.')
            job = dict(status='queued', phase='대기', payload=payload, identity=identity,
                       created=time.time(), chunks_done=0, chunks=1, gpu_concurrency=1)
            jobs[job_id] = job
            persist(job_id)
            pending.put_nowait(job_id)
            return public(job)

    @app.get('/v1/{token}/jobs/{job_id}')
    def status(token: str, job_id: str):
        authorize(token)
        with lock:
            job = get_job(job_id)
            if not job:
                raise HTTPException(404, '작업을 찾을 수 없습니다.')
            return public(job)

    @app.post('/v1/{token}/jobs/{job_id}/cancel')
    def cancel(token: str, job_id: str):
        authorize(token)
        with lock:
            job = get_job(job_id)
            if not job:
                raise HTTPException(404, '작업을 찾을 수 없습니다.')
            if job['status'] == 'queued':
                job.update(status='cancelled', phase='취소', finished=time.time())
                persist(job_id)
            return public(job)  # active calculation finishes and is saved.

    @app.get('/v1/{token}/jobs/{job_id}/audio')
    def audio(token: str, job_id: str):
        authorize(token)
        with lock:
            job = get_job(job_id)
            if not job or job.get('status') != 'done' or not Path(job.get('file', '')).is_file():
                raise HTTPException(404, '완료된 음성 파일이 없습니다.')
            return FileResponse(job['file'], media_type='audio/wav', filename=job_id + '.wav')

    uvicorn.run(app, host='127.0.0.1', port=state['port'], access_log=False)


def direct(plan_path):
    """Native Colab execution via the same private local API, no public tunnel."""
    import io
    import wave
    import zipfile
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    if plan.get('format') != 'voice-studio-cosy3-plan-v1' or plan.get('bank_revision') != BANK_REVISION:
        raise ValueError('사이트에서 받은 CosyVoice 3 대본 ZIP을 사용해주세요.')
    info = local_request('/health')
    if info.get('service') != SERVICE:
        raise RuntimeError('CosyVoice 3 서버를 먼저 준비해주세요.')
    items = plan.get('items', [])
    if not items or len(items) > 4096:
        raise ValueError('대본은 1~4096개 대사까지 지원합니다.')
    indices = [item['index'] for item in items]
    if len(set(indices)) != len(indices) or any(type(i) is not int or i < 1 for i in indices):
        raise ValueError('대사 순번이 올바르지 않습니다.')
    references = plan.get('references', {})
    base = read_state()['base']
    for ref_id, encoded in references.items():
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_REFERENCE_BYTES or hashlib.sha256(data).hexdigest() != ref_id:
            raise ValueError('대본의 참고 음성이 올바르지 않습니다.')
        req = urllib.request.Request(base + '/references/' + ref_id, data=data, method='POST')
        with urllib.request.urlopen(req, timeout=90) as response:
            response.read()
    output = ROOT / 'direct_results'
    output.mkdir(exist_ok=True)
    attempts_path = output / 'job_ids.json'
    try:
        attempts = json.loads(attempts_path.read_text())
    except (OSError, ValueError):
        attempts = {}
    ordered = sorted(items, key=lambda item: item['index'])
    paths = []
    for position, item in enumerate(ordered, 1):
        payload = {key: value for key, value in item.items() if key not in ('index', 'speaker')}
        identity = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256((BANK_REVISION + identity).encode()).hexdigest()[:32]
        job_id = attempts.get(key, key)
        status = local_request('/jobs/' + job_id, payload)
        if status.get('status') in ('error', 'cancelled'):
            # A fresh run explicitly resumes a previous failed line. Never
            # automatically retry an error produced during this run.
            job_id = secrets.token_hex(16)
            attempts[key] = job_id
            save_json(attempts_path, attempts)
            status = local_request('/jobs/' + job_id, payload)
        started = time.monotonic()
        while status.get('status') != 'done':
            if status.get('status') in ('error', 'cancelled'):
                raise RuntimeError(f"{item['index']}번 중단: {status.get('message', status['status'])}")
            if time.monotonic() - started > 1800:
                raise RuntimeError('처리 대기가 30분을 넘었습니다. 5번 로그를 확인해주세요.')
            time.sleep(1)
            status = local_request('/jobs/' + job_id)
        path = output / (f"{item['index']:04d}_" + job_id + '.wav')
        with urllib.request.urlopen(base + '/jobs/' + job_id + '/audio', timeout=90) as response:
            path.write_bytes(response.read(128 * 1024 * 1024))
        paths.append(path)
        print(f"✅ {position}/{len(ordered)} · {item['speaker']}", flush=True)
    full = output / 'full_audio.wav'
    pause_ms = min(3000, max(0, int(plan.get('pause_ms', 500))))
    with wave.open(str(full), 'wb') as dst:
        for number, path in enumerate(paths):
            with wave.open(str(path), 'rb') as src:
                if number == 0:
                    params = src.getparams()
                    dst.setparams(params)
                elif src.getparams()[:3] != params[:3]:
                    raise RuntimeError('음성 형식이 달라 합치기를 중단했습니다.')
                if number:
                    dst.writeframes(b'\0' * (params.nchannels * params.sampwidth * params.framerate * pause_ms // 1000))
                dst.writeframes(src.readframes(src.getnframes()))
    mp3 = output / 'full_audio.mp3'
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', full, '-b:a', '192k', mp3], '대본 순서대로 MP3 한 파일 저장')
    bundle = output / 'CosyVoice3_audio.zip'
    with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(mp3, 'full_audio.mp3')
        archive.writestr('VOICE_ATTRIBUTION.txt', ATTRIBUTION)
    print('✅ 완성: ' + str(bundle), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['setup', 'start', 'serve', 'tunnel', 'direct'])
    parser.add_argument('--plan')
    args = parser.parse_args()
    if args.command == 'direct':
        direct(args.plan)
    else:
        globals()[args.command]()
