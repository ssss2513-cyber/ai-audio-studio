"""Selected-model Gemini TTS with bounded, visible transient server recovery."""
from collections import OrderedDict
from concurrent.futures import CancelledError
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import io
import math
import os
from pathlib import Path
import re
import random
import shutil
import subprocess
import tempfile
import threading
import time
import wave

from .gemini_quota import MAX_QUOTA_WAIT, quota_error_message, quota_recovery

_CLIENTS = threading.local()
_PACERS = {}
_PACERS_LOCK = threading.Lock()
_SERVER_RETRIES = 2
_SERVER_RETRY_CODES = {500, 502, 503, 504}
_MAX_RETRY_WAIT = 10.0
# One registered key pool shares this request budget, including previews and
# retries. Ten starts per minute with a small timing margin; a provider's
# stricter quota/cooldown can only increase the interval.
GEMINI_REQUEST_INTERVAL = 6.2


class RequestPacer:
    """Share request starts across workers; waiting for audio can overlap."""
    def __init__(self, interval=GEMINI_REQUEST_INTERVAL):
        self.interval = interval
        self.lock = threading.Lock()
        self.last_started = time.monotonic() - interval
        self.not_before = 0.0

    def defer(self, delay, interval=None):
        """All workers sharing keys must honor the same provider cooldown."""
        with self.lock:
            self.not_before = max(self.not_before, time.monotonic() + delay)
            if interval is not None:
                self.interval = max(self.interval, interval)

    def wait(self, cancel=None, on_cooldown=None):
        started = time.monotonic()
        reported_deadline = None
        while True:
            if cancel and cancel():
                raise CancelledError()
            with self.lock:
                now = time.monotonic()
                remaining = max(self.last_started + self.interval, self.not_before) - now
                cooldown = max(0.0, self.not_before - now)
                deadline = self.not_before if cooldown else 0.0
                if remaining <= 0:
                    self.last_started = now
            # Do not hold the lock while sleeping: another worker's 429 must
            # be able to extend the shared cooldown before the next request.
            if on_cooldown and reported_deadline != deadline:
                on_cooldown(cooldown)
                reported_deadline = deadline
            if remaining <= 0:
                return time.monotonic() - started
            time.sleep(min(remaining, 0.1))


def _pacer(keys):
    # A key set shares one pacing budget; adding keys does not raise that budget.
    identity = hashlib.sha256(str(keys).encode()).hexdigest()
    with _PACERS_LOCK:
        now = time.monotonic()
        for old, pacer in list(_PACERS.items()):
            if now - max(pacer.last_started, pacer.not_before) > 600 and not pacer.lock.locked():
                _PACERS.pop(old, None)
        pacer = _PACERS.setdefault(identity, RequestPacer())
        with pacer.lock:
            pacer.interval = max(pacer.interval, GEMINI_REQUEST_INTERVAL)
        return pacer


def close_worker_clients():
    clients = getattr(_CLIENTS, "clients", {})
    for transport in clients.values():
        try:
            transport["client"].close()
        except Exception:
            pass
    clients.clear()


def _transport(key):
    from google import genai
    from google.genai import types
    if not hasattr(_CLIENTS, "clients"):
        _CLIENTS.clients = OrderedDict()
    clients = _CLIENTS.clients
    if key not in clients:
        clients[key] = {
            "client": genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=180_000, retry_options=types.HttpRetryOptions(attempts=1))),
            "last_started": 0.0,
        }
    clients.move_to_end(key)
    while len(clients) > 4:
        _, old = clients.popitem(last=False)
        old["client"].close()
    return clients[key]


def _status_code(exc):
    code = getattr(exc, "code", None)
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def _server_retry_delay(exc, retry_number):
    """Respect Retry-After; never turn a long provider delay into an early retry."""
    delay = 2.0 * (2 ** retry_number) + random.uniform(0.0, 0.5)
    response = getattr(exc, "response", None)
    header = (getattr(response, "headers", None) or {}).get("Retry-After")
    if header is not None:
        try:
            requested = float(header)
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                requested = (date - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return None
        if not math.isfinite(requested) or requested > _MAX_RETRY_WAIT:
            return None
        delay = max(delay, requested)
    return delay


def _wait_retry(delay, cancel):
    started = time.monotonic()
    while True:
        if cancel and cancel():
            raise CancelledError()
        remaining = delay - (time.monotonic() - started)
        if remaining <= 0:
            return time.monotonic() - started
        time.sleep(min(remaining, 0.1))


def _request_error(exc):
    code = _status_code(exc)
    if code == 429:
        return RuntimeError(quota_error_message(quota_recovery(exc)))
    if code in (401, 403):
        return RuntimeError(f"Gemini 접근 오류({code})입니다. API 키와 선택한 모델의 이용 권한을 확인해주세요.")
    if code in (400, 404):
        return RuntimeError(f"선택한 Gemini 모델의 요청 오류({code})입니다. 모델과 음성 설정을 확인해주세요. 다른 모델로 자동 변경하지 않았습니다.")
    if code and code >= 500:
        return RuntimeError(f"Gemini 서버 오류({code})로 요청이 실패했습니다. 완료 파일은 유지됩니다. 잠시 후 이어서 생성해주세요.")
    return RuntimeError("Gemini 응답을 받지 못했습니다 (" + type(exc).__name__
                        + "). 중복 생성은 하지 않았습니다. 연결 상태를 확인한 뒤 이어서 생성해주세요.")


def synthesize(prompt, output_file, *, api_key, model, voice, metrics=None, progress=None,
               pacing_group=None, cancel=None):
    """Preserve model, voice, prompt and native PCM. Reuse one client per worker."""
    from google.genai import types
    started = time.monotonic()
    metrics = metrics if metrics is not None else {}
    metrics.update(engine="gemini", model=model, attempts=0, pacing_seconds=0.0,
                   request_seconds=0.0, retries=0, retry_seconds=0.0, retrying=False,
                   quota_retries=0, waiting_for_quota=False, quota_retry_at=0.0)
    target = Path(output_file).absolute()
    if target.suffix.lower() not in (".wav", ".mp3"):
        raise ValueError("출력 파일은 WAV 또는 MP3여야 합니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    transport = _transport(api_key)
    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))))
    pacer = _pacer(pacing_group or api_key)
    previous_quota_delay = 0.0

    def cooldown_progress(remaining):
        metrics.update(waiting_for_quota=remaining > 0,
                       quota_retry_at=time.time() + remaining if remaining > 0 else 0.0)
        if remaining > 0:
            metrics["retrying"] = True
            if progress:
                progress("Gemini 요청 제한(429) · 대기 후 자동 이어서 생성", metrics)

    for attempt in range(_SERVER_RETRIES + 1):
        if progress:
            progress("요청 간격 조절" if attempt == 0 else f"Gemini 재시도 {attempt}/{_SERVER_RETRIES} · 요청 간격 조절", metrics)
        metrics["pacing_seconds"] += pacer.wait(cancel, on_cooldown=cooldown_progress)
        if cancel and cancel():
            raise CancelledError()
        metrics.update(retrying=attempt > 0, waiting_for_quota=False, quota_retry_at=0.0)
        metrics["attempts"] = attempt + 1
        metrics["retries"] = attempt
        if progress:
            progress("Gemini 응답 대기" if attempt == 0 else f"Gemini 재시도 {attempt}/{_SERVER_RETRIES} · 응답 대기", metrics)
        request_started = time.monotonic()
        transport["last_started"] = request_started
        failure = None
        try:
            response = transport["client"].models.generate_content(
                model=model, contents=prompt, config=config)
        except CancelledError:
            raise
        except Exception as exc:
            failure = exc
        finally:
            metrics["request_seconds"] += time.monotonic() - request_started
        if failure is None:
            break
        code = _status_code(failure)
        if code == 429 and attempt < _SERVER_RETRIES:
            recovery = quota_recovery(failure)
            if recovery.kind == "temporary":
                delay = max(recovery.delay, previous_quota_delay * 2) + random.uniform(0.0, 0.5)
                if delay <= MAX_QUOTA_WAIT:
                    previous_quota_delay = delay
                    pacer.defer(delay, recovery.interval)
                    metrics["quota_retries"] += 1
                    metrics["retrying"] = True
                    continue
        if code not in _SERVER_RETRY_CODES or attempt == _SERVER_RETRIES:
            error = _request_error(failure)
            if attempt:
                error = RuntimeError(f"자동 재시도 {attempt}회 후에도 실패했습니다. {error}")
            raise error from None
        delay = _server_retry_delay(failure, attempt)
        if delay is None:
            raise RuntimeError(str(_request_error(failure))
                               + " 서버가 지정한 대기 시간을 지키기 위해 자동 재시도를 멈췄습니다.") from None
        metrics["retrying"] = True
        if progress:
            progress(f"Gemini 서버 오류({code}) · 약 {math.ceil(delay)}초 후 자동 재시도 {attempt + 1}/{_SERVER_RETRIES}", metrics)
        metrics["retry_seconds"] += _wait_retry(delay, cancel)
    metrics["retrying"] = False
    conversion_started = time.monotonic()
    if progress:
        progress("Gemini 원음 저장", metrics)
    audio_bytes, mime = None, ""
    for candidate in getattr(response, "candidates", None) or []:
        for part in getattr(getattr(candidate, "content", None), "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                audio_bytes, mime = inline.data, (getattr(inline, "mime_type", "") or "")
                break
        if audio_bytes:
            break
    if not audio_bytes:
        raise RuntimeError("Gemini가 오디오를 반환하지 않았습니다. 완료 파일을 보관하고 멈췄습니다. 다른 모델로 반복 생성하지 않았습니다.")
    if not audio_bytes.startswith(b"RIFF"):
        rate = re.search(r"(?:^|;)\s*rate=(\d+)", mime)
        if rate and int(rate.group(1)) != 24000:
            raise RuntimeError("Gemini 오디오의 샘플레이트가 예상과 다릅니다. 음높이 변경을 피하기 위해 저장을 멈췄습니다.")
        if mime and not mime.lower().startswith(("audio/l16", "audio/pcm")):
            raise RuntimeError("Gemini가 예상과 다른 오디오 형식을 반환했습니다: " + mime[:80])
        if len(audio_bytes) % 2:
            raise RuntimeError("Gemini 원음 데이터가 잘렸습니다. 음성을 저장하지 않았습니다.")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(audio_bytes)
        audio_bytes = buffer.getvalue()
    with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
        frames, rate = wav.getnframes(), wav.getframerate()
        if frames <= 0 or rate <= 0 or len(wav.readframes(frames)) != frames * wav.getnchannels() * wav.getsampwidth():
            raise RuntimeError("Gemini 원음 파일이 비어 있거나 잘렸습니다.")
        metrics["audio_seconds"] = frames / rate
    with tempfile.TemporaryDirectory(prefix="gemini_", dir=target.parent) as folder:
        source = Path(folder) / "speech.wav"
        source.write_bytes(audio_bytes)
        if target.suffix.lower() == ".wav":
            completed = source
        else:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("MP3 저장에 필요한 FFmpeg가 없습니다.")
            completed = Path(folder) / "speech.mp3"
            result = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source),
                                     "-b:a", "192k", str(completed)], capture_output=True, timeout=60)
            if result.returncode:
                raise RuntimeError("Gemini MP3 변환에 실패했습니다. 완료 파일은 유지됩니다.")
        os.replace(completed, target)
    metrics.update(postprocess_seconds=time.monotonic() - conversion_started,
                   total_seconds=time.monotonic() - started)
    return str(output_file)
