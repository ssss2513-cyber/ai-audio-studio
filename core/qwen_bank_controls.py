"""Twenty directly selectable voices: no recordings or Gemini keys to supply."""
from pathlib import Path
import time

import streamlit as st

from qwen_voicebank_catalog import BANK_REVISION, VOICEBANK
from .qwen_voicebank_client import close_worker_client, synthesize
from .qwen_bank_connection import connection_status
from .colab_session_ui import render_qwen_bank_connection as render_connection
from .tts_engine import VoiceConfig
from .voice_recommendations import preview_text


def preset(speaker, current, gender, used=()):
    current = current or {}
    needs_review = (current.get('gender_needs_review', False)
                    if current.get('engine') == 'qwen-bank' else not bool(gender))
    gender = gender or '여성'
    choices = [key for key, info in VOICEBANK.items() if info['gender'] == gender]
    old = current.get('voice') if current.get('engine') == 'qwen-bank' else None
    voice = old if old in choices and old not in used else next((key for key in choices if key not in used), choices[len(used) % len(choices)])
    return dict(engine='qwen-bank', voice=voice, gender=gender, style='🎤 기본',
                gender_needs_review=needs_review, bank_revision=BANK_REVISION)


def _change_gender(speaker):
    current = st.session_state['voice_settings'][speaker]
    gender = st.session_state[f'qwen_bank_gender_{speaker}']
    voice = st.session_state.get(f'qwen_bank_voice_{speaker}', current.get('voice'))
    if VOICEBANK.get(voice, {}).get('gender') != gender:
        choices = [key for key, info in VOICEBANK.items() if info['gender'] == gender]
        used = {row.get('voice') for name, row in st.session_state['voice_settings'].items()
                if name != speaker and row.get('engine') == 'qwen-bank'}
        voice = next((key for key in choices if key not in used), choices[0])
    current.update(gender=gender, voice=voice, gender_needs_review=False)
    st.session_state[f'qwen_bank_voice_{speaker}'] = voice


def render_voice(speaker, current, *, work_dir, segments, busy):
    default = current.get('voice', 'F01')
    gender = VOICEBANK.get(default, VOICEBANK['F01'])['gender']
    if current.get('gender_needs_review'):
        st.caption('성별 단서가 부족합니다. 이 화자에 맞는 성별을 확인해주세요.')
    selected_gender = st.radio('성우 성별', ['남성', '여성'], horizontal=True,
        index=0 if gender == '남성' else 1, key=f'qwen_bank_gender_{speaker}',
        on_change=_change_gender, args=(speaker,), disabled=busy)
    voices = [key for key, info in VOICEBANK.items() if info['gender'] == selected_gender]
    key = f'qwen_bank_voice_{speaker}'
    if st.session_state.get(key, default) not in voices:
        st.session_state[key] = voices[0]
    voice = st.selectbox('기본 목소리 선택 · 성별마다 10개', voices, key=key,
        index=voices.index(default) if default in voices else 0,
        format_func=lambda key: VOICEBANK[key]['name'], disabled=busy)
    st.caption(VOICEBANK[voice]['description'])
    st.caption('선택만 하면 됩니다. 첫 생성 때 이 목소리의 기준 음성을 자동으로 준비합니다. 실제 음색·연령감은 생성 결과에 따라 다릅니다.')
    st.caption('이 목록은 각 목소리에 정해진 기본 말투를 사용하며 별도 감정 지시 입력은 지원하지 않습니다.')
    st.session_state['voice_settings'][speaker] = dict(engine='qwen-bank', voice=voice,
        gender=selected_gender, style='🎤 기본', bank_revision=BANK_REVISION,
        gender_needs_review=current.get('gender_needs_review', False))
    duplicates = [name for name, row in st.session_state['voice_settings'].items()
                  if name != speaker and row.get('engine') == 'qwen-bank' and row.get('voice') == voice]
    if duplicates:
        st.caption('같은 목소리를 쓰는 화자: ' + ', '.join(duplicates))
    if st.button(f'🔊 {speaker} 기본 목소리 미리듣기', key=f'preview_btn_{speaker}',
                 disabled=busy, use_container_width=True):
        url = st.session_state.get('qwen_bank_url', '')
        path = Path(work_dir) / f'preview_qwen_bank_{time.time_ns()}.wav'
        try:
            ok, message = connection_status(url)
            if not ok:
                raise ValueError(message)
            status = st.empty()
            config = VoiceConfig(engine='qwen-bank', voice=voice, qwen_url=url)
            sample = preview_text(speaker, segments)
            synthesize(sample, str(path), config, progress=lambda phase, details: status.info(phase))
            status.empty()
            st.audio(path.read_bytes(), format='audio/wav')
            st.caption('미리듣기 대사: ' + sample)
        except Exception as exc:
            st.error(str(exc).replace(url, '[내 코랩 주소]') if url else str(exc))
        finally:
            close_worker_client()
            path.unlink(missing_ok=True)
