"""Forward runner output to a Colab cell and show verified completion details.

Only standard-library imports; importing this file never starts a process.
The notebooks embed this helper so downloaded copies remain self-contained.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request


def _ready_state(runner):
    expected_service = {
        'qwen_colab_server.py': 'voice-studio-qwen-customvoice',
        'qwen_voicebank_colab_server.py': 'voice-studio-qwen-voicebank',
    }[runner.name]
    try:
        state = json.loads((runner.parent / 'state.json').read_text(encoding='utf-8'))
        port, token = state['port'], state['token']
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('Invalid local port')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', token):
            raise ValueError('Invalid local token')
        request = urllib.request.Request(f'http://127.0.0.1:{port}/v1/{token}/health')
        with urllib.request.urlopen(request, timeout=15) as response:
            status = json.load(response)
        if status.get('service') != expected_service or not status.get('ready'):
            raise ValueError('Server is not ready')
        return state, status
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError('서버 준비 상태를 확인하지 못했습니다. 2번 셀을 실행하고 위에 표시되는 오류를 확인해주세요.') from None


def run_qwen(runner, action, *arguments):
    runner = Path(runner)
    labels = {
        'setup': '1번 설치', 'start': '2번 서버 준비',
        'direct': '3번 전체 음성 생성', 'tunnel': '4번 연결 주소 만들기',
        'backup': '목소리 보관', 'restore': '목소리 복원',
    }
    if action not in labels:
        raise ValueError('지원하지 않는 코랩 작업입니다.')
    if not runner.is_file():
        raise RuntimeError('먼저 수정본의 1번 설치 셀을 실행해주세요.')
    print(f'▶ {labels[action]} 시작 · 진행 내용이 아래에 표시됩니다.', flush=True)
    command = [sys.executable, '-u', str(runner), action, *map(str, arguments)]
    # Notebook kernels do not always display inherited subprocess file descriptors.
    # Read the child's output explicitly and print through the notebook's stream.
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1,
        env=dict(os.environ, PYTHONUNBUFFERED='1'),
    )
    try:
        for line in process.stdout:
            print(line, end='', flush=True)
        returncode = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        print('\n셀 대기를 중단했습니다. 다시 실행하면 서버 상태를 먼저 확인합니다.', flush=True)
        raise
    finally:
        process.stdout.close()
    if returncode != 0:
        raise RuntimeError(f'{labels[action]} 중 오류가 발생했습니다. 바로 위 오류 내용을 확인해주세요. (종료 코드 {returncode})')
    # A zero exit code alone is not proof that the requested resource is ready.
    if action == 'setup':
        if not (runner.parent / 'installed.json').is_file() or not (runner.parent / 'venv/bin/python').is_file():
            raise RuntimeError('설치 완료 파일이 없습니다. 위 설치 로그를 확인하고 1번 셀을 다시 실행해주세요.')
        print('✅ 1번 설치 완료. 이제 2번 셀의 ▶를 눌러주세요.', flush=True)
    elif action == 'start':
        _, status = _ready_state(runner)
        print(f"✅ 2번 서버 응답 확인 완료 · {status.get('gpu', '')}", flush=True)
        print('공유 사이트에서 사용하려면 3번을 건너뛰고 4번 셀의 ▶를 눌러주세요.', flush=True)
    elif action == 'tunnel':
        state, _ = _ready_state(runner)
        url = state.get('public_url', '')
        if not isinstance(url, str) or not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com/v1/[A-Za-z0-9_-]{20,100}', url) or not url.endswith('/v1/' + state['token']):
            raise RuntimeError('연결 주소가 저장되지 않았습니다. 위 로그를 확인하고 4번 셀을 다시 실행해주세요.')
        label = ('기본 목소리 20종 · 내 코랩 주소'
                 if runner.name == 'qwen_voicebank_colab_server.py' else 'Qwen3-TTS · 내 코랩 주소')
        print('\n✅ 프로그램 연결 주소 — 아래 한 줄 전체를 복사하세요.', flush=True)
        print(url, flush=True)
        print(f'공유 사이트 왼쪽 「{label}」 칸에 붙여넣고 연결 확인을 누르세요.', flush=True)
    # Return None so the notebook does not replace useful output with CompletedProcess.
