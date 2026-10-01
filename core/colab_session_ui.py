"""Stateless Colab connection controls with notebook session supervision."""
from datetime import datetime
from pathlib import Path

import streamlit as st

from .personal_colab import clear_session, connection_url
from .tts_engine import TTSEngine
from .qwen_bank_connection import CONNECTION_REPORT_VERSION, connection_report

ROOT = Path(__file__).resolve().parent.parent
# Refresh this release revision whenever either notebook is updated.
# The pinned link opens the same named notebook that the download button serves.
NOTEBOOK_RELEASE_REVISION = "a732065587eb4c8620bc4726062819469977cd45"
COSY_NOTEBOOK_RELEASE_REVISION = "a732065587eb4c8620bc4726062819469977cd45"
ENGINES = {
    "gpt-sovits": ("GPT-SoVITS v4", "gpt_sovits_url", "input_gpt_sovits_url", TTSEngine.test_gpt_sovits_connection),
    "cosyvoice": ("CosyVoice 2", "cosyvoice_url", "input_cosyvoice_url", TTSEngine.test_cosyvoice_connection),
}


def render_connections(mode):
    selected = list(ENGINES) if mode == "custom" else [mode]
    selected = [engine for engine in selected if engine in ENGINES]
    st.markdown("#### ☁️ 내 구글 코랩 연결")
    st.caption("코랩 연결 화면 v2.9.41 · 서버 감독 적용")
    st.info("이용자마다 본인 구글 계정으로 코랩을 실행합니다. 내 코랩 주소를 아래에 넣어주세요.")
    with st.expander("처음 이용할 때 · 연결 순서", expanded=not any(st.session_state.get(ENGINES[e][1]) for e in selected)):
        st.markdown(
            "1. 아래에서 사용할 엔진의 **코랩 바로 열기**를 누릅니다.\n"
            "2. 본인 구글 계정으로 로그인합니다. 파일로 받았다면 **파일 → 노트북 업로드**로 엽니다.\n"
            "3. **런타임 → 런타임 유형 변경 → T4 GPU**를 선택하고 **1번 설치 셀**을 실행합니다.\n"
            "4. 준비 완료 후 **4번 연결 셀의 ▶**를 누릅니다. 주소가 나온 뒤에도 **4번 셀을 실행 상태로 둡니다.**\n"
            "5. 출력된 **프로그램 연결 주소 전체**를 아래에 붙여넣고 **연결 확인**을 누릅니다.\n"
            "6. 대본과 참조 음성을 등록한 뒤 **미리듣기 → 전체 생성 → 다운로드** 순서로 사용합니다."
        )
        st.caption("코랩의 2·3번 셀은 코랩 안에서 직접 음성을 만들고 들을 때 사용합니다.")
        st.caption("설치 중인 코랩 화면을 유지하고, 새 세션을 시작했다면 새 연결 주소를 입력해주세요.")
    notebooks = []
    if "gpt-sovits" in selected:
        notebooks.append(("GPT-SoVITS 코랩 v2.8.8 받기", "GPT_SoVITS_Colab_API.ipynb", "GPT_SoVITS_Colab_v2.8.8.ipynb"))
        st.caption("GPT 코랩 v2.8.8은 4번에서 준비·연결 후 서버 감독을 계속합니다. 음성 서버는 기존 v2.8.7을 사용합니다.")
        recovery = ROOT / "GPT_Connection_Recovery_v2.8.7.py"
        if recovery.is_file():
            with st.expander("GPT 업데이트 후 연결 복구 · 기존 코랩에서 실행"):
                st.write("이전에 사용하던 코랩의 ＋코드에 아래 코드를 붙여넣고 실행하세요.")
                st.caption("기존 서버와 설치 파일을 먼저 이어 쓰고, 설치가 없는 경우에만 GPU 확인 후 준비합니다.")
                st.code(recovery.read_text(encoding="utf-8") + "\n" + session_patch_code('gpt-sovits'), language="python")
    if "cosyvoice" in selected:
        notebooks.append(("CosyVoice 코랩 v2.9.17 받기", "CosyVoice_Colab_API.ipynb", "CosyVoice_Colab_v2.9.17.ipynb"))
        st.caption("Cosy 코랩 v2.9.17은 4번 서버 감독을 추가했습니다. 음성 서버 v2.9.16의 동시 1~4개 조절·모델·FP32·샘플링 설정은 유지합니다.")
        recovery = ROOT / "CosyVoice_Recovery_v2.9.16.py"
        if recovery.is_file():
            with st.expander("CosyVoice v2.9.16 계산 속도 업데이트 · 기존 코랩에서 실행"):
                st.write("음성 생성이 멈췄거나 끝난 뒤, 지금 쓰는 코랩의 ＋코드에 아래 코드를 붙여 넣고 실행하세요.")
                st.caption("실행 중인 기존 설치·모델을 재사용합니다. 마지막에 나온 새 연결 주소를 아래 칸에 넣어주세요.")
                st.code(recovery.read_text(encoding="utf-8") + "\n" + session_patch_code('cosyvoice'), language="python")
        st.caption("첫 대사로 메모리를 확인한 뒤 동시 2개부터 시작합니다. 현재 대본의 완료 속도를 비교해 동시 수를 조절하며, 비교에 쓰인 대사도 모두 결과로 저장합니다. 빈자리가 나면 다음 대사를 바로 시작하고 최종 음성은 번호순으로 합칩니다.")
    st.caption("이전에 열어둔 코랩이나 Drive 복사본은 자동 업데이트되지 않습니다. 처음 이용할 때는 아래 버전이 표시된 버튼으로 열어주세요.")
    for label, filename, download_name in notebooks:
        revision = COSY_NOTEBOOK_RELEASE_REVISION if filename == "CosyVoice_Colab_API.ipynb" else NOTEBOOK_RELEASE_REVISION
        st.link_button(label.replace(" 받기", " 바로 열기 ↗"),
                       "https://colab.research.google.com/github/ssss2513-cyber/ai-audio-studio/blob/" + revision + "/" + filename,
                       use_container_width=True)
        path = ROOT / filename
        if path.is_file():
            st.download_button("⬇️ " + label, data=path.read_bytes(), file_name=download_name,
                               mime="application/x-ipynb+json", key="notebook_" + filename,
                               use_container_width=True)
    for engine in selected:
        render_session_patch(engine)
    st.link_button("Google Colab 열기 ↗", "https://colab.research.google.com/", use_container_width=True)
    for engine in selected:
        label, setting, widget, checker = ENGINES[engine]
        if widget not in st.session_state:
            st.session_state[widget] = st.session_state.get(setting, "")
        value = st.text_input(label + " · 내 코랩 주소", key=widget, type="password",
                              placeholder="코랩의 프로그램 연결 주소 전체를 붙여넣으세요",
                              help="본인 코랩에서 나온 주소를 사용하세요. 주소에 포함된 접속 코드를 다른 사람에게 보내지 마세요.")
        try:
            url = connection_url(value, engine)
        except ValueError as exc:
            url = ""
            st.warning(str(exc))
        if st.session_state.get(setting) != url:
            st.session_state.pop("checked_" + setting, None)
        st.session_state[setting] = url
        if st.button(label + " 연결 확인", key="connect_" + setting, disabled=not url, use_container_width=True):
            with st.spinner(label + " 준비 상태 확인 중…"):
                ok, message = checker(url)
            st.session_state["checked_" + setting] = (ok, message, datetime.now().strftime("%H:%M"))
        result = st.session_state.get("checked_" + setting)
        if result:
            ok, message, checked_at = result
            (st.success if ok else st.error)(message)
            st.caption("마지막 연결 확인 " + checked_at + " · 코랩이 종료되면 다시 연결해주세요.")
    st.caption("참조 음성과 대사는 이 사이트 서버를 거쳐 본인 코랩으로 전송됩니다.")
    st.caption("새로고침이나 접속 종료로 연결 설정·작업 화면이 초기화될 수 있습니다. 결과는 작업 중 다운로드해주세요.")
    st.caption("무료 코랩은 외부 웹 화면을 통한 사용이 제한되어 연결이 종료될 수 있습니다. "
               "그때는 코랩 3번 셀에서 직접 생성할 수 있습니다. "
               "[Colab 안내](https://research.google.com/colaboratory/faq.html)")


def render_reset():
    st.button("내 연결·작업 지우기", on_click=clear_session, args=(st.session_state,),
              help="현재 접속에서 입력한 설정과 생성한 파일을 지웁니다. 필요한 결과를 먼저 다운로드해주세요.",
              use_container_width=True)


def session_patch_code(engine):
    code_file = ROOT / 'Colab_Session_Recovery_v1.0.py'
    if not code_file.is_file():
        return ''
    return code_file.read_text(encoding='utf-8').replace('ENGINE = "qwen-bank"', 'ENGINE = "' + engine + '"')


def render_session_patch(engine, nested=False):
    code = session_patch_code(engine)
    if not code:
        return
    label = {'qwen-bank': 'Qwen 20종', 'cosyvoice': 'CosyVoice', 'gpt-sovits': 'GPT-SoVITS'}[engine]
    def content():
        st.markdown('**이미 연결한 코랩에 적용하기**')
        st.write('현재 코랩의 ＋코드에 아래 코드를 복사해 실행하세요. 설치와 모델을 다시 불러오지 않고 현재 서버 상태를 확인합니다.')
        st.caption('이 셀이 실행 중인 상태로 사이트를 사용하세요. 다른 셀을 쓸 때는 먼저 ■로 감독을 중단하세요. 감독 중단은 음성 서버를 끄지 않습니다.')
        st.code(code, language='python')
        st.caption('외부 연결 도구가 종료된 경우에만 연결을 복구합니다. 새 주소가 나오면 사이트 주소를 바꿔주세요. 코랩 자체의 사용 시간 제한을 변경하지는 않습니다.')
    if nested:
        if st.checkbox('현재 Qwen 코랩에 서버 감독 적용 코드 보기', key='show_qwen_session_patch'):
            content()
    else:
        with st.expander(label + ' · 현재 코랩에 서버 감독 적용'):
            content()


def render_qwen_bank_connection(prominent=False):
    if st.session_state.get('_qwen_bank_report_version') != CONNECTION_REPORT_VERSION:
        # Discard the earlier ambiguous message without touching the address,
        # speaker assignments, generated files or the user's Colab process.
        st.session_state.pop('checked_qwen_bank_url', None)
        st.session_state.pop('qwen_bank_connection_details', None)
        st.session_state['_qwen_bank_report_version'] = CONNECTION_REPORT_VERSION
    with st.expander('🎭 기본 목소리 20종 · 전용 코랩 연결', expanded=prominent):
        st.caption('20종 연결 화면 v2.9.41 · 서버 감독 적용')
        st.markdown('**남성 10개 + 여성 10개 · 녹음 업로드·Gemini 키 불필요**')
        st.caption('코랩이 선택한 목소리를 처음 한 번 만들고 저장한 뒤, 같은 목소리로 대사를 읽습니다.')
        st.markdown('1. 아래 코랩을 열고 **런타임 → 런타임 유형 변경 → T4 GPU**를 선택합니다.\n'
                    '2. **1번 설치 → 2번 서버 준비 → 4번 사이트 연결**을 실행합니다.\n'
                    '3. 나온 **프로그램 연결 주소 전체**를 아래에 넣고 **4번 셀은 실행 상태로 둡니다.**')
        st.link_button('20종 전용 코랩 v1.0.3 열기 ↗',
            'https://colab.research.google.com/github/ssss2513-cyber/ai-audio-studio/blob/'
            + NOTEBOOK_RELEASE_REVISION + '/Qwen3_TTS_VoiceBank_Colab_API.ipynb', use_container_width=True)
        notebook = ROOT / 'Qwen3_TTS_VoiceBank_Colab_API.ipynb'
        if notebook.is_file():
            st.download_button('⬇️ 기본 목소리 20종 코랩 v1.0.3 받기', notebook.read_bytes(),
                file_name='Qwen3_TTS_기본목소리20종_Colab_v1.0.3.ipynb',
                mime='application/x-ipynb+json', use_container_width=True)
        st.caption('열린 코랩 맨 위에 ‘Qwen 기본 목소리 20종 전용 · v1.0.3’가 표시됩니다. 이 코랩의 4번 주소를 아래에 넣어주세요.')
        st.caption('Cosy/GPT와 별도의 코랩에서 사용하세요. 같은 GPU에 여러 엔진을 함께 올리지 마세요.')
        st.caption('첫 사용은 모델 다운로드와 선택한 목소리 준비 때문에 추가 시간이 걸립니다. 준비가 끝나면 대사마다 목소리를 다시 만들지 않습니다.')
        st.caption('무료 코랩 안에서 직접 생성하려면 사이트에서 ‘기본 목소리 20종 코랩 대본 받기’를 눌러 파일을 받고, 코랩 1 → 2 → 3번을 실행하세요. '
                   '[외부 웹 연결 제한 안내](https://research.google.com/colaboratory/faq.html)')
        render_session_patch('qwen-bank', nested=True)
        key = 'input_qwen_bank_url'
        if key not in st.session_state:
            st.session_state[key] = st.session_state.get('qwen_bank_url', '')
        value = st.text_input('기본 목소리 20종 · 내 코랩 주소', key=key, type='password',
                              placeholder='새 코랩 4번 셀의 https://…/v1/… 전체 주소')
        try:
            url = connection_url(value, 'qwen-bank')
        except ValueError as exc:
            url = ''
            st.warning(str(exc))
        if st.session_state.get('qwen_bank_url') != url:
            st.session_state.pop('checked_qwen_bank_url', None)
            st.session_state.pop('qwen_bank_connection_details', None)
        st.session_state['qwen_bank_url'] = url
        if st.button('기본 목소리 20종 연결 확인', disabled=not url, use_container_width=True):
            with st.spinner('전용 코랩 확인 중…'):
                ok, message, details = connection_report(url)
                st.session_state['checked_qwen_bank_url'] = (ok, message)
                st.session_state['qwen_bank_connection_details'] = details
        checked = st.session_state.get('checked_qwen_bank_url')
        if checked:
            (st.success if checked[0] else st.error)(checked[1])
            details = st.session_state.get('qwen_bank_connection_details')
            if details:
                st.markdown('**연결 확인 결과**')
                st.code('\n'.join(f'{label}: {answer}' for label, answer in details.items()),
                        language=None, wrap_lines=True)
        st.caption('연결 확인은 음성을 생성하지 않습니다. 연결이 안 되면 서버 종류·모델·목소리 목록 버전을 구분해 표시합니다.')
        st.caption('목소리는 현재 코랩 런타임에 저장됩니다. 코랩 6번 셀에서 보관하면 새 런타임에서도 같은 음성을 복원할 수 있습니다.')
