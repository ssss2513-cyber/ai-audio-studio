"""Personal Colab connection steps for visitors of a shared Streamlit site."""
from datetime import datetime
from pathlib import Path

import streamlit as st

from .personal_colab import clear_session, connection_url
from .tts_engine import TTSEngine

ROOT = Path(__file__).resolve().parent.parent
ENGINES = {
    "gpt-sovits": ("GPT-SoVITS v4", "gpt_sovits_url", "input_gpt_sovits_url", TTSEngine.test_gpt_sovits_connection),
    "cosyvoice": ("CosyVoice 2", "cosyvoice_url", "input_cosyvoice_url", TTSEngine.test_cosyvoice_connection),
    "xtts": ("XTTS v2", "xtts_url", "input_xtts_url", TTSEngine.test_xtts_connection),
}


def render_connections(mode):
    selected = list(ENGINES) if mode == "custom" else [mode]
    selected = [engine for engine in selected if engine in ENGINES]
    st.markdown("#### ☁️ 내 구글 코랩 연결")
    st.info("이용자마다 본인 구글 계정으로 코랩을 실행합니다. 내 코랩 주소를 아래에 넣어주세요.")
    with st.expander("처음 이용할 때 · 연결 순서", expanded=not any(st.session_state.get(ENGINES[e][1]) for e in selected)):
        st.markdown(
            "1. 아래에서 사용할 엔진의 **코랩 파일을 다운로드**합니다.\n"
            "2. **Google Colab 열기** → 본인 구글 계정 로그인 → **파일 → 노트북 업로드**로 파일을 엽니다.\n"
            "3. **런타임 → 런타임 유형 변경 → T4 GPU**를 선택하고 **1번 설치 셀**을 실행합니다.\n"
            "4. 준비 완료 후 **4번 연결 셀**에서 연결 항목을 체크하고 실행합니다.\n"
            "5. 출력된 **프로그램 연결 주소 전체**를 아래에 붙여넣고 **연결 확인**을 누릅니다.\n"
            "6. 대본과 참조 음성을 등록한 뒤 **미리듣기 → 전체 생성 → 다운로드** 순서로 사용합니다."
        )
        st.caption("코랩의 2·3번 셀은 코랩 안에서 직접 음성을 만들고 들을 때 사용합니다.")
        st.caption("설치 중인 코랩 화면을 유지하고, 새 세션을 시작했다면 새 연결 주소를 입력해주세요.")
    notebooks = []
    if "gpt-sovits" in selected:
        notebooks.append(("GPT-SoVITS v4 코랩 받기", "GPT_SoVITS_Colab_API.ipynb"))
    if any(e in selected for e in ("cosyvoice", "xtts")):
        notebooks.append(("CosyVoice + XTTS 코랩 받기", "CosyVoice_XTTS_Colab_API.ipynb"))
    for label, filename in notebooks:
        path = ROOT / filename
        if path.is_file():
            st.download_button("⬇️ " + label, data=path.read_bytes(), file_name=filename,
                               mime="application/x-ipynb+json", key="notebook_" + filename,
                               use_container_width=True)
    st.link_button("Google Colab 열기 ↗", "https://colab.research.google.com/", use_container_width=True)
    if mode in ("cosyvoice", "xtts"):
        st.caption("통합 코랩 1번에서 지금 사용할 엔진만 선택할 수 있습니다.")
    if "xtts" in selected:
        st.caption("XTTS 공개 모델은 비상업용입니다. [이용 조건](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt)")
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
