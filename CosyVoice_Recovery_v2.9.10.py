# CosyVoice v2.9.10 속도 개선 · 사용 중인 코랩의 ＋코드 셀에서 실행
# 음성 생성이 끝난 뒤 실행하세요. FP32·목소리·생성 설정과 모델 파일을 유지합니다.
# 완성된 음성을 전송하는 동안 다음 대사를 계산합니다. 모델·목소리·PCM 원음은 유지합니다.
# 실제 소요 시간은 GPU와 대사 길이에 따라 달라집니다.
from pathlib import Path
import hashlib
import runpy
import urllib.request

RUNNER = Path('/content/ai_voice_cosy_runner.py')
REVISION = '9f9beaca51f862b2d9d99d41e608d02d6706e1bd'
EXPECTED = 'a28a0b77d6f667e7ca09a9e5c3fca778c1de68d7477366a99591813b807676cd'
if not Path('/content').is_dir():
    raise RuntimeError('지금 사용 중인 Google Colab의 ＋코드 셀에 붙여 넣고 실행해주세요.')
print('1/3 CosyVoice 속도 개선 파일 확인 중…', flush=True)
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
