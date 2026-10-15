"""Recent Joseon folk-story spotlight for Maison Lumiel.

Uses only official YouTube Data API v3 and public embeddable YouTube videos.
The API key is read on the server from YOUTUBE_API_KEY and never sent to users.
Refresh candidate data at most every six hours per server process; when an
upstream request fails, reuse the last known valid snapshot when available.
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_ROOT = "https://www.googleapis.com/youtube/v3/"
KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
CACHE_SECONDS = 6 * 3600
FAILURE_SECONDS = 15 * 60
KEYWORDS = ("야담", "옛날이야기", "민담", "설화", "전설", "조선")
SEARCHES = (
    ("야담 옛날이야기", "viewCount"),
    ("조선 야담 민담", "date"),
)
_LOCK = threading.RLock()
_CACHE = {"until": 0.0, "items": [], "error": "", "updated": None}
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DURATION = re.compile(r"^P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def _call(method: str, **params) -> dict:
    params = {"key": KEY, **params}
    url = API_ROOT + method + "?" + urlencode(params)
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "MaisonLumiel/1.0"})
    with urlopen(req, timeout=12) as res:
        result = json.load(res)
    if not isinstance(result, dict):
        raise ValueError("YouTube returned an unexpected response")
    return result


def _batch(items: list, n: int = 50):
    for index in range(0, len(items), n):
        yield items[index:index + n]


def _seconds(iso: str) -> int:
    match = _DURATION.fullmatch(iso or "")
    if not match:
        return 0
    h, m, s = (int(x or 0) for x in match.groups())
    return h * 3600 + m * 60 + s


def _load() -> list[dict]:
    after = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds").replace("+00:00", "Z")
    video_ids: set[str] = set()
    errors = 0
    for term, order in SEARCHES:
        try:
            found = _call(
                "search", part="snippet", type="video", q=term,
                regionCode="KR", relevanceLanguage="ko",
                publishedAfter=after, order=order, maxResults=50,
                videoEmbeddable="true", safeSearch="moderate",
            )
            for item in found.get("items", []):
                vid = item.get("id", {}).get("videoId", "")
                if _ID.fullmatch(vid):
                    video_ids.add(vid)
        except Exception:
            errors += 1
    if not video_ids:
        if errors:
            raise RuntimeError("YouTube video search could not be completed")
        return []

    details: list[dict] = []
    for chunk in _batch(sorted(video_ids)):
        try:
            result = _call("videos", part="snippet,statistics,status,contentDetails", id=",".join(chunk))
            details.extend(result.get("items", []))
        except Exception:
            errors += 1
    channels = {v.get("snippet", {}).get("channelId") for v in details}
    channels.discard(None)
    stats: dict[str, dict] = {}
    for chunk in _batch(sorted(channels)):
        try:
            result = _call("channels", part="statistics", id=",".join(chunk))
            stats.update({x["id"]: x.get("statistics", {}) for x in result.get("items", [])})
        except Exception:
            errors += 1

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    chosen = []
    for v in details:
        snippet = v.get("snippet", {})
        title = snippet.get("title", "")
        description = snippet.get("description", "")
        channel_id = snippet.get("channelId", "")
        cid = v.get("id", "")
        status = v.get("status", {})
        channel = stats.get(channel_id, {})
        if not _ID.fullmatch(cid) or not status.get("embeddable"):
            continue
        if status.get("privacyStatus") != "public":
            continue
        if snippet.get("liveBroadcastContent", "none") not in ("none", ""):
            continue
        if _seconds(v.get("contentDetails", {}).get("duration", "")) < 4 * 60:
            continue
        if not any(word in (title + " " + description) for word in KEYWORDS):
            continue
        try:
            posted = datetime.fromisoformat(snippet.get("publishedAt", "").replace("Z", "+00:00"))
            subscribers = int(channel.get("subscriberCount", 0))
            views = int(v.get("statistics", {}).get("viewCount", 0))
        except (ValueError, TypeError):
            continue
        # Unknown or hidden subscriber counts make the ratio meaningless.
        if posted < cutoff or posted > datetime.now(timezone.utc):
            continue
        if channel.get("hiddenSubscriberCount") or subscribers <= 0 or views < 3000:
            continue
        ratio = views / subscribers
        chosen.append({
            "id": cid,
            "title": title,
            "channel": snippet.get("channelTitle", ""),
            "publishedAt": snippet.get("publishedAt", ""),
            "views": views,
            "subscribers": subscribers,
            "ratio": round(ratio, 3),
            "thumbnail": "https://i.ytimg.com/vi/" + cid + "/hqdefault.jpg",
            "watchUrl": "https://www.youtube.com/watch?v=" + cid,
        })
    chosen.sort(key=lambda item: (item["ratio"], item["views"]), reverse=True)
    return chosen[:12]


def select_feature(exclude: str = "") -> dict:
    """Return one rotating candidate. No unverified or made-up video fallback."""
    if not KEY:
        return {
            "status": "setup_required",
            "video": None,
            "message": "운영자가 YouTube Data API 키를 연결하면 최근 인기 야담이 자동으로 표시됩니다.",
        }
    now = time.time()
    with _LOCK:
        if now >= _CACHE["until"]:
            try:
                updated = _load()
                _CACHE["items"] = updated
                _CACHE["error"] = ""
                _CACHE["until"] = time.time() + CACHE_SECONDS
                _CACHE["updated"] = datetime.now(timezone.utc).isoformat()
            except Exception:
                _CACHE["error"] = "YouTube 정보를 잠시 가져올 수 없습니다."
                _CACHE["until"] = time.time() + FAILURE_SECONDS
        options = _CACHE["items"]
        if not options:
            return {
                "status": "unavailable" if _CACHE["error"] else "empty",
                "video": None,
                "message": _CACHE["error"] or "최근 30일 이내에 조건을 충족하는 야담 영상을 찾지 못했습니다.",
            }
        eligible = [v for v in options if v["id"] != exclude] or options
        # Random choice from the twelve best ratios, excluding this browser's last play.
        pick = random.SystemRandom().choice(eligible)
        return {
            "status": "ok", "video": pick, "candidateCount": len(options),
            "windowDays": 30, "ranking": "조회수 ÷ 채널 구독자 수",
            "updatedAt": _CACHE["updated"],
            "message": _CACHE["error"],
        }
