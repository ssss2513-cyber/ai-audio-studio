# CosyVoice v2.9.16 계산 속도 업데이트 · 기존 코랩의 ＋코드 셀에서 실행
# 생성 완료 또는 저장 후 멈춘 뒤 실행하세요. 기존 설치·모델 파일을 재사용합니다.
# CPU 중첩 병렬을 제한하고, 실제 대사의 처리 속도에 맞춰 동시 1~4개를 선택합니다.
# 별도 시험 음성을 만들지 않으며 모든 완료 대사를 저장합니다.
# 빈자리를 계속 채우고 최종 결과는 대사 순서대로 합칩니다.
# 모델·참고 목소리·FP32·샘플링·출력 음질 설정을 유지합니다.
# 마지막에 나온 새 프로그램 연결 주소를 공유 사이트의 CosyVoice 주소 칸에 넣어주세요.
from pathlib import Path
import hashlib
import runpy
import urllib.request

RUNNER = Path('/content/ai_voice_cosy_runner.py')
REVISION = '82a34f8e3f0f9c80d2881b7f11512139f2893184'
EXPECTED = 'e50820649b731a4df4f3c0c930856c2582f35598a52838f2507446196e7ba69a'
if not Path('/content').is_dir():
    raise RuntimeError('지금 사용 중인 Google Colab의 ＋코드 셀에 붙여 넣고 실행해주세요.')
print('1/3 CosyVoice 계산 속도 수정 파일 확인 중…', flush=True)
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
