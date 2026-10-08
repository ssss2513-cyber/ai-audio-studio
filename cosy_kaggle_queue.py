"""Private Kaggle queue: three inference lanes, three CPU-prepared requests.

Only enabled by the Kaggle runner. Colab keeps its existing APIs and scheduler.
Completed CPU audio goes to a bounded writer queue before a lane takes its next
request. No precision, sampling, model or speech-speed setting changes here.
"""
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import secrets
import threading
import time

PROTOCOL = 'cosy-kaggle-three-prefetch-v1'
LANES = 3
PREFETCH = 3
LIVE = ('queued', 'preparing', 'prepared', 'generating')


def install(app, authorize, root, prepare, generate, write_audio):
    from fastapi import HTTPException, Request
    root = Path(root)
    job_dir, refs = root / 'kaggle_jobs', root / 'kaggle_references'
    job_dir.mkdir(exist_ok=True)
    refs.mkdir(exist_ok=True)
    condition = threading.Condition(threading.RLock())
    jobs, waiting, ready = {}, deque(), deque()
    prepared = {}
    completed = queue.Queue(maxsize=LANES + PREFETCH)
    stopping = False

    def atomic(path, data):
        temp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.part')
        temp.write_bytes(data)
        os.replace(temp, path)

    def persist(job_id):
        atomic(job_dir / (job_id + '.json'), json.dumps(jobs[job_id], ensure_ascii=False).encode())

    def public(job):
        return {key: value for key, value in job.items() if key not in ('payload', 'identity')}

    def cancel_waiting():
        # Called under the condition. A preparation already in flight notices
        # cancellation before publishing its CPU payload. Active audio is saved.
        for job_id, job in jobs.items():
            if job['status'] in ('queued', 'preparing', 'prepared'):
                job.update(status='cancelled', finished=time.time())
                try:
                    persist(job_id)
                except OSError:
                    pass  # The live API still reports cancellation on disk failure.
        waiting.clear()
        ready.clear()
        prepared.clear()
        condition.notify_all()

    def failed(job_id, exc):
        nonlocal stopping
        detail = getattr(exc, 'detail', str(exc))
        message = str(detail)[:1200]
        memory = 'out of memory' in message.lower() or 'cuda_memory_limit' in message
        with condition:
            jobs[job_id].update(status='error', finished=time.time(),
                               error_code='memory' if memory else 'generation', message=message)
            if memory:
                jobs[job_id]['message'] = ('GPU 메모리가 부족해 새 대사를 중단했습니다. '
                    '완료 음성은 보관하며 음질 설정은 낮추지 않습니다. ' + message)
            stopping = True
            try:
                persist(job_id)
            except OSError:
                pass
            cancel_waiting()

    def preparation_loop():
        while True:
            with condition:
                condition.wait_for(lambda: not stopping and waiting and len(ready) < PREFETCH)
                job_id = waiting.popleft()
                jobs[job_id]['status'] = 'preparing'
                payload = dict(jobs[job_id]['payload'])
            try:
                # File lookup/download and input validation only. No extra TTS
                # requests or GPU conditioning are run for the prefetched lines.
                value = prepare(payload, refs)
                with condition:
                    if stopping or jobs[job_id]['status'] == 'cancelled':
                        continue
                    prepared[job_id] = value
                    jobs[job_id].update(status='prepared', prepared_at=time.time())
                    ready.append(job_id)
                    condition.notify_all()
            except Exception as exc:
                with condition:
                    cancelled = jobs[job_id]['status'] == 'cancelled'
                if not cancelled:
                    failed(job_id, exc)

    def inference_loop():
        while True:
            with condition:
                condition.wait_for(lambda: not stopping and ready)
                job_id = ready.popleft()
                value = prepared.pop(job_id)
                jobs[job_id].update(status='generating', started=time.time())
                condition.notify_all()
            try:
                started = time.monotonic()
                audio, metrics = generate(value)
                del value
                with condition:
                    jobs[job_id].update(status='saving', calculated=time.time(),
                        generation_wall_seconds=time.monotonic() - started, metrics=metrics)
                    condition.notify_all()
                completed.put((job_id, audio, metrics))
                del audio
                # Take a prepared line immediately; the writer and coordinator
                # save the previous result independently of this compute lane.
            except Exception as exc:
                failed(job_id, exc)

    def writer_loop():
        while True:
            job_id, audio, metrics = completed.get()
            try:
                path = job_dir / (job_id + '.wav')
                temp = path.with_suffix('.part.wav')
                write_audio(temp, audio, metrics)
                os.replace(temp, path)
                with condition:
                    jobs[job_id].update(status='done', finished=time.time())
                    persist(job_id)
                    condition.notify_all()
            except Exception as exc:
                failed(job_id, exc)
            finally:
                del audio
                completed.task_done()

    def snapshot():
        return dict(protocol=PROTOCOL, lanes=LANES, prefetch=PREFETCH, stopping=stopping,
            acoustic_concurrency=1,
            generating=[job['index'] for job in jobs.values() if job['status'] == 'generating'],
            prepared=[job['index'] for job in jobs.values() if job['status'] == 'prepared'],
            preparing=[job['index'] for job in jobs.values() if job['status'] in ('preparing', 'queued')],
            saving=[job['index'] for job in jobs.values() if job['status'] == 'saving'])

    async def body(request, maximum):
        data = bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data) > maximum:
                raise HTTPException(413, '요청 파일이 너무 큽니다.')
        return bytes(data)

    @app.get('/v1/{token}/kaggle/state')
    def state(token: str, ids: str = ''):
        authorize(token)
        requested = ids.split(',') if ids else []
        if len(requested) > 32 or any(not re.fullmatch('[a-f0-9]{32}', value) for value in requested):
            raise HTTPException(400, '작업 ID 오류')
        with condition:
            return dict(**snapshot(), jobs={job_id: public(jobs[job_id])
                                            for job_id in requested if job_id in jobs})

    @app.post('/v1/{token}/kaggle/references/{reference_id}')
    async def reference(token: str, reference_id: str, request: Request):
        authorize(token)
        if not re.fullmatch('[a-f0-9]{64}', reference_id):
            raise HTTPException(400, '참고 음성 ID 오류')
        raw = await body(request, 10 * 1024 * 1024)
        if not raw or hashlib.sha256(raw).hexdigest() != reference_id:
            raise HTTPException(400, '참고 음성이 완전하지 않습니다.')
        path = refs / (reference_id + '.audio')
        if not path.is_file():
            atomic(path, raw)
        return dict(reference_id=reference_id)

    @app.post('/v1/{token}/kaggle/jobs/{job_id}')
    async def submit(token: str, job_id: str, request: Request):
        authorize(token)
        if not re.fullmatch('[a-f0-9]{32}', job_id):
            raise HTTPException(400, '작업 ID 오류')
        try:
            payload = json.loads(await body(request, 128 * 1024))
            if not isinstance(payload, dict) or type(payload.get('index')) is not int or payload['index'] < 1:
                raise ValueError('대사 순번 오류')
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from None
        identity = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with condition:
            if job_id in jobs:
                if jobs[job_id]['identity'] != identity:
                    raise HTTPException(409, '같은 작업 ID에 다른 대사가 전달됐습니다.')
                return public(jobs[job_id])
            if stopping:
                raise HTTPException(409, '중단된 작업 큐입니다. 완료 음성을 먼저 보관하세요.')
            if len(jobs) >= 4096:
                raise HTTPException(429, '한 작업에서는 최대 4096개 대사를 처리합니다.')
            if sum(job['status'] in LIVE for job in jobs.values()) >= LANES + PREFETCH:
                raise HTTPException(429, '실행 3개와 다음 대사 3개가 준비되어 있습니다.')
            jobs[job_id] = dict(index=payload['index'], status='queued', payload=payload,
                                identity=identity, submitted=time.time())
            try:
                persist(job_id)
            except OSError:
                jobs.pop(job_id, None)
                raise HTTPException(507, '작업 정보를 저장할 공간이 부족합니다.') from None
            waiting.append(job_id)
            condition.notify_all()
            return public(jobs[job_id])

    @app.post('/v1/{token}/kaggle/stop')
    def stop(token: str):
        nonlocal stopping
        authorize(token)
        with condition:
            stopping = True
            cancel_waiting()
            return snapshot()

    threading.Thread(target=preparation_loop, name='cosy-prefetch', daemon=True).start()
    threading.Thread(target=writer_loop, name='cosy-save', daemon=True).start()
    for lane in range(LANES):
        threading.Thread(target=inference_loop, name=f'cosy-generate-{lane}', daemon=True).start()


def install_cosy2(app, authorize, root, synthesize, token):
    import io
    from starlette.datastructures import UploadFile

    def prepare(item, refs):
        ref = str(item.get('reference_id', ''))
        if not re.fullmatch('[a-f0-9]{64}', ref) or not (refs / (ref + '.audio')).is_file():
            raise ValueError('참고 음성을 먼저 등록해주세요.')
        return dict(text=item['text'], prompt_text=item['prompt_text'], speed=item['speed'],
                    style_instruction=item.get('style_instruction', ''), raw=(refs / (ref + '.audio')).read_bytes())

    def generate(item):
        item = dict(item)
        reference = UploadFile(file=io.BytesIO(item.pop('raw')), filename='reference.audio')
        result = synthesize(token, reference=reference, reference_id='', audio_format='wav', **item)
        fields = {'synthesis_seconds': 'X-Synthesis-Seconds', 'reference_seconds': 'X-Reference-Seconds',
                  'llm_seconds': 'X-LLM-Seconds', 'acoustic_seconds': 'X-Acoustic-Seconds',
                  'acoustic_wait_seconds': 'X-Acoustic-Wait-Seconds'}
        return result.body, {key: result.headers.get(header, '') for key, header in fields.items()}

    install(app, authorize, root, prepare, generate, lambda path, audio, metrics: path.write_bytes(audio))


def install_cosy3(app, authorize, root, model):
    import math
    import numpy as np
    import soundfile as sf
    from cosy3_model import spoken

    def prepare(item, refs):
        voice = item['voice']
        speed = float(item['speed'])
        style = str(item.get('style', '')).strip()
        if not math.isfinite(speed) or not 0.8 <= speed <= 1.2 or len(style) > 1200 or '<|' in style or '|>' in style:
            raise ValueError('속도 또는 스타일 지시 오류')
        payload = dict(text=spoken(item['text']), voice=voice, speed=speed, style=style,
                       prompt_text='', ref_path='')
        if voice == 'custom':
            ref = str(item.get('reference_id', ''))
            if not re.fullmatch('[a-f0-9]{64}', ref) or not (refs / (ref + '.audio')).is_file():
                raise ValueError('참고 음성을 먼저 등록해주세요.')
            path = refs / (ref + '.wav')
            if not path.is_file():
                audio, rate = sf.read(refs / (ref + '.audio'), dtype='float32', always_2d=True)
                if not 3 <= len(audio) / rate <= 30 or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 0.001:
                    raise ValueError('3~30초의 또렷한 참고 녹음이 필요합니다.')
                sf.write(path, audio, rate, subtype='FLOAT')
            payload.update(ref_path=str(path), prompt_text=spoken(item.get('prompt_text', ''), 2000))
        else:
            # Download the next speaker's reference while current audio runs.
            model.reference(voice)
        return payload

    def generate(item):
        started = time.monotonic()
        audio, rate = model.generate(**item)
        timings = {name: round(model.generation_timer[name], 3) for name in (
            'reference_seconds', 'llm_seconds', 'sampling_seconds', 'acoustic_seconds',
            'acoustic_wait_seconds')}
        return audio, dict(synthesis_seconds=time.monotonic() - started,
                           sample_rate=rate, audio_seconds=len(audio) / rate, **timings)

    install(app, authorize, root, prepare, generate,
            lambda path, audio, metrics: sf.write(path, audio, metrics['sample_rate'], subtype='PCM_16'))
