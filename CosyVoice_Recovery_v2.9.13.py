# CosyVoice v2.9.13 연속 생성 업데이트 · 사용 중인 코랩의 ＋코드 셀에서 실행
# 음성 생성이 끝난 뒤 실행하세요. FP32·목소리·생성 설정과 모델 파일을 유지합니다.
# 동시 생성 상한은 10개입니다. 하나가 끝나면 다음 대사를 채우며 32개 묶음 대기를 없앱니다.
# 메모리가 부족하면 동시 수를 줄입니다. Gemini 오류가 나도 Cosy는 계속 생성합니다.
# 모델·목소리·FP32·PCM 설정은 유지합니다.
# 실제 소요 시간은 GPU와 대사 길이에 따라 달라집니다.
from pathlib import Path
import hashlib
import runpy
import urllib.request

RUNNER = Path('/content/ai_voice_cosy_runner.py')
REVISION = '76c53afec316101a818a9c4d8220128a194de8fd'
EXPECTED = '9bcad145cc6f245c8a8867e6887a15fda9154fd942b220bacebb54ff0322828f'
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
