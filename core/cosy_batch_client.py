"""Receive numbered Cosy clips while the Colab producer starts the next one."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import time
import uuid

import requests

from .cosy_colab_client import _transport, check_connection, decode_audio_bytes, normalize_url

BATCH_CAPABILITY = 'ordered_batch_stream_v2910'
PARALLEL_CAPABILITY = 'adaptive_cuda_parallel_v2911'
CONTINUOUS_CAPABILITY = 'continuous_queue_v2913'
FIXED_FOUR_CAPABILITY = 'fixed_four_continuous_v2915'
MAX_QUEUE_ITEMS = 4096
MAX_AUDIO_BYTES = 64 * 1024 * 1024


class BatchGenerationError(RuntimeError):
    def __init__(self, index, message):
        self.index = index
        super().__init__(f'대사 {index}번: {message}')


def queue_limits(url):
    """Use the full script queue on updated servers; keep old servers usable."""
    ok, message, status = check_connection(normalize_url(url), use_cached=True)
    if not ok:
        raise RuntimeError(message)
    if CONTINUOUS_CAPABILITY in status.get('capabilities', []):
        return MAX_QUEUE_ITEMS, 64
    return 32, 16


def _read_exact(raw, size):
    data = bytearray()
    while len(data) < size:
        part = raw.read(size - len(data))
        if not part:
            raise RuntimeError('코랩 연속 전송이 끊겼습니다. 완료 파일은 유지되며 자동 중복 생성하지 않습니다.')
        data.extend(part)
    return bytes(data)


def synthesize_batch(entries, *, cancel, on_completed, on_started, on_status=None, on_calculated=None):
    """Return False only before any synthesis, when an older server lacks support."""
    if not entries or len(entries) > MAX_QUEUE_ITEMS:
        raise ValueError(f'연속 생성은 한 대기열에 1~{MAX_QUEUE_ITEMS}개 대사를 사용합니다.')
    base = normalize_url(entries[0]['url'])
    ok, message, status = check_connection(base, use_cached=True)
    if not ok:
        raise RuntimeError(message)
    if BATCH_CAPABILITY not in status.get('capabilities', []):
        return False
    if (PARALLEL_CAPABILITY in status.get('capabilities', [])
            and FIXED_FOUR_CAPABILITY not in status.get('capabilities', [])):
        raise RuntimeError('현재 코랩에는 최대 4개 설정이 아직 적용되지 않았습니다. '
                           '사이트 왼쪽의 CosyVoice v2.9.15 업데이트 코드를 기존 코랩에서 실행하고, '
                           '새 연결 주소를 입력한 뒤 이어서 생성해주세요. 완료 파일은 유지됩니다.')
    continuous = CONTINUOUS_CAPABILITY in status.get('capabilities', [])
    if not continuous and len(entries) > 32:
        raise ValueError('이전 코랩은 32개 묶음까지만 지원합니다. v2.9.13 연속 생성 업데이트를 실행해주세요.')
    if PARALLEL_CAPABILITY not in status.get('capabilities', []) and on_status is not None:
        on_status(dict(enabled=False, limit=1, calibrated=True,
                       reason='현재 코랩은 순차 생성입니다. v2.9.13 연속 생성 업데이트를 실행해주세요.'))
    references, reference_keys, items, by_index = {}, {}, [], {}
    for entry in entries:
        if normalize_url(entry['url']) != base or entry['index'] in by_index:
            raise ValueError('코랩 주소 또는 대사 번호가 올바르지 않습니다.')
        by_index[entry['index']] = entry
        path = Path(entry['ref_path'])
        if path not in reference_keys:
            with path.open('rb') as handle:
                data = handle.read(10 * 1024 * 1024 + 1)
            if not data or len(data) > 10 * 1024 * 1024:
                raise ValueError('참조 음성은 10MB 이하로 등록해주세요.')
            key = hashlib.sha256(data).hexdigest()
            reference_keys[path] = key
            if key not in references:
                references[key] = base64.b64encode(data).decode('ascii')
        key = reference_keys[path]
        items.append(dict(index=entry['index'], text=entry['text'], prompt_text=entry['prompt_text'],
                          speed=entry['speed'], style_instruction=entry['style_instruction'], reference=key))
    if cancel():
        return True
    job_id, seen = uuid.uuid4().hex, set()
    transport = _transport(base)
    response = None
    stop_sent = False
    last_parallel = None
    try:
        response = transport.session.post(base + '/synthesize_batch',
            json=dict(job_id=job_id, items=items, references=references, notify_calculated=True),
            headers={'Accept-Encoding': 'identity'}, stream=True, timeout=(15, 90), allow_redirects=False)
        if not response.ok:
            raise RuntimeError(f'코랩 연속 생성 요청 실패 (HTTP {response.status_code}). 완료 파일은 유지됩니다.')
        if response.headers.get('X-Batch-Protocol') != '1':
            raise RuntimeError('코랩 연속 전송 형식을 확인할 수 없습니다. 자동으로 다시 생성하지 않았습니다.')
        while True:
            if cancel() and not stop_sent:
                stopped = transport.session.post(base + '/batch/' + job_id + '/stop', timeout=(5, 10),
                                                 allow_redirects=False)
                try:
                    stopped.raise_for_status()
                finally:
                    stopped.close()
                stop_sent = True
            size = struct.unpack('!I', _read_exact(response.raw, 4))[0]
            if not 1 <= size <= 16 * 1024:
                raise RuntimeError('코랩 전송 정보의 크기가 올바르지 않습니다.')
            header = json.loads(_read_exact(response.raw, size))
            if not isinstance(header, dict):
                raise RuntimeError('코랩 전송 정보가 올바르지 않습니다.')
            body_size = header.get('size')
            if type(body_size) is not int or not 0 <= body_size <= MAX_AUDIO_BYTES:
                raise RuntimeError('코랩 음성 크기가 올바르지 않습니다.')
            kind, index = header.get('type'), header.get('index')
            parallel = header.get('parallel')
            if isinstance(parallel, dict) and type(parallel.get('limit')) is int and 1 <= parallel['limit'] <= 32:
                if on_status is not None:
                    details = dict({key: parallel[key] for key in ('limit', 'target_limit', 'calibrated', 'memory_retries', 'reason',
                                                                  'selection', 'tuning', 'best_limit', 'measurements')
                                    if key in parallel}, enabled=parallel.get('mode') == 'auto', continuous_queue=continuous)
                    if details != last_parallel:
                        on_status(details)
                        last_parallel = details.copy()
            if kind != 'audio' and body_size:
                raise RuntimeError('코랩 제어 정보에 잘못된 음성 데이터가 있습니다.')
            if kind == 'heartbeat':
                continue
            if kind == 'started':
                if index not in by_index or index in seen:
                    raise RuntimeError('생성 중인 대사 번호가 요청과 다릅니다.')
                on_started(index)
                continue
            if kind == 'calculated':
                if index not in by_index or index in seen:
                    raise RuntimeError('계산 완료된 대사 번호가 요청과 다릅니다.')
                if on_calculated is not None:
                    on_calculated(index)
                continue
            if kind == 'error':
                raise BatchGenerationError(index, str(header.get('message', '코랩 생성 실패'))[:1200])
            if kind == 'end':
                if header.get('done') != len(seen):
                    raise RuntimeError('코랩 저장 완료 개수가 전송된 음성과 다릅니다.')
                if len(seen) != len(entries) and not (stop_sent and header.get('paused')):
                    raise RuntimeError('일부 대사 전송이 누락됐습니다. 완료 파일은 보관됩니다.')
                return True
            if kind != 'audio' or index not in by_index or index in seen or body_size == 0:
                raise RuntimeError('코랩 음성의 대사 번호가 중복되거나 요청과 다릅니다.')
            download_started = time.monotonic()
            body = _read_exact(response.raw, body_size)
            downloaded = time.monotonic() - download_started
            decode_started = time.monotonic()
            wav, duration, wire_format = decode_audio_bytes(body)
            decoded = time.monotonic() - decode_started
            target = Path(by_index[index]['output_file'])
            if target.suffix.lower() != '.wav':
                raise ValueError('연속 생성의 중간 파일은 WAV여야 합니다.')
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name('.' + target.stem + '.' + job_id + '.part.wav')
            saved_at = time.monotonic()
            try:
                temporary.write_bytes(wav)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            saved_seconds = time.monotonic() - saved_at
            headers = requests.structures.CaseInsensitiveDict(header.get('headers', {}))
            server_seconds = float(header.get('server_seconds', 0))
            if not math.isfinite(server_seconds) or server_seconds < 0:
                raise RuntimeError('코랩 생성 시간 정보가 올바르지 않습니다.')
            metrics = dict(engine='cosyvoice', batch_stream=True,
                           total_seconds=server_seconds + downloaded + decoded + saved_seconds,
                           server_seconds=server_seconds, download_seconds=downloaded, decode_seconds=decoded,
                           save_seconds=saved_seconds, audio_seconds=duration, wire_format=wire_format,
                           wire_bytes=len(body), wav_bytes=len(wav),
                           server_version=headers.get('X-CosyVoice-Version', '')[:24],
                           gpu_name=str(status.get('gpu_name', ''))[:80],
                           acceleration=str(status.get('acceleration', {}).get('label', ''))[:200],
                           reference_cached=headers.get('X-Reference-Cache') == 'hit')
            if isinstance(parallel, dict):
                metrics['parallel_limit'] = parallel.get('limit', 1)
                metrics['parallel_calibrated'] = bool(parallel.get('calibrated'))
                metrics['memory_retries'] = header.get('memory_retries', 0)
            for name, key in (('reference_seconds', 'X-Reference-Seconds'), ('synthesis_seconds', 'X-Synthesis-Seconds'),
                              ('postprocess_seconds', 'X-Postprocess-Seconds'), ('llm_seconds', 'X-LLM-Seconds'),
                              ('sampling_seconds', 'X-Sampling-Seconds'), ('retries', 'X-Generation-Retries'),
                              ('retry_seconds', 'X-Retry-Seconds'), ('chunks', 'X-Text-Chunks')):
                try:
                    value = float(headers[key])
                    if math.isfinite(value) and value >= 0:
                        metrics[name] = int(value) if name in ('retries', 'chunks') else value
                except (KeyError, ValueError):
                    pass
            seen.add(index)
            on_completed(index, str(target), metrics)
    except requests.RequestException as exc:
        transport.invalidate()
        raise RuntimeError('코랩 연속 연결이 끊겼습니다. 저장된 대사는 유지되며 요청을 자동 반복하지 않습니다.') from exc
    finally:
        if response is not None:
            response.close()
