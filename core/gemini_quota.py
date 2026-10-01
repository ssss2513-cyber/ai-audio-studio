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
    evidence: tuple = ()
    basis: str = "unavailable"


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


def _identifier(value):
    return re.sub(r"[^a-z]", "", str(value).lower())


def _evidence_id(value):
    """Keep quota identifiers, never provider prose, project IDs or credentials."""
    value = str(value or "")
    if re.fullmatch(r"(?:Generate|Input|Output|Requests|Tokens|Audio|Concurrent|Spend|Model|Total)[A-Za-z0-9_-]{1,150}", value):
        return value
    return ""


def _explicit_period_exhaustion(text):
    # A help sentence such as "see daily limits" is not a daily violation.
    period = r"(?:daily|monthly|per[\s_-]*day|per[\s_-]*month)"
    exhausted = r"(?:exceeded|exhausted|reached)"
    return bool(re.search(rf"\b{period}\b[^.;:\n]{{0,60}}\b{exhausted}\b|"
                          rf"\b{exhausted}\b[^.;:\n]{{0,60}}\b{period}\b", text, re.I))


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
    structured, descriptions, delays, intervals, evidence = [], [], [], [], []
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
            structured.append(str(detail.get("reason", "")))
            metadata = detail.get("metadata") or {}
            if isinstance(metadata, dict):
                structured.extend(str(metadata.get(key, "")) for key in ("quota_limit", "quota_limit_name", "quota_metric"))
                value = _number(metadata.get("quota_limit_value"))
                zero_quota |= value == 0
                quota_id = _evidence_id(metadata.get("quota_limit") or metadata.get("quota_limit_name"))
                if quota_id:
                    evidence.append((quota_id, value))
                    if value and "requestsperminute" in _identifier(quota_id):
                        intervals.append(60.0 / value + 0.1)
        if kind.endswith("google.rpc.QuotaFailure"):
            violations = detail.get("violations") or []
            for violation in violations if isinstance(violations, list) else []:
                if not isinstance(violation, dict):
                    continue
                quota_id = str(violation.get("quotaId", violation.get("quota_id", "")))
                structured.extend([quota_id, str(violation.get("quotaMetric", violation.get("quota_metric", "")))])
                descriptions.append(str(violation.get("description", "")))
                value = _number(violation.get("quotaValue", violation.get("quota_value")))
                zero_quota |= value == 0
                if _evidence_id(quota_id):
                    evidence.append((quota_id, value))
                identifier = _identifier(quota_id)
                if value and "requestsperminute" in identifier:
                    intervals.append(60.0 / value + 0.1)

    normalized = _identifier(" ".join(structured))
    periods = ("perday", "daily", "permonth", "monthly")
    billing = ("spendlimit", "spendbased", "billingdisabled", "creditsexhausted", "balancedepleted")
    windows = periods + billing + ("perminute", "persecond")
    specific = zero_quota or any(term in normalized for term in windows)
    fallback_text = " ".join(descriptions + [message])
    basis = "structured" if specific else "message" if fallback_text else "unavailable"
    def result(kind, delay=None, interval=None):
        return QuotaRecovery(kind, delay, interval, tuple(dict.fromkeys(evidence))[:4], basis)
    # A short RetryInfo may accompany daily/zero quota errors. It is not proof
    # of a per-minute limit and must never override these non-retryable cases.
    if zero_quota or re.search(r"\blimit\s*:\s*0(?:\.0+)?\s*(?:[,;\n]|$)", message, re.I):
        return result("zero")
    if any(term in normalized for term in periods) or (not specific and _explicit_period_exhaustion(fallback_text)):
        return result("daily")
    if any(term in normalized for term in billing) or (not specific and any(term in _identifier(fallback_text) for term in billing)):
        return result("billing")

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
        return result("unknown")
    if not delays:
        rate_text = normalized if specific else normalized + _identifier(fallback_text)
        if any(term in rate_text for term in ("perminute", "persecond", "ratelimitexceeded", "toomanyrequests")):
            delays.append(60.0)
        else:
            return result("unknown")
    requested = max(delays)
    if not math.isfinite(requested) or requested > MAX_QUOTA_WAIT - 1:
        return result("long_wait")
    return result("temporary", max(1.0, math.ceil(requested) + 1.0),
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
    if recovery.evidence:
        fields = [name + (f" (허용값 {value:g})" if value is not None else "")
                  for name, value in recovery.evidence]
        reason += "\nGoogle 제한 항목: " + "; ".join(fields)
        reason += ". 허용값은 남은 사용량이 아닙니다."
    elif recovery.kind in ("daily", "zero", "billing"):
        reason += "\n제한 종류는 응답 문구로 판정했습니다. Google이 구체적인 제한 이름을 제공하지 않았습니다."
    return reason + " 완료된 음성은 유지됩니다."
