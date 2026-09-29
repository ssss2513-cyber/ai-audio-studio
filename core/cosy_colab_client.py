"""Client for the self-contained CosyVoice 2 Colab notebook (API v1)."""
import io
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit
import wave

import requests

ENGINES = {
    "cosyvoice": ("ai-voice-studio-cosyvoice", "CosyVoice 2"),
}
GENERATION_CAPABILITY = "validated_generation_v293"



def normalize_url(url):
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("코랩에 표시된 https:// 주소 전체를 입력해주세요.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("코랩의 '프로그램 연결 주소'를 그대로 복사해주세요.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def check_connection(url, engine="cosyvoice"):
    service, label = ENGINES[engine]
    try:
        base = normalize_url(url)
        response = requests.get(base + "/health", timeout=(10, 20), allow_redirects=False)
        response.raise_for_status()
        status = response.json()
        if status.get("service") != service or status.get("api_version") != 1:
            return False, f"{label}의 프로그램 연결 주소가 아닙니다. 코랩에 표시된 엔진 이름을 확인해주세요.", None
        if status.get("ready") is not True:
            return False, "코랩에서 모델을 준비 중입니다. '준비 완료'가 나온 뒤 연결해주세요.", None
        if GENERATION_CAPABILITY not in status.get("capabilities", []):
            return False, (
                "이전 CosyVoice 생성 서버가 실행 중입니다. 사이트의 'CosyVoice 코랩 v2.9.3 바로 열기'로 "
                "수정본을 열고 1번 준비 완료 → 4번 순서로 실행한 뒤 새 연결 주소를 넣어주세요."
            ), status
        return True, f"✅ {label} v{status.get('server_version', '')} 모델 준비 완료 · 프로그램 연결 성공", status
    except (requests.RequestException, ValueError, AttributeError) as exc:
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
    ok, message, status = check_connection(base, engine=engine)
    if not ok:
        raise RuntimeError(message)
    if style_instruction and "style_instruction" not in (status or {}).get("capabilities", []):
        raise RuntimeError("이 코랩은 스타일을 지원하지 않는 이전 버전입니다. 사이트의 CosyVoice 코랩 바로 열기로 최신 코랩을 열고, 런타임을 다시 시작한 뒤 1번과 4번을 실행해주세요. 기존 코랩은 '기본' 스타일로 사용할 수 있습니다.")
    try:
        with reference.open("rb") as audio:
            response = requests.post(
                base + "/synthesize",
                data={"text": text, "prompt_text": prompt_text, "speed": speed, "style_instruction": style_instruction},
                files={"reference": (reference.name, audio, "application/octet-stream")},
                timeout=(15, 600), allow_redirects=False,
            )
        if not response.ok:
            try:
                detail = response.json().get("detail", "")
            except ValueError:
                detail = "코랩 실행 상태와 새 연결 주소를 확인해주세요."
            raise RuntimeError(f"{label} 생성 실패 (HTTP {response.status_code}): {str(detail)[:800]}")
    except requests.Timeout as exc:
        raise RuntimeError("응답 시간이 초과되었습니다. 코랩 마지막 오류를 확인해주세요.") from exc
    except requests.RequestException as exc:
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
        raise RuntimeError("서버가 유효한 WAV 음성을 보내지 않았습니다. 코랩 오류를 확인해주세요.") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    # A failed conversion must not replace a previous successful output.
    with tempfile.TemporaryDirectory(prefix="cosy_", dir=target.parent) as folder:
        wav_path = Path(folder) / "speech.wav"
        wav_path.write_bytes(response.content)
        if target.suffix.lower() == ".mp3":
            completed = Path(folder) / "speech.mp3"
            result = subprocess.run(
                [ffmpeg, "-nostdin", "-y", "-i", str(wav_path), "-b:a", "192k", str(completed)],
                capture_output=True, text=True,
            )
            if result.returncode:
                raise RuntimeError("MP3 변환 실패: " + result.stderr[-600:])
        else:
            completed = wav_path
        os.replace(completed, target)
    return str(output_file)
