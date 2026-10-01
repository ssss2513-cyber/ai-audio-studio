"""Twenty directly selectable voices: no recordings or Gemini keys to supply."""
from pathlib import Path
import time

import streamlit as st

from qwen_voicebank_catalog import BANK_REVISION, VOICEBANK
from .personal_colab import connection_url
from .qwen_voicebank_client import connection_status, close_worker_client, synthesize
from .tts_engine import VoiceConfig
from .voice_recommendations import preview_text

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK_REVISION = '567ce736edefed7edfa037cbe1a2ebbea8aef69c'  # Voice-bank Colab v1.0.1: visible output.


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


def render_connection(prominent=False):
    with st.expander('🎭 기본 목소리 20종 · 전용 코랩 연결', expanded=prominent):
        st.markdown('**남성 10개 + 여성 10개 · 녹음 업로드·Gemini 키 불필요**')
        st.caption('기존 Qwen 9종에 더해 고를 수 있는 새 목소리 목록입니다. 코랩이 선택한 목소리를 처음 한 번 만들고 저장한 뒤, 같은 목소리로 대사를 읽습니다.')
        st.markdown('1. 아래 코랩을 열고 **런타임 → 런타임 유형 변경 → T4 GPU**를 선택합니다.\n'
                    '2. **1번 설치 → 2번 서버 준비 → 4번 사이트 연결**을 실행합니다.\n'
                    '3. 나온 **프로그램 연결 주소 전체**를 아래에 넣습니다.')
        st.link_button('기본 목소리 20종 코랩 v1.0.1 열기 ↗',
            'https://colab.research.google.com/github/ssss2513-cyber/ai-audio-studio/blob/'
            + NOTEBOOK_REVISION + '/Qwen3_TTS_VoiceBank_Colab_API.ipynb', use_container_width=True)
        notebook = ROOT / 'Qwen3_TTS_VoiceBank_Colab_API.ipynb'
        if notebook.is_file():
            st.download_button('⬇️ 기본 목소리 20종 코랩 v1.0.1 받기', notebook.read_bytes(),
                file_name='Qwen3_TTS_기본목소리20종_Colab_v1.0.1.ipynb',
                mime='application/x-ipynb+json', use_container_width=True)
        st.caption('v1.0.1은 진행 로그와 연결 주소를 셀에 직접 표시합니다. 이미 열어둔 이전 코랩에는 자동 적용되지 않으므로 위 수정본을 열어주세요.')
        st.caption('기존 Qwen 9종 코랩과 별도의 새 코랩입니다. 같은 GPU에 두 코랩 모델을 함께 실행하지 마세요.')
        st.caption('첫 사용은 모델 다운로드와 선택한 목소리 준비 때문에 추가 시간이 걸립니다. 준비가 끝나면 대사마다 목소리를 다시 만들지 않습니다.')
        st.caption('무료 코랩 안에서 직접 생성하려면 사이트에서 ‘기본 목소리 20종 코랩 대본 받기’를 눌러 파일을 받고, 코랩 1 → 2 → 3번을 실행하세요. '
                   '[외부 웹 연결 제한 안내](https://research.google.com/colaboratory/faq.html)')
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
        st.session_state['qwen_bank_url'] = url
        if st.button('기본 목소리 20종 연결 확인', disabled=not url, use_container_width=True):
            with st.spinner('전용 코랩 확인 중…'):
                st.session_state['checked_qwen_bank_url'] = connection_status(url)
        checked = st.session_state.get('checked_qwen_bank_url')
        if checked:
            (st.success if checked[0] else st.error)(checked[1])
        st.caption('목소리는 현재 코랩 런타임에 저장됩니다. 코랩 6번 셀에서 보관하면 새 런타임에서도 같은 음성을 복원할 수 있습니다.')


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
    st.caption('이 목록은 각 목소리에 정해진 기본 말투를 사용합니다. 별도 감정 지시는 기존 Qwen CustomVoice 9종에서 사용할 수 있습니다.')
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
