import asyncio
import os
import io
import re
import time
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import edge_tts

def clean_spoken_text(text: str) -> str:
    """
    TTS 음성 합성 시 화자 이름(예: '성복:', '[성복]', '나레이션:', '(성복)')이나
    지문, 불필요한 따옴표가 소리내어 발음되지 않도록 순수 대사만 정제합니다.
    화자 이름 표시는 화면과 자막에만 남고, 실제 음성에서는 대사만 출력됩니다.
    """
    if not text:
        return ""
    
    text = text.strip()
    # 1. '화자: 대사' 또는 '화자： 대사' 또는 '화자 - 대사' 제거
    text = re.sub(r'^\s*(?:\[[^\]]+\]|\([^)]+\)|[가-힣a-zA-Z0-9_\-\s]{1,15})\s*[:：\-]\s*', '', text)
    # 2. '[화자] 대사' 또는 '(화자) 대사' 형태 제거
    text = re.sub(r'^\s*\[[^\]]+\]\s*', '', text)
    text = re.sub(r'^\s*\([가-힣a-zA-Z0-9_\-\s]{1,15}\)\s*', '', text)
    
    # 3. 앞뒤 따옴표 벗기기
    text = text.strip()
    if (text.startswith('"') and text.endswith('"')) or \
       (text.startswith('“') and text.endswith('”')) or \
       (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()
        
    # 4. 혹시 중복으로 붙어있던 경우(예: '나레이션: [성복] 대사') 재차 제거
    text = re.sub(r'^\s*(?:\[[^\]]+\]|\([^)]+\)|[가-힣a-zA-Z0-9_\-\s]{1,15})\s*[:：\-]\s*', '', text)
    text = re.sub(r'^\s*\[[^\]]+\]\s*', '', text)
    text = re.sub(r'^\s*\([가-힣a-zA-Z0-9_\-\s]{1,15}\)\s*', '', text)
    
    # 5. 문장 시작 부분의 마침표/쉼표/말줄임표/기호 제거 (TTS 말더듬 "어...", "티..." 잡음 방지)
    text = re.sub(r'^[.,?!~…\s]+', '', text)
    # 6. 연속된 말줄임표(...)를 깔끔한 단일 마침표(.)로 정규화
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r'…+', '.', text)
    
    return text.strip()

def optimize_text_for_sovits(text: str, max_chunk_len: int = 40) -> str:
    """
    GPT-SoVITS의 자기회귀(AR) 모델 특성상 문장이 35~40자 이상 길어지면
    호흡 조절 실패, 주의집중(Attention) 이탈로 인해 뒤로 갈수록 말이 빨라지고
    발음 뭉개짐(slurring), 씹힘, 톤 변형이 발생합니다.
    
    미리듣기(20~25자)처럼 또렷하고 선명한 고음질을 전체 생성에서도 유지하기 위해,
    쉼표(,)나 마침표 없이 40자를 초과하는 긴 절(Clause)을
    한국어 문맥(접속사, 연결어미, 띄어쓰기)을 고려해 자연스럽게 쉼표(,)로 분할합니다.
    """
    if not text or len(text) <= max_chunk_len:
        return text

    # 문장부호 단위로 1차 분할
    tokens = re.split(r'([,.:;?!~…\n]+)', text)
    result_parts = []
    
    # 한국어에서 호흡을 쉬어가기 가장 자연스러운 연결어미 및 접속어
    endings = (
        '있었고', '있으며', '있지만', '있는데', '하는데', '였는데', '되었고', '보았고',
        '그리고', '하지만', '그러나', '그런데', '때문에', '따라서',
        '고', '며', '면서', '면', '지만', '는데', '은데', '인데', '하여', '하고', '더니', '거든', '려고'
    )
    
    for token in tokens:
        if not token:
            continue
        if re.match(r'^[,.:;?!~…\n]+$', token):
            result_parts.append(token)
            continue
            
        cur = token.strip()
        while len(cur) > max_chunk_len:
            search_window_start = 20
            search_window_end = min(len(cur), max_chunk_len + 5)
            sub = cur[:search_window_end]
            
            words = sub.split(' ')
            split_pos = -1
            
            # 1순위: 긴 연결어미 패턴 일치
            acc_len = 0
            for w in words[:-1]:
                acc_len += len(w) + 1
                if acc_len >= search_window_start:
                    clean_w = re.sub(r'[^가-힣a-zA-Z0-9]', '', w)
                    for e in endings:
                        if clean_w.endswith(e):
                            split_pos = acc_len - 1
                            break
                    if split_pos != -1:
                        break
            
            # 2순위: 20자 ~ max_chunk_len 사이의 일반 띄어쓰기 위치
            if split_pos == -1:
                acc_len = 0
                best_space = -1
                for w in words[:-1]:
                    acc_len += len(w) + 1
                    if search_window_start <= acc_len <= max_chunk_len + 5:
                        best_space = acc_len - 1
                if best_space != -1:
                    split_pos = best_space
            
            # 3순위: 그래도 없으면 max_chunk_len 위치
            if split_pos == -1 or split_pos <= 5:
                split_pos = max_chunk_len
            
            part = cur[:split_pos].strip()
            if part:
                if not part.endswith((',', '.', '!', '?', ';', ':')):
                    result_parts.append(part + ', ')
                else:
                    result_parts.append(part + ' ')
            cur = cur[split_pos:].strip()
            
        if cur:
            result_parts.append(cur)
            
    return ''.join(result_parts).strip()

@dataclass
class VoiceConfig:
    engine: str = "supertonic"            # "supertonic", "gemini", "edge-tts", "gpt-sovits", "f5-tts", "xtts"
    voice: str = "F1"                     # 보이스 ID (Supertonic: F1~F5, M1~M5 / Gemini: Kore, Charon... / Edge: SunHi...)
    model: str = "gemini-3.1-flash-tts-preview"       # Gemini TTS 전용 모델명
    style: str = "🎤 기본"                # 음성 스타일 (34종 감정/연령/톤)
    speed: float = 1.0                    # 속도
    rate: str = "+0%"                     # Edge-TTS 속도
    pitch: str = "+0Hz"                   # Edge-TTS 피치
    volume: str = "+0%"
    api_key: Optional[str] = None         # Gemini API Key
    gpt_sovits_url: str = "http://127.0.0.1:9880/tts" # GPT-SoVITS API 주소
    f5_tts_url: str = "http://127.0.0.1:7860" # Pinokio F5-TTS API 주소
    ref_audio_path: str = ""              # GPT-SoVITS / F5-TTS 목소리 복제용 참조 오디오 (.wav, .mp3)
    prompt_text: str = ""                 # 참조 오디오의 대사 텍스트
    prompt_lang: str = "ko"               # 참조 오디오 언어 (ko, en, zh, ja)
    text_lang: str = "ko"                 # 생성할 대사 언어 (ko, en, zh, ja)
    speed_factor: float = 1.0             # 배속 (0.5 ~ 2.0)
    temperature: float = 0.65            # GPT-SoVITS 샘플링 온도
    top_k: int = 5                       # GPT-SoVITS Top-k
    top_p: float = 0.85                  # GPT-SoVITS Top-p
    text_split_method: str = "cut5"      # GPT-SoVITS 텍스트 분할 (cut5)
    nfe_steps: int = 32                  # F5-TTS NFE Step (16~64)
    cosyvoice_url: str = ""              # CosyVoice Colab/WebUI API 주소
    xtts_url: str = ""                   # XTTS v2 Colab/WebUI API 주소

# 0. 34종 음성 스타일 프리셋 (감정, 어조, 연령대, 성숙도)
VOICE_STYLES = {
    "🎤 기본": {
        "gemini_prompt": "Read in a natural, neutral, and clear storytelling tone.",
        "rate": 0, "pitch": 0, "volume": 0,
        "desc": "자연스럽고 표준적인 기본 낭독 톤"
    },
    "😊 밝고 활기차게": {
        "gemini_prompt": "Speak brightly, cheerfully, and energetically with an upbeat and lively smile in your voice.",
        "rate": 10, "pitch": 10, "volume": 5,
        "desc": "경쾌하고 활력 넘치는 밝은 목소리"
    },
    "🌙 차분하고 따뜻하게": {
        "gemini_prompt": "Speak calmly, soothingly, gently, and warmly with a relaxed and peaceful pace.",
        "rate": -5, "pitch": -5, "volume": 0,
        "desc": "마음이 편안해지는 따뜻하고 은은한 톤"
    },
    "💌 부드럽고 감성적": {
        "gemini_prompt": "Speak softly, emotionally, tenderly, and delicately with rich emotional depth.",
        "rate": -5, "pitch": 0, "volume": -5,
        "desc": "서정적이고 섬세한 감성의 부드러운 목소리"
    },
    "📺 뉴스 앵커": {
        "gemini_prompt": "Speak like a professional news anchor: crisp diction, authoritative, objective, and articulate.",
        "rate": 5, "pitch": 0, "volume": 5,
        "desc": "신뢰감 있고 또렷한 아나운서/앵커 톤"
    },
    "📖 동화 나레이션": {
        "gemini_prompt": "Speak like an enchanting fairytale storyteller: warm, expressive, playful, and captivating for listeners.",
        "rate": 0, "pitch": 5, "volume": 0,
        "desc": "상상력을 자극하는 포근하고 몰입감 있는 구연동화"
    },
    "⚡ 긴박하게": {
        "gemini_prompt": "Speak urgently, breathlessly, and with high tension and swift, tense pacing as if in an emergency.",
        "rate": 20, "pitch": 10, "volume": 10,
        "desc": "위기 상황이나 추격전의 숨가쁘고 긴장감 넘치는 톤"
    },
    "😢 슬프게": {
        "gemini_prompt": "Speak with profound sorrow, a trembling tearful voice, slow and full of heartbreaking melancholy.",
        "rate": -15, "pitch": -10, "volume": -10,
        "desc": "눈물과 한이 서린 애절하고 슬픈 목소리"
    },
    "✨ 신비롭게": {
        "gemini_prompt": "Speak mysteriously, enigmatically, with an ethereal, magical whisper and aura of wonder.",
        "rate": -10, "pitch": 5, "volume": -5,
        "desc": "몽환적이고 비밀스러운 신비주의 톤"
    },
    "😄 유머러스하게": {
        "gemini_prompt": "Speak humorously, playfully, with chuckling wit, mischievous charm, and comical inflection.",
        "rate": 10, "pitch": 5, "volume": 5,
        "desc": "위트 있고 익살스러운 코믹 톤"
    },
    "🎭 진지하게": {
        "gemini_prompt": "Speak solemnly, seriously, with sincere earnestness and deep thoughtful gravity.",
        "rate": -5, "pitch": -5, "volume": 0,
        "desc": "무게감 있고 엄숙하며 진솔한 태도"
    },
    "🤫 속삭이듯": {
        "gemini_prompt": "Speak in a confidential whisper, soft and hush, as if sharing a secret close to the ear.",
        "rate": -10, "pitch": -5, "volume": -20,
        "desc": "비밀을 털어놓듯 나지막하고 은밀한 속삭임"
    },
    "💪 힘차게": {
        "gemini_prompt": "Speak powerfully, vigorously, with confident courage, loud projection, and roaring energy.",
        "rate": 10, "pitch": 10, "volume": 15,
        "desc": "기백과 용기가 넘치는 웅장하고 우렁찬 톤"
    },
    "👻 으스스하게": {
        "gemini_prompt": "Speak eerily, creepily, with a chilling, hair-raising suspenseful spooky tone.",
        "rate": -15, "pitch": -10, "volume": -5,
        "desc": "간담이 서늘해지는 괴담과 공포 분위기"
    },
    "🌈 희망차게": {
        "gemini_prompt": "Speak with bright hope, uplifting inspiration, optimism, and warmth looking forward to the dawn.",
        "rate": 5, "pitch": 5, "volume": 5,
        "desc": "어둠을 걷어내는 벅찬 희망과 격려의 목소리"
    },
    "📽️ 다큐멘터리": {
        "gemini_prompt": "Speak like a seasoned documentary narrator: deep, objective, reflective, and grandly immersive.",
        "rate": -5, "pitch": -5, "volume": 0,
        "desc": "역사와 대자연을 관조하는 깊이 있는 다큐멘터리 해설"
    },
    "📻 라디오 DJ": {
        "gemini_prompt": "Speak like a friendly, smooth late-night radio DJ: intimate, melodic, conversational, and soothing.",
        "rate": -5, "pitch": 0, "volume": 0,
        "desc": "심야 라디오의 부드럽고 다정한 DJ 톤"
    },
    "🎓 강의/교육": {
        "gemini_prompt": "Speak like an articulate, inspiring professor: structured, pedagogical, clear, and instructive.",
        "rate": 0, "pitch": 0, "volume": 5,
        "desc": "정확한 전달력과 지적인 멘토의 설명 톤"
    },
    "🏆 스포츠 중계": {
        "gemini_prompt": "Speak like an exhilarating sports caster: rapid-fire, explosive excitement, shouting with adrenaline!",
        "rate": 25, "pitch": 15, "volume": 20,
        "desc": "골 순간의 폭발적인 열기와 긴박한 스포츠 중계"
    },
    "💼 비즈니스": {
        "gemini_prompt": "Speak like a professional business executive: polished, crisp, confident, concise, and persuasive.",
        "rate": 5, "pitch": 0, "volume": 5,
        "desc": "프로페셔널하고 설득력 있는 비즈니스 브리핑"
    },
    "🧘 명상/ASMR": {
        "gemini_prompt": "Speak in a calm meditation ASMR guide voice: breathing gently, slow, hushed, deeply relaxing.",
        "rate": -25, "pitch": -5, "volume": -15,
        "desc": "호흡을 가다듬게 돕는 극상의 힐링과 이완"
    },
    "🎪 광고 나레이션": {
        "gemini_prompt": "Speak like an enthusiastic commercial narrator: catchy, captivating, persuasive, and dynamic.",
        "rate": 15, "pitch": 10, "volume": 10,
        "desc": "귀에 쏙쏙 박히는 강렬한 광고 카피 톤"
    },
    "😠 분노/격양": {
        "gemini_prompt": "Speak with fierce outrage, harsh anger, intense indignance, raised voice, and passionate fury!",
        "rate": 15, "pitch": 15, "volume": 20,
        "desc": "호통치거나 격분하여 치미는 분노의 외침"
    },
    "💡 30대 세련된": {
        "gemini_prompt": "Speak as a chic, sophisticated, modern person in their early 30s: stylish, crisp, and intelligent.",
        "rate": 5, "pitch": 5, "volume": 0,
        "desc": "도시적이고 세련되며 당당한 30대"
    },
    "☕ 30대 따뜻한": {
        "gemini_prompt": "Speak as a warm, considerate person in their 30s: gentle, attentive, and comforting like warm coffee.",
        "rate": -5, "pitch": 0, "volume": 0,
        "desc": "다정하고 배려 깊은 온화한 30대"
    },
    "🍷 40대 성숙한": {
        "gemini_prompt": "Speak as an experienced, mature person in their 40s: calm composure, mellow timbre, and distinguished presence.",
        "rate": -5, "pitch": -5, "volume": 0,
        "desc": "원숙미와 기품이 느껴지는 차분한 40대"
    },
    "🏛️ 40대 안정적인": {
        "gemini_prompt": "Speak as an authoritative, stable person in their 40s: dependable, grounded, dignified, and solid.",
        "rate": 0, "pitch": -5, "volume": 5,
        "desc": "신뢰와 권위를 풍기는 듬직한 리더의 톤"
    },
    "🎩 50대 깊이 있는": {
        "gemini_prompt": "Speak as a distinguished, seasoned individual in their 50s: deep resonance, wise, respected, and steady.",
        "rate": -10, "pitch": -10, "volume": 0,
        "desc": "인생의 깊이와 품격이 우러나는 중후한 톤"
    },
    "🍂 50대 여유로운": {
        "gemini_prompt": "Speak as a relaxed, mellow person in their 50s: unhurried, comfortable, warm, and smiling.",
        "rate": -10, "pitch": -5, "volume": 0,
        "desc": "세상을 관조하는 여유와 편안함"
    },
    "👴 시니어 중후한 (60대)": {
        "gemini_prompt": "Speak as a venerable gentleman in his 60s: deep mature voice, deliberate, experienced, and dignified.",
        "rate": -10, "pitch": -12, "volume": 0,
        "desc": "연륜과 위엄을 간직한 60대 어르신"
    },
    "👵 시니어 따뜻한 (70대)": {
        "gemini_prompt": "Speak as an affectionate, loving grandmother in her 70s: gentle, slightly raspy, endearing, and deeply caring.",
        "rate": -15, "pitch": -5, "volume": -5,
        "desc": "손주를 보듬듯 다정하고 포근한 70대 할머니"
    },
    "🧙 시니어 지혜로운 (70~80대)": {
        "gemini_prompt": "Speak as an ancient, wise elder in their 70s-80s: slow, weathered, cracked grandfatherly tone, full of folktale wisdom.",
        "rate": -18, "pitch": -15, "volume": 0,
        "desc": "오랜 세월의 구수한 지혜와 연륜이 묻어나는 이야기 노인"
    },
    "📰 시니어 안정적인 (65세)": {
        "gemini_prompt": "Speak as a seasoned senior in their mid-60s: stable, clear, thoughtful, and composed.",
        "rate": -8, "pitch": -8, "volume": 0,
        "desc": "흐트러짐 없이 또렷하고 안정적인 60대 중반"
    },
    "🎭 시니어 감성적인 (70대 이상)": {
        "gemini_prompt": "Speak as an emotional, reflective elder in their 70s+: nostalgic, wistful, tender, and touching.",
        "rate": -15, "pitch": -8, "volume": -5,
        "desc": "지나온 세월을 회상하듯 아련하고 감동적인 시니어"
    }
}

# 1. Supertonic 3 (로컬 무료) 한국어 보이스 목록
SUPERTONIC_VOICES = {
    "F1": {
        "name": "F1 - 차분하고 지적인 여성 (나레이션 / 노모 추천)",
        "gender": "여성",
        "description": "차분하고 품격 있는 오디오북 해설 톤",
        "default_role": "나레이션 / 노모"
    },
    "F2": {
        "name": "F2 - 밝고 생기있는 여성 (계순 / 젊은 아낙 추천)",
        "gender": "여성",
        "description": "영민하고 발랄하며 호소력 있는 여성 톤",
        "default_role": "계순 (아내) / 젊은 아낙"
    },
    "F3": {
        "name": "F3 - 또렷하고 전달력 높은 표준 여성",
        "gender": "여성",
        "description": "정확한 딕션과 깔끔한 뉴스/해설 톤",
        "default_role": "나레이션"
    },
    "F4": {
        "name": "F4 - 부드럽고 다정한 여성",
        "gender": "여성",
        "description": "따뜻하고 편안한 일상 여성 톤",
        "default_role": "젊은 아낙 / 노모"
    },
    "F5": {
        "name": "F5 - 개성 있고 감성적인 여성",
        "gender": "여성",
        "description": "감정선이 돋보이는 드라마 톤",
        "default_role": "계순"
    },
    "M1": {
        "name": "M1 - 진솔하고 성실한 청년 남성 (성복 / 청년 추천)",
        "gender": "남성",
        "description": "성실하고 진심 어린 주인공 청년 톤",
        "default_role": "성복 (주인공) / 청년"
    },
    "M2": {
        "name": "M2 - 묵직하고 권위 있는 중저음 남성 (사또 / 경헌 / 장인 추천)",
        "gender": "남성",
        "description": "중후하고 위엄 넘치는 관아 사또 / 관리 톤",
        "default_role": "사또 / 경헌 / 장인"
    },
    "M3": {
        "name": "M3 - 연륜 있고 깊은 이야기꾼 남성 (잉손 / 박씨 노인 / 훈장 추천)",
        "gender": "남성",
        "description": "세월의 멋과 연륜이 묻어나는 구수한 노인 톤",
        "default_role": "잉손 (짚신장이 노인) / 박씨 노인 / 훈장"
    },
    "M4": {
        "name": "M4 - 단정하고 힘 있는 남성 (남 포졸 추천)",
        "gender": "남성",
        "description": "단정하고 날렵한 관아 포졸 톤",
        "default_role": "남 포졸"
    },
    "M5": {
        "name": "M5 - 친근하고 편안한 일상 남성",
        "gender": "남성",
        "description": "자연스러운 대화형 남성 톤",
        "default_role": "청년 / 포졸"
    }
}

# 2. Gemini Flash TTS 공식 30대 보이스 전체 목록
GEMINI_VOICES = {
    # --- [여성 보이스 (13종)] ---
    "Kore": {
        "name": "코레 (Kore) - 차분하고 단호한 여성 (Firm)",
        "gender": "여성",
        "description": "품격 있고 절제된 오디오북 표준 해설 톤",
        "default_role": "나레이션 / 지적인 여인 / 노모"
    },
    "Aoede": {
        "name": "아오이데 (Aoede) - 산뜻하고 맑은 여성 (Breezy)",
        "gender": "여성",
        "description": "상쾌하고 생기 넘치는 밝은 여주인공 톤",
        "default_role": "달래 / 계순 / 젊은 아낙"
    },
    "Zephyr": {
        "name": "제피르 (Zephyr) - 밝고 화사한 여성 (Bright)",
        "gender": "여성",
        "description": "화사하고 긍정적인 에너지의 경쾌한 톤",
        "default_role": "말숙 / 생기 넘치는 여인 / 소녀"
    },
    "Leda": {
        "name": "레다 (Leda) - 풋풋하고 발랄한 여성 (Youthful)",
        "gender": "여성",
        "description": "젊고 순수한 매력의 소녀 및 청소년 여성 톤",
        "default_role": "소녀 / 딸 / 젊은 여주인공"
    },
    "Callirrhoe": {
        "name": "칼리로에 (Callirrhoe) - 편안하고 자연스러운 여성 (Easy-going)",
        "gender": "여성",
        "description": "부담 없이 다정하고 편안한 일상 대화 톤",
        "default_role": "이웃 아낙 / 다정한 누나"
    },
    "Autonoe": {
        "name": "아우토노에 (Autonoe) - 긍정적이고 화사한 여성 (Bright)",
        "gender": "여성",
        "description": "빛나고 당찬 호소력 있는 여성 톤",
        "default_role": "아가씨 / 총명한 여주인공"
    },
    "Despina": {
        "name": "데스피나 (Despina) - 매끄럽고 차분한 여성 (Smooth)",
        "gender": "여성",
        "description": "단아하고 정갈하며 세련된 여성 톤",
        "default_role": "단아한 부인 / 정숙한 여인"
    },
    "Erinome": {
        "name": "에리노메 (Erinome) - 투명하고 깨끗한 여성 (Clear)",
        "gender": "여성",
        "description": "군더더기 없이 맑고 명료한 낭독 톤",
        "default_role": "투명한 나레이션 / 맑은 여인"
    },
    "Laomedeia": {
        "name": "라오메데이아 (Laomedeia) - 에너지 넘치는 여성 (Upbeat)",
        "gender": "여성",
        "description": "통통 튀고 신나는 분위기의 활기찬 톤",
        "default_role": "말괄량이 / 귀여운 아이 / 활기찬 아낙"
    },
    "Pulcherrima": {
        "name": "풀케리마 (Pulcherrima) - 당차고 곧은 여성 (Forward)",
        "gender": "여성",
        "description": "카리스마 있고 자신감 넘치는 주도적 톤",
        "default_role": "여장부 / 카리스마 여주인공"
    },
    "Vindemiatrix": {
        "name": "빈데미아트릭스 (Vindemiatrix) - 온화하고 자애로운 여성 (Gentle)",
        "gender": "여성",
        "description": "마음을 어루만지는 따뜻하고 부드러운 톤",
        "default_role": "어머니 / 자애로운 부인 / 할머니"
    },
    "Sadachbia": {
        "name": "사다크비아 (Sadachbia) - 생기 넘치는 쾌활한 여성 (Lively)",
        "gender": "여성",
        "description": "생동감 있고 발랄하게 이야기를 이끄는 톤",
        "default_role": "젊은 여인 / 유쾌한 조연"
    },
    "Sulafat": {
        "name": "술라파트 (Sulafat) - 포근하고 따뜻한 여성 (Warm)",
        "gender": "여성",
        "description": "모성애와 정감이 넘치는 포근한 톤",
        "default_role": "노모 / 포근한 어머니 / 유모"
    },

    # --- [남성 보이스 (17종)] ---
    "Charon": {
        "name": "카론 (Charon) - 중후하고 신뢰감 넘치는 저음 남성 (Informative)",
        "gender": "남성",
        "description": "위엄 있고 묵직한 관아 사또 / 장인 어른 톤",
        "default_role": "사또 / 현감 / 경헌 / 장인"
    },
    "Fenrir": {
        "name": "펜리르 (Fenrir) - 열정적이고 당찬 청년 남성 (Excitable)",
        "gender": "남성",
        "description": "성실하고 씩씩한 패기 넘치는 청년 주인공 톤",
        "default_role": "덕쇠 / 성복 / 포졸 / 청년"
    },
    "Puck": {
        "name": "퍽 (Puck) - 위트 있고 개성 넘치는 남성 (Upbeat)",
        "gender": "남성",
        "description": "구수하고 재치 있는 이야기꾼 톤",
        "default_role": "잉손 / 박씨 노인 / 훈장"
    },
    "Orus": {
        "name": "오루스 (Orus) - 단단하고 묵직한 남성 (Firm)",
        "gender": "남성",
        "description": "강직하고 흔들림 없는 묵직한 장군 톤",
        "default_role": "무관 / 장군 / 엄격한 아버지"
    },
    "Enceladus": {
        "name": "엔켈라두스 (Enceladus) - 감성적이고 속삭이는 남성 (Breathy)",
        "gender": "남성",
        "description": "숨결이 묻어나는 섬세하고 서정적인 청년 톤",
        "default_role": "감성적인 청년 / 시인 / 고뇌하는 인물"
    },
    "Iapetus": {
        "name": "이아페투스 (Iapetus) - 맑고 명료한 표준 남성 (Clear)",
        "gender": "남성",
        "description": "딕션이 또렷하고 전달력이 뛰어난 지적 톤",
        "default_role": "남성 나레이션 / 지식인 / 선비"
    },
    "Umbriel": {
        "name": "움브리엘 (Umbriel) - 여유롭고 부드러운 남성 (Easy-going)",
        "gender": "남성",
        "description": "차분하고 편안한 미소를 머금은 톤",
        "default_role": "다정한 남편 / 너그러운 선비"
    },
    "Algieba": {
        "name": "알기에바 (Algieba) - 세련되고 부드러운 남성 (Smooth)",
        "gender": "남성",
        "description": "품격 있는 젊은 귀족 / 양반 톤",
        "default_role": "젊은 양반 / 귀공자 / 봉진우"
    },
    "Algenib": {
        "name": "알게니브 (Algenib) - 허스키하고 거친 매력의 남성 (Gravelly)",
        "gender": "남성",
        "description": "거친 숨결과 남성미가 묻어나는 매력적인 톤",
        "default_role": "거친 사나이 / 뱃사공 / 주막 건달"
    },
    "Rasalgethi": {
        "name": "라살게티 (Rasalgethi) - 지적이고 논리적인 남성 (Informative)",
        "gender": "남성",
        "description": "설득력 있고 차분한 학자 및 의원 톤",
        "default_role": "의원 / 책사 / 관찰자"
    },
    "Achernar": {
        "name": "아케르나르 (Achernar) - 따뜻하고 부드러운 남성 (Soft)",
        "gender": "남성",
        "description": "자상하고 온화하게 감싸주는 온정 넘치는 톤",
        "default_role": "자상한 아버지 / 다정한 청년"
    },
    "Alnilam": {
        "name": "알닐람 (Alnilam) - 곧고 당당한 기개의 남성 (Firm)",
        "gender": "남성",
        "description": "불의를 참지 않는 곧고 바른 의로운 톤",
        "default_role": "정의로운 판관 / 곧은 선비"
    },
    "Schedar": {
        "name": "셰다르 (Schedar) - 균형 잡히고 안정된 남성 (Even)",
        "gender": "남성",
        "description": "중립적이고 객관적인 정통 다큐멘터리 해설 톤",
        "default_role": "역사 다큐 나레이션 / 중립적 관찰자"
    },
    "Gacrux": {
        "name": "가크룩스 (Gacrux) - 연륜 있고 깊이 있는 시니어 남성 (Mature)",
        "gender": "남성",
        "description": "산전수전을 겪은 어르신의 깊은 연륜 톤",
        "default_role": "송 노인 / 촌장 / 백발 노옹"
    },
    "Achird": {
        "name": "아키르드 (Achird) - 친근하고 다정한 남성 (Friendly)",
        "gender": "남성",
        "description": "이웃집 삼촌처럼 서글서글하고 붙임성 좋은 톤",
        "default_role": "마을 사람 / 이웃집 삼촌 / 착한 머슴"
    },
    "Zubenelgenubi": {
        "name": "주베넬게누비 (Zubenelgenubi) - 털털하고 자유로운 남성 (Casual)",
        "gender": "남성",
        "description": "격식 없이 시원시원하고 익살스러운 일상 톤",
        "default_role": "동네 친구 / 주막 손님 / 봉 행수"
    },
    "Sadaltager": {
        "name": "사달타게르 (Sadaltager) - 학식 깊고 무게감 있는 남성 (Knowledgeable)",
        "gender": "남성",
        "description": "지혜와 권위가 배어 있는 원로 대감 톤",
        "default_role": "이방 오익환 / 대감 / 훈장님 / 노학자"
    }
}

# 3. 무료 Edge-TTS 순수 한국어 신경망 보이스 목록
KOREAN_EDGE_VOICES = {
    "ko-KR-SunHiNeural": {
        "name": "선희 (Sun-Hi) - 한국어 여성 표준",
        "gender": "여성",
        "description": "차분하고 또렷한 표준 한국어 여성 톤",
        "default_role": "나레이션 / 여성 주연"
    },
    "ko-KR-InJoonNeural": {
        "name": "인준 (In-Joon) - 한국어 남성 표준",
        "gender": "남성",
        "description": "자연스럽고 편안한 표준 한국어 남성 톤",
        "default_role": "남성 주연 / 조연"
    },
    "ko-KR-HyunsuMultilingualNeural": {
        "name": "현수 (Hyunsu) - 한국어 남성(청년)",
        "gender": "남성",
        "description": "젊고 활기찬 청년/소년 톤",
        "default_role": "청년 / 소년"
    },
    "en-US-AvaMultilingualNeural": {
        "name": "아바 (Ava) - 다국어 여성",
        "gender": "여성",
        "description": "부드럽고 자연스러운 다국어 여성 톤",
        "default_role": "계순 / 젊은 아낙"
    },
    "en-US-BrianMultilingualNeural": {
        "name": "브라이언 (Brian) - 다국어 남성",
        "gender": "남성",
        "description": "중후하고 깊이 있는 다국어 남성 톤",
        "default_role": "사또 / 장인"
    },
    "en-US-AndrewMultilingualNeural": {
        "name": "앤드류 (Andrew) - 다국어 노인/남성",
        "gender": "남성",
        "description": "연륜 있고 따뜻한 다국어 남성 톤",
        "default_role": "잉손 / 박씨 노인"
    },
    "en-US-EmmaMultilingualNeural": {
        "name": "엠마 (Emma) - 다국어 여성",
        "gender": "여성",
        "description": "다정하고 차분한 다국어 여성 톤",
        "default_role": "노모"
    }
}

# 싱글톤 Supertonic 모델 인스턴스
_supertonic_instance = None

def get_supertonic_engine():
    global _supertonic_instance
    if _supertonic_instance is None:
        import supertonic
        _supertonic_instance = supertonic.TTS(auto_download=False)
    return _supertonic_instance

class TTSEngine:
    _gemini_key_counter: int = 0

    def __init__(self):
        pass

    @staticmethod
    def get_supertonic_voices() -> Dict[str, dict]:
        return SUPERTONIC_VOICES

    @staticmethod
    def get_gemini_voices() -> Dict[str, dict]:
        return GEMINI_VOICES

    @staticmethod
    def get_edge_voices() -> Dict[str, dict]:
        return KOREAN_EDGE_VOICES

    @staticmethod
    def clean_spoken_text(text: str) -> str:
        return clean_spoken_text(text)

    @staticmethod
    def get_voice_styles() -> Dict[str, dict]:
        return VOICE_STYLES

    @classmethod
    def generate_supertonic_speech(
        cls,
        text: str,
        output_file: str,
        voice_config: VoiceConfig
    ) -> str:
        """
        Supertonic 3 로컬 초고속 무료 합성 (하이브 수퍼톤 ONNX 모델) + 스타일 후처리
        """
        text = clean_spoken_text(text)
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        tts = get_supertonic_engine()

        voice_key = voice_config.voice if voice_config.voice in SUPERTONIC_VOICES else "F1"
        voice_style = tts.get_voice_style(voice_key)

        res = tts.synthesize(text, voice_style=voice_style, lang="ko")
        audio_array = res[0]

        temp_wav = output_file.replace(".mp3", ".wav")
        tts.save_audio(audio_array, temp_wav)

        # 스타일별 속도/음량 ffmpeg 필터 연산
        style_key = getattr(voice_config, "style", "🎤 기본")
        style_info = VOICE_STYLES.get(style_key, {})
        rate_val = style_info.get("rate", 0)
        vol_val = style_info.get("volume", 0)

        audio_filters = []
        tempo = 1.0 + (rate_val / 100.0)
        if 0.5 <= tempo <= 2.0 and abs(tempo - 1.0) > 0.02:
            audio_filters.append(f"atempo={tempo:.2f}")

        if vol_val != 0:
            vol_db = vol_val / 5.0
            audio_filters.append(f"volume={vol_db:+.1f}dB")

        # ffmpeg로 mp3 고품질 인코딩
        cmd = ["ffmpeg", "-y", "-i", temp_wav]
        if audio_filters:
            cmd.extend(["-filter:a", ",".join(audio_filters)])
        cmd.extend(["-b:a", "192k", output_file])

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except Exception:
                pass

        return output_file

    @classmethod
    async def generate_gemini_speech_async(
        cls,
        text: str,
        output_file: str,
        voice_config: VoiceConfig,
        retries: int = 3
    ) -> str:
        """
        Gemini 3.1 / 2.5 / 2.0 Flash TTS 오디오 합성 (스타일 감정 연기 지시문 주입 및 자동 폴백)
        """
        import wave
        text = clean_spoken_text(text)
        from google import genai
        from google.genai import types

        raw_key = voice_config.api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not raw_key:
            raise ValueError("Gemini API 키가 필요합니다. 사이드바에 키를 입력해주세요.")

        # 다중 API 키 지원: 쉼표(,), 공백, 줄바꿈으로 구분된 여러 개의 키 추출
        api_keys = [k.strip() for k in re.split(r'[,;\s\n]+', raw_key) if k.strip()]
        if not api_keys:
            raise ValueError("유효한 Gemini API 키가 없습니다. 사이드바에 올바른 키를 입력해주세요.")

        # 키 순환 및 로드 밸런싱 (화자/세그먼트별 분산으로 15 RPM 한도 도달 방지)
        cls._gemini_key_counter = (getattr(cls, "_gemini_key_counter", 0) + 1) % len(api_keys)
        start_idx = cls._gemini_key_counter
        ordered_keys = [api_keys[(start_idx + i) % len(api_keys)] for i in range(len(api_keys))]

        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

        v_name = voice_config.voice if voice_config.voice in GEMINI_VOICES else "Kore"

        style_key = getattr(voice_config, "style", "🎤 기본")
        style_info = VOICE_STYLES.get(style_key, {})
        style_prompt = style_info.get("gemini_prompt", "Read clearly, naturally, and expressively.")

        # CRITICAL: contents에는 오직 낭독해야 할 한국어 대본 텍스트만 전달!
        if style_key == "🎤 기본":
            sys_instruct = (
                "You are an expert Korean voice actor. "
                "Read the Korean script verbatim with clear, natural pronunciation. "
                "Do NOT speak any introductory phrases, English words, instructions, or meta-commentary aloud."
            )
        else:
            sys_instruct = (
                "You are an expert Korean voice actor performing a script for an audiobook or video. "
                f"Emotion and performance style: {style_prompt}. "
                "CRITICAL INSTRUCTION: Speak ONLY the exact Korean text given in the input. "
                "Do NOT speak instructions, notes, or English words aloud under any circumstances. "
                "Output purely the voiced Korean narration or dialogue."
            )

        # 사용 가능한 공식 Google Gemini TTS 모델 목록 (무료/유료 공용 Flash 모델 우선)
        VALID_GEMINI_TTS_MODELS = [
            "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-preview-tts",
        ]

        req_model = (voice_config.model or "").strip()
        models_to_try = []
        if req_model:
            models_to_try.append(req_model)
        for m in VALID_GEMINI_TTS_MODELS:
            if m not in models_to_try:
                models_to_try.append(m)

        last_err = None
        loop = asyncio.get_event_loop()

        # 1단계: 등록된 API 키들을 순회하며 생성 시도 (로드밸런싱 및 즉각 키 전환)
        for current_key in ordered_keys:
            client = genai.Client(api_key=current_key)
            for current_model in models_to_try:
                for attempt in range(1, retries + 1):
                    try:
                        def _call_gemini(m=current_model, c=client):
                            return c.models.generate_content(
                                model=m,
                                contents=text,
                                config=types.GenerateContentConfig(
                                    response_modalities=["AUDIO"],
                                    speech_config=types.SpeechConfig(
                                        voice_config=types.VoiceConfig(
                                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                                voice_name=v_name
                                            )
                                        )
                                    )
                                )
                            )

                        response = await loop.run_in_executor(None, _call_gemini)

                        audio_bytes = None
                        rejection_msgs = []
                        if response and response.candidates:
                            for candidate in response.candidates:
                                if candidate.finish_reason and str(candidate.finish_reason) not in ("FinishReason.STOP", "1", "STOP"):
                                    rejection_msgs.append(f"종료 사유: {candidate.finish_reason}")
                                if candidate.content and candidate.content.parts:
                                    for part in candidate.content.parts:
                                        if getattr(part, "inline_data", None) and part.inline_data.data:
                                            audio_bytes = part.inline_data.data
                                            break
                                        elif getattr(part, "text", None):
                                            rejection_msgs.append(f"텍스트 응답: {part.text[:60]}")
                                    if audio_bytes:
                                        break

                        if not audio_bytes:
                            err_detail = "; ".join(rejection_msgs) if rejection_msgs else "오디오 데이터 없음"
                            raise RuntimeError(f"모델 '{current_model}'에서 오디오 미수신 ({err_detail})")

                        # PCM 또는 WAV 오디오 데이터 MP3로 변환
                        temp_wav = output_file + f".{attempt}.wav"
                        if audio_bytes.startswith(b"RIFF"):
                            with open(temp_wav, "wb") as f:
                                f.write(audio_bytes)
                        else:
                            # Google Gemini TTS의 기본 출력은 24kHz 16비트 모노 PCM
                            with wave.open(temp_wav, "wb") as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)
                                wf.setframerate(24000)
                                wf.writeframes(audio_bytes)

                        # ffmpeg로 mp3 표준화 변환
                        cmd = ["ffmpeg", "-y", "-i", temp_wav, "-b:a", "192k", output_file]
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

                        if os.path.exists(temp_wav):
                            try:
                                os.remove(temp_wav)
                            except Exception:
                                pass

                        return output_file
                    except Exception as e:
                        last_err = e
                        err_str = str(e)
                        # 만약 Pro 모델에서 무료 키(limit: 0) 오류가 발생하면, 즉시 Flash 모델로 전환
                        if "limit: 0" in err_str or ("pro" in current_model.lower() and ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str)):
                            break
                        # 404: 만료/미지원 모델은 재시도하지 않고 다음 후보 모델로 즉시 건너뜀
                        if "404" in err_str or "NOT_FOUND" in err_str or "no longer available" in err_str:
                            break
                        # 429: 분당 쿼터 초과 시 다른 등록된 키가 있다면 즉시 다음 키로 전환
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "Quota exceeded" in err_str:
                            break
                        # 500 INTERNAL: 구글 프리뷰 일시 오류 재시도
                        if "500" in err_str or "INTERNAL" in err_str:
                            if attempt < retries:
                                await asyncio.sleep(1.0 * attempt)
                                continue
                            else:
                                break
                        if attempt < retries:
                            await asyncio.sleep(0.5)

                # 현재 키가 429 한도에 걸렸다면 다음 모델 대신 다음 API 키로 즉시 전환
                if last_err and any(k in str(last_err) for k in ("429", "RESOURCE_EXHAUSTED", "Quota exceeded")):
                    if not ("limit: 0" in str(last_err) and "pro" in str(last_err).lower()):
                        break

        # 2단계: 모든 키에서 429 쿼터 한도가 발생한 경우
        # (구글의 15 RPM 한도는 60초 롤링 윈도우이므로, 10초/15초/25초 대기하며 만료 즉시 자동 복구 재시도)
        if last_err and any(k in str(last_err) for k in ("429", "RESOURCE_EXHAUSTED", "Quota exceeded")):
            if not ("limit: 0" in str(last_err) and "pro" in str(last_err).lower()):
                fallback_candidates = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]
                for wait_sec in [10.0, 15.0, 25.0]:
                    await asyncio.sleep(wait_sec)
                    for key_candidate in ordered_keys:
                        fb_client = genai.Client(api_key=key_candidate)
                        for fallback_model in fallback_candidates:
                            try:
                                def _call_fb(m=fallback_model, c=fb_client):
                                    return c.models.generate_content(
                                        model=m,
                                        contents=text,
                                        config=types.GenerateContentConfig(
                                            response_modalities=["AUDIO"],
                                            speech_config=types.SpeechConfig(
                                                voice_config=types.VoiceConfig(
                                                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                                        voice_name=v_name
                                                    )
                                                )
                                            )
                                        )
                                    )
                                response = await loop.run_in_executor(None, _call_fb)
                                audio_bytes = None
                                if response and response.candidates:
                                    for candidate in response.candidates:
                                        if candidate.content and candidate.content.parts:
                                            for part in candidate.content.parts:
                                                if getattr(part, "inline_data", None) and part.inline_data.data:
                                                    audio_bytes = part.inline_data.data
                                                    break
                                        if audio_bytes:
                                            break
                                if audio_bytes:
                                    temp_wav = output_file + ".retry.wav"
                                    if audio_bytes.startswith(b"RIFF"):
                                        with open(temp_wav, "wb") as f:
                                            f.write(audio_bytes)
                                    else:
                                        with wave.open(temp_wav, "wb") as wf:
                                            wf.setnchannels(1)
                                            wf.setsampwidth(2)
                                            wf.setframerate(24000)
                                            wf.writeframes(audio_bytes)
                                    cmd = ["ffmpeg", "-y", "-i", temp_wav, "-b:a", "192k", output_file]
                                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                                    if os.path.exists(temp_wav):
                                        try:
                                            os.remove(temp_wav)
                                        except Exception:
                                            pass
                                    return output_file
                            except Exception as fb_err:
                                last_err = fb_err
                                if "429" not in str(fb_err) and "RESOURCE_EXHAUSTED" not in str(fb_err):
                                    break

        if last_err:
            err_str = str(last_err)
            if any(k in err_str for k in ("429", "RESOURCE_EXHAUSTED", "Quota exceeded")):
                if "limit: 0" in err_str and "pro" in err_str.lower():
                    raise RuntimeError(
                        "선택하신 'Gemini 2.5 Pro TTS' 모델은 Google Cloud 유료 결제(Billing) 계정 전용 모델입니다.\n"
                        "현재 사용 중이신 무료 API 키에서는 한도가 0(limit: 0)으로 설정되어 있습니다.\n"
                        "▶ 해결 방법: 사이드바에서 무료 지원 모델인 '⚡ Gemini 3.1 Flash'를 선택하시거나, 완전 무료인 'Supertonic 3' 엔진을 사용해주세요."
                    )
                raise RuntimeError(
                    "Gemini API 요청 한도(무료 키 기준 15 RPM 또는 일일 쿼터)에 도달했습니다.\n"
                    "💡 해결 방법:\n"
                    "1. Google AI Studio(aistudio.google.com)에서 무료 API 키를 1~2개 더 발급받아, 사이드바 키 입력창에 쉼표(,)로 구분해 여러 개 등록하시면(예: 키1, 키2) 즉시 한도가 늘어나 무제한 연속 생성이 가능합니다.\n"
                    "2. 또는 분당 요청 제한이 전혀 없는 100% 무제한 무료 오프라인 고속 엔진 '👑 Supertonic 3'을 사용해주세요."
                )
            if "500" in err_str or "INTERNAL" in err_str:
                raise RuntimeError(
                    "Google Gemini 서버에서 일시적 내부 오류(500 INTERNAL)가 발생했습니다.\n"
                    "구글 TTS 프리뷰 서버의 일시적 지연일 수 있으니 잠시 후 다시 시도해주시거나,\n"
                    "서버 통신 오류가 없는 완전 무료 오프라인 엔진 '👑 Supertonic' 또는 '🌐 Edge-TTS'를 사용해주세요."
                )

        raise last_err or RuntimeError("모든 Gemini TTS 모델 시도 실패")

    @classmethod
    async def generate_edge_speech_async(
        cls,
        text: str,
        output_file: str,
        voice_config: VoiceConfig,
        retries: int = 3
    ) -> str:
        """
        Edge-TTS 기반 무료 비동기 음성 생성 (스타일 피치/속도 보정 반영)
        """
        text = clean_spoken_text(text)
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        
        # 스타일 보정치 계산
        style_key = getattr(voice_config, "style", "🎤 기본")
        style_info = VOICE_STYLES.get(style_key, {})
        s_rate = style_info.get("rate", 0)
        s_pitch = style_info.get("pitch", 0)

        base_rate = 0
        try:
            r_str = str(voice_config.rate).replace("%", "").replace("+", "").strip()
            base_rate = int(r_str) if r_str else 0
        except Exception:
            pass

        base_pitch = 0
        try:
            p_str = str(voice_config.pitch).replace("Hz", "").replace("+", "").strip()
            base_pitch = int(p_str) if p_str else 0
        except Exception:
            pass

        final_rate = max(-50, min(100, base_rate + s_rate))
        final_pitch = max(-50, min(50, base_pitch + s_pitch))

        rate_param = f"{final_rate:+d}%"
        pitch_param = f"{final_pitch:+d}Hz"

        last_err = None
        for attempt in range(1, retries + 1):
            try:
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=voice_config.voice,
                    rate=rate_param,
                    pitch=pitch_param,
                    volume=voice_config.volume
                )
                await communicate.save(output_file)
                return output_file
            except Exception as e:
                last_err = e
                if attempt < retries:
                    await asyncio.sleep(1.0)
                else:
                    raise last_err

        raise last_err

    @classmethod
    def test_f5_tts_connection(cls, api_url: str = "http://127.0.0.1:7860") -> Tuple[bool, str]:
        """
        Pinokio F5-TTS API 서버 연결 가능 여부 테스트
        """
        import requests
        base_url = api_url.rstrip("/")
        try:
            res = requests.get(f"{base_url}/config", timeout=3)
            if res.status_code == 200:
                return True, f"Pinokio F5-TTS 서버({base_url})에 성공적으로 연결되었습니다!"
            return True, f"Pinokio F5-TTS 서버({base_url}) 응답 확인 완료!"
        except requests.exceptions.ConnectionError:
            return False, f"서버 연결 실패: '{base_url}' 주소에 응답하는 F5-TTS 서버가 없습니다. (포트 7860 실행 여부를 확인해주세요)"
        except Exception as e:
            return False, f"서버 연결 실패: {str(e)}"

    @classmethod
    def generate_f5_tts_speech(
        cls,
        text: str,
        output_file: str,
        voice_config: VoiceConfig,
        retries: int = 2
    ) -> str:
        """
        Pinokio F5-TTS (Gradio API)를 통한 음성 합성 (Voice Cloning)
        """
        text = clean_spoken_text(text)
        if not text:
            raise ValueError("생성할 텍스트가 비어 있습니다.")

        ref_path = getattr(voice_config, "ref_audio_path", "")
        if not ref_path:
            raise ValueError("참조 오디오(.wav 또는 .mp3) 파일이 지정되지 않았습니다.")
        if not os.path.exists(ref_path):
            raise FileNotFoundError(f"참조 오디오 파일을 찾을 수 없습니다: '{ref_path}'")
        if os.path.isdir(ref_path):
            raise IsADirectoryError(f"입력하신 경로('{ref_path}')는 파일이 아니라 폴더입니다.")

        actual_ref_path = os.path.abspath(ref_path)
        prompt_txt = getattr(voice_config, "prompt_text", "").strip()

        # prompt_txt가 비어있으면 저장된 .txt 캐시나 Whisper로 자동 분석
        if not prompt_txt:
            txt_cache = actual_ref_path + ".txt"
            if os.path.exists(txt_cache):
                try:
                    with open(txt_cache, "r", encoding="utf-8") as cf:
                        prompt_txt = cf.read().strip()
                except Exception:
                    pass
            if not prompt_txt:
                prompt_txt = cls.transcribe_audio_whisper(actual_ref_path)

        api_url = getattr(voice_config, "f5_tts_url", "http://127.0.0.1:7860") or "http://127.0.0.1:7860"
        api_url = api_url.rstrip("/")

        speed_val = float(getattr(voice_config, "speed_factor", 1.0) or 1.0)
        nfe_val = int(getattr(voice_config, "nfe_steps", 32) or 32)

        from gradio_client import Client, handle_file

        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        last_err = None

        for attempt in range(1, retries + 1):
            try:
                client = Client(api_url, verbose=False)
                res = client.predict(
                    ref_audio_input=handle_file(actual_ref_path),
                    ref_text_input=prompt_txt,
                    gen_text_input=text,
                    remove_silence=False,
                    randomize_seed=True,
                    seed_input=0,
                    cross_fade_duration_slider=0.15,
                    nfe_slider=nfe_val,
                    speed_slider=speed_val,
                    api_name="/basic_tts"
                )
                if not res or not res[0] or not os.path.exists(res[0]):
                    raise RuntimeError("F5-TTS 음성 파일 생성 실패 (결과 파일 없음)")

                temp_audio = res[0]
                if output_file.lower().endswith(".mp3"):
                    cmd = ["ffmpeg", "-y", "-i", temp_audio, "-b:a", "192k", output_file]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                else:
                    shutil.copy2(temp_audio, output_file)

                return output_file
            except Exception as e:
                last_err = e
                if attempt == retries:
                    raise last_err
                time.sleep(1.0)

    @classmethod
    def test_cosyvoice_connection(cls, url: str) -> Tuple[bool, str]:
        if not url:
            return False, "CosyVoice 주소를 입력해주세요."
        import requests
        clean_url = url.rstrip("/")
        try:
            res = requests.get(clean_url, timeout=5)
            return True, f"✅ CosyVoice 서버({clean_url})에 성공적으로 연결되었습니다!"
        except Exception as e:
            return False, f"서버 연결 실패: '{clean_url}' 에 연결할 수 없습니다. (구글 코랩 실행 상태를 확인해주세요: {str(e)})"

    @classmethod
    def test_xtts_connection(cls, url: str) -> Tuple[bool, str]:
        if not url:
            return False, "XTTS v2 주소를 입력해주세요."
        import requests
        clean_url = url.rstrip("/")
        try:
            res = requests.get(clean_url, timeout=5)
            return True, f"✅ XTTS v2 서버({clean_url})에 성공적으로 연결되었습니다!"
        except Exception as e:
            return False, f"서버 연결 실패: '{clean_url}' 에 연결할 수 없습니다. (구글 코랩 실행 상태를 확인해주세요: {str(e)})"

    @classmethod
    def generate_cosyvoice_speech(
        cls,
        text: str,
        output_file: str,
        voice_config: VoiceConfig,
        retries: int = 2
    ) -> str:
        """
        CosyVoice 3.0 / 2.0 (Google Colab Gradio API)를 통한 음성 합성 (Voice Cloning)
        """
        text = clean_spoken_text(text)
        if not text:
            raise ValueError("생성할 텍스트가 비어 있습니다.")

        ref_path = getattr(voice_config, "ref_audio_path", "")
        if not ref_path:
            raise ValueError("참조 오디오(.wav 또는 .mp3) 파일이 지정되지 않았습니다.")
        if not os.path.exists(ref_path):
            raise FileNotFoundError(f"참조 오디오 파일을 찾을 수 없습니다: '{ref_path}'")

        actual_ref_path = os.path.abspath(ref_path)
        prompt_txt = getattr(voice_config, "prompt_text", "").strip()
        if not prompt_txt:
            txt_cache = actual_ref_path + ".txt"
            if os.path.exists(txt_cache):
                try:
                    with open(txt_cache, "r", encoding="utf-8") as cf:
                        prompt_txt = cf.read().strip()
                except Exception:
                    pass
            if not prompt_txt:
                prompt_txt = cls.transcribe_audio_whisper(actual_ref_path)

        api_url = getattr(voice_config, "cosyvoice_url", "")
        if not api_url:
            raise ValueError("CosyVoice 코랩 접속 주소(URL)를 입력해주세요.")
        api_url = api_url.rstrip("/")

        speed_val = float(getattr(voice_config, "speed_factor", 1.0) or 1.0)

        from gradio_client import Client, handle_file
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        last_err = None

        for attempt in range(1, retries + 1):
            try:
                client = Client(api_url, verbose=False)
                try:
                    res = client.predict(
                        text,                          # tts_text
                        "3s极速复刻",                  # mode_checkbox_group
                        "",                            # sft_dropdown
                        prompt_txt,                    # prompt_text
                        handle_file(actual_ref_path),  # prompt_wav_upload
                        None,                          # prompt_wav_record
                        "",                            # instruct_text
                        0,                             # seed
                        False,                         # stream (bool)
                        speed_val,                     # speed
                        api_name="/generate_audio"
                    )
                except Exception:
                    res = client.predict(
                        tts_text=text,
                        mode_checkbox_group="3s极速复刻",
                        sft_dropdown="",
                        prompt_text=prompt_txt,
                        prompt_wav_upload=handle_file(actual_ref_path),
                        prompt_wav_record=None,
                        instruct_text="",
                        seed=0,
                        stream=False,
                        speed=speed_val,
                        api_name="/generate_audio"
                    )

                if not res:
                    raise RuntimeError("CosyVoice 음성 파일 생성 실패 (결과 없음)")

                temp_audio = res[0] if isinstance(res, (list, tuple)) else res
                if output_file.lower().endswith(".mp3"):
                    cmd = ["ffmpeg", "-y", "-i", temp_audio, "-b:a", "192k", output_file]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                else:
                    shutil.copy2(temp_audio, output_file)

                return output_file
            except Exception as e:
                last_err = e
                if attempt == retries:
                    raise last_err
                time.sleep(1.0)

    @classmethod
    def generate_xtts_speech(
        cls,
        text: str,
        output_file: str,
        voice_config: VoiceConfig,
        retries: int = 2
    ) -> str:
        """
        XTTS v2 (Google Colab Gradio API)를 통한 음성 합성 (Voice Cloning)
        """
        text = clean_spoken_text(text)
        if not text:
            raise ValueError("생성할 텍스트가 비어 있습니다.")

        ref_path = getattr(voice_config, "ref_audio_path", "")
        if not ref_path:
            raise ValueError("참조 오디오(.wav 또는 .mp3) 파일이 지정되지 않았습니다.")
        if not os.path.exists(ref_path):
            raise FileNotFoundError(f"참조 오디오 파일을 찾을 수 없습니다: '{ref_path}'")

        actual_ref_path = os.path.abspath(ref_path)
        api_url = getattr(voice_config, "xtts_url", "")
        if not api_url:
            raise ValueError("XTTS v2 코랩 접속 주소(URL)를 입력해주세요.")
        api_url = api_url.rstrip("/")

        from gradio_client import Client, handle_file
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        last_err = None

        for attempt in range(1, retries + 1):
            try:
                client = Client(api_url, verbose=False)
                res = client.predict(
                    handle_file(actual_ref_path),
                    text,
                    "ko",
                    api_name="/predict"
                )
                if not res:
                    raise RuntimeError("XTTS v2 음성 파일 생성 실패 (결과 없음)")

                temp_audio = res[0] if isinstance(res, (list, tuple)) else res
                if output_file.lower().endswith(".mp3"):
                    cmd = ["ffmpeg", "-y", "-i", temp_audio, "-b:a", "192k", output_file]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                else:
                    shutil.copy2(temp_audio, output_file)

                return output_file
            except Exception as e:
                last_err = e
                if attempt == retries:
                    raise last_err
                time.sleep(1.0)


    @classmethod
    def test_gpt_sovits_connection(cls, api_url: str = "http://127.0.0.1:9880") -> Tuple[bool, str]:
        """
        GPT-SoVITS API 서버 연결 가능 여부 테스트
        """
        import requests
        base_url = api_url.replace("/tts", "").rstrip("/")
        try:
            res = requests.get(f"{base_url}/", timeout=3)
            return True, f"GPT-SoVITS 서버({base_url})에 성공적으로 연결되었습니다!"
        except Exception:
            try:
                res = requests.get(f"{base_url}/tts", timeout=3)
                return True, f"GPT-SoVITS 서버({base_url}) 연결 확인 완료!"
            except requests.exceptions.ConnectionError:
                return False, f"서버 연결 실패: '{base_url}' 주소에 응답하는 GPT-SoVITS API 서버가 없습니다. (포트 9880 실행 여부를 확인해주세요)"
            except Exception as e:
                return False, f"서버 연결 실패: {str(e)}"

    @classmethod
    def generate_gpt_sovits_speech(
        cls,
        text: str,
        output_file: str,
        voice_config: VoiceConfig,
        retries: int = 2
    ) -> str:
        """
        GPT-SoVITS 로컬/원격 API를 통한 음성 합성 (Voice Cloning)
        """
        text = clean_spoken_text(text)
        import requests
        api_url = getattr(voice_config, "gpt_sovits_url", "http://127.0.0.1:9880/tts")
        if not api_url.endswith("/tts"):
            api_url = api_url.rstrip("/") + "/tts"

        ref_path = getattr(voice_config, "ref_audio_path", "")
        if not ref_path:
            raise ValueError("참조 오디오(.wav 또는 .mp3) 파일이 지정되지 않았습니다.")
        if not os.path.exists(ref_path):
            raise FileNotFoundError(f"참조 오디오 파일을 찾을 수 없습니다: '{ref_path}'")
        if os.path.isdir(ref_path):
            raise IsADirectoryError(
                f"입력하신 경로('{ref_path}')는 파일이 아니라 폴더(디렉토리)입니다.\n"
                f"폴더 안의 실제 오디오 파일(예: sample.wav 또는 참고 TTS.wav) 전체 경로를 입력해주세요."
            )

        import hashlib
        actual_ref_path = os.path.abspath(ref_path)
        ref_file_hash = "default"
        try:
            with open(actual_ref_path, "rb") as rf:
                ref_raw_bytes = rf.read()
            ref_file_hash = hashlib.md5(ref_raw_bytes).hexdigest()[:12]
        except Exception:
            pass

        was_trimmed = False
        try:
            import soundfile as sf
            import numpy as np
            info = sf.info(actual_ref_path)
            
            cache_dir = os.path.join(os.path.dirname(os.path.abspath(output_file)), "ref_cache")
            os.makedirs(cache_dir, exist_ok=True)
            safe_base = os.path.splitext(os.path.basename(actual_ref_path))[0]
            safe_base = "".join(c for c in safe_base if c.isalnum() or c in ('_', '-'))
            trimmed_path = os.path.join(cache_dir, f"{safe_base}_{ref_file_hash}_norm.wav")

            # 1. 음원 로드 및 모노 변환
            data, sr = sf.read(actual_ref_path)
            if data.ndim > 1:
                data = np.mean(data, axis=1)

            # 2. 음원 길이 최적화 (10초 초과 시에만 5.0초 ~ 8.5초 사이의 무음 밸리 탐색)
            duration = len(data) / sr
            if duration > 10.0:
                win = int(sr * 0.1)
                hop = int(sr * 0.05)
                rms = np.array([np.sqrt(np.mean(data[i:i+win]**2)) for i in range(0, len(data)-win, hop)])
                times = np.arange(len(rms)) * (hop / sr)
                
                mask = (times >= 5.0) & (times <= 8.5)
                if np.any(mask):
                    sub_rms = rms[mask]
                    sub_times = times[mask]
                    best_cut_sec = float(sub_times[np.argmin(sub_rms)])
                else:
                    best_cut_sec = min(8.0, duration)
                data = data[:int(best_cut_sec * sr)]
                was_trimmed = True

            # 3. 음량 정규화 (-1.0 dBFS Peak Normalization)
            max_amp = np.max(np.abs(data))
            if max_amp > 0.001:
                data = (data / max_amp) * 0.891  # 0.891 = -1.0 dBFS

            sf.write(trimmed_path, data, sr)
            actual_ref_path = os.path.abspath(trimmed_path)
        except Exception:
            was_trimmed = False

        # 4. prompt_text 정밀 동기화 (Whisper 검증)
        user_prompt_txt = getattr(voice_config, "prompt_text", "").strip()
        txt_cache = actual_ref_path + ".txt"
        
        # 실제 음원을 Whisper로 직접 청취하여 정답 텍스트 추출
        real_spoken_txt = ""
        if os.path.exists(txt_cache):
            try:
                with open(txt_cache, "r", encoding="utf-8") as cf:
                    real_spoken_txt = cf.read().strip()
            except Exception:
                pass
        if not real_spoken_txt:
            real_spoken_txt = cls.transcribe_audio_whisper(actual_ref_path)
            if real_spoken_txt:
                try:
                    with open(txt_cache, "w", encoding="utf-8") as cf:
                        cf.write(real_spoken_txt)
                except Exception:
                    pass

        # 검증: 음원이 10초 초과로 잘린 경우(was_trimmed),
        # 반드시 잘린 음원에 실제로 발음된 real_spoken_txt를 사용하여 텍스트-음소 불일치 뭉개짐 방지
        if was_trimmed and real_spoken_txt:
            prompt_txt = real_spoken_txt
        elif user_prompt_txt:
            prompt_txt = user_prompt_txt
        elif real_spoken_txt:
            prompt_txt = real_spoken_txt
        else:
            prompt_txt = ""

        # prompt_txt의 실제 언어 자동 감지 (한글이 없고 알파벳이면 en으로 자동 전환하여 파열음/잡음 차단)
        raw_p_lang = getattr(voice_config, "prompt_lang", "ko")
        target_p_lang = "all_ko" if raw_p_lang == "ko" else raw_p_lang
        has_ko = bool(re.search(r'[가-힣]', prompt_txt))
        has_en = bool(re.search(r'[a-zA-Z]', prompt_txt))
        if has_en and not has_ko:
            target_p_lang = "all_en"
        elif has_ko:
            target_p_lang = "all_ko"

        raw_t_lang = getattr(voice_config, "text_lang", "ko")
        target_t_lang = "all_ko" if raw_t_lang == "ko" else raw_t_lang

        speed_val = float(getattr(voice_config, "speed_factor", 0.95))
        if speed_val <= 0:
            speed_val = 0.95

        temp_val = float(getattr(voice_config, "temperature", 0.65))
        if temp_val <= 0 or temp_val > 1.2:
            temp_val = 0.65
        top_k_val = int(getattr(voice_config, "top_k", 5))
        top_p_val = float(getattr(voice_config, "top_p", 0.85))

        optimized_text = optimize_text_for_sovits(text, max_chunk_len=40)
        split_method = getattr(voice_config, "text_split_method", "cut5") or "cut5"
        frag_interval = float(getattr(voice_config, "fragment_interval", 0.2))

        # 로컬 서버 vs 원격(Colab) 서버 최적 전송 전략
        is_local_api = any(h in api_url for h in ["127.0.0.1", "localhost"])
        
        payload = {
            "text": optimized_text,
            "text_lang": target_t_lang,
            "prompt_text": prompt_txt,
            "prompt_lang": target_p_lang,
            "text_split_method": split_method,
            "speed_factor": speed_val,
            "top_k": top_k_val,
            "top_p": top_p_val,
            "temperature": temp_val,
            "repetition_penalty": 1.35,
            "fragment_interval": frag_interval,
            "parallel_infer": True
        }

        if is_local_api:
            # 로컬 서버: 디스크의 고유 해시 파일 경로를 직접 전달
            # (base64 왕복 인코딩 오버헤드 0%, GPT-SoVITS 캐시 갱신 100% 보장)
            payload["ref_audio_path"] = actual_ref_path
        else:
            # 원격 서버(Colab): base64 인코딩 전달 및 고유 해시 파일명 부여
            payload["ref_audio_path"] = f"remote_ref_{ref_file_hash}.wav"
            try:
                if os.path.exists(actual_ref_path):
                    import base64
                    with open(actual_ref_path, "rb") as rf:
                        payload["ref_audio_base64"] = base64.b64encode(rf.read()).decode("utf-8")
            except Exception:
                pass

        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                res = requests.post(api_url, json=payload, timeout=90)
                audio_bytes = None
                if res.status_code == 200 and len(res.content) > 100:
                    audio_bytes = res.content
                else:
                    if res.status_code == 502:
                        raise RuntimeError(
                            "GPT-SoVITS 서버 응답 없음 (502 Bad Gateway).\n"
                            "원인: Google Colab에서 AI 서버가 아직 준비 중이거나 실행되지 않았습니다.\n"
                            "해결: 구글 코랩의 4단계 셀이 완전히 실행 완료될 때까지 기다린 후 다시 시도해주세요."
                        )
                    if "pos" in res.text:
                        raise RuntimeError(
                            "GPT-SoVITS 한국어 형태소 분석기(Mecab) 미적용 오류.\n"
                            "▶ 해결 방법: 구글 코랩 상단 메뉴 [런타임] -> [세션 다시 시작 및 모두 실행]을 누르신 후 새로 발급된 주소를 입력해주세요."
                        )
                    else:
                        raise RuntimeError(f"GPT-SoVITS API 오류 (상태코드: {res.status_code}): {res.text[:200]}")

                if audio_bytes:
                    if output_file.lower().endswith(".mp3"):
                        temp_wav = output_file + ".temp.wav"
                        with open(temp_wav, "wb") as f:
                            f.write(audio_bytes)
                        cmd = ["ffmpeg", "-y", "-i", temp_wav, "-b:a", "192k", output_file]
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                        if os.path.exists(temp_wav):
                            try:
                                os.remove(temp_wav)
                            except Exception:
                                pass
                    else:
                        with open(output_file, "wb") as f:
                            f.write(audio_bytes)
                    return output_file
            except requests.exceptions.ConnectionError:
                raise ConnectionError(
                    f"GPT-SoVITS API 서버({api_url})에 연결할 수 없습니다.\n"
                    f"로컬에서 GPT-SoVITS API 서버가 실행 중인지 확인해주세요.\n"
                    f"(명령어: python api_v2.py -a 127.0.0.1 -p 9880)"
                )
            except Exception as e:
                last_err = e
                if attempt == retries:
                    raise last_err
        raise last_err

    @classmethod
    def transcribe_audio_whisper(cls, audio_path: str) -> str:
        """
        GPT-SoVITS 로컬 런타임에 내장된 faster-whisper를 이용해
        참조 오디오의 실제 대사를 자동으로 텍스트 추출합니다.
        """
        python_exe = r"C:\Users\user\Downloads\GPT-SoVITS-v2pro-20250604\GPT-SoVITS-v2pro-20250604\runtime\python.exe"
        sovits_dir = r"C:\Users\user\Downloads\GPT-SoVITS-v2pro-20250604\GPT-SoVITS-v2pro-20250604"
        if not os.path.exists(python_exe) or not os.path.exists(audio_path):
            return ""
        
        py_code = (
            "import sys\n"
            f"sys.path.append(r'{sovits_dir}')\n"
            "from faster_whisper import WhisperModel\n"
            "model = WhisperModel('small', device='cpu', compute_type='int8')\n"
            f"segments, _ = model.transcribe(r'{os.path.abspath(audio_path)}', language='ko')\n"
            "sys.stdout.reconfigure(encoding='utf-8')\n"
            "print(''.join(s.text for s in segments).strip())\n"
        )
        try:
            res = subprocess.run(
                [python_exe, "-c", py_code],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=30
            )
            return res.stdout.strip()
        except Exception:
            return ""

    @classmethod
    async def generate_speech_async(
        cls,
        text: str,
        output_file: str,
        voice_config: Optional[VoiceConfig] = None
    ) -> str:
        if not voice_config:
            voice_config = VoiceConfig()

        if voice_config.engine == "supertonic":
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: cls.generate_supertonic_speech(text, output_file, voice_config))
        elif voice_config.engine == "f5-tts":
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: cls.generate_f5_tts_speech(text, output_file, voice_config))
        elif voice_config.engine == "cosyvoice":
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: cls.generate_cosyvoice_speech(text, output_file, voice_config))
        elif voice_config.engine == "xtts":
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: cls.generate_xtts_speech(text, output_file, voice_config))
        elif voice_config.engine == "gpt-sovits":
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: cls.generate_gpt_sovits_speech(text, output_file, voice_config))
        elif voice_config.engine == "gemini":
            return await cls.generate_gemini_speech_async(text, output_file, voice_config)
        else:
            return await cls.generate_edge_speech_async(text, output_file, voice_config)

    @classmethod
    def generate_speech(
        cls,
        text: str,
        output_file: str,
        voice_config: Optional[VoiceConfig] = None
    ) -> str:
        """
        동기 방식으로 음성 생성 호출
        """
        text = clean_spoken_text(text)
        if voice_config and voice_config.engine in ("supertonic", "gpt-sovits", "f5-tts", "cosyvoice", "xtts"):
            if voice_config.engine == "supertonic":
                return cls.generate_supertonic_speech(text, output_file, voice_config)
            elif voice_config.engine == "f5-tts":
                return cls.generate_f5_tts_speech(text, output_file, voice_config)
            elif voice_config.engine == "cosyvoice":
                return cls.generate_cosyvoice_speech(text, output_file, voice_config)
            elif voice_config.engine == "xtts":
                return cls.generate_xtts_speech(text, output_file, voice_config)
            else:
                return cls.generate_gpt_sovits_speech(text, output_file, voice_config)

        coro = cls.generate_speech_async(text, output_file, voice_config)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(coro)).result()

    @classmethod
    def generate_preview(
        cls,
        voice_config: Optional[VoiceConfig] = None,
        output_file: Optional[str] = None,
        sample_text: str = "안녕하십니까. 이번 이야기의 목소리를 맡았습니다.",
        **kwargs
    ) -> str:
        sample_text = clean_spoken_text(sample_text)
        if not isinstance(voice_config, VoiceConfig):
            voice = kwargs.get("voice", voice_config)
            output_file = kwargs.get("output_file", output_file)
            sample_text = kwargs.get("sample_text", sample_text)
            rate = kwargs.get("rate", "+0%")
            pitch = kwargs.get("pitch", "+0Hz")
            style = kwargs.get("style", "🎤 기본")
            
            eng = kwargs.get("engine")
            if not eng:
                eng = "supertonic" if voice in SUPERTONIC_VOICES else ("gemini" if voice in GEMINI_VOICES else "edge-tts")

            voice_config = VoiceConfig(
                engine=eng,
                voice=str(voice),
                rate=str(rate),
                pitch=str(pitch),
                style=str(style),
                cosyvoice_url=kwargs.get("cosyvoice_url", ""),
                xtts_url=kwargs.get("xtts_url", ""),
                gpt_sovits_url=kwargs.get("gpt_sovits_url", "http://127.0.0.1:9880/tts"),
                f5_tts_url=kwargs.get("f5_tts_url", "http://127.0.0.1:7860"),
                ref_audio_path=kwargs.get("ref_audio_path", ""),
                prompt_text=kwargs.get("prompt_text", ""),
                speed_factor=kwargs.get("speed_factor", 1.0),
                nfe_steps=kwargs.get("nfe_steps", 32)
            )
        return cls.generate_speech(sample_text, output_file, voice_config)
