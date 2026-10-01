# 코랩 서버 감독 v1.0 · 실행 중인 서버에 적용 (설치·모델 재시작 없음)
ENGINE = "qwen-bank" #@param ["qwen-bank", "cosyvoice", "gpt-sovits"]
from pathlib import Path
import hashlib, runpy, urllib.request
session_file = Path('/content/voice_studio_colab_session_v1.py')
expected = '76dffa7867fba3ac579a65537e9b35f6f5782b65edc3d11da19f7e2e290e3605'
source_url = 'https://raw.githubusercontent.com/ssss2513-cyber/ai-audio-studio/d035be00f4582f8b5b084d899ffcc6a7537eb7d7/colab_session.py'
source = session_file.read_bytes() if session_file.is_file() else b''
if hashlib.sha256(source).hexdigest() != expected:
    with urllib.request.urlopen(source_url, timeout=30) as response:
        source = response.read(128 * 1024)
    if hashlib.sha256(source).hexdigest() != expected:
        raise RuntimeError('서버 감독 파일 확인에 실패했습니다. 다시 복사해 실행해주세요.')
    session_file.write_bytes(source)
    session_file.chmod(0o600)
runpy.run_path(str(session_file))['supervise'](ENGINE)
