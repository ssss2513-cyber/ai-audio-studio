"""Qwen voice-bank jobs on a visitor's own Colab; no local model imports."""
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
from .qwen_bank_connection import CONNECTION_REPORT_VERSION, connection_report, connection_status
from qwen_voicebank_catalog import BASE_MODEL, BANK_REVISION, VOICEBANK

MODEL_ID = BASE_MODEL
SERVICE = "voice-studio-qwen-voicebank"
MAX_WORKERS = 4  # Requests in flight; GPU batch size is reported separately.
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
        raise ValueError("왼쪽 ‘기본 목소리 20종’ 설정에 새 코랩 4번 셀의 프로그램 연결 주소를 넣어주세요.")
    return url


def request_payload(text, config):
    text = str(text).strip()
    if not text or len(text) > 12000:
        raise ValueError("Qwen 한 대사는 비어 있지 않은 12,000자 이하의 텍스트여야 합니다.")
    if config.voice not in VOICEBANK:
        raise ValueError("기본 목소리 20종 목록에서 다시 선택해주세요.")
    return {"text": text, "voice": config.voice, "instruct": ""}


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


def prepare_voices(value, voices, *, progress=None, cancel=None):
    """Prepare the entire selected cast once before dispatching its dialogue."""
    voices = sorted(set(voices))
    if not voices or any(voice not in VOICEBANK for voice in voices):
        raise ValueError("기본 목소리 20종 목록에서 목소리를 선택해주세요.")
    if cancel and cancel():
        raise CancelledError()
    base = api_base(value)
    identity = 'prepare:' + BANK_REVISION + json.dumps(voices)
    job_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
    session = _session()
    job_url = base + '/jobs/' + job_id
    try:
        try:
            status = _json_response(session.post(base + '/prepare/' + job_id,
                json={'voices': voices}, timeout=(10, 30), allow_redirects=False))
        except requests.RequestException:
            status = _json_response(session.get(job_url, timeout=(10, 30), allow_redirects=False))
        started, last = time.monotonic(), None
        while status.get('status') != 'done':
            state = status.get('status')
            if state == 'cancelled':
                raise CancelledError()
            if state == 'error':
                raise RuntimeError('기본 목소리 준비가 중단되었습니다. 코랩 5번 셀의 오류 로그를 확인해주세요. 이미 준비한 목소리는 보관합니다.')
            if state not in ('queued', 'generating'):
                raise RuntimeError('목소리 준비 상태를 확인할 수 없습니다.')
            if cancel and cancel():
                try:
                    session.post(job_url + '/cancel', timeout=(10, 20), allow_redirects=False).close()
                except requests.RequestException:
                    pass
                raise CancelledError()
            phase = status.get('phase', '기본 목소리 준비 중')
            detail = (phase, status.get('chunks_done', 0), len(voices))
            if progress and detail != last:
                progress(f'{phase} · {detail[1]}/{detail[2]}',
                         dict(engine='qwen-bank', preparing=True, voices_done=detail[1], voices_total=detail[2]))
                last = detail
            if time.monotonic() - started > 7200:
                raise RuntimeError('목소리 준비 응답이 2시간을 넘었습니다. 코랩 5번 셀의 로그를 확인해주세요. 자동으로 재생성하지 않았습니다.')
            time.sleep(1)
            status = _json_response(session.get(job_url, timeout=(10, 30), allow_redirects=False))
    except requests.RequestException:
        raise RuntimeError('목소리 준비 중 코랩 연결이 끊겼습니다. 같은 코랩으로 이어서 생성하면 완료한 목소리를 재사용합니다.') from None


def synthesize(text, output_file, config, *, metrics=None, progress=None, cancel=None, prepared=False):
    base = api_base(config.qwen_url)
    payload = request_payload(text, config)
    if not prepared:
        prepare_voices(config.qwen_url, [config.voice], progress=progress, cancel=cancel)
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
    marker = target.parent / ('.' + stable_stem + '.qwen-bank-job.json')
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
                progress("Qwen 생성 대기" if state == "queued" else "Qwen Base 음성 생성",
                         dict(engine="qwen-bank", chunks_done=detail[1], chunks=detail[2], batch_size=detail[3]))
                last_phase = detail
            if time.monotonic() - started > 1800:
                raise RuntimeError("Qwen 작업 응답이 30분 동안 완료되지 않았습니다. 코랩 로그를 확인해주세요. 자동 재생성하지 않았습니다.")
            time.sleep(1.0)
            status = _json_response(session.get(job_url, timeout=(10, 30), allow_redirects=False))
        if progress:
            progress("Qwen 음성 수신·저장", dict(engine="qwen-bank"))
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
            metrics.update(engine="qwen-bank", model=MODEL_ID, audio_seconds=duration,
                           synthesis_seconds=status.get("synthesis_seconds", 0),
                           download_seconds=time.monotonic() - download_started,
                           batch_size=status.get("batch_size", 1), chunks=status.get("chunks", 1),
                           total_seconds=time.monotonic() - started)
        marker.unlink(missing_ok=True)
        return str(target)
    except requests.RequestException:
        raise RuntimeError("Qwen 코랩 연결이 끊겼습니다. 완료된 대사는 유지합니다. 코랩 상태를 확인한 뒤 이어서 생성해주세요.") from None


def export_plan(segments, settings, pause_ms, include_speaker):
    """Credential-free plan with preset IDs, requiring no reference upload."""
    from .tts_engine import VoiceConfig, clean_spoken_text
    items = []
    for segment in segments:
        row = settings.get(segment.speaker, {})
        if row.get("engine") != "qwen-bank":
            raise ValueError("모든 화자를 Qwen 기본 목소리 20종으로 바꾸면 받을 수 있습니다.")
        config = VoiceConfig(engine="qwen-bank", voice=row.get("voice", "F01"))
        items.append(dict(request_payload(clean_spoken_text(segment.text), config),
                          index=segment.index, speaker=segment.speaker))
    return json.dumps(dict(format="voice-studio-qwen-plan-v1", model=MODEL_ID,
                           bank_revision=BANK_REVISION, items=items,
                           pause_ms=pause_ms, include_speaker=include_speaker), ensure_ascii=False, indent=2).encode("utf-8")
