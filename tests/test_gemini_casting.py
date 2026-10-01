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


def test_analysis_uses_one_text_request_and_closes_client(monkeypatch):
    from google import genai
    calls, options, closed = [], [], []
    def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps({"speakers": [row()]}))
    def client(**kwargs):
        options.append(kwargs)
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content), close=lambda: closed.append(True))
    monkeypatch.setattr(genai, "Client", client)
    result = casting.analyze_gemini_casting(script=SCRIPT, speakers=["도윤"], profiles={}, existing={},
                                          voices=GEMINI_VOICES, styles=VOICE_STYLES, api_key="test-key,other-key")
    assert len(calls) == 1 and closed == [True]
    assert options[0]["http_options"].retry_options.attempts == 1
    assert options[0]["api_key"] == "test-key"
    assert json.loads(calls[0]["contents"])["script"] == SCRIPT
    assert "test-key" not in calls[0]["contents"]
    assert calls[0]["config"].response_mime_type == "application/json"
    assert result["도윤"]["voice"] == "Fenrir"


def test_analysis_quota_failure_exposes_no_secret_or_partial_update(monkeypatch):
    from google import genai
    calls = []
    class QuotaError(Exception):
        code = 429
    def generate_content(**kwargs):
        calls.append(kwargs)
        raise QuotaError("secret-key")
    monkeypatch.setattr(genai, "Client", lambda **kwargs: SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content), close=lambda: None))
    existing = {"도윤": {"voice": "Kore"}}
    before = copy.deepcopy(existing)
    with pytest.raises(casting.CastingError) as error:
        casting.analyze_gemini_casting(script=SCRIPT, speakers=["도윤"], profiles={}, existing=existing,
                                      voices=GEMINI_VOICES, styles=VOICE_STYLES, api_key="secret-key")
    assert "429" in str(error.value) and "secret-key" not in str(error.value)
    assert len(calls) == 1 and existing == before
