"""Shared MOV controls for finished Kaggle and ordinary site results."""
import hashlib
from pathlib import Path

import streamlit as st

from . import subtitle_mov
from .result_downloads import saved_file_download


def render_subtitle_mov(work_dir, result, scope):
    source = Path(result.get('srt') or '')
    audio = Path(result.get('full_audio') or '')
    if not source.is_file() or not audio.is_file():
        return
    info = source.stat()
    tag = hashlib.sha256(f'{source.resolve()}:{info.st_size}:{info.st_mtime_ns}'.encode()).hexdigest()[:16]
    key = f'{scope}_subtitle_mov_{tag}'
    identity = st.session_state.get(key + '_id')
    active = subtitle_mov.get_export(work_dir, identity).get('status') in subtitle_mov.ACTIVE

    @st.fragment(run_every=2 if active else None)
    def panel():
        state = subtitle_mov.get_export(work_dir, st.session_state.get(key + '_id'))
        busy = state.get('status') in subtitle_mov.ACTIVE
        if active and not busy:
            st.rerun()
        with st.expander('🎬 투명 자막 MOV 만들기·다운로드', expanded=bool(state)):
            st.caption('영상 위에 올릴 투명 배경 자막입니다. 완성 음성과 같은 자막 시간을 사용합니다.')
            left, right = st.columns(2)
            with left:
                size = st.selectbox('영상 비율', ['가로 16:9 · 1920×1080', '세로 9:16 · 1080×1920'],
                                    key=key + '_size', disabled=busy)
            with right:
                font_size = st.slider('글자 크기', 32, 88, 64, key=key + '_font', disabled=busy)
            st.caption('하단 중앙 · 흰 글씨와 검은 테두리 · 30fps · 소리 없는 자막 영상')
            if st.button('🎬 투명 자막 MOV 만들기', key=key + '_start', disabled=busy,
                         use_container_width=True):
                width, height = subtitle_mov.SIZES[0 if size.startswith('가로') else 1]
                try:
                    st.session_state[key + '_id'] = subtitle_mov.start_export(
                        work_dir, str(source), str(audio), width, height, font_size)
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            if busy:
                st.progress(state.get('progress', 0), text=state.get('message', '자막 영상 준비 중'))
                st.caption('음성을 다시 생성하지 않습니다. 변환 중에도 완성 MP3와 SRT는 받을 수 있습니다.')
                if st.button('MOV 만들기 중단', key=key + '_stop'):
                    subtitle_mov.cancel_export(work_dir, state['id'])
                    st.rerun()
            elif state.get('status') == 'complete':
                st.success(state['message'])
                st.caption(f"완성 파일: {state['width']}×{state['height']} · 글자 크기 {state['font_size']}")
                saved_file_download('⬇️ 투명 자막 MOV 받기', state['file'],
                    file_name='subtitles.mov', mime='video/quicktime', key=key + '_download', prepare_large=True)
                st.info('편집 사이트의 ‘자막영상 MOV’에는 subtitles.mov, ‘나레이션’에는 full_audio.mp3를 넣고 둘 다 0초부터 배치하세요.')
            elif state.get('message'):
                st.warning(state['message'])
            st.caption('MOV는 별도 파일입니다. 기존 MP3·SRT·VTT와 완성본 ZIP은 그대로 받을 수 있습니다.')
    panel()
