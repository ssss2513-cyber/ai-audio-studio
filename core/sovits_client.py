"""GPT-SoVITS client: preserve reference/transcript alignment and never retry synthesis."""
import base64
import hashlib
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

SERVICE = "ai-voice-studio-gpt-sovits"


def api_base(url):
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("GPT-SoVITS 연결 주소 전체를 입력해주세요.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("코랩에 표시된 연결 주소를 그대로 복사해주세요.")
    path = parts.path.rstrip("/")
    if path.endswith("/tts"):
        path = path[:-4]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def check_connection(url):
    try:
        base = api_base(url)
        response = requests.get(base + "/health", timeout=(5, 15), allow_redirects=False)
        if response.status_code == 200:
            status = response.json()
            if status.get("service") != SERVICE or status.get("ready") is not True:
                return False, "GPT-SoVITS 모델이 준비된 주소가 아닙니다."
            return True, f"GPT-SoVITS {status.get('model_version', '?')} · {status.get('sample_rate', '?')} Hz · 연결 완료"
        if response.status_code != 404:
            response.raise_for_status()
        # Support the official local api_v2.py, which has no /health endpoint.
        response = requests.get(base + "/openapi.json", timeout=(5, 15), allow_redirects=False)
        response.raise_for_status()
        schema = response.json()
        fields = schema.get("components", {}).get("schemas", {}).get("TTS_Request", {}).get("properties", {})
        if "/tts" not in schema.get("paths", {}) or not {"text", "ref_audio_path"} <= fields.keys():
            return False, "GPT-SoVITS API 주소가 아닙니다."
        return True, "GPT-SoVITS API 연결 완료 (모델 버전 미확인). v4는 수정 코랩에서 실행하세요."
    except (requests.RequestException, ValueError, AttributeError, TypeError):
        return False, "연결 실패. 코랩의 'v4 준비 완료'를 확인하고 새 연결 주소를 입력해주세요."


def prepare_reference(source, target, ffmpeg):
    """Decode without normalization, denoising or accepted truncation."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError("참조 오디오 파일을 다시 등록해주세요.")
    if source.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("참조 음성은 3~10초, 10MB 이하로 준비해주세요.")
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source),
         "-t", "10.1", "-vn", "-ac", "1", "-c:a", "pcm_s16le", str(target)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise ValueError("참조 음성을 읽지 못했습니다. WAV 또는 MP3 파일을 확인해주세요.")
    with wave.open(str(target), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
        frames = audio.readframes(audio.getnframes())
    if not 3.0 <= duration <= 10.0:
        raise ValueError(
            "참조 음성은 3~10초여야 합니다. 자동으로 자르면 대사와 어긋나므로 생성하지 않았습니다. "
            "문장이 끝나는 지점에서 직접 자르고, 그 구간에서 말한 대사를 입력해주세요."
        )
    import numpy as np
    values = np.frombuffer(frames, dtype="<i2").astype(float) / 32768.0
    if not len(values) or not np.isfinite(values).all() or np.max(np.abs(values)) < 0.001:
        raise ValueError("참조 음성이 무음이거나 너무 작습니다. 또렷하게 들리는 녹음을 사용해주세요.")
    return Path(target).read_bytes()


def request_payload(text, cfg):
    prompt = (getattr(cfg, "prompt_text", "") or "").strip()
    if not text.strip():
        raise ValueError("생성할 대사를 입력해주세요.")
    if not prompt:
        raise ValueError("'참조 오디오 실제 대사'에 녹음에서 말한 문장을 정확히 입력해주세요.")
    def number(name, default, minimum, maximum):
        value = float(getattr(cfg, name, default))
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{name} 설정은 {minimum}~{maximum} 범위여야 합니다.")
        return value
    langs = {"auto", "auto_yue", "ko", "en", "zh", "ja", "yue", "all_ko", "all_zh", "all_ja", "all_yue"}
    def language(name):
        value = getattr(cfg, name, "ko")
        value = "en" if value == "all_en" else value
        if value not in langs:
            raise ValueError("지원되지 않는 음성 언어입니다.")
        return value
    steps = int(getattr(cfg, "sample_steps", 32))
    if steps not in (4, 8, 16, 32):
        raise ValueError("v4 생성 단계는 4, 8, 16, 32 중에서 선택해주세요.")
    split = getattr(cfg, "text_split_method", "cut5")
    if split not in {f"cut{i}" for i in range(6)}:
        raise ValueError("지원되지 않는 문장 분할 방식입니다.")
    return {
        "text": text.strip(), "text_lang": language("text_lang"),
        "prompt_text": prompt, "prompt_lang": language("prompt_lang"),
        "speed_factor": number("speed_factor", 1.0, 0.5, 2.0),
        "temperature": number("temperature", 1.0, 0.1, 1.0),
        "top_k": int(number("top_k", 15, 1, 100)), "top_p": number("top_p", 1.0, 0.05, 1.0),
        "sample_steps": steps, "text_split_method": split,
        "fragment_interval": number("fragment_interval", 0.3, 0.01, 1.0),
        "repetition_penalty": 1.35, "batch_size": 1, "parallel_infer": False,
        "split_bucket": False, "seed": -1, "media_type": "wav",
        "streaming_mode": False, "super_sampling": False,
    }


def synthesize(text, output_file, cfg):
    base = api_base(getattr(cfg, "gpt_sovits_url", ""))
    payload = request_payload(text, cfg)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("참조 음성 확인 및 저장에 필요한 FFmpeg를 설치해주세요.")
    target = Path(output_file).absolute()
    if target.suffix.lower() not in (".mp3", ".wav"):
        raise ValueError("출력 파일은 WAV 또는 MP3여야 합니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sovits_", dir=target.parent) as folder:
        folder = Path(folder)
        normalized = prepare_reference(getattr(cfg, "ref_audio_path", ""), folder / "reference.wav", ffmpeg)
        if urlsplit(base).hostname in ("127.0.0.1", "localhost", "::1") and "/v1/" not in urlsplit(base).path:
            payload["ref_audio_path"] = str(folder / "reference.wav")
        else:
            payload["ref_audio_path"] = "ref_" + hashlib.sha256(normalized).hexdigest() + ".wav"
            payload["ref_audio_base64"] = base64.b64encode(normalized).decode("ascii")
        try:
            # Exactly one generation request. A timeout must not start a duplicate GPU job.
            response = requests.post(base + "/tts", json=payload, timeout=(15, 600), allow_redirects=False)
        except requests.Timeout as exc:
            raise RuntimeError("생성 응답 시간이 초과됐습니다. 자동 재시도하지 않았습니다. 코랩 로그를 확인해주세요.") from exc
        except requests.RequestException as exc:
            raise RuntimeError("GPT-SoVITS 연결이 끊겼습니다. 코랩 실행 상태와 주소를 확인해주세요.") from exc
        if not response.ok:
            try:
                error = response.json()
                detail = error.get("detail") or error.get("Exception") or error.get("message") or str(error)
            except ValueError:
                detail = "코랩 마지막 오류와 연결 주소를 확인해주세요."
            raise RuntimeError(f"GPT-SoVITS 생성 실패 (HTTP {response.status_code}): {str(detail)[:800]}")
        try:
            with wave.open(io.BytesIO(response.content), "rb") as audio:
                if not audio.getnframes() or audio.getframerate() <= 0:
                    raise ValueError("empty audio")
        except (wave.Error, EOFError, ValueError) as exc:
            raise RuntimeError("서버가 정상적인 WAV 음성을 보내지 않았습니다.") from exc
        wav_path = folder / "speech.wav"
        wav_path.write_bytes(response.content)
        completed = wav_path
        if target.suffix.lower() == ".mp3":
            completed = folder / "speech.mp3"
            result = subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(wav_path),
                 "-c:a", "libmp3lame", "-b:a", "192k", str(completed)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode:
                raise RuntimeError("MP3 저장에 실패했습니다: " + result.stderr[-400:])
        os.replace(completed, target)
    return str(output_file)
