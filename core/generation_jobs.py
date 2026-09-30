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
    """Read MP3 metadata only; short, valid speech must not be regenerated."""
    try:
        path = Path(path)
        return (path.is_file() and path.stat().st_size >= 100
                and math.isfinite(length := MP3(path).info.length) and length > 0)
    except Exception:
        # Mutagen exposes several parsing exception classes for damaged files.
        return False


def _freeze_references(work_dir, items, force_overwrite):
    """Keep an upload/change in the UI from changing an in-flight job's voice."""
    copies = {}
    for item in items:
        if not force_overwrite and valid_audio(item.file_path):
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


def _run_job(work_dir, items, state, pause, force_overwrite, pause_ms, include_speaker, ownership):
    generated_gemini = False
    pause_path = Path(work_dir) / ".generation.pause"
    try:
        for item in items:
            if pause.is_set() or pause_path.exists():
                state.update(status="paused", message="요청에 따라 생성을 멈췄습니다. 완료 파일은 보관됩니다.")
                return
            state.update(current_index=item.index, current_speaker=item.speaker,
                         message=f"{item.index}번 대사 생성 중 · {item.speaker}",
                         stage_started=time.time())
            _save(work_dir, state)
            target = Path(item.file_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not force_overwrite and valid_audio(target):
                state["reused"] += 1
            else:
                if item.config.engine == "gemini" and generated_gemini:
                    keys = [key for key in re.split(r"[,;\s]+", item.config.api_key or "") if key]
                    if pause.wait(max(1.0, 4.2 / max(1, len(keys)))) or pause_path.exists():
                        state.update(status="paused", message="요청에 따라 생성을 멈췄습니다.")
                        return
                # Every engine writes to a temporary MP3. A failure cannot
                # destroy a previously completed output, even in overwrite mode.
                temporary = target.with_name("." + target.stem + "." + state["id"] + ".part.mp3")
                try:
                    TTSEngine.generate_speech(item.text, str(temporary), item.config)
                    if not valid_audio(temporary):
                        raise RuntimeError("저장된 음성 파일을 읽을 수 없습니다. 해당 대사에서 멈췄습니다.")
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
                generated_gemini = generated_gemini or item.config.engine == "gemini"
            state["completed"].append({
                "index": item.index, "speaker": item.speaker,
                "text": item.text, "file_path": str(target),
            })
            state["done"] = len(state["completed"])
            state["progress"] = 0.9 * state["done"] / state["total"]
            state["message"] = f"대사 {state['done']}/{state['total']}개 저장 완료"
            _save(work_dir, state)

        if pause.is_set() or pause_path.exists():
            state.update(status="paused", message="음성을 모두 저장했습니다. 이어서 생성하면 병합부터 진행합니다.")
            return
        # Result files are separate from earlier successful bundles. Publishing
        # a result requires merging, subtitles and both ZIPs to finish.
        folder = Path(work_dir) / "results" / state["id"]
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        state.update(stage="merge", message="음성 생성 완료 · 전체 오디오를 합치고 있습니다.",
                     stage_started=time.time())
        _save(work_dir, state)
        full_audio = str(folder / "full_audio.mp3")
        _, timings = AudioProcessor(pause_ms=pause_ms).merge_segments(
            state["completed"], full_audio, pause_ms=pause_ms)
        state.update(stage="subtitles", progress=0.95, message="자막을 만들고 있습니다.")
        _save(work_dir, state)
        srt, vtt = str(folder / "subtitles.srt"), str(folder / "subtitles.vtt")
        SubtitleGenerator.generate_srt(timings, srt, include_speaker=include_speaker)
        SubtitleGenerator.generate_vtt(timings, vtt, include_speaker=include_speaker)
        state.update(stage="package", progress=0.97, message="다운로드 파일을 준비하고 있습니다.")
        _save(work_dir, state)
        main_zip, seg_zip = str(folder / "tts_main_bundle.zip"), str(folder / "tts_segments_bundle.zip")
        with zipfile.ZipFile(main_zip, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(full_audio, "full_audio.mp3", compress_type=zipfile.ZIP_STORED)
            bundle.write(srt, "subtitles.srt")
            bundle.write(vtt, "subtitles.vtt")
        _segment_bundle(seg_zip, state["completed"])
        state.update(status="complete", progress=1.0, message="모든 음성과 자막을 저장했습니다.", result={
            "full_audio": full_audio, "srt": srt, "vtt": vtt,
            "main_zip": main_zip, "seg_zip": seg_zip,
            "timings": [asdict(timing) for timing in timings],
            "audio_info_list": state["completed"],
        })
    except Exception as exc:
        location = (f"대사 {state['current_index']}번 ({state['current_speaker']})"
                    if state["stage"] == "voice" else state["message"])
        state.update(status="failed", error=location + ": " + _error_message(exc, items),
                     message="생성을 멈췄습니다. 완료된 음성은 보관됩니다.")
    finally:
        if state["status"] == "running":
            state.update(status="interrupted", error="작업이 중단되었습니다. 완료 파일을 확인해 이어서 생성해주세요.")
        try:
            _save(work_dir, state)
        finally:
            with _LOCK:
                _JOBS.pop(work_dir, None)
                ownership.close()


def _segment_bundle(target, completed):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as bundle:
        for item in completed:
            path = Path(item["file_path"])
            if path.is_file():
                bundle.write(path, "segments/" + path.name)


def partial_bundle(work_dir):
    """Only called on an explicit download-preparation click, never by polling."""
    with _LOCK:
        if is_running(work_dir):
            raise RuntimeError("진행 중인 대사를 저장한 뒤 멈추고 다운로드해주세요.")
        state = get_job(work_dir)
        if not state or not state.get("completed"):
            raise ValueError("아직 저장된 대사가 없습니다.")
        folder = Path(work_dir) / "results" / state["id"]
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = folder / "tts_partial_segments.zip"
        _segment_bundle(target, state["completed"])
        return str(target)
