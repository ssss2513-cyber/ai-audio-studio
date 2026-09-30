# CosyVoice v2.9.7 속도 개선 · 사용 중인 코랩의 ＋코드 셀에서 실행
# 음성 생성이 끝난 뒤 실행하세요. FP32·목소리·생성 설정과 모델 파일을 유지합니다.
# 첫 서버 준비 때 음향 인코더 가속을 준비하며, 준비 실패 시 기본 방식으로 계속합니다.
from pathlib import Path
import hashlib
import runpy
import urllib.request

RUNNER = Path('/content/ai_voice_cosy_runner.py')
REVISION = '2512b461285728f9a72e8ef1492e880f90ee4023'
EXPECTED = 'b1e36e5384824603861a442203dd3dead2eb596d9ef86c2557c56eb98de26d50'
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
