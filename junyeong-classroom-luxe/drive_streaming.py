"""Safe Google Drive byte-range and error classification utilities.

No authentication keys, file names or user data are logged by this module.
"""
from __future__ import annotations
import re


WINDOW_BYTES = 4 * 1024 * 1024
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
VALID_REASONS = frozenset({
    "rateLimitExceeded", "userRateLimitExceeded", "downloadQuotaExceeded",
    "dailyLimitExceeded", "quotaExceeded", "notFound", "insufficientFilePermissions",
    "fileNotDownloadable", "cannotDownloadFile", "cannotDownloadAbusiveFile",
    "forbidden", "appNotAuthorizedToFile", "backendError", "internalError",
})


def capped_range(header: str, size: int, window: int = WINDOW_BYTES):
    """Return a bounded inclusive range, or None if Range not supplied.

    A browser can request bytes=0- for a multi-GB file. We only ask Google Drive
    for a few MB per request and advertise precisely that interval as HTTP 206.
    """
    if not header:
        return None
    if size <= 0 or window <= 0:
        raise ValueError("invalid media size")
    match = RANGE_RE.fullmatch(header)
    if not match or not any(match.groups()):
        raise ValueError("unsupported Range header")
    a, b = match.groups()
    if a == "":
        suffix = int(b)
        if suffix <= 0:
            raise ValueError("invalid suffix range")
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(a)
        end = size - 1 if not b else min(int(b), size - 1)
    if start >= size or end < start:
        raise ValueError("unsatisfiable Range")
    end = min(end, start + window - 1, size - 1)
    return start, end


def known_reason(data):
    """Read only Google's bounded public reason code, not its freeform message."""
    if not isinstance(data, dict):
        return "unknown"
    err = data.get("error", {})
    if not isinstance(err, dict):
        return "unknown"
    candidates = err.get("errors", [])
    if isinstance(candidates, list):
        for item in candidates[:3]:
            if isinstance(item, dict):
                reason = item.get("reason", "")
                if reason in VALID_REASONS:
                    return reason
    return "unknown"


def should_retry(status: int, reason: str):
    """Temporary server/rate errors can be retried, but hard quota/ACL errors cannot."""
    return status in (429, 500, 502, 503, 504) or (
        status == 403 and reason in ("rateLimitExceeded", "userRateLimitExceeded")
    )


def content_range_start(header: str):
    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", header.strip())
    if not match:
        return None
    return int(match.group(1))
