"""Classify Gemini quota responses without exposing provider text or credentials."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re


MAX_QUOTA_WAIT = 180.0


@dataclass(frozen=True)
class QuotaRecovery:
    kind: str
    delay: float | None = None
    interval: float | None = None


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _duration(value):
    if isinstance(value, dict):
        seconds, nanos = _number(value.get("seconds", 0)), _number(value.get("nanos", 0))
        return seconds + nanos / 1e9 if seconds is not None and nanos is not None else None
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?s", value):
        return _number(value[:-1])
    return None


def quota_recovery(exc):
    # google-genai stores the entire JSON response in APIError.details.
    payload = getattr(exc, "details", {})
    if isinstance(payload, dict):
        error = payload.get("error", payload)
        error = error if isinstance(error, dict) else {}
        details = error.get("details", [])
    else:
        error, details = {}, payload if isinstance(payload, list) else []
    details = details if isinstance(details, list) else []
    message = str(error.get("message") or getattr(exc, "message", "") or "")
    texts, delays, intervals = [message], [], []
    invalid_delay, zero_quota = False, False
    for detail in details:
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get("@type", ""))
        if kind.endswith("google.rpc.RetryInfo"):
            delay = _duration(detail.get("retryDelay", detail.get("retry_delay")))
            if delay is None:
                invalid_delay = True
            else:
                delays.append(delay)
        if kind.endswith("google.rpc.ErrorInfo"):
            texts.append(str(detail.get("reason", "")))
            metadata = detail.get("metadata") or {}
            if isinstance(metadata, dict):
                texts.extend(str(metadata.get(key, "")) for key in ("quota_limit", "quota_limit_name", "quota_metric"))
                if "quota_limit_value" in metadata and _number(metadata["quota_limit_value"]) == 0:
                    zero_quota = True
        if kind.endswith("google.rpc.QuotaFailure"):
            violations = detail.get("violations") or []
            for violation in violations if isinstance(violations, list) else []:
                if not isinstance(violation, dict):
                    continue
                quota_id = str(violation.get("quotaId", violation.get("quota_id", "")))
                texts.extend([quota_id, str(violation.get("quotaMetric", violation.get("quota_metric", ""))),
                              str(violation.get("description", ""))])
                value = _number(violation.get("quotaValue", violation.get("quota_value")))
                if value == 0:
                    zero_quota = True
                identifier = re.sub(r"[^a-z]", "", quota_id.lower())
                if value and "requestsperminute" in identifier:
                    intervals.append(60.0 / value + 0.1)

    combined = " ".join(texts)
    normalized = re.sub(r"[^a-z]", "", combined.lower())
    # A short RetryInfo may accompany daily/zero quota errors. It is not proof
    # of a per-minute limit and must never override these non-retryable cases.
    if zero_quota or re.search(r"\blimit\s*:\s*0(?:\.0+)?\s*(?:[,;\n]|$)", message, re.I):
        return QuotaRecovery("zero")
    if any(term in normalized for term in ("perday", "daily", "permonth", "monthly")):
        return QuotaRecovery("daily")
    if any(term in normalized for term in ("spendlimit", "spendbased", "billingdisabled", "creditsexhausted", "balancedepleted")):
        return QuotaRecovery("billing")

    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    header = next((value for key, value in headers.items() if str(key).lower() == "retry-after"), None)
    if header is not None:
        delay = _number(header)
        if delay is None:
            try:
                date = parsedate_to_datetime(str(header))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                delay = max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                invalid_delay = True
        if delay is not None:
            delays.append(delay)
    if not delays and not invalid_delay:
        matches = re.findall(r"(?:retryDelay[\"'\s:]+|retry in\s+)(\d+(?:\.\d+)?)s", message, re.I)
        delays.extend(float(value) for value in matches)
    if invalid_delay:
        return QuotaRecovery("unknown")
    if not delays:
        if any(term in normalized for term in ("perminute", "persecond", "ratelimitexceeded", "toomanyrequests")):
            delays.append(60.0)
        else:
            return QuotaRecovery("unknown")
    requested = max(delays)
    if not math.isfinite(requested) or requested > MAX_QUOTA_WAIT - 1:
        return QuotaRecovery("long_wait")
    return QuotaRecovery("temporary", max(1.0, math.ceil(requested) + 1.0),
                         max(intervals) if intervals else None)


def quota_error_message(recovery):
    reason = {
        "daily": "Gemini 일일·기간 사용 한도(429)에 도달했습니다. 한도 초기화 또는 상향 후 이어서 생성해주세요.",
        "zero": "Gemini 모델의 허용 한도가 0입니다(429). AI Studio에서 이 프로젝트의 모델 이용 한도와 결제 상태를 확인해주세요.",
        "billing": "Gemini 결제·지출 한도(429)에 도달했습니다. AI Studio에서 프로젝트의 결제·지출 한도를 확인해주세요.",
        "temporary": "Gemini 요청 제한(429)이 대기 후에도 계속되어 멈췄습니다. 잠시 후 이어서 생성해주세요.",
        "long_wait": "Gemini가 긴 한도 대기를 요청했습니다(429). AI Studio에서 한도와 초기화 시점을 확인한 뒤 이어서 생성해주세요.",
        "unknown": "Gemini 사용 한도 오류(429)입니다. 서버가 복구 가능한 대기 시간을 알려주지 않아 멈췄습니다. AI Studio에서 프로젝트 한도를 확인해주세요.",
    }[recovery.kind]
    return reason + " 완료된 음성은 유지됩니다. 같은 프로젝트의 API 키를 추가해도 한도는 늘어나지 않습니다."
