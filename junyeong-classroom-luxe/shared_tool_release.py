"""Stage a verified tool release on the existing private Railway data volume.

Drive is used only to transfer the owner-supplied release once. Member downloads
use the verified local ZIP and never redirect to Drive. No credentials are logged.
"""
from pathlib import Path
import hashlib
import json
import logging
import os
import tempfile
import threading
import zipfile

RELEASES = {
    'yadam-image-prompts-v2.7.4.zip': {
        'drive_id': '1GGHCJoX5RdN8ApxwPakp2fZPkgF1PxKp',
        'size': 5889889,
        'sha256': '71de06695d7bf3b00c1e780a13aefbbe474db7b0c7acf3f45784fb3de3871494',
    },
}
_LOCK = threading.Lock()
_VERIFIED = {}
_LOG = logging.getLogger('maison.shared_tools')


def _fingerprint(path):
    info = path.stat()
    return (info.st_size, info.st_mtime_ns)


def _valid(path, release):
    if not path.is_file() or path.stat().st_size != release['size']:
        return False
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == release['sha256']


def _download_to(release, destination):
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    raw = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
    if not raw:
        raise RuntimeError('Existing release-transfer credentials are not configured')
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=['https://www.googleapis.com/auth/drive.readonly'])
    with AuthorizedSession(credentials) as session:
        url = 'https://www.googleapis.com/drive/v3/files/' + release['drive_id']
        with session.get(url, params={'alt': 'media', 'supportsAllDrives': 'true'},
                         stream=True, timeout=(10, 60)) as response:
            if response.status_code != 200:
                raise RuntimeError('Release transfer HTTP ' + str(response.status_code))
            total = 0
            digest = hashlib.sha256()
            with destination.open('wb') as output:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > release['size']:
                        raise ValueError('Release size exceeds the approved file')
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if total != release['size'] or digest.hexdigest() != release['sha256']:
                raise ValueError('Release bytes do not match the approved file')


def ensure_release(asset, data_root):
    """Return only the exact owner-supplied ZIP; failed transfers never replace it."""
    if asset not in RELEASES:
        raise ValueError('Unregistered release')
    release = RELEASES[asset]
    directory = Path(data_root) / 'shared-tools'
    path = directory / asset
    with _LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        if path.is_file() and _VERIFIED.get(str(path)) == _fingerprint(path):
            return path
        if not _valid(path, release):
            fd, name = tempfile.mkstemp(prefix='release-', suffix='.tmp', dir=directory)
            os.close(fd)
            temp = Path(name)
            try:
                _download_to(release, temp)
                if not _valid(temp, release):
                    raise ValueError('Release checksum verification failed')
                with zipfile.ZipFile(temp) as archive:
                    if archive.testzip() is not None:
                        raise ValueError('Release ZIP integrity check failed')
                temp.chmod(0o600)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        _VERIFIED[str(path)] = _fingerprint(path)
        _LOG.warning('Shared tool ready: %s bytes=%s sha256=%s',
                     asset, release['size'], release['sha256'])
        return path
