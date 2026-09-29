# GPT 서버 복구 v2.8.6 · 1번을 완료한 기존 코랩에서 실행
from pathlib import Path
import hashlib, urllib.request, runpy

RUNNER = Path('/content/gpt_sovits_v4_runner.py')
EXPECTED = 'f37f3adf3ab3b2c771c9967962f2c0b5224fd8f5bbc2c272f37067563cdea535'
SOURCE_URL = 'https://raw.githubusercontent.com/ssss2513-cyber/ai-audio-studio/064f3610e4c9bb001c52c3ce13a0753004175ca6/gpt_sovits_colab_server.py'
print('서버 상태를 확인하고 종료됐다면 한 번 다시 시작합니다. 패키지 재설치와 음성 생성은 하지 않습니다.', flush=True)
runner_bytes = RUNNER.read_bytes() if RUNNER.is_file() else b''
if hashlib.sha256(runner_bytes).hexdigest() != EXPECTED:
    with urllib.request.urlopen(SOURCE_URL, timeout=30) as response:
        runner_bytes = response.read(1024 * 1024)
    if hashlib.sha256(runner_bytes).hexdigest() != EXPECTED:
        raise RuntimeError('실행 파일 확인에 실패했습니다. 다운로드 연결을 확인해주세요.')
    RUNNER.write_bytes(runner_bytes)
runner_api = runpy.run_path(str(RUNNER))
runner_api['tunnel']()
print('✅ 위 프로그램 연결 주소 전체를 공유 사이트의 GPT 코랩 주소 칸에 입력하세요.', flush=True)
print('실행 버튼이 ▶로 돌아와도 서버는 계속 실행됩니다.', flush=True)
