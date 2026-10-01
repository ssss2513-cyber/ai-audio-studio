"""No paid APIs or TTS models: exercise ordering, cancellation and wire handling."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import socket
import struct
import threading
import time
from types import SimpleNamespace
import wave

import pytest
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
import uvicorn

import colab_server
from core import cosy_batch_client as batch
from core import generation_jobs as jobs
from core import generation_pipeline as pipeline
from core.gemini_client import RequestPacer
from core.tts_engine import VoiceConfig


def audio_bytes(value=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(struct.pack('<h', value) * 2400)
    return output.getvalue()


def items_at(folder, count=4):
    return [jobs.GenerationItem(i, f'speaker{i}', str(i), str(folder / f'{i}.mp3'),
                                VoiceConfig(engine='gemini', voice='Kore', api_key='test-key'))
            for i in range(1, count + 1)]


def state_for(items):
    return dict(id='test-job', total=len(items), done=0, reused=0, completed=[], generated=0,
                recent_seconds=[], pending_total=len(items), performance={}, started=time.time())


def test_parallel_finishes_out_of_order_but_keeps_script_order(tmp_path, monkeypatch):
    barrier = threading.Barrier(2)
    finished = []
    def generate(text, output, config, **kwargs):
        index = int(text)
        if index <= 2:
            barrier.wait(timeout=2)
        time.sleep(0.06 if index == 1 else 0.005)
        Path(output).write_bytes(audio_bytes(index))
        finished.append(index)
    monkeypatch.setattr(pipeline.TTSEngine, 'generate_gemini_speech', generate)
    items = items_at(tmp_path)
    state = state_for(items)
    pipeline.run_generation(str(tmp_path), items, state, threading.Event(), False,
                            lambda *args: None, jobs._record_performance)
    assert finished[0] == 2 and sorted(finished) == [1, 2, 3, 4]
    assert [entry['index'] for entry in state['completed']] == [1, 2, 3, 4]
    for entry in state['completed']:
        assert Path(entry['file_path']).read_bytes() == audio_bytes(entry['index'])
    assert state['throughput_seconds'] > 0


def test_pause_keeps_inflight_and_does_not_start_remaining(tmp_path, monkeypatch):
    pause = threading.Event()
    barrier = threading.Barrier(2)
    started = []
    def generate(text, output, config, **kwargs):
        started.append(int(text))
        barrier.wait(timeout=2)
        if text == '1':
            pause.set()
        else:
            time.sleep(0.03)
        Path(output).write_bytes(audio_bytes(int(text)))
    monkeypatch.setattr(pipeline.TTSEngine, 'generate_gemini_speech', generate)
    items = items_at(tmp_path, 6)
    state = state_for(items)
    pipeline.run_generation(str(tmp_path), items, state, pause, False,
                            lambda *args: None, jobs._record_performance)
    assert sorted(started) == [1, 2]
    assert [part['index'] for part in state['completed']] == [1, 2]


def test_failure_drains_other_request_and_preserves_old_output(tmp_path, monkeypatch):
    barrier = threading.Barrier(2)
    started = []
    original = audio_bytes(99)
    (tmp_path / '1.wav').write_bytes(original)
    def generate(text, output, config, **kwargs):
        started.append(int(text))
        barrier.wait(timeout=2)
        if text == '1':
            Path(output).write_bytes(b'broken')
            raise RuntimeError('simulated provider failure')
        time.sleep(0.03)
        Path(output).write_bytes(audio_bytes(2))
    monkeypatch.setattr(pipeline.TTSEngine, 'generate_gemini_speech', generate)
    items = items_at(tmp_path)
    state = state_for(items)
    with pytest.raises(RuntimeError, match='simulated provider failure'):
        pipeline.run_generation(str(tmp_path), items, state, threading.Event(), True,
                                lambda *args: None, jobs._record_performance)
    assert sorted(started) == [1, 2]
    assert [part['index'] for part in state['completed']] == [2]
    assert (tmp_path / '1.wav').read_bytes() == original
    assert not list(tmp_path.glob('.*.part.wav'))


def test_resume_reuses_completed_audio(tmp_path, monkeypatch):
    items = items_at(tmp_path, 2)
    for item in items:
        Path(item.file_path).with_suffix('.wav').write_bytes(audio_bytes(item.index))
    def unexpected(*args, **kwargs):
        raise AssertionError('must not synthesize a cached line')
    monkeypatch.setattr(pipeline.TTSEngine, 'generate_gemini_speech', unexpected)
    state = state_for(items)
    pipeline.run_generation(str(tmp_path), items, state, threading.Event(), False,
                            lambda *args: None, jobs._record_performance)
    assert state['reused'] == 2 and state['generated'] == 0


def test_full_job_merges_once_with_ordered_audio_and_subtitles(tmp_path, monkeypatch):
    calls, merged = [], []
    original_merge = jobs.AudioProcessor.merge_segments
    def generate(text, output, config, **kwargs):
        time.sleep(0.05 if text == '1' else 0.003)
        calls.append(int(text))
        Path(output).write_bytes(audio_bytes(int(text)))
    def merge(self, segments, output_file, **kwargs):
        merged.append([part['index'] for part in segments])
        return original_merge(self, segments, output_file, **kwargs)
    monkeypatch.setattr(pipeline.TTSEngine, 'generate_gemini_speech', generate)
    monkeypatch.setattr(jobs.AudioProcessor, 'merge_segments', merge)
    jobs.start_job(str(tmp_path), items_at(tmp_path), pause_ms=100)
    deadline = time.monotonic() + 8
    while jobs.is_running(str(tmp_path)) and time.monotonic() < deadline:
        time.sleep(0.02)
    state = jobs.get_job(str(tmp_path))
    assert state['status'] == 'complete', state.get('error')
    assert calls[0] != 1 and sorted(calls) == [1, 2, 3, 4]
    assert merged == [[1, 2, 3, 4]]
    assert jobs.valid_audio(state['result']['full_audio'])
    timings = state['result']['timings']
    assert [part['segment_index'] for part in timings] == [1, 2, 3, 4]
    assert [part['start_ms'] for part in timings] == [0, 200, 400, 600]
    subtitle = Path(state['result']['srt']).read_text()
    assert subtitle.index('speaker1') < subtitle.index('speaker2') < subtitle.index('speaker3') < subtitle.index('speaker4')


def test_shared_pacer_spaces_starts_without_serializing_responses():
    pacer = RequestPacer(interval=0.035)
    starts = []
    def task():
        pacer.wait()
        starts.append(time.monotonic())
        time.sleep(0.12)
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda _: task(), range(3)))
    starts.sort()
    assert all(b - a >= 0.03 for a, b in zip(starts, starts[1:]))
    assert starts[-1] - starts[0] < 0.2


@pytest.fixture
def local_cosy():
    app = FastAPI()
    gib = 1024 ** 3
    control = SimpleNamespace(calls=[], fail=None, capability=True, second=threading.Event(), hook=None)
    control.parallel = colab_server.AutoConcurrency(
        lambda: dict(free=12*gib, total=16*gib, allocated=4*gib, reserved=4*gib, peak=5*gib),
        lambda: None, enabled=False)
    def authorize(token):
        if token != 'test-token':
            raise HTTPException(403)
    @app.get('/v1/{token}/health')
    def health(token):
        authorize(token)
        return dict(service='ai-voice-studio-cosyvoice', api_version=1, ready=True,
                    capabilities=['validated_generation_v293', 'reference_cache_v294']
                    + ([batch.BATCH_CAPABILITY] if control.capability else []), instance_id='local-test',
                    server_version='2.9.10', gpu_name='fake-model', acceleration={})
    def synthesize(token, text, prompt, speed, reference, style, reference_id, audio_format):
        authorize(token)
        control.calls.append((text, prompt, speed, style, reference.file.read()))
        reference.file.close()
        if text == '2':
            control.second.set()
        if text == control.fail:
            raise HTTPException(502, 'simulated audio failure')
        if control.hook is not None:
            control.hook(int(text))
        time.sleep(0.015)
        return Response(audio_bytes(int(text)), media_type='audio/wav',
                        headers={'X-CosyVoice-Version': '2.9.10', 'X-Synthesis-Seconds': '0.015'})
    colab_server.install_batch_routes(app, synthesize, authorize, control.parallel)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    control.url = f'http://127.0.0.1:{sock.getsockname()[1]}/v1/test-token'
    try:
        yield control
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


def entries_at(folder, server, count=5):
    reference = folder / 'reference.wav'
    reference.write_bytes(audio_bytes(42))
    return [dict(index=i, url=server.url, text=str(i), prompt_text='참고 대사', ref_path=str(reference),
                 style_instruction='warm', speed=1.0, output_file=str(folder / f'{i}.wav'))
            for i in range(1, count + 1)]


def test_cosy_starts_next_while_previous_result_is_being_received(tmp_path, local_cosy):
    done = []
    def completed(index, path, metrics):
        if index == 1:
            assert local_cosy.second.wait(1), 'next calculation waited for client completion'
        assert Path(path).read_bytes() == audio_bytes(index)
        assert metrics['batch_stream']
        done.append(index)
    assert batch.synthesize_batch(entries_at(tmp_path, local_cosy), cancel=lambda: False,
                                   on_completed=completed, on_started=lambda index: None)
    assert done == [1, 2, 3, 4, 5]
    assert all(call[1:] == ('참고 대사', 1.0, 'warm', audio_bytes(42)) for call in local_cosy.calls)


def test_cosy_pause_drains_saved_audio_and_stops_producer(tmp_path, local_cosy):
    pause, done = threading.Event(), []
    def completed(index, path, metrics):
        done.append(index)
        pause.set()
    assert batch.synthesize_batch(entries_at(tmp_path, local_cosy, 12), cancel=pause.is_set,
                                   on_completed=completed, on_started=lambda index: None)
    assert 1 <= len(done) < 12
    assert done == [int(call[0]) for call in local_cosy.calls]


def test_cosy_failure_keeps_completed_and_never_retries(tmp_path, local_cosy):
    local_cosy.fail = '2'
    done = []
    with pytest.raises(RuntimeError, match='simulated audio failure'):
        batch.synthesize_batch(entries_at(tmp_path, local_cosy), cancel=lambda: False,
                               on_completed=lambda index, *args: done.append(index), on_started=lambda index: None)
    assert done == [1]
    assert [call[0] for call in local_cosy.calls] == ['1', '2']
    assert not (tmp_path / '2.wav').exists()


def test_old_cosy_returns_before_generation(tmp_path, local_cosy):
    local_cosy.capability = False
    assert not batch.synthesize_batch(entries_at(tmp_path, local_cosy), cancel=lambda: False,
                                      on_completed=lambda *args: None, on_started=lambda *args: None)
    assert not local_cosy.calls


def test_batch_rejects_duplicate_indices_and_requires_token(local_cosy):
    item = dict(index=1, text='1', prompt_text='ref', reference='ref', speed=1.0)
    payload = dict(job_id='a' * 32, items=[item, item],
                   references={'ref': base64.b64encode(audio_bytes()).decode()})
    assert requests.post(local_cosy.url + '/synthesize_batch', json=payload).status_code == 422
    bad_url = local_cosy.url.replace('test-token', 'wrong-token')
    assert requests.post(bad_url + '/synthesize_batch', json=payload).status_code == 403
    assert requests.post(bad_url + '/batch/' + 'a' * 32 + '/stop').status_code == 403
    assert not local_cosy.calls


def test_truncated_audio_cannot_replace_previous_file(tmp_path, monkeypatch):
    entries = entries_at(tmp_path, SimpleNamespace(url='http://localhost/v1/test-token'), 1)
    target = Path(entries[0]['output_file'])
    target.write_bytes(audio_bytes(99))
    metadata = json.dumps(dict(type='audio', index=1, size=1000)).encode()
    response = SimpleNamespace(ok=True, headers={'X-Batch-Protocol': '1'},
                               raw=io.BytesIO(struct.pack('!I', len(metadata)) + metadata + b'cut'), close=lambda: None)
    monkeypatch.setattr(batch, 'check_connection', lambda *a, **k: (True, '', {'capabilities': [batch.BATCH_CAPABILITY]}))
    monkeypatch.setattr(batch, '_transport', lambda base: SimpleNamespace(session=SimpleNamespace(post=lambda *a, **k: response)))
    with pytest.raises(RuntimeError, match='전송이 끊겼습니다'):
        batch.synthesize_batch(entries, cancel=lambda: False, on_completed=lambda *a: None, on_started=lambda *a: None)
    assert target.read_bytes() == audio_bytes(99)


def test_auto_concurrency_uses_measured_memory_and_limits_after_oom():
    gib = 1024 ** 3
    info = dict(free=20*gib, total=24*gib, allocated=4*gib, reserved=4*gib, peak=5*gib)
    policy = colab_server.AutoConcurrency(lambda: info, lambda: None)
    policy.begin()
    assert policy.limit == 1 and not policy.calibrated
    policy.calibrated_after_success()
    assert policy.limit == 8  # remaining memory / observed requirement + reserves
    info['free'] = gib
    assert not policy.can_add(1)
    policy.memory_failure(8)
    info['free'] = 20*gib
    policy.refresh()
    assert policy.limit == 4
    # A large GPU is bounded by the 32-entry request, not a fixed 2-worker setting.
    large = dict(free=90*gib, total=96*gib, allocated=6*gib, reserved=6*gib, peak=7*gib)
    other = colab_server.AutoConcurrency(lambda: large, lambda: None)
    other.begin(); other.calibrated_after_success()
    assert other.limit == 32


def test_real_cosy_requests_overlap_to_memory_limit_and_save_matching_indices(tmp_path, local_cosy):
    local_cosy.parallel.ceiling = 32
    barrier = threading.Barrier(5)
    reached, done, statuses = [], [], []
    guard = threading.Lock()
    def hook(index):
        if 2 <= index <= 6:
            with guard:
                reached.append(index)
            barrier.wait(timeout=3)
            time.sleep((7 - index) * 0.015)
    local_cosy.hook = hook
    assert batch.synthesize_batch(entries_at(tmp_path, local_cosy, 9), cancel=lambda: False,
        on_started=lambda index: None, on_completed=lambda index, *args: done.append(index),
        on_status=statuses.append)
    assert sorted(reached) == [2, 3, 4, 5, 6]
    assert done[0] == 1 and done[1] == 6
    assert sorted(done) == list(range(1, 10))
    assert max(status['limit'] for status in statuses) == 5
    for index in done:
        assert (tmp_path / f'{index}.wav').read_bytes() == audio_bytes(index)


def test_parallel_cosy_error_drains_other_inflight_successes_before_error(tmp_path, local_cosy):
    local_cosy.parallel.ceiling = 32
    barrier, done = threading.Barrier(5), []
    def hook(index):
        if 2 <= index <= 6:
            barrier.wait(timeout=3)
            if index == 2:
                raise HTTPException(502, 'parallel voice failure')
            time.sleep(0.06)
    local_cosy.hook = hook
    with pytest.raises(RuntimeError, match='parallel voice failure'):
        batch.synthesize_batch(entries_at(tmp_path, local_cosy, 10), cancel=lambda: False,
            on_started=lambda index: None, on_completed=lambda index, *args: done.append(index))
    assert sorted(done) == [1, 3, 4, 5, 6]
    assert sorted(int(call[0]) for call in local_cosy.calls) == [1, 2, 3, 4, 5, 6]
    assert not (tmp_path / '2.wav').exists()


def test_parallel_cosy_oom_drains_then_retries_only_failed_clip_once(tmp_path, local_cosy):
    local_cosy.parallel.ceiling = 32
    barrier, done, statuses = threading.Barrier(5), [], []
    counts, guard = {}, threading.Lock()
    def hook(index):
        with guard:
            counts[index] = counts.get(index, 0) + 1
            attempt = counts[index]
        if 2 <= index <= 6 and attempt == 1:
            barrier.wait(timeout=3)
            if index == 2:
                raise HTTPException(503, {'code': 'cuda_memory_limit', 'message': 'simulated OOM'})
            time.sleep(0.05)
    local_cosy.hook = hook
    assert batch.synthesize_batch(entries_at(tmp_path, local_cosy, 9), cancel=lambda: False,
        on_started=lambda index: None, on_completed=lambda index, *args: done.append(index), on_status=statuses.append)
    assert sorted(done) == list(range(1, 10))
    assert counts[2] == 2 and all(count == 1 for index, count in counts.items() if index != 2)
    assert done.index(2) > max(done.index(index) for index in range(3, 7))
    assert local_cosy.parallel.limit <= 2
    assert statuses[-1]['memory_retries'] == 1


def test_parallel_cosy_pause_saves_every_started_calculation_without_refill(tmp_path, local_cosy):
    local_cosy.parallel.ceiling = 32
    all_running, release, pause = threading.Event(), threading.Event(), threading.Event()
    seen, done, guard = set(), [], threading.Lock()
    def hook(index):
        if 2 <= index <= 6:
            with guard:
                seen.add(index)
                if len(seen) == 5:
                    all_running.set()
            assert release.wait(3)
    local_cosy.hook = hook
    def pauser():
        assert all_running.wait(3)
        pause.set()
        # Stop is sent on the next streamed heartbeat, before CUDA work returns.
        time.sleep(1.2)
        release.set()
    local_cosy.hook = hook
    thread = threading.Thread(target=pauser)
    thread.start()
    try:
        assert batch.synthesize_batch(entries_at(tmp_path, local_cosy, 12), cancel=pause.is_set,
            on_started=lambda index: None, on_completed=lambda index, *args: done.append(index))
    finally:
        release.set(); thread.join(timeout=4)
    assert sorted(done) == [1, 2, 3, 4, 5, 6]
    assert sorted(int(call[0]) for call in local_cosy.calls) == [1, 2, 3, 4, 5, 6]


def test_generation_timers_are_isolated_between_threads():
    timer = colab_server.RequestTimer()
    barrier = threading.Barrier(4)
    def worker(index):
        timer['llm_seconds'] = index
        barrier.wait(timeout=2)
        return timer['llm_seconds']
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(worker, [1, 2, 3, 4])) == [1, 2, 3, 4]
    assert timer['llm_seconds'] == 0


def test_persistent_oom_stops_after_one_retry_and_keeps_saved_clips(tmp_path, local_cosy):
    local_cosy.parallel.ceiling = 32
    def hook(index):
        if index == 2:
            raise HTTPException(503, {'code': 'cuda_memory_limit', 'message': 'persistent memory shortage'})
    local_cosy.hook = hook
    done = []
    with pytest.raises(RuntimeError, match='persistent memory shortage'):
        batch.synthesize_batch(entries_at(tmp_path, local_cosy, 12), cancel=lambda: False,
            on_started=lambda index: None, on_completed=lambda index, *args: done.append(index))
    assert len([call for call in local_cosy.calls if call[0] == '2']) == 2
    assert 1 in done and not (tmp_path / '2.wav').exists()
    assert len(done) == len(set(done))


def test_whole_batch_gpu_lease_blocks_other_engines_and_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(colab_server, 'ROOT', tmp_path / 'cosy')
    with colab_server.exclusive_gpu_lease():
        with pytest.raises(HTTPException) as blocked:
            with colab_server.exclusive_gpu_lease():
                pytest.fail('two owners held the GPU lease')
        assert blocked.value.status_code == 409
    with colab_server.exclusive_gpu_lease():
        pass
