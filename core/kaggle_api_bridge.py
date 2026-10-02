"""One isolated, authenticated Kaggle API operation. Never imported by the UI.

Credentials arrive through this child process's environment, never notebook
code, argv, metadata, logs or a shared ~/.kaggle directory.
"""
import contextlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

CURRENT_STAGE = 'module_load'


def stage(name):
    global CURRENT_STAGE
    CURRENT_STAGE = name


def scrub(text):
    text = str(text)
    for name in ('KAGGLE_API_TOKEN', 'KAGGLE_KEY'):
        value = os.environ.get(name, '')
        if value:
            text = text.replace(value, '[인증정보]')
    text = re.sub(r'https?://\S+', '[주소]', text)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*|bearer\s+)\S+', r'\1[인증정보]', text)
    return text[:2400]


def safe_error(exc):
    # HTTPError.__str__ only gives "409 Conflict for url". Kaggle's useful
    # reason is in the response body; retain known message fields, not headers,
    # request bodies or the full response (which can contain signed URLs).
    response = getattr(exc, 'response', None)
    messages = []

    def collect(value, depth=0):
        if depth > 4:
            return
        if isinstance(value, str) and value.strip():
            messages.append(scrub(value.strip()))
        elif isinstance(value, dict):
            for key in ('message', 'error', 'detail', 'details', 'errors', 'reason'):
                if key in value:
                    collect(value[key], depth + 1)
        elif isinstance(value, list):
            for item in value[:5]:
                collect(item, depth + 1)

    if response is not None:
        try:
            collect(response.json())
        except (ValueError, TypeError):
            if 'text/plain' in response.headers.get('Content-Type', ''):
                collect(response.text)
    return scrub(' · '.join(dict.fromkeys(messages)) or str(exc))


def field(obj, name, fallback='', default=None):
    return getattr(obj, name, getattr(obj, fallback, default))


def output_page(api, ref, page_token=None):
    from kagglesdk.kernels.types.kernels_api_service import ApiListKernelSessionOutputRequest
    owner, slug = ref.split('/')
    request = ApiListKernelSessionOutputRequest()
    request.user_name, request.kernel_slug = owner, slug
    request.page_size = 100
    if page_token:
        request.page_token = page_token
    with api.build_kaggle_client() as client:
        return client.kernels.kernels_api_client.list_kernel_session_output(request)


def inspect_job(api, ref):
    """Separate account authentication from access to one particular kernel.

    Kaggle's own CLI notes that kernel status 401/403 can mean a wrong slug.
    A missing list/search entry alone is never proof that a job was not made.
    """
    from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest
    owner, slug = ref.split('/')
    if owner.lower() != api.get_config_value(api.CONFIG_NAME_USER).lower():
        raise ValueError('연결한 계정과 이 작업의 소유자가 다릅니다.')
    stage('account_check')
    page = api.kernels_list_with_response(mine=True, page_size=100, sort_by='dateCreated')
    stage('kernel_info')
    query = ApiGetKernelRequest()
    query.user_name, query.kernel_slug = owner, slug
    try:
        with api.build_kaggle_client() as client:
            result = client.kernels.kernels_api_client.get_kernel(query)
        return {'account_authenticated': True, 'exists': True,
                'ref': getattr(result.metadata, 'ref', None) or ref}
    except Exception as exc:
        code = getattr(getattr(exc, 'response', None), 'status_code', None)
        if code not in (401, 403, 404):
            raise
        reason = safe_error(exc)
    # Read the authenticated owner's inventory. Match only the exact unique
    # title/ref we submitted; never attach to a merely similar notebook.
    stage('job_lookup')
    seen = set()
    for _ in range(10):
        for item in page.kernels or []:
            candidate = getattr(item, 'ref', '') or ''
            if candidate.lower() == ref.lower() or getattr(item, 'title', '') == slug:
                return {'account_authenticated': True, 'exists': True, 'ref': candidate or ref}
        token = page.next_page_token
        if not token or token in seen:
            break
        seen.add(token)
        page = api.kernels_list_with_response(mine=True, page_size=100,
            sort_by='dateCreated', page_token=token)
    return {'account_authenticated': True, 'exists': False if code == 404 else None,
            'http_status': code, 'reason': reason}


def download_results(api, ref, destination):
    # The pinned CLI's downloader buffers whole files. Stream only our named
    # outputs and validate paths/status/size instead of downloading every WAV.
    import requests
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    allowed = re.compile(r'^(?:voice_studio_site_result\.json|voice_studio_results/[a-f0-9]+/(?:complete_audio\.zip|progress\.json|resume\.zip))$')
    token, seen, paths, used = None, set(), [], 0
    while True:
        response = output_page(api, ref, token)
        for item in response.files or []:
            name = item.file_name
            if not allowed.fullmatch(name) or '..' in PurePosixPath(name).parts:
                continue
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = target.with_suffix(target.suffix + '.part')
            with requests.get(item.url, stream=True, timeout=(20, 120)) as remote:
                remote.raise_for_status()
                size = 0
                with temporary.open('wb') as handle:
                    os.chmod(temporary, 0o600)
                    for block in remote.iter_content(1024 * 1024):
                        size += len(block)
                        used += len(block)
                        if used > 2 * 1024**3 or size > 1024**3:
                            raise ValueError('결과가 사이트 수신 한도를 넘습니다. 캐글 작업 링크에서 다운로드해주세요.')
                        handle.write(block)
                if remote.headers.get('Content-Length') and size != int(remote.headers['Content-Length']):
                    raise ValueError('결과 수신이 끊겼습니다. 결과 다시 받기를 눌러주세요.')
            os.replace(temporary, target)
            paths.append(str(target))
        token = response.next_page_token
        if not token:
            break
        if token in seen:
            raise RuntimeError('캐글 결과 목록이 반복되어 수신을 중단했습니다.')
        seen.add(token)
    return {'paths': paths}


def operate(api, request):
    operation = request['operation']
    if operation == 'auth':
        stage('account_check')
        api.kernels_list(mine=True, page_size=1)
        return {'username': api.get_config_value(api.CONFIG_NAME_USER)}
    if operation == 'inspect_job':
        return inspect_job(api, request['ref'])
    if operation == 'create_dataset':
        result = api.dataset_create_new(request['folder'], public=False, quiet=True, convert_to_csv=False)
        if result is None or result.error:
            raise ValueError(result.error if result else '캐글이 업로드 결과를 반환하지 않았습니다.')
        return {'created': True}
    if operation == 'dataset_status':
        return {'status': api.dataset_status(request['ref'])}
    if operation == 'push':
        result = api.kernels_push(request['folder'], acc='NvidiaTeslaT4')
        if result is None or result.error:
            raise ValueError(result.error if result else '캐글이 작업 제출 결과를 반환하지 않았습니다.')
        if field(result, 'invalid_dataset_sources', 'invalidDatasetSources'):
            raise ValueError('캐글이 대본 데이터를 연결하지 못했습니다. 작업 상태를 확인해주세요.')
        return {'version': field(result, 'version_number', 'versionNumber'), 'url': result.url}
    if operation == 'kernel_info':
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest
        owner, slug = request['ref'].split('/')
        query = ApiGetKernelRequest()
        query.user_name, query.kernel_slug = owner, slug
        try:
            with api.build_kaggle_client() as client:
                result = client.kernels.kernels_api_client.get_kernel(query)
        except Exception as exc:
            if getattr(getattr(exc, 'response', None), 'status_code', None) == 404:
                return {'exists': False}
            raise
        return {'exists': True, 'version': getattr(result.metadata, 'current_version_number', None)}
    if operation == 'status':
        # The CLI wrapper converts HTTP 401/403 into a generic ValueError,
        # losing the status needed to distinguish access errors from absence.
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusRequest
        owner, slug = request['ref'].split('/')
        query = ApiGetKernelSessionStatusRequest()
        query.user_name, query.kernel_slug = owner, slug
        with api.build_kaggle_client() as client:
            result = client.kernels.kernels_api_client.get_kernel_session_status(query)
        status = getattr(result.status, 'name', str(result.status)).lower().rsplit('.', 1)[-1]
        logs = ''
        try:
            logs = api.kernels_logs(request['ref']) or ''
            try:
                entries = json.loads(logs)
                if isinstance(entries, list):
                    logs = ''.join(str(row.get('data', row.get('text', ''))) if isinstance(row, dict) else str(row) for row in entries)
            except (TypeError, ValueError):
                pass
        except Exception:
            pass  # A temporary log failure must not submit a second GPU job.
        return {'status': status, 'error': result.failure_message or '', 'logs': logs[-48000:]}
    if operation == 'pull':
        return download_results(api, request['ref'], request['folder'])
    raise ValueError('알 수 없는 캐글 작업입니다.')


def main():
    request = json.loads(sys.stdin.read())
    # Kaggle prints some notices on import/authentication. Never forward those
    # notices (or HTTP debug logs) into the session's result object.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            stage('authentication')
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.config_values = {}
            if os.environ.get('KAGGLE_API_TOKEN'):
                connected = api._authenticate_with_access_token()
            else:
                username, key = os.environ.get('KAGGLE_USERNAME'), os.environ.get('KAGGLE_KEY')
                if not username or not key:
                    raise ValueError('내 캐글 API 토큰 또는 kaggle.json을 등록해주세요.')
                api.config_values.update({api.CONFIG_NAME_USER: username, api.CONFIG_NAME_KEY: key})
                connected = api._authenticate_with_legacy_apikey()
            if not connected:
                raise ValueError('입력한 인증정보로 캐글 계정을 확인하지 못했습니다. 토큰 재발급으로 해결되는지는 아직 확인되지 않았습니다.')
            api._authenticated = True
            stage(request['operation'])
            result = {'ok': True, 'result': operate(api, request)}
        except (Exception, SystemExit) as exc:
            response = getattr(exc, 'response', None)
            result = {'ok': False, 'error': safe_error(exc),
                      'http_status': getattr(response, 'status_code', None),
                      'operation': request.get('operation', ''), 'stage': CURRENT_STAGE}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
