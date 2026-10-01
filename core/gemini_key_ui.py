"""Separate masked inputs; migrate existing combined input without losing keys."""
import streamlit as st

from .gemini_keys import GeminiKeyInputError, parse_gemini_keys


def render_key_inputs():
    if '_gemini_key_slots' not in st.session_state:
        saved = st.session_state.get('input_gemini_api_key') or st.session_state.get('gemini_api_key', '')
        try:
            initial = parse_gemini_keys(saved)
        except GeminiKeyInputError:
            initial = [saved]  # Keep the original visible in a masked edit field.
        st.session_state['_gemini_key_slots'] = max(3, len(initial))
        st.session_state['_gemini_key_values'] = initial
    saved_values = st.session_state.get('_gemini_key_values', [])
    keys, raw_values, count = [], [], 0
    first_slots, slot_status = {}, []
    invalid = False
    for index in range(st.session_state['_gemini_key_slots']):
        widget = f'gemini_key_slot_{index + 1}'
        if widget not in st.session_state:
            st.session_state[widget] = saved_values[index] if index < len(saved_values) else ''
        value = st.text_input(f'Gemini API 키 {index + 1}', type='password', key=f'gemini_key_slot_{index + 1}',
                              placeholder='AI Studio에서 복사한 전체 키',
                              help='한 칸에 키 하나를 넣으세요. 기존 쉼표·줄바꿈 구분 입력도 인식합니다.')
        raw_values.append(value)
        try:
            parsed = parse_gemini_keys(value)
        except GeminiKeyInputError as exc:
            invalid = True
            st.warning(f'입력칸 {index + 1}: {exc}')
            slot_status.append(f'{index + 1}번: 입력 확인 필요')
            continue
        count += len(parsed)
        duplicates = sorted({first_slots[key] for key in parsed if key in first_slots})
        added = 0
        for key in parsed:
            if key not in keys:
                keys.append(key)
                first_slots[key] = index + 1
                added += 1
        if not parsed:
            slot_status.append(f'{index + 1}번: 비어 있음')
        elif added:
            slot_status.append(f'{index + 1}번: 등록 {added}개' + (' · 중복 제외' if duplicates else ''))
        else:
            slot_status.append(f'{index + 1}번: ' + ', '.join(str(slot) for slot in duplicates) + '번과 같은 키')
    # Widgets are removed when another engine hides this section. Keep the
    # values in this session only, so switching engines does not erase keys.
    st.session_state['_gemini_key_values'] = raw_values
    if st.button('＋ 키 입력칸 추가', key='add_gemini_key_slot'):
        st.session_state['_gemini_key_slots'] += 1
        st.rerun()
    raw = '\n'.join(raw_values)
    st.session_state['gemini_api_key'] = raw
    st.caption('입력칸 상태 · ' + ' / '.join(slot_status))
    if invalid:
        return raw, []
    if keys:
        st.info(f'서로 다른 Gemini API 키 {len(keys)}개 인식됨 · 음성 생성에 순번대로 사용합니다.')
        if count > len(keys):
            st.caption(f'같은 키 {count - len(keys)}개는 중복으로 계산하지 않았습니다.')
        st.caption('입력 인식 결과입니다. 실제 연결·이용 권한은 요청할 때 확인하며, 이번 작업의 키별 요청 결과를 생성 화면에 표시합니다.')
        st.caption('같은 Google 프로젝트의 키들은 한도를 공유합니다. 키 3개가 한도 3배를 뜻하지는 않습니다.')
    return ','.join(keys), keys
