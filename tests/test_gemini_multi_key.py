from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from core import gemini_keys, gemini_client
from core.tts_engine import TTSEngine, VoiceConfig


@pytest.fixture(autouse=True)
def reset_key_counters():
    gemini_keys._KEY_COUNTERS.clear()
    yield
    gemini_keys._KEY_COUNTERS.clear()


@pytest.mark.parametrize('raw', [
    'one-key,two-key,three-key', 'one-key\ntwo-key\nthree-key',
    'one-key；two-key，three-key',
    'API Key 1: "one-key"\nAPI Key 2: "two-key"\nAPI Key 3: "three-key"',
])
def test_three_keys_are_recognized_separately(raw):
    assert gemini_keys.parse_gemini_keys(raw) == ['one-key', 'two-key', 'three-key']


def test_parallel_selection_uses_all_keys_fairly_without_cross_user_counter():
    keys = ['one-key', 'two-key', 'three-key']
    with ThreadPoolExecutor(max_workers=6) as pool:
        selected = list(pool.map(lambda _: gemini_keys.select_gemini_key(keys), range(60)))
    assert Counter(key for key, _ in selected) == dict.fromkeys(keys, 20)
    assert gemini_keys.select_gemini_key(['other-one', 'other-two']) == ('other-one', 1)


def test_synthesis_receives_three_distinct_credentials_and_only_safe_metrics(monkeypatch):
    calls, snapshots = [], []
    def synthesize(prompt, output_file, **kwargs):
        calls.append(kwargs['api_key'])
        snapshots.append(dict(kwargs['metrics']))
        assert kwargs['pacing_group'] == ('one-key', 'three-key', 'two-key')
        return output_file
    monkeypatch.setattr(gemini_client, 'synthesize', synthesize)
    config = VoiceConfig(engine='gemini', voice='Kore', api_key='one-key,two-key,three-key')
    for _ in range(6):
        TTSEngine.generate_gemini_speech('원래 대사', 'unused.wav', config)
    assert calls == ['one-key', 'two-key', 'three-key'] * 2
    assert [row['key_index'] for row in snapshots] == [1, 2, 3] * 2
    assert all(row['key_count'] == 3 for row in snapshots)
    assert not any(key in repr(snapshots) for key in calls)


def test_failure_identifies_key_without_switching_to_another_key(monkeypatch):
    calls = []
    def synthesize(prompt, output_file, **kwargs):
        calls.append(kwargs['api_key'])
        raise RuntimeError('일일 한도(429)')
    monkeypatch.setattr(gemini_client, 'synthesize', synthesize)
    config = VoiceConfig(engine='gemini', voice='Kore', api_key='one-key,two-key,three-key')
    with pytest.raises(RuntimeError, match='키 1/3') as failure:
        TTSEngine.generate_gemini_speech('원래 대사', 'unused.wav', config)
    assert calls == ['one-key']
    assert '429' in str(failure.value)
    assert '요청 모델 gemini-3.1-flash-tts-preview' in str(failure.value)


def test_job_reports_each_key_and_retries_count_once(tmp_path, monkeypatch):
    from core import generation_jobs as jobs, generation_pipeline as pipeline
    import io
    import wave
    def synthesize(prompt, output_file, **kwargs):
        metrics, progress = kwargs['metrics'], kwargs['progress']
        for attempt in (1, 1, 2, 2):
            metrics.update(attempts=attempt)
            progress('응답 대기', metrics)
        with wave.open(str(output_file), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b'\x01\x00' * 2400)
    monkeypatch.setattr(gemini_client, 'synthesize', synthesize)
    config = VoiceConfig(engine='gemini', voice='Kore', api_key='one-key,two-key,three-key')
    items = [jobs.GenerationItem(i, '화자', str(i), str(tmp_path / f'{i}.mp3'), config) for i in range(1, 7)]
    state = dict(id='keys-test', total=6, done=0, reused=0, completed=[], generated=0,
                 recent_seconds=[], pending_total=6, performance={}, started=0)
    pipeline.run_generation(str(tmp_path), items, state, threading.Event(), False, lambda *args: None, jobs._record_performance)
    assert all(row['requests'] == 4 and row['completed'] == 2 for row in state['gemini_key_usage'].values())
    assert len(state['gemini_key_usage']) == 3
    assert not any(key in repr(state) for key in ['one-key', 'two-key', 'three-key'])
