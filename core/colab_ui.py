"""Personal Colab connection steps for visitors of a shared Streamlit site."""
from datetime import datetime
from pathlib import Path

import streamlit as st

from .personal_colab import clear_session, connection_url
from .tts_engine import TTSEngine

ROOT = Path(__file__).resolve().parent.parent
# Refresh this release revision whenever either notebook is updated.
# The pinned link opens the same named notebook that the download button serves.
NOTEBOOK_RELEASE_REVISION = "86f931695dc01e443b80d36537089cfb7434d2b2"
ENGINES = {
    "gpt-sovits": ("GPT-SoVITS v4", "gpt_sovits_url", "input_gpt_sovits_url", TTSEngine.test_gpt_sovits_connection),
    "cosyvoice": ("CosyVoice 2", "cosyvoice_url", "input_cosyvoice_url", TTSEngine.test_cosyvoice_connection),
}


def render_connections(mode):
    selected = list(ENGINES) if mode == "custom" else [mode]
    selected = [engine for engine in selected if engine in ENGINES]
    st.markdown("#### ☁️ 내 구글 코랩 연결")
    st.info("이용자마다 본인 구글 계정으로 코랩을 실행합니다. 내 코랩 주소를 아래에 넣어주세요.")
    with st.expander("처음 이용할 때 · 연결 순서", expanded=not any(st.session_state.get(ENGINES[e][1]) for e in selected)):
        st.markdown(
            "1. 아래에서 사용할 엔진의 **코랩 바로 열기**를 누릅니다.\n"
            "2. 본인 구글 계정으로 로그인합니다. 파일로 받았다면 **파일 → 노트북 업로드**로 엽니다.\n"
            "3. **런타임 → 런타임 유형 변경 → T4 GPU**를 선택하고 **1번 설치 셀**을 실행합니다.\n"
            "4. 준비 완료 후 **4번 연결 셀의 ▶**를 누릅니다. 연결 주소가 나온 뒤 버튼이 ▶로 돌아오는 것은 정상입니다.\n"
            "5. 출력된 **프로그램 연결 주소 전체**를 아래에 붙여넣고 **연결 확인**을 누릅니다.\n"
            "6. 대본과 참조 음성을 등록한 뒤 **미리듣기 → 전체 생성 → 다운로드** 순서로 사용합니다."
        )
        st.caption("코랩의 2·3번 셀은 코랩 안에서 직접 음성을 만들고 들을 때 사용합니다.")
        st.caption("설치 중인 코랩 화면을 유지하고, 새 세션을 시작했다면 새 연결 주소를 입력해주세요.")
    notebooks = []
    if "gpt-sovits" in selected:
        notebooks.append(("GPT-SoVITS 코랩 v2.8.3 받기", "GPT_SoVITS_Colab_API.ipynb", "GPT_SoVITS_Colab_v2.8.3.ipynb"))
    if "cosyvoice" in selected:
        notebooks.append(("CosyVoice 코랩 v2.9.4 받기", "CosyVoice_Colab_API.ipynb", "CosyVoice_Colab_v2.9.4.ipynb"))
    st.caption("이전에 열어둔 코랩이나 Drive 복사본은 자동 업데이트되지 않습니다. 아래 버전이 표시된 버튼으로 새로 열어주세요.")
    for label, filename, download_name in notebooks:
        st.link_button(label.replace(" 받기", " 바로 열기 ↗"),
                       "https://colab.research.google.com/github/ssss2513-cyber/ai-audio-studio/blob/" + NOTEBOOK_RELEASE_REVISION + "/" + filename,
                       use_container_width=True)
        path = ROOT / filename
        if path.is_file():
            st.download_button("⬇️ " + label, data=path.read_bytes(), file_name=download_name,
                               mime="application/x-ipynb+json", key="notebook_" + filename,
                               use_container_width=True)
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
