# GPT 연결 복구 v2.8.5 · 1번을 완료한 기존 코랩에서 실행
from pathlib import Path
import hashlib, urllib.request, runpy

RUNNER = Path('/content/gpt_sovits_v4_runner.py')
EXPECTED = 'af26df7ebfa603d87435365e1a22a3ca041e07f530cbeafdef3d6b72a43a5ddb'
SOURCE_URL = 'https://raw.githubusercontent.com/ssss2513-cyber/ai-audio-studio/7da120118fbf8b4feae3ef3cdb297326c300a3e3/gpt_sovits_colab_server.py'
print('연결 확인 코드만 갱신합니다. 패키지 설치와 음성 생성은 실행하지 않습니다.', flush=True)
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
