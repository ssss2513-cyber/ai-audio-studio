"""CosyVoice 3 controls with a separate URL, bank and per-speaker references."""
import hashlib
from pathlib import Path
import time

import streamlit as st

from cosy3_voicebank_catalog import VOICEBANK, BANK_REVISION, ATTRIBUTION, reference_url
from .cosy3_client import check_connection, close_worker_client, synthesize
from .personal_colab import connection_url
from .tts_engine import VoiceConfig, GEMINI_VOICES, SUPERTONIC_VOICES, KOREAN_EDGE_VOICES
from .voice_recommendations import speaker_gender, preview_text

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK_REVISION = '94316f84ffd282028f478cc35925781b9dfb5d4a'


def preset(speaker, current=None, used=(), known_gender='', profile=''):
    current = current or {}
    catalog = {'gemini': GEMINI_VOICES, 'chirp': GEMINI_VOICES, 'supertonic': SUPERTONIC_VOICES,
               'edge-tts': KOREAN_EDGE_VOICES, 'cosyvoice3': VOICEBANK}.get(current.get('engine'), {})
    gender = (current.get('gender') or catalog.get(current.get('voice'), {}).get('gender')
              or speaker_gender(speaker, profile) or known_gender)
    review = not bool(gender)
    gender = gender if gender in ('남성', '여성') else '여성'
    choices = [key for key, value in VOICEBANK.items() if value['gender'] == gender]
    old = current.get('voice') if current.get('engine') == 'cosyvoice3' else None
    voice = old if old in choices and old not in used else next((key for key in choices if key not in used), choices[0])
    if old == 'custom':
        voice = old
    result = dict(engine='cosyvoice3', voice=voice, gender=gender, style=current.get('style', '🎤 기본'),
                  speed=current.get('speed', 1.0) if old else 1.0,
                  gender_needs_review=current.get('gender_needs_review', review), bank_revision=BANK_REVISION)
    if voice == 'custom':
        result.update(ref_audio_path=current.get('ref_audio_path', ''), prompt_text=current.get('prompt_text', ''))
    return result


def sync_widgets(speaker, config):
    st.session_state[f'cosy3_gender_{speaker}'] = config['gender']
    st.session_state[f'cosy3_voice_{speaker}'] = config['voice']
    st.session_state[f'cosy3_source_{speaker}'] = '내 한국어 녹음' if config['voice'] == 'custom' else '기본 목소리 20종'
    st.session_state[f'cosy3_speed_{speaker}'] = float(config.get('speed', 1))


def render_connection(prominent=False):
    with st.expander('🔥 CosyVoice 3 · 별도 서버 · 목소리 20종', expanded=prominent):
        st.markdown('**CosyVoice 3 · 남성 10개 + 여성 10개**')
        st.caption('버전 2와 주소·모델·화자 설정이 분리됩니다. 두 버전을 함께 사용하려면 각각 실행 가능한 GPU 런타임이 필요합니다.')
        st.markdown('1. 아래 **CosyVoice 3 전용 코랩**을 새로 엽니다.\n'
                    '2. **T4 GPU** 선택 후 **1번 설치 → 2번 서버 준비**를 실행합니다.\n'
                    '3. **4번 사이트 연결**의 전체 주소를 아래에 넣고 연결합니다.')
        st.link_button('CosyVoice 3 전용 코랩 v1.0.1 열기 ↗',
            'https://colab.research.google.com/github/ssss2513-cyber/ai-audio-studio/blob/'
            + NOTEBOOK_REVISION + '/CosyVoice3_Colab_API.ipynb', use_container_width=True)
        notebook = ROOT / 'CosyVoice3_Colab_API.ipynb'
        if notebook.is_file():
            st.download_button('⬇️ CosyVoice 3 코랩 v1.0.1 받기', notebook.read_bytes(),
                file_name='CosyVoice3_Colab_v1.0.1.ipynb', mime='application/x-ipynb+json',
                key='download_cosy3_notebook', use_container_width=True)
        recovery = ROOT / 'CosyVoice3_Show_Address_v1.0.py'
        if recovery.is_file() and st.checkbox('서버는 정상인데 연결 주소가 안 보일 때', key='cosy3_show_address_help'):
            st.write('기존 코랩 4번의 ■를 누른 뒤 ＋코드에 아래 내용을 붙여넣고 실행하세요. 모델을 재설치하지 않고 주소를 다시 표시합니다.')
            st.code(recovery.read_text(encoding='utf-8'), language='python')
        st.caption('‘음성 서버: 정상 · 외부 연결: 정상’이 반복되면 연결을 유지하는 중입니다. 다음 완료 화면을 기다리지 말고 출력된 주소를 아래 칸에 붙여넣으세요.')
        st.caption('4번 셀은 실행 상태로 두세요. 같은 코랩에 버전 2와 3을 함께 올리지 않습니다. 무료 계정에서 GPU 런타임 두 개가 배정되는 것은 보장되지 않습니다.')
        st.caption('외부 연결이 제한되면 사이트의 ‘CosyVoice 3 코랩 대본 받기’ 파일을 코랩 3번에 올려 직접 생성할 수 있습니다.')
        key = 'input_cosyvoice3_url'
        if key not in st.session_state:
            st.session_state[key] = st.session_state.get('cosyvoice3_url', '')
        raw = st.text_input('CosyVoice 3 · 내 코랩 주소', key=key, type='password',
                            placeholder='버전 3 코랩의 https://…/v1/… 전체 주소')
        try:
            url = connection_url(raw, 'cosyvoice3')
        except ValueError as exc:
            url = ''
            st.warning(str(exc))
        if st.session_state.get('cosyvoice3_url') != url:
            st.session_state.pop('checked_cosyvoice3_url', None)
        st.session_state['cosyvoice3_url'] = url
        if st.button('CosyVoice 3 연결 확인', key='connect_cosyvoice3', disabled=not url, use_container_width=True):
            with st.spinner('버전 3 서버와 목소리 목록 확인 중…'):
                st.session_state['checked_cosyvoice3_url'] = check_connection(url)
        result = st.session_state.get('checked_cosyvoice3_url')
        if result:
            (st.success if result[0] else st.error)(result[1])
        st.caption('기본 20종은 VCTK의 서로 다른 공개 화자입니다. 영어 참고 음색으로 한국어를 합성하므로 억양에 차이가 있을 수 있습니다. 한국어 녹음도 등록할 수 있습니다.')
        st.download_button('목소리 출처·표시 문구 받기', ATTRIBUTION.encode('utf-8'),
            file_name='CosyVoice3_목소리출처.txt', key='cosy3_attribution', use_container_width=True)


def _change_gender(speaker):
    gender = st.session_state[f'cosy3_gender_{speaker}']
    config = st.session_state['voice_settings'][speaker]
    config.update(gender=gender, gender_needs_review=False)
    if config.get('voice') == 'custom':
        return
    options = [key for key, value in VOICEBANK.items() if value['gender'] == gender]
    current = st.session_state.get(f'cosy3_voice_{speaker}', config.get('voice'))
    if current not in options:
        used = {row.get('voice') for name, row in st.session_state['voice_settings'].items()
                if name != speaker and row.get('engine') == 'cosyvoice3'}
        current = next((key for key in options if key not in used), options[0])
    config['voice'] = current
    st.session_state[f'cosy3_voice_{speaker}'] = current


def render_voice(speaker, current, *, work_dir, segments, busy, style_selector):
    st.markdown('**🔥 CosyVoice 3 · 목소리 선택**')
    if current.get('gender_needs_review'):
        st.caption('성별 단서가 부족합니다. 이 화자의 성별을 확인해주세요.')
    gender = st.radio('성우 성별', ['남성', '여성'], horizontal=True,
        index=0 if current.get('gender') == '남성' else 1,
        key=f'cosy3_gender_{speaker}', on_change=_change_gender, args=(speaker,), disabled=busy)
    modes = ['기본 목소리 20종', '내 한국어 녹음']
    mode = st.radio('목소리 선택 방식', modes, index=1 if current.get('voice') == 'custom' else 0,
                    key=f'cosy3_source_{speaker}', disabled=busy)
    ref_path, prompt = '', ''
    if mode == modes[0]:
        options = [key for key, row in VOICEBANK.items() if row['gender'] == gender]
        selected = f'cosy3_voice_{speaker}'
        default = current.get('voice', options[0])
        if st.session_state.get(selected, default) not in options:
            st.session_state[selected] = options[0]
        voice = st.selectbox('목소리 · 성별마다 서로 다른 10명', options,
            index=options.index(default) if default in options else 0,
            format_func=lambda key: VOICEBANK[key]['name'], key=selected, disabled=busy)
        st.caption('영어 공개 참고 음색으로 한국어를 읽습니다. 기본 목소리는 업로드·API 키가 필요 없습니다.')
        with st.expander('기준 목소리 원본 듣기 · 영어 녹음'):
            st.audio(reference_url(voice), format='audio/wav')
            st.caption('기준 녹음입니다. 아래 한국어 미리듣기에서 실제 합성 결과를 들을 수 있습니다.')
    else:
        voice = 'custom'
        saved_key, prompt_key = f'cosy3_saved_{speaker}', f'cosy3_prompt_{speaker}'
        ref_path = st.session_state.get(saved_key, current.get('ref_audio_path', ''))
        uploaded = st.file_uploader('한국어 참고 음성 · 3~30초 · WAV/FLAC',
            type=['wav', 'flac'], key=f'cosy3_upload_{speaker}', disabled=busy)
        if uploaded is not None:
            data = uploaded.getvalue()
            if not 0 < len(data) <= 10 * 1024 * 1024:
                st.error('참고 음성은 10MB 이하로 등록해주세요.')
            else:
                digest = hashlib.sha256(data).hexdigest()
                path = Path(work_dir) / 'cosy3_references' / (digest + Path(uploaded.name).suffix.lower())
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.is_file():
                    path.write_bytes(data)
                    path.chmod(0o600)
                if str(path) != ref_path:
                    st.session_state[prompt_key] = ''
                ref_path = str(path)
                st.session_state[saved_key] = ref_path
        if ref_path and Path(ref_path).is_file():
            st.audio(Path(ref_path).read_bytes())
        if prompt_key not in st.session_state:
            st.session_state[prompt_key] = current.get('prompt_text', '')
        prompt = st.text_area('참고 녹음에서 실제로 말한 대사', key=prompt_key, disabled=busy,
            help='생성할 대사가 아니라 위 녹음에 들어 있는 문장을 정확히 입력하세요.')
        st.caption('배경음 없이 한 사람만 말한 녹음을 사용하세요. 해당 화자의 한국어 음색과 말투를 기준으로 생성합니다.')
    style = style_selector('🎨 음성 스타일 · 감정·말투', speaker, current.get('style', '🎤 기본'))
    speed = st.slider('말하기 속도', 0.8, 1.2, float(current.get('speed', 1)), 0.05,
                      key=f'cosy3_speed_{speaker}', disabled=busy)
    st.caption('스타일은 말투 지시입니다. 선택한 목소리의 성별과 화자 ID는 유지됩니다.')
    config = dict(engine='cosyvoice3', voice=voice, gender=gender, style=style, speed=speed,
                  bank_revision=BANK_REVISION, gender_needs_review=current.get('gender_needs_review', False))
    if voice == 'custom':
        config.update(ref_audio_path=ref_path, prompt_text=prompt)
    st.session_state['voice_settings'][speaker] = config
    duplicates = [name for name, row in st.session_state['voice_settings'].items()
                  if name != speaker and voice != 'custom' and row.get('engine') == 'cosyvoice3' and row.get('voice') == voice]
    if duplicates:
        st.caption('같은 목소리를 쓰는 화자: ' + ', '.join(duplicates))
    if st.button(f'🔊 {speaker} CosyVoice 3 미리듣기', key=f'preview_btn_{speaker}',
                 disabled=busy, use_container_width=True):
        url = st.session_state.get('cosyvoice3_url', '')
        path = Path(work_dir) / f'preview_cosy3_{time.time_ns()}.wav'
        try:
            ok, message = check_connection(url)
            if not ok:
                raise ValueError(message)
            status = st.empty()
            sample = preview_text(speaker, segments)
            cfg = VoiceConfig(engine='cosyvoice3', voice=voice, style=style, speed_factor=speed,
                cosyvoice3_url=url, ref_audio_path=ref_path, prompt_text=prompt)
            synthesize(sample, str(path), cfg, progress=lambda phase, details: status.info(phase))
            status.empty()
            st.audio(path.read_bytes(), format='audio/wav')
            st.caption('미리듣기 대사: ' + sample)
        except Exception as exc:
            st.error(str(exc).replace(url, '[내 코랩 주소]') if url else str(exc))
        finally:
            close_worker_client()
            path.unlink(missing_ok=True)
