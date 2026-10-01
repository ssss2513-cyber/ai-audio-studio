# CosyVoice v2.9.15 최대 4개 연속 생성 · 기존 코랩의 ＋코드 셀에서 실행
# 진행 중인 생성이 끝나거나 저장 후 멈춘 뒤 실행하세요.
# 첫 대사를 저장한 뒤 최대 4개를 채우고, 하나가 끝나면 즉시 다음 대사를 시작합니다.
# 2개가 끝나면 다음 2개를 채웁니다. 나머지가 끝나거나 파일 전송이 끝날 때까지 묶어 기다리지 않습니다.
# GPU 메모리가 부족할 때만 동시 수를 낮춥니다. 모델·목소리·FP32·말하기 속도는 유지합니다.
# 설치·모델을 재사용하며, 마지막에 나온 새 연결 주소를 사이트에 넣어주세요.
from pathlib import Path
import hashlib
import runpy
import urllib.request

RUNNER = Path('/content/ai_voice_cosy_runner.py')
REVISION = '63aa393594aa0c5224ddf3b9a5a976e0c0125871'
EXPECTED = '356aad8ac0ace3c30d4e38db194efc2b76d36bed466ac8372c511f975fd3f06e'
if not Path('/content').is_dir():
    raise RuntimeError('지금 사용 중인 Google Colab의 ＋코드 셀에 붙여 넣고 실행해주세요.')
print('1/3 CosyVoice 최대 4개 설정 파일 확인 중…', flush=True)
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
