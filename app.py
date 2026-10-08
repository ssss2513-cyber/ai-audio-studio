from __future__ import annotations
import io
import math
import os
import re
import time
import shutil
import subprocess
from types import SimpleNamespace
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple, Set
import streamlit as st
import streamlit.components.v1 as components

from core.tts_engine import (
    TTSEngine,
    VoiceConfig,
    clean_spoken_text,
    SUPERTONIC_VOICES,
    GEMINI_VOICES,
    KOREAN_EDGE_VOICES,
    VOICE_STYLES
)
from core.personal_colab import session_workspace, upload_name
from core.result_downloads import saved_file_download
from core.colab_session_ui import render_connections, render_reset, render_qwen_bank_connection
from core.voice_recommendations import (
    recommend_style, preview_text, style_note, speaker_gender,
    resolve_style, style_display_label, style_description,
)
from core.gemini_keys import GeminiKeyInputError, parse_gemini_keys
from core.gemini_recovery_ui import render_gemini_recovery
from core.chirp_client import CHIRP_VOICES, validate_key as validate_chirp_key
from core.chirp_ui import render_chirp_settings, render_chirp_voice
from cosy3_voicebank_catalog import VOICEBANK as COSY3_VOICES, BANK_REVISION as COSY3_BANK_REVISION
from core.cosy3_connection_ui import render_connection as render_cosy3_connection
from core.cosy3_ui import (render_voice as render_cosy3_voice,
                          preset as cosy3_preset, sync_widgets as sync_cosy3_widgets)
from core.cosy3_client import check_connection as cosy3_status, export_plan as export_cosy3_plan
from core.cosy_kaggle import (render_downloads as render_kaggle_downloads, render_export as render_kaggle_export,
    render_status as render_kaggle_status, use_kaggle as use_kaggle_gpu, start_preview as start_kaggle_preview,
    render_setup as render_kaggle_setup)
from core.kaggle_jobs import is_running as is_kaggle_running, clear_job as clear_kaggle_job
from qwen_voicebank_catalog import VOICEBANK as QWEN_BANK_VOICES
from core.qwen_voicebank_client import export_plan as export_qwen_bank_plan
from core.qwen_bank_connection import connection_status as qwen_bank_status
from core.qwen_bank_controls import render_voice as render_qwen_bank_voice, preset as bank_preset
from core.gemini_casting import (
    CastingError, analyze_gemini_casting, apply_casting_to_state, casting_fingerprint,
)
from core.generation_jobs import (
    GenerationItem, start_job, get_job, is_running, request_pause, clear_job, start_partial_merge, valid_audio, cached_audio_path,
)
from core.parser import ScriptParser, ScriptSegment
from core.story_precise_parser import parse_story_precisely, parse_story_with_gemini, is_already_formatted_script
from core.emotion_directing import segment_cue, tagged_line, require_support
from core.emotion_ui import render_emotions


APP_VERSION = "v2.9.54 · GPU당 3개 생성·다음 3개 준비"

st.set_page_config(
    page_title=f"화자별 자동 TTS 생성기 (CosyVoice 2·3 · Qwen · Gemini) - {APP_VERSION}",
    page_icon="🎙️",
    layout="wide"
)

# 창고지기 성복 13인 예제 대본 파일 확인 및 로드
SEONGBOK_PATH = "seongbok_story_script.txt"

if os.path.exists(SEONGBOK_PATH):
    with open(SEONGBOK_PATH, "r", encoding="utf-8") as f:
        SEONGBOK_SCRIPT = f.read()
else:
    SEONGBOK_SCRIPT = ""

DEFAULT_SAMPLE_SCRIPT = """[나레이션] 어느 평화로운 장터 마을에 새로운 아침이 밝아왔습니다.
[만복] "이 됫박, 어찌 같은데 무게가 다르단 말이오?"
[나레이션] 만복은 저울 앞에 서서 흙 묻은 됫박 두 개를 나란히 올려 두고 있었습니다.
[옥련] "서방님, 어찌 됫박을 그리 오래 들여다보십니까?"
[원복] "매제는 참 태평도 하시오! 일할 생각은 안 하고 처가살이만 하고 있으니 원."
[나레이션] 장터 사람들은 수군거리며 그 모습을 지켜보았습니다."""

# 13인 등장인물별 기본 추천 스타일 매핑 (34종 음성 스타일 프리셋 연계)
DEFAULT_CHARACTER_STYLES = {
    "나레이션": "📖 동화 나레이션",
    "성복": "🎭 진지하게",
    "계순": "💌 부드럽고 감성적",
    "사또": "😠 분노/격양",
    "경헌": "🏛️ 40대 안정적인",
    "잉손": "🧙 시니어 지혜로운 (70~80대)",
    "남 포졸": "💪 힘차게",
    "노모": "👵 시니어 따뜻한 (70대)",
    "장인": "🎩 50대 깊이 있는",
    "훈장": "🎓 강의/교육",
    "박씨 노인": "👴 시니어 중후한 (60대)",
    "젊은 아낙": "😊 밝고 활기차게",
    "청년": "💪 힘차게",
}

# 1. Supertonic 3 (로컬 무료) 13인 캐릭터 맞춤 성우 프리셋
SUPERTONIC_CHARACTER_PRESETS = {
    "나레이션": {"voice": "F1", "style": "📖 동화 나레이션", "role_desc": "차분하고 지적인 여성 해설"},
    "성복": {"voice": "M1", "style": "🎭 진지하게", "role_desc": "진솔하고 성실한 청년 주인공"},
    "계순": {"voice": "F2", "style": "💌 부드럽고 감성적", "role_desc": "밝고 생기있는 아내"},
    "경헌": {"voice": "M2", "style": "🏛️ 40대 안정적인", "role_desc": "묵직하고 냉철한 관리"},
    "사또": {"voice": "M2", "style": "😠 분노/격양", "role_desc": "중후하고 위엄 넘치는 관아 사또"},
    "잉손": {"voice": "M3", "style": "🧙 시니어 지혜로운 (70~80대)", "role_desc": "연륜 있고 구수한 이야기꾼 노인"},
    "남 포졸": {"voice": "M4", "style": "💪 힘차게", "role_desc": "단정하고 힘 있는 관아 포졸"},
    "노모": {"voice": "F1", "style": "👵 시니어 따뜻한 (70대)", "role_desc": "자애롭고 따뜻한 노모"},
    "장인": {"voice": "M2", "style": "🎩 50대 깊이 있는", "role_desc": "속 깊은 장인 어른"},
    "훈장": {"voice": "M3", "style": "🎓 강의/교육", "role_desc": "점잖고 깊이 있는 학식 훈장"},
    "박씨 노인": {"voice": "M3", "style": "👴 시니어 중후한 (60대)", "role_desc": "동네 토박이 구수한 노인"},
    "젊은 아낙": {"voice": "F2", "style": "😊 밝고 활기차게", "role_desc": "생기 넘치는 마을 아낙"},
    "청년": {"voice": "M1", "style": "💪 힘차게", "role_desc": "기운 넘치는 동네 청년"},
}

# 2. Gemini 3.1 / 2.0 Flash TTS 30대 보이스 기반 캐릭터 프리셋
GEMINI_CHARACTER_PRESETS = {
    # 우물가 여인 / 창고지기 성복 및 단편 소설 주요 배역 매핑
    "나레이션": {"voice": "Kore", "style": "📖 동화 나레이션", "role_desc": "차분하고 따뜻한 여성 해설"},
    "달래": {"voice": "Aoede", "style": "💌 부드럽고 감성적", "role_desc": "맑고 생기 있는 여주인공"},
    "덕쇠": {"voice": "Fenrir", "style": "💪 힘차게", "role_desc": "씩씩하고 충직한 소년/청년"},
    "송 노인": {"voice": "Sadaltager", "style": "👴 시니어 중후한 (60대)", "role_desc": "연륜과 깊이가 있는 남성 염색장 어르신"},
    "말숙": {"voice": "Zephyr", "style": "😊 밝고 활기차게", "role_desc": "화사하고 개성 있는 여인"},
    "봉 행수": {"voice": "Zubenelgenubi", "style": "🏛️ 40대 안정적인", "role_desc": "산전수전 겪은 노련한 행수"},
    "이방 오익환": {"voice": "Sadaltager", "style": "🎩 50대 깊이 있는", "role_desc": "무게감 있고 영악한 관아 이방"},
    "현감": {"voice": "Charon", "style": "😠 분노/격양", "role_desc": "위엄과 권위를 드러내는 고을 수령"},
    "봉진우": {"voice": "Algieba", "style": "💡 30대 세련된", "role_desc": "세련되고 신중한 양반 인물"},
    "마을 사람": {"voice": "Achird", "style": "🍷 40대 성숙한", "role_desc": "친근하고 서글서글한 동네 주민"},
    "성복": {"voice": "Fenrir", "style": "🎭 진지하게", "role_desc": "힘 있고 당찬 청년 주인공"},
    "계순": {"voice": "Aoede", "style": "💌 부드럽고 감성적", "role_desc": "맑고 생기있는 아내"},
    "경헌": {"voice": "Charon", "style": "🏛️ 40대 안정적인", "role_desc": "중후하고 깊은 저음 관리"},
    "사또": {"voice": "Orus", "style": "😠 분노/격양", "role_desc": "위엄 넘치는 관아 사또"},
    "잉손": {"voice": "Puck", "style": "🧙 시니어 지혜로운 (70~80대)", "role_desc": "위트 있고 구수한 이야기꾼 노인"},
    "남 포졸": {"voice": "Alnilam", "style": "💪 힘차게", "role_desc": "단정하고 곧은 기개의 포졸"},
    "노모": {"voice": "Sulafat", "style": "👵 시니어 따뜻한 (70대)", "role_desc": "자애롭고 포근한 노모"},
    "장인": {"voice": "Charon", "style": "🎩 50대 깊이 있는", "role_desc": "무뚝뚝한 장인 어른"},
    "훈장": {"voice": "Sadaltager", "style": "🎓 강의/교육", "role_desc": "점잖은 마을 훈장"},
    "박씨 노인": {"voice": "Charon", "style": "👴 시니어 중후한 (60대)", "role_desc": "동네 토박이 남성 노인"},
    "젊은 아낙": {"voice": "Callirrhoe", "style": "😊 밝고 활기차게", "role_desc": "생기 넘치는 마을 아낙"},
    "청년": {"voice": "Enceladus", "style": "💪 힘차게", "role_desc": "패기 넘치는 동네 청년"},
}

# 3. Edge-TTS 무료 한국어 순수 신경망 프리셋
EDGE_CHARACTER_PRESETS = {
    "나레이션": {"voice": "ko-KR-SunHiNeural", "style": "📖 동화 나레이션", "rate": 0, "pitch": 0, "role_desc": "차분하고 또렷한 표준 여성 해설"},
    "성복": {"voice": "ko-KR-HyunsuMultilingualNeural", "style": "🎭 진지하게", "rate": 0, "pitch": 0, "role_desc": "젊고 활기찬 청년 주인공"},
    "계순": {"voice": "ko-KR-SunHiNeural", "style": "💌 부드럽고 감성적", "rate": 5, "pitch": 10, "role_desc": "밝고 또렷한 표준 여성"},
    "경헌": {"voice": "ko-KR-InJoonNeural", "style": "🏛️ 40대 안정적인", "rate": 0, "pitch": -5, "role_desc": "냉철한 표준 남성"},
    "사또": {"voice": "ko-KR-InJoonNeural", "style": "😠 분노/격양", "rate": -10, "pitch": -15, "role_desc": "묵직하고 엄숙한 남성"},
    "잉손": {"voice": "ko-KR-InJoonNeural", "style": "🧙 시니어 지혜로운 (70~80대)", "rate": -15, "pitch": -5, "role_desc": "느긋하고 차분한 노인 남성"},
    "남 포졸": {"voice": "ko-KR-InJoonNeural", "style": "💪 힘차게", "rate": 5, "pitch": 5, "role_desc": "날렵한 젊은 남성"},
    "노모": {"voice": "ko-KR-SunHiNeural", "style": "👵 시니어 따뜻한 (70대)", "rate": -10, "pitch": -5, "role_desc": "차분하고 깊이 있는 여성"},
    "장인": {"voice": "ko-KR-InJoonNeural", "style": "🎩 50대 깊이 있는", "rate": -5, "pitch": -10, "role_desc": "무뚝뚝한 남성"},
    "훈장": {"voice": "ko-KR-InJoonNeural", "style": "🎓 강의/교육", "rate": -5, "pitch": 0, "role_desc": "단정한 표준 남성"},
    "박씨 노인": {"voice": "ko-KR-InJoonNeural", "style": "👴 시니어 중후한 (60대)", "rate": -10, "pitch": 0, "role_desc": "나이 지긋한 남성"},
    "젊은 아낙": {"voice": "ko-KR-SunHiNeural", "style": "😊 밝고 활기차게", "rate": 5, "pitch": 15, "role_desc": "발랄하고 높은 톤의 여성"},
    "청년": {"voice": "ko-KR-HyunsuMultilingualNeural", "style": "💪 힘차게", "rate": 5, "pitch": 10, "role_desc": "활기찬 청년"},
}

# 인물별 맞춤 대사 샘플 (미리듣기용)
CHARACTER_SAMPLE_LINES = {
    "나레이션": "어느 눈 내린 겨울날, 정선의 산골 창고 마을에서 벌어진 이야기입니다.",
    "달래": "이따 물에 담그면 네 눈으로 직접 보게 될 것이다.",
    "덕쇠": "누나, 독 냄새가 또 이상해요. 어제보다 더 시큼해요.",
    "송 노인": "물은 거짓말을 하지 못한다. 젖은 천은 지나간 일을 기억하는 법이니라.",
    "봉 행수": "이 샘물만 손에 쥐면 한양 저잣거리 비단 시장은 전부 우리 차지다!",
    "말숙": "사흘 안에 혼인을 파하십시오. 그러지 않으면 아버님의 샘을 빼앗깁니다.",
    "이방 오익환": "고을 법도에 따라 샘물의 소유권 문서를 엄정히 확인해야겠소.",
    "현감": "양측의 주장을 들었으니, 물의 증좌를 눈앞에 밝히도록 하라.",
    "봉진우": "달래 낭자, 그 샘물의 비밀을 풀지 못하면 혼례는 파기될 것입니다.",
    "마을 사람": "여울골 옥정 물이 없으면 우리 마을 염색일은 다 끝장난 거나 다름없지.",
    "성복": "소인은 결백하옵니다. 짚신 코가 닳아 문설주에 닿았을 뿐이옵니다.",
    "계순": "여보, 낙담하지 마세요. 반드시 진실을 밝혀낼 방도가 있을 거예요.",
    "사또": "발자국은 하나인데, 어찌 못 자리는 둘이란 말이냐! 진실을 고하라.",
    "경헌": "사또 나으리, 간밤에 창고를 지킨 자는 성복이 외에는 아무도 없었사옵니다.",
    "잉손": "그 짚신 바닥의 쇠못... 내 손으로 박아준 기억이 분명히 나네 그려.",
    "남 포졸": "성복아, 억울하더라도 관아의 법도를 따라야 하지 않겠느냐.",
    "노모": "아범아, 아무 걱정 말거라. 하늘이 무너져도 솟아날 구멍은 있단다.",
    "장인": "자네가 죄를 짓지 않았다는 걸 내가 누구보다 잘 알고 있네.",
    "훈장": "진실은 눈 속에 파묻혀도 언젠가는 햇볕 아래 드러나는 법이지요.",
    "박씨 노인": "그날 밤 창고 쪽으로 수상한 그림자가 지나가는 걸 언뜻 보았소만...",
    "젊은 아낙": "어머나, 성복 서방님이 그럴 분이 아니신데 어쩌면 좋아요!",
    "청년": "제가 지난밤 순라를 돌 때 창고 뒷골목에서 수상한 자를 보았습니다!"
}

SUPERTONIC_VOICE_KEYS = list(SUPERTONIC_VOICES.keys())
GEMINI_VOICE_KEYS = list(GEMINI_VOICES.keys())
EDGE_VOICE_KEYS = list(KOREAN_EDGE_VOICES.keys())
VOICE_STYLE_KEYS = list(VOICE_STYLES.keys())

def render_style_selector(label, speaker, configured_style):
    """Repair stale/empty widget values before rendering any engine's selector."""
    key = f"style_select_{speaker}"
    fallback = resolve_style(configured_style, VOICE_STYLES)
    saved = st.session_state.get(key, fallback)
    canonical = resolve_style(saved, VOICE_STYLES, fallback=fallback)
    if key in st.session_state and saved != canonical:
        st.session_state[key] = canonical
    selected = st.selectbox(
        label, options=VOICE_STYLE_KEYS, index=VOICE_STYLE_KEYS.index(canonical),
        format_func=style_display_label, key=key,
    )
    # A cleared browser selection can arrive during this rerun. Keep the last
    # configured style now; the widget is repaired before its next render.
    return resolve_style(selected, VOICE_STYLES, fallback=canonical)

def get_supertonic_voice_label(v_key: str) -> str:
    info = SUPERTONIC_VOICES.get(v_key, {})
    return f"{info.get('name', v_key)}"

def get_gemini_voice_label(v_key: str) -> str:
    info = GEMINI_VOICES.get(v_key, {})
    gender = info.get("gender", "")
    badge = "👩" if gender == "여성" else "👨"
    return f"{badge} {info.get('name', v_key)} | {info.get('description', '')}"

def get_edge_voice_label(v_key: str) -> str:
    info = KOREAN_EDGE_VOICES.get(v_key, {})
    return f"{info.get('name', v_key)} | {info.get('description', '')}"


def gemini_preset(speaker, current=None):
    """Keep a chosen voice, and preserve gender when switching engines."""
    current = current or {}
    preset = GEMINI_CHARACTER_PRESETS.get(speaker, {})
    catalog = {"gemini": GEMINI_VOICES, "supertonic": SUPERTONIC_VOICES,
               "edge-tts": KOREAN_EDGE_VOICES, "qwen-bank": QWEN_BANK_VOICES, "cosyvoice3": COSY3_VOICES,
               "chirp": {name: {"gender": gender} for name, gender in CHIRP_VOICES.items()}}.get(current.get("engine"), {})
    gender = (current.get("gender") or catalog.get(current.get("voice"), {}).get("gender")
              or speaker_gender(speaker, st.session_state.get(f"voice_profile_{speaker}", "")))
    voice = current.get("voice") if current.get("engine") == "gemini" else preset.get("voice")
    if voice not in GEMINI_VOICES or (gender and GEMINI_VOICES[voice]["gender"] != gender):
        voice = "Charon" if gender == "남성" else "Kore"
    return {"engine": "gemini", "voice": voice, "gender": GEMINI_VOICES[voice]["gender"],
            "style": current.get("style", preset.get("style", "🎤 기본")), "speed": 1.0}


def chirp_preset(speaker, current=None):
    current = current or {}
    catalog = {"gemini": GEMINI_VOICES, "supertonic": SUPERTONIC_VOICES,
               "edge-tts": KOREAN_EDGE_VOICES, "qwen-bank": QWEN_BANK_VOICES, "cosyvoice3": COSY3_VOICES}.get(current.get("engine"), {})
    gender = (current.get("gender") or catalog.get(current.get("voice"), {}).get("gender")
              or speaker_gender(speaker, st.session_state.get(f"voice_profile_{speaker}", "")))
    voice = (current.get("voice") if current.get("engine") in ("gemini", "chirp")
             else GEMINI_CHARACTER_PRESETS.get(speaker, {}).get("voice"))
    if voice not in CHIRP_VOICES or (gender and CHIRP_VOICES[voice] != gender):
        voice = "Charon" if gender == "남성" else "Kore"
    return {"engine": "chirp", "voice": voice, "gender": CHIRP_VOICES[voice],
            "style": "🎤 기본", "speed": current.get("speed", 1.0) if current.get("engine") == "chirp" else 1.0}


def qwen_bank_preset(speaker, current=None, used=()):
    current = current or {}
    catalog = {"gemini": GEMINI_VOICES, "supertonic": SUPERTONIC_VOICES,
               "edge-tts": KOREAN_EDGE_VOICES, "qwen-bank": QWEN_BANK_VOICES, "cosyvoice3": COSY3_VOICES,
               "chirp": {name: {"gender": gender} for name, gender in CHIRP_VOICES.items()}}.get(current.get("engine"), {})
    profile = st.session_state.get(f"voice_profile_{speaker}", "")
    gender = (current.get("gender") or catalog.get(current.get("voice"), {}).get("gender")
              or speaker_gender(speaker, profile))
    if not gender:
        known = GEMINI_CHARACTER_PRESETS.get(speaker, {}).get("voice")
        gender = GEMINI_VOICES.get(known, {}).get("gender", "")
    return bank_preset(speaker, current, gender, used)


def cosy3_character_preset(speaker, current=None, used=()):
    known = GEMINI_CHARACTER_PRESETS.get(speaker, {}).get("voice")
    return cosy3_preset(speaker, current, used,
        known_gender=GEMINI_VOICES.get(known, {}).get("gender", ""),
        profile=st.session_state.get(f"voice_profile_{speaker}", ""))


def retire_qwen_customvoice(busy):
    """Remove the old engine without interrupting its already-running job."""
    if st.session_state.get("active_engine_mode") == "qwen":
        st.session_state["active_engine_mode"] = "qwen-bank"
    if busy:
        return
    # These names are migration metadata only, never offered as voice choices.
    previous_gender = {name: "남성" for name in ("Uncle_Fu", "Ryan", "Aiden", "Dylan", "Eric")}
    previous_gender.update({name: "여성" for name in ("Sohee", "Serena", "Vivian", "Ono_Anna")})
    settings = st.session_state.get("voice_settings", {})
    used = {row.get("voice") for row in settings.values() if row.get("engine") == "qwen-bank"}
    changed = []
    for speaker, current in list(settings.items()):
        if current.get("engine") != "qwen":
            continue
        st.session_state.setdefault("_retired_qwen_cast", {})[speaker] = dict(current)
        gender = current.get("gender")
        if gender not in ("남성", "여성"):
            gender = previous_gender.get(current.get("voice"))
        if not gender:
            gender = speaker_gender(speaker, st.session_state.get(f"voice_profile_{speaker}", ""))
        converted = dict(current)
        converted.update(bank_preset(speaker, current, gender, used))
        converted.pop("qwen_instruction", None)
        settings[speaker] = converted
        used.add(converted["voice"])
        st.session_state[f"engine_select_{speaker}"] = "qwen-bank"
        st.session_state[f"qwen_bank_voice_{speaker}"] = converted["voice"]
        st.session_state[f"qwen_bank_gender_{speaker}"] = converted["gender"]
        changed.append(speaker)
    for key in ("qwen_url", "input_qwen_url", "checked_qwen_url"):
        st.session_state.pop(key, None)
    if changed:
        st.session_state["_qwen_bank_migration_notice"] = changed


def change_gemini_gender(speaker):
    gender = st.session_state[f"gemini_gender_{speaker}"]
    config = st.session_state["voice_settings"][speaker]
    voice = st.session_state.get(f"gemini_voice_{speaker}", config.get("voice"))
    if GEMINI_VOICES.get(voice, {}).get("gender") != gender:
        candidate = GEMINI_CHARACTER_PRESETS.get(speaker, {}).get("voice")
        voice = (candidate if GEMINI_VOICES.get(candidate, {}).get("gender") == gender
                 else "Charon" if gender == "남성" else "Kore")
    config.update(voice=voice, gender=gender)
    st.session_state[f"gemini_voice_{speaker}"] = voice
    report = current_casting_report().get(speaker)
    if report:
        report.update(voice=voice, gender=gender, detected_gender=gender,
                      needs_review=False, gender_note="사용자가 성별 직접 선택", gender_quote="")


def casting_source_text():
    current = st.session_state.get("script_editor", "")
    if current == st.session_state.get("casting_formatted_text"):
        return st.session_state.get("casting_source_text", current)
    return current


def current_casting_report():
    speakers = st.session_state.get("speakers", [])
    profiles = {s: st.session_state.get(f"voice_profile_{s}", "") for s in speakers}
    report = st.session_state.get("gemini_casting_report", {})
    if report.get("fingerprint") == casting_fingerprint(casting_source_text(), speakers, profiles):
        return report.get("speakers", {})
    return {}


def correct_legacy_gemini_voices():
    """Repair selections made under the three incorrect gender labels once."""
    if st.session_state.get("_gemini_gender_catalog_v2913"):
        return
    corrections = {"Gacrux": ("남성", "Sadaltager"),
                   "Achernar": ("남성", "Umbriel"),
                   "Sadachbia": ("여성", "Zephyr")}
    changed = []
    for name, config in st.session_state.get("voice_settings", {}).items():
        old = config.get("voice")
        if config.get("engine") != "gemini" or old not in corrections:
            continue
        old_gender, replacement = corrections[old]
        intended = (config.get("gender")
                    or speaker_gender(name, st.session_state.get(f"voice_profile_{name}", ""))
                    or old_gender)
        if GEMINI_VOICES[old]["gender"] != intended:
            config.update(voice=replacement, gender=intended)
            st.session_state[f"gemini_voice_{name}"] = replacement
            st.session_state[f"gemini_gender_{name}"] = intended
            changed.append(f"{name}: {old} → {replacement}")
    st.session_state["_gemini_gender_corrections"] = changed
    st.session_state["_gemini_gender_catalog_v2913"] = True


def apply_preset_to_speakers(speakers, engine_type):
    """현재 화자 목록에 특정 엔진의 프리셋을 일괄 적용 (스타일 포함)"""
    new_settings = {}
    for idx, spk in enumerate(speakers):
        def_style = DEFAULT_CHARACTER_STYLES.get(spk, "🎤 기본")
        if engine_type == "supertonic":
            if spk in SUPERTONIC_CHARACTER_PRESETS:
                p = SUPERTONIC_CHARACTER_PRESETS[spk]
                new_settings[spk] = {"engine": "supertonic", "voice": p["voice"], "style": p.get("style", def_style), "speed": 1.0}
            elif "나레이션" in spk or "해설" in spk:
                new_settings[spk] = {"engine": "supertonic", "voice": "F1", "style": "📖 동화 나레이션", "speed": 1.0}
            else:
                v_idx = idx % len(SUPERTONIC_VOICE_KEYS)
                new_settings[spk] = {"engine": "supertonic", "voice": SUPERTONIC_VOICE_KEYS[v_idx], "style": def_style, "speed": 1.0}
        elif engine_type == "gemini":
            new_settings[spk] = gemini_preset(spk, st.session_state.get("voice_settings", {}).get(spk))
        elif engine_type == "chirp":
            new_settings[spk] = chirp_preset(spk, st.session_state.get("voice_settings", {}).get(spk))
        elif engine_type == "qwen-bank":
            used = {row["voice"] for row in new_settings.values() if row.get("engine") == "qwen-bank"}
            new_settings[spk] = qwen_bank_preset(spk, st.session_state.get("voice_settings", {}).get(spk), used)
        elif engine_type == "cosyvoice3":
            used = {row["voice"] for row in new_settings.values() if row.get("engine") == "cosyvoice3"}
            new_settings[spk] = cosy3_character_preset(spk, st.session_state.get("voice_settings", {}).get(spk), used)
        elif engine_type == "cosyvoice":
            current = st.session_state.get("voice_settings", {}).get(spk, {})
            ref = current.get("ref_audio_path", "")
            prompt = current.get("prompt_text", "")
            if not ref:
                ref = st.session_state.get(f"cosy_saved_path_{spk}", "")
                prompt = st.session_state.get(f"cosy_prompt_{spk}", "")
            new_settings[spk] = {
                "engine": "cosyvoice",
                "voice": "CosyVoice 2 목소리 복제",
                "style": current.get("style", def_style),
                "ref_audio_path": ref,
                "prompt_text": prompt,
                "speed": current.get("speed", 1.0) if current.get("engine") == "cosyvoice" else 1.0
            }
            if current.get("gender"):
                new_settings[spk]["gender"] = current["gender"]
        elif engine_type == "gpt-sovits":
            new_settings[spk] = {
                "engine": "gpt-sovits",
                "voice": "GPT-SoVITS 목소리 복제",
                "style": def_style,
                "ref_audio_path": "",
                "prompt_text": "",
                "speed": 1.0,
                "temperature": 1.0,
                "top_k": 15,
                "top_p": 1.0
            }
        else:
            if spk in SUPERTONIC_CHARACTER_PRESETS:
                p = SUPERTONIC_CHARACTER_PRESETS[spk]
                new_settings[spk] = {"engine": "supertonic", "voice": p["voice"], "style": p.get("style", def_style), "speed": 1.0}
            elif "나레이션" in spk or "해설" in spk:
                new_settings[spk] = {"engine": "supertonic", "voice": "F1", "style": "📖 동화 나레이션", "speed": 1.0}
            else:
                v_idx = idx % len(SUPERTONIC_VOICE_KEYS)
                new_settings[spk] = {"engine": "supertonic", "voice": SUPERTONIC_VOICE_KEYS[v_idx], "style": def_style, "speed": 1.0}
    return new_settings

def set_state_safe(key: str, value: Any) -> None:
    """StreamlitWidgetAlreadyInstantiatedError 등 위젯 키 수정 예외 방어용 안전 세션 상태 저장기"""
    try:
        st.session_state[key] = value
    except Exception:
        pass

def apply_recommended_styles(speaker=None):
    speakers = [speaker] if speaker else st.session_state.get("speakers", [])
    for name in speakers:
        config = st.session_state.get("voice_settings", {}).get(name)
        if config is None or config.get("engine") in ("chirp", "qwen-bank"):
            continue
        recommendation = recommend_style(name, st.session_state.get("parsed_segments", []),
                                         st.session_state.get(f"voice_profile_{name}", ""))
        style = resolve_style(current_casting_report().get(name, {}).get("style", recommendation.style),
                              VOICE_STYLES, fallback=config.get("style", "🎤 기본"))
        config["style"] = style
        st.session_state[f"style_select_{name}"] = style


def set_speakers_preset(engine_type: str):
    """모든 화자의 엔진, 보이스, 스타일 설정을 일괄 변경하고 Streamlit 위젯 상태까지 강제 동기화"""
    speakers = st.session_state.get("speakers", [])
    if not speakers:
        return
    eng_mode = "edge" if engine_type == "edge-tts" else engine_type
    st.session_state["active_engine_mode"] = eng_mode
    new_settings = apply_preset_to_speakers(speakers, engine_type)
    st.session_state["voice_settings"] = new_settings

    for spk, sdata in new_settings.items():
        eng = sdata.get("engine", engine_type)
        set_state_safe(f"engine_select_{spk}", eng)
        voice = sdata.get("voice", "")
        style = sdata.get("style", "🎤 기본")
        if eng == "supertonic":
            set_state_safe(f"supertonic_voice_{spk}", voice)
        elif eng == "cosyvoice3":
            sync_cosy3_widgets(spk, sdata)
        elif eng == "cosyvoice":
            set_state_safe(f"cosy_speed_{spk}", sdata.get("speed", 1.0))
            set_state_safe(f"cosy_prompt_{spk}", sdata.get("prompt_text", ""))
            set_state_safe(f"cosy_saved_path_{spk}", sdata.get("ref_audio_path", ""))
        elif eng == "gemini":
            set_state_safe(f"gemini_voice_{spk}", voice)
            set_state_safe(f"gemini_gender_{spk}", GEMINI_VOICES[voice]["gender"])
        elif eng == "chirp":
            set_state_safe(f"chirp_voice_{spk}", voice)
            set_state_safe(f"chirp_gender_{spk}", CHIRP_VOICES[voice])
            set_state_safe(f"chirp_speed_{spk}", sdata.get("speed", 1.0))
        elif eng == "qwen-bank":
            set_state_safe(f"qwen_bank_voice_{spk}", voice)
            set_state_safe(f"qwen_bank_gender_{spk}", QWEN_BANK_VOICES[voice]["gender"])
        elif eng == "edge-tts":
            set_state_safe(f"edge_voice_{spk}", voice)
        elif eng == "gpt-sovits":
            set_state_safe(f"sovits_speed_{spk}", sdata.get("speed", 1.0))
            set_state_safe(f"sovits_temp_{spk}", sdata.get("temperature", 1.0))
            if sdata.get("prompt_text"):
                set_state_safe(f"sovits_prompt_{spk}", sdata.get("prompt_text"))
            if sdata.get("ref_audio_path"):
                set_state_safe(f"sovits_saved_path_{spk}", sdata.get("ref_audio_path"))
        set_state_safe(f"style_select_{spk}", style)

def render_generation_status(work_dir, active_at_render, pause_ms=500):
    # Only the small status panel polls. The worker never touches Streamlit state.
    @st.fragment(run_every=2 if active_at_render else None)
    def panel():
        job = get_job(work_dir)
        if not job:
            return
        active = is_running(work_dir)
        if active_at_render and not active:
            # Re-enable controls and display terminal results once, then stop polling.
            st.rerun()
        st.progress(float(job.get("progress", 0)))
        st.write(f"**저장 완료 {job['done']} / {job['total']}개** · 기존 파일 재사용 {job.get('reused', 0)}개")
        st.caption(f"작업 범위: {job['first']}번 ~ {job['last']}번")
        if job.get("execution_mode") in ("ordered_parallel_v2918", "ordered_parallel_v2919", "ordered_parallel_v2920", "ordered_independent_v2921"):
            st.caption("Qwen 요청 최대 4개(코랩 GPU 배치 1~2개) · Chirp 최대 4개 · Gemini 최대 2개 · CosyVoice 2 동시 수는 아래에 표시됩니다. CosyVoice 3는 요청 최대 4개·GPU 계산 1개입니다. 완료 대사는 번호순으로 한 번 합칩니다.")
            engine_names = {"qwen-bank": "Qwen 기본 목소리 20종", "qwen": "이전 Qwen 작업", "chirp": "Chirp 3 HD", "gemini": "Gemini", "cosyvoice": "CosyVoice 2", "cosyvoice3": "CosyVoice 3", "gpt-sovits": "GPT-SoVITS", "supertonic": "Supertonic"}
            for engine, progress in (job.get("engine_progress") or {}).items():
                label = engine_names.get(engine, engine)
                status = {"running": "생성 중", "complete": "완료", "failed": "오류로 중단", "paused": "일시 중단",
                          "quota_wait": "한도 대기 · 자동 재개 예정"}.get(progress.get("status"), "대기")
                receiving = f" · 수신 {progress['receiving']}개" if progress.get('receiving') else ""
                st.caption(f"{label}: 저장 {progress['done']}/{progress['total']}개 · 진행 {progress.get('active', 0)}개{receiving} · {status}")
                for index, retry in (job.get("retrying_lines") or {}).items():
                    if retry.get("engine") == engine:
                        phase = retry['phase']
                        if retry.get("waiting_for_quota"):
                            remaining = max(0, math.ceil(retry.get("quota_retry_at", 0) - time.time()))
                            phase = f"Gemini 요청 제한(429) · 약 {remaining}초 후 자동 이어서 생성 · 버튼을 다시 누르지 않아도 됩니다."
                        st.info(f"{index}번({retry['speaker']}): {phase}")
                error = (job.get("engine_errors") or {}).get(engine)
                if error and active:
                    suffix = "다른 엔진은 계속 생성합니다." if len(job["engine_progress"]) > 1 else "진행 중인 결과를 저장합니다."
                    st.warning(f"{label} · {error['index']}번({error['speaker']}): {error['message']} {suffix}")
            parallel = job.get("cosy_parallel") or {}
            if parallel:
                if parallel.get("enabled") is False:
                    st.caption("Cosy 순차 생성 · 현재 동시 1개")
                elif parallel.get("selection") == "adaptive_four":
                    phase = "실제 대사로 동시 수 조절 중" if parallel.get("tuning") else "관측한 속도에 맞춰 생성 중"
                    st.caption(f"Cosy 속도 자동 조절 · 최대 4개 · 현재 허용 {parallel.get('limit', 1)}개 · {phase}")
                    st.caption("빈자리가 생기면 다음 대사를 바로 시작합니다. 동시 수를 줄일 때도 진행 중인 대사는 끝까지 저장합니다.")
                    if parallel.get("measurements"):
                        with st.expander("Cosy 동시 수별 처리 속도"):
                            st.table([{"동시 수": row['limit'], "추정 처리율 (음성 초/초)": row['audio_per_second'],
                                       "관측 대사": row['samples']} for row in parallel['measurements']])
                            st.caption("완료한 실제 대사로 계산합니다. 참고 음성을 처음 분석한 대사·재생성·동시 수 전환 구간은 비교에서 제외하며, 말하기 속도 조절 전 길이를 사용합니다.")
                elif parallel.get("selection") == "fixed_four":
                    if parallel.get("calibrated"):
                        st.caption(f"Cosy 최대 4개 연속 생성 · 현재 허용 {parallel.get('limit', 1)}개 · 하나가 끝나면 다음 대사를 바로 시작합니다.")
                    else:
                        st.caption("Cosy 첫 대사를 생성·저장한 뒤 메모리 여유에 맞춰 최대 4개까지 채웁니다.")
                elif parallel.get("selection") == "measured_throughput":
                    phase = "속도 비교 중" if parallel.get("tuning") else "관측 결과 적용 중"
                    st.caption(f"Cosy 동시 생성: 현재 {parallel.get('limit', 1)}개 · {phase} · 관측상 선택 {parallel.get('best_limit', 1)}개")
                    if parallel.get("measurements"):
                        with st.expander("Cosy 동시 수별 관측 결과"):
                            st.table([{"동시 수": row['limit'], "생성 음성 초 / 처리 1초": row['audio_per_second'],
                                       "관측 대사": row['samples']} for row in parallel['measurements']])
                            st.caption("실제 생성 중인 대사로 비교합니다. 대사 길이·화자가 달라질 수 있어 고정 성능 시험 결과는 아닙니다.")
                elif parallel.get("calibrated"):
                    if parallel.get("target_limit"):
                        st.caption(f"Cosy 동시 생성: 설정 상한 {parallel['target_limit']}개 · 현재 허용 {parallel.get('limit', 1)}개 · 진행 {len(job.get('active_cosy_indices', []))}개")
                    else:
                        st.caption(f"Cosy 동시 생성: 현재 허용 {parallel.get('limit', 1)}개 · 진행 {len(job.get('active_cosy_indices', []))}개")
                else:
                    st.caption("Cosy 첫 대사 생성 중 · 결과를 저장한 뒤 메모리 여유를 보며 동시 수를 단계적으로 늘립니다.")
                if parallel.get("reason"):
                    st.caption(parallel["reason"])
                if parallel.get("memory_retries"):
                    st.caption(f"메모리 부족 대사 재처리 {parallel['memory_retries']}회 · 완료 파일과 음질 설정 유지")
                if parallel.get("continuous_queue") is False:
                    st.caption("현재 코랩은 32개 묶음 방식입니다. v2.9.16은 최대 4개 안에서 빈자리를 계속 채웁니다.")
                if parallel.get("selection") != "adaptive_four":
                    st.caption("속도 자동 조절은 코랩 v2.9.16부터 적용됩니다. 현재 작업을 완료하거나 저장 후 멈춘 다음, 왼쪽 업데이트 코드를 실행하고 새 주소로 연결해주세요.")
            for note in job.get("execution_notes", []):
                st.caption(note)
            if job.get("voice_preparation"):
                st.info(job["voice_preparation"].get("phase", "선택한 기본 목소리 준비 중…"))
            if job.get("gemini_key_usage"):
                with st.expander("Gemini 키별 요청·완료 상태", expanded=bool(job.get('engine_errors', {}).get('gemini'))):
                    st.table([{"키": f"키 {index}", "누적 요청 횟수": row['requests'], "완료 대사": row['completed'],
                               "실패 대사": row['errors'], "최근 상태": row['status']}
                              for index, row in sorted(job['gemini_key_usage'].items(), key=lambda pair: int(pair[0]))])
                    st.caption("‘누적 요청 횟수’는 이번 작업 전체의 요청 합계이며 재시도도 포함합니다. 분당 요청 수(RPM)나 Google의 남은 한도를 뜻하지 않습니다.")
        if job.get("generated", 0):
            estimate = (f"최근 완료 속도: 대사당 {job['throughput_seconds']:.1f}초"
                        if "throughput_seconds" in job else f"최근 새 대사 평균 {job.get('average_seconds', 0):.1f}초")
            if active and job.get("stage") == "voice" and job["generated"] >= 3:
                remaining = max(0, round(job.get("remaining_estimate_seconds", 0)))
                estimate += f" · 남은 생성 약 {remaining // 60}분 {remaining % 60}초"
            st.caption(estimate + " · 대사 길이에 따라 달라지며 최종 합치기 시간은 별도입니다.")
        latest = job.get("latest_metrics") or {}
        if latest.get("engine") == "cosyvoice":
            st.caption(f"최근 {latest['index']}번: 처리 {latest.get('total_seconds', 0):.1f}초"
                       f" · 만들어진 음성 길이 {latest.get('audio_seconds', 0):.1f}초")
            runtime = [latest.get("gpu_name"), latest.get("acceleration")]
            st.caption(f"코랩 v{latest.get('server_version') or '확인 필요'} · "
                       + " · ".join(value for value in runtime if value))
            if latest.get("wire_format") != "flac":
                st.caption("무손실 압축 전송은 코랩 v2.9.9부터 적용됩니다. 기존 WAV 전송도 계속 사용할 수 있습니다.")
            with st.expander("CosyVoice 처리 시간 자세히"):
                parts = []
                for field, label in (("reference_seconds", "참고 분석"), ("synthesis_seconds", "음성 계산"),
                                     ("postprocess_seconds", "코랩 후처리"), ("transport_seconds", "통신·미측정 대기"),
                                     ("save_seconds", "원음 저장")):
                    if field in latest:
                        parts.append(f"{label} {latest[field]:.1f}초")
                st.write(" · ".join(parts) or "이전 코랩은 세부 시간을 보내지 않습니다.")
                if latest.get("batch_stream"):
                    st.caption(f"연속 생성: 코랩 처리 {latest.get('server_seconds', 0):.1f}초"
                               f" · 파일 수신 {latest.get('download_seconds', 0):.1f}초"
                               f" · 원음 복원 {latest.get('decode_seconds', 0):.1f}초")
                    st.caption("여러 대사의 계산·전송 시간이 겹칠 수 있습니다. 전체 진행 속도는 위의 ‘최근 완료 속도’를 확인해주세요.")
                elif "download_seconds" in latest:
                    st.caption(f"통신 상세: 서버 계산 외 응답 대기 {latest.get('response_wait_seconds', 0):.1f}초"
                               f" · 파일 다운로드 {latest['download_seconds']:.1f}초"
                               f" · 연결 확인·요청 준비 {latest.get('client_preparation_seconds', 0):.1f}초"
                               f" · 음성 복원·검사 {latest.get('decode_seconds', 0):.1f}초")
                    st.caption("응답 대기에는 연결·요청 업로드·중계 서버·코랩의 미측정 처리가 포함됩니다. 순수 다운로드 시간과는 다릅니다.")
                    if latest.get("wire_format") == "flac":
                        st.caption(f"무손실 전송: WAV {latest.get('wav_bytes', 0) / 1024:.0f}KB"
                                   f" → 전송 {latest.get('wire_bytes', 0) / 1024:.0f}KB · 동일 PCM 원음으로 복원")
                if "llm_seconds" in latest:
                    if "acoustic_seconds" in latest:
                        st.caption(f"음성 계산 중 발음 순서 {latest['llm_seconds']:.1f}초"
                                   f" · 음향·파형 생성 {latest['acoustic_seconds']:.1f}초")
                    else:
                        st.caption(f"음성 계산 중 발음 순서 계산 {latest['llm_seconds']:.1f}초"
                                   f" · 나머지 처리 약 {max(0, latest.get('synthesis_seconds', 0) - latest['llm_seconds']):.1f}초")
                st.caption("음성 계산은 해당 대사가 처리된 경과 시간이며 GPU 대기와 재생성을 포함합니다. 전체 속도는 위의 ‘최근 완료 속도’로 확인해주세요.")
                if "sampling_seconds" in latest:
                    st.caption(f"발음 순서 계산에 포함된 후보 선택·GPU 대기 {latest['sampling_seconds']:.1f}초"
                               " · 앞선 GPU 계산이 끝나기를 기다린 시간도 포함합니다.")
                if "acoustic_wait_seconds" in latest:
                    st.caption(f"공유 음향 계산 대기 {latest['acoustic_wait_seconds']:.1f}초 · 발음 계산은 최대 4개 요청으로 진행하고 음향·파형 계산은 충돌 없이 차례로 처리합니다.")
                if "retries" in latest:
                    totals = job.get("performance", {})
                    st.caption(f"최근 대사 재시도 {latest['retries']}회 · 추가 처리 {latest.get('retry_seconds', 0):.1f}초"
                               f" · 저장 완료 대사 누적 재시도 {int(totals.get('retries', 0))}회")
                else:
                    st.caption("재시도 상세 표시는 코랩 v2.9.7부터 지원됩니다.")
                st.caption("개별 대사는 WAV 원음으로 저장하고, 마지막에 MP3 한 파일로 변환합니다.")
        if latest.get("engine") == "cosyvoice3":
            st.caption(f"최근 {latest['index']}번 CosyVoice 3: 전체 {latest.get('total_seconds', 0):.1f}초"
                       f" · 서버 생성 {latest.get('synthesis_seconds', 0):.1f}초"
                       f" · 수신·저장 {latest.get('download_seconds', 0):.1f}초")
            st.caption("버전 3은 GPU 계산 1개와 다음 요청 대기를 분리합니다. 완료 음성을 전송하는 동안 다음 대사를 계산하며, 버전 2·Gemini 작업도 별도로 진행됩니다.")
        if latest.get("engine") in ("qwen", "qwen-bank"):
            st.caption(f"최근 {latest['index']}번 Qwen: 전체 {latest.get('total_seconds', 0):.1f}초"
                       f" · GPU 계산 {latest.get('synthesis_seconds', 0):.1f}초"
                       f" · 수신·저장 {latest.get('download_seconds', 0):.1f}초"
                       f" · GPU 배치 {latest.get('batch_size', 1)}개")
            st.caption("GPU 배치 크기와 사이트의 요청 대기 수는 다릅니다. 완료된 WAV를 전송하는 동안 다음 GPU 작업을 이어갑니다.")
        if latest.get("engine") == "chirp":
            st.caption(f"최근 {latest['index']}번 Chirp: 전체 {latest.get('total_seconds', 0):.1f}초"
                       f" · Google 생성·수신 {latest.get('request_seconds', 0):.1f}초"
                       f" · 요청 간격·제한 대기 {latest.get('pacing_seconds', 0):.1f}초"
                       f" · WAV 저장 {latest.get('postprocess_seconds', 0):.1f}초")
        if latest.get("engine") == "gemini":
            st.caption(f"최근 {latest['index']}번 Gemini: 전체 {latest.get('total_seconds', 0):.1f}초"
                       f" · Google 응답·수신 {latest.get('request_seconds', 0):.1f}초"
                       f" · 요청 간격·한도 대기 {latest.get('pacing_seconds', 0):.1f}초"
                       f" · 원음 저장 {latest.get('postprocess_seconds', 0):.1f}초")
            if latest.get("retries"):
                st.caption(f"자동 재시도 {latest['retries']}회 · 이 중 한도 대기 후 재시도 {latest.get('quota_retries', 0)}회"
                           f" · 서버 오류 대기 {latest.get('retry_seconds', 0):.1f}초")
            st.caption(f"사용 모델: {latest.get('model', '확인 필요')} · 요청 {latest.get('attempts', 1)}회")
        current = job.get("current_metrics") or {}
        if active and current.get("engine") == "gemini":
            st.caption(f"현재 Gemini 모델: {current.get('model', '')} · 대기 단계는 아래에 표시됩니다.")
        if job.get("merge_seconds") is not None:
            st.caption(f"전체 MP3 합치기: {job['merge_seconds']:.1f}초")
        if active:
            st.info(job.get("message", "음성을 생성하고 있습니다."))
            if job.get("stage_started"):
                elapsed = max(0, int(time.time() - job["stage_started"]))
                st.caption(f"현재 단계 경과: {elapsed // 60}분 {elapsed % 60}초 · 진행 상황은 자동 갱신됩니다.")
            if job.get("stage") != "partial_merge":
                if st.button("진행 중인 대사 저장 후 멈추기", key="pause_generation_job"):
                    request_pause(work_dir)
                    st.session_state["_pause_requested_job"] = job["id"]
                if st.session_state.get("_pause_requested_job") == job["id"]:
                    st.warning("정지 요청을 받았습니다. 이미 요청한 음성은 응답을 받아 저장한 뒤 멈춥니다.")
        elif job.get("status") == "complete":
            if st.session_state.get("_generation_result_job") != job["id"]:
                result = job["result"]
                result["timings"] = [SimpleNamespace(**timing) for timing in result["timings"]]
                st.session_state["generation_result"] = result
                st.session_state["_generation_result_job"] = job["id"]
        else:
            if job.get("error"):
                st.error(job["error"])
            else:
                st.warning(job.get("message", "작업이 중단되었습니다."))
            st.caption("위의 '완료 파일도 새로 만들기'를 끄고 생성 버튼을 누르면, 같은 대사·설정의 완료 파일을 재사용합니다.")
            if job.get("done", 0):
                if job.get("partial_error"):
                    st.error(job["partial_error"])
                partial_audio = job.get("partial_audio", "")
                if job.get("partial_status") == "complete" and os.path.isfile(partial_audio):
                    saved_file_download("⬇️ 완료된 대사 MP3 한 파일 받기", partial_audio,
                                        file_name="completed_audio.mp3", mime="audio/mpeg", primary=True,
                                        key="download_partial_audio_v2913")
                    st.caption(f"저장된 {job.get('partial_count', job['done'])}개 대사만 대본 순서대로 합쳤습니다. 아직 생성하지 않은 대사는 포함되지 않습니다.")
                elif st.button("완료된 대사 한 파일로 합치기", key="prepare_partial_download_v2913",
                               type="primary", use_container_width=True):
                    try:
                        start_partial_merge(work_dir, pause_ms=pause_ms)
                    except Exception as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
    panel()


def main():
    # 크롬 브라우저 자동 번역으로 인한 React removeChild 크래시 원천 차단
    st.html("""
    <meta name="google" content="notranslate">
    <style>
    .notranslate {
        translate: no !important;
    }
    </style>
    <script>
    (function() {
        try {
            document.documentElement.setAttribute('translate', 'no');
            document.documentElement.classList.add('notranslate');
            if (document.body) {
                document.body.setAttribute('translate', 'no');
                document.body.classList.add('notranslate');
            }
            if (!document.querySelector('meta[name="google"][content="notranslate"]')) {
                const meta = document.createElement('meta');
                meta.name = 'google';
                meta.content = 'notranslate';
                document.head.appendChild(meta);
            }
        } catch (e) {}

        // React DOM removeChild/insertBefore Crash Guard (구글 번역 충돌 방어)
        if (typeof Node === 'function' && Node.prototype) {
            const origRemoveChild = Node.prototype.removeChild;
            Node.prototype.removeChild = function(child) {
                if (child && child.parentNode !== this) {
                    if (console && console.warn) {
                        console.warn('React Crash Guard: Blocked invalid removeChild');
                    }
                    return child;
                }
                return origRemoveChild.apply(this, arguments);
            };

            const origInsertBefore = Node.prototype.insertBefore;
            Node.prototype.insertBefore = function(newNode, refNode) {
                if (refNode && refNode.parentNode !== this) {
                    if (console && console.warn) {
                        console.warn('React Crash Guard: Blocked invalid insertBefore');
                    }
                    return newNode;
                }
                return origInsertBefore.apply(this, arguments);
            };
        }
    })();
    </script>
    """)

    # 고품격 스튜디오 디자인 테마 CSS 주입
    st.markdown("""
    <style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap');

    html, body, [class*="css"] {
        font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, Roboto, sans-serif;
    }

    /* 전역 다크 테마 & 폰트 */
    .stApp {
        background-color: #0b0f19 !important;
        color: #f8fafc !important;
    }

    /* 메인 영역 모든 텍스트 고대비 보장 */
    .main p, .main span, .main label, .main h1, .main h2, .main h3, .main h4, .main h5 {
        color: #f8fafc !important;
    }

    /* 여백 및 레이아웃 최적화 */
    .main .block-container {
        padding-top: 1.8rem !important;
        padding-bottom: 4rem !important;
        max-width: 1320px !important;
    }

    /* 프리미엄 Hero 배너 */
    .hero-wrapper {
        background: linear-gradient(135deg, rgba(24, 20, 68, 0.95) 0%, rgba(15, 23, 42, 0.98) 50%, rgba(45, 10, 85, 0.95) 100%);
        border: 1px solid rgba(168, 85, 247, 0.45);
        border-radius: 22px;
        padding: 2.2rem 2.6rem;
        margin-bottom: 2rem;
        box-shadow: 0 20px 45px -15px rgba(0, 0, 0, 0.7);
        position: relative;
        overflow: hidden;
    }

    .hero-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: rgba(168, 85, 247, 0.25);
        border: 1px solid rgba(192, 132, 252, 0.55);
        color: #f3e8ff !important;
        padding: 5px 14px;
        border-radius: 9999px;
        font-size: 0.82rem;
        font-weight: 700;
        letter-spacing: 0.5px;
        margin-bottom: 0.9rem;
    }

    .hero-title, h1.hero-title, .hero-wrapper h1 {
        font-size: 2.45rem;
        font-weight: 800;
        letter-spacing: -0.8px;
        color: #ffffff !important;
        margin-bottom: 0.8rem;
        line-height: 1.25;
        text-shadow: 0 2px 12px rgba(0, 0, 0, 0.8) !important;
    }

    .hero-title span {
        color: #ffffff !important;
    }

    .gradient-text {
        background: linear-gradient(135deg, #c084fc 0%, #38bdf8 50%, #f472b6 100%) !important;
        -webkit-background-clip: text !important;
        -webkit-text-fill-color: transparent !important;
    }

    .hero-subtitle {
        font-size: 1.05rem;
        color: #e2e8f0 !important;
        line-height: 1.6;
        margin-bottom: 1.4rem;
        max-width: 950px;
    }

    .tag-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
    }

    .tag-chip {
        display: inline-flex;
        align-items: center;
        padding: 6px 13px;
        border-radius: 8px;
        font-size: 0.82rem;
        font-weight: 600;
    }
    .tag-chip-super { background: rgba(245, 158, 11, 0.2); border: 1px solid rgba(245, 158, 11, 0.6); color: #fef08a !important; }
    .tag-chip-gemini { background: rgba(56, 189, 248, 0.2); border: 1px solid rgba(56, 189, 248, 0.6); color: #e0f2fe !important; }
    .tag-chip-edge { background: rgba(16, 185, 129, 0.2); border: 1px solid rgba(16, 185, 129, 0.6); color: #d1fae5 !important; }
    .tag-chip-sovits { background: rgba(236, 72, 153, 0.2); border: 1px solid rgba(236, 72, 153, 0.6); color: #fce7f3 !important; }
    .tag-chip-sub { background: rgba(129, 140, 248, 0.2); border: 1px solid rgba(129, 140, 248, 0.6); color: #e0e7ff !important; }

    /* 사이드바 프리미엄 다크 테마 */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #090d16 0%, #0f172a 45%, #191438 100%) !important;
        border-right: 1px solid rgba(168, 85, 247, 0.3) !important;
        box-shadow: 4px 0 25px rgba(0, 0, 0, 0.5) !important;
    }

    [data-testid="stSidebar"][aria-expanded="true"] {
        width: 380px !important;
        min-width: 380px !important;
    }

    [data-testid="stSidebarUserContent"] {
        padding-left: 1.25rem !important;
        padding-right: 1.25rem !important;
    }

    @media (max-width: 767px) {
        [data-testid="stSidebar"][aria-expanded="true"] {
            width: min(380px, calc(100vw - 2rem)) !important;
            min-width: 0 !important;
        }
    }

    [data-testid="stSidebar"] * {
        color: #f1f5f9;
    }

    [data-testid="stSidebar"] h1, 
    [data-testid="stSidebar"] h2, 
    [data-testid="stSidebar"] h3, 
    [data-testid="stSidebar"] h4 {
        color: #ffffff !important;
        font-weight: 700 !important;
    }

    [data-testid="stSidebar"] hr {
        border-color: rgba(255, 255, 255, 0.15) !important;
    }

    [data-testid="stSidebar"] .stAlert {
        background: rgba(30, 41, 59, 0.85) !important;
        border: 1px solid rgba(168, 85, 247, 0.45) !important;
        border-radius: 12px !important;
        color: #f8fafc !important;
    }

    [data-testid="stSidebar"] .stRadio > div {
        background: rgba(15, 23, 42, 0.8) !important;
        border: 1px solid rgba(255, 255, 255, 0.12) !important;
        border-radius: 12px !important;
        padding: 12px 14px !important;
    }

    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] {
        gap: 0.65rem !important;
    }

    [data-testid="stSidebar"] [data-testid="stRadio"] label {
        height: auto !important;
        align-items: flex-start !important;
    }

    [data-testid="stSidebar"] [data-testid="stRadio"] label p,
    [data-testid="stSidebar"] button p {
        white-space: normal !important;
        word-break: keep-all !important;
        overflow-wrap: anywhere !important;
        line-height: 1.55 !important;
    }

    [data-testid="stSidebar"] .stRadio label,
    [data-testid="stSidebar"] .stCheckbox label,
    [data-testid="stSidebar"] label p,
    [data-testid="stSidebar"] label span {
        color: #ffffff !important;
        font-weight: 600 !important;
    }

    [data-testid="stSidebar"] input[type="text"], 
    [data-testid="stSidebar"] input[type="password"] {
        background: #1e293b !important;
        border: 1px solid rgba(168, 85, 247, 0.5) !important;
        color: #ffffff !important;
        border-radius: 10px !important;
    }

    /* 대형 주 버튼 스타일링 */
    button[kind="primary"] {
        background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #d946ef 100%) !important;
        border: none !important;
        color: #ffffff !important;
        font-weight: 700 !important;
        font-size: 1.05rem !important;
        padding: 0.65rem 1.4rem !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 20px -2px rgba(139, 92, 246, 0.5) !important;
        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }

    button[kind="primary"] * {
        color: #ffffff !important;
    }

    button[kind="primary"]:hover {
        transform: translateY(-2px) scale(1.01) !important;
        box-shadow: 0 8px 25px rgba(139, 92, 246, 0.7) !important;
    }

    /* 보조 버튼 스타일링 (미리듣기, 테스트 버튼 등) - 절대 흰색/투명 배경 금지 */
    button[kind="secondary"] {
        background: #1e293b !important;
        border: 1px solid rgba(168, 85, 247, 0.55) !important;
        color: #ffffff !important;
        font-weight: 700 !important;
        border-radius: 10px !important;
        transition: all 0.2s ease !important;
    }

    button[kind="secondary"] * {
        color: #ffffff !important;
    }

    button[kind="secondary"]:hover {
        background: #3b0764 !important;
        border-color: #c084fc !important;
        color: #ffffff !important;
        transform: translateY(-1px) !important;
    }

    /* 카드 컨테이너 고대비 다크 스타일링 */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 16px !important;
        border: 1px solid rgba(139, 92, 246, 0.35) !important;
        background: #131b2e !important;
        box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.6) !important;
        transition: all 0.3s ease !important;
    }

    div[data-testid="stVerticalBlockBorderWrapper"]:hover {
        border-color: rgba(192, 132, 252, 0.6) !important;
        box-shadow: 0 12px 35px -8px rgba(139, 92, 246, 0.4) !important;
    }

    /* 셀렉트박스 & 입력창 고대비 다크 */
    div[data-baseweb="select"] > div {
        background-color: #1e293b !important;
        border: 1px solid rgba(139, 92, 246, 0.45) !important;
        color: #ffffff !important;
    }
    div[data-baseweb="select"] * {
        color: #ffffff !important;
    }

    input[type="text"], input[type="password"], textarea {
        background-color: #1e293b !important;
        border: 1px solid rgba(139, 92, 246, 0.45) !important;
        color: #ffffff !important;
        border-radius: 10px !important;
    }

    /* 오디오 플레이어 커스텀 */
    audio {
        border-radius: 12px !important;
        width: 100% !important;
        filter: drop-shadow(0 4px 10px rgba(0, 0, 0, 0.35));
    }

    /* 프로그레스 바 그라데이션 */
    .stProgress > div > div > div > div {
        background: linear-gradient(90deg, #6366f1, #a855f7, #ec4899) !important;
        border-radius: 9999px !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # 화려한 Hero 비주얼 배너 렌더링
    st.markdown("""
    <div class="hero-wrapper notranslate" translate="no">
        <div class="hero-badge">🎙️ NEXT-GEN AI VOICE PRODUCTION STUDIO PRO</div>
        <h1 class="hero-title"><span style="color:#ffffff !important;">멀티 보이스 오디오 드라마</span> <span class="gradient-text">스튜디오 PRO</span></h1>
        <p class="hero-subtitle">
            소설 및 시나리오 속 <b>실제 등장인물과 대사</b>를 AI가 스마트하게 자동 분석하고, 
            <b>초고속 로컬 무료 AI(Supertonic 3)</b> · <b>성우급 감정 연기(Gemini Flash)</b> · <b>AI 목소리 복제(Colab GPU)</b>로 
            생생한 멀티 보이스 낭독 오디오와 싱크 자막(SRT/VTT)을 원클릭으로 제작합니다.
        </p>
        <div class="tag-row">
            <span class="tag-chip tag-chip-super">☁️ Chirp 3 HD (월 100만 자 무료)</span>
            <span class="tag-chip tag-chip-gemini">⚡ Gemini 3.1 / 3.8 Flash 감정 연기</span>
            <span class="tag-chip tag-chip-sovits">🎙️ AI 제로샷 목소리 복제 (Colab 16GB GPU)</span>
            <span class="tag-chip tag-chip-sub">📝 싱크 정밀 자막(SRT/VTT)</span>
        </div>
        <div style="background: rgba(34, 197, 94, 0.15); border: 1px solid #22c55e; border-radius: 8px; padding: 8px 14px; margin: 12px auto 0 auto; max-width: 650px; color: #86efac; font-size: 13px; font-weight: 600; text-align: center;">
            ☁️ 개인 코랩 연결 · 본인 구글 계정으로 실행 · 접속별 작업 공간
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.caption("현재 적용 버전: " + APP_VERSION)

    # 작업 디렉토리 설정
    work_dir = session_workspace(st.session_state)
    generation_active = is_running(work_dir) or is_kaggle_running(work_dir)
    correct_legacy_gemini_voices()
    retire_qwen_customvoice(generation_active)
    if st.session_state.get("_qwen_bank_migration_notice"):
        st.info("이전 Qwen 화자를 같은 성별의 기본 목소리 20종으로 옮겼습니다: "
                + ", ".join(st.session_state["_qwen_bank_migration_notice"])
                + ". 새 목소리는 음색이 다르므로 생성 전에 배정을 확인해주세요. 기존 생성 파일은 보관합니다.")

    # 지연된 대본 텍스트가 있다면 위젯 생성 전에 안전하게 적용
    if "pending_script_text" in st.session_state:
        st.session_state["script_editor"] = st.session_state.pop("pending_script_text")

    # Keep existing scripts and references when removing engines from the UI.
    retired_engines = {
        "xtts": ("cosyvoice", "xtts", "cosy", "CosyVoice 2 목소리 복제"),
        "f5-tts": ("gpt-sovits", "f5", "sovits", "GPT-SoVITS 목소리 복제"),
    }
    previous_mode = st.session_state.get("active_engine_mode")
    if previous_mode in retired_engines:
        st.session_state["active_engine_mode"] = retired_engines[previous_mode][0]
    for speaker, config in st.session_state.get("voice_settings", {}).items():
        previous_engine = config.get("engine")
        if previous_engine in retired_engines:
            engine, old_prefix, prefix, voice = retired_engines[previous_engine]
            config.update(engine=engine, voice=voice)
            config["ref_audio_path"] = config.get("ref_audio_path") or st.session_state.get(f"{old_prefix}_saved_path_{speaker}", "")
            config["prompt_text"] = config.get("prompt_text") or st.session_state.get(f"{old_prefix}_prompt_{speaker}", "")
            st.session_state[f"engine_select_{speaker}"] = engine
            st.session_state[f"{prefix}_saved_path_{speaker}"] = config["ref_audio_path"]
            st.session_state[f"{prefix}_speed_{speaker}"] = config.get("speed", 1.0)
            st.session_state[f"{prefix}_prompt_{speaker}"] = config["prompt_text"]
            if engine == "gpt-sovits":
                st.session_state[f"sovits_prompt_ref_{speaker}"] = config["ref_audio_path"]
    for setting in list(st.session_state):
        if setting.startswith(("xtts_", "input_xtts_", "checked_xtts_", "f5_", "input_f5_", "checked_f5_")):
            st.session_state.pop(setting, None)

    # Personal connections and optional API keys belong only to this browser session.
    for setting in ("gemini_api_key", "cloud_tts_api_key", "gpt_sovits_url", "cosyvoice_url", "qwen_bank_url"):
        if setting not in st.session_state:
            st.session_state[setting] = ""

    if "gemini_model" not in st.session_state or "tts" not in str(st.session_state.get("gemini_model", "")) or "2.0" in str(st.session_state.get("gemini_model", "")):
        st.session_state["gemini_model"] = "gemini-3.1-flash-tts-preview"

    if "script_editor" not in st.session_state:
        st.session_state["script_editor"] = DEFAULT_SAMPLE_SCRIPT
    if "parsed_segments" not in st.session_state:
        st.session_state["parsed_segments"] = []
    if "speakers" not in st.session_state:
        st.session_state["speakers"] = []
    if "voice_settings" not in st.session_state:
        st.session_state["voice_settings"] = {}
    if "active_engine_mode" not in st.session_state:
        st.session_state["active_engine_mode"] = "qwen-bank"
    if "generation_result" not in st.session_state:
        st.session_state["generation_result"] = None
    if "last_processed_file_id" not in st.session_state:
        st.session_state["last_processed_file_id"] = None

    # 원스톱 대본 변환 및 화자 분석 헬퍼 함수
    def process_and_setup_script(
        raw_text: str,
        remove_stage_dirs: bool = False,
        update_editor: bool = True,
        custom_characters: Optional[List[str]] = None,
        use_gemini: bool = False,
        gemini_key: str = "",
        gemini_model: str = "gemini-3.8-flash",
        progress_callback: Optional[Any] = None
    ):
        if not raw_text or not raw_text.strip():
            return False, "대본 내용을 먼저 입력해주세요."
        
        # 1. 이미 화자 구분이 된 대본인지 확인 (0.05초 초고속 처리)
        if is_already_formatted_script(raw_text):
            segs = parse_story_precisely(raw_text, custom_characters=custom_characters)
        elif use_gemini:
            if not gemini_key:
                return False, "현재 세션에 등록된 Gemini API 키가 없어 AI 화자 분석을 진행할 수 없습니다."
            try:
                segs = parse_story_with_gemini(
                    raw_text,
                    api_key=gemini_key,
                    model=gemini_model,
                    custom_characters=custom_characters,
                    progress_callback=progress_callback
                )
            except Exception as e:
                return False, f"Gemini AI 분석 실패: {str(e)}"
        else:
            segs = parse_story_precisely(raw_text, custom_characters=custom_characters)

        if not segs:
            return False, "대본에서 유효한 대사나 내용을 찾을 수 없습니다."

        # 2. '화자: 대사' 텍스트로 표준화
        formatted_script = "\n".join([f"{s[0]}: {s[1]}" for s in segs])
        # Retain character descriptions/stage directions for later casting even
        # when the speaking script is normalized or stage directions are removed.
        if raw_text != st.session_state.get("casting_formatted_text"):
            st.session_state["casting_source_text"] = raw_text
        st.session_state["casting_formatted_text"] = formatted_script
        if update_editor:
            try:
                st.session_state["script_editor"] = formatted_script
            except Exception:
                # 위젯이 이미 화면에 렌더링된 후(버튼 클릭 등) 안전하게 지연 반영
                st.session_state["pending_script_text"] = formatted_script

        # 3. ScriptParser로 개별 세그먼트 생성
        parser = ScriptParser()
        segments = parser.parse(formatted_script, remove_stage_directions=remove_stage_dirs)
        speakers = parser.extract_speakers(segments)

        st.session_state["parsed_segments"] = segments
        st.session_state["speakers"] = speakers

        # 4. 현재 엔진 프리셋 및 스타일 자동 배정 (위젯 상태 동기화 포함)
        mode = st.session_state.get("active_engine_mode", "supertonic")
        set_speakers_preset(mode)
        st.session_state["generation_result"] = None
        spk_summary = ", ".join(speakers)
        return True, f"✨ 총 {len(speakers)}명의 화자({spk_summary})와 {len(segments)}개 대사로 정밀 변환 및 배정 완료!"

    # 최초 접속 시 기본 샘플 대본 1회 자동 분석 (화자 카드가 바로 나타나도록)
    if "initialized_auto_pipeline" not in st.session_state:
        st.session_state["initialized_auto_pipeline"] = True
        if st.session_state.get("script_editor"):
            process_and_setup_script(st.session_state["script_editor"])

    # 사이드바 설정
    with st.sidebar:
        st.header("⚙️ 엔진 & 환경 설정")
        render_kaggle_downloads(work_dir, generation_active)

        # 1. 엔진 모드 선택
        engine_mode_options = [
            "👑 Supertonic 3 (무료 한국어)",
            "⚡ Gemini Flash TTS (감정 연기)",
            "🔥 CosyVoice 2 (목소리 복제)",
            "🎙️ GPT-SoVITS v4 (코랩 목소리 복제)",
            "🔀 하이브리드 (인물별 선택)",
            "☁️ Google Chirp 3 HD (월 100만 자 무료)",
            "🎭 Qwen 기본 목소리 20종 (남성 10 · 여성 10)",
            "🔥 CosyVoice 3 (목소리 20종)"
        ]
        curr_idx = 0
        if st.session_state["active_engine_mode"] == "gemini":
            curr_idx = 1
        elif st.session_state["active_engine_mode"] == "cosyvoice":
            curr_idx = 2
        elif st.session_state["active_engine_mode"] == "gpt-sovits":
            curr_idx = 3
        elif st.session_state["active_engine_mode"] == "custom":
            curr_idx = 4
        elif st.session_state["active_engine_mode"] == "chirp":
            curr_idx = 5
        elif st.session_state["active_engine_mode"] == "qwen-bank":
            curr_idx = 6
        elif st.session_state["active_engine_mode"] == "cosyvoice3":
            curr_idx = 7

        selected_mode_label = st.radio(
            "🎙️ TTS 기본 엔진 선택",
            options=engine_mode_options,
            index=curr_idx,
            help="새 대본 분석에 사용할 기본 엔진을 선택합니다. 현재 화자의 엔진·성우·스타일·참조 음성은 바뀌지 않습니다. 코지2·3는 위에서 선택한 캐글 또는 코랩으로 생성합니다."
        )

        new_mode = "supertonic"
        if "Gemini" in selected_mode_label:
            new_mode = "gemini"
        elif "CosyVoice 3" in selected_mode_label:
            new_mode = "cosyvoice3"
        elif "CosyVoice" in selected_mode_label:
            new_mode = "cosyvoice"
        elif "GPT-SoVITS" in selected_mode_label:
            new_mode = "gpt-sovits"
        elif "하이브리드" in selected_mode_label:
            new_mode = "custom"
        elif "Chirp" in selected_mode_label:
            new_mode = "chirp"
        elif "기본 목소리 20종" in selected_mode_label:
            new_mode = "qwen-bank"

        # 기본 엔진 선택은 기존 화자 설정을 덮어쓰지 않는다.
        # 전체 화자 변경은 화자 설정 영역의 명시적인 일괄 적용 버튼에서만 수행한다.
        if new_mode != st.session_state["active_engine_mode"]:
            st.session_state["active_engine_mode"] = new_mode
            st.rerun()
        st.caption("기본 엔진을 바꿔도 현재 화자별 설정은 유지됩니다. 모두 바꾸려면 화자 설정 영역의 ‘전체 …’ 버튼을 눌러주세요.")

        if not use_kaggle_gpu():
            render_cosy3_connection(
                prominent=st.session_state["active_engine_mode"] in ("cosyvoice3", "custom")
                or any(config.get("engine") == "cosyvoice3" for config in st.session_state["voice_settings"].values()))
            render_qwen_bank_connection(
                prominent=st.session_state["active_engine_mode"] in ("qwen-bank", "custom")
                or any(config.get("engine") == "qwen-bank" for config in st.session_state["voice_settings"].values()))
        chirp_api_key = render_chirp_settings(
            prominent=not use_kaggle_gpu() and (st.session_state["active_engine_mode"] in ("chirp", "custom")
            or any(config.get("engine") == "chirp" for config in st.session_state["voice_settings"].values())))

        # 2. Supertonic 옵션 안내
        if not use_kaggle_gpu() and st.session_state["active_engine_mode"] == "supertonic":
            st.success("✅ Supertonic 3 로컬 엔진 활성화됨 (100% 완전 무료 · API 키 불필요)")
            st.caption("💡 하이브(HYBE) 수퍼톤의 가벼운 고속 ONNX 모델로, 내 컴퓨터에서 완전 무료로 동작합니다.")

        # Keep existing session values without displaying the Gemini settings panel.
        gemini_model_options = [
            "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-preview-tts",
            "gemini-2.5-pro-preview-tts"
        ]
        curr_model = st.session_state.get("gemini_model", "gemini-3.1-flash-tts-preview")
        if curr_model not in gemini_model_options:
            curr_model = "gemini-3.1-flash-tts-preview"
        gemini_api_key = st.session_state.get("gemini_api_key", "")
        gemini_model = curr_model

        # Visitors connect their own GPU runtime; URLs stay in session state.
        if not use_kaggle_gpu() and st.session_state["active_engine_mode"] in ["cosyvoice", "gpt-sovits", "custom"]:
            render_connections(st.session_state["active_engine_mode"])
        elif not use_kaggle_gpu() and any(config.get("engine") == "cosyvoice" for config in st.session_state["voice_settings"].values()):
            render_connections("cosyvoice")
        if generation_active:
            st.caption("음성 생성 중입니다. 작업을 지우려면 먼저 생성을 멈춰주세요.")
        else:
            render_reset()

        st.divider()
        st.markdown("#### 🎚️ 재생 및 자막 설정")
        pause_sec = st.slider("대사 간 무음 간격 (초)", min_value=0.1, max_value=3.0, value=0.5, step=0.1)
        pause_ms = int(pause_sec * 1000)
        
        remove_stage = st.checkbox("지문/지시문(괄호 안 텍스트) 자동 제거", value=False)
        include_spk_in_sub = st.checkbox("자막에 화자 이름 표시", value=True)

    # Show the selected compute route, separately from the default engine.
    if use_kaggle_gpu():
        st.info("현재 생성 위치: **캐글 GPU 2개** · 지원 모델: **CosyVoice 2 / 3**\n\n"
                "**대본 분석 → 2번 캐글 모델 설정에서 적용 → 화자별 목소리·스타일 확인 → 3번 캐글 음성 생성**")
    else:
        # 메인 상단 엔진 상태 안내 바
        st.info(
            f"💡 **현재 기본 엔진: {selected_mode_label}**\n"
            "- **Qwen 기본 목소리 20종**: 남성 10개·여성 10개를 바로 선택 · 처음 한 번 자동 준비 · 녹음 업로드 불필요\n"
            "- **CosyVoice 3**: 버전 2와 별도 서버 · 남성 10명·여성 10명 선택 · 감정 스타일 · 한국어 참고 음성 등록\n"
            "- **Google Chirp 3 HD**: 코랩 없이 한국어 음성 생성 · 월 100만 자까지 무료, 초과분 과금\n"
            "- **Supertonic 3**: 하이브 수퍼톤 한국어 모델로 **완전 무료 + 인터넷/키 없이도 로컬에서 초고속 생성**\n"
            "- **Gemini Flash**: 구글 최신 Gemini 멀티모달 오디오 모델 기반의 **스튜디오 성우급 감정 연기**\n"
            "- **AI 목소리 복제 (CosyVoice / GPT-SoVITS)**: 본인 구글 코랩을 연결하고 참조 음성과 실제 대사를 등록해 사용합니다."
        )

    # Step 1: 대본 입력
    st.subheader("1️⃣ 대본 입력 및 자동 변환")
    custom_chars_str = st.text_input(
        "👤 소설 속 주요 등장인물 직접 지정 (선택사항, 쉼표로 구분)",
        placeholder="예시: 만복, 옥련, 원복, 부인 (여기에 적으시면 '스승', '친구' 등 지나가는 인물이 생기지 않고 딱 이 인물들로만 대본이 정밀 완성됩니다)",
        key="custom_characters_input",
        help="소설 본문에서 대사를 할 실제 주요 인물들만 쉼표로 적어주시면 다른 잡음 인물이 생기는 것을 원천 방지합니다."
    )
    custom_chars_list = [c.strip() for c in custom_chars_str.split(",") if c.strip()] if custom_chars_str else None

    col_btn1, col_btn2, col_btn3, col_btn4 = st.columns([2.5, 2.8, 2.5, 2.2])
    
    with col_btn1:
        if st.button("✨ 소설 대본 자동 변환 (로컬 정밀 분석)", help="소설 글(따옴표 대사)의 앞뒤 문맥을 분석하여 실제 인물들만 추출하여 '화자: 대사' 대본으로 자동 변환합니다.", use_container_width=True):
            curr_text = st.session_state.get("script_editor", "")
            if curr_text.strip():
                with st.spinner("소설 속 대사와 등장인물을 정밀 문맥 분석하여 대본으로 변환 중..."):
                    ok, msg = process_and_setup_script(curr_text, remove_stage_dirs=remove_stage, custom_characters=custom_chars_list)
                    if ok:
                        st.toast(msg)
                        st.rerun()
                    else:
                        st.error(msg)
            else:
                st.warning("입력창에 소설이나 이야기 글을 먼저 입력해주세요.")

    with col_btn2:
        if st.button("🤖 Gemini AI 화자 완벽 분석 (100% 정밀)", help="Google Gemini 2.0 Flash AI가 소설 전체의 인간관계와 대화를 완벽히 이해하여 100% 정확한 대본으로 변환합니다.", use_container_width=True):
            curr_text = st.session_state.get("script_editor", "")
            if curr_text.strip():
                if is_already_formatted_script(curr_text):
                    with st.spinner("이미 화자가 표기된 대본을 0.05초 초고속으로 정밀 분석 중..."):
                        ok, msg = process_and_setup_script(
                            curr_text,
                            remove_stage_dirs=remove_stage,
                            custom_characters=custom_chars_list
                        )
                        if ok:
                            st.toast("⚡ 이미 화자가 명확히 구분된 대본 파일이므로 0.05초 만에 즉시 배정 완료되었습니다!")
                            st.rerun()
                        else:
                            st.error(msg)
                elif not gemini_api_key:
                    st.error("현재 세션에 등록된 Gemini API 키가 없어 AI 화자 분석을 진행할 수 없습니다.")
                else:
                    progress_bar = st.progress(0.0)
                    status_text = st.empty()
                    def on_gemini_prog(cur, tot):
                        progress_bar.progress(cur / tot)
                        status_text.info(f"🤖 Gemini AI가 소설 문맥을 정밀 분석 중... ({cur}/{tot} 파트 완료)")
                    
                    with st.spinner("Gemini AI가 소설 문맥과 인간관계를 정밀 분석하여 대본으로 변환 중..."):
                        ok, msg = process_and_setup_script(
                            curr_text,
                            remove_stage_dirs=remove_stage,
                            custom_characters=custom_chars_list,
                            use_gemini=True,
                            gemini_key=gemini_api_key,
                            gemini_model="gemini-3.8-flash",
                            progress_callback=on_gemini_prog
                        )
                    progress_bar.empty()
                    status_text.empty()
                    if ok:
                        st.toast(msg)
                        st.rerun()
                    else:
                        st.error(msg)
            else:
                st.warning("입력창에 소설이나 이야기 글을 먼저 입력해주세요.")

    with col_btn3:
        uploaded_file = st.file_uploader("대본 텍스트 파일 (.txt) 업로드", type=["txt"], label_visibility="collapsed")
        if uploaded_file is not None:
            # 파일 고유 식별자로 최초 1회만 처리 (무한 re-read 루프 방지)
            file_id = f"{uploaded_file.name}_{uploaded_file.size}"
            if st.session_state.get("last_processed_file_id") != file_id:
                st.session_state["last_processed_file_id"] = file_id
                file_bytes = uploaded_file.getvalue()
                text_content = file_bytes.decode("utf-8", errors="ignore")
                if text_content.strip():
                    with st.spinner("대본 파일을 분석하여 화자를 자동 배정 중..."):
                        ok, msg = process_and_setup_script(
                            text_content,
                            remove_stage_dirs=remove_stage,
                            custom_characters=custom_chars_list
                        )
                        if ok:
                            st.toast(f"✅ 파일 업로드 완료! {msg}")
                            st.rerun()
                        else:
                            st.error(msg)

    with col_btn4:
        if st.button("📜 [창고지기 성복] 예제 불러오기", use_container_width=True):
            if os.path.exists(SEONGBOK_PATH):
                with open(SEONGBOK_PATH, "r", encoding="utf-8") as f:
                    content = f.read()
                ok, msg = process_and_setup_script(content, remove_stage_dirs=remove_stage)
                if ok:
                    st.toast("창고지기 성복 대본을 성공적으로 불러왔습니다!")
                    st.rerun()
                else:
                    st.error(msg)

    # 텍스트 에어리어 위젯 선언 (안정적인 고정 key로 화면 깜빡임 완벽 차단)
    input_text = st.text_area(
        "대본 내용을 입력하거나 확인하세요 (소설 글을 붙여넣고 '대본 자동 변환'을 누르면 즉시 '화자: 대사'로 변환됩니다):",
        key="script_editor",
        height=260
    )

    # 대본 분석 및 화자 배정 버튼
    if st.button("🔍 화자 및 대사 분석하기 (수정 사항 즉시 반영)", type="primary", use_container_width=True):
        text_to_process = st.session_state.get("script_editor", input_text)
        if not text_to_process.strip():
            st.warning("대본 내용을 먼저 입력해주세요.")
        else:
            with st.spinner("대본 속 화자와 대사를 정밀 분석 중..."):
                ok, msg = process_and_setup_script(text_to_process, remove_stage_dirs=remove_stage, custom_characters=custom_chars_list)
                if ok:
                    st.toast(msg)
                    st.rerun()
                else:
                    st.error(msg)

    # Step 2: 화자별 목소리 설정
    if st.session_state["speakers"]:
        st.divider()
        st.subheader("2️⃣ 화자별 목소리 및 성우 배정")
        if st.session_state.get("_gemini_gender_corrections"):
            st.info("이전 버전의 잘못된 성별 표기를 바로잡고 성우를 조정했습니다. "
                    + " · ".join(st.session_state["_gemini_gender_corrections"])
                    + " · 이미 만든 음성은 다음 생성부터 수정됩니다.")
        
        if use_kaggle_gpu():
            render_kaggle_setup(st.session_state["speakers"], set_speakers_preset, generation_active)
        else:
            # 일괄 변경 원클릭 버튼 바
            if st.button("🎭 전체 화자에 기본 목소리 20종 배정", use_container_width=True,
                         disabled=generation_active, key="qwen_bank_cast_all",
                         help="성별별로 서로 다른 목소리를 우선 배정합니다. 생성 버튼을 누르면 선택한 목소리를 코랩에서 처음 한 번 준비합니다."):
                set_speakers_preset("qwen-bank")
                st.toast("새 기본 목소리를 배정했습니다. 성별과 목소리를 확인해주세요.")
                st.rerun()
            with st.expander("🎭 새 기본 목소리 20종 목록 보기"):
                st.caption("남성 10개·여성 10개의 제작 목소리 목록입니다. 선택한 목소리는 첫 생성 때 준비하며, 이후 같은 기준 음성을 재사용합니다.")
                st.table([{"번호": key, "성별": row["gender"], "목소리": row["name"], "특징": row["description"]}
                          for key, row in QWEN_BANK_VOICES.items()])
            if st.button("☁️ 전체 화자를 Google Chirp 3 HD로 변경", use_container_width=True,
                         disabled=generation_active, key="chirp_cast_all",
                         help="현재 성별과 호환되는 보이스 이름을 우선 유지합니다. Gemini API 호출 없이 바꾸며, 생성 버튼을 눌러야 음성을 만듭니다."):
                set_speakers_preset("chirp")
                st.toast("전체 화자를 Chirp 3 HD로 변경했습니다. 각 화자의 성별과 목소리를 확인해주세요.")
                st.rerun()
            col_bar1, col_bar2, col_bar3 = st.columns(3)
            with col_bar1:
                if st.button("👑 전체 Supertonic 3 (로컬 무료)", use_container_width=True):
                    set_speakers_preset("supertonic")
                    st.toast("모든 화자가 Supertonic 3 로컬 무료 모델로 일괄 변경되었습니다!")
                    st.rerun()
            with col_bar2:
                if st.button("⚡ 전체 Gemini Flash (성우 연기)", use_container_width=True,
                             key="gemini_cast_all", disabled=generation_active,
                             help="현재 대본과 인물 정보를 분석해 성별·나이·역할에 맞는 보이스와 스타일을 함께 설정합니다. Gemini API 키가 필요합니다."):
                    casting_status = st.empty()
                    try:
                        if not gemini_api_key.strip():
                            raise CastingError("현재 세션에 등록된 Gemini API 키가 없어 자동 배정을 진행할 수 없습니다.")
                        # Parse current editor contents, including edits since the
                        # last analysis, without changing anything until AI succeeds.
                        pairs = parse_story_precisely(st.session_state.get("script_editor", ""),
                                                     custom_characters=custom_chars_list)
                        parser = ScriptParser()
                        cast_segments = parser.parse("\n".join(f"{s}: {t}" for s, t in pairs),
                                                     remove_stage_directions=remove_stage)
                        cast_speakers = parser.extract_speakers(cast_segments)
                        profiles = {s: st.session_state.get(f"voice_profile_{s}", "") for s in cast_speakers}
                        existing = {s: gemini_preset(s, st.session_state["voice_settings"].get(s))
                                    for s in cast_speakers}
                        source_text = casting_source_text()
                        with st.spinner("대본 전체에서 화자의 성별·나이·관계·말투를 분석하고 보이스와 스타일을 고르는 중..."):
                            cast = analyze_gemini_casting(
                                script=source_text, speakers=cast_speakers, profiles=profiles,
                                existing=existing, voices=GEMINI_VOICES, styles=VOICE_STYLES,
                                api_key=gemini_api_key, progress=casting_status.caption)
                        casting_status.empty()
                        apply_casting_to_state(st.session_state, result=cast, speakers=cast_speakers,
                                               segments=cast_segments, script=source_text, profiles=profiles)
                        st.toast(f"{len(cast_speakers)}명 보이스·스타일 자동 설정 완료")
                        st.rerun()
                    except CastingError as exc:
                        casting_status.empty()
                        st.error(str(exc))
            with col_bar3:
                if st.button("🎙️ 전체 AI 목소리 복제 (Colab GPU)", use_container_width=True):
                    set_speakers_preset("gpt-sovits")
                    st.toast("모든 화자가 AI 목소리 복제로 일괄 변경되었습니다!")
                    st.rerun()

            if st.button("🔥 전체 CosyVoice 3 목소리 배정", key="assign_all_cosy3",
                         disabled=generation_active, use_container_width=True):
                set_speakers_preset("cosyvoice3")
                st.toast("CosyVoice 3 목소리를 성별에 맞춰 중복을 줄여 배정했습니다.")
                st.rerun()
            st.caption("⚡ 전체 Gemini Flash를 누르면 대본을 분석해 화자별 보이스·성별·스타일까지 함께 설정합니다.")
        active_casting = current_casting_report()
        if active_casting:
            st.success(f"대본 맞춤 보이스·스타일 설정 완료 · {len(active_casting)}명")
            uncertain = [s for s, row in active_casting.items() if row["needs_review"]]
            if uncertain:
                st.warning("성별 단서가 부족한 화자: " + ", ".join(uncertain)
                           + " · 기존 성우 성별을 임시 유지했습니다. 아래 카드에서 성우 성별을 선택하거나 인물 정보를 적고 다시 분석해주세요.")
            with st.expander("📋 화자별 분석 결과와 추천 보이스 보기", expanded=True):
                st.dataframe([{
                    "화자": s, "성별": row["detected_gender"] if row["detected_gender"] != "불명" else row["gender_note"],
                    "나이": row["age"], "역할·말투": f"{row['role']} · {row['personality']}",
                    "추천 보이스": row["voice"], "추천 스타일": row["style"], "추천 이유": row["reason"],
                } for s, row in active_casting.items()], hide_index=True, use_container_width=True)
                st.caption("표는 분석 결과입니다. 이후 직접 바꾼 보이스와 스타일은 각 화자 카드의 현재 설정이 적용됩니다.")

        # 화자별 대사 개수 통계 뱃지
        spk_counts = {}
        for seg in st.session_state["parsed_segments"]:
            spk_counts[seg.speaker] = spk_counts.get(seg.speaker, 0) + 1
        spk_summary_str = " · ".join([f"**{spk}** ({spk_counts.get(spk, 0)}개 대사)" for spk in st.session_state["speakers"]])
        st.info(f"👥 **감지된 총 {len(st.session_state['speakers'])}명의 화자**: {spk_summary_str}")

        # 화자 통합 및 이름 변경 도구 (오인된 화자 합치기)
        with st.expander("🛠️ 화자 통합 및 이름 변경 도구 (오인된 화자 합치기 / 삭제)", expanded=False):
            st.caption("대본 분석 중 오인되어 분리된 화자를 다른 화자나 나레이션으로 즉시 합치거나 이름을 바꿀 수 있습니다.")
            m1, m2, m3 = st.columns([2, 2, 1.5])
            with m1:
                merge_from = st.selectbox("합칠 대상 화자", options=st.session_state["speakers"], key="m_from")
            with m2:
                other_spks = [s for s in st.session_state["speakers"] if s != merge_from]
                merge_to_options = other_spks + ["직접 새 이름 입력..."]
                merge_to_choice = st.selectbox("변경할 대상 화자", options=merge_to_options, key="m_to_sel")
                if merge_to_choice == "직접 새 이름 입력...":
                    merge_target = st.text_input("새 화자 이름", value="", key="m_custom_name")
                else:
                    merge_target = merge_to_choice
            with m3:
                st.write("")
                st.write("")
                if st.button("🔄 화자 합치기 적용", use_container_width=True):
                    if merge_from and merge_target and merge_target.strip():
                        target_name = merge_target.strip()
                        updated_script_lines = []
                        for seg in st.session_state["parsed_segments"]:
                            new_s = target_name if seg.speaker == merge_from else seg.speaker
                            updated_script_lines.append(tagged_line(seg, speaker=new_s))
                        new_script_text = "\n".join(updated_script_lines)
                        st.session_state["pending_script_text"] = new_script_text
                        try:
                            st.session_state["script_editor"] = new_script_text
                        except Exception:
                            pass
                        ok, msg = process_and_setup_script(new_script_text, remove_stage_dirs=remove_stage, custom_characters=custom_chars_list, update_editor=False)
                        st.toast(f"'{merge_from}' 화자가 '{target_name}'(으)로 통합되었습니다!")
                        st.rerun()

        st.caption(f"💡 총 **{len(st.session_state['speakers'])}명**의 화자 카드. 각 카드에서 엔진과 음성 스타일(감정/연령/톤)을 자유롭게 설정할 수 있습니다.")

        st.button("✨ 화자별 추천 스타일 한 번에 적용", on_click=apply_recommended_styles,
                  help="대본과 입력한 인물 정보를 바탕으로 스타일만 바꿉니다. Chirp의 목소리·속도는 유지하며, GPT-SoVITS에는 참조 음성 선택 가이드로 표시됩니다.")
        st.caption("추천은 화자 이름·인물 정보·해당 화자의 대사를 바탕으로 합니다. 나이나 성격이 불분명하면 인물 정보를 직접 적어주세요.")

        # 34종 음성 스타일 프리셋 갤러리 (접이식 안내)
        with st.expander("🎨 34종 음성 스타일(감정/연령/톤) 전체 목록 보기", expanded=False):
            st.markdown("##### 🎭 감정 및 어조 스타일 (23종)")
            emotion_styles = [
                "🎤 기본", "😊 밝고 활기차게", "🌙 차분하고 따뜻하게", "💌 부드럽고 감성적", "📺 뉴스 앵커",
                "📖 동화 나레이션", "⚡ 긴박하게", "😢 슬프게", "✨ 신비롭게", "😄 유머러스하게",
                "🎭 진지하게", "🤫 속삭이듯", "💪 힘차게", "👻 으스스하게", "🌈 희망차게",
                "📽️ 다큐멘터리", "📻 라디오 DJ", "🎓 강의/교육", "🏆 스포츠 중계", "💼 비즈니스",
                "🧘 명상/ASMR", "🎪 광고 나레이션", "😠 분노/격양"
            ]
            st.write(" · ".join([f"`{s}`" for s in emotion_styles]))

            st.markdown("##### 🕰️ 연령대 및 성숙도 스타일 (11종)")
            age_styles = [
                "💡 30대 세련된", "☕ 30대 따뜻한", "🍷 40대 성숙한", "🏛️ 40대 안정적인",
                "🎩 50대 깊이 있는", "🍂 50대 여유로운", "👴 시니어 중후한 (60대)",
                "👵 시니어 따뜻한 (70대)", "🧙 시니어 지혜로운 (70~80대)",
                "📰 시니어 안정적인 (65세)", "🎭 시니어 감성적인 (70대 이상)"
            ]
            st.write(" · ".join([f"`{s}`" for s in age_styles]))
            st.caption("Gemini: 연령·감정·말투 지시 / CosyVoice v2.9.2: 연기 지시+참조 목소리 / Supertonic: 속도·음량 보정 / GPT-SoVITS: 참조 목소리 기준")
            st.caption("시니어 스타일은 노년 캐릭터의 연기 설정입니다. 시니어 청취자용 해설에는 '차분하고 따뜻하게'도 추천합니다.")

        # 각 화자별 설정 카드 (3열 반응형 레이아웃)
        cols = st.columns(3)
        for idx, spk in enumerate(st.session_state["speakers"]):
            col = cols[idx % 3]
            with col:
                with st.container(border=True):
                    current_cfg = st.session_state["voice_settings"].get(spk, {})
                    spk_engine = current_cfg.get("engine", st.session_state["active_engine_mode"])
                    if spk_engine == "custom":
                        spk_engine = "supertonic"
                    if spk_engine == "qwen":
                        st.info(f"{spk}: 이전 Qwen 작업을 완료하면 기본 목소리 20종으로 전환됩니다.")
                        continue

                    # 화자 헤더 (글자 줄바꿈 원천 방지 및 초고대비 배치)
                    st.markdown(
                        f"""<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; padding-bottom:8px; border-bottom:1px solid rgba(139,92,246,0.3);">
                            <span style="font-size:1.35rem; font-weight:800; color:#ffffff !important; text-shadow:0 1px 4px rgba(0,0,0,0.6); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                                👤 {spk}
                            </span>
                            <span style="font-size:0.75rem; font-weight:800; padding:4px 10px; border-radius:6px; background:#4f46e5 !important; color:#ffffff !important; border:1px solid #818cf8; letter-spacing:0.5px;">
                                {spk_engine.upper()}
                            </span>
                        </div>""", 
                        unsafe_allow_html=True
                    )
                    engine_choices = ["supertonic", "gemini", "cosyvoice", "cosyvoice3", "gpt-sovits", "chirp", "qwen-bank"]
                    if use_kaggle_gpu():
                        # Keep the current assignment visible until explicitly changed.
                        engine_choices = ["cosyvoice", "cosyvoice3"]
                        if spk_engine not in engine_choices:
                            engine_choices.insert(0, spk_engine)
                    chosen_engine = st.selectbox(
                        "음성 엔진 선택",
                        options=engine_choices,
                        index=engine_choices.index(spk_engine) if spk_engine in engine_choices else 0,
                        format_func=lambda e: {
                            "supertonic": "👑 Supertonic 3 (로컬 무료)",
                            "gemini": "⚡ Gemini Flash",
                            "chirp": "☁️ Google Chirp 3 HD",
                            "qwen-bank": "🎭 기본 목소리 20종 (코랩)",
                            "cosyvoice": "🔥 CosyVoice 2 (캐글 목소리 복제)" if use_kaggle_gpu() else "🔥 CosyVoice 2 (코랩 GPU 복제)",
                            "cosyvoice3": "🔥 CosyVoice 3 (캐글 · 목소리 20종)" if use_kaggle_gpu() else "🔥 CosyVoice 3 (별도 서버 · 목소리 20종)",
                            "gpt-sovits": "🎙️ GPT-SoVITS (복제)",
                        }.get(e, e),
                        key=f"engine_select_{spk}"
                    )
                    
                    # 현재 스타일 가져오기
                    cur_style = resolve_style(current_cfg.get("style"), VOICE_STYLES,
                                              fallback=DEFAULT_CHARACTER_STYLES.get(spk, "🎤 기본"))

                    if chosen_engine != spk_engine:
                        # 화자 엔진이 바뀌면 해당 엔진 기본 프리셋으로 갱신
                        if chosen_engine == "supertonic":
                            p = SUPERTONIC_CHARACTER_PRESETS.get(spk, {"voice": "F1", "style": cur_style})
                            current_cfg = {"engine": "supertonic", "voice": p["voice"], "style": p.get("style", cur_style), "speed": 1.0}
                            set_state_safe(f"supertonic_voice_{spk}", p["voice"])
                        elif chosen_engine == "cosyvoice3":
                            used = {row.get("voice") for name, row in st.session_state["voice_settings"].items()
                                    if name != spk and row.get("engine") == "cosyvoice3"}
                            current_cfg = cosy3_character_preset(spk, current_cfg, used)
                            sync_cosy3_widgets(spk, current_cfg)
                        elif chosen_engine == "cosyvoice":
                            candidate_ref = os.path.join(work_dir, "ref_audios", "나레이션_참고 TTS.wav") if ("나레이션" in spk or "해설" in spk) else ""
                            candidate_prompt = ""
                            if candidate_ref and os.path.exists(candidate_ref):
                                c_txt_file = candidate_ref + ".txt"
                                if os.path.exists(c_txt_file):
                                    try:
                                        with open(c_txt_file, "r", encoding="utf-8") as cf:
                                            candidate_prompt = cf.read().strip()
                                    except Exception:
                                        pass
                            current_cfg = {
                                "engine": "cosyvoice",
                                "voice": "CosyVoice 2 목소리 복제",
                                "style": cur_style,
                                "ref_audio_path": candidate_ref if (candidate_ref and os.path.exists(candidate_ref)) else "",
                                "prompt_text": candidate_prompt,
                                "speed": 1.0
                            }
                            set_state_safe(f"cosy_speed_{spk}", 1.0)
                            if candidate_prompt:
                                set_state_safe(f"cosy_prompt_{spk}", candidate_prompt)
                            if candidate_ref:
                                set_state_safe(f"cosy_saved_path_{spk}", candidate_ref)
                        elif chosen_engine == "gemini":
                            current_cfg = gemini_preset(spk, current_cfg)
                            set_state_safe(f"gemini_voice_{spk}", current_cfg["voice"])
                            set_state_safe(f"gemini_gender_{spk}", current_cfg["gender"])
                        elif chosen_engine == "qwen-bank":
                            used = {row.get("voice") for name, row in st.session_state["voice_settings"].items()
                                    if name != spk and row.get("engine") == "qwen-bank"}
                            current_cfg = qwen_bank_preset(spk, current_cfg, used)
                            set_state_safe(f"qwen_bank_voice_{spk}", current_cfg["voice"])
                            set_state_safe(f"qwen_bank_gender_{spk}", current_cfg["gender"])
                        elif chosen_engine == "chirp":
                            current_cfg = chirp_preset(spk, current_cfg)
                            set_state_safe(f"chirp_voice_{spk}", current_cfg["voice"])
                            set_state_safe(f"chirp_gender_{spk}", current_cfg["gender"])
                            set_state_safe(f"chirp_speed_{spk}", current_cfg["speed"])
                        elif chosen_engine == "gpt-sovits":
                            candidate_ref = os.path.join(work_dir, "ref_audios", "나레이션_참고 TTS.wav") if ("나레이션" in spk or "해설" in spk) else ""
                            candidate_prompt = ""
                            if candidate_ref and os.path.exists(candidate_ref):
                                c_txt_file = candidate_ref + ".txt"
                                if os.path.exists(c_txt_file):
                                    try:
                                        with open(c_txt_file, "r", encoding="utf-8") as cf:
                                            candidate_prompt = cf.read().strip()
                                    except Exception:
                                        pass
                            current_cfg = {
                                "engine": "gpt-sovits",
                                "voice": "GPT-SoVITS 목소리 복제",
                                "style": cur_style,
                                "ref_audio_path": candidate_ref if (candidate_ref and os.path.exists(candidate_ref)) else "",
                                "prompt_text": candidate_prompt,
                                "speed": 1.0,
                                "temperature": 1.0,
                                "top_k": 15,
                                "top_p": 1.0
                            }
                            set_state_safe(f"sovits_speed_{spk}", 1.0)
                            set_state_safe(f"sovits_temp_{spk}", 1.0)
                            if candidate_prompt:
                                set_state_safe(f"sovits_prompt_{spk}", candidate_prompt)
                            if candidate_ref:
                                set_state_safe(f"sovits_saved_path_{spk}", candidate_ref)
                        else:
                            p = EDGE_CHARACTER_PRESETS.get(spk, {"voice": "ko-KR-SunHiNeural", "style": cur_style, "rate": 0, "pitch": 0})
                            current_cfg = {"engine": "edge-tts", "voice": p["voice"], "style": p.get("style", cur_style), "rate": p.get("rate", 0), "pitch": p.get("pitch", 0)}
                            set_state_safe(f"edge_voice_{spk}", p["voice"])
                        st.session_state["voice_settings"][spk] = current_cfg
                        spk_engine = chosen_engine
                        st.rerun()

                    with st.expander("👤 인물 정보 · 추천 조정", expanded=False):
                        st.text_input("나이·역할·성격", key=f"voice_profile_{spk}", max_chars=200,
                                      placeholder="예: 70대 할머니, 다정하고 차분함")
                    if use_kaggle_gpu() and spk_engine not in ("cosyvoice", "cosyvoice3"):
                        st.warning("현재 화자는 캐글에서 지원하지 않는 엔진입니다. 위에서 코지2·3를 선택하거나 ‘캐글 모델 설정’에서 전체 화자에 적용해주세요.")
                        continue
                    recommendation = recommend_style(spk, st.session_state["parsed_segments"],
                                                     st.session_state.get(f"voice_profile_{spk}", ""))
                    cast_info = active_casting.get(spk)
                    if cast_info:
                        recommendation = SimpleNamespace(style=cast_info["style"], reason=cast_info["reason"])
                        if cast_info["needs_review"]:
                            st.warning("성별 확인 필요 · 아래 성우 성별을 확인해주세요.")
                        st.caption(f"대본 분석: {cast_info['age']} · {cast_info['role']} · {cast_info['personality']}")
                        if cast_info["gender_quote"]:
                            st.caption(f"성별 근거: {cast_info['gender_quote']}")
                    recommendation_label = style_display_label(resolve_style(recommendation.style, VOICE_STYLES))
                    if spk_engine not in ("chirp", "qwen-bank"):
                        st.markdown(f"**💡 추천: {recommendation_label}**")
                        st.caption(recommendation.reason)
                        st.button("추천 참조 스타일 선택" if spk_engine == "gpt-sovits" else "추천 스타일 적용",
                                  key=f"recommend_style_{spk}", on_click=apply_recommended_styles,
                                  args=(spk,), use_container_width=True)

                    # 1. Supertonic 3 설정 폼 (로컬 무료)
                    if spk_engine == "supertonic":
                        default_v = current_cfg.get("voice", "F1")
                        try:
                            def_idx = SUPERTONIC_VOICE_KEYS.index(default_v)
                        except ValueError:
                            def_idx = 0
                            
                        selected_voice = st.selectbox(
                            "보이스 캐릭터 (로컬 무료)",
                            options=SUPERTONIC_VOICE_KEYS,
                            index=def_idx,
                            format_func=get_supertonic_voice_label,
                            key=f"supertonic_voice_{spk}"
                        )

                        selected_style = render_style_selector("🎨 스타일 (속도·음량 보정)", spk, cur_style)
                        st.caption(f"✨ {style_description(selected_style, VOICE_STYLES)}")
                        st.caption(style_note(spk_engine, selected_style, VOICE_STYLES))

                        st.session_state["voice_settings"][spk] = {
                            "engine": "supertonic",
                            "voice": selected_voice,
                            "style": selected_style,
                            "speed": 1.0
                        }

                        # 목소리 미리듣기 버튼 (Supertonic)
                        if st.button(f"🔊 {spk} Supertonic 미리듣기 (무료)", key=f"preview_btn_{spk}", use_container_width=True, disabled=generation_active):
                            safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                            preview_file = os.path.join(work_dir, f"preview_super_{safe_spk}.mp3")
                            sample_text = preview_text(spk, st.session_state["parsed_segments"])
                            
                            cfg = VoiceConfig(
                                engine="supertonic",
                                voice=selected_voice,
                                style=selected_style
                            )
                            with st.spinner(f"'{spk}' ({selected_voice} · {selected_style}) Supertonic 음성 생성 중..."):
                                try:
                                    TTSEngine.generate_preview(
                                        voice_config=cfg,
                                        output_file=preview_file,
                                        sample_text=sample_text
                                    )
                                    if os.path.exists(preview_file):
                                        st.audio(preview_file, format="audio/mp3")
                                        st.caption(f'💬 샘플: "{sample_text}" [{selected_style}]')
                                except Exception as e:
                                    st.error(f"음성 생성 실패: {str(e)}")

                    # 2. Gemini Flash TTS 설정 폼
                    elif spk_engine == "gemini":
                        default_v = current_cfg.get("voice", "Kore")
                        default_gender = GEMINI_VOICES.get(default_v, {}).get("gender", "여성")
                        selected_gender = st.radio(
                            "성우 성별", ["남성", "여성"],
                            index=0 if default_gender == "남성" else 1, horizontal=True,
                            key=f"gemini_gender_{spk}", on_change=change_gemini_gender, args=(spk,))
                        voice_options = [voice for voice in GEMINI_VOICE_KEYS
                                         if GEMINI_VOICES[voice]["gender"] == selected_gender]
                        selected_key = f"gemini_voice_{spk}"
                        if st.session_state.get(selected_key, default_v) not in voice_options:
                            st.session_state[selected_key] = voice_options[0]
                        selected_voice = st.selectbox(
                            "보이스 선택",
                            options=voice_options,
                            index=voice_options.index(default_v) if default_v in voice_options else 0,
                            format_func=get_gemini_voice_label,
                            key=f"gemini_voice_{spk}"
                        )

                        selected_style = render_style_selector("🎨 음성 스타일 (감정 연기 지시)", spk, cur_style)
                        st.caption(f"✨ {style_description(selected_style, VOICE_STYLES)}")
                        st.caption(style_note(spk_engine, selected_style, VOICE_STYLES))

                        st.session_state["voice_settings"][spk] = {
                            "engine": "gemini",
                            "voice": selected_voice,
                            "gender": selected_gender,
                            "style": selected_style,
                            "speed": 1.0
                        }

                        # 목소리 미리듣기 버튼 (Gemini)
                        if st.button(f"🔊 {spk} Gemini Flash 미리듣기", key=f"preview_btn_{spk}", use_container_width=True, disabled=generation_active):
                            if not gemini_api_key:
                                st.error("현재 세션에 등록된 Gemini API 키가 없어 미리듣기를 생성할 수 없습니다.")
                            else:
                                safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                                preview_file = os.path.join(work_dir, f"preview_gemini_{safe_spk}.mp3")
                                sample_text = preview_text(spk, st.session_state["parsed_segments"])
                                
                                safe_gemini_model = gemini_model if gemini_model in gemini_model_options else "gemini-3.1-flash-tts-preview"
                                cfg = VoiceConfig(
                                    engine="gemini",
                                    voice=selected_voice,
                                    style=selected_style,
                                    model=safe_gemini_model,
                                    api_key=gemini_api_key
                                )
                                with st.spinner(f"'{spk}' ({selected_voice} · {selected_style}) Gemini Flash 음성 생성 중..."):
                                    try:
                                        TTSEngine.generate_preview(
                                            voice_config=cfg,
                                            output_file=preview_file,
                                            sample_text=sample_text
                                        )
                                        if os.path.exists(preview_file):
                                            st.audio(preview_file, format="audio/mp3")
                                            st.caption(f'💬 샘플: "{sample_text}" [{selected_style}]')
                                    except Exception as e:
                                        st.error(f"음성 생성 실패: {str(e)}")

                    elif spk_engine == "cosyvoice3":
                        render_cosy3_voice(spk, current_cfg, work_dir=work_dir,
                            segments=st.session_state["parsed_segments"], busy=generation_active,
                            style_selector=render_style_selector)
                    elif spk_engine == "qwen-bank":
                        render_qwen_bank_voice(spk, current_cfg, work_dir=work_dir,
                                               segments=st.session_state["parsed_segments"], busy=generation_active)
                    elif spk_engine == "chirp":
                        render_chirp_voice(spk, current_cfg, work_dir=work_dir,
                                           segments=st.session_state["parsed_segments"],
                                           api_key=chirp_api_key, busy=generation_active)

                    # 2.6. CosyVoice 2 설정 폼 (구글 코랩 16GB GPU 복제)
                    elif spk_engine == "cosyvoice":
                        st.markdown("**🔥 CosyVoice 2 목소리 복제**")
                        cosy_url = st.session_state.get("cosyvoice_url", "")
                        if not cosy_url and not use_kaggle_gpu():
                            st.warning("⚠️ 좌측 사이드바에 **'🔥 CosyVoice 코랩 접속 주소'**를 먼저 입력해주세요!")

                        ref_audio_val = current_cfg.get("ref_audio_path", "")
                        prompt_text_val = current_cfg.get("prompt_text", "")
                        speed_val = current_cfg.get("speed", 1.0)

                        if not ref_audio_val:
                            candidate_ref = os.path.join(work_dir, "ref_audios", "나레이션_참고 TTS.wav")
                            if os.path.exists(candidate_ref):
                                ref_audio_val = candidate_ref
                                if not prompt_text_val:
                                    c_txt_file = candidate_ref + ".txt"
                                    if os.path.exists(c_txt_file):
                                        try:
                                            with open(c_txt_file, "r", encoding="utf-8") as cf:
                                                prompt_text_val = cf.read().strip()
                                        except Exception:
                                            pass

                        saved_ref_key = f"cosy_saved_path_{spk}"
                        if saved_ref_key not in st.session_state and ref_audio_val:
                            st.session_state[saved_ref_key] = ref_audio_val

                        # 1. 파일 직접 업로드
                        ref_file = st.file_uploader(
                            "🎙️ 참조 오디오 파일 (.wav, .mp3) 업로드",
                            type=["wav", "mp3"],
                            key=f"cosy_upload_{spk}",
                            help="배경음 없이 한 사람이 문장을 말하는 3~30초 음성입니다. 5~10초 분량을 권장합니다."
                        )
                        
                        effective_ref_audio = ""
                        if ref_file is not None:
                            safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                            save_ref_dir = os.path.join(work_dir, "ref_audios")
                            os.makedirs(save_ref_dir, exist_ok=True)
                            raw_val = ref_file.getvalue()
                            import hashlib
                            raw_hash = hashlib.md5(raw_val).hexdigest()[:8]
                            uploaded_path = os.path.join(save_ref_dir, f"{safe_spk}_{raw_hash}_{upload_name(ref_file.name)}")
                            with open(uploaded_path, "wb") as f:
                                f.write(raw_val)
                            effective_ref_audio = uploaded_path
                            previous_ref = st.session_state.get(saved_ref_key, "")
                            st.session_state[saved_ref_key] = uploaded_path
                            st.success(f"✅ 오디오 파일 업로드 완료: **{ref_file.name}**")

                            txt_cache = uploaded_path + ".txt"
                            cached_prompt = ""
                            if os.path.isfile(txt_cache):
                                try:
                                    with open(txt_cache, encoding="utf-8") as cf:
                                        cached_prompt = cf.read().strip()
                                except OSError:
                                    pass
                            if previous_ref != uploaded_path:
                                prompt_text_val = cached_prompt
                                set_state_safe(f"cosy_prompt_{spk}", cached_prompt)
                            st.caption("아래 '참조 오디오 실제 대사'에 녹음에서 말한 내용을 그대로 입력해주세요.")
                        elif st.session_state.get(saved_ref_key) and os.path.exists(st.session_state[saved_ref_key]):
                            effective_ref_audio = st.session_state[saved_ref_key]
                        elif ref_audio_val and os.path.exists(ref_audio_val):
                            effective_ref_audio = ref_audio_val
                            st.session_state[saved_ref_key] = ref_audio_val

                        # 2. 등록된 참조 오디오 플레이어
                        if effective_ref_audio and os.path.isfile(effective_ref_audio):
                            col_a1, col_a2 = st.columns([3.5, 1.2])
                            with col_a1:
                                st.info(f"🎧 등록된 참조 음성: **{os.path.basename(effective_ref_audio)}**")
                            with col_a2:
                                if st.button("🗑️ 변경", key=f"cosy_del_audio_{spk}"):
                                    set_state_safe(saved_ref_key, "")
                                    set_state_safe(f"cosy_prompt_{spk}", "")
                                    effective_ref_audio = ""
                                    st.rerun()

                            try:
                                with open(effective_ref_audio, "rb") as af:
                                    st.audio(af.read(), format=f"audio/{effective_ref_audio.split('.')[-1]}")
                            except Exception:
                                pass

                        saved_prompt = st.session_state.get(f"cosy_prompt_{spk}", prompt_text_val)
                        prompt_input = st.text_input(
                            "참조 오디오 실제 대사",
                            value=saved_prompt,
                            key=f"cosy_prompt_{spk}",
                            help="위 참고 음성 전체에서 실제로 말한 내용을 그대로 적으세요. 새로 읽힐 대사는 아래 미리듣기 대사에 적습니다."
                        )

                        speed_cosy = st.slider("말하기 속도", 0.5, 2.0, float(speed_val), 0.05, key=f"cosy_speed_{spk}")
                        selected_style = render_style_selector("🎨 스타일", spk, cur_style)
                        st.caption(style_note(spk_engine, selected_style, VOICE_STYLES))

                        st.session_state["voice_settings"][spk] = {
                            "engine": "cosyvoice",
                            "voice": "CosyVoice 2 목소리 복제",
                            "style": selected_style,
                            "ref_audio_path": effective_ref_audio,
                            "prompt_text": prompt_input,
                            "speed": speed_cosy
                        }

                        import hashlib
                        default_sample = preview_text(spk, st.session_state["parsed_segments"])
                        sample_key = hashlib.sha256(default_sample.encode("utf-8")).hexdigest()[:10]
                        sample_text = st.text_area(
                            "미리듣기에서 읽힐 대사", value=default_sample, max_chars=300,
                            key=f"cosy_sample_{spk}_{sample_key}",
                            help="이 칸의 문장을 읽습니다. 위 참고 음성의 실제 대사와는 별개입니다."
                        )
                        if selected_style == "🎤 기본" and len(sample_text.strip()) < len(prompt_input.strip()) / 2:
                            st.caption("참고 대사에 비해 미리듣기 문장이 짧습니다. 가능하면 문장 1~2개로 들어보세요.")

                        # CosyVoice 미리듣기 버튼
                        if st.button(f"🔊 {spk} CosyVoice 미리듣기", key=f"cosy_preview_btn_{spk}", use_container_width=True, disabled=generation_active):
                            cosy_url = st.session_state.get("cosyvoice_url", "")
                            if use_kaggle_gpu():
                                start_kaggle_preview(work_dir, spk, sample_text, st.session_state["voice_settings"])
                            elif not cosy_url:
                                st.error("⚠️ 먼저 좌측 사이드바에 CosyVoice 코랩 주소를 입력해주세요!")
                            elif not effective_ref_audio:
                                st.error("참조 오디오 파일을 먼저 업로드해주세요!")
                            elif not prompt_input.strip():
                                st.error("참조 오디오 실제 대사를 입력해주세요.")
                            elif not sample_text.strip():
                                st.error("미리듣기에서 읽힐 대사를 입력해주세요.")
                            else:
                                safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                                preview_id = hashlib.sha256(
                                    f"{sample_text}|{selected_style}|{effective_ref_audio}|{prompt_input}|{speed_cosy}|v293".encode("utf-8")
                                ).hexdigest()[:12]
                                preview_file = os.path.join(work_dir, f"preview_cosy_{safe_spk}_{preview_id}.wav")
                                cfg = VoiceConfig(
                                    engine="cosyvoice",
                                    style=selected_style,
                                    cosyvoice_url=cosy_url,
                                    ref_audio_path=effective_ref_audio,
                                    prompt_text=prompt_input,
                                    speed_factor=speed_cosy
                                )
                                with st.spinner(f"'{spk}' CosyVoice 2 고음질 음성 복제 생성 중 (코랩 GPU)..."):
                                    try:
                                        TTSEngine.generate_preview(
                                            voice_config=cfg,
                                            output_file=preview_file,
                                            sample_text=sample_text
                                        )
                                        if os.path.exists(preview_file) and os.path.getsize(preview_file) > 0:
                                            st.audio(preview_file, format="audio/wav")
                                            st.caption(f'💬 샘플: "{sample_text}"')
                                            with open(preview_file, "rb") as af:
                                                st.download_button("⬇️ CosyVoice 미리듣기 WAV 받기", af.read(),
                                                                   file_name=f"{safe_spk}_미리듣기.wav", mime="audio/wav",
                                                                   key=f"cosy_download_{spk}_{preview_id}")
                                        else:
                                            st.error("❌ 오디오 파일이 생성되지 않았습니다.\n\n"
                                                     "**확인 사항:**\n"
                                                     "1. 코랩 탭에서 서버가 실행 중인지 확인\n"
                                                     "2. 사이드바 URL이 최신 trycloudflare 주소인지 확인\n"
                                                     "3. 참조 오디오가 3초 이상인지 확인")
                                    except Exception as e:
                                        st.error(f"❌ CosyVoice 음성 생성 실패: {e}")

                    # 3. GPT-SoVITS 설정 폼 (목소리 복제)
                    elif spk_engine == "gpt-sovits":
                        st.markdown("**🎙️ GPT-SoVITS 목소리 복제 (수정 코랩: v4 · 48kHz)**")
                        ref_audio_val = current_cfg.get("ref_audio_path", "")
                        prompt_text_val = current_cfg.get("prompt_text", "")
                        speed_val = current_cfg.get("speed", 1.0)
                        temp_val = current_cfg.get("temperature", 1.0)

                        st.caption("배경음 없이 한 사람만 말하는 3~10초 음성과, 그 음성의 정확한 대사를 함께 등록하세요.")
                        if st.button("↺ 기본 생성 설정으로 복원", key=f"sovits_reset_{spk}"):
                            speed_val = temp_val = 1.0
                            set_state_safe(f"sovits_speed_{spk}", 1.0)
                            set_state_safe(f"sovits_temp_{spk}", 1.0)
                            set_state_safe(f"sovits_steps_{spk}", 32)

                        # 나레이션 화자이고 기본 등록된 참고 파일이 있으면 자동 연결
                        if not ref_audio_val and ("나레이션" in spk or "해설" in spk) and f"sovits_saved_path_{spk}" not in st.session_state:
                            candidate_ref = os.path.join(work_dir, "ref_audios", "나레이션_참고 TTS.wav")
                            if os.path.exists(candidate_ref):
                                ref_audio_val = candidate_ref
                                if not prompt_text_val:
                                    c_txt_file = candidate_ref + ".txt"
                                    if os.path.exists(c_txt_file):
                                        try:
                                            with open(c_txt_file, "r", encoding="utf-8") as cf:
                                                prompt_text_val = cf.read().strip()
                                        except Exception:
                                            pass

                        saved_ref_key = f"sovits_saved_path_{spk}"
                        if saved_ref_key not in st.session_state and ref_audio_val:
                            st.session_state[saved_ref_key] = ref_audio_val

                        # 1. 파일 직접 업로드 (경로 입력 일절 불필요!)
                        ref_file = st.file_uploader(
                            "🎙️ 참조 오디오 파일 (.wav, .mp3) 업로드",
                            type=["wav", "mp3"],
                            key=f"sovits_upload_{spk}_{st.session_state.get(f'sovits_upload_version_{spk}', 0)}",
                            help="복제할 인물의 3~10초 길이 목소리 음원 파일 (업로드 시 경로 입력은 일절 필요 없습니다!)"
                        )
                        
                        effective_ref_audio = ""
                        if ref_file is not None:
                            safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                            save_ref_dir = os.path.join(work_dir, "ref_audios")
                            os.makedirs(save_ref_dir, exist_ok=True)
                            raw_val = ref_file.getvalue()
                            import hashlib
                            raw_hash = hashlib.md5(raw_val).hexdigest()[:8]
                            uploaded_path = os.path.join(save_ref_dir, f"{safe_spk}_{raw_hash}_{upload_name(ref_file.name)}")
                            with open(uploaded_path, "wb") as f:
                                f.write(raw_val)
                            effective_ref_audio = uploaded_path
                            st.session_state[saved_ref_key] = uploaded_path
                            st.success(f"✅ 오디오 파일 업로드 완료: **{ref_file.name}**")

                        elif st.session_state.get(saved_ref_key) and os.path.exists(st.session_state[saved_ref_key]):
                            effective_ref_audio = st.session_state[saved_ref_key]
                        elif ref_audio_val and os.path.exists(ref_audio_val):
                            effective_ref_audio = ref_audio_val
                            st.session_state[saved_ref_key] = ref_audio_val

                        # 2. 등록된 참조 오디오 플레이어 & 길이 점검
                        if effective_ref_audio and os.path.isfile(effective_ref_audio):
                            col_a1, col_a2 = st.columns([3.5, 1.2])
                            with col_a1:
                                st.info(f"🎧 등록된 참조 음성: **{os.path.basename(effective_ref_audio)}**")
                            with col_a2:
                                if st.button("🗑️ 오디오 변경", key=f"del_audio_{spk}", help="등록된 참조 오디오를 해제하고 새로 등록합니다."):
                                    set_state_safe(saved_ref_key, "")
                                    set_state_safe(f"sovits_prompt_{spk}", "")
                                    st.session_state["voice_settings"][spk]["ref_audio_path"] = ""
                                    st.session_state["voice_settings"][spk]["prompt_text"] = ""
                                    st.session_state[f"sovits_upload_version_{spk}"] = st.session_state.get(f"sovits_upload_version_{spk}", 0) + 1
                                    set_state_safe(f"sovits_manual_path_{spk}", "")
                                    effective_ref_audio = ""
                                    st.rerun()

                            if effective_ref_audio and os.path.isfile(effective_ref_audio):
                                try:
                                    with open(effective_ref_audio, "rb") as af:
                                        st.audio(af.read(), format=f"audio/{effective_ref_audio.split('.')[-1]}")
                                    import soundfile as sf
                                    a_info = sf.info(effective_ref_audio)
                                    dur = a_info.duration
                                    if dur > 10.0:
                                        st.warning(f"파일 길이 {dur:.1f}초: 문장이 끝나는 지점에서 3~10초로 잘라 다시 등록해주세요. 자동으로 자르지 않습니다.")
                                    elif dur < 3.0:
                                        st.warning(f"⚠️ 파일 길이가 **{dur:.1f}초**로 짧습니다. (권장: 3초~10초)")
                                    else:
                                        st.caption(f"⏱️ 파일 길이: **{dur:.1f}초** (✅ 최적 길이 3~10초 적합)")
                                except Exception:
                                    pass

                        st.caption("참조 음성은 위 업로드 버튼으로 등록해주세요.")

                        # Reset the transcript only when the selected reference changes.
                        prompt_ref_key = f"sovits_prompt_ref_{spk}"
                        previous_ref = st.session_state.get(prompt_ref_key, ref_audio_val)
                        if previous_ref != effective_ref_audio:
                            prompt_text_val = ""
                            transcript_path = effective_ref_audio + ".txt" if effective_ref_audio else ""
                            if transcript_path and os.path.isfile(transcript_path):
                                try:
                                    with open(transcript_path, encoding="utf-8") as transcript:
                                        prompt_text_val = transcript.read().strip()
                                except OSError:
                                    pass
                            set_state_safe(f"sovits_prompt_{spk}", prompt_text_val)
                        st.session_state[prompt_ref_key] = effective_ref_audio

                        # 4. 참조 오디오 실제 대사 추출 및 입력
                        col_lbl, col_wbtn = st.columns([2.6, 1.4])
                        with col_lbl:
                            st.markdown("**참조 오디오 실제 대사 (프롬프트 텍스트)**")
                        with col_wbtn:
                            extract_clicked = st.button(
                                "✨ 대사 자동 추출",
                                key=f"whisper_btn_{spk}",
                                help="자동 인식 결과에는 오타가 있을 수 있습니다. 직접 듣고 고친 뒤 사용하세요.",
                                use_container_width=True
                            )

                        if extract_clicked:
                            if effective_ref_audio and os.path.isfile(effective_ref_audio):
                                with st.spinner("AI(Whisper)가 오디오 대사를 듣고 추출 중..."):
                                    auto_txt = TTSEngine.transcribe_audio_whisper(effective_ref_audio)
                                    if auto_txt:
                                        prompt_text_val = auto_txt
                                        set_state_safe(f"sovits_prompt_{spk}", auto_txt)
                                        txt_cache = effective_ref_audio + ".txt"
                                        try:
                                            with open(txt_cache, "w", encoding="utf-8") as cf:
                                                cf.write(auto_txt)
                                        except Exception:
                                            pass
                                        st.toast(f"대사 자동 인식 완료: {auto_txt}")
                                        st.rerun()
                                    else:
                                        st.warning("대사를 추출하지 못했습니다. (직접 입력해주세요)")
                            else:
                                st.warning("먼저 오디오 파일을 업로드해주세요.")

                        # 현재 오디오에 대한 캐시된 실제 대사 확인
                        known_real_txt = ""
                        if effective_ref_audio and os.path.isfile(effective_ref_audio):
                            txt_cache = effective_ref_audio + ".txt"
                            if os.path.exists(txt_cache):
                                try:
                                    with open(txt_cache, "r", encoding="utf-8") as cf:
                                        known_real_txt = cf.read().strip()
                                except Exception:
                                    pass
                            if not prompt_text_val and known_real_txt:
                                prompt_text_val = known_real_txt
                                if f"sovits_prompt_{spk}" not in st.session_state:
                                    set_state_safe(f"sovits_prompt_{spk}", known_real_txt)

                        prompt_input = st.text_input(
                            "참조 오디오 실제 대사",
                            value=prompt_text_val,
                            placeholder="예: 꿈을 이루고자 하는 용기가 있다면 모든 꿈은 실현 가능하다.",
                            key=f"sovits_prompt_{spk}",
                            label_visibility="collapsed",
                            help="등록한 참조 음성에서 실제로 말한 문장과 일치해야 합니다. 빈 대사로는 생성하지 않습니다."
                        )
                        if known_real_txt:
                            st.caption("자동 추출한 대사는 오타가 있을 수 있습니다. 참조 음성을 듣고 위 입력란을 수정하세요. 직접 수정한 내용은 유지됩니다.")
                        else:
                            st.caption("생성할 대사가 아니라 참조 녹음에서 말한 문장을 입력하세요. 빠진 단어나 추가된 문장이 없는지 확인해주세요.")

                        col_s1, col_s2 = st.columns(2)
                        with col_s1:
                            speed_slider = st.slider(
                                "말하기 속도",
                                min_value=0.5,
                                max_value=2.0,
                                value=float(speed_val),
                                step=0.05,
                                key=f"sovits_speed_{spk}"
                            )
                        with col_s2:
                            temp_slider = st.slider(
                                "발음 샘플링 (Temperature)",
                                min_value=0.10,
                                max_value=1.00,
                                value=float(temp_val),
                                step=0.05,
                                key=f"sovits_temp_{spk}",
                                help="음색 유사도를 보장하는 수치가 아닙니다. 공식 API 기본값 1.0에서 먼저 확인하고 조금씩 조절하세요."
                            )

                        step_options = [8, 16, 32]
                        step_value = current_cfg.get("sample_steps", 32)
                        sample_steps = st.selectbox("v4 생성 단계", step_options,
                                                     index=step_options.index(step_value) if step_value in step_options else 2,
                                                     key=f"sovits_steps_{spk}",
                                                     help="32단계는 계산 시간이 더 걸립니다. 실제 음질은 참조 녹음과 대사에도 영향을 받습니다.")
                        st.caption("처음 비교할 때는 속도 1.0을 사용하세요. 미리듣기와 전체 생성에 같은 설정이 적용됩니다.")

                        selected_style = render_style_selector("🎨 참조 음성 스타일 가이드", spk, cur_style)
                        st.caption(style_note(spk_engine, selected_style, VOICE_STYLES))

                        st.session_state["voice_settings"][spk] = {
                            "engine": "gpt-sovits",
                            "voice": "GPT-SoVITS",
                            "style": selected_style,
                            "ref_audio_path": effective_ref_audio,
                            "prompt_text": prompt_input,
                            "speed": speed_slider,
                            "temperature": temp_slider,
                            "top_k": 15,
                            "top_p": 1.0,
                            "sample_steps": sample_steps
                        }

                        # 목소리 미리듣기 버튼 (GPT-SoVITS)
                        if st.button(f"🔊 {spk} GPT-SoVITS 미리듣기", key=f"preview_btn_{spk}", use_container_width=True, disabled=generation_active):
                            sovits_url = st.session_state.get("gpt_sovits_url", "")
                            if not effective_ref_audio:
                                st.error("⚠️ 먼저 위에서 참조 오디오(.wav 또는 .mp3) 파일을 업로드해주세요. (경로 입력은 전혀 필요 없습니다!)")
                            elif not os.path.exists(effective_ref_audio):
                                st.error(f"⚠️ 참조 오디오 파일을 찾을 수 없습니다: `{effective_ref_audio}`")
                            elif os.path.isdir(effective_ref_audio):
                                st.error(f"⚠️ `{effective_ref_audio}`는 파일이 아니라 폴더입니다! 폴더 안의 오디오 파일을 선택해주세요.")
                            elif not prompt_input.strip():
                                st.error("참조 오디오 실제 대사를 입력해주세요.")
                            else:
                                safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                                preview_file = os.path.join(work_dir, f"preview_sovits_{safe_spk}.wav")
                                sample_text = preview_text(spk, st.session_state["parsed_segments"])

                                cfg = VoiceConfig(
                                    engine="gpt-sovits",
                                    voice="GPT-SoVITS",
                                    style=selected_style,
                                    gpt_sovits_url=sovits_url,
                                    ref_audio_path=effective_ref_audio,
                                    prompt_text=prompt_input,
                                    speed_factor=speed_slider,
                                    temperature=temp_slider,
                                    top_k=15,
                                    top_p=1.0,
                                    sample_steps=sample_steps
                                )
                                with st.spinner(f"'{spk}' GPT-SoVITS 목소리 복제 생성 중 (서버 처리 중)..."):
                                    try:
                                        TTSEngine.generate_preview(
                                            voice_config=cfg,
                                            output_file=preview_file,
                                            sample_text=sample_text
                                        )
                                        if os.path.exists(preview_file):
                                            with open(preview_file, "rb") as af:
                                                st.audio(af.read(), format="audio/wav")
                                            st.caption(f'💬 샘플: "{sample_text}" [GPT-SoVITS 복제]')
                                    except Exception as e:
                                        st.error(f"음성 생성 실패: {str(e)}")

                    # 4. Edge-TTS 설정 폼
                    else:
                        default_v = current_cfg.get("voice", "ko-KR-SunHiNeural")
                        try:
                            def_idx = EDGE_VOICE_KEYS.index(default_v)
                        except ValueError:
                            def_idx = 0

                        selected_voice = st.selectbox(
                            "음성 선택 (무료)",
                            options=EDGE_VOICE_KEYS,
                            index=def_idx,
                            format_func=get_edge_voice_label,
                            key=f"edge_voice_{spk}"
                        )

                        selected_style = render_style_selector("🎨 음성 스타일 (감정/연령/톤)", spk, cur_style)
                        st.caption(f"✨ {style_description(selected_style, VOICE_STYLES)}")
                        st.caption(style_note(spk_engine, selected_style, VOICE_STYLES))

                        rate_val = st.slider(
                            "말하기 속도 미세조절 (%)",
                            min_value=-50,
                            max_value=50,
                            value=int(current_cfg.get("rate", 0)),
                            step=5,
                            key=f"rate_slider_{spk}"
                        )

                        pitch_val = st.slider(
                            "음조/톤 미세조절 (Hz)",
                            min_value=-50,
                            max_value=50,
                            value=int(current_cfg.get("pitch", 0)),
                            step=5,
                            key=f"pitch_slider_{spk}"
                        )

                        st.session_state["voice_settings"][spk] = {
                            "engine": "edge-tts",
                            "voice": selected_voice,
                            "style": selected_style,
                            "rate": rate_val,
                            "pitch": pitch_val
                        }

                        # 목소리 미리듣기 버튼 (Edge-TTS)
                        if st.button(f"🔊 {spk} 미리듣기 (무료)", key=f"preview_btn_{spk}", use_container_width=True, disabled=generation_active):
                            rate_str = f"{rate_val:+d}%"
                            pitch_str = f"{pitch_val:+d}Hz"
                            safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                            preview_file = os.path.join(work_dir, f"preview_edge_{safe_spk}.mp3")
                            sample_text = preview_text(spk, st.session_state["parsed_segments"])
                            
                            cfg = VoiceConfig(
                                engine="edge-tts",
                                voice=selected_voice,
                                style=selected_style,
                                rate=rate_str,
                                pitch=pitch_str
                            )
                            with st.spinner(f"'{spk}' ({selected_style}) 무료 샘플 생성 중..."):
                                try:
                                    TTSEngine.generate_preview(
                                        voice_config=cfg,
                                        output_file=preview_file,
                                        sample_text=sample_text
                                    )
                                    if os.path.exists(preview_file):
                                        st.audio(preview_file, format="audio/mp3")
                                        st.caption(f'💬 샘플: "{sample_text}" [{selected_style}]')
                                except Exception as e:
                                    st.error(f"음성 생성 실패: {str(e)}")

        render_emotions(st.session_state["parsed_segments"], st.session_state["voice_settings"],
                        work_dir, busy=generation_active, gemini_key=gemini_api_key, gemini_model=gemini_model)

        # Step 3: 전체 생성 옵션
        st.divider()
        st.subheader("3️⃣ TTS 오디오 및 자막 생성")
        if not use_kaggle_gpu():
            st.caption("CosyVoice 3는 버전 2와 별도 서버·대기열로 생성합니다. 목소리 20종은 처음 사용할 때 참고 녹음만 내려받아 재사용하며, 실제 음성은 생성 버튼을 눌렀을 때 만듭니다.")
            st.caption("새 기본 목소리 20종은 선택한 목소리를 처음 한 번 준비합니다. 전체 생성에서는 사용할 목소리를 먼저 한꺼번에 준비한 뒤 대사를 연속 생성하고, 번호순으로 MP3 하나로 합칩니다.")
            st.caption("Chirp는 코랩 없이 최대 4개를 연속 생성합니다. 한 대사가 끝나면 다음 대사를 바로 요청하고, 전부 완료되면 번호순으로 MP3 하나로 합칩니다.")
            st.caption("CosyVoice 2 코랩 v2.9.18: 문장 경계를 우선해 생성하고, 길이 제한 종료·비정상 반복을 검사합니다. 요청은 최대 4개로 연속 처리하되 공유 음향·파형 계산은 차례로 진행합니다. Gemini 작업은 별도로 진행되며 완료 음성은 번호순으로 MP3 하나로 합칩니다.")
        st.caption("이미 만들어진 파일은 그대로 보관합니다. 기존 음성의 문제 대사를 다시 만들려면 아래에서 해당 구간을 선택하고 ‘완료 파일도 새로 만들기’를 켜세요. 기존 MP3 자체가 자동 복구되는 것은 아닙니다.")
        if not use_kaggle_gpu():
            st.caption("다음 Gemini 생성부터 요청 시작 간격은 최소 7초입니다. 등록한 모든 키와 재시도를 합쳐 평균 분당 약 8.6회 이내로 조절하며, 완료된 음성은 계속 재사용합니다.")
        
        total_segs = len(st.session_state["parsed_segments"])
        
        col_opt1, col_opt2 = st.columns([1.5, 3])
        with col_opt1:
            gen_mode = st.radio("생성 범위 선택", ["전체 대사 생성", "구간 테스트 생성 (일부만)"], horizontal=True)
            if st.session_state.pop("_reset_bulk_overwrite", False):
                st.session_state["bulk_force_overwrite"] = False
            force_overwrite = st.checkbox("완료 파일도 새로 만들기", value=False,
                                          key="bulk_force_overwrite", disabled=generation_active,
                                          help="끄면 동일한 대사와 음성 설정으로 완료된 파일을 재사용합니다. 설정을 바꾼 대사는 자동으로 새로 생성됩니다.")
            if st.button("🧹 이전 음성 캐시 완전히 비우기", disabled=generation_active, help="이전에 생성된 오디오 파일을 모두 삭제하여 100% 새 설정으로 깨끗하게 다시 생성합니다."):
                clear_job(work_dir)
                clear_kaggle_job(work_dir)
                shutil.rmtree(os.path.join(work_dir, "results"), ignore_errors=True)
                for result_key in ("_partial_download", "_generation_result_job"):
                    st.session_state.pop(result_key, None)
                seg_d = os.path.join(work_dir, "segments_all")
                ref_c = os.path.join(work_dir, "ref_cache")
                ref_c2 = os.path.join(seg_d, "ref_cache")
                for d in [seg_d, ref_c, ref_c2]:
                    if os.path.exists(d):
                        for fn in os.listdir(d):
                            fp = os.path.join(d, fn)
                            if os.path.isfile(fp):
                                try: os.remove(fp)
                                except Exception: pass
                for single_f in ["full_audio.mp3", "subtitles.srt", "subtitles.vtt", "tts_main_bundle.zip", "tts_segments_bundle.zip"]:
                    p = os.path.join(work_dir, single_f)
                    if os.path.exists(p):
                        try: os.remove(p)
                        except Exception: pass
                st.session_state["generation_result"] = None
                st.toast("✅ 이전 음성 캐시를 모두 비웠습니다! 이제 새로 생성하시면 100% 최신 음성으로 생성됩니다.")
                st.rerun()
        
        with col_opt2:
            if gen_mode == "구간 테스트 생성 (일부만)":
                seg_range = ((1, 1) if total_segs == 1 else st.slider(
                    "생성할 대사 구간 (시작 ~ 끝 번호)", min_value=1, max_value=total_segs,
                    value=(1, min(10, total_segs)), disabled=generation_active))
                st.caption(f"💡 선택한 {seg_range[0]}번부터 {seg_range[1]}번까지 총 {seg_range[1] - seg_range[0] + 1}개 대사를 생성합니다. 시간은 대사 길이와 엔진에 따라 달라집니다.")
            else:
                seg_range = (1, total_segs)
                st.info(f"총 **{total_segs}개** 대사 · 완료된 파일은 재사용하고 남은 대사를 생성합니다."
                        if not force_overwrite else f"총 **{total_segs}개** 대사를 모두 새로 만듭니다.")

        btn_label = f"🚀 TTS 및 자막 생성 시작 ({seg_range[0]}번 ~ {seg_range[1]}번, 총 {seg_range[1] - seg_range[0] + 1}개)"
        previous_job = get_job(work_dir)
        recovery_request = st.session_state.pop("_gemini_recovery_request", None)
        if recovery_request and (
                use_kaggle_gpu() or generation_active or not previous_job
                or previous_job.get("id") != recovery_request.get("job_id")
                or gemini_model != recovery_request.get("model")
                or not 1 <= recovery_request.get("first", 0) <= recovery_request.get("last", 0) <= total_segs):
            recovery_request = None
            st.warning("작업 또는 대본이 바뀌어 자동 시작하지 않았습니다. 생성 범위를 확인하고 다시 눌러주세요.")
        if not use_kaggle_gpu():
            render_gemini_recovery(work_dir, previous_job, generation_active)
        try:
            keys_for_start = parse_gemini_keys(gemini_api_key)
            key_input_error = ""
        except GeminiKeyInputError as exc:
            keys_for_start, key_input_error = [], str(exc)
        selected_segments = st.session_state["parsed_segments"][seg_range[0] - 1 : seg_range[1]]
        use_kaggle = render_kaggle_export(selected_segments, st.session_state["voice_settings"],
            pause_ms, include_spk_in_sub, force_overwrite, generation_active, work_dir)
        cosy3_segments = [seg for seg in selected_segments
                          if st.session_state["voice_settings"].get(seg.speaker, {}).get("engine") == "cosyvoice3"]
        if not use_kaggle and cosy3_segments and len(cosy3_segments) == len(selected_segments):
            try:
                st.download_button("⬇️ CosyVoice 3 코랩 대본 받기",
                    export_cosy3_plan(selected_segments, st.session_state["voice_settings"], pause_ms),
                    file_name="CosyVoice3_대본.zip", mime="application/zip", key="cosy3_direct_plan")
            except ValueError as exc:
                st.caption(str(exc))
        bank_segments = [seg for seg in selected_segments
                         if st.session_state["voice_settings"].get(seg.speaker, {}).get("engine") == "qwen-bank"]
        if (not use_kaggle and bank_segments and len(bank_segments) == len(selected_segments)
                and not any(segment_cue(seg)[0] for seg in selected_segments)):
            try:
                bank_plan = export_qwen_bank_plan(selected_segments, st.session_state["voice_settings"], pause_ms, include_spk_in_sub)
                st.download_button("⬇️ 기본 목소리 20종 코랩 대본 받기", bank_plan,
                    file_name="Qwen_기본목소리20종_대본.json", mime="application/json",
                    help="새 기본 목소리 전용 코랩의 3번 셀에 올리면 녹음 파일 없이 직접 생성합니다.")
            except ValueError as exc:
                st.warning(str(exc))
        has_gemini = any(st.session_state["voice_settings"].get(seg.speaker, {}).get("engine") == "gemini"
                        for seg in selected_segments)
        chirp_characters = sum(len(clean_spoken_text(seg.text)) for seg in selected_segments
                               if st.session_state["voice_settings"].get(seg.speaker, {}).get("engine") == "chirp")
        if not use_kaggle and chirp_characters:
            st.caption(f"선택 범위의 Chirp 대사: {chirp_characters:,}자 · 저장된 음성 재사용분은 다시 요청하지 않습니다.")
            st.caption("월 100만 자 무료 한도는 Google Cloud 사용량 기준입니다. 다른 작업·미리듣기·재생성도 합산되며 초과분은 과금됩니다.")
        if not use_kaggle and has_gemini:
            st.caption(f"다음 생성에 사용할 Gemini 키: 서로 다른 {len(keys_for_start)}개 · 모델: {gemini_model}")
            if key_input_error:
                st.error(key_input_error)
        if previous_job and previous_job.get("status") in ("failed", "paused", "interrupted") and not force_overwrite:
            btn_label = f"▶ 남은 대사 이어서 생성 ({seg_range[0]}번 ~ {seg_range[1]}번)"
        if generation_active:
            st.caption("생성 중인 작업은 시작할 때의 대본과 음성 설정을 사용합니다. 진행 상황은 아래에서 확인하세요.")
        start_clicked = False
        if not use_kaggle:
            start_clicked = st.button(btn_label, key="bulk_generation_start", type="primary", use_container_width=True,
                                      disabled=generation_active)
        if not use_kaggle and (start_clicked or recovery_request):
            if recovery_request:
                seg_range = (recovery_request["first"], recovery_request["last"])
                force_overwrite = False
            all_segments = st.session_state["parsed_segments"]
            target_segments = all_segments[seg_range[0] - 1 : seg_range[1]]

            segments_dir = os.path.join(work_dir, "segments_all")
            items = []
            for seg in target_segments:
                safe_spk = "".join(c for c in seg.speaker if c.isalnum() or c in (' ', '_', '-')).strip()
                spk_cfg_data = st.session_state["voice_settings"].get(seg.speaker, {})
                seg_engine = spk_cfg_data.get("engine", "supertonic")
                seg_style = spk_cfg_data.get("style", "🎤 기본")
                emotion, emotion_intensity = segment_cue(seg)
                try:
                    require_support(seg_engine, emotion)
                except ValueError as exc:
                    st.error(f"{seg.index}번 · {seg.speaker}: {exc}")
                    return
                
                clean_text_to_speak = clean_spoken_text(seg.text)
                engine_prefix = seg_engine.replace("-", "")

                # 화자 설정(엔진, 보이스, 스타일, 참조 오디오, 배속, 텍스트)의 고유 해시 생성
                # 설정을 조금이라도 변경하면 이전 캐시를 재탕하지 않고 자동으로 새로 생성하도록 보장
                import hashlib
                generation_revision = {"cosyvoice": "v7_cosy_generation",
                                       "cosyvoice3": COSY3_BANK_REVISION,
                                       "gemini": "v8_gemini_voice_identity",
                                       "chirp": "v1_chirp_native_wav",
                                       "qwen-bank": "v1_qwen_designed_cast_base_17b"}.get(seg_engine, "v6_style_directions")
                cfg_unique_str = f"{clean_text_to_speak}_{seg_engine}_{sorted(spk_cfg_data.items())}_{generation_revision}"
                if emotion:
                    cfg_unique_str += f"_emotion_v1_{emotion}_{emotion_intensity}"
                cfg_hash = hashlib.md5(cfg_unique_str.encode('utf-8', errors='ignore')).hexdigest()[:8]
                filename = f"{seg.index:04d}_{engine_prefix}_{safe_spk}_{cfg_hash}.mp3"
                seg_file_path = os.path.join(segments_dir, filename)

                if seg_engine == "supertonic":
                    v_name = spk_cfg_data.get("voice", "F1")
                    cfg = VoiceConfig(
                        engine="supertonic",
                        voice=v_name,
                        style=seg_style
                    )
                elif seg_engine == "cosyvoice3":
                    cfg = VoiceConfig(engine="cosyvoice3", voice=spk_cfg_data.get("voice", "F01"),
                        style=seg_style, speed_factor=float(spk_cfg_data.get("speed", 1.0)),
                        cosyvoice3_url=st.session_state.get("cosyvoice3_url", ""),
                        ref_audio_path=spk_cfg_data.get("ref_audio_path", ""),
                        prompt_text=spk_cfg_data.get("prompt_text", ""))
                elif seg_engine == "cosyvoice":
                    actual_spk_ref = spk_cfg_data.get("ref_audio_path") or st.session_state.get(f"cosy_saved_path_{seg.speaker}", "")
                    actual_spk_prompt = spk_cfg_data.get("prompt_text") or st.session_state.get(f"cosy_prompt_{seg.speaker}", "")
                    cfg = VoiceConfig(
                        engine="cosyvoice",
                        voice="CosyVoice",
                        style=seg_style,
                        cosyvoice_url=st.session_state.get("cosyvoice_url", ""),
                        ref_audio_path=actual_spk_ref,
                        prompt_text=actual_spk_prompt,
                        speed_factor=float(spk_cfg_data.get("speed", 1.0))
                    )
                elif seg_engine == "gemini":
                    v_name = spk_cfg_data.get("voice", "Kore")
                    safe_gemini_model = gemini_model if gemini_model in gemini_model_options else "gemini-3.1-flash-tts-preview"
                    cfg = VoiceConfig(
                        engine="gemini",
                        voice=v_name,
                        style=seg_style,
                        model=safe_gemini_model,
                        api_key=gemini_api_key
                    )
                elif seg_engine == "qwen-bank":
                    cfg = VoiceConfig(engine="qwen-bank", voice=spk_cfg_data.get("voice", "F01"),
                                      style="🎤 기본", qwen_url=st.session_state.get("qwen_bank_url", ""))
                elif seg_engine == "qwen":
                    st.error("이전 Qwen 설정이 남아 있습니다. 기본 목소리 20종으로 배정해주세요.")
                    return
                elif seg_engine == "chirp":
                    cfg = VoiceConfig(engine="chirp", voice=spk_cfg_data.get("voice", "Kore"),
                                      speed=float(spk_cfg_data.get("speed", 1.0)),
                                      cloud_tts_api_key=chirp_api_key)
                elif seg_engine == "gpt-sovits":
                    actual_spk_ref = spk_cfg_data.get("ref_audio_path") or st.session_state.get(f"sovits_saved_path_{seg.speaker}", "")
                    actual_spk_prompt = spk_cfg_data.get("prompt_text") or st.session_state.get(f"sovits_prompt_{seg.speaker}", "")
                    cfg = VoiceConfig(
                        engine="gpt-sovits",
                        voice="GPT-SoVITS",
                        style=seg_style,
                        gpt_sovits_url=st.session_state.get("gpt_sovits_url", ""),
                        ref_audio_path=actual_spk_ref,
                        prompt_text=actual_spk_prompt,
                        speed_factor=float(spk_cfg_data.get("speed", 1.0)),
                        temperature=float(spk_cfg_data.get("temperature", 1.0)),
                        top_k=int(spk_cfg_data.get("top_k", 15)),
                        top_p=float(spk_cfg_data.get("top_p", 1.0)),
                        sample_steps=int(spk_cfg_data.get("sample_steps", 32))
                    )
                else:
                    v_name = spk_cfg_data.get("voice", "ko-KR-SunHiNeural")
                    r_val = spk_cfg_data.get("rate", 0)
                    p_val = spk_cfg_data.get("pitch", 0)
                    cfg = VoiceConfig(
                        engine="edge-tts",
                        voice=v_name,
                        style=seg_style,
                        rate=f"{r_val:+d}%",
                        pitch=f"{p_val:+d}Hz"
                    )

                cfg.emotion, cfg.emotion_intensity = emotion, emotion_intensity
                items.append(GenerationItem(seg.index, seg.speaker, clean_text_to_speak, seg_file_path, cfg))
            pending_items = items if force_overwrite else [item for item in items if not cached_audio_path(item)]
            checked_engines = set()
            for item in pending_items:
                cfg = item.config
                if cfg.engine == "cosyvoice3":
                    if cfg.voice == "custom" and (not cfg.prompt_text.strip() or not os.path.isfile(cfg.ref_audio_path)):
                        st.error(f"화자 '{item.speaker}'의 한국어 참고 음성과 실제 대사를 등록해주세요.")
                        return
                    if "cosyvoice3" not in checked_engines:
                        ok, message = cosy3_status(cfg.cosyvoice3_url)
                        if not ok:
                            st.error(message)
                            return
                        checked_engines.add("cosyvoice3")
                if cfg.engine == "qwen-bank" and "qwen-bank" not in checked_engines:
                    ok, message = qwen_bank_status(cfg.qwen_url)
                    if not ok:
                        st.error(message)
                        return
                    checked_engines.add("qwen-bank")
                if cfg.engine == "gemini" and not keys_for_start:
                    st.error(key_input_error or "Gemini 화자가 있으나 현재 세션에 등록된 Gemini API 키가 없습니다.")
                    return
                if cfg.engine == "chirp":
                    try:
                        validate_chirp_key(cfg.cloud_tts_api_key)
                    except ValueError as exc:
                        st.error(str(exc))
                        return
                if cfg.engine in ("cosyvoice", "gpt-sovits"):
                    if not cfg.prompt_text.strip():
                        st.error(f"화자 '{item.speaker}'의 참조 음성에서 실제로 말한 대사를 입력해주세요.")
                        return
                    if not os.path.isfile(cfg.ref_audio_path):
                        st.error(f"화자 '{item.speaker}'의 참조 음성 파일을 등록해주세요.")
                        return
                    if cfg.engine not in checked_engines:
                        if cfg.engine == "cosyvoice":
                            ok, message = TTSEngine.test_cosyvoice_connection(cfg.cosyvoice_url)
                        else:
                            ok, message = TTSEngine.test_gpt_sovits_connection(cfg.gpt_sovits_url)
                        if not ok:
                            st.error(f"코랩 연결을 확인해주세요: {message}")
                            return
                        checked_engines.add(cfg.engine)

            try:
                start_job(work_dir, items, force_overwrite=force_overwrite,
                          pause_ms=pause_ms, include_speaker=include_spk_in_sub)
            except Exception as exc:
                st.error(f"작업을 시작하지 못했습니다: {exc}")
            else:
                st.session_state["generation_result"] = None
                st.session_state["_reset_bulk_overwrite"] = True
                st.session_state.pop("_partial_download", None)
                st.session_state.pop("result_clip_index", None)
                st.session_state.pop("play_full_result", None)
                st.rerun()

        if not use_kaggle:
            render_generation_status(work_dir, is_running(work_dir), pause_ms=pause_ms)

    render_kaggle_status(work_dir)

    # Step 4: 결과 화면 및 다운로드
    if st.session_state.get("generation_result"):
        res = st.session_state["generation_result"]
        st.divider()
        st.success("✨ 생성한 대사를 순서대로 합친 MP3 한 파일이 준비되었습니다.")

        col_main, col_down = st.columns([3, 2])
        with col_main:
            st.markdown("### 🎧 전체 병합 오디오 재생")
            if os.path.exists(res["full_audio"]) and st.checkbox("전체 오디오 들어보기", key="play_full_result"):
                st.audio(res["full_audio"], format="audio/mp3")

        with col_down:
            st.markdown("### 📥 내 결과 다운로드")
            path = res.get("full_audio", "")
            saved_file_download("⬇️ 전체 대사 MP3 한 파일 받기", path,
                                file_name="full_audio.mp3", mime="audio/mpeg", primary=True,
                                key="result_download_full_audio")
            st.caption("생성한 구간의 나레이션과 인물 대사를 대본 순서대로 합친 MP3 1개입니다.")
            st.caption("버튼이 반응하지 않으면 왼쪽 재생바의 ⋮ → 다운로드로도 저장할 수 있습니다.")
            downloads = [
                ("📦 완성본 패키지 (오디오 + 자막)", "main_zip", "tts_main_bundle.zip", "application/zip"),
                ("📝 자막 파일 (SRT)", "srt", "subtitles.srt", "text/plain"),
                ("📝 자막 파일 (VTT)", "vtt", "subtitles.vtt", "text/vtt"),
            ]
            with st.expander("자막 및 오디오+자막 묶음 받기", expanded=False):
                for label, result_key, filename, mime in downloads:
                    path = res.get(result_key, "")
                    if path and os.path.isfile(path):
                        saved_file_download(label, path, file_name=filename,
                                            mime=mime, key="result_download_" + result_key,
                                            prepare_large=result_key == "main_zip")
            st.caption("현재 접속에서 생성한 파일입니다. 창을 닫거나 작업을 지우기 전에 다운로드해주세요.")

        # 개별 대사별 타임라인 및 재생 목록
        with st.expander("🔍 세부 대사별 타임라인 및 개별 음성 확인", expanded=False):
            # Load just one selected clip, not hundreds of audio widgets at once.
            if res["timings"]:
                clip_index = st.selectbox(
                    "확인할 대사", range(len(res["timings"])),
                    format_func=lambda i: f"{res['timings'][i].segment_index}번 · {res['timings'][i].speaker}",
                    key="result_clip_index")
                t = res["timings"][clip_index]
                st.caption(f"{t.start_ms / 1000.0:.2f}초 ~ {t.end_ms / 1000.0:.2f}초")
                st.write(t.text)
                if os.path.isfile(t.file_path):
                    st.audio(t.file_path, format="audio/wav" if t.file_path.lower().endswith(".wav") else "audio/mp3")

if __name__ == "__main__":
    main()
