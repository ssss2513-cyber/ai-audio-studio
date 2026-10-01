"""One selected-model Gemini TTS request, with visible timing and no hidden retries."""
from collections import OrderedDict
import io
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave

_CLIENTS = threading.local()


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


def _request_error(exc):
    code = getattr(exc, "code", None)
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    if code == 429:
        # Do not rotate keys/models to get around a project quota. A delayed
        # duplicate request may consume quota without producing a usable file.
        hint = ""
        match = re.search(r'(?:retryDelay[\"\s:]+|retry in\s+)([0-9.]+)s', str(exc), re.I)
        if match:
            delay = float(match.group(1))
            if math.isfinite(delay) and 0 < delay < 86400:
                hint = f" 서버가 안내한 재시도 대기: 약 {math.ceil(delay)}초."
        return RuntimeError("Gemini 요청 한도 오류(429)로 멈췄습니다." + hint
                            + " 완료된 음성은 유지됩니다. AI Studio에서 해당 프로젝트의 한도를 확인한 뒤 이어서 생성해주세요."
                            + " 같은 프로젝트의 API 키를 추가해도 한도는 늘어나지 않습니다.")
    if code in (401, 403):
        return RuntimeError(f"Gemini 접근 오류({code})입니다. API 키와 선택한 모델의 이용 권한을 확인해주세요.")
    if code in (400, 404):
        return RuntimeError(f"선택한 Gemini 모델의 요청 오류({code})입니다. 모델과 음성 설정을 확인해주세요. 다른 모델로 자동 변경하지 않았습니다.")
    if code and code >= 500:
        return RuntimeError(f"Gemini 서버 오류({code})로 요청이 실패했습니다. 완료 파일은 유지됩니다. 잠시 후 이어서 생성해주세요.")
    return RuntimeError("Gemini 응답을 받지 못했습니다 (" + type(exc).__name__
                        + "). 중복 생성은 하지 않았습니다. 연결 상태를 확인한 뒤 이어서 생성해주세요.")


def synthesize(prompt, output_file, *, api_key, model, voice, metrics=None, progress=None):
    """Preserve model, voice, prompt and native PCM. Reuse one client per worker."""
    from google.genai import types
    started = time.monotonic()
    metrics = metrics if metrics is not None else {}
    metrics.update(engine="gemini", model=model, attempts=0, pacing_seconds=0.0)
    target = Path(output_file).absolute()
    if target.suffix.lower() not in (".wav", ".mp3"):
        raise ValueError("출력 파일은 WAV 또는 MP3여야 합니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    transport = _transport(api_key)
    # Count the previous API call's duration toward the interval; a 5-second
    # successful request no longer incurs another 4.2-second sleep afterwards.
    wait = max(0.0, 4.2 - (time.monotonic() - transport["last_started"]))
    if wait:
        if progress:
            progress("요청 간격 조절", dict(metrics, pacing_seconds=wait))
        pacing_started = time.monotonic()
        time.sleep(wait)
        metrics["pacing_seconds"] = time.monotonic() - pacing_started
    metrics["attempts"] = 1
    if progress:
        progress("Gemini 응답 대기", metrics)
    request_started = time.monotonic()
    transport["last_started"] = request_started
    metrics["attempts"] = 1
    try:
        response = transport["client"].models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
    except Exception as exc:
        raise _request_error(exc) from exc
    finally:
        metrics["request_seconds"] = time.monotonic() - request_started
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
