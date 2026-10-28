"""Serve the verified owner-supplied ZIP committed alongside the application.
No network, runtime reconstruction, credential access, or provider calls.
"""
from pathlib import Path
import hashlib
import threading
import zipfile

ASSET = 'yadam-image-prompts-v2.7.8.zip'
SHA256 = '66077b98a3e25dfafac1b1c233e4b0b6de07e415376f5770bb92cdbdefd14112'
SIZE = 5919916
FILE_COUNT = 75
_LOCK = threading.Lock()
_VERIFIED = {}

def build_image278_package(package_root, data_root):
    path = Path(package_root) / ASSET
    with _LOCK:
        info = path.stat()
        fingerprint = (info.st_size, info.st_mtime_ns)
        if _VERIFIED.get(str(path)) == fingerprint:
            return path
        if info.st_size != SIZE or hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
            raise ValueError('Registered image tool 2.7.8 checksum mismatch')
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError('Registered image tool ZIP CRC failed')
            if len([i for i in archive.infolist() if not i.is_dir()]) != FILE_COUNT:
                raise ValueError('Registered image tool member count mismatch')
        _VERIFIED[str(path)] = fingerprint
        return path
