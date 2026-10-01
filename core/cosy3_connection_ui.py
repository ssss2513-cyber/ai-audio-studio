"""CosyVoice 3 connection controls; no generation workers or model state."""
from pathlib import Path

import streamlit as st

from cosy3_voicebank_catalog import ATTRIBUTION
from .cosy3_client import check_connection
from .personal_colab import connection_url

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK_REVISION = '94316f84ffd282028f478cc35925781b9dfb5d4a'


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
