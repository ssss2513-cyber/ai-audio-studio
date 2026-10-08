"""Visible recovery controls and private job history; reads never submit work."""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import time

import streamlit as st

from . import kaggle_history as history
from . import kaggle_jobs as jobs
from .result_downloads import saved_file_download

KST = timezone(timedelta(hours=9))
STATUS = {'uploading': '대본 전송', 'dataset_ready': '대본 준비', 'submitting': 'GPU 요청',
    'queued': 'GPU 배정 대기', 'running': '캐글 실행 중', 'receiving': '결과 수신·합치기',
    'checking': '기존 작업 확인 중', 'needs_check': '상태 확인 필요', 'failed': '실패·중단',
    'complete': '완료', 'canceled': '중지 완료', 'cancelled': '중지 완료',
    'stopping': '캐글 중지 처리 중', 'cancel_requested': '캐글 중지 요청됨',
    'cancel_acknowledged': '캐글 중지 완료'}


def _time(value):
    try:
        if isinstance(value, (float, int)):
            date = datetime.fromtimestamp(value, timezone.utc)
        else:
            date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        return date.astimezone(KST).strftime('%m/%d %H:%M:%S')
    except (ValueError, TypeError, OverflowError, OSError):
        return '확인 전'


def _clean(value):
    text = str(value or '')
    credentials = st.session_state.get('_kaggle_credentials') or {}
    for name in ('token', 'key'):
        if credentials.get(name):
            text = text.replace(credentials[name], '[인증정보]')
    return re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?[비공개]', text)


def clean_message(value):
    return _clean(value)


def _label(state):
    name = state.get('label') or ('코지2·3 동시 생성' if state.get('kind') == 'parallel_cosy'
        else '미리듣기' if state.get('preview') else '음성 생성')
    return _clean(name)


def _registration(state):
    if state.get('kind') == 'parallel_cosy':
        confirmed = sum(jobs.job_registered(child) for child in
                        (state.get('children') or {}).values() if child)
        return f'{confirmed}/2개 등록 확인'
    if jobs.job_registered(state):
        return '캐글 등록 확인'
    return {'not_created': '등록 거절·미생성', 'not_sent': 'GPU 요청 전'}.get(
        state.get('submission'), '등록 확인 중')


def _events(state):
    return [{'시각 (한국)': _time(event.get('at')), '단계': STATUS.get(event.get('status'), event.get('status', '')),
        '완료': f"{event.get('done', 0)}/{event.get('total', 0)}",
        '오류 단계': jobs.OPERATION_LABELS.get(event.get('operation'), event.get('operation', '')),
        '응답 코드': str(event.get('http_status') or ''),
        '내용': _clean(event.get('message')), '오류': _clean(event.get('error'))}
        for event in reversed(state.get('timeline') or [])]


def _report(state):
    lines = ['Voice Studio 캐글 작업 기록', '시각은 한국 시간입니다.',
             '작업: ' + _label(state), '작업 번호: ' + str(state.get('id', '')),
             '상태: ' + STATUS.get(state.get('status'), str(state.get('status', ''))),
             '최초 시작 (한국): ' + _time(state.get('started')),
             '등록 확인: ' + ('확인됨' if jobs.job_registered(state) else '미확인'),
             '제출 상태: ' + str(state.get('submission', '이전 기록에 없음')),
             '대본 데이터 주소: ' + str(state.get('dataset_ref') or state.get('ref', '')),
             '캐글 주소: ' + str(state.get('ref', '')), '안내: ' + _clean(state.get('message')),
             '오류: ' + _clean(state.get('error'))]
    for label, detail in [('현재 오류 상세', state.get('diagnostic')),
                          ('최초 생성 요청 오류', state.get('submission_diagnostic'))]:
        if detail:
            phase = detail.get('stage') or detail.get('operation', '')
            lines.extend(['', '[' + label + ']', '단계: ' + jobs.OPERATION_LABELS.get(phase, phase),
                          '응답 코드: ' + str(detail.get('http_status') or ''), _clean(detail.get('reason'))])
    lines.extend(['', '[단계별 기록]'])
    for event in _events(state):
        lines.append(' | '.join(str(value) for value in event.values()))
    lines.extend(['', '[최근 실행 로그]', _clean(state.get('logs'))])
    if state.get('log_error'):
        lines.append('실행 로그 조회 오류: ' + _clean(state['log_error']))
    for engine, child in (state.get('children') or {}).items():
        if child:
            lines.extend(['', '[' + engine + ']', _report(child)])
    return '\n'.join(lines)


def error_text(state):
    diagnostic = state.get('diagnostic') or {}
    if (state.get('account_authenticated') and state.get('status') == 'needs_check'
            and diagnostic.get('stage') == 'kernel_info'
            and diagnostic.get('http_status') in (401, 403)):
        return ('계정 인증은 통과했지만 캐글 작업의 등록·접근 상태를 확인하지 못했습니다. '
                '바로 아래 ‘오류 기록 TXT 받기’를 눌러 최초 생성 요청 오류를 확인해주세요.')
    return _clean(state.get('error') or diagnostic.get('reason'))


def render_record_download(state, key):
    safe_id = re.sub(r'[^A-Za-z0-9_-]', '_', str(state.get('id', 'job')))[:80]
    failed = state.get('status') in ('failed', 'needs_check')
    st.download_button('⬇️ 오류 기록 TXT 받기' if failed else '⬇️ 이 작업 기록 TXT 받기',
        _report(state).encode('utf-8-sig'), file_name='kaggle_job_' + safe_id + '.txt',
        mime='text/plain', key=key + '_report', on_click='ignore',
        type='primary' if failed else 'secondary', use_container_width=True)
    if failed:
        detail = state.get('submission_diagnostic') or {}
        original = detail.get('reason') or state.get('submission_error')
        if original:
            code = detail.get('http_status')
            st.caption('최초 생성 요청 오류' + (f' · 응답 코드 {code}' if code else ''))
            st.code(_clean(original), language=None)


def render_job_link(state, label='캐글 원본 작업 열기'):
    ref = state.get('ref')
    if not ref or not jobs.REF_PATTERN.fullmatch(ref):
        return
    if jobs.job_registered(state):
        st.link_button(label, 'https://www.kaggle.com/code/' + ref, use_container_width=True)
    else:
        st.caption('캐글 작업 등록 미확인 · 아래는 등록 요청에 사용한 주소입니다.')
        st.code(ref, language=None)


def render_stop_controls(state):
    """Open the exact job's native stop control; never pretend this cancels it.

    The official SDK's cancel operation requires a numeric kernel_session_id.
    SaveKernel/GetKernelSessionStatus do not provide that identity for batch
    pushes. A notebook ID or relative version number is NOT a session ID.
    Do not guess, delete the notebook, or mark local polling as GPU shutdown.
    """
    children = state.get('children') if state.get('kind') == 'parallel_cosy' else None
    rows = list((children or {}).items()) if children is not None else [('', state)]
    shown = False
    credentials = st.session_state.get('_kaggle_credentials') or {}
    for engine, child in rows:
        if not child or child.get('status') not in jobs.ACTIVE | {'needs_check'}:
            continue
        ref = child.get('ref', '')
        if not jobs.REF_PATTERN.fullmatch(ref) or child.get('submission') in ('not_sent', 'not_created'):
            continue
        if credentials and ref.split('/')[0].lower() != credentials.get('username', '').lower():
            continue
        label = {'cosyvoice': '코지2', 'cosyvoice3': '코지3'}.get(engine, '현재 작업')
        st.link_button('⏹ ' + label + ' 중지 화면 열기', 'https://www.kaggle.com/code/' + ref,
                       use_container_width=True)
        shown = True
    if shown:
        st.caption('위 버튼으로 해당 캐글 작업을 연 뒤, 실행 중인 버전을 취소하세요. '
                   '링크를 여는 것만으로는 GPU가 멈추지 않습니다. 두 모델을 모두 멈추려면 각각 취소하세요. '
                   '사이트는 실제 중지 상태를 확인해 표시합니다. 강제 종료 시 저장 전 음성은 남지 않을 수 있습니다.')


def _details(state, key):
    diagnostic = state.get('diagnostic') or {}
    reason = error_text(state)
    if reason:
        st.error(reason)
    if state.get('preview') and state.get('status') == 'complete':
        audio_path = Path(state.get('result', {}).get('full_audio') or '')
        if audio_path.is_file():
            st.audio(str(audio_path), format='audio/mp3')
            saved_file_download('⬇️ 이 미리듣기 MP3 받기', str(audio_path),
                file_name='preview.mp3', mime='audio/mpeg', key=key + '_preview_audio')
    render_record_download(state, key)
    render_stop_controls(state)
    if diagnostic.get('stage') or diagnostic.get('operation'):
        stage = diagnostic.get('stage') or diagnostic['operation']
        code = diagnostic.get('http_status')
        st.caption('오류 단계: ' + jobs.OPERATION_LABELS.get(stage, stage)
                   + (f' · 응답 코드 {code}' if code else ''))
    if state.get('history_warning'):
        st.warning(state['history_warning'])
    render_job_link(state)
    for engine, child in (state.get('children') or {}).items():
        if child:
            render_job_link(child, ('코지2' if engine == 'cosyvoice' else '코지3') + ' 캐글 작업 열기')
            render_record_download(child, key + '_' + engine)
    if state.get('children'):
        st.dataframe([{'모델': '코지2' if engine == 'cosyvoice' else '코지3',
            '상태': STATUS.get(child.get('status'), child.get('status', '')),
            '완료': f"{child.get('done', 0)}/{child.get('total', 0)}개"}
            for engine, child in state['children'].items() if child],
            hide_index=True, use_container_width=True)
    with st.expander('단계별 이력·오류·실행 로그'):
        for label, detail in [('현재 오류 응답', diagnostic),
                              ('최초 생성 요청 오류', state.get('submission_diagnostic'))]:
            if detail and detail.get('reason'):
                st.caption(label)
                st.code(_clean(detail['reason']), language=None)
        rows = _events(state)
        if rows:
            st.dataframe(rows, hide_index=True, use_container_width=True, height=230)
        else:
            st.caption('업데이트 이전 작업은 현재 상태와 남아 있는 로그를 표시합니다. 새 단계부터 이력을 기록합니다.')
        if state.get('log_error'):
            st.warning('캐글 실행 로그 조회 오류: ' + _clean(state['log_error']))
        if state.get('logs'):
            st.code(_clean(state['logs']), language=None)
        for engine, child in (state.get('children') or {}).items():
            if child:
                st.write(('코지2' if engine == 'cosyvoice' else '코지3') + ' · '
                         + STATUS.get(child.get('status'), child.get('status', '')))
                if child.get('error'):
                    st.error(_clean(child['error']))
                child_events = _events(child)
                if child_events:
                    st.dataframe(child_events, hide_index=True, use_container_width=True, height=180)
                if child.get('logs'):
                    st.code(_clean(child['logs']), language=None)


def _check_current(work_dir, key):
    credentials = st.session_state.get('_kaggle_credentials')
    if st.button('🔄 이전 작업 상태 확인·결과 받기', key=key + '_check',
                 disabled=not credentials or jobs.monitoring(work_dir), use_container_width=True):
        try:
            jobs.reconnect(work_dir, credentials)
        except (ValueError, OSError, jobs.KaggleError) as exc:
            st.error(_clean(exc))
        else:
            st.rerun()


def render_job_blocker(work_dir, key):
    initial = jobs.get_job(work_dir)
    if not initial:
        return
    active = jobs.is_running(work_dir) or jobs.monitoring(work_dir)

    @st.fragment(run_every=3 if active else None)
    def panel():
        state = jobs.get_job(work_dir)
        if not state:
            return
        if active and not jobs.is_running(work_dir) and not jobs.monitoring(work_dir):
            st.rerun()
        st.caption('이전 작업 · ' + _label(state) + ' · ' + _time(state.get('started')))
        if state.get('status') == 'needs_check':
            st.warning('이전 작업 확인이 끝나야 새 생성을 시작할 수 있습니다. 바로 아래 버튼을 눌러주세요.')
        elif state.get('status') in jobs.ACTIVE:
            st.info(state.get('message') or '기존 작업을 확인하고 있습니다.')
        elif state.get('status') == 'complete':
            st.success('이전 작업이 완료됐습니다. 아래 완성 결과를 받거나 미리듣기 버튼을 다시 누르세요.')
        elif state.get('submission') in ('not_sent', 'not_created'):
            st.info('실행 중인 GPU 작업이 없는 것으로 확인됐습니다. 미리듣기 또는 생성 버튼을 다시 누르세요.')
        else:
            st.info(state.get('message') or '이전 작업 기록입니다.')
        _check_current(work_dir, key)
        _details(state, key)
    panel()


def render_preview_recovery(work_dir, speaker):
    def matches(job):
        return bool(job and job.get('preview') and
                    (job.get('preview_speaker') == speaker or job.get('label') == '미리듣기 · ' + speaker))

    initial = jobs.get_job(work_dir)
    notice = st.session_state.get('_kaggle_preview_notice') or {}
    if (not matches(initial) and notice.get('speaker') != speaker
            and st.session_state.get('_kaggle_preview_help') != speaker):
        return
    tag = hashlib.sha256(speaker.encode()).hexdigest()[:12]
    key = 'preview_recovery_' + tag
    active = jobs.is_running(work_dir) or jobs.monitoring(work_dir)

    @st.fragment(run_every=3 if active else None)
    def panel():
        job = jobs.get_job(work_dir)
        if active and not jobs.is_running(work_dir) and not jobs.monitoring(work_dir):
            st.rerun()
        with st.container(border=True):
            st.markdown('**🎧 ' + speaker + ' 미리듣기 진행·결과**')
            request = st.session_state.get('_kaggle_preview_notice') or {}
            if request.get('speaker') == speaker:
                st.caption('버튼 요청 시각: ' + _time(request.get('at')))
                if request.get('error'):
                    st.error(_clean(request['error']))
                elif request.get('status') == 'preparing':
                    st.info(request.get('message', '미리듣기 요청을 준비하고 있습니다.'))
            if matches(job):
                st.write('**' + STATUS.get(job.get('status'), '상태 확인 중') + '**')
                st.caption('작업 번호: ' + str(job.get('id', '')))
                if job.get('status') == 'complete':
                    path = Path(job.get('result', {}).get('full_audio') or '')
                    if path.is_file():
                        st.success('미리듣기가 준비됐습니다. 아래 ▶ 버튼으로 들어보세요.')
                        st.audio(str(path), format='audio/mp3')
                        saved_file_download('⬇️ 이 미리듣기 MP3 받기', str(path),
                            file_name='preview.mp3', mime='audio/mpeg', key=key + '_audio')
                    else:
                        st.warning('완료 기록은 있지만 음성 파일을 찾지 못했습니다. 아래에서 결과를 다시 받아주세요.')
                        _check_current(work_dir, key)
                elif job.get('error'):
                    _details(job, key)
                    _check_current(work_dir, key)
                else:
                    st.info(job.get('message') or '캐글 작업 상태를 확인하고 있습니다.')
                    if not jobs.job_registered(job):
                        st.caption('사이트에서 요청을 접수했습니다. 캐글 GPU 작업 등록은 확인 중입니다.')
                    done, total = job.get('done', 0), job.get('total', 1)
                    st.progress(min(1.0, done / max(1, total)), text=f'완료 확인 {done}/{total}개')
                    elapsed = max(0, int(time.time() - job.get('started', time.time())))
                    st.caption(f'경과 {elapsed // 60}분 {elapsed % 60}초 · 3초마다 표시 갱신')
                    render_job_link(job)
                    if not jobs.monitoring(work_dir):
                        _check_current(work_dir, key)
                    if job.get('logs'):
                        with st.expander('최근 캐글 실행 기록'):
                            st.code(_clean(job['logs'][-5000:]), language=None)
                if job.get('preview_text'):
                    st.caption('요청 대사: ' + _clean(job['preview_text']))
            elif job:
                st.info('현재 확인할 작업: ' + _label(job) + ' · '
                        + STATUS.get(job.get('status'), '상태 확인 중'))
                _check_current(work_dir, key)
            elif not request.get('error'):
                st.warning('아직 등록된 미리듣기 작업이 없습니다. 위 버튼을 눌러 요청해주세요.')
            st.markdown('[전체 작업 목록 보기](#kaggle-work-list)')
    panel()


def render_history(work_dir, enabled=False):
    credentials = st.session_state.get('_kaggle_credentials')
    initial = jobs.get_job(work_dir)
    if not enabled and not credentials and not initial:
        return
    active = jobs.is_running(work_dir) or jobs.monitoring(work_dir)

    @st.fragment(run_every=3 if active else None)
    def panel():
        current = jobs.get_job(work_dir)
        if active and not jobs.is_running(work_dir) and not jobs.monitoring(work_dir):
            st.rerun()
        with st.container(border=True):
            st.subheader('🗂 캐글 작업 목록', anchor='kaggle-work-list')
            st.caption('미리듣기와 전체 생성의 접수·진행·실패·완료 기록입니다. 진행 중에는 3초마다 자동 갱신됩니다.')
            st.button('🔄 작업 목록 화면 갱신', key='history_local_refresh', use_container_width=True)
            username = credentials['username'] if credentials else history.owner(current or {})
            local = history.local_jobs(work_dir, username, current) if username else []
            rows = [{'시작 (한국)': _time(row.get('started')), '작업': _label(row),
                     '상태': STATUS.get(row.get('status'), row.get('status', '')),
                     '완료': f"{row.get('done', 0)}/{row.get('total', 0)}",
                     '등록': _registration(row),
                     '작업 번호': str(row.get('id', ''))} for _, row in local]
            notice = st.session_state.get('_kaggle_preview_notice') or {}
            if notice and not notice.get('job_id'):
                rows.insert(0, {'시작 (한국)': _time(notice.get('at')),
                    '작업': '미리듣기 · ' + str(notice.get('speaker', '')),
                    '상태': '요청 준비 실패' if notice.get('error') else '요청 준비',
                    '완료': '0/1', '등록': '접수 미확인', '작업 번호': '생성 전'})
            if rows:
                st.dataframe(rows[:30], hide_index=True, use_container_width=True,
                             height=min(330, 40 + 36 * len(rows[:30])))
            else:
                st.info('이 접속에서 요청한 작업이 없습니다. 미리듣기 또는 생성 버튼을 누르면 여기에 표시됩니다.')
            if notice.get('error') and not notice.get('job_id'):
                st.error('최근 미리듣기 요청: ' + _clean(notice['error']))
            if current:
                st.write('**현재 작업: ' + _label(current) + '**')
                st.write(STATUS.get(current.get('status'), current.get('status', '')) + ' · '
                         f"완료 {current.get('done', 0)}/{current.get('total', 0)}개")
                st.caption(_clean(current.get('message')) + ' · 마지막 확인 ' + _time(current.get('updated')))
                _check_current(work_dir, 'history_current')
                _details(current, 'history_current')
            if not credentials:
                st.info('내 캐글 계정을 연결하면 이전 접속에서 만든 작업도 목록에서 찾을 수 있습니다.')
                return
            if local:
                with st.expander('이 접속에서 보관한 이전 작업'):
                    records = dict(local)
                    choice = st.selectbox('보관한 작업 선택', list(records), key='history_local_choice',
                        format_func=lambda value: _time(records[value].get('started')) + ' · '
                        + _label(records[value]) + ' · ' + STATUS.get(records[value].get('status'), '확인 필요'))
                    chosen = records[choice]
                    if not current or chosen.get('folder') != current.get('folder'):
                        _details(chosen, 'history_selected')
                    if st.button('보관 결과 열기·상태 이어받기', key='history_local_open',
                                 disabled=jobs.is_running(work_dir) or jobs.monitoring(work_dir), use_container_width=True):
                        try:
                            if current and chosen.get('folder') == current.get('folder'):
                                if current.get('status') != 'complete':
                                    jobs.reconnect(work_dir, credentials)
                            else:
                                jobs.restore_saved(work_dir, choice, credentials)
                        except (ValueError, OSError, jobs.KaggleError) as exc:
                            st.error(_clean(exc))
                        else:
                            st.session_state['generation_result'] = None
                            st.rerun()
            st.markdown('**내 캐글 계정의 이전 작업 찾기**')
            st.caption('사이트를 새로 열었어도 같은 계정의 작업을 찾습니다. 목록 조회와 불러오기는 음성을 새로 생성하지 않습니다.')
            cache_key = '_kaggle_account_jobs_' + username.lower()
            error_key = '_kaggle_account_jobs_error_' + username.lower()
            cached = st.session_state.get(cache_key, {})
            refresh = st.button('🔎 내 캐글 작업 목록 불러오기·새로고침', key='history_remote_refresh', use_container_width=True)
            more = bool(cached.get('next_page_token')) and st.button('이전 목록 더 보기', key='history_remote_more')
            if refresh or more:
                try:
                    with st.spinner('내 캐글 작업 목록을 읽고 있습니다…'):
                        response = jobs.recent_jobs(credentials, cached.get('next_page_token') if more and not refresh else None)
                    previous = cached.get('jobs', []) if more and not refresh else []
                    combined = {row['ref']: row for row in previous + response.get('jobs', [])}
                    cached = dict(response, jobs=list(combined.values()), checked=datetime.now(KST).isoformat())
                    st.session_state[cache_key] = cached
                    st.session_state.pop(error_key, None)
                except (ValueError, OSError, jobs.KaggleError) as exc:
                    st.session_state[error_key] = _clean(
                        jobs._error_message(exc) if isinstance(exc, jobs.KaggleError) else exc)
            if st.session_state.get(error_key):
                st.error('캐글 목록 조회 실패: ' + st.session_state[error_key])
            if cached:
                st.caption('목록 조회 시각: ' + _time(cached.get('checked')))
                rows = cached.get('jobs') or []
                if not rows:
                    st.info('이번 목록에서 Voice Studio 작업을 찾지 못했습니다. 이전 목록이 있으면 더 보기를 누르세요.')
                else:
                    by_ref = {row['ref']: row for row in rows}
                    st.dataframe([{'최근 실행 (한국)': _time(row.get('last_run')), '작업': row['ref']}
                                  for row in rows[:30]], hide_index=True, use_container_width=True, height=220)
                    chosen_ref = st.selectbox('불러올 캐글 작업', list(by_ref), key='history_remote_choice',
                        format_func=lambda value: _time(by_ref[value].get('last_run')) + ' · ' + value.split('/')[-1])
                    if st.button('선택 작업 상태·결과 불러오기', key='history_remote_open',
                                 disabled=jobs.is_running(work_dir) or jobs.monitoring(work_dir), use_container_width=True):
                        try:
                            jobs.restore(work_dir, chosen_ref, credentials)
                        except (ValueError, OSError, jobs.KaggleError) as exc:
                            st.error(_clean(exc))
                        else:
                            st.session_state['generation_result'] = None
                            st.rerun()
                    st.link_button('선택 작업을 캐글에서 열기', 'https://www.kaggle.com/code/' + chosen_ref,
                                   use_container_width=True)
                    st.caption('목록에는 캐글에 등록된 작업만 나옵니다. 제출 전 실패와 상세 단계 기록은 위의 현재 접속 이력에서 확인하세요.')
            st.caption('계정이 같아도 코지2·3 혼합 작업을 다시 합치려면 기존 ‘두 모델 작업 복구 파일’을 사용하세요.')
    panel()
