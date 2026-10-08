"""Package existing source into self-contained notebooks; never run the app."""
import base64
import json
from pathlib import Path
import textwrap
import zlib

ROOT = Path(__file__).resolve().parent
SOURCES = ('cosy_kaggle_runner.py', 'cosy_kaggle_contract.py', 'colab_server.py',
           'cosy3_colab_server.py', 'cosy3_model.py', 'cosy3_voicebank_catalog.py',
           'cosy_kaggle_queue.py')
payload = json.dumps({name: (ROOT / name).read_text(encoding='utf-8') for name in SOURCES},
                     ensure_ascii=False).encode()
encoded = base64.b64encode(zlib.compress(payload, 9)).decode()


def markdown(text):
    return dict(cell_type='markdown', metadata={}, source=text.splitlines(keepends=True))


def code(text, hidden=False):
    metadata = {'jupyter': {'source_hidden': True}} if hidden else {}
    return dict(cell_type='code', execution_count=None, metadata=metadata, outputs=[],
                source=text.splitlines(keepends=True))


bootstrap = '''import base64, json, os, signal, subprocess, sys, zlib
from pathlib import Path
RUNTIME = Path('/tmp/voice_studio_kaggle_v1')
CODE = RUNTIME / 'code'
CODE.mkdir(parents=True, exist_ok=True)
SOURCE_BUNDLE = (
__BUNDLE__
)
sources = json.loads(zlib.decompress(base64.b64decode(SOURCE_BUNDLE)))
for name, content in sources.items():
    (CODE / name).write_text(content, encoding='utf-8')
RUNNER = CODE / 'cosy_kaggle_runner.py'
PYTHON = RUNTIME / 'venv/bin/python'
ENGINE = __ENGINE__
GPU_COUNT = 2

def run_tool(command, allow_partial=False):
    process = subprocess.Popen([str(arg) for arg in command],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        start_new_session=True)
    try:
        while True:
            try:
                line = process.stdout.readline()
                if line:
                    print(line, end='', flush=True)
                elif process.poll() is not None:
                    break
            except KeyboardInterrupt:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    print('중단 요청을 전달했습니다. 현재 대사를 저장할 때까지 기다려주세요.', flush=True)
        result = process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
    if result == 2 and allow_partial:
        print('완료된 대사는 보관했습니다. 4번 셀에서 resume.zip을 받으세요.')
    elif result:
        raise RuntimeError('작업을 완료하지 못했습니다. 위 오류를 확인해주세요. 종료 코드: ' + str(result))
    return result

print('✅ 1번 준비 완료. 2번 설치를 실행하세요. 아직 모델이나 음성을 실행하지 않았습니다.')
'''
bundle_lines = '\n'.join('    ' + repr(line) for line in textwrap.wrap(encoded, 100))
bootstrap = bootstrap.replace('__BUNDLE__', bundle_lines)

install = '''# 보통 비워두세요. Input에 대본이 여러 개일 때만 정확한 ZIP 또는 plan.json 경로를 넣습니다.
대본파일경로 = ""
PLAN_ARGUMENTS = ['--plan', 대본파일경로] if 대본파일경로.strip() else []
run_tool([sys.executable, '-u', RUNNER, 'setup', '--engine', ENGINE,
          '--gpus', GPU_COUNT, *PLAN_ARGUMENTS])
'''
generate = '''# GPU마다 3개 생성 + 다음 3개 사전 준비. 하나가 끝나면 바로 다음 대사를 시작합니다.
# 기존 완료 파일은 재사용합니다. 다른 노트북 탭에서 동시에 실행하지 마세요.
run_tool([PYTHON, '-u', RUNNER, 'run', '--engine', ENGINE,
          '--gpus', GPU_COUNT, *PLAN_ARGUMENTS], allow_partial=True)
'''
download = '''import json, os
from pathlib import Path
from IPython.display import display, FileLink

working = Path('/kaggle/working')
latest = working / 'voice_studio_results/latest.json'
if not latest.is_file():
    print('아직 생성 작업이 없습니다. 2번 설치를 완료한 뒤 3번을 실행해주세요.')
else:
    info = json.loads(latest.read_text())
    folder = Path(info['folder'])
    print('작업 상태:', info['status'])
    print('결과 폴더:', folder)
    os.chdir(working)
    names = ['resume.zip', 'progress.json']
    if info['status'] == 'complete':
        names = ['full_audio.mp3', 'complete_audio.zip', 'subtitles.srt',
                 'subtitles.vtt', 'full_audio.wav'] + names
    for name in names:
        path = folder / name
        if path.is_file():
            display(FileLink(str(path.relative_to(working))))
    print('링크가 열리지 않으면 오른쪽 Output에서 같은 파일을 내려받으세요.')
    print('새 세션에서 이어하려면 기존 대본 Input을 빼고 resume.zip을 비공개 Input으로 넣습니다.')
'''

for engine, title, filename in (
    ('cosyvoice', 'CosyVoice 2', 'CosyVoice2_Kaggle_DualGPU.ipynb'),
    ('cosyvoice3', 'CosyVoice 3', 'CosyVoice3_Kaggle_DualGPU.ipynb'),
    ('auto', 'CosyVoice 2·3 혼합 대본', 'CosyVoice2_3_Kaggle_DualGPU.ipynb'),
):
    intro = f'''# {title} · 캐글 GPU 2개 · v1.0.2

기존 코랩은 계속 사용할 수 있습니다. 이 노트북은 **캐글에서 대본 전체를 생성하는 추가 옵션**입니다.

## 시작 전

1. 공유 사이트에서 대본 분석 → 화자 설정 → **3번 생성 영역 → 캐글 GPU 2개 → 캐글용 대본·목소리 받기**로 ZIP을 받습니다.
2. Kaggle의 **Settings → Accelerator → GPU T4 ×2**, **Internet ON**을 선택합니다.
3. **Add Input → Upload**로 받은 ZIP을 **비공개**로 추가합니다. ZIP이 자동으로 풀려도 인식합니다.
4. **Run All**로 아래 1~4번을 순서대로 실행합니다. 설치와 모델 다운로드는 첫 실행에 시간이 걸립니다.

GPU마다 모델 하나를 공유해 대사 **3개를 동시 처리**하고, 다음 **3개의 대사·화자·스타일·참고 파일**을 미리 준비합니다.
GPU 2개인 작업 하나에서 최대 **6개 생성 + 다음 6개 준비**입니다. 남은 대사가 적으면 실행 수도 줄어듭니다.
셋이 모두 끝나기를 기다리지 않고, 하나가 끝나면 준비된 다음 대사를 바로 시작합니다.
완료 음성의 파일 저장은 별도 작업으로 넘깁니다. 공유 음향·파형 계산은 충돌을 막기 위해 차례로 처리합니다.
대사마다 사이트로 음성을 전송하지 않고 캐글 안에서 처리한 뒤, 원래 순번대로 **MP3 하나**로 합칩니다.
혼합 대본은 두 GPU로 코지2를 만든 다음 코지3를 만듭니다. 최종 파일은 대본 순서입니다.
위 설명은 이 수동 통합 노트북을 직접 실행할 때의 방식입니다.
공유 사이트 v2.9.54의 혼합 생성 버튼은 코지2·코지3를 별도 캐글 작업으로 동시에 제출합니다.
각 작업은 GPU 2개를 요청하고, 원본 WAV를 사이트에서 대본 순번대로 합쳐 MP3를 한 번만 만듭니다.
화자·스타일·속도·참고 음성과 기존 모델의 FP32 및 생성 설정을 유지합니다.

브라우저를 닫고 배치 실행하려면 Input과 설정을 저장한 뒤 **Save Version → Save & Run All**을 사용하세요.
이 모드는 깨끗한 세션에서 1번부터 다시 실행합니다. 실행 시간·GPU 할당량은 Kaggle 계정 제한을 따릅니다.
완료 시간이나 두 배 속도를 보장하지 않으며, 이 배포에서 실제 GPU 실행이나 음질 테스트는 하지 않았습니다.
'''
    cells = [markdown(intro), markdown('## 1. 실행 파일 준비\n이 셀의 긴 코드는 수정하지 않아도 됩니다.\n'),
             code(bootstrap.replace('__ENGINE__', repr(engine)), hidden=True),
             markdown('## 2. 독립 환경 설치 · 모델 받기\n코랩 파일과 캐글 기본 Python 환경을 바꾸지 않습니다.\n'), code(install),
             markdown('## 3. 전체 음성 생성\n이 셀을 실행하면 실제 대사를 생성합니다. 끝나면 MP3 하나로 합칩니다.\n'
                      '중단 버튼은 진행 중인 대사를 저장한 뒤 멈추도록 요청합니다. 세션 자체가 종료되면 마지막 대사는 저장되지 않을 수 있습니다.\n'),
             code(generate), markdown('## 4. 완성 음성 · 이어하기 파일 받기\n'
                  '**full_audio.mp3**는 전체 음성 한 파일, **complete_audio.zip**은 그 MP3와 자막입니다.\n'
                  '**full_audio.wav**는 MP3 압축 전 음성입니다. **resume.zip**은 이어하기용 개별 결과와 설정입니다.\n'
                  '같은 세션에서는 3번을 다시 실행하면 완료된 대사를 재사용합니다. 새 세션에서는 resume.zip을 Input에 넣고 1번부터 실행하세요.\n'
                  '실패하거나 누락된 대사가 있으면 최종 MP3를 만들지 않고 완료분을 보관합니다.\n'), code(download)]
    notebook = dict(cells=cells, metadata={
        'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
        'language_info': {'name': 'python'},
        'voice_studio': {'version': '1.0.2', 'engine': engine, 'gpu_count': 2,
                         'concurrency_per_gpu': 3, 'prefetch_per_gpu': 3}}, nbformat=4, nbformat_minor=4)
    (ROOT / filename).write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('작성: ' + filename)
