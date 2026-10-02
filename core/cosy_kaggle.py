"""Generate on Kaggle from the shared site; preserve existing Colab controls."""
import hashlib
import io
import json
import math
from pathlib import Path
import secrets
import time
from types import SimpleNamespace
import zipfile

import streamlit as st

from cosy_kaggle_contract import (FORMAT, VERSION, MODELS, MAX_ITEMS,
    MAX_REFERENCE_BYTES, MAX_REFERENCES_BYTES)
from cosy3_voicebank_catalog import BANK_REVISION, ATTRIBUTION
from .cosy3_client import request_payload
from .tts_engine import TTSEngine, VoiceConfig, clean_spoken_text
from . import kaggle_jobs

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOKS = {
    'cosyvoice': ('CosyVoice 2', 'CosyVoice2_Kaggle_DualGPU.ipynb'),
    'cosyvoice3': ('CosyVoice 3', 'CosyVoice3_Kaggle_DualGPU.ipynb'),
    'auto': ('CosyVoice 2·3 혼합 대본', 'CosyVoice2_3_Kaggle_DualGPU.ipynb'),
}


def notebook_download(engine, key):
    label, filename = NOTEBOOKS[engine]
    path = ROOT / filename
    if path.is_file():
        st.download_button('⬇️ ' + label + ' 캐글 노트북', path.read_bytes(),
            file_name=filename, mime='application/x-ipynb+json', key=key)
    else:
        st.caption('캐글 노트북 파일 반영 중입니다. 잠시 후 새로고침해주세요.')


def use_kaggle():
    return st.session_state.get('cosy_compute_provider') == 'kaggle'


def _forget_connection():
    for key in ('_kaggle_credentials', 'kaggle_api_token', '_kaggle_connection_notice'):
        st.session_state.pop(key, None)
    st.session_state['_kaggle_upload_revision'] = st.session_state.get('_kaggle_upload_revision', 0) + 1


def render_downloads(work_dir, busy=False):
    with st.expander('🚀 코지2·3 · 캐글 연결', expanded=use_kaggle()):
        if 'cosy_compute_provider' not in st.session_state:
            st.session_state['cosy_compute_provider'] = (
                'kaggle' if st.session_state.get('cosy_batch_location') == '캐글 GPU 2개' else 'colab')
        st.radio('음성을 계산할 GPU', ['colab', 'kaggle'],
            format_func=lambda value: '기존 코랩 연결' if value == 'colab' else '캐글 GPU 2개',
            key='cosy_compute_provider', disabled=busy,
            help='두 방식 모두 이 공유 사이트에서 생성하고 완성된 음성을 받습니다.')
        st.caption('캐글을 한 번 연결하면 이 사이트의 생성 버튼으로 실행하고 MP3도 여기서 받습니다.')
        credentials = st.session_state.get('_kaggle_credentials')
        if credentials:
            st.success('캐글 연결됨 · ' + credentials['username'])
            if st.session_state.get('_kaggle_connection_notice'):
                st.info(st.session_state['_kaggle_connection_notice'])
            if st.button('현재 토큰으로 연결 다시 확인', key='kaggle_recheck_connection',
                         disabled=busy or kaggle_jobs.monitoring(work_dir), use_container_width=True):
                try:
                    with st.spinner('현재 토큰으로 계정과 기존 작업을 확인합니다…'):
                        checked = kaggle_jobs.authenticate(credentials)
                        st.session_state['_kaggle_credentials'] = checked
                        job = kaggle_jobs.get_job(work_dir)
                        resumed = bool(job and job.get('status') in ('needs_check', 'failed')
                            and kaggle_jobs.reconnect(work_dir, checked))
                        st.session_state['_kaggle_connection_notice'] = (
                            '현재 토큰으로 계정 인증을 확인했습니다. 기존 작업을 확인 중입니다.' if resumed else
                            '현재 토큰으로 계정 인증을 확인했습니다. 새 토큰을 발급받을 필요가 없습니다.')
                except kaggle_jobs.KaggleError as exc:
                    st.session_state.pop('_kaggle_connection_notice', None)
                    st.error(kaggle_jobs._error_message(exc))
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            st.button('캐글 연결 정보 지우기', key='kaggle_disconnect', on_click=_forget_connection, disabled=busy)
        else:
            st.markdown('이미 발급받은 토큰을 아래에 붙여넣고 **내 캐글 연결**을 누르세요.\n\n'
                        '토큰이 없는 경우에만 [캐글 API 설정](https://www.kaggle.com/settings/api)에서 발급받습니다.')
            method = st.radio('연결 방법', ['API 토큰 붙여넣기', 'kaggle.json 파일 등록'],
                key='kaggle_auth_method', disabled=busy)
            token, uploaded = '', None
            if method == 'API 토큰 붙여넣기':
                token = st.text_input('캐글 API 토큰', type='password', key='kaggle_api_token', disabled=busy)
            else:
                st.caption('캐글 API 설정의 Legacy API Credentials → Create Legacy API Key로 받은 파일입니다.')
                uploaded = st.file_uploader('kaggle.json 선택', type=['json'],
                    key='kaggle_credentials_' + str(st.session_state.get('_kaggle_upload_revision', 0)), disabled=busy)
            if st.button('🔗 내 캐글 연결', key='kaggle_connect', disabled=busy, type='primary', use_container_width=True):
                try:
                    if method == 'API 토큰 붙여넣기':
                        if not token.strip() or len(token) > 8192 or any(c.isspace() for c in token.strip()):
                            raise ValueError('명령어 전체가 아니라 발급된 토큰 값만 붙여넣어주세요.')
                        supplied = {'token': token.strip()}
                    else:
                        if uploaded is None or uploaded.size > 16384:
                            raise ValueError('캐글에서 받은 kaggle.json 파일을 선택해주세요.')
                        parsed = json.loads(uploaded.getvalue())
                        if not isinstance(parsed, dict) or not all(isinstance(parsed.get(k), str) and parsed[k].strip() for k in ('username', 'key')):
                            raise ValueError('username과 key가 들어 있는 kaggle.json 파일이 필요합니다.')
                        supplied = {k: parsed[k].strip() for k in ('username', 'key')}
                    with st.spinner('내 캐글 계정 연결 확인 중…'):
                        st.session_state['_kaggle_credentials'] = kaggle_jobs.authenticate(supplied)
                except kaggle_jobs.KaggleError as exc:
                    st.error(kaggle_jobs._error_message(exc))
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        st.caption('접속한 사람마다 자신의 캐글 계정을 연결합니다. 입력한 인증정보는 현재 접속에서만 사용합니다.')
        st.caption('GPU가 잠겨 있으면 캐글 휴대폰 인증과 사용 가능 시간을 확인하세요. 생성 요청은 내 계정의 GPU 시간을 사용합니다.')
        if credentials:
            with st.expander('이전 캐글 작업 불러오기'):
                ref = st.text_input('캐글 작업 주소', key='kaggle_restore_ref',
                    placeholder='https://www.kaggle.com/code/내아이디/voice-studio-…')
                if st.button('상태·결과 불러오기', key='kaggle_restore', disabled=busy):
                    try:
                        kaggle_jobs.restore(work_dir, ref, credentials)
                    except (ValueError, OSError) as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
        guide = ROOT / 'Kaggle_CosyVoice_Guide.txt'
        if guide.is_file():
            st.download_button('⬇️ 캐글 사용법', guide.read_bytes(),
                file_name=guide.name, mime='text/plain', key='kaggle_guide')
        with st.expander('수동 실행용 노트북 · 선택 사항'):
            st.caption('공유 사이트에서 생성할 때는 이 파일을 받을 필요가 없습니다.')
            for engine in NOTEBOOKS:
                notebook_download(engine, 'kaggle_notebook_sidebar_' + engine)


def prepare_plan(segments, settings, pause_ms, include_speaker):
    """Export selected identities and conditioning, never URLs or API keys."""
    if not 1 <= len(segments) <= MAX_ITEMS:
        raise ValueError('캐글 대본은 1~4096개 대사까지 지원합니다.')
    items, references, by_path = [], {}, {}
    seen = set()
    for segment in segments:
        row = settings.get(segment.speaker, {})
        engine = row.get('engine')
        if engine not in MODELS:
            raise ValueError('캐글 생성은 선택한 모든 화자가 CosyVoice 2 또는 3일 때 사용할 수 있습니다.')
        if type(segment.index) is not int or segment.index < 1 or segment.index in seen:
            raise ValueError('대사 순번이 중복되거나 잘못되었습니다. 대본을 다시 분석해주세요.')
        seen.add(segment.index)
        config = VoiceConfig(engine=engine, voice=row.get('voice', ''),
            style=row.get('style', '🎤 기본'), speed_factor=float(row.get('speed', 1)),
            ref_audio_path=row.get('ref_audio_path', ''), prompt_text=row.get('prompt_text', ''))
        if engine == 'cosyvoice':
            prepared = TTSEngine.cosyvoice_request(segment.text, config)
            payload = {key: prepared[key] for key in ('text', 'prompt_text', 'speed', 'style_instruction')}
            ref_path = prepared['ref_path']
        else:
            payload = request_payload(clean_spoken_text(segment.text), config)
            ref_path = config.ref_audio_path if config.voice == 'custom' else ''
        text = payload['text']
        low, high = (0.5, 2.0) if engine == 'cosyvoice' else (0.8, 1.2)
        if not text.strip() or len(text) > (2000 if engine == 'cosyvoice' else 12000):
            raise ValueError(f'{segment.index}번 대사가 비어 있거나 너무 깁니다. 대사를 나눠주세요.')
        if '<|' in text or '|>' in text:
            raise ValueError(f'{segment.index}번 대사에서 모델 제어 문자를 제거해주세요.')
        if not math.isfinite(payload['speed']) or not low <= payload['speed'] <= high:
            raise ValueError(f'{segment.speaker}: 말하기 속도를 {low}~{high}로 설정해주세요.')
        needs_ref = engine == 'cosyvoice' or payload.get('voice') == 'custom'
        if needs_ref:
            path = Path(ref_path) if ref_path else None
            if not path or not path.is_file() or not payload.get('prompt_text', '').strip():
                raise ValueError(f'{segment.speaker}: 참고 음성과 실제 녹음 대사를 등록해주세요.')
            if str(path) not in by_path:
                if not 0 < path.stat().st_size <= MAX_REFERENCE_BYTES:
                    raise ValueError(f'{segment.speaker}: 참고 음성은 10MB 이하여야 합니다.')
                data = path.read_bytes()
                ref_id = hashlib.sha256(data).hexdigest()
                references[ref_id] = data
                by_path[str(path)] = ref_id
                if len(references) > 64 or sum(map(len, references.values())) > MAX_REFERENCES_BYTES:
                    raise ValueError('참고 음성은 최대 64개, 합계 64MB까지 넣을 수 있습니다.')
            payload['reference_id'] = by_path[str(path)]
        items.append(dict(payload, engine=engine, index=segment.index, speaker=segment.speaker))
    plan = dict(format=FORMAT, exporter_version=VERSION, bank_revision=BANK_REVISION,
        items=sorted(items, key=lambda item: item['index']),
        references={key: 'references/' + key + '.audio' for key in references},
        pause_ms=int(pause_ms), include_speaker=bool(include_speaker), generation_nonce='')
    return plan, references


def project_zip(plan, references):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr('plan.json', json.dumps(plan, ensure_ascii=False, indent=2))
        for key, data in references.items():
            archive.writestr(plan['references'][key], data)
        archive.writestr('VOICE_ATTRIBUTION.txt', ATTRIBUTION)
    return out.getvalue()


def _start(work_dir, segments, settings, pause_ms, include_speaker, force=False, preview=False):
    plan, references = prepare_plan(segments, settings, pause_ms, include_speaker)
    engines = {row['engine'] for row in plan['items']}
    engine = next(iter(engines)) if len(engines) == 1 else 'auto'
    state = kaggle_jobs.start_job(work_dir, plan, project_zip(plan, references),
        st.session_state.get('_kaggle_credentials'), NOTEBOOKS[engine][1], force=force, preview=preview)
    st.session_state['generation_result'] = None
    st.session_state['_reset_bulk_overwrite'] = True
    st.session_state.pop('play_kaggle_result', None)
    return state


def start_preview(work_dir, speaker, text, settings):
    try:
        if not text.strip():
            raise ValueError('미리듣기에서 읽을 대사를 입력해주세요.')
        _start(work_dir, [SimpleNamespace(index=1, speaker=speaker, text=text)], settings, 0, False, preview=True)
    except (ValueError, OSError, kaggle_jobs.KaggleError) as exc:
        st.error(str(exc))
    else:
        st.rerun()


def render_export(segments, settings, pause_ms, include_speaker, force_overwrite, busy, work_dir):
    engines = {settings.get(segment.speaker, {}).get('engine') for segment in segments}
    if not use_kaggle() or not engines.intersection(MODELS):
        return False
    if not engines.issubset(MODELS):
        st.warning('캐글 생성은 코지2·3 화자만 지원합니다. 생성 범위를 코지 화자로 선택하거나, 다른 엔진도 함께 만들려면 왼쪽에서 기존 코랩 연결을 선택해주세요.')
        return True
    st.info('이 사이트에서 생성 → 캐글 GPU 2개가 계산 → 완성된 MP3 한 파일을 이 사이트에서 받습니다.')
    st.caption('캐글 새 작업마다 GPU 배정·설치·모델 준비 시간이 필요합니다. 미리듣기도 같은 준비 과정을 거칩니다.')
    if len(engines) > 1:
        st.caption('코지2를 두 GPU로 생성한 다음 코지3를 생성하고, 대본 순번대로 합칩니다.')
    connected = bool(st.session_state.get('_kaggle_credentials'))
    if not connected:
        st.warning('왼쪽 ‘🚀 코지2·3 · 캐글 연결’을 열어 내 캐글 계정을 연결해주세요.')
    if st.button(f'🚀 이 사이트에서 음성 생성 · 캐글 GPU 2개 ({len(segments)}개 대사)',
                 key='kaggle_generate', type='primary', use_container_width=True, disabled=busy or not connected):
        try:
            _start(work_dir, segments, settings, pause_ms, include_speaker, force_overwrite)
        except (OSError, ValueError, TypeError, kaggle_jobs.KaggleError) as exc:
            st.error(str(exc))
        else:
            st.rerun()
    with st.expander('수동 실행용 대본 받기 · 선택 사항'):
        st.caption('자동 연결을 이용할 때는 대본 파일을 옮기거나 캐글에서 Run All을 누를 필요가 없습니다.')
        if st.button('수동 대본 파일 준비', key='kaggle_prepare_manual', disabled=busy):
            try:
                plan, references = prepare_plan(segments, settings, pause_ms, include_speaker)
                if force_overwrite:
                    plan['generation_nonce'] = secrets.token_hex(16)
                st.session_state['_cosy_kaggle_manual_zip'] = project_zip(plan, references)
            except (OSError, ValueError, TypeError) as exc:
                st.caption(str(exc))
        if st.session_state.get('_cosy_kaggle_manual_zip'):
            st.download_button('⬇️ 캐글용 대본·목소리 받기', st.session_state['_cosy_kaggle_manual_zip'],
                file_name='CosyVoice_Kaggle_Project.zip', mime='application/zip', key='kaggle_project', disabled=busy)
            st.caption('마지막으로 ‘수동 대본 파일 준비’를 눌렀을 때의 설정입니다. 설정 변경 후에는 파일을 다시 준비하세요.')
    return True


def render_status(work_dir):
    initial = kaggle_jobs.get_job(work_dir)
    if not initial:
        return
    credentials = st.session_state.get('_kaggle_credentials')
    active = kaggle_jobs.is_running(work_dir)
    old_conflict = (initial.get('status') == 'needs_check'
        and not initial.get('conflict_recovery_version')
        and (initial.get('diagnostic', {}).get('http_status') == 409 or '409' in initial.get('error', '')))
    old_access_error = (initial.get('status') == 'needs_check'
        and not initial.get('access_recovery_version')
        and initial.get('diagnostic', {}).get('http_status') in (401, 403))
    if (active or old_conflict or old_access_error) and credentials and not kaggle_jobs.monitoring(work_dir):
        try:
            kaggle_jobs.reconnect(work_dir, credentials)
        except ValueError:
            pass
        active = kaggle_jobs.is_running(work_dir)

    @st.fragment(run_every=3 if active else None)
    def panel():
        job = kaggle_jobs.get_job(work_dir)
        if not job:
            return
        if active and not kaggle_jobs.is_running(work_dir):
            st.rerun()
        st.divider()
        st.markdown('### 🎧 캐글 미리듣기' if job.get('preview') else '### 🎧 캐글 생성 진행·완성 음성')
        done, total = job.get('done', 0), job.get('total', 0)
        if total:
            st.progress(min(1.0, done / total), text=f'완료 확인 {done} / {total}개 대사')
        if job['status'] == 'complete':
            st.success(job['message'])
            result = job['result']
            if st.checkbox('완성 음성 들어보기', key='play_kaggle_result'):
                st.audio(result['full_audio'], format='audio/mp3')
            st.download_button('⬇️ 전체 대사 MP3 한 파일 받기', Path(result['full_audio']).read_bytes,
                file_name='preview.mp3' if job.get('preview') else 'full_audio.mp3', mime='audio/mpeg',
                type='primary', use_container_width=True, key='kaggle_mp3', on_click='ignore')
            with st.expander('자막·완성본 묶음 받기'):
                for key, label, name, mime in [('srt', 'SRT 자막 받기', 'subtitles.srt', 'text/plain'),
                    ('vtt', 'VTT 자막 받기', 'subtitles.vtt', 'text/vtt'),
                    ('main_zip', 'MP3 + 자막 묶음 받기', 'complete_audio.zip', 'application/zip')]:
                    st.download_button(label, Path(result[key]).read_bytes, file_name=name, mime=mime,
                        key='kaggle_result_' + key, on_click='ignore')
        elif job.get('error'):
            st.error(job['error'])
            if job.get('submission') == 'not_created':
                st.info('이전 작업 때문에 막혔던 생성 제한을 해제했습니다. 위의 생성 또는 미리듣기 버튼을 다시 누르세요.')
        else:
            st.info(job.get('message', '캐글에서 작업 중입니다.'))
        st.markdown('[내 캐글 작업 열기](https://www.kaggle.com/code/' + job['ref'] + ')')
        st.caption('이 작업 주소를 보관하면 사이트에 다시 접속한 후 왼쪽 ‘이전 캐글 작업 불러오기’에서 결과를 받을 수 있습니다.')
        if job['status'] in kaggle_jobs.ACTIVE:
            elapsed = max(0, int(time.time() - job['started']))
            st.caption(f'경과 {elapsed // 60}분 {elapsed % 60}초 · 진행 상황은 자동 갱신됩니다. 캐글 기록 반영은 지연될 수 있습니다.')
            st.caption('중단하려면 ‘내 캐글 작업 열기’에서 실행 중인 작업을 중지하세요. 사이트를 닫아도 제출된 캐글 작업은 계속됩니다.')
        if job['status'] in ('failed', 'needs_check'):
            if job['status'] == 'needs_check':
                st.caption('아래 버튼으로 기존 작업을 확인합니다. 실제 작업이 있으면 이어받고, 등록되지 않은 것이 확인되면 다시 생성할 수 있습니다.')
            if st.button('상태·결과 다시 확인', key='kaggle_reconnect', disabled=not credentials):
                try:
                    kaggle_jobs.reconnect(work_dir, credentials)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            if job.get('resume'):
                st.caption('완료된 대사는 저장했습니다. 대본·설정을 그대로 두고 생성 버튼을 누르면 저장된 대사를 이어서 사용합니다.')
                st.download_button('이어하기 파일 보관', Path(job['resume']).read_bytes,
                    file_name='resume.zip', mime='application/zip', key='kaggle_resume', on_click='ignore')
            details = [('현재 확인 오류', job.get('diagnostic')),
                       ('최초 생성 요청 오류', job.get('submission_diagnostic'))]
            if any(detail for _, detail in details):
                with st.expander('캐글 오류 상세 보기'):
                    if job.get('account_authenticated'):
                        st.write('기존 토큰의 계정 인증: 통과')
                    for label, detail in details:
                        if not detail:
                            continue
                        st.write(label)
                        phase = detail.get('stage') or detail.get('operation')
                        st.write('발생 단계: ' + kaggle_jobs.OPERATION_LABELS.get(phase, '요청 처리'))
                        if detail.get('http_status'):
                            st.write('응답 코드: ' + str(detail['http_status']))
                        st.code(detail.get('reason', ''), language=None)
        if job.get('logs'):
            with st.expander('캐글 실행 기록 보기'):
                st.code(job['logs'], language=None)
    panel()
