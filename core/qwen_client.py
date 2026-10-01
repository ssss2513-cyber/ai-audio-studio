"""Qwen CustomVoice jobs on a visitor's own Colab; no local model imports."""
from concurrent.futures import CancelledError
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import time
import uuid
import wave

import requests

from .personal_colab import connection_url

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
SERVICE = "voice-studio-qwen-customvoice"
MAX_WORKERS = 4  # Requests in flight; GPU batch size is reported separately.
QWEN_VOICES = {
    "Sohee": {"gender": "여성", "name": "소희 · 따뜻한 한국어 여성", "native": "한국어"},
    "Serena": {"gender": "여성", "name": "Serena · 부드러운 여성", "native": "중국어"},
    "Vivian": {"gender": "여성", "name": "Vivian · 밝은 여성", "native": "중국어"},
    "Ono_Anna": {"gender": "여성", "name": "Ono Anna · 발랄한 여성", "native": "일본어"},
    "Uncle_Fu": {"gender": "남성", "name": "Uncle Fu · 중후하고 낮은 남성", "native": "중국어"},
    "Ryan": {"gender": "남성", "name": "Ryan · 힘 있는 남성", "native": "영어"},
    "Aiden": {"gender": "남성", "name": "Aiden · 밝고 또렷한 남성", "native": "영어"},
    "Dylan": {"gender": "남성", "name": "Dylan · 젊고 또렷한 남성", "native": "중국어"},
    "Eric": {"gender": "남성", "name": "Eric · 활기찬 남성", "native": "중국어"},
}
_LOCAL = threading.local()


def _session():
    if not hasattr(_LOCAL, "session"):
        _LOCAL.session = requests.Session()
    return _LOCAL.session


def close_worker_client():
    session = getattr(_LOCAL, "session", None)
    if session is not None:
        session.close()
        del _LOCAL.session


def api_base(value):
    url = connection_url(value, "qwen")
    if not url:
        raise ValueError("왼쪽 Qwen 설정에 코랩 4번 셀의 프로그램 연결 주소를 넣어주세요.")
    return url


def build_instruction(style, custom, voice):
    from .tts_engine import VOICE_STYLES
    from .voice_recommendations import resolve_style
    if voice not in QWEN_VOICES:
        raise ValueError("Qwen 목소리를 다시 선택해주세요.")
    custom = str(custom or "").strip()
    if len(custom) > 1200:
        raise ValueError("직접 입력하는 Qwen 스타일은 1,200자 이하로 적어주세요.")
    direction = VOICE_STYLES[resolve_style(style, VOICE_STYLES)].get("gemini_prompt", "")
    gender = "male" if QWEN_VOICES[voice]["gender"] == "남성" else "female"
    return (f"Speak the supplied Korean text clearly. {direction} "
            + (f"Additional delivery direction: {custom}. " if custom else "")
            + f"Keep the selected {gender} speaker's voice identity and gender. "
              "Perform the delivery instructions; do not read the instructions aloud.")


def request_payload(text, config):
    text = str(text).strip()
    if not text or len(text) > 12000:
        raise ValueError("Qwen 한 대사는 비어 있지 않은 12,000자 이하의 텍스트여야 합니다.")
    return {"text": text, "voice": config.voice,
            "instruct": build_instruction(config.style, getattr(config, "qwen_instruction", ""), config.voice)}


def _json_response(response):
    with response:
        if response.status_code == 404:
            raise RuntimeError("Qwen 코랩 주소 또는 작업을 찾지 못했습니다. 코랩 4번 셀에서 나온 주소를 확인해주세요.")
        if not response.ok:
            raise RuntimeError(f"Qwen 코랩 요청 실패 (HTTP {response.status_code}). 코랩 화면의 상태를 확인해주세요.")
        if len(response.content) > 128 * 1024:
            raise RuntimeError("Qwen 서버의 상태 응답이 너무 큽니다.")
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError("Qwen 코랩이 정상적인 상태 정보를 보내지 않았습니다.") from None
        if not isinstance(data, dict):
            raise RuntimeError("Qwen 코랩 상태 형식을 확인할 수 없습니다.")
        return data


def connection_status(value):
    try:
        with requests.Session() as session:
            data = _json_response(session.get(api_base(value) + "/health", timeout=(10, 20), allow_redirects=False))
        if data.get("service") != SERVICE or data.get("model") != MODEL_ID or data.get("protocol") != 1:
            return False, "Qwen 전용 코랩 v1.0.0 주소가 아닙니다. Cosy/GPT 주소와 구분해주세요."
        if not data.get("ready"):
            return False, "Qwen 모델을 준비 중입니다. 코랩 2번 셀에서 준비 완료를 기다려주세요."
        return True, (f"Qwen 1.7B 준비 완료 · {data.get('gpu', 'GPU')} · "
                      f"GPU 배치 최대 {data.get('batch_size', 1)}개 · 스타일 지시 지원")
    except (ValueError, RuntimeError) as exc:
        return False, str(exc)
    except requests.RequestException:
        return False, "Qwen 코랩에 연결하지 못했습니다. 코랩 실행 상태와 최신 주소를 확인해주세요."


def synthesize(text, output_file, config, *, metrics=None, progress=None, cancel=None):
    base = api_base(config.qwen_url)
    payload = request_payload(text, config)
    target = Path(output_file)
    if target.suffix.lower() != ".wav":
        raise ValueError("Qwen 중간 음성은 WAV 파일로 저장해야 합니다.")
    if cancel and cancel():
        raise CancelledError()
    target.parent.mkdir(parents=True, exist_ok=True)
    # A stable marker beside the requested output lets an interrupted download
    # retrieve the same GPU job instead of silently synthesizing it a second time.
    identity = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    endpoint_id = hashlib.sha256(base.encode()).hexdigest()
    stable_stem = re.sub(r'\.[a-f0-9]{32}\.part$', '', target.stem).lstrip('.')
    marker = target.parent / ('.' + stable_stem + '.qwen-job.json')
    job_id = uuid.uuid4().hex
    try:
        old = json.loads(marker.read_text(encoding="utf-8"))
        if isinstance(old, dict) and old.get("identity") == identity and old.get("endpoint") == endpoint_id:
            candidate = old.get("job_id", "")
            if len(candidate) == 32 and all(c in "0123456789abcdef" for c in candidate):
                job_id = candidate
    except (OSError, ValueError, TypeError):
        pass
    marker.write_text(json.dumps(dict(identity=identity, endpoint=endpoint_id, job_id=job_id)), encoding="utf-8")
    marker.chmod(0o600)
    session = _session()
    job_url = base + "/jobs/" + job_id
    started = time.monotonic()
    cancel_sent = False
    last_phase = None
    try:
        try:
            status = _json_response(session.post(job_url, json=payload, timeout=(10, 30), allow_redirects=False))
        except requests.RequestException:
            # The POST could already be accepted. Only query this exact ID.
            status = _json_response(session.get(job_url, timeout=(10, 30), allow_redirects=False))
        while status.get("status") != "done":
            state = status.get("status")
            if state == "cancelled":
                marker.unlink(missing_ok=True)
                raise CancelledError()
            if state == "error":
                marker.unlink(missing_ok=True)
                code = status.get("error_code")
                message = {"memory": "GPU 메모리가 부족합니다. 다른 코랩 모델을 종료하고 다시 생성해주세요.",
                           "invalid_audio": "정상적인 음성이 나오지 않아 저장하지 않았습니다. 대사와 스타일을 확인해주세요."}.get(
                               code, "Qwen 음성 생성에 실패했습니다. 코랩 5번 셀의 오류 로그를 확인해주세요.")
                raise RuntimeError(message)
            if state not in ("queued", "generating"):
                raise RuntimeError("Qwen 서버가 알 수 없는 작업 상태를 보냈습니다.")
            if cancel and cancel() and not cancel_sent:
                status = _json_response(session.post(job_url + "/cancel", timeout=(10, 20), allow_redirects=False))
                cancel_sent = True
                continue
            detail = (state, status.get("chunks_done", 0), status.get("chunks", 1), status.get("batch_size", 1))
            if progress and detail != last_phase:
                progress("Qwen 생성 대기" if state == "queued" else "Qwen GPU 음성 생성",
                         dict(engine="qwen", chunks_done=detail[1], chunks=detail[2], batch_size=detail[3]))
                last_phase = detail
            if time.monotonic() - started > 1800:
                raise RuntimeError("Qwen 작업 응답이 30분 동안 완료되지 않았습니다. 코랩 로그를 확인해주세요. 자동 재생성하지 않았습니다.")
            time.sleep(1.0)
            status = _json_response(session.get(job_url, timeout=(10, 30), allow_redirects=False))
        if progress:
            progress("Qwen 음성 수신·저장", dict(engine="qwen"))
        download_started = time.monotonic()
        with session.get(job_url + "/audio", stream=True, timeout=(10, 90), allow_redirects=False) as response:
            if not response.ok:
                raise RuntimeError("Qwen 음성 파일을 받지 못했습니다. 같은 작업의 완료 음성을 보관했습니다.")
            audio = bytearray()
            for chunk in response.iter_content(128 * 1024):
                audio.extend(chunk)
                if len(audio) > 128 * 1024 * 1024:
                    raise RuntimeError("Qwen 한 대사의 음성 파일이 너무 큽니다. 대사를 나누어주세요.")
        try:
            with wave.open(io.BytesIO(audio), "rb") as wav:
                frames, rate = wav.getnframes(), wav.getframerate()
                if frames <= 0 or rate <= 0:
                    raise ValueError()
                wav.setpos(frames - 1)
                if len(wav.readframes(1)) != wav.getsampwidth() * wav.getnchannels():
                    raise ValueError()
                duration = frames / rate
        except (wave.Error, EOFError, ValueError):
            raise RuntimeError("Qwen에서 받은 WAV가 완전하지 않습니다. 자동 재생성하지 않았습니다.") from None
        target.write_bytes(audio)
        if metrics is not None:
            metrics.update(engine="qwen", model=MODEL_ID, audio_seconds=duration,
                           synthesis_seconds=status.get("synthesis_seconds", 0),
                           download_seconds=time.monotonic() - download_started,
                           batch_size=status.get("batch_size", 1), chunks=status.get("chunks", 1),
                           total_seconds=time.monotonic() - started)
        marker.unlink(missing_ok=True)
        return str(target)
    except requests.RequestException:
        raise RuntimeError("Qwen 코랩 연결이 끊겼습니다. 완료된 대사는 유지합니다. 코랩 상태를 확인한 뒤 이어서 생성해주세요.") from None


def export_plan(segments, settings, pause_ms, include_speaker):
    """Portable, credential-free plan for notebook-only generation."""
    from .tts_engine import VoiceConfig, clean_spoken_text
    items = []
    for segment in segments:
        row = settings.get(segment.speaker, {})
        if row.get("engine") != "qwen":
            raise ValueError("코랩 직접 생성 파일은 선택한 모든 화자를 Qwen으로 바꾼 뒤 받을 수 있습니다.")
        config = VoiceConfig(engine="qwen", voice=row.get("voice", "Sohee"),
                             style=row.get("style", "🎤 기본"), qwen_instruction=row.get("qwen_instruction", ""))
        items.append(dict(request_payload(clean_spoken_text(segment.text), config),
                          index=segment.index, speaker=segment.speaker))
    return json.dumps(dict(format="voice-studio-qwen-plan-v1", model=MODEL_ID, items=items,
                           pause_ms=pause_ms, include_speaker=include_speaker), ensure_ascii=False, indent=2).encode("utf-8")
