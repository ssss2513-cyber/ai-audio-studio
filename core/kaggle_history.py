"""Private, bounded job snapshots and event history inside one session."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import secrets
import time

MAX_EVENTS = 160
MAX_SNAPSHOT_BYTES = 4 * 1024**2


def owner(state):
    return str(state.get('owner') or state.get('ref', '').split('/')[0]).lower()


def folder_for(work_dir, key):
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,100}', str(key)):
        raise ValueError('작업 기록 번호가 올바르지 않습니다.')
    root = (Path(work_dir).resolve() / 'kaggle_jobs').resolve()
    folder = (root / key).resolve()
    if not folder.is_relative_to(root):
        raise ValueError('현재 접속의 작업 기록만 열 수 있습니다.')
    return folder


def remember(work_dir, state):
    """Called before publishing the current state; no credentials or network."""
    root = Path(work_dir).resolve() / 'kaggle_jobs'
    folder = Path(state.get('folder', '')).resolve()
    if folder.parent != root or not folder.is_dir():
        return
    diagnostic = state.get('diagnostic') or {}
    event = dict(status=state.get('status', ''), message=state.get('message', ''),
        done=state.get('done', 0), total=state.get('total', 0),
        operation=diagnostic.get('stage') or diagnostic.get('operation', ''),
        http_status=diagnostic.get('http_status'),
        error=str(state.get('error') or diagnostic.get('reason') or state.get('submission_error') or '')[:2400])
    timeline = state.setdefault('timeline', [])
    if not timeline or {k: v for k, v in timeline[-1].items() if k != 'at'} != event:
        timeline.append(dict(event, at=state.get('updated') or time.time()))
        state['timeline'] = timeline[-MAX_EVENTS:]
    target = folder / 'site_job_state.json'
    temporary = folder / ('site_job_state.' + secrets.token_hex(4) + '.tmp')
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temporary.chmod(0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def read(work_dir, key, username):
    folder = folder_for(work_dir, key)
    path = folder / 'site_job_state.json'
    if path.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise ValueError('작업 기록이 너무 큽니다.')
    state = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(state, dict) or owner(state) != username.lower():
        raise ValueError('이 작업을 만든 캐글 계정으로 연결해주세요.')
    if Path(state.get('folder', '')).resolve() != folder:
        raise ValueError('작업 기록의 저장 위치를 확인할 수 없습니다.')
    return state


def local_jobs(work_dir, username, current=None):
    rows = {}
    root = Path(work_dir) / 'kaggle_jobs'
    if root.is_dir():
        folders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
        for folder in folders[:100]:
            try:
                rows[folder.name] = read(work_dir, folder.name, username)
            except (OSError, ValueError, TypeError):
                continue
    if current and owner(current) == username.lower():
        folder = Path(current.get('folder', ''))
        if folder.parent.resolve() == root.resolve():
            rows[folder.name] = deepcopy(current)
    return sorted(rows.items(), key=lambda row: row[1].get('updated', row[1].get('started', 0)), reverse=True)
