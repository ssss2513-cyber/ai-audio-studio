"""Google Cloud Chirp 3 HD, isolated from Gemini and personal GPU runtimes.

Only an explicit synthesis request contacts Google. Credentials are sent in a
header to the fixed Google endpoint, never in URLs, files, or progress events.
"""
import base64
from concurrent.futures import CancelledError
from email.utils import parsedate_to_datetime
import hashlib
import io
import math
from pathlib import Path
import re
import threading
import time
import wave

import requests


ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
MAX_INPUT_BYTES = 4500  # Cloud TTS's hard limit is 5,000 UTF-8 bytes.
MAX_WORKERS = 4
REQUEST_INTERVAL = 1.0  # Separate from Gemini's seven-second request pacer.

# Official Chirp 3 HD catalog; voice gender must not be inferred from its name.
CHIRP_VOICES = {
    "Achernar": "여성", "Achird": "남성", "Algenib": "남성",
    "Algieba": "남성", "Alnilam": "남성", "Aoede": "여성",
    "Autonoe": "여성", "Callirrhoe": "여성", "Charon": "남성",
    "Despina": "여성", "Enceladus": "남성", "Erinome": "여성",
    "Fenrir": "남성", "Gacrux": "여성", "Iapetus": "남성",
    "Kore": "여성", "Laomedeia": "여성", "Leda": "여성",
    "Orus": "남성", "Pulcherrima": "여성", "Puck": "남성",
    "Rasalgethi": "남성", "Sadachbia": "남성", "Sadaltager": "남성",
    "Schedar": "남성", "Sulafat": "여성", "Umbriel": "남성",
    "Vindemiatrix": "여성", "Zephyr": "여성", "Zubenelgenubi": "남성",
}


def validate_key(value):
    key = (value or "").strip()
    if not key:
        raise ValueError("왼쪽 ‘Google Chirp 3 HD 설정’에 Cloud Text-to-Speech API 키를 입력해주세요.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", key):
        raise ValueError("Cloud Text-to-Speech API 키 한 개만 입력해주세요. 주소나 JSON 파일 내용은 넣지 않습니다.")
    return key


def split_text(text):
    """Preserve every character and split oversized Korean text at boundaries."""
    remaining = str(text).strip()
    if not remaining:
        raise ValueError("Chirp로 읽을 대사가 비어 있습니다.")
    chunks = []
    while len(remaining.encode("utf-8")) > MAX_INPUT_BYTES:
        size = 0
        end = 0
        for char in remaining:
            size += len(char.encode("utf-8"))
            if size > MAX_INPUT_BYTES:
                break
            end += 1
        prefix = remaining[:end]
        boundaries = list(re.finditer(r"[.!?。！？](?:[\"'”’])?(?:\s+|$)|\n+", prefix))
        cut = boundaries[-1].end() if boundaries else max(prefix.rfind(" ") + 1, 0)
        if cut < end // 3:
            cut = end
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


class _Pacer:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_start = 0.0

    def wait(self, cancel):
        started = time.monotonic()
        while True:
            if cancel():
                raise CancelledError()
            with self.lock:
                delay = self.next_start - time.monotonic()
                if delay <= 0:
                    self.next_start = time.monotonic() + REQUEST_INTERVAL
                    return time.monotonic() - started
            time.sleep(min(0.2, delay))

    def defer(self, seconds):
        with self.lock:
            self.next_start = max(self.next_start, time.monotonic() + seconds)


_PACERS_LOCK = threading.Lock()
_PACERS = {}
_LOCAL = threading.local()


def _pacer(key):
    fingerprint = hashlib.sha256(key.encode()).hexdigest()
    with _PACERS_LOCK:
        if fingerprint not in _PACERS:
            _PACERS[fingerprint] = _Pacer()
        return _PACERS[fingerprint]


def _session():
    if getattr(_LOCAL, "session", None) is None:
        _LOCAL.session = requests.Session()
    return _LOCAL.session


def close_worker_client():
    session = getattr(_LOCAL, "session", None)
    if session is not None:
        session.close()
        del _LOCAL.session


def _error_info(response):
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    details = error.get("details", [])
    return error, details if isinstance(details, list) else []


def _retry_delay(response, details, fallback):
    delays = [float(fallback)]
    value = response.headers.get("Retry-After", "")
    try:
        delays.append(float(value))
    except (TypeError, ValueError):
        try:
            delays.append(parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            pass
    for detail in details:
        if not isinstance(detail, dict):
            continue
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            try:
                delays.append(float(str(detail.get("retryDelay", "")).removesuffix("s")))
            except ValueError:
                pass
    return max(value for value in delays if math.isfinite(value) and value >= 0)


def _error_message(status, error, details):
    # Classify provider metadata, but never echo raw responses/credentials.
    reasons = " ".join(str(row.get("reason", "")) for row in details if isinstance(row, dict)).upper()
    message = str(error.get("message", "")).lower()
    if "SERVICE_DISABLED" in reasons or "has not been used" in message or "is disabled" in message:
        return "Google Cloud에서 이 키의 프로젝트에 ‘Cloud Text-to-Speech API’를 사용 설정해주세요."
    if "BILLING" in reasons or "billing" in message:
        return "이 키의 Google Cloud 프로젝트에 결제 계정을 연결하고 결제 상태를 확인해주세요."
    if status == 401 or "API_KEY_INVALID" in reasons:
        return "Cloud Text-to-Speech API 키가 올바르지 않습니다. Google Cloud 콘솔에서 발급한 키를 확인해주세요."
    if status == 403:
        return ("Cloud TTS 접근이 거부되었습니다. 키의 API 제한에 ‘Cloud Text-to-Speech API’를 허용하고, "
                "공유 사이트의 서버 요청을 허용하는 키인지 확인해주세요.")
    if status == 429:
        return "Cloud TTS 프로젝트의 요청·사용량 한도에 도달했습니다. Google Cloud 할당량을 확인한 뒤 이어서 생성해주세요."
    if status in (400, 404):
        return "Chirp 3 HD 요청이 거부되었습니다. 한국어 보이스와 속도 설정, Cloud TTS 사용 설정을 확인해주세요."
    return f"Google Cloud TTS 응답 오류({status})입니다. 잠시 후 이어서 생성해주세요."


def synthesize(text, output_file, *, api_key, voice="Kore", speed=1.0,
               metrics=None, progress=None, cancel=None):
    key = validate_key(api_key)
    if voice not in CHIRP_VOICES:
        raise ValueError("Chirp 3 HD 목록에서 목소리를 다시 선택해주세요.")
    speed = float(speed)
    if not math.isfinite(speed) or not 0.25 <= speed <= 2.0:
        raise ValueError("Chirp 읽기 속도는 0.25~2.0 사이여야 합니다.")
    target = Path(output_file)
    if target.suffix.lower() != ".wav":
        raise ValueError("Chirp 원음 저장 경로는 WAV 형식이어야 합니다.")
    chunks = split_text(text)
    metrics = metrics if metrics is not None else {}
    metrics.update(engine="chirp", model="chirp-3-hd", attempts=0, retries=0,
                   characters=0, chunks=len(chunks), request_seconds=0.0,
                   pacing_seconds=0.0, postprocess_seconds=0.0)
    progress = progress or (lambda phase, details: None)
    cancel = cancel or (lambda: False)
    pacer = _pacer(key)
    audio_parts = []
    for number, chunk in enumerate(chunks, 1):
        body = {
            "input": {"text": chunk},
            "voice": {"languageCode": "ko-KR", "name": f"ko-KR-Chirp3-HD-{voice}"},
            "audioConfig": {"audioEncoding": "LINEAR16", "speakingRate": speed},
        }
        retry_details = {}
        for attempt in range(3):
            progress("Google 제한 대기 후 재시도" if retry_details else "요청 간격 대기",
                     dict(retry_details, chunk=number, chunks=len(chunks)))
            metrics["pacing_seconds"] += pacer.wait(cancel)
            metrics["attempts"] += 1
            metrics["characters"] += len(chunk)
            progress("Google 음성 생성·수신", {"attempts": metrics["attempts"], "chunk": number})
            started = time.monotonic()
            try:
                response = _session().post(
                    ENDPOINT, headers={"X-Goog-Api-Key": key}, json=body,
                    timeout=(10, 180), allow_redirects=False)
            except requests.RequestException:
                # A timeout may already have incurred usage; avoid duplicate
                # synthesis behind the user's back when no response is received.
                raise RuntimeError("Cloud TTS 응답을 받지 못했습니다. 완료된 대사는 유지됩니다. 잠시 후 이어서 생성해주세요.") from None
            finally:
                metrics["request_seconds"] += time.monotonic() - started
            with response:
                if response.status_code == 200:
                    try:
                        data = response.json()
                        audio = base64.b64decode(data["audioContent"], validate=True)
                    except (ValueError, KeyError, TypeError):
                        raise RuntimeError("Google Cloud TTS 음성 응답이 올바르지 않습니다. 완료된 대사는 유지됩니다.") from None
                    audio_parts.append(audio)
                    break
                error, details = _error_info(response)
                diagnostic = " ".join(str(row) for row in details).lower()
                daily_or_zero = any(word in diagnostic for word in ("perday", "per_day", "permonth", "per_month"))
                for row in details:
                    if isinstance(row, dict):
                        for violation in row.get("violations", []) if isinstance(row.get("violations", []), list) else []:
                            if isinstance(violation, dict) and str(violation.get("quotaValue", "")) in ("0", "0.0"):
                                daily_or_zero = True
                retryable = response.status_code in (429, 500, 502, 503, 504)
                if attempt == 2 or not retryable or daily_or_zero:
                    raise RuntimeError(_error_message(response.status_code, error, details))
                delay = _retry_delay(response, details, 15 * (attempt + 1) if response.status_code == 429 else 2 ** (attempt + 1))
                pacer.defer(delay)
                metrics["retries"] += 1
                retry_details = {
                    "retrying": True, "waiting_for_quota": response.status_code == 429,
                    "quota_retry_at": time.time() + delay, "attempts": metrics["attempts"],
                }
                progress("Google 제한 대기 후 재시도", retry_details)
                if delay > 180:
                    raise RuntimeError(f"Google이 {math.ceil(delay)}초 뒤 재요청하도록 안내했습니다. 그 이후 이어서 생성해주세요.")
        # Keep the response for the last requested chunk even when pause is set.
        if number < len(chunks) and cancel():
            raise CancelledError()

    started = time.monotonic()
    target.parent.mkdir(parents=True, exist_ok=True)
    params = None
    frames = []
    try:
        for audio in audio_parts:
            with wave.open(io.BytesIO(audio), "rb") as source:
                current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                if source.getcomptype() != "NONE" or not source.getnframes():
                    raise ValueError("empty audio")
                if params is not None and current != params:
                    raise ValueError("inconsistent audio")
                params = current
                raw = source.readframes(source.getnframes())
                if len(raw) != source.getnframes() * current[0] * current[1]:
                    raise ValueError("incomplete audio")
                frames.append(raw)
        with wave.open(str(target), "wb") as output:
            output.setnchannels(params[0])
            output.setsampwidth(params[1])
            output.setframerate(params[2])
            for raw in frames:
                output.writeframesraw(raw)
    except (wave.Error, EOFError, ValueError, TypeError):
        target.unlink(missing_ok=True)
        raise RuntimeError("Chirp 음성 원음을 저장하지 못했습니다. 완료된 이전 대사는 유지됩니다.") from None
    metrics["postprocess_seconds"] = time.monotonic() - started
    metrics["audio_seconds"] = sum(map(len, frames)) / (params[0] * params[1] * params[2])
    return str(target)
