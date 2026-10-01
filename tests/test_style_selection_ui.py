"""Real Streamlit reruns for stale style values; no API or audio generation."""
import uuid
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.parser import ScriptParser
from core.tts_engine import GEMINI_VOICES, VOICE_STYLES


APP = Path(__file__).resolve().parents[1] / "app.py"
STYLE = "👵 시니어 따뜻한 (70대)"


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    import requests
    def forbidden(*args, **kwargs):
        raise AssertionError("No live API or audio requests in UI regression tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)


def seeded_app(engine="gemini", saved_style=STYLE):
    app = AppTest.from_file(str(APP), default_timeout=15)
    seed = {
        "_personal_colab_session_id": uuid.uuid4().hex,
        "initialized_auto_pipeline": True,
        "script_editor": "노인: 반갑습니다.",
        "speakers": ["노인"],
        "parsed_segments": ScriptParser().parse("노인: 반갑습니다."),
        "active_engine_mode": engine,
        "voice_settings": {"노인": {
            "engine": engine, "voice": "F1" if engine == "supertonic" else "Charon",
            "gender": "남성", "style": STYLE,
        }},
        "style_select_노인": saved_style,
    }
    for key, value in seed.items():
        app.session_state[key] = value
    return app


@pytest.mark.parametrize("engine", ["gemini", "cosyvoice", "supertonic", "gpt-sovits"])
@pytest.mark.parametrize("saved", ["👴 시니어 따뜻한 (70대)", None, "삭제된 스타일"])
def test_stale_selection_renders_and_preserves_existing_style(engine, saved):
    app = seeded_app(engine, saved).run()
    assert not app.exception
    assert app.selectbox(key="style_select_노인").value == STYLE
    config = app.session_state["voice_settings"]["노인"]
    assert config["engine"] == engine and config["style"] == STYLE
    if engine == "gemini":
        assert config["voice"] == "Charon" and config["gender"] == "남성"
    app.run()
    assert not app.exception
    assert app.session_state["voice_settings"]["노인"]["style"] == STYLE


def test_every_style_and_gender_change_keep_canonical_generation_settings():
    app = seeded_app().run()
    for style in VOICE_STYLES:
        app.selectbox(key="style_select_노인").select(style).run()
        assert not app.exception, style
        assert app.session_state["voice_settings"]["노인"]["style"] == style
    app.selectbox(key="style_select_노인").select(STYLE).run()
    for gender in ["여성", "남성"]:
        app.radio(key="gemini_gender_노인").set_value(gender).run()
        assert not app.exception
        config = app.session_state["voice_settings"]["노인"]
        assert config["style"] == STYLE
        assert GEMINI_VOICES[config["voice"]]["gender"] == gender


def test_casting_button_applies_results_then_renders_senior_style(monkeypatch):
    from core import gemini_casting
    def offline_analysis(**kwargs):
        assert kwargs["speakers"] == ["노인"]
        return {"노인": {
            "voice": "Charon", "gender": "남성", "style": STYLE,
            "detected_gender": "남성", "age": "70대", "role": "노인",
            "personality": "다정함", "reason": "온화한 노년 역할", "gender_note": "인물 정보 근거",
            "gender_quote": "남성, 70대", "needs_review": False, "voice_repaired": False,
        }}
    monkeypatch.setattr(gemini_casting, "analyze_gemini_casting", offline_analysis)
    app = seeded_app("gpt-sovits")
    app.session_state["gemini_api_key"] = "offline-test-key"
    app.run().button(key="gemini_cast_all").click().run()
    assert not app.exception
    assert app.session_state["voice_settings"]["노인"]["engine"] == "gemini"
    assert app.selectbox(key="style_select_노인").value == STYLE
    assert any("대본 맞춤 보이스·스타일 설정 완료" in item.value for item in app.success)
    app.button(key="recommend_style_노인").click().run()
    assert not app.exception
    assert app.session_state["voice_settings"]["노인"]["style"] == STYLE
