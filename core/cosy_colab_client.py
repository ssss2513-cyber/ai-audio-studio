"""Client for the self-contained CosyVoice 2 Colab notebook (API v1)."""
import io
from collections import OrderedDict
import hashlib
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit
import wave

import requests

ENGINES = {
    "cosyvoice": ("ai-voice-studio-cosyvoice", "CosyVoice 2"),
}
GENERATION_CAPABILITY = "validated_generation_v293"
REFERENCE_CACHE_CAPABILITY = "reference_cache_v294"
REFERENCE_TRANSPORT_CAPABILITY = "reference_transport_v295"
_CLIENTS = threading.local()


class _Transport:
    """A connection belongs to one worker thread and one private Colab URL."""
    def __init__(self):
        self.session = requests.Session()
        self.status = None
        self.checked_at = 0.0
        self.reference_ids = OrderedDict()

    def invalidate(self):
        self.status = None
        self.checked_at = 0.0
        self.reference_ids.clear()


def _transport(base):
    if not hasattr(_CLIENTS, "connections"):
        _CLIENTS.connections = OrderedDict()
    connections = _CLIENTS.connections
    if base not in connections:
        connections[base] = _Transport()
    connections.move_to_end(base)
    while len(connections) > 4:
        _, old = connections.popitem(last=False)
        old.session.close()
    return connections[base]



def normalize_url(url):
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("코랩에 표시된 https:// 주소 전체를 입력해주세요.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("코랩의 '프로그램 연결 주소'를 그대로 복사해주세요.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def check_connection(url, engine="cosyvoice", *, use_cached=False):
    service, label = ENGINES[engine]
    try:
        base = normalize_url(url)
        transport = _transport(base)
        if use_cached and transport.status is not None and time.monotonic() - transport.checked_at < 60:
            status = transport.status
        else:
            response = transport.session.get(base + "/health", timeout=(10, 20), allow_redirects=False)
            response.raise_for_status()
            status = response.json()
            if not isinstance(status, dict):
                raise ValueError("invalid health response")
            if transport.status and transport.status.get("instance_id") != status.get("instance_id"):
                transport.reference_ids.clear()
        if status.get("service") != service or status.get("api_version") != 1:
            transport.invalidate()
            return False, f"{label}의 프로그램 연결 주소가 아닙니다. 코랩에 표시된 엔진 이름을 확인해주세요.", None
        if status.get("ready") is not True:
            transport.invalidate()
            return False, "코랩에서 모델을 준비 중입니다. '준비 완료'가 나온 뒤 연결해주세요.", None
        if not {GENERATION_CAPABILITY, REFERENCE_CACHE_CAPABILITY}.issubset(status.get("capabilities", [])):
            transport.invalidate()
            return False, (
                "이전 CosyVoice 서버가 실행 중입니다. 사이트의 'CosyVoice 코랩 v2.9.5 바로 열기'로 "
                "수정본을 열고 1번 준비 완료 → 4번 순서로 실행한 뒤 새 연결 주소를 넣어주세요."
            ), status
        if transport.status is not status:
            transport.status = status
            transport.checked_at = time.monotonic()
        message = f"✅ {label} v{status.get('server_version', '')} 모델 준비 완료 · 프로그램 연결 성공"
        if REFERENCE_TRANSPORT_CAPABILITY in status.get("capabilities", []):
            message += " · 참고 음성 반복 전송 생략"
        return True, message, status
    except (requests.RequestException, ValueError, AttributeError) as exc:
        if "transport" in locals():
            transport.invalidate()
        if isinstance(exc, requests.HTTPError):
            detail = f"HTTP {exc.response.status_code}"
        else:
            detail = type(exc).__name__
        return False, (
            f"연결할 수 없습니다 ({detail}). 수정 코랩의 설치 셀과 연결 셀을 실행한 뒤, "
            "새 '프로그램 연결 주소' 전체를 넣어주세요."
        ), None


def synthesize(url, text, ref_path, prompt_text, speed, output_file, engine="cosyvoice", *, style_instruction=""):
    _, label = ENGINES[engine]
    base = normalize_url(url)
    if not text.strip():
        raise ValueError("생성할 대사를 입력해주세요.")
    if not prompt_text.strip():
        raise ValueError("'참조 오디오 실제 대사'에 녹음에서 말한 내용을 그대로 입력해주세요.")
    if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
        raise ValueError("말하기 속도는 0.5~2.0 사이로 설정해주세요.")
    reference = Path(ref_path)
    if not reference.is_file():
        raise FileNotFoundError("참조 오디오 파일을 찾을 수 없습니다.")
    if reference.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("참조 음성은 3~30초, 10MB 이하의 WAV 또는 MP3를 사용해주세요.")
    target = Path(output_file).absolute()
    if target.suffix.lower() not in (".wav", ".mp3"):
        raise ValueError("출력 파일 확장자는 .wav 또는 .mp3여야 합니다.")
    ffmpeg = shutil.which("ffmpeg")
    if target.suffix.lower() == ".mp3" and not ffmpeg:
        raise RuntimeError("MP3 저장에 필요한 FFmpeg가 없습니다. FFmpeg 설치 후 프로그램을 다시 열어주세요.")
    ok, message, status = check_connection(base, engine=engine, use_cached=True)
    if not ok:
        raise RuntimeError(message)
    if style_instruction and "style_instruction" not in (status or {}).get("capabilities", []):
        raise RuntimeError("이 코랩은 스타일을 지원하지 않는 이전 버전입니다. 사이트의 CosyVoice 코랩 바로 열기로 최신 코랩을 열고, 런타임을 다시 시작한 뒤 1번과 4번을 실행해주세요. 기존 코랩은 '기본' 스타일로 사용할 수 있습니다.")
    transport = _transport(base)
    supports_ids = REFERENCE_TRANSPORT_CAPABILITY in (status or {}).get("capabilities", [])
    # Hash the exact uploaded bytes. Filename/mtime alone can select a different
    # person's voice after an upload replaces an earlier recording.
    with reference.open("rb") as handle:
        reference_bytes = handle.read(10 * 1024 * 1024 + 1)
    if not reference_bytes or len(reference_bytes) > 10 * 1024 * 1024:
        raise ValueError("참고 음성은 10MB 이하의 WAV 또는 MP3로 등록해주세요.")
    identity = (hashlib.sha256(reference_bytes).hexdigest(), prompt_text, style_instruction)
    reference_id = transport.reference_ids.get(identity, "") if supports_ids else ""
    if reference_id:
        transport.reference_ids.move_to_end(identity)
    data = {"text": text, "prompt_text": prompt_text, "speed": speed, "style_instruction": style_instruction}

    def post(with_reference):
        fields = dict(data)
        if with_reference:
            files = {"reference": (reference.name, reference_bytes, "application/octet-stream")}
        else:
            fields["reference_id"] = reference_id
            files = None
        return transport.session.post(base + "/synthesize", data=fields, files=files,
                                      timeout=(15, 600), allow_redirects=False)

    try:
        response = post(with_reference=not bool(reference_id))
        # A 428 is issued only BEFORE inference, when an LRU reference was
        # evicted. Restore its bytes once. Timeouts, 409s and server failures
        # never retry synthesis, because a GPU job may already have started.
        if reference_id and response.status_code == 428:
            try:
                detail = response.json().get("detail", {})
            except (ValueError, AttributeError):
                detail = {}
            if isinstance(detail, dict) and detail.get("code") == "reference_required" and detail.get("synthesis_started") is False:
                transport.reference_ids.pop(identity, None)
                response.close()
                response = post(with_reference=True)
        if not response.ok:
            transport.invalidate()
            try:
                detail = response.json().get("detail", "")
            except ValueError:
                detail = "코랩 실행 상태와 새 연결 주소를 확인해주세요."
            raise RuntimeError(f"{label} 생성 실패 (HTTP {response.status_code}): {str(detail)[:800]}")
    except requests.Timeout as exc:
        transport.invalidate()
        raise RuntimeError("응답 시간이 초과되었습니다. 음성 요청은 자동 반복하지 않았습니다. 코랩 마지막 오류를 확인해주세요.") from exc
    except requests.RequestException as exc:
        transport.invalidate()
        raise RuntimeError("코랩 연결이 끊겼습니다. 코랩 실행 상태와 연결 주소를 확인해주세요.") from exc
    try:
        with wave.open(io.BytesIO(response.content), "rb") as wav:
            if wav.getnframes() <= 0 or wav.getframerate() <= 0:
                raise ValueError("empty WAV")
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 24000:
                raise ValueError("unexpected CosyVoice 2 audio format")
            expected_bytes = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
            if len(wav.readframes(wav.getnframes())) != expected_bytes:
                raise ValueError("truncated WAV")
    except (wave.Error, EOFError, ValueError) as exc:
        transport.invalidate()
        raise RuntimeError("서버가 유효한 WAV 음성을 보내지 않았습니다. 코랩 오류를 확인해주세요.") from exc
    saved_id = response.headers.get("X-Reference-ID", "")
    if supports_ids and len(saved_id) == 71 and saved_id.startswith("studio_") and all(c in "0123456789abcdef" for c in saved_id[7:]):
        transport.reference_ids[identity] = saved_id
        transport.reference_ids.move_to_end(identity)
        while len(transport.reference_ids) > 16:
            transport.reference_ids.popitem(last=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A failed conversion must not replace a previous successful output.
    with tempfile.TemporaryDirectory(prefix="cosy_", dir=target.parent) as folder:
        wav_path = Path(folder) / "speech.wav"
        wav_path.write_bytes(response.content)
        if target.suffix.lower() == ".mp3":
            completed = Path(folder) / "speech.mp3"
            result = subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(wav_path), "-b:a", "192k", str(completed)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode:
                raise RuntimeError("MP3 변환 실패: " + result.stderr[-600:])
        else:
            completed = wav_path
        os.replace(completed, target)
    return str(output_file)
