"""Casting validation only; no Gemini requests or audio generation."""
import copy
import json
from types import SimpleNamespace

import pytest

from core import gemini_casting as casting
from core.tts_engine import GEMINI_VOICES, VOICE_STYLES


SCRIPT = "[나레이션] 도윤은 열세 살 소년이었다. 순덕은 일흔 살 할머니였다.\n[도윤] 할머니, 제가 도와드릴게요.\n[순덕] 고맙구나."


def row(name="도윤", **changes):
    result = dict(speaker=name, gender="남성", gender_source="script", gender_quote="도윤은 열세 살 소년이었다.",
                  age="13세", role="소년", personality="씩씩하고 다정함", voice="Fenrir",
                  style="😊 밝고 활기차게", reason="씩씩한 소년에게 또렷하고 활기찬 목소리")
    result.update(changes)
    return result


def validate(rows, **changes):
    params = dict(script=SCRIPT, speakers=[r["speaker"] for r in rows], profiles={},
                  existing={}, voices=GEMINI_VOICES, styles=VOICE_STYLES)
    params.update(changes)
    return casting.validate_casting({"speakers": rows}, **params)


def test_casting_replaces_wrong_gender_while_keeping_script_order():
    result = validate([
        row("순덕", gender="여성", gender_quote="순덕은 일흔 살 할머니였다.", age="70세", role="할머니",
            voice="Charon", style="👵 시니어 따뜻한 (70대)"),
        row(voice="Aoede"),
    ], speakers=["도윤", "순덕"])
    assert list(result) == ["도윤", "순덕"]
    assert GEMINI_VOICES[result["도윤"]["voice"]]["gender"] == "남성"
    assert GEMINI_VOICES[result["순덕"]["voice"]]["gender"] == "여성"
    assert result["순덕"]["style"] == "👵 시니어 따뜻한 (70대)"
    assert result["도윤"]["role"] == "소년"  # Addressing grandma is not their identity.


def test_explicit_profile_overrides_model_and_previous_voice():
    result = validate([row(voice="Aoede", gender="여성")],
                      profiles={"도윤": "남성, 13세 소년, 차분함"}, existing={"도윤": {"voice": "Kore"}})
    assert result["도윤"]["gender"] == "남성"
    assert not result["도윤"]["needs_review"]


@pytest.mark.parametrize("gender,quote", [("불명", ""), ("여성", "없는 인용문")])
def test_unknown_or_fabricated_gender_keeps_current_voice_and_flags_review(gender, quote):
    result = validate([row(gender=gender, gender_quote=quote)], existing={"도윤": {"voice": "Charon"}})
    assert result["도윤"]["voice"] == "Charon"
    assert result["도윤"]["detected_gender"] == "불명"
    assert result["도윤"]["needs_review"]


def test_narrator_does_not_inherit_the_characters_gender():
    result = validate([row("나레이션", gender="불명", gender_source="unknown", gender_quote="",
                           voice="Charon", role="해설", age="불명")], existing={"나레이션": {"voice": "Kore"}})
    assert result["나레이션"]["voice"] == "Kore"
    assert result["나레이션"]["gender"] == "여성"
    assert not result["나레이션"]["needs_review"]


@pytest.mark.parametrize("rows,speakers", [
    ([row()], ["도윤", "순덕"]),
    ([row(), row()], ["도윤", "순덕"]),
    ([row("없는 이름")], ["도윤"]),
    ([row(style="존재하지 않는 스타일")], ["도윤"]),
])
def test_incomplete_cast_is_rejected_as_a_whole(rows, speakers):
    with pytest.raises(casting.CastingError):
        validate(rows, speakers=speakers)


def test_applied_widgets_and_generation_settings_agree():
    result = validate([row(voice="Aoede")])
    state = {"voice_settings": {"도윤": {"engine": "cosyvoice", "voice": "clone"}},
             "gemini_voice_도윤": "Kore", "style_select_도윤": "🎤 기본", "voice_profile_도윤": "사용자 정보"}
    segments = [SimpleNamespace(index=1, speaker="도윤", text="할머니, 제가 도와드릴게요.")]
    casting.apply_casting_to_state(state, result=result, speakers=["도윤"], segments=segments,
                                   script=SCRIPT, profiles={})
    settings = state["voice_settings"]["도윤"]
    assert state["gemini_voice_도윤"] == settings["voice"]
    assert state["gemini_gender_도윤"] == settings["gender"] == "남성"
    assert state["style_select_도윤"] == settings["style"]
    assert state["engine_select_도윤"] == settings["engine"] == "gemini"
    assert state["parsed_segments"] is segments
    assert segments[0].text == "할머니, 제가 도와드릴게요."
    assert state["voice_profile_도윤"] == "사용자 정보"


def test_editing_script_or_profile_invalidates_old_analysis():
    old = casting.casting_fingerprint(SCRIPT, ["도윤"], {})
    assert old != casting.casting_fingerprint(SCRIPT + "새 대사", ["도윤"], {})
    assert old != casting.casting_fingerprint(SCRIPT, ["도윤"], {"도윤": "여성"})


def api_response(rows=None, *, finish="STOP"):
    return {"candidates": [{"content": {"role": "model", "parts": [
        {"text": json.dumps({"speakers": rows if rows is not None else [row()]})}
    ]}, "finishReason": finish}]}


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    import requests
    def forbidden(*args, **kwargs):
        raise AssertionError("Real HTTP requests are forbidden in casting tests")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", forbidden)
    monkeypatch.setattr(casting.time, "sleep", lambda _: None)


def memory_http(monkeypatch, replies):
    """Exercise real requests serialization with an in-memory HTTP adapter."""
    import requests
    original = requests.Session
    calls, closed = [], []
    class Adapter(requests.adapters.HTTPAdapter):
        def send(self, request, **kwargs):
            calls.append((request, kwargs))
            reply = replies[len(calls) - 1]
            if isinstance(reply, Exception):
                raise reply
            status, body, *headers = reply
            response = requests.Response()
            response.status_code = status
            response.request = request
            response.url = request.url
            response.headers.update(headers[0] if headers else {})
            response._content = json.dumps(body, ensure_ascii=False).encode()
            response.encoding = "utf-8"
            return response
        def close(self):
            closed.append(True)
            super().close()
    def session():
        result = original()
        result.mount("https://generativelanguage.googleapis.com/", Adapter())
        return result
    monkeypatch.setattr(requests, "Session", session)
    return calls, closed


def analyze(**kwargs):
    params = dict(script=SCRIPT, speakers=["도윤"], profiles={}, existing={},
                  voices=GEMINI_VOICES, styles=VOICE_STYLES, api_key="test-key,other-key")
    params.update(kwargs)
    return casting.analyze_gemini_casting(**params)


def test_analysis_uses_one_text_request_and_closes_client(monkeypatch):
    calls, closed = memory_http(monkeypatch, [(200, api_response())])
    result = analyze()
    assert len(calls) == 1 and closed
    request, options = calls[0]
    body = json.loads(request.body)
    assert request.method == "POST" and request.url.endswith(":generateContent")
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in request.url and b"test-key" not in request.body
    assert options["timeout"] == (10, 60)
    assert json.loads(body["contents"][0]["parts"][0]["text"])["script"] == SCRIPT
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "low"
    assert result["도윤"]["voice"] == "Fenrir"


def test_analysis_quota_failure_exposes_no_secret_or_partial_update(monkeypatch):
    calls, closed = memory_http(monkeypatch, [(429, {"error": {"message": "secret-key"}})])
    existing = {"도윤": {"voice": "Kore"}}
    before = copy.deepcopy(existing)
    with pytest.raises(casting.CastingError) as error:
        analyze(existing=existing, api_key="secret-key")
    assert "HTTP 429" in str(error.value) and "secret-key" not in str(error.value)
    assert len(calls) == 1 and existing == before and closed


def test_transient_timeout_recovers_once_without_changing_key_or_model(monkeypatch):
    import requests
    calls, _ = memory_http(monkeypatch, [requests.Timeout("sensitive URL"), (200, api_response())])
    updates = []
    assert analyze(progress=updates.append)["도윤"]["voice"] == "Fenrir"
    assert len(calls) == 2
    assert calls[0][0].url == calls[1][0].url
    assert calls[0][0].body == calls[1][0].body
    assert calls[0][0].headers["x-goog-api-key"] == calls[1][0].headers["x-goog-api-key"]
    assert any("자동 재시도" in update for update in updates)
    assert all("sensitive" not in update for update in updates)


def test_busy_server_stops_after_one_retry_with_actual_status(monkeypatch):
    calls, _ = memory_http(monkeypatch, [(503, {"error": {"message": "secret"}})] * 2)
    with pytest.raises(casting.CastingError) as error:
        analyze()
    assert len(calls) == 2
    assert "HTTP 503" in str(error.value) and "secret" not in str(error.value)


def test_unknown_model_is_not_disguised_as_a_connection_error(monkeypatch):
    calls, _ = memory_http(monkeypatch, [(404, {"error": {"message": "secret"}})])
    with pytest.raises(casting.CastingError, match="HTTP 404"):
        analyze()
    assert len(calls) == 1


def test_incompatible_schema_retries_with_json_and_keeps_gender_validation(monkeypatch):
    calls, _ = memory_http(monkeypatch, [
        (400, {"error": {"message": "response_schema is unsupported"}}),
        (200, api_response([row(voice="Kore")])),
    ])
    result = analyze()
    body = json.loads(calls[1][0].body)
    assert "responseSchema" not in body["generationConfig"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert GEMINI_VOICES[result["도윤"]["voice"]]["gender"] == "남성"


def test_truncated_response_gets_one_larger_output_budget(monkeypatch):
    calls, _ = memory_http(monkeypatch, [(200, api_response(finish="MAX_TOKENS")), (200, api_response())])
    assert analyze()["도윤"]["voice"] == "Fenrir"
    assert json.loads(calls[1][0].body)["generationConfig"]["maxOutputTokens"] == 32768


def test_provider_blocks_do_not_retry(monkeypatch):
    calls, _ = memory_http(monkeypatch, [(200, {"promptFeedback": {"blockReason": "SAFETY"}})])
    with pytest.raises(casting.CastingError, match="CONTENT_BLOCKED"):
        analyze()
    assert len(calls) == 1


def test_long_server_retry_after_is_respected(monkeypatch):
    calls, _ = memory_http(monkeypatch, [(503, {}, {"Retry-After": "60"})])
    with pytest.raises(casting.CastingError, match="60초"):
        analyze()
    assert len(calls) == 1


def test_bad_key_format_is_reported_without_transmission(monkeypatch):
    calls, _ = memory_http(monkeypatch, [])
    with pytest.raises(casting.CastingError, match="INVALID_KEY_FORMAT"):
        analyze(api_key="제미나이키")
    assert calls == []
