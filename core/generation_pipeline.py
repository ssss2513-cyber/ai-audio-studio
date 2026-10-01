"""Independent engine queues, immediate slot refill, and ordered checkpoints."""
from concurrent.futures import CancelledError
from pathlib import Path
import os
import queue
import threading
import time

from .tts_engine import TTSEngine


def _target(item):
    target = Path(item.file_path)
    return target.with_suffix('.wav') if item.config.engine in ('cosyvoice', 'gemini', 'chirp', 'qwen') else target


def _generate_one(item, job_id, stopped, emit):
    from .generation_jobs import valid_audio
    if stopped():
        raise CancelledError()
    emit('started', item, {})
    target = _target(item)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.' + target.stem + '.' + job_id + '.part' + target.suffix)
    started = time.monotonic()
    metrics = {'engine': item.config.engine}
    try:
        if item.config.engine in ('gemini', 'chirp', 'qwen'):
            def progress(phase, details):
                emit('phase', item, dict(details, phase=phase))
            generate = {'chirp': TTSEngine.generate_chirp_speech,
                        'qwen': TTSEngine.generate_qwen_speech,
                        'gemini': TTSEngine.generate_gemini_speech}[item.config.engine]
            generate(item.text, str(temporary), item.config,
                     metrics=metrics, progress=progress, cancel=stopped)
        elif item.config.engine == 'cosyvoice':
            TTSEngine.generate_cosyvoice_speech(item.text, str(temporary), item.config, metrics=metrics)
        else:
            TTSEngine.generate_speech(item.text, str(temporary), item.config)
        if not valid_audio(temporary):
            raise RuntimeError('저장된 음성 파일을 읽을 수 없습니다. 완료된 이전 음성은 유지됩니다.')
        os.replace(temporary, target)
    except CancelledError:
        emit('cancelled', item, {})
        raise
    finally:
        temporary.unlink(missing_ok=True)
    metrics['total_seconds'] = time.monotonic() - started
    emit('completed', item, (str(target), metrics))


def run_generation(work_dir, items, state, pause, force_overwrite, save, record_performance):
    from .generation_jobs import cached_audio_path, _error_message
    from .cosy_batch_client import synthesize_batch, queue_limits
    from .cosy_colab_client import close_worker_connections
    from .gemini_client import close_worker_clients
    from .chirp_client import MAX_WORKERS as CHIRP_WORKERS, close_worker_client as close_chirp_client
    from .qwen_client import MAX_WORKERS as QWEN_WORKERS, close_worker_client as close_qwen_client

    events = queue.Queue()
    pause_path = Path(work_dir) / '.generation.pause'
    active = set()
    calculating = set()
    completed = {}
    key_attempts, line_keys = {}, {}
    failures = []
    threads = []
    by_index = {item.index: item for item in items}
    engine_stops = {item.config.engine: threading.Event() for item in items}
    state.update(execution_mode='ordered_independent_v2921', active_indices=[], execution_notes=[],
                 cosy_parallel={}, engine_progress={}, engine_errors={}, retrying_lines={}, gemini_key_usage={})
    for item in items:
        progress = state['engine_progress'].setdefault(item.config.engine, dict(total=0, done=0, active=0))
        progress['total'] += 1

    def stopped():
        if pause_path.exists():
            pause.set()
        return pause.is_set()

    def emit(kind, item, details):
        events.put((kind, item, details))

    def engine_stopped(engine):
        return stopped() or engine_stops[engine].is_set()

    def fail_engine(item, exc):
        # A Gemini quota error must not cancel the independent Cosy queue.
        engine_stops[item.config.engine].set()
        emit('error', item, exc)

    def key_record(item, details=None):
        details = details or {}
        index = details.get('key_index', line_keys.get(item.index))
        if not index:
            return None
        line_keys[item.index] = index
        for number in range(1, int(details.get('key_count', index)) + 1):
            state['gemini_key_usage'].setdefault(number, dict(requests=0, completed=0, errors=0, status='아직 요청 없음'))
        return state['gemini_key_usage'][index]

    def update_progress():
        state['active_indices'] = sorted(active)
        state['active_cosy_indices'] = sorted(calculating)
        for engine, progress in state['engine_progress'].items():
            in_flight = sum(by_index[index].config.engine == engine for index in active)
            progress['active'] = len(calculating) if engine == 'cosyvoice' else in_flight
            progress['receiving'] = max(0, in_flight - progress['active'])
            waiting_for_quota = any(retry.get('engine') == engine and retry.get('waiting_for_quota')
                                    for retry in state['retrying_lines'].values())
            progress['status'] = ('failed' if engine in state['engine_errors'] else
                                  'complete' if progress['done'] == progress['total'] else
                                  'paused' if pause.is_set() else
                                  'quota_wait' if waiting_for_quota else 'running')

    def complete(item, path, metrics, reused=False):
        state['retrying_lines'].pop(item.index, None)
        if item.index in completed:
            raise RuntimeError(f'대사 {item.index}번이 중복 완료되어 병합하지 않았습니다.')
        completed[item.index] = dict(index=item.index, speaker=item.speaker, text=item.text,
                                     file_path=path, metrics=metrics)
        state['completed'] = [completed[index] for index in sorted(completed)]
        state['done'] = len(completed)
        state['engine_progress'][item.config.engine]['done'] += 1
        state['progress'] = 0.9 * state['done'] / state['total']
        if reused:
            state['reused'] += 1
        else:
            record_performance(state, item, metrics)
            record = key_record(item, metrics)
            if record is not None:
                record['completed'] += 1
                record['status'] = '생성 완료'
        active.discard(item.index)
        calculating.discard(item.index)

    pending = []
    for item in items:
        cached = cached_audio_path(item) if not force_overwrite else None
        if cached:
            complete(item, cached, None, reused=True)
        else:
            pending.append(item)
    update_progress()
    save(work_dir, state)
    gemini = queue.Queue()
    chirp = queue.Queue()
    qwen = queue.Queue()
    serial = []
    for item in pending:
        if item.config.engine == 'gemini':
            gemini.put(item)
        elif item.config.engine == 'chirp':
            chirp.put(item)
        elif item.config.engine == 'qwen':
            qwen.put(item)
        else:
            serial.append(item)

    def cloud_lane(engine, pending_queue):
        cancel = lambda: engine_stopped(engine)
        while not cancel():
            try:
                item = pending_queue.get_nowait()
            except queue.Empty:
                return
            try:
                _generate_one(item, state['id'], cancel, emit)
            except CancelledError:
                return
            except Exception as exc:
                fail_engine(item, exc)
                return

    def serial_lane():
        position = 0
        while position < len(serial) and not stopped():
            item = serial[position]
            if engine_stops[item.config.engine].is_set():
                position += 1
                continue
            cancel = lambda engine=item.config.engine: engine_stopped(engine)
            try:
                if item.config.engine == 'cosyvoice':
                    item_limit, reference_limit = queue_limits(item.config.cosyvoice_url)
                    group = [item]
                    paths = {item.config.ref_audio_path}
                    reference_bytes = sum(Path(path).stat().st_size for path in paths)
                    while position + len(group) < len(serial) and len(group) < item_limit:
                        other = serial[position + len(group)]
                        if (other.config.engine != 'cosyvoice'
                                or other.config.cosyvoice_url != item.config.cosyvoice_url):
                            break
                        # References are shared by the whole queue, read once.
                        path = other.config.ref_audio_path
                        if path not in paths:
                            size = Path(path).stat().st_size
                            if len(paths) >= reference_limit or reference_bytes + size > 32 * 1024 * 1024:
                                break
                            paths.add(path)
                            reference_bytes += size
                        group.append(other)
                    entries = [dict(TTSEngine.cosyvoice_request(part.text, part.config),
                                    index=part.index, output_file=str(_target(part))) for part in group]
                    used_batch = synthesize_batch(entries, cancel=cancel,
                        on_started=lambda index: emit('started', by_index[index], {'batch_stream': True}),
                        on_calculated=lambda index: emit('calculated', by_index[index], {}),
                        on_completed=lambda index, path, metrics: emit('completed', by_index[index], (path, metrics)),
                        on_status=lambda details: emit('capacity', item, details))
                    if used_batch:
                        position += len(group)
                        continue
                    emit('notice', item, '현재 Cosy 코랩은 대사별 요청 방식입니다. v2.9.15로 업데이트하면 최대 4개를 생성하며 빈자리를 계속 채웁니다.')
                _generate_one(item, state['id'], cancel, emit)
                position += 1
            except CancelledError:
                return
            except Exception as exc:
                failed_item = by_index.get(getattr(exc, 'index', None), item)
                fail_engine(failed_item, exc)
                position += 1

    def worker(lane):
        try:
            lane()
        except BaseException as exc:
            pause.set()
            emit('error', None, exc)
        finally:
            try:
                close_worker_clients()
                close_chirp_client()
                close_qwen_client()
                close_worker_connections()
            finally:
                emit('worker_done', None, None)

    lanes = ([lambda: cloud_lane('gemini', gemini)] * min(2, gemini.qsize())
             + [lambda: cloud_lane('chirp', chirp)] * min(CHIRP_WORKERS, chirp.qsize())
             + [lambda: cloud_lane('qwen', qwen)] * min(QWEN_WORKERS, qwen.qsize())
             + ([serial_lane] if serial else []))
    try:
        for number, lane in enumerate(lanes):
            thread = threading.Thread(target=worker, args=(lane,),
                                      name=f"tts-{state['id'][:8]}-{number}", daemon=True)
            thread.start()
            threads.append(thread)
        finished = 0
        while finished < len(threads):
            stopped()
            try:
                kind, item, details = events.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == 'worker_done':
                finished += 1
                continue
            if kind == 'error':
                failures.append((item, details))
                if item:
                    record = key_record(item)
                    if record is not None:
                        record['errors'] += 1
                        record['status'] = '오류 · 아래 사유 확인'
                    state['retrying_lines'].pop(item.index, None)
                    active.discard(item.index)
                    state['engine_errors'].setdefault(item.config.engine, dict(
                        index=item.index, speaker=item.speaker, message=_error_message(details, items)))
                    if item.config.engine == 'cosyvoice':
                        # Batch errors arrive after all successful in-flight
                        # clips have been received; clear remaining failed slots.
                        active.difference_update(index for index in tuple(active)
                                                 if by_index[index].config.engine == 'cosyvoice')
                        calculating.clear()
                else:
                    pause.set()  # Unattributed worker failure: stop safely.
            elif kind == 'cancelled':
                record = key_record(item)
                if record is not None:
                    record['status'] = '대기 취소'
                state['retrying_lines'].pop(item.index, None)
                active.discard(item.index)
                calculating.discard(item.index)
            elif kind == 'calculated':
                calculating.discard(item.index)
            elif kind == 'notice':
                if details not in state['execution_notes']:
                    state['execution_notes'].append(details)
            elif kind == 'capacity':
                state['cosy_parallel'] = details
            elif kind == 'completed':
                complete(item, *details)
            elif kind in ('started', 'phase'):
                record = key_record(item, details)
                if record is not None:
                    attempts = int(details.get('attempts', 0))
                    record['requests'] += max(0, attempts - key_attempts.get(item.index, 0))
                    key_attempts[item.index] = max(attempts, key_attempts.get(item.index, 0))
                    record['status'] = ('한도 대기' if details.get('waiting_for_quota') else
                                        '재시도 중' if details.get('retrying') else '처리 중')
                if details.get('retrying'):
                    state['retrying_lines'][item.index] = dict(
                        engine=item.config.engine, speaker=item.speaker, phase=details.get('phase', '자동 재시도 중'),
                        waiting_for_quota=bool(details.get('waiting_for_quota')),
                        quota_retry_at=details.get('quota_retry_at', 0.0))
                else:
                    state['retrying_lines'].pop(item.index, None)
                active.add(item.index)
                state.update(current_index=item.index, current_speaker=item.speaker,
                             current_metrics=dict(details, index=item.index, engine=item.config.engine))
                if kind == 'started':
                    if item.config.engine == 'cosyvoice':
                        calculating.add(item.index)
                    state['stage_started'] = time.time()
            update_progress()
            running = ', '.join(str(index) for index in sorted(active))
            state['message'] = (f"생성·수신 중: {running}번 · 저장 완료 {state['done']}/{state['total']}개"
                                if running else f"대사 {state['done']}/{state['total']}개 저장 완료")
            if pause.is_set():
                state['message'] = '중단 요청 처리 중 · 이미 생성 중인 대사를 받아 저장하고 있습니다.'
            elif failures:
                state['message'] += ' · 오류가 없는 엔진은 계속 생성합니다.'
            save(work_dir, state)
    except BaseException:
        pause.set()
        raise
    finally:
        # The workspace lock stays held until every in-flight result is saved.
        for thread in threads:
            thread.join()
        active.clear()
        calculating.clear()
        state['retrying_lines'].clear()
        update_progress()
    if failures:
        item, exc = failures[0]
        if item:
            state.update(current_index=item.index, current_speaker=item.speaker)
        raise RuntimeError(str(exc)) from exc
    if not pause.is_set() and set(completed) != set(by_index):
        raise RuntimeError('완료되지 않은 대사가 있어 전체 파일을 합치지 않았습니다. 저장된 대사는 유지됩니다.')
