"""Per-workspace TTS jobs which survive Streamlit UI reruns.

Workers never call Streamlit or read session_state. Only explicit starts launch
work; polling and reading a checkpoint never retry a synthesis request.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import os
import re
import threading
import time
import uuid
import wave
import zipfile

try:
    import fcntl
except ImportError:  # Local Windows runs still use the in-process registry.
    fcntl = None

from mutagen.mp3 import MP3

from .audio_processor import AudioProcessor
from .subtitle import SubtitleGenerator
from .tts_engine import TTSEngine, VoiceConfig


@dataclass
class GenerationItem:
    index: int
    speaker: str
    text: str
    file_path: str
    config: VoiceConfig


_LOCK = threading.RLock()
_JOBS = {}


def _key(work_dir):
    return str(Path(work_dir).resolve())


def is_running(work_dir):
    with _LOCK:
        if _key(work_dir) in _JOBS:
            return True
        lock_path = Path(work_dir) / ".generation.lock"
        if fcntl is not None and lock_path.is_file():
            with lock_path.open("a+b") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                fcntl.flock(handle, fcntl.LOCK_UN)
        return False


def _save(work_dir, state):
    state["updated"] = time.time()
    target = Path(work_dir) / "generation_job.json"
    temporary = target.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        json.dump(state, handle, ensure_ascii=False)
    os.replace(temporary, target)


def get_job(work_dir):
    try:
        state = json.loads((Path(work_dir) / "generation_job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    if state.get("status") == "running" and not is_running(work_dir):
        state["status"] = "interrupted"
        state["error"] = (
            "사이트 작업이 중단되었습니다. 저장된 음성은 유지됩니다. "
            "덮어쓰기를 끄고 생성 버튼을 누르면 완료 파일을 확인해 이어서 만듭니다."
        )
    if state.get("partial_status") == "running" and not is_running(work_dir):
        state["partial_status"] = "interrupted"
        state["partial_error"] = "MP3 합치기가 중단되었습니다. 저장된 대사는 유지되며 다시 합칠 수 있습니다."
    return state


def clear_job(work_dir):
    with _LOCK:
        if is_running(work_dir):
            raise RuntimeError("음성 생성 중에는 작업 파일을 지울 수 없습니다.")
        (Path(work_dir) / "generation_job.json").unlink(missing_ok=True)


def request_pause(work_dir):
    with _LOCK:
        job = _JOBS.get(_key(work_dir))
        if job:
            job["pause"].set()
        if is_running(work_dir):
            # Also reaches a worker kept alive across a source-code reload.
            (Path(work_dir) / ".generation.pause").touch(mode=0o600)


def valid_audio(path):
    """Read saved WAV/MP3 metadata without decoding a story into memory."""
    try:
        path = Path(path)
        if path.suffix.lower() == ".wav":
            if not path.is_file():
                return False
            with wave.open(str(path), "rb") as audio:
                frames = audio.getnframes()
                if frames <= 0 or audio.getframerate() <= 0:
                    return False
                audio.setpos(frames - 1)
                return len(audio.readframes(1)) == audio.getnchannels() * audio.getsampwidth()
        return (path.is_file() and path.stat().st_size >= 100
                and math.isfinite(length := MP3(path).info.length) and length > 0)
    except Exception:
        # Mutagen exposes several parsing exception classes for damaged files.
        return False


def cached_audio_path(item):
    """Prefer a completed lossless CosyVoice clip; accept previous MP3 caches."""
    target = Path(item.file_path)
    candidates = [target.with_suffix(".wav"), target] if item.config.engine in ("cosyvoice", "gemini") else [target]
    return next((str(path) for path in candidates if valid_audio(path)), None)


def _freeze_references(work_dir, items, force_overwrite):
    """Keep an upload/change in the UI from changing an in-flight job's voice."""
    copies = {}
    for item in items:
        if not force_overwrite and cached_audio_path(item):
            continue
        if item.config.engine not in ("gpt-sovits", "cosyvoice"):
            continue
        source = Path(item.config.ref_audio_path)
        if str(source) not in copies:
            with source.open("rb") as handle:
                data = handle.read(10 * 1024 * 1024 + 1)
            if not data or len(data) > 10 * 1024 * 1024:
                raise ValueError(f"{item.speaker}: 참조 음성은 10MB 이하로 등록해주세요.")
            folder = Path(work_dir) / "generation_references"
            folder.mkdir(exist_ok=True, mode=0o700)
            target = folder / (hashlib.sha256(data).hexdigest() + source.suffix.lower())
            if not target.exists():
                target.write_bytes(data)
                os.chmod(target, 0o600)
            copies[str(source)] = str(target)
        item.config.ref_audio_path = copies[str(source)]


def start_job(work_dir, items, *, force_overwrite=False, pause_ms=500, include_speaker=True):
    work_dir = _key(work_dir)
    with _LOCK:
        if is_running(work_dir):
            raise RuntimeError("이미 음성을 생성하고 있습니다. 아래 진행 상황을 확인해주세요.")
        items = deepcopy(items)
        if not items:
            raise ValueError("생성할 대사가 없습니다.")
        indices = [item.index for item in items]
        if any(type(index) is not int or index <= 0 for index in indices) or len(set(indices)) != len(indices):
            raise ValueError("대사 번호가 중복되거나 올바르지 않습니다.")
        items.sort(key=lambda item: item.index)
        # On the shared Linux server, a source reload must not launch a second
        # worker over the first module's still-running thread.
        ownership = (Path(work_dir) / ".generation.lock").open("a+b")
        try:
            if fcntl is not None:
                fcntl.flock(ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _freeze_references(work_dir, items, force_overwrite)
            (Path(work_dir) / ".generation.pause").unlink(missing_ok=True)
        except BaseException:
            ownership.close()
            raise
        state = {
            "id": uuid.uuid4().hex, "status": "running", "stage": "voice",
            "total": len(items), "done": 0, "reused": 0, "completed": [],
            "first": items[0].index, "last": items[-1].index,
            "current_index": items[0].index, "current_speaker": items[0].speaker,
            "progress": 0.0, "message": "음성 생성을 준비하고 있습니다.",
            "started": time.time(), "error": "", "result": None,
            "pause_ms": pause_ms,
            "pending_total": sum(1 for item in items if force_overwrite or not cached_audio_path(item)),
            "generated": 0, "recent_seconds": [], "performance": {},
        }
        pause = threading.Event()
        thread = threading.Thread(
            target=_run_job,
            args=(work_dir, items, state, pause, force_overwrite, pause_ms, include_speaker, ownership),
            name="tts-" + state["id"][:8], daemon=True,
        )
        _JOBS[work_dir] = {"thread": thread, "pause": pause}
        try:
            _save(work_dir, state)
            thread.start()
        except BaseException:
            _JOBS.pop(work_dir, None)
            ownership.close()
            raise
    return state["id"]


def _error_message(exc, items):
    message = str(exc)
    for item in items:
        config = item.config
        for secret in (config.gpt_sovits_url, config.cosyvoice_url):
            if secret:
                message = message.replace(secret, "[내 코랩 주소]")
        for secret in re.split(r"[,;\s]+", config.api_key or ""):
            if secret:
                message = message.replace(secret, "[API 키]")
    return re.sub(r"/v1/[A-Za-z0-9_-]{16,128}", "/v1/[연결 토큰]", message)[:1600]


def _record_performance(state, item, metrics):
    state["generated"] += 1
    state["recent_seconds"] = (state["recent_seconds"] + [metrics["total_seconds"]])[-10:]
    average = sum(state["recent_seconds"]) / len(state["recent_seconds"])
    state["average_seconds"] = average
    elapsed = max(0.0, time.time() - state["started"])
    completions = (state.get("completion_times", [0.0]) + [elapsed])[-11:]
    state["completion_times"] = completions
    throughput = (completions[-1] - completions[0]) / max(1, len(completions) - 1)
    state["throughput_seconds"] = throughput
    state["remaining_estimate_seconds"] = throughput * max(0, state["pending_total"] - state["generated"])
    state["latest_metrics"] = dict(metrics, index=item.index, speaker=item.speaker)
    if item.config.engine == "cosyvoice":
        totals = state["performance"]
        totals["cosy_lines"] = totals.get("cosy_lines", 0) + 1
        for name in ("total_seconds", "audio_seconds", "reference_seconds", "synthesis_seconds",
                     "transport_seconds", "save_seconds", "postprocess_seconds", "llm_seconds", "sampling_seconds",
                     "retries", "retry_seconds", "response_wait_seconds", "download_seconds",
                     "decode_seconds", "client_preparation_seconds", "wire_bytes", "wav_bytes"):
            if name in metrics:
                totals[name] = totals.get(name, 0) + metrics[name]


def _run_job(work_dir, items, state, pause, force_overwrite, pause_ms, include_speaker, ownership):
    pause_path = Path(work_dir) / ".generation.pause"
    try:
        from .generation_pipeline import run_generation
        run_generation(work_dir, items, state, pause, force_overwrite, _save, _record_performance)

        if pause.is_set() or pause_path.exists():
            state.update(status="paused", message=("음성을 모두 저장했습니다. 이어서 생성하면 병합부터 진행합니다."
                         if state['done'] == state['total'] else "진행 중이던 대사까지 저장하고 멈췄습니다. 완료 파일은 유지됩니다."))
            return
        # Result files are separate from earlier successful bundles. Publishing
        # a result requires merging, subtitles and the final bundle to finish.
        folder = Path(work_dir) / "results" / state["id"]
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        state.update(stage="merge", message="음성 생성 완료 · 전체 오디오를 합치고 있습니다.",
                     stage_started=time.time())
        merge_started = time.monotonic()
        _save(work_dir, state)
        full_audio = str(folder / "full_audio.mp3")
        _, timings = AudioProcessor(pause_ms=pause_ms).merge_segments(
            state["completed"], full_audio, pause_ms=pause_ms)
        state["merge_seconds"] = time.monotonic() - merge_started
        state.update(stage="subtitles", progress=0.95, message="자막을 만들고 있습니다.")
        _save(work_dir, state)
        srt, vtt = str(folder / "subtitles.srt"), str(folder / "subtitles.vtt")
        SubtitleGenerator.generate_srt(timings, srt, include_speaker=include_speaker)
        SubtitleGenerator.generate_vtt(timings, vtt, include_speaker=include_speaker)
        state.update(stage="package", progress=0.97, message="다운로드 파일을 준비하고 있습니다.")
        _save(work_dir, state)
        main_zip = str(folder / "tts_main_bundle.zip")
        with zipfile.ZipFile(main_zip, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(full_audio, "full_audio.mp3", compress_type=zipfile.ZIP_STORED)
            bundle.write(srt, "subtitles.srt")
            bundle.write(vtt, "subtitles.vtt")
        state.update(status="complete", progress=1.0, message="모든 음성과 자막을 저장했습니다.", result={
            "full_audio": full_audio, "srt": srt, "vtt": vtt,
            "main_zip": main_zip,
            "timings": [asdict(timing) for timing in timings],
            "audio_info_list": state["completed"],
        })
    except Exception as exc:
        location = (f"대사 {state['current_index']}번 ({state['current_speaker']})"
                    if state["stage"] == "voice" else state["message"])
        state.update(status="failed", error=location + ": " + _error_message(exc, items),
                     message="일부 대사를 생성하지 못했습니다. 다른 엔진에서 완료한 음성도 보관됩니다. 미완료 대사를 이어서 생성해주세요.")
    finally:
        from .gemini_client import close_worker_clients
        close_worker_clients()
        if state["status"] == "running":
            state.update(status="interrupted", error="작업이 중단되었습니다. 완료 파일을 확인해 이어서 생성해주세요.")
        try:
            _save(work_dir, state)
        finally:
            with _LOCK:
                _JOBS.pop(work_dir, None)
                ownership.close()


def start_partial_merge(work_dir, *, pause_ms=500):
    """Join saved lines in a background worker; never call a TTS engine."""
    work_dir = _key(work_dir)
    with _LOCK:
        if is_running(work_dir):
            raise RuntimeError("진행 중인 대사를 저장한 뒤 멈추고 다운로드해주세요.")
        state = get_job(work_dir)
        if not state or not state.get("completed"):
            raise ValueError("아직 저장된 대사가 없습니다.")
        ownership = (Path(work_dir) / ".generation.lock").open("a+b")
        try:
            if fcntl is not None:
                fcntl.flock(ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = deepcopy(state)
            previous_stage = state.get("stage", "voice")
            previous_message = state.get("message", "완료된 음성은 보관됩니다.")
            state.update(stage="partial_merge", partial_status="running", partial_error="",
                         stage_started=time.time(), message=f"저장된 대사 {len(state['completed'])}개를 MP3 하나로 합치고 있습니다.")
            thread = threading.Thread(
                target=_run_partial_merge,
                args=(work_dir, state, state.get("pause_ms", pause_ms), ownership,
                      previous_stage, previous_message),
                name="merge-" + state["id"][:8], daemon=True)
            _JOBS[work_dir] = {"thread": thread, "pause": threading.Event()}
            _save(work_dir, state)
            thread.start()
        except BaseException:
            _JOBS.pop(work_dir, None)
            ownership.close()
            raise
    return state["id"]


def _run_partial_merge(work_dir, state, pause_ms, ownership, previous_stage, previous_message):
    temporary = None
    try:
        folder = Path(work_dir) / "results" / state["id"]
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = folder / "completed_audio.mp3"
        temporary = folder / ".completed_audio.part.mp3"
        completed = sorted(state["completed"], key=lambda item: item["index"])
        AudioProcessor(pause_ms=pause_ms).merge_segments(completed, str(temporary), pause_ms=pause_ms)
        os.replace(temporary, target)
        state.update(partial_status="complete", partial_audio=str(target), partial_count=len(completed))
    except Exception as exc:
        state.update(partial_status="failed", partial_error=str(exc)[:1600])
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        state.update(stage=previous_stage, message=previous_message)
        if state.get("partial_status") == "running":
            state.update(partial_status="interrupted", partial_error="MP3 합치기가 중단되었습니다. 다시 합쳐주세요.")
        try:
            _save(work_dir, state)
        finally:
            with _LOCK:
                _JOBS.pop(work_dir, None)
                ownership.close()
