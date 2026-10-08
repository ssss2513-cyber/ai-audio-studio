"""Explicit per-dialogue direction edits. Rendering never synthesizes audio."""
import hashlib
import json
from pathlib import Path
import time

import streamlit as st

from .emotion_directing import (EMOTIONS, INHERIT, INTENSITIES, SUPPORTED_ENGINES,
                                acting_direction, segment_cue, tagged_line)


def _preview(segment, settings, work_dir, gemini_key, gemini_model):
    from .cosy_kaggle import use_kaggle, start_preview
    from .tts_engine import TTSEngine, VoiceConfig
    emotion, intensity = segment_cue(segment)
    if use_kaggle():
        start_preview(work_dir, segment.speaker, segment.text, settings,
                      emotion=emotion, emotion_intensity=intensity)
        return
    row = settings.get(segment.speaker, {})
    engine = row.get('engine')
    if engine not in SUPPORTED_ENGINES:
        st.error('이 화자를 코지2·3 또는 Gemini로 설정해주세요.')
        return
    config = VoiceConfig(engine=engine, voice=row.get('voice', ''),
        style=row.get('style', '🎤 기본'), emotion=emotion, emotion_intensity=intensity,
        ref_audio_path=row.get('ref_audio_path', ''), prompt_text=row.get('prompt_text', ''),
        speed_factor=float(row.get('speed', 1)), api_key=gemini_key, model=gemini_model,
        cosyvoice_url=st.session_state.get('cosyvoice_url', ''),
        cosyvoice3_url=st.session_state.get('cosyvoice3_url', ''))
    target = Path(work_dir) / f'emotion_preview_{time.time_ns()}.wav'
    try:
        generator = {'cosyvoice': TTSEngine.generate_cosyvoice_speech,
                     'cosyvoice3': TTSEngine.generate_cosy3_speech,
                     'gemini': TTSEngine.generate_gemini_speech}[engine]
        with st.spinner(f'{segment.index}번 대사를 저장한 감정으로 생성합니다…'):
            generator(segment.text, str(target), config)
        st.audio(target.read_bytes(), format='audio/wav')
        st.caption(f'{segment.index}번 · {segment.speaker} · {emotion or INHERIT} · {intensity}')
    except Exception as exc:
        message = str(exc)
        for secret in (config.cosyvoice_url, config.cosyvoice3_url, gemini_key):
            if secret:
                message = message.replace(secret, '[내 연결 정보]')
        st.error(message)
    finally:
        if engine == 'cosyvoice3':
            from .cosy3_client import close_worker_client
            close_worker_client()
        target.unlink(missing_ok=True)


def render_emotions(segments, settings, work_dir, *, busy, gemini_key, gemini_model):
    if not segments:
        return
    with st.expander('🎭 대사별 감정 넣기 · 슬픔·분노·속삭임', expanded=True):
        st.write('같은 화자도 대사마다 다른 감정으로 말하게 할 수 있습니다. '
                 '표에서 감정과 강도를 고른 뒤 **감정 설정 저장**을 누르세요.')
        st.caption('코지2·3 / Gemini 지원 · 선택한 목소리는 유지 · 대사별 감정이 화자 스타일보다 우선합니다. '
                   '‘화자 스타일 사용’을 고르면 기존 스타일을 그대로 씁니다.')
        st.caption('강도는 모델에 보내는 연기 지시입니다. 음색·한국어 발음·감정 표현은 모델과 참고 음성에 따라 달라집니다.')
        with st.expander('대본에 감정 표시를 직접 넣는 방법'):
            st.code('만복: [분노, 강하게] 어찌 내 가족을 속일 수 있단 말이오!\n'
                    '옥련: [울먹임] 저는 그런 적이 없습니다.\n'
                    '만복: [속삭임] 이 일은 아무에게도 말하지 마시오.', language=None)
            st.caption('[sad], [angry], [whispering] 같은 영어 표시도 지원합니다. '
                       '한 줄에는 한 감정만 넣고, 중간에 바뀌면 같은 화자로 다음 줄을 쓰세요. '
                       '이 표시는 읽을 대사와 자막에서 제외됩니다.')
            st.write('사용 가능한 감정: ' + ' · '.join(EMOTIONS))
        source = st.session_state.get('casting_formatted_text')
        if source is None:
            source = '\n'.join(getattr(seg, 'raw_line', '') for seg in segments)
        if st.session_state.get('script_editor', '').strip() != source.strip():
            st.info('대본 입력창에 아직 분석하지 않은 수정이 있습니다. 1번의 ‘화자 및 대사 분석하기’를 누른 뒤 감정을 설정해주세요.')
            return
        notes = [(seg.index, getattr(seg, 'emotion_note', '')) for seg in segments
                 if getattr(seg, 'emotion_note', '')]
        for index, note in notes:
            st.warning(f'{index}번: {note}')
        rows = [{'순번': seg.index, '화자': seg.speaker, '대사': seg.text,
                 '감정': segment_cue(seg)[0] or INHERIT, '강도': segment_cue(seg)[1]} for seg in segments]
        identity = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()[:16]
        with st.form('emotion_form_' + identity):
            edited = st.data_editor(rows, hide_index=True, width='stretch', height=350,
                key='emotion_rows_' + identity,
                disabled=True if busy else ['순번', '화자', '대사'],
                column_config={
                    '대사': st.column_config.TextColumn('읽을 대사', width='large'),
                    '감정': st.column_config.SelectboxColumn('감정', options=[INHERIT] + list(EMOTIONS), required=True),
                    '강도': st.column_config.SelectboxColumn('강도', options=list(INTENSITIES), required=True),
                })
            submitted = st.form_submit_button('💾 감정 설정 저장', disabled=busy, type='primary')
        if submitted:
            updates = []
            try:
                if len(edited) != len(segments):
                    raise ValueError('대사 목록이 바뀌었습니다. 화면을 다시 확인해주세요.')
                for seg, row in zip(segments, edited):
                    emotion = '' if row['감정'] == INHERIT else row['감정']
                    intensity = row['강도']
                    if row['순번'] != seg.index or intensity not in INTENSITIES:
                        raise ValueError('대사 순번과 감정 강도를 다시 확인해주세요.')
                    acting_direction(emotion, intensity)
                    updates.append((seg, emotion, intensity))
            except (ValueError, KeyError, TypeError) as exc:
                st.error(str(exc))
            else:
                for seg, emotion, intensity in updates:
                    seg.emotion, seg.emotion_intensity, seg.emotion_note = emotion, intensity, ''
                # The source guard above prevents overwriting an unanalysed draft.
                script = '\n'.join(tagged_line(seg) for seg in segments)
                st.session_state['pending_script_text'] = script
                st.session_state['casting_formatted_text'] = script
                st.toast('대사별 감정을 저장했습니다. 목소리 배정은 유지됩니다.')
                st.rerun()
        unsupported = [str(seg.index) for seg in segments if segment_cue(seg)[0]
                       and settings.get(seg.speaker, {}).get('engine') not in SUPPORTED_ENGINES]
        if unsupported:
            st.warning('감정 지시를 지원하지 않는 엔진의 대사: ' + ', '.join(unsupported)
                       + '번. 코지2·3 또는 Gemini로 바꾸거나 해당 감정을 ‘화자 스타일 사용’으로 고르세요.')
        st.download_button('⬇️ 감정 표시가 들어간 대본 받기',
            '\n'.join(tagged_line(seg) for seg in segments).encode('utf-8-sig'),
            file_name='감정설정_대본.txt', mime='text/plain', key='emotion_script_download', on_click='ignore')
        by_index = {seg.index: seg for seg in segments}
        if st.session_state.get('emotion_preview_line') not in by_index:
            st.session_state['emotion_preview_line'] = segments[0].index
        selected = st.selectbox('감정을 들어볼 대사', list(by_index),
            format_func=lambda index: f'{index}번 · {by_index[index].speaker} · {by_index[index].text[:65]}',
            key='emotion_preview_line', disabled=busy)
        segment = by_index[selected]
        from .cosy_kaggle import use_kaggle
        engine = settings.get(segment.speaker, {}).get('engine')
        supported = engine in (('cosyvoice', 'cosyvoice3') if use_kaggle() else SUPPORTED_ENGINES)
        st.caption('화자 카드의 미리듣기는 화자 기본 스타일입니다. 아래 버튼은 표에 저장한 대사별 감정을 사용합니다.')
        if use_kaggle():
            st.caption('캐글 미리듣기도 새 GPU 작업의 설치·모델 준비를 거칩니다. 완료 음성은 아래 캐글 진행 화면에 나옵니다.')
        if st.button('🔊 선택 대사 감정 미리듣기', key='emotion_preview_start',
                     disabled=busy or not supported, use_container_width=True):
            _preview(segment, settings, work_dir, gemini_key, gemini_model)
