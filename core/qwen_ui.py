"""Qwen-specific setup, gender-preserving voices and instruction controls."""
from pathlib import Path
import time

import streamlit as st

from .personal_colab import connection_url
from .qwen_client import QWEN_VOICES, connection_status, close_worker_client
from .tts_engine import TTSEngine, VoiceConfig, VOICE_STYLES
from .voice_recommendations import preview_text, resolve_style, style_display_label, style_description

ROOT = Path(__file__).resolve().parent.parent
QWEN_NOTEBOOK_REVISION = "567ce736edefed7edfa037cbe1a2ebbea8aef69c"  # Colab v1.0.1: visible progress and connection URL.


def render_qwen_connection(prominent=False):
    with st.expander("🗣️ Qwen3-TTS 설정 · 내 코랩 연결", expanded=prominent):
        st.caption("1.7B CustomVoice · 한국어 · 감정·말투 지시 · 참조 녹음 불필요")
        st.markdown("**1 → 2 → 4번 셀 순서로 실행하세요.**\n\n"
                    "1. 아래 코랩을 열고 **런타임 → 런타임 유형 변경 → T4 GPU**를 선택합니다.\n"
                    "2. **1번 설치**, **2번 모델 준비**를 차례로 실행하고 준비 완료를 기다립니다.\n"
                    "3. **4번 사이트 연결**을 실행하고 나온 **프로그램 연결 주소 전체**를 아래에 넣습니다.")
        st.link_button("Qwen3-TTS 코랩 v1.0.1 열기 ↗",
                       "https://colab.research.google.com/github/ssss2513-cyber/ai-audio-studio/blob/"
                       + QWEN_NOTEBOOK_REVISION + "/Qwen3_TTS_Colab_API.ipynb", use_container_width=True)
        notebook = ROOT / "Qwen3_TTS_Colab_API.ipynb"
        if notebook.is_file():
            st.download_button("⬇️ Qwen 코랩 v1.0.1 받기", notebook.read_bytes(),
                               file_name="Qwen3_TTS_CustomVoice_Colab_v1.0.1.ipynb",
                               mime="application/x-ipynb+json", use_container_width=True)
        st.caption("v1.0.1은 진행 로그와 연결 주소를 셀에 직접 표시합니다. 이미 열어둔 이전 코랩에는 자동 적용되지 않으므로 위 수정본을 열어주세요.")
        st.caption("Qwen은 전용 코랩에서 실행하세요. 같은 GPU에 Cosy/GPT 모델도 올리면 메모리가 부족할 수 있습니다.")
        st.caption("무료 코랩은 외부 웹 화면 위주 사용이 제한됩니다. 무료로 코랩 안에서 사용하려면 "
                   "사이트의 ‘Qwen 코랩 직접 생성용 대본 받기’로 파일을 받고, 4번 대신 3번 셀에 올리세요. "
                   "[Colab 안내](https://research.google.com/colaboratory/faq.html)")
        key = "input_qwen_url"
        if key not in st.session_state:
            st.session_state[key] = st.session_state.get("qwen_url", "")
        value = st.text_input("Qwen3-TTS · 내 코랩 주소", key=key, type="password",
                              placeholder="4번 셀의 https://…/v1/… 전체 주소")
        try:
            url = connection_url(value, "qwen")
        except ValueError as exc:
            url = ""
            st.warning(str(exc))
        if st.session_state.get("qwen_url") != url:
            st.session_state.pop("checked_qwen_url", None)
        st.session_state["qwen_url"] = url
        if st.button("Qwen 연결 확인", disabled=not url, use_container_width=True):
            with st.spinner("Qwen 모델 준비 상태 확인 중…"):
                st.session_state["checked_qwen_url"] = connection_status(url)
        checked = st.session_state.get("checked_qwen_url")
        if checked:
            (st.success if checked[0] else st.error)(checked[1])
        st.caption("모델은 한 번만 올려 재사용합니다. 최초 음성 생성 시간과 처리 속도는 GPU·대사 길이에 따라 달라집니다.")
        st.caption("주소와 접속 코드는 이 접속에서만 사용합니다. 다른 이용자는 본인 코랩 주소를 넣어주세요.")


def _change_gender(speaker):
    config = st.session_state["voice_settings"][speaker]
    gender = st.session_state[f"qwen_gender_{speaker}"]
    voice = st.session_state.get(f"qwen_voice_{speaker}", config.get("voice"))
    if QWEN_VOICES.get(voice, {}).get("gender") != gender:
        voice = "Uncle_Fu" if gender == "남성" else "Sohee"
    config.update(voice=voice, gender=gender)
    config["gender_needs_review"] = False
    st.session_state[f"qwen_voice_{speaker}"] = voice


def render_qwen_voice(speaker, current, *, work_dir, segments, busy):
    default_voice = current.get("voice", "Sohee")
    gender = QWEN_VOICES.get(default_voice, QWEN_VOICES["Sohee"])["gender"]
    if current.get("gender_needs_review"):
        st.caption("성별 단서가 없어 Sohee를 임시 선택했습니다. 이 화자에 맞는 성우 성별을 확인해주세요.")
    selected_gender = st.radio("성우 성별", ["남성", "여성"], horizontal=True,
                               index=0 if gender == "남성" else 1, key=f"qwen_gender_{speaker}",
                               on_change=_change_gender, args=(speaker,))
    voices = [name for name, info in QWEN_VOICES.items() if info["gender"] == selected_gender]
    voice_key = f"qwen_voice_{speaker}"
    if st.session_state.get(voice_key, default_voice) not in voices:
        st.session_state[voice_key] = "Uncle_Fu" if selected_gender == "남성" else "Sohee"
    voice = st.selectbox("Qwen 목소리", voices, key=voice_key,
                         index=voices.index(default_voice) if default_voice in voices else 0,
                         format_func=lambda name: QWEN_VOICES[name]["name"])
    st.caption("한국어 전용 기본 보이스는 여성 Sohee입니다. 다른 보이스도 한국어를 읽지만 발음·억양은 다를 수 있습니다.")
    style = resolve_style(current.get("style", "🎤 기본"), VOICE_STYLES)
    styles = list(VOICE_STYLES)
    saved_style = st.session_state.get(f"style_select_{speaker}", style)
    canonical_style = resolve_style(saved_style, VOICE_STYLES, fallback=style)
    if saved_style != canonical_style:
        st.session_state[f"style_select_{speaker}"] = canonical_style
    selected_style = st.selectbox("🎨 음성 스타일", styles, index=styles.index(canonical_style),
                                  key=f"style_select_{speaker}", format_func=style_display_label)
    selected_style = resolve_style(selected_style, VOICE_STYLES, fallback=canonical_style)
    st.caption(style_description(selected_style, VOICE_STYLES))
    instruction = st.text_area("추가 스타일 지시 (선택)", value=current.get("qwen_instruction", ""),
                               key=f"qwen_instruction_{speaker}", max_chars=1200, height=100,
                               placeholder="예: 차분하고 따뜻하게, 문장 끝을 또렷하게 읽어주세요.")
    st.caption("스타일은 읽을 대사와 분리해 전달합니다. 감정·말투의 반영 정도는 보이스와 대사에 따라 다르며, 시니어 스타일은 실제 나이를 보장하지 않습니다.")
    st.session_state["voice_settings"][speaker] = dict(engine="qwen", voice=voice,
        gender=selected_gender, style=selected_style, qwen_instruction=instruction,
        gender_needs_review=current.get("gender_needs_review", False))
    if st.button(f"🔊 {speaker} Qwen 미리듣기", key=f"preview_btn_{speaker}",
                 disabled=busy, use_container_width=True):
        path = None
        url = st.session_state.get("qwen_url", "")
        try:
            ok, message = connection_status(url)
            if not ok:
                raise ValueError(message)
            path = Path(work_dir) / f"preview_qwen_{time.time_ns()}.wav"
            config = VoiceConfig(engine="qwen", voice=voice, style=selected_style,
                                 qwen_url=url, qwen_instruction=instruction)
            sample = preview_text(speaker, segments)
            with st.spinner(f"{speaker} · Qwen 스타일 음성 생성 중…"):
                TTSEngine.generate_preview(config, output_file=str(path), sample_text=sample)
            st.audio(path.read_bytes(), format="audio/wav")
            st.caption("미리듣기 대사: " + sample)
        except Exception as exc:
            st.error(str(exc).replace(url, "[내 코랩 주소]") if url else str(exc))
        finally:
            close_worker_client()
            if path is not None:
                path.unlink(missing_ok=True)
