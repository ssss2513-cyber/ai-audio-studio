"""Supervise an actual TTS server and its tunnel from the running notebook cell.

No synthetic activity, browser clicks, inference or model restarts. This cannot
override Colab's idle/resource policies. Importing the module does no work.
"""
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

VERSION = '1.0.0'
ENGINES = {
    'qwen-bank': ('/content/voice_studio_qwen_bank_v1', 'voice-studio-qwen-voicebank', 'Qwen 20종'),
    'cosyvoice': ('/content/ai_voice_dual_v1/cosyvoice', 'ai-voice-studio-cosyvoice', 'CosyVoice'),
    'cosyvoice3': ('/content/voice_studio_cosy3_v1', 'voice-studio-cosyvoice3', 'CosyVoice 3'),
    'gpt-sovits': ('/content/ai_voice_sovits_v4_1', 'ai-voice-studio-gpt-sovits', 'GPT-SoVITS'),
}


def _redact(value):
    return re.sub(r'/v1/[A-Za-z0-9_-]+', '/v1/[접속 코드 숨김]', str(value))


def _state(root):
    try:
        result = json.loads((root / 'state.json').read_text())
        if not isinstance(result, dict):
            raise ValueError()
        port = result['port']
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError()
        base = result.get('base') or f"http://127.0.0.1:{port}/v1/{result['token']}"
        if not re.fullmatch(rf'http://127\.0\.0\.1:{port}/v1/[A-Za-z0-9_-]{{20,100}}', base):
            raise ValueError()
        return result, base
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError('서버 실행 기록을 읽지 못했습니다. 같은 코랩에서 서버 준비 셀을 먼저 실행해주세요.') from None


def _public(state):
    value = state.get('public_url') or state.get('public_base') or ''
    return value if re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com/v1/[A-Za-z0-9_-]{20,100}', value) else ''


def _process(pid):
    """A zombie is stopped; start ticks distinguish a reused process id."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
        return None if fields[0] in ('Z', 'X') else (pid, fields[19])
    except (OSError, IndexError):
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _health(base, service):
    try:
        with urllib.request.build_opener(_NoRedirect()).open(base + '/health', timeout=8) as response:
            data = json.loads(response.read(65537))
        if not isinstance(data, dict) or data.get('service') != service or data.get('ready') is not True:
            return False, '서버 종류 또는 준비 상태가 다릅니다.'
        return True, '정상'
    except urllib.error.HTTPError as exc:
        return False, f'HTTP {exc.code}'
    except (OSError, ValueError):
        return False, '응답 없음'


def _resources(root):
    info = {}
    try:
        available = re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)
        if available:
            info['available_ram_mib'] = int(available[1]) // 1024
        info['free_disk_mib'] = shutil.disk_usage(root).free // (1024 * 1024)
        # Only report an observed cgroup OOM count, never infer Colab's reason.
        for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines():
            key, value = line.split()
            if key == 'oom_kill':
                info['oom_kill_count'] = int(value)
    except (OSError, ValueError):
        pass
    return info


def _record(root, kind, message, **details):
    path = root / 'session.log'
    try:
        if path.exists() and path.stat().st_size > 1024 * 1024:
            path.replace(root / 'session.previous.log')
        with path.open('a', encoding='utf-8') as log:
            log.write(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(), kind=kind,
                                      message=_redact(message), **details), ensure_ascii=False) + '\n')
        path.chmod(0o600)
    except OSError:
        pass  # A full disk must not stop an otherwise working server session.


def _last_errors(root):
    for filename in ('server.log', 'tunnel.log'):
        path = root / filename
        try:
            with path.open('rb') as log:
                log.seek(max(0, path.stat().st_size - 6000))
                output = log.read().decode('utf-8', errors='replace')
            print(f'\n【{filename} 마지막 기록】\n' + _redact('\n'.join(output.splitlines()[-25:])), flush=True)
        except OSError:
            pass


def _restore_tunnel(root, state, base, service):
    """Replace a confirmed stopped tunnel only; never stop the voice model."""
    binary = root / 'cloudflared'
    if not binary.is_file():
        raise RuntimeError('연결 도구가 없습니다. 이 코랩의 4번 셀을 다시 실행해주세요.')
    if _process(state.get('tunnel_pid')) is not None:
        raise RuntimeError('기존 연결 도구가 실행 중이므로 중복 실행하지 않았습니다.')
    with (root / 'tunnel.log').open('w') as log:
        child = subprocess.Popen([str(binary), 'tunnel', '--no-autoupdate', '--url',
                                  f"http://127.0.0.1:{state['port']}"], stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    saved = False
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError('외부 연결 도구가 다시 종료됐습니다. 아래 tunnel.log를 확인해주세요.')
            content = (root / 'tunnel.log').read_text(errors='replace')[-24000:]
            match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', content)
            if match:
                public = match[0] + base.split(f":{state['port']}", 1)[1]
                ok, _ = _health(public, service)
                if ok:
                    current, current_base = _state(root)
                    if current_base != base:
                        raise RuntimeError('서버 실행 정보가 바뀌었습니다. 중복 연결 생성을 중단했습니다.')
                    current.update(tunnel_pid=child.pid)
                    current['public_url' if 'token' in current else 'public_base'] = public
                    temporary = root / 'state.session.tmp'
                    temporary.write_text(json.dumps(current), encoding='utf-8')
                    temporary.chmod(0o600)
                    temporary.replace(root / 'state.json')
                    saved = True
                    return current
            time.sleep(2)
        raise RuntimeError('외부 연결 복구 응답이 없습니다. 음성 서버는 중지하지 않았습니다.')
    finally:
        if not saved and child.poll() is None:
            child.terminate()


def supervise(engine):
    """Keep real process supervision attached to the interactive notebook cell."""
    if engine not in ENGINES:
        raise ValueError('지원하지 않는 음성 엔진입니다.')
    directory, service, label = ENGINES[engine]
    root = Path(directory)
    state, base = _state(root)
    with (root / 'session.lock').open('a') as session_lock:
        try:
            fcntl.flock(session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('이미 다른 셀에서 서버 상태를 확인하고 있습니다. 해당 셀을 계속 사용하세요.', flush=True)
            return
        ok, reason = _health(base, service)
        if not ok:
            _last_errors(root)
            raise RuntimeError('음성 서버 응답 확인 실패: ' + reason + ' 서버 준비 셀의 오류를 확인해주세요.')
        server_process = _process(state.get('pid'))
        initial_resources = _resources(root)
        _record(root, 'start', label + ' 서버 감독 시작', resources=initial_resources)
        print(f'\n▶ {label} 서버 감독 v{VERSION} 실행 중 — 이 셀을 실행 상태로 두고 사이트를 사용하세요.', flush=True)
        print('서버·외부 연결의 실제 상태와 오류를 확인합니다. 다른 셀을 사용하려면 먼저 이 셀의 ■를 누르세요.', flush=True)
        print('■는 감독만 종료합니다. 음성 서버·생성 중인 작업·완료 파일은 그대로 둡니다.', flush=True)
        print('코랩의 자원 회수나 유휴 정책을 변경하지 않으며 최대 사용 시간을 보장하지 않습니다.', flush=True)
        repairs, failures, previous, last_report, next_public = 0, 0, '', 0.0, 0.0
        public_reason = '확인 중'
        try:
            while True:
                current, current_base = _state(root)
                if current_base != base:
                    raise RuntimeError('음성 서버 주소가 변경됐습니다. 새 주소의 4번 셀에서 다시 연결해주세요.')
                current_process = _process(current.get('pid'))
                if server_process and current_process != server_process:
                    resources = _resources(root)
                    oom_before = initial_resources.get('oom_kill_count', 0)
                    if resources.get('oom_kill_count', 0) > oom_before:
                        reason = '서버 프로세스 종료와 시스템 RAM 부족 종료 기록의 증가가 확인됐습니다.'
                    else:
                        reason = '음성 서버 프로세스가 종료되거나 교체됐습니다. 종료 원인은 마지막 로그를 확인해야 합니다.'
                    _record(root, 'server_stopped', reason, resources=resources)
                    raise RuntimeError(reason + ' 코랩 런타임 전체 종료와는 다릅니다.')
                ok, reason = _health(base, service)
                failures = 0 if ok else failures + 1
                if failures >= 3:
                    raise RuntimeError('음성 서버가 세 번 연속 응답하지 않습니다. 프로세스가 살아 있어 자동 재시작하지 않았습니다. ' + reason)
                public = _public(current)
                tunnel_alive = _process(current.get('tunnel_pid'))
                if ok and type(current.get('tunnel_pid')) is int and tunnel_alive is None:
                    if repairs >= 2:
                        raise RuntimeError('외부 연결 도구가 반복 종료되어 복구를 멈췄습니다. 음성 서버는 그대로 실행 중입니다.')
                    repairs += 1
                    print(f'외부 연결 도구 종료 확인 — 음성 서버를 유지하고 연결만 복구합니다 ({repairs}/2).', flush=True)
                    _record(root, 'tunnel_stopped', '외부 연결만 복구', attempt=repairs)
                    current = _restore_tunnel(root, current, base, service)
                    public = _public(current)
                    print('\n✅ 새 프로그램 연결 주소 — 사이트의 내 코랩 주소를 아래 주소로 바꿔주세요.', flush=True)
                    print(public + ('/tts' if engine == 'gpt-sovits' else ''), flush=True)
                    next_public = 0
                now = time.monotonic()
                if ok and now >= next_public:
                    public_ok, public_reason = _health(public, service) if public else (False, '주소 없음')
                    public_reason = '정상' if public_ok else public_reason + ' · 코랩 내부 서버는 정상'
                    next_public = now + 60
                message = f'음성 서버: {reason} · 외부 연결: {public_reason}'
                if message != previous or now - last_report >= 60:
                    print(datetime.now().strftime('%H:%M:%S') + ' · ' + message, flush=True)
                    _record(root, 'status', message, resources=_resources(root))
                    previous, last_report = message, now
                time.sleep(15)
        except KeyboardInterrupt:
            _record(root, 'user_stop', '사용자가 감독 셀을 중단함')
            print('\n감독을 종료했습니다. 음성 서버와 생성 결과는 유지됩니다. 다시 감독하려면 4번 셀을 실행하세요.', flush=True)
        except Exception as exc:
            _record(root, 'error', str(exc), resources=_resources(root))
            _last_errors(root)
            raise


def diagnostics(engine):
    root = Path(ENGINES[engine][0])
    print('현재 자원 상태:', _resources(root))
    path = root / 'session.log'
    if path.is_file():
        with path.open('rb') as log:
            log.seek(max(0, path.stat().st_size - 12000))
            print(_redact(log.read().decode('utf-8', errors='replace')))
    else:
        print('이 런타임에는 감독 기록이 없습니다. 런타임 자체가 삭제됐다면 이전 로컬 기록은 복구할 수 없습니다.')
    _last_errors(root)
