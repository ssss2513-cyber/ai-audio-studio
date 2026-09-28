"""Per-browser workspaces and validated URLs for the bundled personal Colab notebooks."""
import os
from pathlib import Path
import re
import secrets
import shutil
from urllib.parse import urlsplit, urlunsplit

SESSION_KEY = "_personal_colab_session_id"
WORK_ROOT = Path(__file__).resolve().parent.parent / "outputs" / "web_sessions"


def session_workspace(state):
    session_id = state.get(SESSION_KEY, "")
    if not isinstance(session_id, str) or not re.fullmatch(r"[a-f0-9]{32}", session_id):
        # Old shared-workspace paths and operator defaults must not survive an upgrade.
        state.clear()
        session_id = secrets.token_hex(16)
        state[SESSION_KEY] = session_id
    directory = WORK_ROOT / session_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return str(directory)


def clear_session(state):
    session_id = state.get(SESSION_KEY, "")
    if isinstance(session_id, str) and re.fullmatch(r"[a-f0-9]{32}", session_id):
        directory = WORK_ROOT / session_id
        if directory.is_dir() and not directory.is_symlink():
            shutil.rmtree(directory)
    state.clear()


def upload_name(name):
    basename = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    stem, suffix = os.path.splitext(basename)
    stem = re.sub(r"[^\w .-]", "_", stem).strip(" .")[:60] or "reference"
    return stem + suffix.lower()


def connection_url(value, engine):
    value = (value or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("코랩 4번 셀의 연결 주소 전체를 복사해주세요.") from exc
    if (parts.scheme != "https" or not parts.hostname
            or not re.fullmatch(r"[a-z0-9-]+\.trycloudflare\.com", parts.hostname)
            or port not in (None, 443) or parts.username or parts.password
            or parts.query or parts.fragment):
        raise ValueError("이 공유 사이트에는 수정 코랩에서 발급한 https://…trycloudflare.com 주소를 입력해주세요.")
    path = parts.path.rstrip("/")
    suffix = r"/tts" if engine == "gpt-sovits" else ""
    if not re.fullmatch(r"/v1/[A-Za-z0-9_-]{16,128}" + suffix, path):
        ending = "/v1/…/tts" if engine == "gpt-sovits" else "/v1/…"
        raise ValueError(f"주소 끝의 {ending} 부분까지 모두 복사해주세요. 선택한 엔진의 주소인지 확인하세요.")
    return urlunsplit(("https", parts.hostname, path, "", ""))
