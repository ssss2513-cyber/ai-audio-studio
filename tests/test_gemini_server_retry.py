"""Transient Gemini server failures, native audio and ordered job recovery."""
from collections import Counter
from concurrent.futures import CancelledError
import copy
import io
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import wave

from google.genai.errors import ServerError, ClientError
import pytest

from core import gemini_client as client


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b'\x10\x00' * 2400)
    return output.getvalue()


def audio_response(data=None):
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[
        SimpleNamespace(inline_data=SimpleNamespace(data=data or wav_bytes(), mime_type='audio/wav'))
    ]))])


def api_error(code=503, headers=None):
    cls = ServerError if code >= 500 else ClientError
    return cls(code, {'error': {'code': code, 'message': 'secret-test-key'}},
               response=SimpleNamespace(headers=headers or {}))


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No live Gemini request in retry tests')
    import requests
    import httpx
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    monkeypatch.setattr(client.random, 'uniform', lambda *args: 0)
    waits, pacing = [], []
    monkeypatch.setattr(client, '_wait_retry', lambda delay, cancel: waits.append(delay) or delay)
    def pace(cancel):
        if cancel and cancel():
            raise CancelledError()
        pacing.append(True)
        return 0.0
    monkeypatch.setattr(client, '_pacer', lambda *args: SimpleNamespace(wait=pace))
    def install(replies):
        calls = []
        def generate(**kwargs):
            calls.append(copy.deepcopy(kwargs))
            reply = replies[len(calls) - 1]
            if isinstance(reply, Exception):
                raise reply
            return reply
        transport = {'client': SimpleNamespace(models=SimpleNamespace(generate_content=generate)), 'last_started': 0.0}
        keys = []
        monkeypatch.setattr(client, '_transport', lambda key: keys.append(key) or transport)
        return calls, keys
    return SimpleNamespace(install=install, waits=waits, pacing=pacing)


def synthesize(path, **kwargs):
    return client.synthesize('정확한 원문 대사와 스타일', path, api_key='secret-test-key',
                             model='gemini-3.1-flash-tts-preview', voice='Charon', **kwargs)


def test_503_recovers_twice_with_identical_request_and_native_audio(tmp_path, offline):
    calls, keys = offline.install([api_error(), api_error(), audio_response()])
    metrics, updates = {}, []
    target = tmp_path / 'voice.wav'
    synthesize(target, metrics=metrics, progress=lambda phase, data: updates.append((phase, dict(data))))
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    assert keys == ['secret-test-key']
    assert offline.waits == [2.0, 4.0] and len(offline.pacing) == 3
    assert target.read_bytes() == wav_bytes()
    assert metrics['attempts'] == 3 and metrics['retries'] == 2
    assert metrics['retry_seconds'] == 6.0 and not metrics['retrying']
    assert any('자동 재시도 2/2' in phase for phase, _ in updates)
    assert 'secret-test-key' not in repr(updates)


def test_persistent_503_stops_bounded_and_preserves_previous_file(tmp_path, offline):
    calls, _ = offline.install([api_error()] * 3)
    target = tmp_path / 'voice.wav'
    target.write_bytes(wav_bytes())
    with pytest.raises(RuntimeError, match='자동 재시도 2회') as error:
        synthesize(target)
    assert len(calls) == 3 and target.read_bytes() == wav_bytes()
    assert 'secret-test-key' not in str(error.value)
    assert sorted(path.name for path in tmp_path.iterdir()) == ['voice.wav']


@pytest.mark.parametrize('code', [400, 401, 403, 404, 429])
def test_quota_and_permission_errors_do_not_retry(tmp_path, offline, code):
    calls, _ = offline.install([api_error(code)])
    with pytest.raises(RuntimeError, match=str(code)):
        synthesize(tmp_path / 'voice.wav')
    assert len(calls) == 1 and not offline.waits


@pytest.mark.parametrize('header,expected_attempts', [('5', 2), ('60', 1), ('invalid-date', 1)])
def test_server_retry_after_is_respected(tmp_path, offline, header, expected_attempts):
    calls, _ = offline.install([api_error(headers={'Retry-After': header}), audio_response()])
    if expected_attempts == 1:
        with pytest.raises(RuntimeError, match='서버가 지정한 대기'):
            synthesize(tmp_path / 'voice.wav')
        assert not offline.waits
    else:
        synthesize(tmp_path / 'voice.wav')
        assert offline.waits == [5.0]
    assert len(calls) == expected_attempts


def test_pause_during_backoff_prevents_another_request(tmp_path, offline, monkeypatch):
    calls, _ = offline.install([api_error()])
    paused = threading.Event()
    def wait(delay, cancel):
        paused.set()
        if cancel():
            raise CancelledError()
    monkeypatch.setattr(client, '_wait_retry', wait)
    with pytest.raises(CancelledError):
        synthesize(tmp_path / 'voice.wav', cancel=paused.is_set)
    assert len(calls) == 1 and not list(tmp_path.iterdir())


def test_retry_wait_observes_already_requested_pause():
    with pytest.raises(CancelledError):
        client._wait_retry(10, lambda: True)


def test_no_retry_after_received_audio_fails_validation(tmp_path, offline):
    calls, _ = offline.install([audio_response(b'bad')])
    with pytest.raises(RuntimeError):
        synthesize(tmp_path / 'voice.wav')
    assert len(calls) == 1 and not offline.waits


def test_recovered_jobs_continue_in_order_without_duplicate_completion(tmp_path, offline):
    from core import generation_jobs as jobs, generation_pipeline as pipeline
    from core.tts_engine import VoiceConfig
    lock, counts = threading.Lock(), Counter()
    def generate(**kwargs):
        with lock:
            counts[kwargs['contents']] += 1
            attempt = counts[kwargs['contents']]
        if attempt == 1:
            raise api_error()
        return audio_response()
    # All workers use the same offline fake; real pacing and transport remain
    # covered by the request assertions above, and no speech model is loaded.
    from unittest.mock import patch
    transport = {'client': SimpleNamespace(models=SimpleNamespace(generate_content=generate)), 'last_started': 0.0}
    items = [jobs.GenerationItem(i, '귀례', f'{i}번 대사입니다.', str(tmp_path / f'{i}.mp3'),
                                VoiceConfig(engine='gemini', voice='Charon', api_key='secret-test-key')) for i in range(1, 4)]
    state = dict(id='retry-job', total=3, done=0, reused=0, completed=[], generated=0,
                 recent_seconds=[], pending_total=3, performance={}, started=time.time())
    snapshots = []
    with patch.object(client, '_transport', return_value=transport):
        pipeline.run_generation(str(tmp_path), items, state, threading.Event(), False,
                                lambda *args: snapshots.append(copy.deepcopy(state)), jobs._record_performance)
    assert list(counts.values()) == [2, 2, 2]
    assert state['done'] == 3 and [row['index'] for row in state['completed']] == [1, 2, 3]
    assert not state['engine_errors'] and not state['retrying_lines']
    assert any(snapshot['retrying_lines'] for snapshot in snapshots)
    assert 'secret-test-key' not in repr(snapshots)
    assert not list(tmp_path.glob('.*.part.wav'))
