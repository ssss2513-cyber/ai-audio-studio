"""Session-local setup and voice controls; rendering never calls a TTS API."""
from pathlib import Path
import time

import streamlit as st

from .chirp_client import CHIRP_VOICES, close_worker_client, validate_key
from .tts_engine import TTSEngine, VoiceConfig
from .voice_recommendations import preview_text


def render_chirp_settings(prominent=False):
    with st.expander("☁️ Google Chirp 3 HD 설정 · 코랩 불필요", expanded=prominent):
        st.text_input("Cloud Text-to-Speech API 키", type="password",
                      key="cloud_tts_api_key", placeholder="Google Cloud 콘솔에서 발급한 API 키",
                      help="이 브라우저 세션에만 보관합니다. 다른 방문자의 키와 공유하지 않습니다.")
        st.caption("한국어 30개 보이스 · 최대 4개 연속 생성 · WAV 원음 저장 후 MP3 하나로 합치기")
        st.caption("월 100만 자까지 무료이며 초과분은 과금됩니다. 미리듣기·재생성도 사용량에 포함됩니다.")
        st.markdown("[Google Cloud 사용량·결제 확인](https://console.cloud.google.com/billing)")
        st.markdown("**처음 연결하는 방법 · 3단계**")
        st.markdown(
            "1. [Google Cloud 콘솔](https://console.cloud.google.com/)에서 사용할 프로젝트와 결제 계정을 준비합니다.\n"
            "2. [Cloud Text-to-Speech API](https://console.cloud.google.com/apis/library/texttospeech.googleapis.com)를 열어 **사용**을 누릅니다.\n"
            "3. [사용자 인증 정보](https://console.cloud.google.com/apis/credentials)에서 **사용자 인증 정보 만들기 → API 키**로 발급하고 위 칸에 입력합니다."
        )
        st.caption("키의 API 제한에는 Cloud Text-to-Speech API를 선택하세요. 브라우저 웹사이트 제한(HTTP 리퍼러) 키는 이 서버의 요청과 맞지 않습니다.")
        st.caption("키를 입력하는 것만으로 음성이 생성되거나 결제 설정이 변경되지는 않습니다. 생성 버튼을 눌렀을 때 요청합니다.")
        if st.session_state.get("cloud_tts_api_key", "").strip():
            try:
                validate_key(st.session_state["cloud_tts_api_key"])
            except ValueError as exc:
                st.warning(str(exc))
            else:
                st.caption("키 입력됨 · 인증·API 사용 설정은 실제 생성 요청 시 확인됩니다.")
    return st.session_state.get("cloud_tts_api_key", "").strip()


def _change_gender(speaker):
    gender = st.session_state[f"chirp_gender_{speaker}"]
    config = st.session_state["voice_settings"][speaker]
    voice = st.session_state.get(f"chirp_voice_{speaker}", config.get("voice"))
    if CHIRP_VOICES.get(voice) != gender:
        voice = "Charon" if gender == "남성" else "Kore"
    config.update(voice=voice, gender=gender)
    st.session_state[f"chirp_voice_{speaker}"] = voice


def render_chirp_voice(speaker, current, *, work_dir, segments, api_key, busy):
    default_voice = current.get("voice", "Kore")
    default_gender = CHIRP_VOICES.get(default_voice, "여성")
    gender = st.radio("성우 성별", ["남성", "여성"],
                      index=0 if default_gender == "남성" else 1, horizontal=True,
                      key=f"chirp_gender_{speaker}", on_change=_change_gender, args=(speaker,))
    voices = [name for name, value in CHIRP_VOICES.items() if value == gender]
    voice_key = f"chirp_voice_{speaker}"
    if st.session_state.get(voice_key, default_voice) not in voices:
        st.session_state[voice_key] = "Charon" if gender == "남성" else "Kore"
    voice = st.selectbox("한국어 Chirp 보이스", voices,
                         index=voices.index(default_voice) if default_voice in voices else 0,
                         key=voice_key, format_func=lambda name: f"{name} · {CHIRP_VOICES[name]}")
    speed = st.slider("읽기 속도", 0.75, 1.25,
                      value=min(1.25, max(0.75, float(current.get("speed", 1.0)))),
                      step=0.05, key=f"chirp_speed_{speaker}")
    st.caption("선택한 목소리와 속도로 읽습니다. 감정·시니어 연기 지시문은 적용하지 않습니다.")
    settings = dict(engine="chirp", voice=voice, gender=gender, style="🎤 기본", speed=speed)
    st.session_state["voice_settings"][speaker] = settings
    if st.button(f"🔊 {speaker} Chirp 미리듣기", key=f"preview_btn_{speaker}",
                 use_container_width=True, disabled=busy):
        path = None
        try:
            validate_key(api_key)
            safe_name = "".join(char for char in speaker if char.isalnum() or char in "_-") or "voice"
            path = Path(work_dir) / f"preview_chirp_{safe_name}_{time.time_ns()}.wav"
            config = VoiceConfig(engine="chirp", voice=voice, speed=speed, cloud_tts_api_key=api_key)
            sample = preview_text(speaker, segments)
            with st.spinner(f"{speaker} · {voice} 음성 생성 중..."):
                TTSEngine.generate_preview(voice_config=config, output_file=str(path), sample_text=sample)
            st.audio(path.read_bytes(), format="audio/wav")
            st.caption(f"미리듣기 대사: {sample}")
        except Exception as exc:
            st.error(str(exc).replace(api_key, "[API 키]") if api_key else str(exc))
        finally:
            close_worker_client()
            if path is not None:
                path.unlink(missing_ok=True)
