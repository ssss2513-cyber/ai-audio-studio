"""Serve saved results through Streamlit's immediate file download path."""
import hashlib
from pathlib import Path

import streamlit as st


def saved_file_download(label, path, *, file_name, mime, key, primary=False,
                        prepare_large=False):
    # Passing Path.read_bytes WITHOUT calling it uses a deferred backend
    # operation and only creates a browser link after an asynchronous response.
    # Register the finished file during rendering so a click uses a ready URL.
    try:
        source = Path(path)
        info = source.stat()
        if not source.is_file() or info.st_size == 0:
            raise OSError('missing or empty result')
    except (OSError, TypeError, ValueError):
        st.error('다운로드할 완성 파일을 찾을 수 없습니다. 현재 화면과 작업 기록을 보관해주세요.')
        return

    identity = hashlib.sha256(
        f'{source.resolve()}:{info.st_size}:{info.st_mtime_ns}'.encode()).hexdigest()[:16]
    widget_key = f'{key}_direct_{identity}'
    # MP3 is ready in one click. Large optional bundles are only loaded when
    # requested, so every page render does not also load a second audio copy.
    if prepare_large and info.st_size > 8 * 1024 * 1024:
        ready_key = '_ready_file_' + key
        if st.session_state.get(ready_key) != identity:
            if not st.button(label + ' 준비', key=widget_key + '_prepare',
                             use_container_width=True,
                             help='파일을 준비한 다음 나타나는 저장 버튼을 누르세요.'):
                return
            st.session_state[ready_key] = identity
        st.caption('파일이 준비되었습니다. 아래 버튼으로 저장하세요.')

    try:
        with source.open('rb') as handle:
            st.download_button(label, data=handle, file_name=file_name, mime=mime,
                key=widget_key, type='primary' if primary else 'secondary',
                use_container_width=True, on_click='ignore',
                help=f'저장할 파일: {file_name} · {info.st_size / (1024 * 1024):.1f} MB')
    except OSError:
        st.error('저장된 파일을 읽지 못했습니다. 음성을 다시 생성하지 말고 현재 화면을 보관해주세요.')
