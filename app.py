from __future__ import annotations
import io
import os
import shutil
import subprocess
import zipfile
from typing import List, Optional, Dict, Any, Tuple, Set
import streamlit as st
import streamlit.components.v1 as components

import importlib
import core.tts_engine
try:
    importlib.reload(core.tts_engine)
except Exception:
    pass
from core.tts_engine import (
    TTSEngine,
    VoiceConfig,
    clean_spoken_text,
    SUPERTONIC_VOICES,
    GEMINI_VOICES,
    KOREAN_EDGE_VOICES,
    VOICE_STYLES
)
from core.audio_processor import AudioProcessor
from core.subtitle import SubtitleGenerator
from core.parser import ScriptParser, ScriptSegment
import core.story_precise_parser
try:
    importlib.reload(core.story_precise_parser)
except Exception:
    pass
from core.story_precise_parser import parse_story_precisely, parse_story_with_gemini, is_already_formatted_script


APP_VERSION = "v2.6 (Gemini 3.1/3.8 지원 패치 완료)"

st.set_page_config(
    page_title=f"화자별 자동 TTS 생성기 (Supertonic 3 · Gemini Flash · Edge-TTS) - {APP_VERSION}",
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
    "송 노인": {"voice": "Gacrux", "style": "👴 시니어 중후한 (60대)", "role_desc": "연륜과 깊이가 있는 염색장 어르신"},
    "말숙": {"voice": "Zephyr", "style": "😊 밝고 활기차게", "role_desc": "화사하고 개성 있는 여인"},
    "봉 행수": {"voice": "Zubenelgenubi", "style": "🏛️ 40대 안정적인", "role_desc": "산전수전 겪은 노련한 행수"},
    "이방 오익환": {"voice": "Sadaltager", "style": "🎩 50대 깊이 있는", "role_desc": "무게감 있고 영악한 관아 이방"},
    "현감": {"voice": "Charon", "style": "😠 분노/격양", "role_desc": "위엄과 권위를 드러내는 고을 수령"},
    "봉진우": {"voice": "Algieba", "style": "🤵 30대 신뢰감 있는", "role_desc": "세련되고 신중한 양반 인물"},
    "마을 사람": {"voice": "Achird", "style": "🌾 40대 구수한", "role_desc": "친근하고 서글서글한 동네 주민"},
    "성복": {"voice": "Fenrir", "style": "🎭 진지하게", "role_desc": "힘 있고 당찬 청년 주인공"},
    "계순": {"voice": "Aoede", "style": "💌 부드럽고 감성적", "role_desc": "맑고 생기있는 아내"},
    "경헌": {"voice": "Charon", "style": "🏛️ 40대 안정적인", "role_desc": "중후하고 깊은 저음 관리"},
    "사또": {"voice": "Orus", "style": "😠 분노/격양", "role_desc": "위엄 넘치는 관아 사또"},
    "잉손": {"voice": "Puck", "style": "🧙 시니어 지혜로운 (70~80대)", "role_desc": "위트 있고 구수한 이야기꾼 노인"},
    "남 포졸": {"voice": "Alnilam", "style": "💪 힘차게", "role_desc": "단정하고 곧은 기개의 포졸"},
    "노모": {"voice": "Sulafat", "style": "👵 시니어 따뜻한 (70대)", "role_desc": "자애롭고 포근한 노모"},
    "장인": {"voice": "Charon", "style": "🎩 50대 깊이 있는", "role_desc": "무뚝뚝한 장인 어른"},
    "훈장": {"voice": "Sadaltager", "style": "🎓 강의/교육", "role_desc": "점잖은 마을 훈장"},
    "박씨 노인": {"voice": "Gacrux", "style": "👴 시니어 중후한 (60대)", "role_desc": "동네 토박이 노인"},
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
            if spk in GEMINI_CHARACTER_PRESETS:
                p = GEMINI_CHARACTER_PRESETS[spk]
                new_settings[spk] = {"engine": "gemini", "voice": p["voice"], "style": p.get("style", def_style), "speed": 1.0}
            elif "나레이션" in spk or "해설" in spk:
                new_settings[spk] = {"engine": "gemini", "voice": "Kore", "style": "📖 동화 나레이션", "speed": 1.0}
            else:
                v_idx = idx % len(GEMINI_VOICE_KEYS)
                new_settings[spk] = {"engine": "gemini", "voice": GEMINI_VOICE_KEYS[v_idx], "style": def_style, "speed": 1.0}
        elif engine_type == "gpt-sovits":
            new_settings[spk] = {
                "engine": "gpt-sovits",
                "voice": "GPT-SoVITS 목소리 복제",
                "style": def_style,
                "ref_audio_path": "",
                "prompt_text": "",
                "speed": 0.95,
                "temperature": 0.65,
                "top_k": 5,
                "top_p": 0.85
            }
        else:
            if spk in EDGE_CHARACTER_PRESETS:
                p = EDGE_CHARACTER_PRESETS[spk]
                new_settings[spk] = {"engine": "edge-tts", "voice": p["voice"], "style": p.get("style", def_style), "rate": p["rate"], "pitch": p["pitch"]}
            elif "나레이션" in spk or "해설" in spk:
                new_settings[spk] = {"engine": "edge-tts", "voice": "ko-KR-SunHiNeural", "style": "📖 동화 나레이션", "rate": 0, "pitch": 0}
            else:
                v_idx = idx % len(EDGE_VOICE_KEYS)
                new_settings[spk] = {"engine": "edge-tts", "voice": EDGE_VOICE_KEYS[v_idx], "style": def_style, "rate": 0, "pitch": 0}
    return new_settings

def set_state_safe(key: str, value: Any) -> None:
    """StreamlitWidgetAlreadyInstantiatedError 등 위젯 키 수정 예외 방어용 안전 세션 상태 저장기"""
    try:
        st.session_state[key] = value
    except Exception:
        pass

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
        elif eng == "gemini":
            set_state_safe(f"gemini_voice_{spk}", voice)
        elif eng == "edge-tts":
            set_state_safe(f"edge_voice_{spk}", voice)
        elif eng == "gpt-sovits":
            set_state_safe(f"sovits_speed_{spk}", sdata.get("speed", 0.95))
            set_state_safe(f"sovits_temp_{spk}", sdata.get("temperature", 0.65))
            if sdata.get("prompt_text"):
                set_state_safe(f"sovits_prompt_{spk}", sdata.get("prompt_text"))
            if sdata.get("ref_audio_path"):
                set_state_safe(f"sovits_saved_path_{spk}", sdata.get("ref_audio_path"))
        set_state_safe(f"style_select_{spk}", style)

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
            <b>초고속 로컬 무료 AI(Supertonic 3)</b> · <b>100% 무료 신경망(Edge-TTS)</b> · <b>목소리 복제(GPT-SoVITS)</b>로 
            생생한 멀티 보이스 낭독 오디오와 싱크 자막(SRT/VTT)을 원클릭으로 제작합니다.
        </p>
        <div class="tag-row">
            <span class="tag-chip tag-chip-super">👑 Supertonic 3 (로컬 완전 무료)</span>
            <span class="tag-chip tag-chip-gemini">⚡ Gemini 3.1 / 3.8 Flash 감정 연기</span>
            <span class="tag-chip tag-chip-edge">🌐 Edge-TTS 한국어 표준 성우</span>
            <span class="tag-chip tag-chip-sovits">🎙️ GPT-SoVITS 6초 즉석 복제</span>
            <span class="tag-chip tag-chip-sub">📝 싱크 정밀 자막(SRT/VTT)</span>
        </div>
        <div style="background: rgba(34, 197, 94, 0.15); border: 1px solid #22c55e; border-radius: 8px; padding: 8px 14px; margin: 12px auto 0 auto; max-width: 650px; color: #86efac; font-size: 13px; font-weight: 600; text-align: center;">
            🚀 시스템 패치 v2.6 적용 완료 (Gemini 3.8 / 3.1 Flash 전용 모델 탑재 및 자동 다중 폴백)
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 작업 디렉토리 설정
    work_dir = os.path.abspath("outputs/web_session")
    os.makedirs(work_dir, exist_ok=True)

    # 지연된 대본 텍스트가 있다면 위젯 생성 전에 안전하게 적용
    if "pending_script_text" in st.session_state:
        st.session_state["script_editor"] = st.session_state.pop("pending_script_text")

    # 세션 상태 기본값 초기화 (Streamlit Secrets / 환경변수 자동 연동)
    if "gemini_api_key" not in st.session_state:
        sec_key = ""
        try:
            sec_key = st.secrets.get("GEMINI_API_KEY", "") or st.secrets.get("GOOGLE_API_KEY", "")
        except Exception:
            pass
        st.session_state["gemini_api_key"] = sec_key or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")

    if "gpt_sovits_url" not in st.session_state:
        sec_url = ""
        try:
            sec_url = st.secrets.get("GPT_SOVITS_URL", "")
        except Exception:
            pass
        st.session_state["gpt_sovits_url"] = sec_url or "http://127.0.0.1:9880/tts"

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
        st.session_state["active_engine_mode"] = "supertonic"  # "supertonic", "gemini", "edge", "custom"
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
                return False, "Gemini AI 화자 분석을 위해 사이드바에 Gemini API Key를 입력해주세요."
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

        # 1. 엔진 모드 선택
        engine_mode_options = [
            "👑 Supertonic 3 (로컬) (무료)",
            "⚡ Gemini 3.1 Flash TTS (선불/버텍스)",
            "🌐 Edge-TTS (무료)",
            "🎙️ GPT-SoVITS (로컬 보이스 클로닝 API, 무료)",
            "🔀 하이브리드 (인물별 자유 선택)"
        ]
        curr_idx = 0
        if st.session_state["active_engine_mode"] == "gemini":
            curr_idx = 1
        elif st.session_state["active_engine_mode"] == "edge":
            curr_idx = 2
        elif st.session_state["active_engine_mode"] == "gpt-sovits":
            curr_idx = 3
        elif st.session_state["active_engine_mode"] == "custom":
            curr_idx = 4

        selected_mode_label = st.radio(
            "🎙️ TTS 기본 엔진 선택",
            options=engine_mode_options,
            index=curr_idx,
            help="Supertonic 3와 Edge-TTS, GPT-SoVITS는 완전 무료입니다."
        )

        new_mode = "supertonic"
        if "Gemini" in selected_mode_label:
            new_mode = "gemini"
        elif "Edge-TTS" in selected_mode_label:
            new_mode = "edge"
        elif "GPT-SoVITS" in selected_mode_label:
            new_mode = "gpt-sovits"
        elif "하이브리드" in selected_mode_label:
            new_mode = "custom"

        # 엔진 모드가 변경되었으면 화자 설정 일괄 업데이트 및 위젯 상태 동기화
        if new_mode != st.session_state["active_engine_mode"]:
            if new_mode != "custom":
                set_speakers_preset("edge-tts" if new_mode == "edge" else new_mode)
            else:
                st.session_state["active_engine_mode"] = "custom"
            st.rerun()

        # 2. Supertonic 옵션 안내
        if st.session_state["active_engine_mode"] == "supertonic":
            st.success("✅ Supertonic 3 로컬 엔진 활성화됨 (100% 완전 무료 · API 키 불필요)")
            st.caption("💡 하이브(HYBE) 수퍼톤의 가벼운 고속 ONNX 모델로, 내 컴퓨터에서 완전 무료로 동작합니다.")

        # 3. Gemini Flash TTS 및 AI 화자 분석 설정
        gemini_model_options = [
            "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-preview-tts",
            "gemini-2.5-pro-preview-tts"
        ]
        curr_model = st.session_state.get("gemini_model", "gemini-3.1-flash-tts-preview")
        if curr_model not in gemini_model_options:
            curr_model = "gemini-3.1-flash-tts-preview"
        model_idx = gemini_model_options.index(curr_model)
        gemini_api_key = st.session_state.get("gemini_api_key", "")
        gemini_model = curr_model

        show_gemini_prominent = st.session_state["active_engine_mode"] in ["gemini", "custom"]

        def render_gemini_section():
            st.markdown("#### ⚡ Gemini Flash API 설정")
            saved_key = st.session_state.get("gemini_api_key", "")
            g_key = st.text_input(
                "Gemini API Key (선불/무료키)",
                value=saved_key,
                type="password",
                placeholder="AIzaSy...",
                help="Google AI Studio 또는 Vertex AI API 키를 입력하세요.",
                key="input_gemini_api_key"
            )
            st.session_state["gemini_api_key"] = g_key

            def _format_model_name(m: str) -> str:
                labels = {
                    "gemini-3.1-flash-tts-preview": "⚡ Gemini 3.1 Flash TTS (추천 · 최신 고음질 · 무료/유료 공용)",
                    "gemini-2.5-flash-preview-tts": "🚀 Gemini 2.5 Flash TTS (안정형 고속 · 무료/유료 공용)",
                    "gemini-2.5-pro-preview-tts": "💎 Gemini 2.5 Pro TTS (유료 결제 계정 전용 · 무료 키는 한도 0)"
                }
                return labels.get(m, m)

            g_model = st.selectbox(
                "Gemini TTS 음성 합성 모델 선택",
                options=gemini_model_options,
                index=model_idx,
                format_func=_format_model_name,
                key="select_gemini_model"
            )
            st.session_state["gemini_model"] = g_model

            if g_model == "gemini-2.5-pro-preview-tts":
                st.warning("⚠️ **Gemini 2.5 Pro 안내**: 구글 정책상 Pro TTS는 Google Cloud 유료 결제(Billing)가 등록된 API 키에서만 사용 가능합니다. 무료 API 키를 쓰시는 경우 429(한도 0) 오류가 발생하므로 **'Gemini 3.1 Flash'**를 선택해주세요.")

            if not g_key and st.session_state["active_engine_mode"] == "gemini":
                st.warning("⚠️ Gemini API 키를 입력하세요. 무료로 쓰시려면 'Supertonic 3 (로컬 무료)'를 선택하세요.")
                st.markdown("[👉 Google AI Studio에서 무료 키 받기](https://aistudio.google.com/)")
            elif g_key:
                st.success("✅ Gemini API Key 준비 완료")
            return g_key, g_model

        if show_gemini_prominent:
            gemini_api_key, gemini_model = render_gemini_section()
        else:
            with st.expander("⚡ Gemini API 키 등록 / 화자 분석 연동", expanded=bool(st.session_state.get("gemini_api_key"))):
                gemini_api_key, gemini_model = render_gemini_section()

        if st.session_state["active_engine_mode"] == "edge":
            st.success("✅ Edge-TTS 무료 모드 활성화됨 (API 키 불필요)")

        # 4. GPT-SoVITS 로컬 API 전용 옵션
        if st.session_state["active_engine_mode"] in ["gpt-sovits", "custom"]:
            st.markdown("#### 🎙️ GPT-SoVITS API 설정")

            # 구글 코랩 원클릭 실행 배지 및 안내
            st.markdown(
                """
                <div style="background: rgba(66, 133, 244, 0.08); border: 1px solid rgba(66, 133, 244, 0.25); border-radius: 8px; padding: 10px; margin-bottom: 12px;">
                    <div style="font-weight: 600; font-size: 0.86rem; color: #4285F4; margin-bottom: 3px;">
                        ☁️ 내 컴퓨터 GPU가 없거나 24시간 쓰려면?
                    </div>
                    <div style="font-size: 0.80rem; color: #aaa; margin-bottom: 8px; line-height: 1.35;">
                        Google Colab 무료 T4 GPU에서 원클릭으로 켜고 나온 주소를 아래에 붙여넣으세요!
                    </div>
                    <a href="https://colab.research.google.com/github/ssss2513-cyber/ai-audio-studio/blob/main/GPT_SoVITS_Colab_API.ipynb" target="_blank">
                        <img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab">
                    </a>
                </div>
                """,
                unsafe_allow_html=True
            )

            saved_sovits = st.session_state.get("gpt_sovits_url", "http://127.0.0.1:9880/tts")
            sovits_url = st.text_input(
                "GPT-SoVITS API 주소",
                value=saved_sovits,
                help="로컬 또는 서버에서 실행 중인 GPT-SoVITS API 주소 (예: https://xxx.trycloudflare.com/tts 또는 http://127.0.0.1:9880/tts)",
                key="input_gpt_sovits_url"
            )
            st.session_state["gpt_sovits_url"] = sovits_url

            if st.button("🔌 GPT-SoVITS 서버 연결 테스트", use_container_width=True, key="btn_test_sovits"):
                ok, msg = TTSEngine.test_gpt_sovits_connection(sovits_url)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
                    st.info(
                        "💡 **서버 연결 방법**:\n"
                        "1. **구글 코랩(무료 GPU)**: 상단의 [Open In Colab]을 눌러 서버를 켜고 생성된 `https://...trycloudflare.com/tts` 주소를 붙여넣기\n"
                        "2. **내 컴퓨터(로컬 PC)**: 터미널에서 `python api_v2.py -a 127.0.0.1 -p 9880` 실행"
                    )

        st.divider()
        st.markdown("#### 🎚️ 재생 및 자막 설정")
        pause_sec = st.slider("대사 간 무음 간격 (초)", min_value=0.1, max_value=3.0, value=0.5, step=0.1)
        pause_ms = int(pause_sec * 1000)
        
        remove_stage = st.checkbox("지문/지시문(괄호 안 텍스트) 자동 제거", value=False)
        include_spk_in_sub = st.checkbox("자막에 화자 이름 표시", value=True)

    # 메인 상단 엔진 상태 안내 바
    st.info(
        f"💡 **현재 기본 엔진: {selected_mode_label}**\n"
        "- **Supertonic 3**: 하이브 수퍼톤 한국어 모델로 **완전 무료 + 인터넷/키 없이도 로컬에서 초고속 생성**\n"
        "- **Gemini Flash**: 구글 최신 Gemini 멀티모달 오디오 모델 기반의 **초고속 TTS**\n"
        "- **Edge-TTS**: 마이크로소프트의 **100% 평생 무료 표준 음성**\n"
        "- **GPT-SoVITS**: 단 몇 초의 샘플 음성으로 목소리를 복제하는 **오픈소스 AI 보이스 클로닝 API**"
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
                    st.error("⚠️ Gemini AI 분석을 위해 사이드바 '⚡ Gemini Flash API 설정'에 API Key를 먼저 입력해주세요!")
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
        
        # 일괄 변경 원클릭 버튼 바
        col_bar1, col_bar2, col_bar3, col_bar4 = st.columns(4)
        with col_bar1:
            if st.button("👑 전체 Supertonic 3(무료)", use_container_width=True):
                set_speakers_preset("supertonic")
                st.toast("모든 화자가 Supertonic 3 로컬 무료 모델로 일괄 변경되었습니다!")
                st.rerun()
        with col_bar2:
            if st.button("⚡ 전체 Gemini Flash", use_container_width=True):
                set_speakers_preset("gemini")
                st.toast("모든 화자가 Gemini Flash TTS로 일괄 변경되었습니다!")
                st.rerun()
        with col_bar3:
            if st.button("🌐 전체 Edge-TTS(무료)", use_container_width=True):
                set_speakers_preset("edge-tts")
                st.toast("모든 화자가 Edge-TTS 무료 음성으로 일괄 변경되었습니다!")
                st.rerun()
        with col_bar4:
            if st.button("🎙️ 전체 GPT-SoVITS(API)", use_container_width=True):
                set_speakers_preset("gpt-sovits")
                st.toast("모든 화자가 GPT-SoVITS 보이스 클로닝으로 일괄 변경되었습니다!")
                st.rerun()

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
                            updated_script_lines.append(f"{new_s}: {seg.text}")
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
            st.caption("💡 각 화자별 카드에서 원하는 스타일을 선택하면, 제미나이(Gemini)는 감정 연기 지시문이 적용되고, Supertonic 및 Edge-TTS는 속도/피치/음량이 자동 튜닝됩니다.")

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
                    engine_choices = ["supertonic", "gemini", "edge-tts", "gpt-sovits"]
                    chosen_engine = st.selectbox(
                        "음성 엔진 선택",
                        options=engine_choices,
                        index=engine_choices.index(spk_engine) if spk_engine in engine_choices else 0,
                        format_func=lambda e: "👑 Supertonic (로컬 무료)" if e == "supertonic" else ("⚡ Gemini Flash" if e == "gemini" else ("🌐 Edge-TTS (무료)" if e == "edge-tts" else "🎙️ GPT-SoVITS (API)")),
                        key=f"engine_select_{spk}"
                    )
                    
                    # 현재 스타일 가져오기
                    cur_style = current_cfg.get("style", DEFAULT_CHARACTER_STYLES.get(spk, "🎤 기본"))
                    try:
                        style_idx = VOICE_STYLE_KEYS.index(cur_style)
                    except ValueError:
                        style_idx = 0

                    if chosen_engine != spk_engine:
                        # 화자 엔진이 바뀌면 해당 엔진 기본 프리셋으로 갱신
                        if chosen_engine == "supertonic":
                            p = SUPERTONIC_CHARACTER_PRESETS.get(spk, {"voice": "F1", "style": cur_style})
                            current_cfg = {"engine": "supertonic", "voice": p["voice"], "style": p.get("style", cur_style), "speed": 1.0}
                            set_state_safe(f"supertonic_voice_{spk}", p["voice"])
                        elif chosen_engine == "gemini":
                            p = GEMINI_CHARACTER_PRESETS.get(spk, {"voice": "Kore", "style": cur_style})
                            current_cfg = {"engine": "gemini", "voice": p["voice"], "style": p.get("style", cur_style), "speed": 1.0}
                            set_state_safe(f"gemini_voice_{spk}", p["voice"])
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
                                "speed": 0.95,
                                "temperature": 0.65,
                                "top_k": 5,
                                "top_p": 0.85
                            }
                            set_state_safe(f"sovits_speed_{spk}", 0.95)
                            set_state_safe(f"sovits_temp_{spk}", 0.65)
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

                        selected_style = st.selectbox(
                            "🎨 음성 스타일 (감정/연령/톤)",
                            options=VOICE_STYLE_KEYS,
                            index=style_idx,
                            key=f"style_select_{spk}"
                        )
                        st.caption(f"✨ {VOICE_STYLES[selected_style]['desc']}")

                        st.session_state["voice_settings"][spk] = {
                            "engine": "supertonic",
                            "voice": selected_voice,
                            "style": selected_style,
                            "speed": 1.0
                        }

                        # 목소리 미리듣기 버튼 (Supertonic)
                        if st.button(f"🔊 {spk} Supertonic 미리듣기 (무료)", key=f"preview_btn_{spk}", use_container_width=True):
                            safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                            preview_file = os.path.join(work_dir, f"preview_super_{safe_spk}.mp3")
                            sample_text = CHARACTER_SAMPLE_LINES.get(spk, f"안녕하십니까. 저는 {spk} 역할을 맡은 목소리입니다.")
                            
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
                        try:
                            def_idx = GEMINI_VOICE_KEYS.index(default_v)
                        except ValueError:
                            def_idx = 0
                            
                        selected_voice = st.selectbox(
                            "보이스 선택",
                            options=GEMINI_VOICE_KEYS,
                            index=def_idx,
                            format_func=get_gemini_voice_label,
                            key=f"gemini_voice_{spk}"
                        )

                        selected_style = st.selectbox(
                            "🎨 음성 스타일 (감정 연기 지시)",
                            options=VOICE_STYLE_KEYS,
                            index=style_idx,
                            key=f"style_select_{spk}"
                        )
                        st.caption(f"✨ {VOICE_STYLES[selected_style]['desc']}")

                        st.session_state["voice_settings"][spk] = {
                            "engine": "gemini",
                            "voice": selected_voice,
                            "style": selected_style,
                            "speed": 1.0
                        }

                        # 목소리 미리듣기 버튼 (Gemini)
                        if st.button(f"🔊 {spk} Gemini Flash 미리듣기", key=f"preview_btn_{spk}", use_container_width=True):
                            if not gemini_api_key:
                                st.error("⚠️ 사이드바에 Gemini API Key를 먼저 입력해주세요! (무료로 쓰시려면 상단 'Supertonic'을 누르세요)")
                            else:
                                safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                                preview_file = os.path.join(work_dir, f"preview_gemini_{safe_spk}.mp3")
                                sample_text = CHARACTER_SAMPLE_LINES.get(spk, f"안녕하십니까. 저는 {spk} 역할을 맡은 목소리입니다.")
                                
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

                    # 3. GPT-SoVITS 설정 폼 (목소리 복제)
                    elif spk_engine == "gpt-sovits":
                        st.markdown("**🎙️ GPT-SoVITS 목소리 복제 (초고음질 · 싱크로율 최적화)**")
                        ref_audio_val = current_cfg.get("ref_audio_path", "")
                        prompt_text_val = current_cfg.get("prompt_text", "")
                        speed_val = current_cfg.get("speed", 0.95)
                        temp_val = current_cfg.get("temperature", 0.65)

                        # 나레이션 화자이고 기본 등록된 참고 파일이 있으면 자동 연결
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
                                    if not prompt_text_val:
                                        prompt_text_val = "혼례 이레 만에 신랑이 자취를 감추자 신부는 하루아침에 파혼을 강요받았습니다."

                        saved_ref_key = f"sovits_saved_path_{spk}"
                        if saved_ref_key not in st.session_state and ref_audio_val:
                            st.session_state[saved_ref_key] = ref_audio_val

                        # 1. 파일 직접 업로드 (경로 입력 일절 불필요!)
                        ref_file = st.file_uploader(
                            "🎙️ 참조 오디오 파일 (.wav, .mp3) 업로드",
                            type=["wav", "mp3"],
                            key=f"sovits_upload_{spk}",
                            help="복제할 인물의 3~10초 길이 목소리 음원 파일 (업로드 시 경로 입력은 일절 필요 없습니다!)"
                        )
                        
                        effective_ref_audio = ""
                        if ref_file is not None:
                            safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                            save_ref_dir = os.path.join(work_dir, "ref_audios")
                            os.makedirs(save_ref_dir, exist_ok=True)
                            uploaded_path = os.path.join(save_ref_dir, f"{safe_spk}_{ref_file.name}")
                            with open(uploaded_path, "wb") as f:
                                f.write(ref_file.getvalue())
                            effective_ref_audio = uploaded_path
                            st.session_state[saved_ref_key] = uploaded_path
                            st.success(f"✅ 오디오 파일 업로드 완료: **{ref_file.name}**")

                            # 업로드 즉시 Whisper로 대사 자동 분석 및 동기화
                            txt_cache = uploaded_path + ".txt"
                            auto_spoken = ""
                            if os.path.exists(txt_cache):
                                try:
                                    with open(txt_cache, "r", encoding="utf-8") as cf:
                                        auto_spoken = cf.read().strip()
                                except Exception:
                                    pass
                            if not auto_spoken:
                                with st.spinner("🎧 업로드된 음성을 AI(Whisper)가 듣고 실제 대사를 분석 중..."):
                                    auto_spoken = TTSEngine.transcribe_audio_whisper(uploaded_path)
                                    if auto_spoken:
                                        try:
                                            with open(txt_cache, "w", encoding="utf-8") as cf:
                                                cf.write(auto_spoken)
                                        except Exception:
                                            pass
                            if auto_spoken:
                                prompt_text_val = auto_spoken
                                set_state_safe(f"sovits_prompt_{spk}", auto_spoken)
                                st.info(f"🎙️ AI 자동 인식 대사: **\"{auto_spoken}\"**")
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
                                        st.caption(f"⏱️ 파일 길이: **{dur:.1f}초** (10초 초과 시 앞부분 4.5~8.5초 무음 밸리 자동 슬라이스)")
                                    elif dur < 3.0:
                                        st.warning(f"⚠️ 파일 길이가 **{dur:.1f}초**로 짧습니다. (권장: 3초~10초)")
                                    else:
                                        st.caption(f"⏱️ 파일 길이: **{dur:.1f}초** (✅ 최적 길이 3~10초 적합)")
                                except Exception:
                                    pass

                        # 3. 로컬 파일/폴더 경로 직접 입력 (선택사항, 업로드 안 했을 때만 필요한 대안)
                        with st.expander("📁 (선택사항) PC 로컬 파일 또는 폴더 경로로 직접 지정하기"):
                            st.caption("💡 위에서 파일을 직접 업로드하셨다면 이 칸은 비워두셔도 됩니다.")
                            ref_path_input = st.text_input(
                                "참조 오디오 파일 또는 폴더 경로",
                                value=effective_ref_audio if not ref_file and not st.session_state.get(saved_ref_key) else "",
                                placeholder="예: C:/path/to/reference_sample.wav 또는 폴더 경로",
                                key=f"sovits_manual_path_{spk}",
                                help="로컬 PC 내 참조 오디오 파일 경로 또는 파일이 들어있는 폴더 경로"
                            )
                            if ref_path_input.strip():
                                manual_target = ref_path_input.strip()
                                if os.path.exists(manual_target):
                                    if os.path.isdir(manual_target):
                                        st.warning("⚠️ 입력하신 경로는 **폴더**입니다. 아래 목록에서 사용할 오디오 파일을 선택해주세요.")
                                        try:
                                            audio_files = [
                                                f for f in os.listdir(manual_target)
                                                if f.lower().endswith(('.wav', '.mp3', '.flac', '.ogg', '.m4a'))
                                                and os.path.isfile(os.path.join(manual_target, f))
                                            ]
                                            if audio_files:
                                                audio_files.sort(key=lambda x: (not x.lower().endswith('.wav'), x.lower()))
                                                selected_f = st.selectbox(
                                                    "📁 폴더 내 오디오 파일 선택",
                                                    options=audio_files,
                                                    key=f"sovits_folder_file_{spk}"
                                                )
                                                effective_ref_audio = os.path.join(manual_target, selected_f)
                                                st.session_state[saved_ref_key] = effective_ref_audio
                                                st.success(f"선택된 파일: `{os.path.basename(effective_ref_audio)}`")
                                            else:
                                                st.error("❌ 해당 폴더 안에 지원되는 오디오 파일(.wav, .mp3)이 없습니다.")
                                        except Exception as dir_err:
                                            st.error(f"폴더 탐색 오류: {dir_err}")
                                    else:
                                        effective_ref_audio = manual_target
                                        st.session_state[saved_ref_key] = effective_ref_audio

                        # 4. 참조 오디오 실제 대사 추출 및 입력
                        col_lbl, col_wbtn = st.columns([2.6, 1.4])
                        with col_lbl:
                            st.markdown("**참조 오디오 실제 대사 (프롬프트 텍스트)**")
                        with col_wbtn:
                            extract_clicked = st.button(
                                "✨ 대사 자동 추출",
                                key=f"whisper_btn_{spk}",
                                help="등록된 오디오 파일을 AI(Whisper)가 듣고 실제 대사를 자동 입력합니다.",
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
                                set_state_safe(f"sovits_prompt_{spk}", known_real_txt)

                        prompt_input = st.text_input(
                            "참조 오디오 실제 대사",
                            value=prompt_text_val,
                            placeholder="예: 꿈을 이루고자 하는 용기가 있다면 모든 꿈은 실현 가능하다.",
                            key=f"sovits_prompt_{spk}",
                            label_visibility="collapsed",
                            help="소설 대사가 아니라, 등록하신 참조 오디오 안에서 실제로 나오는 말을 적어주셔야 합니다. (비워두셔도 생성 시 AI가 자동 인식합니다)"
                        )
                        if known_real_txt:
                            st.caption(f"🎧 **AI 감지 실제 대사**: `{known_real_txt}` (음색 완벽 동기화 보장)")
                        else:
                            st.caption("💡 **필독**: 이 칸에는 소설 대사가 아니라 **'음성 파일 안에서 실제로 말한 문장'**을 적어야 목소리가 똑같이 복제됩니다! (위의 '✨ 대사 자동 추출' 클릭 시 자동 완성)")

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
                                "🎯 원음 싱크로율 (Temperature)",
                                min_value=0.50,
                                max_value=0.90,
                                value=float(temp_val),
                                step=0.05,
                                key=f"sovits_temp_{spk}",
                                help="낮을수록(0.60~0.70) 원본 목소리 톤과 억양을 최대한 똑같이 재현하고 환각/잡음이 차단됩니다. (기본 권장값: 0.65)"
                            )

                        selected_style = st.selectbox(
                            "🎨 음성 스타일 (참고용)",
                            options=VOICE_STYLE_KEYS,
                            index=style_idx,
                            key=f"style_select_{spk}"
                        )

                        st.session_state["voice_settings"][spk] = {
                            "engine": "gpt-sovits",
                            "voice": "GPT-SoVITS",
                            "style": selected_style,
                            "ref_audio_path": effective_ref_audio,
                            "prompt_text": prompt_input,
                            "speed": speed_slider,
                            "temperature": temp_slider,
                            "top_k": 5,
                            "top_p": 0.85
                        }

                        # 목소리 미리듣기 버튼 (GPT-SoVITS)
                        if st.button(f"🔊 {spk} GPT-SoVITS 미리듣기", key=f"preview_btn_{spk}", use_container_width=True):
                            sovits_url = st.session_state.get("gpt_sovits_url", "http://127.0.0.1:9880/tts")
                            if not effective_ref_audio:
                                st.error("⚠️ 먼저 위에서 참조 오디오(.wav 또는 .mp3) 파일을 업로드해주세요. (경로 입력은 전혀 필요 없습니다!)")
                            elif not os.path.exists(effective_ref_audio):
                                st.error(f"⚠️ 참조 오디오 파일을 찾을 수 없습니다: `{effective_ref_audio}`")
                            elif os.path.isdir(effective_ref_audio):
                                st.error(f"⚠️ `{effective_ref_audio}`는 파일이 아니라 폴더입니다! 폴더 안의 오디오 파일을 선택해주세요.")
                            else:
                                safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                                preview_file = os.path.join(work_dir, f"preview_sovits_{safe_spk}.mp3")
                                sample_text = CHARACTER_SAMPLE_LINES.get(spk, f"안녕하십니까. 저는 {spk} 역할을 맡은 목소리입니다.")

                                cfg = VoiceConfig(
                                    engine="gpt-sovits",
                                    voice="GPT-SoVITS",
                                    style=selected_style,
                                    gpt_sovits_url=sovits_url,
                                    ref_audio_path=effective_ref_audio,
                                    prompt_text=prompt_input,
                                    speed_factor=speed_slider,
                                    temperature=temp_slider,
                                    top_k=5,
                                    top_p=0.85
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
                                                st.audio(af.read(), format="audio/mp3")
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

                        selected_style = st.selectbox(
                            "🎨 음성 스타일 (감정/연령/톤)",
                            options=VOICE_STYLE_KEYS,
                            index=style_idx,
                            key=f"style_select_{spk}"
                        )
                        st.caption(f"✨ {VOICE_STYLES[selected_style]['desc']}")

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
                        if st.button(f"🔊 {spk} 미리듣기 (무료)", key=f"preview_btn_{spk}", use_container_width=True):
                            rate_str = f"{rate_val:+d}%"
                            pitch_str = f"{pitch_val:+d}Hz"
                            safe_spk = "".join(c for c in spk if c.isalnum() or c in ('_', '-'))
                            preview_file = os.path.join(work_dir, f"preview_edge_{safe_spk}.mp3")
                            sample_text = CHARACTER_SAMPLE_LINES.get(spk, f"안녕하십니까. 저는 {spk} 역할을 맡은 목소리입니다.")
                            
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

        # Step 3: 전체 생성 옵션
        st.divider()
        st.subheader("3️⃣ TTS 오디오 및 자막 생성")
        
        total_segs = len(st.session_state["parsed_segments"])
        
        col_opt1, col_opt2 = st.columns([1.5, 3])
        with col_opt1:
            gen_mode = st.radio("생성 범위 선택", ["전체 대사 생성", "구간 테스트 생성 (일부만)"], horizontal=True)
            force_overwrite = st.checkbox("이전 생성 파일 무시하고 새로 덮어쓰기 (설정 변경 시 권장)", value=True)
            if st.button("🧹 이전 음성 캐시 완전히 비우기", help="이전에 생성된 오디오 파일을 모두 삭제하여 100% 새 설정으로 깨끗하게 다시 생성합니다."):
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
                seg_range = st.slider("생성할 대사 구간 (시작 ~ 끝 번호)", min_value=1, max_value=total_segs, value=(1, min(10, total_segs)))
                st.caption(f"💡 선택한 {seg_range[0]}번부터 {seg_range[1]}번까지 총 {seg_range[1] - seg_range[0] + 1}개 대사만 빠르게 테스트 생성합니다. (약 5~15초 소요)")
            else:
                seg_range = (1, total_segs)
                st.info(f"💡 총 **{total_segs}개**의 대사를 생성합니다.\n(전체 약 35~40분 분량 오디오, 이미 생성된 파일은 자동 건너뛰어 초고속으로 완료됩니다)")

        btn_label = f"🚀 TTS 및 자막 생성 시작 ({seg_range[0]}번 ~ {seg_range[1]}번, 총 {seg_range[1] - seg_range[0] + 1}개)"
        if st.button(btn_label, type="primary", use_container_width=True):
            all_segments = st.session_state["parsed_segments"]
            target_segments = all_segments[seg_range[0] - 1 : seg_range[1]]
            target_count = len(target_segments)

            # Gemini 사용 여부 체크
            has_gemini = any(
                st.session_state["voice_settings"].get(s.speaker, {}).get("engine") == "gemini"
                for s in target_segments
            )

            if has_gemini and not gemini_api_key:
                st.error("⚠️ Gemini 성우가 지정된 인물이 포함되어 있습니다. 사이드바에 Gemini API Key를 입력하시거나, [👑 전체 Supertonic 3(무료)] 일괄 변경 버튼을 눌러주세요!")
                return

            # GPT-SoVITS 사용 여부 체크
            has_sovits = any(
                st.session_state["voice_settings"].get(s.speaker, {}).get("engine") == "gpt-sovits"
                for s in target_segments
            )
            if has_sovits:
                sovits_url = st.session_state.get("gpt_sovits_url", "http://127.0.0.1:9880/tts")
                ok, test_msg = TTSEngine.test_gpt_sovits_connection(sovits_url)
                if not ok:
                    st.error(f"⚠️ GPT-SoVITS API 서버에 연결할 수 없습니다: {test_msg}\n\n로컬 PC에서 GPT-SoVITS 서버(`python api_v2.py -a 127.0.0.1 -p 9880`)를 켜시거나, 좌측 사이드바에서 [Google Colab 무료 GPU] 링크를 눌러 서버를 켜고 주소를 입력해주세요.")
                    return
                # GPT-SoVITS 화자들의 참조 오디오 유효성 사전 검사
                for s in target_segments:
                    spk_data = st.session_state["voice_settings"].get(s.speaker, {})
                    if spk_data.get("engine") == "gpt-sovits":
                        r_path = spk_data.get("ref_audio_path", "")
                        if not r_path or not os.path.exists(r_path):
                            st.error(f"⚠️ 화자 '{s.speaker}'의 GPT-SoVITS 참조 오디오가 설정되지 않았거나 존재하지 않습니다. 화자 카드에서 파일을 등록해주세요!")
                            return
                        if os.path.isdir(r_path):
                            st.error(f"⚠️ 화자 '{s.speaker}'의 경로(`{r_path}`)는 파일이 아니라 폴더입니다! 폴더 안의 실제 오디오 파일(.wav, .mp3)을 선택해주세요.")
                            return

            progress_bar = st.progress(0.0)
            status_text = st.empty()

            segments_dir = os.path.join(work_dir, "segments_all")
            os.makedirs(segments_dir, exist_ok=True)

            audio_info_list = []
            
            # 1. 개별 세그먼트 생성
            error_occurred = False
            for i, seg in enumerate(target_segments, 1):
                safe_spk = "".join(c for c in seg.speaker if c.isalnum() or c in (' ', '_', '-')).strip()
                spk_cfg_data = st.session_state["voice_settings"].get(seg.speaker, {})
                seg_engine = spk_cfg_data.get("engine", "supertonic")
                seg_style = spk_cfg_data.get("style", "🎤 기본")
                
                clean_text_to_speak = clean_spoken_text(seg.text)
                engine_prefix = seg_engine.replace("-", "")

                # 화자 설정(엔진, 보이스, 스타일, 참조 오디오, 배속, 텍스트)의 고유 해시 생성
                # 설정을 조금이라도 변경하면 이전 캐시를 재탕하지 않고 자동으로 새로 생성하도록 보장
                import hashlib
                cfg_unique_str = f"{clean_text_to_speak}_{seg_engine}_{sorted(spk_cfg_data.items())}_v5_clean_ko"
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
                    eng_badge = "👑 Supertonic"
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
                    eng_badge = "⚡ Gemini"
                elif seg_engine == "gpt-sovits":
                    cfg = VoiceConfig(
                        engine="gpt-sovits",
                        voice="GPT-SoVITS",
                        style=seg_style,
                        gpt_sovits_url=st.session_state.get("gpt_sovits_url", "http://127.0.0.1:9880/tts"),
                        ref_audio_path=spk_cfg_data.get("ref_audio_path", ""),
                        prompt_text=spk_cfg_data.get("prompt_text", ""),
                        speed_factor=float(spk_cfg_data.get("speed", 0.95)),
                        temperature=float(spk_cfg_data.get("temperature", 0.65)),
                        top_k=int(spk_cfg_data.get("top_k", 5)),
                        top_p=float(spk_cfg_data.get("top_p", 0.85))
                    )
                    eng_badge = "🎙️ GPT-SoVITS"
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
                    eng_badge = "🌐 Edge"

                pct = i / target_count
                status_text.markdown(f"**🎙️ 음성 생성 중: {i}/{target_count}개 ({int(pct*100)}%)** [{eng_badge} | {seg_style}]<br>`[{seg.speaker}] {clean_text_to_speak[:35]}...`", unsafe_allow_html=True)
                progress_bar.progress(pct)

                # 파일이 이미 존재하고 덮어쓰기가 아니면 건너뛰기 (비정상 크기의 깨진 파일은 무조건 재생성)
                is_corrupt = False
                if os.path.exists(seg_file_path):
                    f_size = os.path.getsize(seg_file_path)
                    if f_size < 100 or (f_size <= 25000 and len(clean_text_to_speak) > 20):
                        is_corrupt = True

                need_generate = force_overwrite or (not os.path.exists(seg_file_path)) or is_corrupt
                if need_generate:
                    if force_overwrite:
                        for old_f in os.listdir(segments_dir):
                            if old_f.startswith(f"{seg.index:04d}_") and old_f != filename:
                                try: os.remove(os.path.join(segments_dir, old_f))
                                except Exception: pass
                    if seg_engine == "gemini" and i > 1:
                        # Gemini 무료 API 키 분당 15회(15 RPM) 한도 초과 방지 스마트 페이싱 (2.5초)
                        import time
                        time.sleep(2.5)
                    try:
                        TTSEngine.generate_speech(clean_text_to_speak, seg_file_path, cfg)
                    except Exception as e:
                        st.error(f"대사 {seg.index}번 생성 실패 ({seg.speaker} - {eng_badge}): {str(e)}")
                        error_occurred = True
                        break

                audio_info_list.append({
                    'index': seg.index,
                    'speaker': seg.speaker,
                    'text': clean_text_to_speak,
                    'file_path': seg_file_path
                })

            if not error_occurred and audio_info_list:
                # 2. 오디오 무손실 병합
                status_text.text("대사 사이 무음을 삽입하여 전체 오디오를 고속 병합하고 있습니다...")
                audio_processor = AudioProcessor(pause_ms=pause_ms)
                full_audio_path = os.path.join(work_dir, "full_audio.mp3")
                merged_path, timings = audio_processor.merge_segments(
                    audio_info_list,
                    full_audio_path,
                    pause_ms=pause_ms
                )

                # 3. 자막 생성
                status_text.text("정밀 타임스탬프 기반 SRT 및 VTT 자막 파일을 생성하고 있습니다...")
                srt_path = os.path.join(work_dir, "subtitles.srt")
                vtt_path = os.path.join(work_dir, "subtitles.vtt")
                SubtitleGenerator.generate_srt(timings, srt_path, include_speaker=include_spk_in_sub)
                SubtitleGenerator.generate_vtt(timings, vtt_path, include_speaker=include_spk_in_sub)

                # 4. ZIP 압축 패키징 (Streamlit 200MB 한도 회피 및 초고속 전송을 위해 완성본/세그먼트 스마트 분리)
                main_zip_path = os.path.join(work_dir, "tts_main_bundle.zip")
                with zipfile.ZipFile(main_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    zipf.write(full_audio_path, arcname="full_audio.mp3")
                    zipf.write(srt_path, arcname="subtitles.srt")
                    zipf.write(vtt_path, arcname="subtitles.vtt")

                seg_zip_path = os.path.join(work_dir, "tts_segments_bundle.zip")
                with zipfile.ZipFile(seg_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    for item in audio_info_list:
                        arcname = os.path.join("segments", os.path.basename(item['file_path']))
                        zipf.write(item['file_path'], arcname=arcname)

                # 5. 초고속 HTTP 직접 다운로드를 위해 static 폴더에 동기화
                static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
                os.makedirs(static_dir, exist_ok=True)
                import shutil
                try:
                    shutil.copy2(full_audio_path, os.path.join(static_dir, "full_audio.mp3"))
                    shutil.copy2(srt_path, os.path.join(static_dir, "subtitles.srt"))
                    shutil.copy2(vtt_path, os.path.join(static_dir, "subtitles.vtt"))
                    shutil.copy2(main_zip_path, os.path.join(static_dir, "tts_main_bundle.zip"))
                    shutil.copy2(seg_zip_path, os.path.join(static_dir, "tts_segments_bundle.zip"))
                except Exception:
                    pass

                progress_bar.progress(1.0)
                status_text.text("🎉 모든 음성 생성 및 자막 합성이 완료되었습니다!")

                st.session_state["generation_result"] = {
                    "full_audio": full_audio_path,
                    "srt": srt_path,
                    "vtt": vtt_path,
                    "main_zip": main_zip_path,
                    "seg_zip": seg_zip_path,
                    "timings": timings,
                    "audio_info_list": audio_info_list
                }
                st.rerun()

    # Step 4: 결과 화면 및 다운로드
    if st.session_state.get("generation_result"):
        res = st.session_state["generation_result"]
        st.divider()
        st.success("✨ 오디오 생성 및 병합이 완벽하게 완료되었습니다!")

        # 윈도우 로컬 환경일 경우 원클릭 탐색기 열기 (다운로드 없이 0초 즉시 사용)
        if os.name == 'nt' and os.path.exists(res.get("full_audio", "")):
            folder_path = os.path.dirname(os.path.abspath(res["full_audio"]))
            col_loc1, col_loc2 = st.columns([3, 1])
            with col_loc1:
                st.info(f"💡 **로컬 즉시 사용**: 파일이 이미 컴퓨터에 100% 저장되어 있습니다: `{folder_path}`")
            with col_loc2:
                if st.button("📂 저장 폴더 즉시 열기 (0초)", use_container_width=True, key="btn_open_local_folder"):
                    subprocess.Popen(["explorer", folder_path])
                    st.toast("✅ 윈도우 파일 탐색기를 열었습니다!")

        import time
        ts = int(time.time())
        col_main, col_down = st.columns([3, 2])
        with col_main:
            st.markdown("### 🎧 전체 병합 오디오 재생")
            if os.path.exists(res["full_audio"]):
                with open(res["full_audio"], "rb") as f:
                    audio_bytes = f.read()
                st.audio(audio_bytes, format="audio/mp3")

        with col_down:
            st.markdown("### 📥 결과 파일 초고속 다운로드")

            # 1. 고속 직접 HTTP 스트리밍 링크 (WebSocket 200MB 버퍼링 한도 우회, 초당 40~300MB/s 속도)
            st.markdown(
                f"""
                <div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px;">
                    <a href="app/static/tts_main_bundle.zip?t={ts}" download="tts_main_bundle.zip" 
                       style="display: block; text-align: center; background: linear-gradient(135deg, #7c3aed, #6366f1); color: white; padding: 12px 16px; border-radius: 8px; text-decoration: none; font-weight: 700; font-size: 0.95rem; box-shadow: 0 4px 12px rgba(124, 58, 237, 0.3);">
                        ⚡ 완성본 초고속 다운로드 (.ZIP - 전체 오디오+자막)
                    </a>
                    <div style="display: flex; gap: 8px;">
                        <a href="app/static/full_audio.mp3?t={ts}" download="full_audio.mp3" 
                           style="flex: 1; text-align: center; background: #1e293b; border: 1px solid #334155; color: #f8fafc; padding: 10px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; font-weight: 600;">
                            🎵 전체 오디오 (MP3)
                        </a>
                        <a href="app/static/subtitles.srt?t={ts}" download="subtitles.srt" 
                           style="flex: 1; text-align: center; background: #1e293b; border: 1px solid #334155; color: #f8fafc; padding: 10px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; font-weight: 600;">
                            📝 자막 파일 (SRT)
                        </a>
                    </div>
                    <a href="app/static/tts_segments_bundle.zip?t={ts}" download="tts_segments_bundle.zip" 
                       style="display: block; text-align: center; background: #0f172a; border: 1px solid #334155; color: #cbd5e1; padding: 8px 12px; border-radius: 6px; text-decoration: none; font-size: 0.82rem;">
                        🗄️ 개별 분할 대사 압축팩 (전체 세그먼트 MP3)
                    </a>
                </div>
                """,
                unsafe_allow_html=True
            )

            # 2. 보조 표준 다운로드 버튼 (스트림 지연 로딩)
            with st.expander("🛠️ 대체 다운로드 방식 (표준 버튼)", expanded=False):
                main_z = res.get("main_zip", res.get("zip"))
                if main_z and os.path.exists(main_z):
                    st.download_button(
                        label="📦 완성본 패키지 (.ZIP)",
                        data=lambda: open(main_z, "rb").read(),
                        file_name="tts_main_bundle.zip",
                        mime="application/zip",
                        use_container_width=True
                    )
                if os.path.exists(res["full_audio"]):
                    st.download_button(
                        "🎵 전체 오디오 (MP3)",
                        data=lambda: open(res["full_audio"], "rb").read(),
                        file_name="full_audio.mp3",
                        mime="audio/mp3",
                        use_container_width=True
                    )
                if os.path.exists(res["srt"]):
                    st.download_button(
                        "📝 자막 파일 (SRT)",
                        data=lambda: open(res["srt"], "rb").read(),
                        file_name="subtitles.srt",
                        mime="text/plain",
                        use_container_width=True
                    )

        # 개별 대사별 타임라인 및 재생 목록
        with st.expander("🔍 세부 대사별 타임라인 및 개별 음성 확인", expanded=False):
            for t in res["timings"]:
                tc1, tc2, tc3 = st.columns([1.5, 4, 3])
                with tc1:
                    start_sec = t.start_ms / 1000.0
                    end_sec = t.end_ms / 1000.0
                    st.markdown(f"**[{t.speaker}]**<br><small>{start_sec:.2f}s ~ {end_sec:.2f}s</small>", unsafe_allow_html=True)
                with tc2:
                    st.write(t.text)
                with tc3:
                    if os.path.exists(t.file_path):
                        with open(t.file_path, "rb") as f:
                            st.audio(f.read(), format="audio/mp3")

if __name__ == "__main__":
    main()
