# CosyVoice 2 음성 끊김 수정 v2.9.18 · 기존 코랩의 ＋코드 셀에서 실행
# 먼저 사이트 생성을 저장 후 멈추고, 코랩 4번의 ■를 누르세요.
# 문장 분할·생성 완료 검사·공유 음향 계산 보호를 적용합니다.
# 기존 설치·모델은 재사용하며 시험 음성을 만들지 않습니다.
# 이미 생성한 MP3는 자동 복구되지 않습니다.
# 마지막에 나온 새 주소를 사이트 CosyVoice 2 주소 칸에 넣어주세요.
from pathlib import Path
import hashlib
import runpy
import urllib.request

RUNNER = Path('/content/ai_voice_cosy_runner.py')
REVISION = '9d6d03c3b5401d77ac327d4a8268d039ae9fbbdf'
EXPECTED = 'a44ccc710ab1e595ffe775a0212cedc594acd25a59efb8a5c057cd7081a0107c'
if not Path('/content').is_dir():
    raise RuntimeError('지금 사용 중인 Google Colab의 ＋코드 셀에 붙여 넣고 실행해주세요.')
print('1/3 CosyVoice 음성 끊김 수정 파일 확인 중…', flush=True)
contents = RUNNER.read_bytes() if RUNNER.is_file() else b''
if hashlib.sha256(contents).hexdigest() != EXPECTED:
    source_url = ('https://raw.githubusercontent.com/ssss2513-cyber/ai-audio-studio/'
                  + REVISION + '/colab_server.py')
    with urllib.request.urlopen(source_url, timeout=30) as response:
        contents = response.read(1024 * 1024)
    if hashlib.sha256(contents).hexdigest() != EXPECTED:
        raise RuntimeError('수정 파일 확인에 실패했습니다. 실행하지 않았습니다.')
    temporary = RUNNER.with_suffix('.next.py')
    temporary.write_bytes(contents)
    temporary.replace(RUNNER)

runner_api = runpy.run_path(str(RUNNER))
print('2/3 기존 설치·모델을 확인하고 수정 서버 준비 중…', flush=True)
runner_api['setup']()
print('3/3 사이트 연결 주소 준비 중…', flush=True)
runner_api['tunnel']()
print('✅ 준비 완료. 위 프로그램 연결 주소 전체를 사이트의 CosyVoice 코랩 주소 칸에 넣어주세요.', flush=True)
