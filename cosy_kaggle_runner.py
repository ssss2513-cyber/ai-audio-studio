"""Kaggle batch execution using two isolated copies of the existing servers.

Only worker subprocesses import Torch. The coordinator assigns physical GPU
UUIDs before those subprocesses start, keeps all HTTP traffic on loopback, and
stores only completed, numbered audio in /kaggle/working. No public tunnel.
"""
import argparse
from contextlib import contextmanager
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import wave
import zipfile

from cosy_kaggle_contract import (FORMAT, VERSION, SOURCE_REVISION, MODELS,
    MAX_ITEMS, MAX_REFERENCE_BYTES, MAX_REFERENCES_BYTES, MAX_PLAN_BYTES)
from cosy3_voicebank_catalog import BANK_REVISION, VOICEBANK, ATTRIBUTION
from cosy_kaggle_queue import PROTOCOL as QUEUE_PROTOCOL, LANES, PREFETCH, LIVE
from audio_join import copy_pcm_clip, write_pcm_silence

CODE = Path(__file__).resolve().parent
RUNTIME = Path('/tmp/voice_studio_kaggle_v1')
SOURCE = RUNTIME / 'CosyVoice'
PYTHON = RUNTIME / 'venv/bin/python'
OUTPUTS = Path('/kaggle/working/voice_studio_results')
PRINT_LOCK = threading.Lock()


def say(message):
    with PRINT_LOCK:
        print(message, flush=True)


def redact(value):
    return re.sub(r'/v1/[A-Za-z0-9_-]+', '/v1/[접속코드]', str(value))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + secrets.token_hex(4) + '.part')
    temp.write_bytes(data)
    temp.chmod(0o600)
    os.replace(temp, path)


def atomic_json(path, data):
    atomic_bytes(path, json.dumps(data, ensure_ascii=False, indent=2).encode())


@contextmanager
def runtime_lock():
    import fcntl
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / 'batch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('이 캐글에서 이미 설치 또는 생성 중입니다. 현재 작업이 끝난 뒤 실행하세요.') from None
        yield


def run_command(command, label, *, cwd=None, env=None):
    say('\n▶ ' + label)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / 'setup.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen([str(x) for x in command], cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            for line in process.stdout:
                print(line, end='', flush=True)
                log.write(line)
            code = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    if code:
        raise RuntimeError(label + ' 실패. 위에 나온 오류를 확인하세요. 종료 코드: ' + str(code))


def gpu_devices(count=2):
    if not Path('/kaggle/working').is_dir():
        raise RuntimeError('이 파일은 Kaggle Notebook에서 실행해주세요.')
    if not shutil.which('nvidia-smi'):
        raise RuntimeError('Settings → Accelerator → GPU T4 x2를 선택해주세요.')
    result = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,name',
        '--format=csv,noheader'], text=True, timeout=15)
    rows = [dict(index=row[0].strip(), uuid=row[1].strip(), name=row[2].strip())
            for row in csv.reader(result.splitlines()) if len(row) == 3]
    visible = os.environ.get('CUDA_VISIBLE_DEVICES')
    if visible is not None:
        permitted = [item.strip() for item in visible.split(',') if item.strip()]
        rows = [row for row in rows if any(item == row['index']
                or (item.startswith('GPU-') and row['uuid'].startswith(item)) for item in permitted)]
    if len(rows) < count:
        raise RuntimeError(f'현재 사용할 수 있는 GPU는 {len(rows)}개입니다. GPU T4 x2를 선택한 뒤 다시 실행하세요.')
    return rows[:count]


def bounded_member(archive, name, maximum):
    entries = [entry for entry in archive.infolist() if entry.filename == name]
    if len(entries) != 1 or entries[0].file_size > maximum:
        raise ValueError('대본 파일 항목이 없거나 너무 큽니다: ' + name)
    with archive.open(entries[0]) as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError('파일 크기 제한 초과: ' + name)
    return value


def read_member(source, name, maximum):
    """Read specific expected members only; never extract user archive paths."""
    source = Path(source)
    if source.suffix.lower() == '.zip':
        with zipfile.ZipFile(source) as archive:
            return bounded_member(archive, name, maximum)
    path = (source.parent / name).resolve()
    if source.parent.resolve() not in path.parents or path.stat().st_size > maximum:
        raise ValueError('대본 파일 경로 또는 크기가 올바르지 않습니다.')
    return path.read_bytes()


def read_plan(source, allowed='auto'):
    raw = read_member(source, 'plan.json', MAX_PLAN_BYTES)
    plan = json.loads(raw)
    if not isinstance(plan, dict) or plan.get('format') != FORMAT:
        raise ValueError('공유 사이트의 ‘캐글용 대본 받기’ ZIP을 사용해주세요.')
    items, references = plan.get('items'), plan.get('references')
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS or not isinstance(references, dict):
        raise ValueError('대본은 1~4096개 대사여야 합니다.')
    if len(references) > 64:
        raise ValueError('참고 음성은 최대 64개까지 지원합니다.')
    indices, used = set(), set()
    for item in items:
        if not isinstance(item, dict) or item.get('engine') not in MODELS:
            raise ValueError('캐글 대본은 CosyVoice 2와 3를 지원합니다.')
        engine, number = item['engine'], item.get('index')
        if allowed != 'auto' and engine != allowed:
            raise ValueError('대본과 노트북의 코지 버전이 다릅니다. 혼합 대본은 코지2·3 통합 노트북을 사용하세요.')
        if type(number) is not int or number < 1 or number in indices:
            raise ValueError('대사 순번이 중복되거나 잘못되었습니다.')
        indices.add(number)
        text, prompt = item.get('text'), item.get('prompt_text', '')
        if not isinstance(text, str) or not text.strip() or len(text) > (2000 if engine == 'cosyvoice' else 12000):
            raise ValueError(f'{number}번 대사 길이가 올바르지 않습니다.')
        if not isinstance(prompt, str) or '<|' in text or '|>' in text:
            raise ValueError(f'{number}번 대사 형식이 올바르지 않습니다.')
        speaker = item.get('speaker')
        if not isinstance(speaker, str) or not 1 <= len(speaker) <= 200:
            raise ValueError('화자 이름이 올바르지 않습니다.')
        speed = item.get('speed', 1.0)
        low, high = (0.5, 2.0) if engine == 'cosyvoice' else (0.8, 1.2)
        if type(speed) not in (int, float) or not math.isfinite(speed) or not low <= speed <= high:
            raise ValueError(f'{number}번 말하기 속도 범위 오류')
        style = item.get('style_instruction' if engine == 'cosyvoice' else 'style', '')
        if not isinstance(style, str) or len(style) > (800 if engine == 'cosyvoice' else 1200) or '<|' in style or '|>' in style:
            raise ValueError(f'{number}번 스타일 지시 오류')
        if engine == 'cosyvoice3' and item.get('voice') not in (*VOICEBANK, 'custom'):
            raise ValueError(f'{number}번 코지3 목소리를 다시 선택해주세요.')
        needs_ref = engine == 'cosyvoice' or item.get('voice') == 'custom'
        if needs_ref:
            ref = item.get('reference_id', '')
            if not re.fullmatch('[a-f0-9]{64}', ref) or ref not in references or not prompt.strip():
                raise ValueError(f'{number}번 화자의 참고 음성과 실제 녹음 대사가 필요합니다.')
            used.add(ref)
    if any(row['engine'] == 'cosyvoice3' for row in items) and plan.get('bank_revision') != BANK_REVISION:
        raise ValueError('코지3 목소리 목록 버전이 다릅니다. 사이트에서 대본을 새로 받으세요.')
    pause = plan.get('pause_ms', 500)
    if type(pause) is not int or not 0 <= pause <= 3000:
        raise ValueError('대사 사이 간격 범위 오류')
    if not isinstance(plan.get('generation_nonce', ''), str):
        raise ValueError('다시 생성 설정 오류')
    assets, total = {}, 0
    for ref in used:
        expected = 'references/' + ref + '.audio'
        if references[ref] != expected:
            raise ValueError('참고 음성 경로가 올바르지 않습니다.')
        data = read_member(source, expected, MAX_REFERENCE_BYTES)
        total += len(data)
        if not data or digest(data) != ref or total > MAX_REFERENCES_BYTES:
            raise ValueError('참고 음성 파일이 손상되었거나 합계 64MB를 넘습니다.')
        assets[ref] = data
    plan['items'] = sorted(items, key=lambda row: row['index'])
    return plan, assets


def discover_plan(allowed='auto', preferred=''):
    if preferred.strip():
        source = Path(preferred.strip())
        read_plan(source, allowed)
        return source
    candidates = list(Path('/kaggle/input').rglob('plan.json'))
    candidates += list(Path('/kaggle/input').rglob('*.zip'))
    found = []
    for path in candidates:
        try:
            header = json.loads(read_member(path, 'plan.json', MAX_PLAN_BYTES))
            if isinstance(header, dict) and header.get('format') == FORMAT:
                engines = {item.get('engine') for item in header.get('items', [])}
                if allowed == 'auto' or engines == {allowed}:
                    found.append(path)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
    if len(found) != 1:
        listing = '\n'.join(str(path) for path in found[:10])
        raise RuntimeError('오른쪽 Add Input에서 캐글용 대본 ZIP을 추가해주세요. '
            '같은 버전의 대본은 한 개만 추가하거나 2번 셀의 대본파일경로를 지정하세요.\n' + listing)
    read_plan(found[0], allowed)
    return found[0]


def setup(engines, count):
    devices = gpu_devices(count)
    say('사용할 GPU: ' + ', '.join(f"{row['index']}번 {row['name']}" for row in devices))
    with runtime_lock():
        if not shutil.which('ffmpeg') or not shutil.which('sox'):
            run_command(['apt-get', 'update', '-qq'], '오디오 도구 목록 준비')
            run_command(['apt-get', 'install', '-y', '-qq', 'ffmpeg', 'sox', 'libsox-dev',
                         'libsndfile1', 'build-essential', 'git'], '오디오 도구 설치')
        if not PYTHON.is_file():
            uv = RUNTIME / 'tools'
            run_command([sys.executable, '-m', 'pip', 'install', '--target', uv,
                         '--disable-pip-version-check', 'uv==0.6.17'], '독립 설치 도구 준비')
            uv_env = dict(os.environ, PYTHONPATH=str(uv), UV_PYTHON_INSTALL_DIR=str(RUNTIME / 'python'),
                          UV_CACHE_DIR=str(RUNTIME / 'uv_cache'))
            run_command([sys.executable, '-m', 'uv', 'python', 'install', '3.10'],
                        '독립 Python 3.10 준비', env=uv_env)
            run_command([sys.executable, '-m', 'uv', 'venv', '--python', '3.10', '--seed', RUNTIME / 'venv'],
                        '캐글 기본 환경과 분리된 음성 실행 환경', env=uv_env)
        if not (SOURCE / '.git').is_dir():
            run_command(['git', 'clone', '--filter=blob:none',
                         'https://github.com/FunAudioLLM/CosyVoice.git', SOURCE], '공식 CosyVoice 소스 받기')
        run_command(['git', '-C', SOURCE, 'checkout', '--detach', SOURCE_REVISION], '기존과 같은 소스 버전 선택')
        run_command(['git', '-C', SOURCE, 'submodule', 'update', '--init', '--recursive'], 'Matcha-TTS 준비')
        requirements = []
        for line in (SOURCE / 'requirements.txt').read_text().splitlines():
            if line.startswith(('deepspeed', 'tensorrt', 'gradio', 'fastapi-cli')):
                continue
            if line.startswith('diffusers=='):
                line = 'diffusers==0.32.2'
            requirements.append(line)
        requirements += ['huggingface-hub==0.30.2', 'python-multipart>=0.0.18,<0.1', 'setuptools<81']
        req, constraints = RUNTIME / 'requirements.txt', RUNTIME / 'build-constraints.txt'
        req.write_text('\n'.join(requirements) + '\n')
        constraints.write_text('setuptools<81\n')
        stamp = digest(req.read_bytes() + constraints.read_bytes())
        marker = RUNTIME / 'dependencies.ok'
        if not marker.is_file() or marker.read_text() != stamp:
            run_command([PYTHON, '-m', 'pip', 'install', '--no-cache-dir', 'pip>=25.3,<26',
                         'setuptools<81', 'wheel', 'Cython<4'], '설치 도구 준비')
            run_command([PYTHON, '-m', 'pip', 'install', '--no-cache-dir', 'torch==2.3.1',
                         'torchaudio==2.3.1', '--index-url', 'https://download.pytorch.org/whl/cu121'],
                        '기존과 같은 GPU 오디오 라이브러리 설치')
            run_command([PYTHON, '-m', 'pip', 'install', '--no-cache-dir', '-r', req,
                         '--build-constraint', constraints], '음성 라이브러리 설치')
            marker.write_text(stamp)
        for engine in engines:
            model = MODELS[engine]
            target = RUNTIME / 'models' / engine
            patterns = [model['yaml'], 'llm.pt', 'flow.pt', 'hift.pt', 'campplus.onnx',
                        model['tokenizer'], 'CosyVoice-BlankEN/*']
            code = ('from huggingface_hub import snapshot_download; '
                    f"snapshot_download({model['repo']!r}, revision={model['revision']!r}, "
                    f"local_dir={str(target)!r}, allow_patterns={patterns!r})")
            run_command([PYTHON, '-c', code], model['label'] + ' 모델 준비 · 첫 실행 때 다운로드')
            if not all((target / filename).is_file() for filename in patterns[:-1]):
                raise RuntimeError(model['label'] + ' 모델 다운로드가 완전하지 않습니다.')
    say('✅ 설치 완료. 음성 생성은 3번에서 시작합니다.')


def worker(engine, directory, port):
    """Called only in a new process whose CUDA_VISIBLE_DEVICES is one UUID."""
    # If Kaggle stops the coordinator, release this worker's GPU as well.
    # This watches our own parent process; it does not keep a session alive.
    parent = int(os.environ['VOICE_STUDIO_PARENT_PID'])
    def parent_watch():
        while os.getppid() == parent:
            time.sleep(1)
        os._exit(1)
    threading.Thread(target=parent_watch, daemon=True).start()
    directory = Path(directory)
    sys.path[:0] = [str(SOURCE), str(SOURCE / 'third_party/Matcha-TTS'), str(CODE)]
    if engine == 'cosyvoice':
        import colab_server as server
    else:
        import cosy3_colab_server as server
    server.ROOT = directory
    server.SOURCE = SOURCE
    server.PYTHON = PYTHON
    server.MODEL = RUNTIME / 'models' / engine
    server.STATE = directory / 'state.json'
    if engine == 'cosyvoice':
        server.serve(port)
    else:
        server.serve()


class LocalWorker:
    def __init__(self, engine, device, folder):
        self.engine, self.device = engine, device
        # Cosy2's gpu.lock is in ROOT.parent; distinct physical GPUs get
        # distinct parents, so an old cross-engine lock cannot serialize them.
        self.root = folder / ('gpu_' + device['index']) / engine
        self.root.mkdir(parents=True, exist_ok=True)
        self.uploaded, self.inflight = set(), {}
        self.stopped = False
        self.process = None
        self.log = None
        self.session = None

    def start(self):
        import requests
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        token = secrets.token_urlsafe(32)
        self.base = f'http://127.0.0.1:{port}/v1/{token}'
        atomic_json(self.root / 'state.json', dict(port=port, token=token, base=self.base))
        shared_voice_cache = RUNTIME / 'voice_cache'
        shared_voice_cache.mkdir(exist_ok=True)
        if not (self.root / 'voice_cache').exists():
            (self.root / 'voice_cache').symlink_to(shared_voice_cache, target_is_directory=True)
        env = dict(os.environ, CUDA_VISIBLE_DEVICES=self.device['uuid'], CUDA_DEVICE_ORDER='PCI_BUS_ID',
            COSY_ACCESS_TOKEN=token, PYTHONUNBUFFERED='1', MPLBACKEND='Agg',
            TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
            OPENBLAS_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1', OMP_WAIT_POLICY='PASSIVE',
            HF_HOME=str(RUNTIME / 'huggingface'), VOICE_STUDIO_PARENT_PID=str(os.getpid()),
            VOICE_STUDIO_KAGGLE_QUEUE=str(LANES))
        env['PYTHONPATH'] = os.pathsep.join([str(CODE), str(SOURCE), str(SOURCE / 'third_party/Matcha-TTS')])
        libs = [str(path) for path in (RUNTIME / 'venv/lib/python3.10/site-packages/nvidia').glob('*/lib')]
        env['LD_LIBRARY_PATH'] = os.pathsep.join(libs + [env.get('LD_LIBRARY_PATH', '')])
        self.log = (self.root / 'server.log').open('w')
        self.process = subprocess.Popen([str(PYTHON), '-u', str(Path(__file__).resolve()), 'worker',
            '--engine', self.engine, '--worker-dir', str(self.root), '--port', str(port)],
            cwd=SOURCE, env=env, stdin=subprocess.DEVNULL, stdout=self.log,
            stderr=subprocess.STDOUT, start_new_session=True)
        self.session = requests.Session()
        self.session.trust_env = False  # Local worker traffic must stay local.

    def health(self):
        if self.process.poll() is not None:
            raise RuntimeError(f"GPU {self.device['index']} 모델 준비 중 종료:\n" + self.tail())
        try:
            with self.session.get(self.base + '/health', timeout=(2, 3), allow_redirects=False) as response:
                if response.status_code != 200:
                    return False
                data = response.json()
        except Exception:
            return False
        expected = MODELS[self.engine]
        ready = (data.get('ready') is True and data.get('service') == expected['service']
                 and (data.get('server_version') if self.engine == 'cosyvoice' else data.get('version')) == expected['version'])
        if not ready:
            return False
        state = self.queue_state()
        if state.get('protocol') != QUEUE_PROTOCOL or state.get('lanes') != LANES or state.get('prefetch') != PREFETCH:
            raise RuntimeError('캐글 실행 파일 버전이 다릅니다. 최신 노트북을 사용해주세요.')
        return True

    def tail(self):
        path = self.root / 'server.log'
        try:
            with path.open('rb') as log:
                log.seek(max(0, path.stat().st_size - 4000))
                return redact(log.read().decode(errors='replace'))
        except OSError:
            return '로그 없음'

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.session is not None:
            self.session.close()
        if self.log is not None:
            self.log.close()

    @staticmethod
    def checked_json(response):
        with response:
            if not response.ok:
                raise RuntimeError(f'음성 요청 실패 HTTP {response.status_code}: ' + redact(response.text[:1200]))
            return response.json()

    def submit(self, item, key, assets):
        ref = item.get('reference_id', '')
        if ref and ref not in self.uploaded:
            self.checked_json(self.session.post(self.base + '/kaggle/references/' + ref, data=assets[ref],
                timeout=(10, 90), allow_redirects=False))
            self.uploaded.add(ref)
        job_id = secrets.token_hex(16)
        # Remember the identity before submission: an ambiguous response must
        # never resubmit the same line under another ID.
        self.inflight[job_id] = dict(item=item, key=key, status='queued', submitted=time.monotonic())
        try:
            status = self.checked_json(self.session.post(self.base + '/kaggle/jobs/' + job_id,
                json=item, timeout=(10, 30), allow_redirects=False))
        except Exception:
            status = self.queue_state().get('jobs', {}).get(job_id)
            if status is None:
                self.inflight.pop(job_id, None)
                raise
        self.inflight[job_id]['status'] = status['status']

    def queue_state(self):
        if self.process.poll() is not None:
            raise RuntimeError('캐글 음성 서버가 종료됐습니다.\n' + self.tail())
        return self.checked_json(self.session.get(self.base + '/kaggle/state',
            params={'ids': ','.join(self.inflight)}, timeout=(5, 30), allow_redirects=False))

    def stop_queue(self):
        if not self.stopped:
            self.checked_json(self.session.post(self.base + '/kaggle/stop', timeout=(5, 30), allow_redirects=False))
            self.stopped = True

    def collect(self, job_id, destination):
        # The writer has committed a lossless WAV locally. There is no remote
        # audio transfer or model wait on this path.
        source = self.root / 'kaggle_jobs' / (job_id + '.wav')
        wav_info(source)
        temp = destination.with_suffix('.part.wav')
        shutil.copyfile(source, temp)
        os.replace(temp, destination)


def wav_info(path):
    with wave.open(str(path), 'rb') as src:
        params = src.getparams()
        if params.nchannels != 1 or params.sampwidth != 2 or params.nframes <= 0 or params.comptype != 'NONE':
            raise ValueError('완료 음성의 PCM 형식이 올바르지 않습니다.')
        expected = params.nframes * params.nchannels * params.sampwidth
        actual = 0
        while True:
            part = src.readframes(65536)
            if not part:
                break
            actual += len(part)
        if actual != expected:
            raise ValueError('음성 파일이 끝까지 저장되지 않았습니다.')
        return dict(frames=params.nframes, rate=params.framerate, seconds=params.nframes / params.framerate)


def sha_file(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(part)
    return value.hexdigest()


def item_key(item, nonce=''):
    source_files = ['cosy_kaggle_contract.py', 'colab_server.py'] if item['engine'] == 'cosyvoice' else [
        'cosy_kaggle_contract.py', 'cosy3_colab_server.py', 'cosy3_model.py', 'cosy3_voicebank_catalog.py']
    source_files.append('cosy_kaggle_queue.py')
    # Cosy3's Kaggle-only adapter reuses the protected FP32 offline path.
    if item['engine'] == 'cosyvoice3':
        source_files.append('colab_server.py')
    fingerprint = {name: sha_file(CODE / name) for name in source_files}
    return digest(canonical(dict(item=item, model=MODELS[item['engine']], source=SOURCE_REVISION,
        precision='FP32', code=fingerprint, generation_nonce=nonce)))


def cached_clip(folder, key):
    audio, receipt = folder / (key + '.wav'), folder / (key + '.json')
    try:
        data = json.loads(receipt.read_text())
        if data.get('key') != key or data.get('sha256') != sha_file(audio):
            return None
        wav_info(audio)
        return audio
    except (OSError, ValueError, EOFError, wave.Error):
        return None


def restore_clips(source, folder, keys):
    for key in keys:
        if cached_clip(folder, key):
            continue
        try:
            metadata = read_member(source, 'clips/' + key + '.json', 16384)
            receipt = json.loads(metadata)
            if receipt.get('key') != key:
                continue
            audio = read_member(source, 'clips/' + key + '.wav', 512 * 1024 * 1024)
            if digest(audio) != receipt.get('sha256'):
                continue
            path = folder / (key + '.wav')
            atomic_bytes(path, audio)
            wav_info(path)
            atomic_bytes(folder / (key + '.json'), metadata)
        except (OSError, ValueError, KeyError, EOFError, wave.Error, zipfile.BadZipFile):
            continue


def write_resume(folder, keys):
    target = folder / 'resume.zip'
    temp = target.with_suffix('.part.zip')
    with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as out:
        out.write(folder / 'plan.json', 'plan.json')
        plan = json.loads((folder / 'plan.json').read_text())
        # Original indices survive splitting a mixed script into two GPU jobs.
        out.writestr('clip_index.json', json.dumps([
            dict(index=item['index'], file='clips/' + key + '.wav',
                 sha256=json.loads((folder / 'clips' / (key + '.json')).read_text())['sha256'])
            for item, key in zip(plan['items'], keys)
            if cached_clip(folder / 'clips', key)], ensure_ascii=False))
        for path in sorted((folder / 'references').glob('*.audio')):
            out.write(path, 'references/' + path.name)
        for key in dict.fromkeys(keys):
            if cached_clip(folder / 'clips', key):
                for suffix in ('.wav', '.json'):
                    path = folder / 'clips' / (key + suffix)
                    out.write(path, 'clips/' + path.name)
        if (folder / 'progress.json').is_file():
            out.write(folder / 'progress.json', 'progress.json')
    os.replace(temp, target)
    return target


def timestamp(milliseconds, comma=True):
    milliseconds = max(0, int(round(milliseconds)))
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f'{hours:02d}:{minute:02d}:{sec:02d}' + (',' if comma else '.') + f'{ms:03d}'


def merge_complete(plan, folder, keys):
    paths = [cached_clip(folder / 'clips', key) for key in keys]
    if not all(paths):
        raise RuntimeError('누락된 대사가 있어 최종 음성을 만들지 않았습니다. resume.zip으로 이어서 생성하세요.')
    if plan.get('site_parallel_shard'):
        # Transfer lossless clips once. The site merges both engines in script
        # order and encodes the final MP3 only once.
        say('모델별 생성 완료 · 원본 WAV와 순번을 공유 사이트에 전달합니다.')
        return
    final_wav = folder / 'full_audio.wav'
    temporary = folder / 'full_audio.part.wav'
    srt, vtt, timings = [], ['WEBVTT\n'], []
    elapsed = 0
    with wave.open(str(temporary), 'wb') as dst:
        rate = None
        for index, (item, path) in enumerate(zip(plan['items'], paths), 1):
            with wave.open(str(path), 'rb') as src:
                if rate is None:
                    rate = src.getframerate()
                    dst.setnchannels(1)
                    dst.setsampwidth(2)
                    dst.setframerate(rate)
                if src.getframerate() != rate:
                    raise RuntimeError('대사별 오디오 형식이 달라 합치기를 중단했습니다.')
                if index > 1:
                    pause_frames = round(rate * plan.get('pause_ms', 500) / 1000)
                    elapsed += write_pcm_silence(dst, pause_frames)
                start = elapsed * 1000 / rate
                elapsed += copy_pcm_clip(src, dst)
                end = elapsed * 1000 / rate
            text = item['text'].replace('\r', ' ').replace('\n', ' ')
            if plan.get('include_speaker', True):
                text = item['speaker'] + ': ' + text
            srt.append(f'{index}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n')
            vtt.append(f'{index}\n{timestamp(start, False)} --> {timestamp(end, False)}\n{text}\n')
            timings.append(dict(index=item['index'], speaker=item['speaker'], engine=item['engine'],
                                start_ms=round(start), end_ms=round(end)))
    os.replace(temporary, final_wav)
    mp3 = folder / 'full_audio.mp3'
    temp_mp3 = folder / 'full_audio.part.mp3'
    run_command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                 '-i', final_wav, '-codec:a', 'libmp3lame', '-b:a', '192k', temp_mp3],
                '전체 대본을 순서대로 MP3 하나로 저장')
    os.replace(temp_mp3, mp3)
    atomic_bytes(folder / 'subtitles.srt', '\n'.join(srt).encode('utf-8-sig'))
    atomic_bytes(folder / 'subtitles.vtt', '\n'.join(vtt).encode('utf-8'))
    atomic_json(folder / 'timing.json', timings)
    atomic_bytes(folder / 'VOICE_ATTRIBUTION.txt', ATTRIBUTION.encode())
    bundle = folder / 'complete_audio.zip'
    with zipfile.ZipFile(bundle.with_suffix('.part.zip'), 'w', compression=zipfile.ZIP_STORED) as out:
        for name in ('full_audio.mp3', 'subtitles.srt', 'subtitles.vtt', 'timing.json', 'VOICE_ATTRIBUTION.txt'):
            out.write(folder / name, name)
    os.replace(bundle.with_suffix('.part.zip'), bundle)
    return mp3


def execute_locked(source, allowed, count):
    plan, assets = read_plan(source, allowed)
    devices = gpu_devices(count)
    keys = [item_key(item, plan.get('generation_nonce', '')) for item in plan['items']]
    job_id = digest(canonical(keys))[:20]
    folder = OUTPUTS / job_id
    clips = folder / 'clips'
    clips.mkdir(parents=True, exist_ok=True)
    (folder / 'references').mkdir(exist_ok=True)
    atomic_json(folder / 'plan.json', plan)
    for key, data in assets.items():
        atomic_bytes(folder / 'references' / (key + '.audio'), data)
    restore_clips(source, clips, keys)
    results = {item['index']: cached_clip(clips, key) for item, key in zip(plan['items'], keys)}
    pending = [(item, key) for item, key in zip(plan['items'], keys) if not results[item['index']]]
    progress_lock, stop = threading.RLock(), threading.Event()
    progress = dict(status='preparing', total=len(keys), done=sum(bool(p) for p in results.values()),
        gpu_count=len(devices), gpu_concurrency=LANES, prefetch_per_gpu=PREFETCH,
        gpus={}, errors=[], output=str(folder), started=time.time())
    atomic_json(folder / 'progress.json', progress)
    atomic_json(OUTPUTS / 'latest.json', dict(folder=str(folder), status=progress['status']))
    say(f"전체 {len(keys)}개 · 재사용 {progress['done']}개 · 새로 생성 {len(pending)}개 · GPU {len(devices)}개")
    workers = []
    old_handler = signal.getsignal(signal.SIGINT)
    def interrupt(signum, frame):
        if stop.is_set():
            say('중단 처리 중입니다. 현재 계산 결과를 저장하고 종료합니다.')
            return
        stop.set()
        say('중단 요청: 새 대사는 시작하지 않습니다. 계산 중인 대사를 저장한 뒤 이어하기 파일을 만듭니다.')
    signal.signal(signal.SIGINT, interrupt)
    def process_workers(workers, tasks):
        failed_workers = set()

        def record_error(local, message, index=None):
            gpu = local.device['index']
            progress['errors'].append(dict(index=index, gpu=gpu, message=redact(message)))
            say(f'❌ GPU {gpu} · 중단: {redact(message)}')
            stop.set()

        while True:
            finished = []
            for local in workers:
                if local in failed_workers:
                    continue
                gpu = local.device['index']
                try:
                    if stop.is_set():
                        local.stop_queue()
                    state = local.queue_state()
                    progress['gpus'][gpu] = {key: state[key] for key in
                        ('lanes', 'prefetch', 'generating', 'prepared', 'preparing', 'saving', 'stopping')}
                    if state['stopping']:
                        stop.set()
                    for job_id, task in list(local.inflight.items()):
                        row = state['jobs'].get(job_id)
                        if row is None:
                            raise RuntimeError(f"{task['item']['index']}번 대사의 서버 접수를 확인할 수 없습니다.")
                        task['status'] = row['status']
                        if row['status'] == 'done':
                            finished.append((local, job_id, task, row))
                        elif row['status'] in ('error', 'cancelled'):
                            if row['status'] == 'error':
                                record_error(local, row.get('message', '음성 생성 오류'), task['item']['index'])
                            local.inflight.pop(job_id)
                        elif time.monotonic() - task['submitted'] > 1800:
                            raise RuntimeError(f"{task['item']['index']}번 대사가 30분 동안 완료되지 않았습니다.")
                except Exception as exc:
                    record_error(local, exc)
                    failed_workers.add(local)
                    # Preserve results already committed by the independent
                    # writer, even if the private HTTP endpoint has stopped.
                    seen = {job_id for worker, job_id, _, _ in finished if worker is local}
                    for job_id, task in list(local.inflight.items()):
                        try:
                            saved = json.loads((local.root / 'kaggle_jobs' / (job_id + '.json')).read_text())
                            if saved.get('status') == 'done' and job_id not in seen:
                                finished.append((local, job_id, task, saved))
                        except (OSError, ValueError):
                            pass
                    local.inflight.clear()

            # Round-robin admission gives both GPUs work even for a short
            # script. Keep three executing + three prepared on each server.
            # Refill BEFORE copying completed WAVs or updating their receipts.
            if not stop.is_set():
                while not tasks.empty():
                    admitted = False
                    for local in workers:
                        if stop.is_set():
                            break
                        live = sum(task['status'] in LIVE for task in local.inflight.values())
                        if live >= LANES + PREFETCH or len(local.inflight) >= 24:
                            continue
                        try:
                            item, key = tasks.get_nowait()
                        except queue.Empty:
                            break
                        try:
                            local.submit(item, key, assets)
                            admitted = True
                        except Exception as exc:
                            record_error(local, exc, item['index'])
                            break
                    if stop.is_set() or not admitted:
                        break

            for local, job_id, task, row in finished:
                item, key = task['item'], task['key']
                gpu = local.device['index']
                try:
                    path = clips / (key + '.wav')
                    local.collect(job_id, path)
                    info = wav_info(path)
                    atomic_json(clips / (key + '.json'), dict(key=key, sha256=sha_file(path), **info,
                        engine=item['engine'], physical_gpu=gpu, metrics=row.get('metrics', {})))
                    results[item['index']] = path
                    progress['done'] += 1
                    (local.root / 'kaggle_jobs' / (job_id + '.wav')).unlink(missing_ok=True)
                    seconds = float(row.get('generation_wall_seconds', 0))
                    say(f"✅ {progress['done']}/{len(keys)} · GPU {gpu} · {item['index']}번 · {seconds:.1f}초 · GPU당 생성 3개 + 다음 3개 준비")
                except Exception as exc:
                    record_error(local, exc, item['index'])
                finally:
                    local.inflight.pop(job_id, None)
            with progress_lock:
                atomic_json(folder / 'progress.json', progress)
            if not any(local.inflight for local in workers) and (stop.is_set() or tasks.empty()):
                break
            # Polling reports progress only: the server refills a compute lane
            # immediately from its ready queue, independently of this interval.
            time.sleep(0.2)
    try:
        for engine in MODELS:
            phase = [(item, key) for item, key in pending if item['engine'] == engine]
            if not phase or stop.is_set():
                continue
            say('\n▶ ' + MODELS[engine]['label'] + ' · 두 GPU에 독립 모델 준비')
            run_folder = RUNTIME / 'runs' / secrets.token_hex(8)
            workers = [LocalWorker(engine, device, run_folder) for device in devices]
            try:
                for local in workers:
                    local.start()
                ready, deadline, last_report = set(), time.monotonic() + 1800, 0.0
                while len(ready) < len(workers) and not stop.is_set():
                    for index, local in enumerate(workers):
                        if index not in ready and local.health():
                            ready.add(index)
                            say(f"✅ GPU {local.device['index']} 모델 준비 완료 · FP32 · 동시 {LANES}개 + 다음 {PREFETCH}개 준비")
                    if time.monotonic() > deadline:
                        raise RuntimeError('모델 준비가 30분을 초과했습니다.\n' + '\n'.join(w.tail() for w in workers))
                    if time.monotonic() - last_report > 30:
                        for index, local in enumerate(workers):
                            if index not in ready:
                                say(f"GPU {local.device['index']} 준비 기록:\n" + local.tail())
                        last_report = time.monotonic()
                    if len(ready) < len(workers):
                        stop.wait(1)
                if stop.is_set():
                    break
                tasks = queue.Queue()
                for item in phase:
                    tasks.put_nowait(item)
                progress['status'] = 'generating'
                process_workers(workers, tasks)
            finally:
                for local in workers:
                    local.close()
                workers = []
        if progress['errors'] or stop.is_set():
            progress['status'] = 'error' if progress['errors'] else 'paused'
        else:
            progress['status'] = 'merging'
            atomic_json(folder / 'progress.json', progress)
            merge_complete(plan, folder, keys)
            progress['status'] = 'complete'
    except BaseException as exc:
        stop.set()
        progress['status'] = 'paused' if isinstance(exc, KeyboardInterrupt) else 'error'
        if not isinstance(exc, KeyboardInterrupt):
            progress['errors'].append(dict(message=redact(exc)))
        say('작업 중단: ' + redact(exc))
    finally:
        for local in workers:
            local.close()
        signal.signal(signal.SIGINT, old_handler)
        progress['finished'] = time.time()
        atomic_json(folder / 'progress.json', progress)
        atomic_json(OUTPUTS / 'latest.json', dict(folder=str(folder), status=progress['status']))
        try:
            write_resume(folder, keys)
        except Exception as exc:
            say('이어하기 ZIP 저장 실패. Output의 clips 폴더와 plan.json을 보관하세요: ' + redact(exc))
        say('결과 폴더: ' + str(folder))
    if progress['status'] == 'complete':
        say('✅ 모델별 원본 음성 완료: 공유 사이트에서 두 모델을 순번대로 합칩니다.'
            if plan.get('site_parallel_shard') else
            '✅ 전체 완료: full_audio.mp3 한 파일 + 자막. complete_audio.zip을 받으세요.')
        return 0
    say('완료된 대사는 보관했습니다. resume.zip을 Add Input에 추가하고 같은 노트북에서 이어서 실행하세요.')
    return 2


def execute(source, allowed, count):
    # Lock before restoring clips or writing any job state.
    with runtime_lock():
        return execute_locked(source, allowed, count)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['setup', 'run', 'worker'])
    parser.add_argument('--engine', choices=['auto', *MODELS], default='auto')
    parser.add_argument('--gpus', type=int, choices=[1, 2], default=2)
    parser.add_argument('--plan', default='')
    parser.add_argument('--worker-dir', default='')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if args.command == 'worker':
        if args.engine == 'auto' or not args.worker_dir or not 1 <= args.port <= 65535:
            parser.error('internal worker arguments missing')
        worker(args.engine, args.worker_dir, args.port)
        return
    source = discover_plan(args.engine, args.plan)
    if args.command == 'setup':
        plan, _ = read_plan(source, args.engine)
        setup(sorted({item['engine'] for item in plan['items']}), args.gpus)
    else:
        raise SystemExit(execute(source, args.engine, args.gpus))


if __name__ == '__main__':
    main()
