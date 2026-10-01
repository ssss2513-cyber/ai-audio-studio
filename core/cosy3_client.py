"""Isolated CosyVoice 3 jobs with stable IDs and a directly selectable voice bank."""
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
from cosy3_voicebank_catalog import MODEL_ID, SERVICE, PROTOCOL, BANK_REVISION, VOICEBANK, ATTRIBUTION

MAX_WORKERS = 4  # HTTP requests queued; server reports actual GPU concurrency.
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
    if hasattr(_LOCAL, 'references'):
        del _LOCAL.references


def api_base(value):
    url = connection_url(value, "cosyvoice3")
    if not url:
        raise ValueError("왼쪽 ‘CosyVoice 3 목소리’ 설정에 새 코랩 4번 셀의 프로그램 연결 주소를 넣어주세요.")
    return url


def request_payload(text, config):
    from .tts_engine import VOICE_STYLES
    text = str(text).strip()
    if not text or len(text) > 12000:
        raise ValueError('한 대사는 비어 있지 않은 12,000자 이하의 문장이어야 합니다.')
    if config.voice not in VOICEBANK and config.voice != 'custom':
        raise ValueError('CosyVoice 3 목소리를 다시 선택해주세요.')
    payload = dict(text=text, voice=config.voice, speed=float(config.speed_factor),
        style=VOICE_STYLES.get(config.style, VOICE_STYLES['🎤 기본'])['gemini_prompt']
              if config.style != '🎤 기본' else '')
    if config.voice == 'custom':
        if not config.prompt_text.strip() or not Path(config.ref_audio_path).is_file():
            raise ValueError('한국어 참고 음성과 그 녹음에서 실제로 말한 대사를 입력해주세요.')
        data = Path(config.ref_audio_path).read_bytes()
        if not 0 < len(data) <= 10 * 1024 * 1024:
            raise ValueError('참고 음성은 10MB 이하 WAV 또는 FLAC으로 등록해주세요.')
        payload.update(reference_id=hashlib.sha256(data).hexdigest(), prompt_text=config.prompt_text.strip())
    return payload


def check_connection(value):
    try:
        base = api_base(value)
        info = _json_response(_session().get(base + '/health', timeout=(10, 20), allow_redirects=False))
        if info.get('service') != SERVICE:
            return False, 'CosyVoice 3 전용 주소가 아닙니다. 버전 2와 3의 주소를 각각 입력해주세요.'
        if (info.get('model') != MODEL_ID or info.get('protocol') != PROTOCOL
                or info.get('bank_revision') != BANK_REVISION):
            return False, 'CosyVoice 3 모델·목소리 목록 버전이 다릅니다. 새 전용 코랩을 열어주세요.'
        if not info.get('ready') or not set(VOICEBANK).issubset(info.get('voices', {})):
            return False, 'CosyVoice 3 모델 또는 목소리 목록 준비가 끝나지 않았습니다.'
        return True, f"CosyVoice 3 v{info.get('version')} 연결 완료 · 남성 10 · 여성 10 · {info.get('gpu', '')}"
    except (ValueError, RuntimeError, requests.RequestException):
        return False, 'CosyVoice 3에 연결하지 못했습니다. 전용 코랩 2번 완료 후 4번의 전체 주소를 넣어주세요.'


def _json_response(response):
    with response:
        if response.status_code == 404:
            raise RuntimeError("CosyVoice 3 코랩 주소 또는 작업을 찾지 못했습니다. 코랩 4번 셀에서 나온 주소를 확인해주세요.")
        if not response.ok:
            try:
                detail = str(response.json().get('detail', ''))[:300]
            except (ValueError, AttributeError):
                detail = ''
            raise RuntimeError(f"CosyVoice 3 코랩 요청 실패 (HTTP {response.status_code}). "
                               + (detail or '코랩 5번 오류 로그를 확인해주세요.'))
        if len(response.content) > 128 * 1024:
            raise RuntimeError("CosyVoice 3 서버의 상태 응답이 너무 큽니다.")
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError("CosyVoice 3 코랩이 정상적인 상태 정보를 보내지 않았습니다.") from None
        if not isinstance(data, dict):
            raise RuntimeError("CosyVoice 3 코랩 상태 형식을 확인할 수 없습니다.")
        return data


def synthesize(text, output_file, config, *, metrics=None, progress=None, cancel=None):
    base = api_base(config.cosyvoice3_url)
    payload = request_payload(text, config)
    target = Path(output_file)
    if target.suffix.lower() != ".wav":
        raise ValueError("CosyVoice 3 중간 음성은 WAV 파일로 저장해야 합니다.")
    if cancel and cancel():
        raise CancelledError()
    target.parent.mkdir(parents=True, exist_ok=True)
    # A stable marker beside the requested output lets an interrupted download
    # retrieve the same GPU job instead of silently synthesizing it a second time.
    identity = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    endpoint_id = hashlib.sha256(base.encode()).hexdigest()
    stable_stem = re.sub(r'\.[a-f0-9]{32}\.part$', '', target.stem).lstrip('.')
    marker = target.parent / ('.' + stable_stem + '.cosyvoice3-job.json')
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
        if payload.get('reference_id'):
            if not hasattr(_LOCAL, 'references'):
                _LOCAL.references = set()
            reference_key = (base, payload['reference_id'])
            if reference_key not in _LOCAL.references:
                with open(config.ref_audio_path, 'rb') as source:
                    uploaded = _json_response(session.post(base + '/references/' + payload['reference_id'],
                        data=source, timeout=(10, 90), allow_redirects=False))
                if uploaded.get('reference_id') != payload['reference_id']:
                    raise RuntimeError('참고 음성 등록 결과가 다릅니다.')
                _LOCAL.references.add(reference_key)
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
                           "interrupted": "CosyVoice 3 서버 재시작으로 중단된 대사입니다. 이어서 생성을 다시 눌러주세요.",
                           "invalid_audio": "정상적인 음성이 나오지 않아 저장하지 않았습니다. 대사와 스타일을 확인해주세요."}.get(
                               code, "CosyVoice 3 음성 생성에 실패했습니다. 코랩 5번 셀의 오류 로그를 확인해주세요.")
                detail = str(status.get('message', ''))[:300]
                raise RuntimeError(message + ('\n' + detail if detail else ''))
            if state not in ("queued", "generating"):
                raise RuntimeError("CosyVoice 3 서버가 알 수 없는 작업 상태를 보냈습니다.")
            if cancel and cancel() and not cancel_sent:
                status = _json_response(session.post(job_url + "/cancel", timeout=(10, 20), allow_redirects=False))
                cancel_sent = True
                continue
            detail = (state, status.get("chunks_done", 0), status.get("chunks", 1), status.get("gpu_concurrency", 1))
            if progress and detail != last_phase:
                progress("CosyVoice 3 생성 대기" if state == "queued" else "CosyVoice 3 음성 생성",
                         dict(engine="cosyvoice3", chunks_done=detail[1], chunks=detail[2], gpu_concurrency=detail[3]))
                last_phase = detail
            if time.monotonic() - started > 1800:
                raise RuntimeError("CosyVoice 3 작업 응답이 30분 동안 완료되지 않았습니다. 코랩 로그를 확인해주세요. 자동 재생성하지 않았습니다.")
            time.sleep(1.0)
            status = _json_response(session.get(job_url, timeout=(10, 30), allow_redirects=False))
        if progress:
            progress("CosyVoice 3 음성 수신·저장", dict(engine="cosyvoice3"))
        download_started = time.monotonic()
        with session.get(job_url + "/audio", stream=True, timeout=(10, 90), allow_redirects=False) as response:
            if not response.ok:
                raise RuntimeError("CosyVoice 3 음성 파일을 받지 못했습니다. 같은 작업의 완료 음성을 보관했습니다.")
            audio = bytearray()
            for chunk in response.iter_content(128 * 1024):
                audio.extend(chunk)
                if len(audio) > 128 * 1024 * 1024:
                    raise RuntimeError("CosyVoice 3 한 대사의 음성 파일이 너무 큽니다. 대사를 나누어주세요.")
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
            raise RuntimeError("CosyVoice 3에서 받은 WAV가 완전하지 않습니다. 자동 재생성하지 않았습니다.") from None
        target.write_bytes(audio)
        if metrics is not None:
            metrics.update(engine="cosyvoice3", model=MODEL_ID, audio_seconds=duration,
                           synthesis_seconds=status.get("synthesis_seconds", 0),
                           download_seconds=time.monotonic() - download_started,
                           gpu_concurrency=status.get("gpu_concurrency", 1), chunks=status.get("chunks", 1),
                           total_seconds=time.monotonic() - started)
        marker.unlink(missing_ok=True)
        return str(target)
    except requests.RequestException:
        raise RuntimeError("CosyVoice 3 코랩 연결이 끊겼습니다. 완료된 대사는 유지합니다. 코랩 상태를 확인한 뒤 이어서 생성해주세요.") from None


def export_plan(segments, settings, pause_ms):
    """No keys or server URLs in the downloadable Colab project."""
    import base64
    import zipfile
    from .tts_engine import VoiceConfig, clean_spoken_text
    items, references = [], {}
    reference_size = 0
    for segment in segments:
        row = settings.get(segment.speaker, {})
        if row.get('engine') != 'cosyvoice3':
            raise ValueError('전체 화자가 CosyVoice 3일 때 코랩 대본을 받을 수 있습니다.')
        config = VoiceConfig(engine='cosyvoice3', voice=row.get('voice', 'F01'),
            style=row.get('style', '🎤 기본'), speed_factor=float(row.get('speed', 1)),
            ref_audio_path=row.get('ref_audio_path', ''), prompt_text=row.get('prompt_text', ''))
        payload = request_payload(clean_spoken_text(segment.text), config)
        ref_id = payload.get('reference_id')
        if ref_id and ref_id not in references:
            data = Path(config.ref_audio_path).read_bytes()
            reference_size += len(data)
            if reference_size > 32 * 1024 * 1024:
                raise ValueError('참고 음성 합계가 32MB를 넘습니다. 녹음 길이를 줄여주세요.')
            references[ref_id] = base64.b64encode(data).decode()
        items.append(dict(payload, index=segment.index, speaker=segment.speaker))
    plan = dict(format='voice-studio-cosy3-plan-v1', bank_revision=BANK_REVISION,
                model=MODEL_ID, items=items, references=references, pause_ms=pause_ms)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('plan.json', json.dumps(plan, ensure_ascii=False, indent=2))
        archive.writestr('VOICE_ATTRIBUTION.txt', ATTRIBUTION)
    return out.getvalue()
