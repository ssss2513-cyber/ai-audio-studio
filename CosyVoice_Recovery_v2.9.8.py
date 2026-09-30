# CosyVoice v2.9.8 속도 개선 · 사용 중인 코랩의 ＋코드 셀에서 실행
# 음성 생성이 끝난 뒤 실행하세요. FP32·목소리·생성 설정과 모델 파일을 유지합니다.
# 발음 후보 전송을 묶고 GPU 메모리를 재사용합니다. 발음 선택 기준·계산 정밀도는 유지합니다.
# 실제 소요 시간은 GPU와 대사 길이에 따라 달라집니다.
from pathlib import Path
import hashlib
import runpy
import urllib.request

RUNNER = Path('/content/ai_voice_cosy_runner.py')
REVISION = 'b5ac49bff1dc0b1b8f019e689f84db32dc3751d5'
EXPECTED = '92cbbb7d5f5cfab775c2c4d6532149aedff77960df3f367e701b09f586df4850'
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
