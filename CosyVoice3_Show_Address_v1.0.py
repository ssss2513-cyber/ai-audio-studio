# CosyVoice 3 주소 다시 표시 · 기존 4번의 ■를 누르고 새 코드 셀에서 실행
# 현재 모델과 생성 파일을 유지합니다. 설치·음성 생성을 실행하지 않습니다.
from pathlib import Path
from runpy import run_path
import sys

ROOT = Path('/content/voice_studio_cosy3_v1')
if not (ROOT / 'cosy3_colab_server.py').is_file():
    raise RuntimeError('CosyVoice 3의 1번·2번을 실행한 코랩에서 사용해주세요.')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Run in the notebook process so the connection address is actually visible.
server = run_path(str(ROOT / 'cosy3_colab_server.py'))
server['tunnel']()
print('\n위의 https:// 주소 전체를 사이트의 ‘CosyVoice 3 · 내 코랩 주소’에 넣으세요.', flush=True)
print('주소가 나온 뒤에는 이 셀이 계속 실행되는 것이 정상입니다.', flush=True)
run_path(str(ROOT / 'colab_session.py'))['supervise']('cosyvoice3')
