"""Read pasted Gemini credentials without guessing Google's credential format.

Only remove copy/paste decoration. Credential validity belongs to Google, and
neither errors nor UI summaries may contain any part of a supplied credential.
"""
from __future__ import annotations

import re


class GeminiKeyInputError(ValueError):
    """Safe to display; never includes the supplied value."""


_COPY_MARKS = str.maketrans({
    "\ufeff": None, "\u200b": None, "\u200c": None, "\u200d": None, "\u2060": None,
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "，": ",", "；": ";", "、": ",", "：": ":",
})
_LABEL = re.compile(
    r"(?:^|(?<=[,;\s{\[]))(?:export\s+|set\s+)?[\"'`]?"
    r"(?:GEMINI_API_KEY|GOOGLE_API_KEY|x-goog-api-key|"
    r"(?:(?:Gemini|Google|제미나이|제미니)\s*)?(?:API\s*)?(?:key|키))"
    r"(?:\s*\d+)?[\"'`]?\s*[:=]\s*", re.I,
)
_WRAPPERS = "\"'`[]{}()<>"


def parse_gemini_keys(raw: str | None) -> list[str]:
    """Return complete opaque keys in input order, with duplicates removed.

    Supports the UI's comma/semicolon/whitespace list, quotes, and common
    environment-variable/header labels. Does not join whitespace-separated
    fragments, truncate values, change key characters, or require a prefix or
    fixed length. No network requests or credential validation happen here.
    """
    if not raw or not raw.strip():
        return []
    text = raw.translate(_COPY_MARKS).strip()
    text = _LABEL.sub("", text)
    keys = []
    for part in re.split(r"[,;\s]+", text):
        key = part.strip(_WRAPPERS)
        if not key:
            continue
        if re.search(r"\*{3,}|[•●…]|\.{3,}", key):
            raise GeminiKeyInputError(
                "일부가 가려지거나 생략된 키가 입력되어 있습니다 [INCOMPLETE_KEY]. "
                "AI Studio의 복사 버튼으로 전체 키를 복사해 왼쪽 입력칸에 붙여넣어주세요."
            )
        # Do not use a legacy [A-Za-z0-9_-] credential whitelist. Opaque key
        # formats can include punctuation; only reject unsafe header text.
        if not re.fullmatch(r"[\x21-\x7e]+", key):
            raise GeminiKeyInputError(
                "API 키 입력에서 전체 키 값을 읽지 못했습니다 [KEY_VALUE_REQUIRED]. "
                "키 이름이나 설명 대신 AI Studio에서 복사한 전체 키를 입력해주세요."
            )
        if key not in keys:
            keys.append(key)
    return keys
