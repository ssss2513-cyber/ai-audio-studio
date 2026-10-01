"""No paid API calls: quota windows, shared waiting, and saved audio continuity."""
from concurrent.futures import CancelledError, ThreadPoolExecutor
import copy
import io
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import wave

from google.genai.errors import ClientError
import pytest

from core import gemini_client as client
from core.gemini_quota import quota_recovery


def quota_error(quota_id="GenerateRequestsPerMinutePerProjectPerModel", value="15", delay="51s",
                headers=None, message="private-test-key"):
    details = []
    if quota_id is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": quota_id, "quotaValue": value}]})
    if delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay})
    return ClientError(429, {"error": {"code": 429, "message": message, "details": details}},
                       response=SimpleNamespace(headers=headers or {}))


def audio_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x20\x00" * 2400)
    return output.getvalue()


def audio_response():
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[
        SimpleNamespace(inline_data=SimpleNamespace(data=audio_bytes(), mime_type="audio/wav"))]))])


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import httpx
    import requests
    def forbidden(*args, **kwargs):
        raise AssertionError("Quota tests must not contact a speech API")
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(client.random, "uniform", lambda *args: 0)


@pytest.fixture
def virtual(monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    def sleep(seconds):
        clock.now += seconds
    monkeypatch.setattr(client, "time", SimpleNamespace(monotonic=lambda: clock.now,
                                                        time=lambda: clock.now, sleep=sleep))
    pacer = client.RequestPacer()
    monkeypatch.setattr(client, "_pacer", lambda *args: pacer)
    calls, times = [], []
    def install(replies):
        def generate(**kwargs):
            calls.append(copy.deepcopy(kwargs))
            times.append(clock.now)
            reply = replies[len(calls) - 1]
            if isinstance(reply, Exception):
                raise reply
            return reply
        transport = {"client": SimpleNamespace(models=SimpleNamespace(generate_content=generate))}
        monkeypatch.setattr(client, "_transport", lambda key: transport)
    return SimpleNamespace(install=install, calls=calls, times=times, pacer=pacer, clock=clock)


def synthesize(path, **kwargs):
    return client.synthesize("원래 대사와 연기 지시", path, api_key="private-test-key",
                             model="gemini-3.1-flash-tts-preview", voice="Charon", **kwargs)


def test_51_second_limit_resumes_same_request_and_keeps_native_audio(tmp_path, virtual):
    virtual.install([quota_error(), audio_response()])
    metrics, updates = {}, []
    target = tmp_path / "31.wav"
    synthesize(target, metrics=metrics, progress=lambda phase, data: updates.append((phase, dict(data))))
    assert virtual.times[1] - virtual.times[0] >= 51
    assert len(virtual.calls) == 2 and virtual.calls[0] == virtual.calls[1]
    assert target.read_bytes() == audio_bytes()
    assert metrics["attempts"] == 2 and metrics["quota_retries"] == 1
    assert not metrics["retrying"] and not metrics["waiting_for_quota"]
    assert any(data["waiting_for_quota"] and data["quota_retry_at"] > virtual.times[0]
               for _, data in updates)
    assert "private-test-key" not in repr(updates)


def test_repeated_limit_uses_bounded_exponential_wait_and_preserves_audio(tmp_path, virtual):
    virtual.install([quota_error()] * 3)
    target = tmp_path / "31.wav"
    target.write_bytes(audio_bytes())
    with pytest.raises(RuntimeError, match="자동 재시도 2회") as failure:
        synthesize(target)
    assert len(virtual.calls) == 3
    assert virtual.times[1] - virtual.times[0] >= 52
    assert virtual.times[2] - virtual.times[1] >= 104
    assert target.read_bytes() == audio_bytes()
    assert "private-test-key" not in str(failure.value)


@pytest.mark.parametrize("error,reason", [
    (quota_error("GenerateRequestsPerDayPerProject", "100"), "일일"),
    (quota_error(value="0"), "한도가 0"),
    (quota_error("SpendLimitPerProject", "100"), "결제"),
    (quota_error(quota_id=None, delay=None), "대기 시간을 알려주지"),
    (quota_error(headers={"Retry-After": "3600"}), "긴 한도 대기"),
    (quota_error(headers={"Retry-After": "invalid"}), "대기 시간을 알려주지"),
])
def test_nonrecoverable_limit_never_retries_even_with_short_retry_info(tmp_path, virtual, error, reason):
    virtual.install([error])
    with pytest.raises(RuntimeError, match=reason):
        synthesize(tmp_path / "31.wav")
    assert len(virtual.calls) == 1 and not list(tmp_path.iterdir())


@pytest.mark.parametrize("error,minimum", [
    (quota_error(delay={"seconds": "51", "nanos": 500000000}), 51.5),
    (quota_error(headers={"retry-after": "75"}), 75),
    (quota_error(quota_id=None, delay=None, message="Please retry in 50.32s."), 50.32),
    (quota_error(delay=None), 60),
])
def test_all_server_delay_forms_are_honored(tmp_path, virtual, error, minimum):
    virtual.install([error, audio_response()])
    synthesize(tmp_path / "31.wav")
    assert virtual.times[1] - virtual.times[0] >= minimum


def test_advertised_rpm_reduces_future_request_frequency(tmp_path, virtual):
    virtual.install([quota_error(value="2", delay="1s"), audio_response(), audio_response()])
    synthesize(tmp_path / "31.wav")
    synthesize(tmp_path / "32.wav")
    assert virtual.pacer.interval >= 30
    assert virtual.times[2] - virtual.times[1] >= 30


def test_daily_violation_takes_priority_over_minute_violation():
    error = quota_error()
    error.details["error"]["details"][0]["violations"].append({
        "quotaId": "GenerateRequestsPerDayPerProject", "quotaValue": "100"})
    assert quota_recovery(error).kind == "daily"


def test_zero_limit_in_provider_message_stops_despite_retry_hint():
    assert quota_recovery(quota_error(quota_id=None, message="Quota exceeded, limit: 0, model: tts")).kind == "zero"


def test_waiting_workers_honor_new_cooldown_and_remain_spaced():
    pacer = client.RequestPacer(interval=0.06)
    pacer.wait()
    waiting = threading.Event()
    def worker():
        pacer.wait(on_cooldown=lambda _: waiting.set())
        return time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        assert waiting.wait(1)
        deadline = time.monotonic() + 0.14
        pacer.defer(0.14)
        starts = sorted(future.result(timeout=2) for future in futures)
    assert starts[0] >= deadline
    assert starts[1] - starts[0] >= 0.05


def test_pause_during_quota_wait_sends_no_retry(tmp_path, virtual):
    virtual.install([quota_error()])
    pause = threading.Event()
    def progress(phase, metrics):
        if metrics["waiting_for_quota"]:
            pause.set()
    with pytest.raises(CancelledError):
        synthesize(tmp_path / "31.wav", progress=progress, cancel=pause.is_set)
    assert len(virtual.calls) == 1 and not list(tmp_path.iterdir())


def test_cosy_finishes_during_gemini_wait_and_job_keeps_original_order(tmp_path, monkeypatch):
    from core import generation_jobs as jobs, generation_pipeline as pipeline, cosy_batch_client
    from core.tts_engine import VoiceConfig
    pacer = client.RequestPacer(interval=0.001)
    original_defer = pacer.defer
    # Use a short real window to exercise threads. The virtual-clock tests
    # above independently assert the full provider-advertised 51-second wait.
    monkeypatch.setattr(pacer, "defer", lambda delay, interval: original_defer(0.15))
    monkeypatch.setattr(client, "_pacer", lambda *args: pacer)
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise quota_error()
        return audio_response()
    monkeypatch.setattr(client, "_transport", lambda key: {
        "client": SimpleNamespace(models=SimpleNamespace(generate_content=generate))})
    monkeypatch.setattr(cosy_batch_client, "queue_limits", lambda url: (1, 1))
    monkeypatch.setattr(cosy_batch_client, "synthesize_batch", lambda *args, **kwargs: False)
    monkeypatch.setattr(pipeline.TTSEngine, "generate_cosyvoice_speech",
                        lambda text, output, config, **kwargs: Path(output).write_bytes(audio_bytes()))
    reference = tmp_path / "reference.wav"
    reference.write_bytes(audio_bytes())
    items = [jobs.GenerationItem(1, "귀례", "원래 대사", str(tmp_path / "1.mp3"),
                                VoiceConfig(engine="gemini", voice="Charon", api_key="private-test-key"))]
    items += [jobs.GenerationItem(i, "나레이션", "이어지는 대사", str(tmp_path / f"{i}.mp3"),
                                 VoiceConfig(engine="cosyvoice", ref_audio_path=str(reference),
                                             prompt_text="참고 문장", cosyvoice_url="http://offline.invalid"))
              for i in (2, 3)]
    state = dict(id="quota-job", total=3, done=0, reused=0, completed=[], generated=0,
                 recent_seconds=[], pending_total=3, performance={}, started=time.time())
    snapshots = []
    pipeline.run_generation(str(tmp_path), items, state, threading.Event(), False,
                            lambda *args: snapshots.append(copy.deepcopy(state)), jobs._record_performance)
    assert len(calls) == 2 and state["done"] == 3
    assert [part["index"] for part in state["completed"]] == [1, 2, 3]
    assert any(s["engine_progress"]["gemini"]["status"] == "quota_wait"
               and s["engine_progress"]["cosyvoice"]["done"] == 2 for s in snapshots)
    assert not state["engine_errors"] and not state["retrying_lines"]
    assert not list(tmp_path.glob(".*.part.wav"))
