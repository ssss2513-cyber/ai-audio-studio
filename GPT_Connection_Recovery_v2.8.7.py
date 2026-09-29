# GPT 준비·연결 복구 v2.8.7 · 현재 사용 중인 코랩에서 실행
from pathlib import Path
import hashlib, urllib.request, runpy

RUNNER = Path('/content/gpt_sovits_v4_runner.py')
EXPECTED = '14d3890c49e7e6894528deac14bd08bf50f36f915a0e5c27820727d4686287a6'
SOURCE_URL = 'https://raw.githubusercontent.com/ssss2513-cyber/ai-audio-studio/4d606ff4907e8b89a8a9134967b71bf4b4f402e1/gpt_sovits_colab_server.py'
print('기존 서버와 설치 파일을 먼저 확인합니다. 설치가 없으면 GPU 확인 후 준비부터 연결까지 진행합니다.', flush=True)
runner_bytes = RUNNER.read_bytes() if RUNNER.is_file() else b''
if hashlib.sha256(runner_bytes).hexdigest() != EXPECTED:
    with urllib.request.urlopen(SOURCE_URL, timeout=30) as response:
        runner_bytes = response.read(1024 * 1024)
    if hashlib.sha256(runner_bytes).hexdigest() != EXPECTED:
        raise RuntimeError('실행 파일 확인에 실패했습니다. 다운로드 연결을 확인해주세요.')
    RUNNER.write_bytes(runner_bytes)
runner_api = runpy.run_path(str(RUNNER))
runner_api['tunnel'](prepare=True)
print('✅ 위 프로그램 연결 주소 전체를 공유 사이트의 GPT 코랩 주소 칸에 입력하세요.', flush=True)
print('실행 버튼이 ▶로 돌아와도 서버는 계속 실행됩니다.', flush=True)
