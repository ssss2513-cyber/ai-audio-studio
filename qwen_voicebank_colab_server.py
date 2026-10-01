"""Personal Colab runner for 20 designed Korean voices + Qwen 1.7B Base.

This module is copied into the notebook, so a downloaded notebook is complete.
Importing this module never installs packages or starts inference.
"""
import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from qwen_voicebank_catalog import VOICEBANK, BASE_MODEL, BANK_REVISION

ROOT = Path('/content/voice_studio_qwen_bank_v1')
PYTHON = ROOT / 'venv/bin/python'
STATE = ROOT / 'state.json'
MODEL_ID = BASE_MODEL
SERVICE = 'voice-studio-qwen-voicebank'
VERSION = '1.0.0'
VOICES = tuple(VOICEBANK)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)


def local_request(path, data=None):
    state = json.loads(STATE.read_text())
    url = f"http://127.0.0.1:{state['port']}/v1/{state['token']}" + path
    request = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def setup():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = ROOT / 'installed.json'
    if marker.is_file() and PYTHON.is_file() and json.loads(marker.read_text()).get('version') == VERSION:
        print('✅ Qwen 설치 파일이 준비되어 있습니다. 2번 셀을 실행하세요.', flush=True)
        return
    import shutil
    if not shutil.which('nvidia-smi') or subprocess.run(['nvidia-smi'], capture_output=True).returncode != 0:
        raise RuntimeError('런타임 → 런타임 유형 변경 → T4 GPU를 선택한 뒤 다시 실행하세요.')
    print('▶ 1/3 Qwen 전용 설치 공간 준비', flush=True)
    # No ensurepip/apt Python-version dependency; pip --python can bootstrap a venv.
    subprocess.check_call([sys.executable, '-m', 'venv', '--without-pip', str(ROOT / 'venv')])
    pip = [sys.executable, '-m', 'pip', '--python', str(PYTHON)]
    print('▶ 2/3 PyTorch 설치 · 다운로드 중에는 시간이 걸립니다.', flush=True)
    subprocess.check_call(pip + ['install', 'torch==2.8.0', 'torchaudio==2.8.0',
                                '--index-url', 'https://download.pytorch.org/whl/cu126'])
    print('▶ 3/3 Qwen 기본 목소리와 연결 도구 설치', flush=True)
    subprocess.check_call(pip + ['install', 'qwen-tts==0.1.1', 'fastapi>=0.115,<1',
                                'uvicorn>=0.30,<1', 'soundfile>=0.12,<1'])
    # sox is an upstream runtime dependency, separate from its Python wrapper.
    subprocess.check_call(['apt-get', '-qq', 'update'])
    subprocess.check_call(['apt-get', '-qq', 'install', '-y', 'sox', 'ffmpeg'])
    save_json(marker, {'version': VERSION})
    print('✅ 설치 완료. 2번 셀에서 모델을 준비하세요.', flush=True)


def start():
    if not PYTHON.is_file() or not (ROOT / 'installed.json').is_file():
        raise RuntimeError('먼저 1번 설치 셀을 끝까지 실행해주세요.')
    state = {}
    if STATE.is_file():
        state = json.loads(STATE.read_text())
        try:
            status = local_request('/health')
            if status.get('service') == SERVICE and status.get('version') == VERSION and status.get('ready'):
                print('✅ 기존 Qwen 모델을 재사용합니다. 3번(직접 생성) 또는 4번(사이트 연결)으로 이동하세요.')
                return
        except (OSError, ValueError):
            pass
        # An earlier cell may have been interrupted while its child still loads.
        pid = state.get('pid')
        command = Path(f'/proc/{pid}/cmdline') if isinstance(pid, int) else None
        if command and command.is_file() and b'qwen_voicebank_colab_server.py\x00serve' in command.read_bytes():
            print('▶ 이미 시작한 Qwen 모델 준비를 이어서 기다립니다.', flush=True)
            wait_ready(pid)
            return
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    state = {'port': port, 'token': secrets.token_urlsafe(32), 'version': VERSION}
    save_json(STATE, state)
    environment = dict(os.environ, PYTHONUNBUFFERED='1', TOKENIZERS_PARALLELISM='false',
                       HF_HOME=str(ROOT / 'hf_cache'), PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True')
    with (ROOT / 'server.log').open('w') as log:
        process = subprocess.Popen([str(PYTHON), str(Path(__file__).resolve()), 'serve'],
                                   stdout=log, stderr=subprocess.STDOUT, env=environment, start_new_session=True)
    state['pid'] = process.pid
    save_json(STATE, state)
    wait_ready(process.pid)


def wait_ready(pid):
    print('▶ 기본 목소리 20종 서버를 시작합니다. 모델과 선택한 목소리는 첫 생성 때 준비합니다.', flush=True)
    started, last_log = time.monotonic(), ''
    while time.monotonic() - started < 1800:
        try:
            status = local_request('/health')
            if status.get('ready') and status.get('service') == SERVICE:
                print(f"✅ 서버 준비 완료 · {status['gpu']} · {status['precision']} · GPU 배치 최대 {status['batch_size']}개", flush=True)
                print('선택한 목소리를 처음 사용할 때 VoiceDesign → Base 준비 시간이 추가됩니다. 같은 목소리는 재사용합니다.', flush=True)
                print('공유 사이트는 4번 셀, 코랩 안에서 직접 생성하려면 3번 셀을 실행하세요.', flush=True)
                return
        except (OSError, ValueError):
            pass
        log = (ROOT / 'server.log').read_text(errors='replace')[-3500:]
        if log != last_log:
            print(log if not last_log else log[-1200:], flush=True)
            last_log = log
        status_path = Path(f'/proc/{pid}/status')
        if not status_path.exists() or re.search(r'^State:\s+Z', status_path.read_text(), re.M):
            raise RuntimeError('Qwen 모델 준비가 중단되었습니다. 바로 위 오류를 확인해주세요.')
        time.sleep(3)
    raise RuntimeError('모델 준비가 30분을 넘었습니다. server.log의 다운로드·GPU 오류를 확인해주세요. 자동으로 재시작하지 않았습니다.')


def tunnel():
    status = local_request('/health')
    if not status.get('ready') or status.get('service') != SERVICE:
        raise RuntimeError('먼저 2번 셀의 모델 준비 완료를 기다려주세요.')
    state = json.loads(STATE.read_text())
    previous = state.get('tunnel_pid')
    command = Path(f'/proc/{previous}/cmdline') if isinstance(previous, int) else None
    if command and command.is_file() and b'cloudflared' in command.read_bytes() and state.get('public_url'):
        print('✅ 프로그램 연결 주소 (아래 전체를 Qwen 코랩 주소 칸에 넣으세요)\n' + state['public_url'])
        return
    binary = ROOT / 'cloudflared'
    if not binary.is_file():
        print('▶ Cloudflare 연결 도구 다운로드', flush=True)
        urllib.request.urlretrieve('https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64', binary)
        binary.chmod(0o700)
    log_path = ROOT / 'tunnel.log'
    with log_path.open('w') as log:
        process = subprocess.Popen([str(binary), 'tunnel', '--url', f"http://127.0.0.1:{state['port']}",
                                    '--no-autoupdate'], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    state['tunnel_pid'] = process.pid
    save_json(STATE, state)
    for _ in range(60):
        output = log_path.read_text(errors='replace')
        match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', output)
        if match:
            state['public_url'] = match.group() + '/v1/' + state['token']
            save_json(STATE, state)
            print('✅ Qwen 공유 사이트 연결 준비 완료\n프로그램 연결 주소:\n' + state['public_url'], flush=True)
            print('이 셀이 ▶로 돌아와도 서버는 실행 중입니다. 코랩 런타임은 유지해주세요.')
            return
        if process.poll() is not None:
            raise RuntimeError('공유 연결을 만들지 못했습니다. 3번 셀에서 직접 생성할 수 있습니다.\n' + output[-1000:])
        time.sleep(1)
    process.terminate()
    raise RuntimeError('연결 주소를 받지 못했습니다. 4번 셀을 다시 실행하거나 3번 셀에서 직접 생성해주세요.')


def split_text(text, maximum=240):
    parts = []
    while len(text) > maximum:
        candidates = [m.end() for m in re.finditer(r'[.!?。！？]\s+|\n+', text[:maximum + 1]) if m.end() >= 40]
        cut = candidates[-1] if candidates else text.rfind(' ', 40, maximum + 1)
        if cut < 40:
            cut = maximum
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def join_wavs(paths, output, pause_ms=0):
    parameters = None
    with wave.open(str(output), 'wb') as writer:
        for number, path in enumerate(paths):
            with wave.open(str(path), 'rb') as reader:
                current = (reader.getnchannels(), reader.getsampwidth(), reader.getframerate())
                if parameters is None:
                    parameters = current
                    writer.setnchannels(current[0])
                    writer.setsampwidth(current[1])
                    writer.setframerate(current[2])
                elif current != parameters:
                    raise RuntimeError('음성 형식이 달라 원음 그대로 합칠 수 없습니다.')
                if number and pause_ms:
                    writer.writeframes(b'\0' * (current[2] * pause_ms // 1000) * current[0] * current[1])
                while True:
                    data = reader.readframes(65536)
                    if not data:
                        break
                    writer.writeframes(data)


def serve():
    import gc
    import numpy as np
    import soundfile as sf
    import torch
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse
    import uvicorn
    from qwen_voicebank_model import VoiceBankModel

    if not torch.cuda.is_available():
        raise RuntimeError('Qwen은 GPU 런타임을 사용합니다. CPU로 자동 전환하지 않았습니다.')
    # T4 lacks native BF16. Keep full precision there instead of forcing FP16.
    dtype = (torch.bfloat16 if torch.cuda.get_device_capability(0)[0] >= 8
             and torch.cuda.is_bf16_supported() else torch.float32)
    model = VoiceBankModel(ROOT, dtype)
    print('✅ 기본 목소리 서버 준비 완료. 생성 요청 때 선택한 목소리만 준비합니다.', flush=True)
    state = json.loads(STATE.read_text())
    audio_dir = ROOT / 'audio'
    audio_dir.mkdir(exist_ok=True, mode=0o700)
    jobs, lock, pending = {}, threading.RLock(), queue.Queue()
    capacity = {'batch_size': 2}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    prefix = '/v1/' + state['token']

    def describe(job):
        return {key: job[key] for key in ('status', 'chunks_done', 'chunks', 'synthesis_seconds', 'batch_size', 'error_code', 'phase')}

    def find_job(job_id):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, '작업 없음')
        return job

    @app.get(prefix + '/health')
    def health():
        return dict(service=SERVICE, version=VERSION, protocol=1, model=MODEL_ID, ready=True,
                    gpu=torch.cuda.get_device_name(0), precision=str(dtype), batch_size=capacity['batch_size'],
                    bank_revision=BANK_REVISION, voice_count=len(VOICES))

    @app.post(prefix + '/prepare/{job_id}')
    async def prepare(job_id: str, request: Request):
        if not re.fullmatch('[a-f0-9]{32}', job_id):
            raise HTTPException(400, '작업 번호 오류')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                raise HTTPException(413, '요청이 너무 큼')
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            raise HTTPException(400, 'JSON 형식 오류')
        voices = data.get('voices') if isinstance(data, dict) else None
        if (not isinstance(voices, list) or not 1 <= len(voices) <= 20
                or any(not isinstance(voice, str) or voice not in VOICES for voice in voices)):
            raise HTTPException(400, '기본 목소리 선택 오류')
        voices = sorted(set(voices))
        digest = hashlib.sha256(json.dumps(voices).encode()).hexdigest()
        with lock:
            if job_id in jobs:
                job = jobs[job_id]
                if job.get('kind') != 'prepare' or job['digest'] != digest:
                    raise HTTPException(409, '작업 번호 중복')
                if job['status'] not in ('error', 'cancelled'):
                    return describe(job)
            if sum(job['status'] in ('queued', 'generating') for job in jobs.values()) >= 64 or len(jobs) >= 10000:
                raise HTTPException(429, '작업 대기열이 가득 참')
            job = dict(id=job_id, digest=digest, kind='prepare', voices=voices, status='queued',
                       chunks=len(voices), chunks_done=0, synthesis_seconds=0.0, batch_size=1,
                       error_code='', phase='기본 목소리 준비 대기', cancel=False)
            jobs[job_id] = job
            pending.put_nowait(job)
            return describe(job)

    @app.post(prefix + '/jobs/{job_id}')
    async def submit(job_id: str, request: Request):
        if not re.fullmatch('[a-f0-9]{32}', job_id):
            raise HTTPException(400, '작업 번호 오류')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 100000:
                raise HTTPException(413, '요청이 너무 큼')
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            raise HTTPException(400, 'JSON 형식 오류')
        if not isinstance(data, dict):
            raise HTTPException(400, '요청 형식 오류')
        text, voice, instruct = data.get('text'), data.get('voice'), data.get('instruct', '')
        if (not isinstance(text, str) or not 1 <= len(text.strip()) <= 12000
                or voice not in VOICES or instruct != ''):
            raise HTTPException(400, '대사·목소리·스타일 오류')
        if not model.has_reference(voice):
            raise HTTPException(409, '먼저 선택한 기본 목소리를 준비해주세요.')
        digest = hashlib.sha256(json.dumps([text, voice, instruct], ensure_ascii=False).encode()).hexdigest()
        with lock:
            if job_id in jobs:
                job = jobs[job_id]
                if job.get('kind') != 'speech' or digest != job['digest']:
                    raise HTTPException(409, '작업 번호 중복')
                if job['status'] not in ('error', 'cancelled'):
                    return describe(job)
                del jobs[job_id]  # An explicit resubmission resumes a failed line.
            if sum(job['status'] in ('queued', 'generating') for job in jobs.values()) >= 64 or len(jobs) >= 10000:
                raise HTTPException(429, '코랩 작업 대기열이 가득 참')
            texts = split_text(text.strip())
            job = dict(id=job_id, digest=digest, kind='speech', texts=texts, voice=voice, instruct=instruct,
                       status='queued', chunks=len(texts), chunks_done=0, synthesis_seconds=0.0,
                       batch_size=capacity['batch_size'], error_code='', phase='대사 생성', paths=[])
            jobs[job_id] = job
            pending.put_nowait(job)
            return describe(job)

    @app.get(prefix + '/jobs/{job_id}')
    def job_status(job_id: str):
        with lock:
            return describe(find_job(job_id))

    @app.post(prefix + '/jobs/{job_id}/cancel')
    def cancel_job(job_id: str):
        with lock:
            job = find_job(job_id)
            # Finish and save an already-started line; cancel only untouched work.
            if job.get('kind') == 'prepare' and job['status'] in ('queued', 'generating'):
                job['cancel'] = True
            if job['status'] == 'queued' and not job['chunks_done']:
                job['status'] = 'cancelled'
            return describe(job)

    @app.get(prefix + '/jobs/{job_id}/audio')
    def audio(job_id: str):
        with lock:
            job = find_job(job_id)
            if job['status'] != 'done' or job.get('kind') != 'speech':
                raise HTTPException(409, '아직 생성 중')
        return FileResponse(str(audio_dir / (job_id + '.wav')), media_type='audio/wav')

    def generate_batch(batch):
        began = time.monotonic()
        # Only this worker enters the model. Native batching avoids unsafe model
        # calls from concurrent HTTP threads and repeated model loading.
        with torch.inference_mode():
            wavs, rate = model.generate_custom_voice(
                text=[job['texts'][job['chunks_done']] for job in batch],
                language=['Korean'] * len(batch), speaker=[job['voice'] for job in batch],
                instruct=[job['instruct'] for job in batch], non_streaming_mode=True)
        if len(wavs) != len(batch):
            raise RuntimeError('batch_count')
        elapsed = time.monotonic() - began
        for job, wav in zip(batch, wavs):
            if not len(wav) or not np.isfinite(wav).all():
                with lock:
                    job.update(status='error', error_code='invalid_audio')
                continue
            part = audio_dir / f"{job['id']}_{job['chunks_done']}.wav"
            sf.write(str(part), wav, rate, subtype='PCM_16')
            with lock:
                job['paths'].append(part)
                job['chunks_done'] += 1
                job['synthesis_seconds'] += elapsed
                job['batch_size'] = len(batch)
                if job['chunks_done'] == job['chunks']:
                    output = audio_dir / (job['id'] + '.wav')
                    join_wavs(job['paths'], output)
                    job['status'] = 'done'
                    for path in job['paths']:
                        path.unlink(missing_ok=True)
                    job['paths'] = []
                    job['texts'] = []
                else:
                    pending.put_nowait(job)

    def process(batch):
        memory_failure = False
        try:
            generate_batch(batch)
        except torch.cuda.OutOfMemoryError:
            memory_failure = True
        except Exception:
            import traceback
            traceback.print_exc()
            with lock:
                for job in batch:
                    if job['status'] != 'done':
                        job.update(status='error', error_code='generation')
        # Leave the exception handler before retrying, releasing its traceback
        # and tensors so the smaller batch can actually reclaim GPU memory.
        if memory_failure:
            gc.collect()
            torch.cuda.empty_cache()
            if len(batch) > 1:
                capacity['batch_size'] = 1
                print('GPU 메모리 부족: 모델·정밀도는 유지하고 배치 크기만 1개로 줄입니다.', flush=True)
                for job in batch:
                    process([job])
            else:
                with lock:
                    batch[0].update(status='error', error_code='memory')

    def worker():
        while True:
            first = pending.get()
            if first.get('kind') == 'prepare':
                from concurrent.futures import CancelledError
                with lock:
                    if first['status'] in ('cancelled', 'error', 'done'):
                        continue
                    first['status'] = 'generating'
                def progress(phase, done, total):
                    with lock:
                        first.update(phase=phase, chunks_done=done, chunks=total)
                try:
                    model.prepare(first['voices'], progress, lambda: first['cancel'])
                    with lock:
                        first['status'] = 'done'
                except CancelledError:
                    with lock:
                        first['status'] = 'cancelled'
                except Exception:
                    import traceback
                    traceback.print_exc()
                    with lock:
                        first.update(status='error', error_code='preparation')
                gc.collect()
                torch.cuda.empty_cache()
                continue
            batch = [first]
            deadline = time.monotonic() + 0.12
            while len(batch) < capacity['batch_size']:
                try:
                    candidate = pending.get(timeout=max(0.001, deadline - time.monotonic()))
                    if candidate.get('kind') == 'prepare':
                        pending.put_nowait(candidate)
                        break
                    batch.append(candidate)
                except queue.Empty:
                    break
                if time.monotonic() >= deadline:
                    break
            with lock:
                batch = [job for job in batch if job['status'] not in ('cancelled', 'done', 'error')]
                for job in batch:
                    job['status'] = 'generating'
            if batch:
                process(batch)

    threading.Thread(target=worker, name='qwen-native-batch', daemon=True).start()
    uvicorn.run(app, host='127.0.0.1', port=state['port'], access_log=False, log_level='warning')


def direct(plan_path):
    """Generate inside a Colab cell, without any external web connection."""
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
    if (plan.get('format') != 'voice-studio-qwen-plan-v1' or plan.get('model') != MODEL_ID
            or plan.get('bank_revision') != BANK_REVISION):
        raise ValueError('공유 사이트에서 받은 Qwen 코랩 직접 생성용 JSON 파일을 올려주세요.')
    items = plan.get('items', [])
    if not items or len(items) > 5000:
        raise ValueError('대사는 1~5,000개까지 넣어주세요.')
    indices = [item['index'] for item in items]
    if any(type(index) is not int or index <= 0 for index in indices) or len(set(indices)) != len(indices):
        raise ValueError('대사 번호가 올바르지 않습니다.')
    items.sort(key=lambda item: item['index'])
    voices = sorted(set(item['voice'] for item in items))
    prepare_id = hashlib.sha256(('prepare:' + BANK_REVISION + json.dumps(voices)).encode()).hexdigest()[:32]
    status = local_request('/prepare/' + prepare_id, {'voices': voices})
    started, last_message = time.monotonic(), ''
    while status['status'] != 'done':
        if status['status'] in ('error', 'cancelled'):
            raise RuntimeError('기본 목소리 준비가 중단되었습니다. 5번 오류 로그를 확인해주세요.')
        message = f"{status.get('phase', '준비 중')} · {status.get('chunks_done', 0)}/{len(voices)}"
        if message != last_message:
            print(message, flush=True)
            last_message = message
        if time.monotonic() - started > 7200:
            raise RuntimeError('목소리 준비 응답이 2시간을 넘었습니다. 5번 오류 로그를 확인해주세요.')
        time.sleep(1)
        status = local_request('/jobs/' + prepare_id)
    pause_ms = max(0, min(3000, int(plan.get('pause_ms', 500))))
    folder = ROOT / 'direct' / hashlib.sha256(Path(plan_path).read_bytes()).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    waiting, active, finished = deque(items), {}, {}
    while waiting or active:
        while waiting and len(active) < 4:
            item = waiting.popleft()
            output = folder / f"{item['index']:04d}.wav"
            if output.is_file():
                finished[item['index']] = output
                continue
            job_id = hashlib.sha256((str(folder) + str(item['index'])).encode()).hexdigest()[:32]
            payload = {name: item[name] for name in ('text', 'voice', 'instruct')}
            local_request('/jobs/' + job_id, payload)
            active[job_id] = (item, output, time.monotonic())
        for job_id, (item, output, submitted_at) in list(active.items()):
            status = local_request('/jobs/' + job_id)
            if status['status'] in ('error', 'cancelled'):
                raise RuntimeError(f"{item['index']}번 생성 중단: {status.get('error_code')}. 완료 음성은 {folder}에 보관했습니다.")
            if time.monotonic() - submitted_at > 1800:
                raise RuntimeError(f"{item['index']}번이 30분 동안 완료되지 않았습니다. 5번 셀에서 로그를 확인해주세요. 완료 음성은 보관합니다.")
            if status['status'] == 'done':
                import shutil
                temporary = output.with_suffix('.part.wav')
                shutil.copyfile(ROOT / 'audio' / (job_id + '.wav'), temporary)
                temporary.replace(output)
                finished[item['index']] = output
                del active[job_id]
                print(f"✅ {len(finished)}/{len(items)}개 저장 완료 · {item['index']}번 {item.get('speaker', '')}", flush=True)
        if active:
            time.sleep(1)
    merged = folder / '전체_음성.wav'
    join_wavs([finished[item['index']] for item in items], merged, pause_ms)
    mp3 = folder / '전체_음성.mp3'
    subprocess.check_call(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(merged),
                           '-c:a', 'libmp3lame', '-b:a', '192k', str(mp3)])
    def timestamp(seconds):
        ms = round(seconds * 1000)
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'
    entries, cursor = [], 0.0
    for number, item in enumerate(items, 1):
        with wave.open(str(finished[item['index']]), 'rb') as reader:
            end = cursor + reader.getnframes() / reader.getframerate()
        text = (item.get('speaker', '') + ': ' if plan.get('include_speaker') else '') + item['text']
        entries.append(f'{number}\n{timestamp(cursor)} --> {timestamp(end)}\n{text}\n')
        cursor = end + pause_ms / 1000
    (folder / '자막.srt').write_text('\n'.join(entries), encoding='utf-8-sig')
    import zipfile
    bundle = folder / 'Qwen_전체음성_자막.zip'
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_STORED) as archive:
        for path in (mp3, folder / '자막.srt'):
            archive.write(path, path.name)
    save_json(ROOT / 'direct_result.json', {'mp3': str(mp3), 'wav': str(merged), 'bundle': str(bundle)})
    print('✅ 전체 대사를 순서대로 합쳤습니다: ' + str(mp3), flush=True)


def backup():
    import zipfile
    bank = ROOT / 'voices' / BANK_REVISION
    archive_path = ROOT / 'Qwen_기본목소리20종_보관.zip'
    count = 0
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr('manifest.json', json.dumps({'format': 'voice-studio-qwen-bank-v1', 'revision': BANK_REVISION}))
        for voice in VOICES:
            audio = bank / (voice + '.wav')
            metadata = audio.with_suffix('.json')
            if audio.is_file() and metadata.is_file():
                archive.write(audio, audio.name)
                archive.write(metadata, metadata.name)
                count += 1
    print(f'✅ 준비한 목소리 {count}개를 보관했습니다: {archive_path}', flush=True)


def restore(archive_path):
    import io
    import zipfile
    from qwen_voicebank_catalog import REFERENCE_TEXT
    if STATE.is_file():
        state = json.loads(STATE.read_text())
        pid = state.get('pid')
        command = Path(f'/proc/{pid}/cmdline') if isinstance(pid, int) else None
        if command and command.is_file() and b'qwen_voicebank_colab_server.py\x00serve' in command.read_bytes():
            raise RuntimeError('복원은 새 런타임에서 1번 설치 → 6번 복원 → 2번 서버 준비 순서로 진행해주세요.')
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > 41 or sum(info.file_size for info in infos) > 160 * 1024 * 1024:
            raise ValueError('목소리 보관 파일 크기가 올바르지 않습니다.')
        if any(info.file_size > 8 * 1024 * 1024 for info in infos):
            raise ValueError('목소리 보관 항목이 너무 큽니다.')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('format') != 'voice-studio-qwen-bank-v1' or manifest.get('revision') != BANK_REVISION:
            raise ValueError('현재 기본 목소리 목록의 보관 파일이 아닙니다.')
        names = set(archive.namelist())
        allowed = {'manifest.json'} | {voice + suffix for voice in VOICES for suffix in ('.wav', '.json')}
        if not names <= allowed or len(names) != len(infos):
            raise ValueError('보관 파일 안의 항목이 올바르지 않습니다.')
        accepted = []
        for voice in VOICES:
            if voice + '.wav' not in names:
                continue
            raw = archive.read(voice + '.wav')
            meta = archive.read(voice + '.json')
            identity = hashlib.sha256(json.dumps([BANK_REVISION, REFERENCE_TEXT, VOICEBANK[voice]],
                                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if json.loads(meta).get('identity') != identity:
                raise ValueError('목소리 설명과 보관 음성이 맞지 않습니다.')
            with wave.open(io.BytesIO(raw), 'rb') as reader:
                frames, rate = reader.getnframes(), reader.getframerate()
                if rate <= 0 or not 2 <= frames / rate <= 30:
                    raise ValueError('보관 음성 길이가 올바르지 않습니다.')
                reader.setpos(frames - 1)
                if len(reader.readframes(1)) != reader.getsampwidth() * reader.getnchannels():
                    raise ValueError('보관 음성 파일이 잘렸습니다.')
            accepted.append((voice, raw, meta))
    bank = ROOT / 'voices' / BANK_REVISION
    bank.mkdir(parents=True, exist_ok=True, mode=0o700)
    for voice, raw, meta in accepted:
        (bank / (voice + '.wav')).write_bytes(raw)
        (bank / (voice + '.json')).write_bytes(meta)
    print(f'✅ 목소리 {len(accepted)}개를 복원했습니다. 2번 셀부터 이어서 실행하세요.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['setup', 'start', 'serve', 'tunnel', 'direct', 'backup', 'restore'])
    parser.add_argument('--plan')
    args = parser.parse_args()
    if args.action == 'direct':
        direct(args.plan)
    elif args.action == 'restore':
        restore(args.plan)
    else:
        globals()[args.action]()
