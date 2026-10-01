"""Selectable Korean voice designs; references are prepared once, not per line."""
BANK_REVISION = "korean-cast-20261001-v1"
REFERENCE_TEXT = "오늘은 마을에 전해 내려오는 이야기를 들려드리겠습니다. 사람들은 저마다의 사연을 품고 살아가고 있었습니다."
DESIGN_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
BASE_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"

# These are designed voice profiles, not extra pretrained CustomVoice speaker IDs.
# A single cached reference per profile anchors the voice across all dialogue.
_ROWS = [
    ("M01", "남성", "도윤 · 차분한 청년", "20대 · 부드러운 중저음", "A Korean man in his late twenties, a smooth warm low tenor, gentle breath support, measured clear delivery."),
    ("M02", "남성", "지후 · 밝은 청년", "20대 · 밝고 가벼운 중음", "A Korean man in his early twenties, a bright light tenor with an open forward resonance, lively but unhurried diction."),
    ("M03", "남성", "태준 · 단정한 선비", "30대 · 맑고 단정한 음색", "A Korean man in his thirties, a clean focused midrange tenor, refined scholarly composure, precise consonants and relaxed vowels."),
    ("M04", "남성", "건우 · 듬직한 장정", "30대 · 두텁고 힘 있는 저음", "A Korean man in his thirties, a thick resonant bass-baritone, robust chest resonance, steady understated strength without shouting."),
    ("M05", "남성", "성민 · 따뜻한 아버지", "40대 · 포근하고 깊은 중저음", "A Korean man in his forties, a mellow rounded baritone with velvety resonance, reassuring paternal warmth and easy clear diction."),
    ("M06", "남성", "진석 · 엄정한 사또", "50대 · 묵직하고 또렷한 저음", "A Korean man in his fifties, a deep sonorous bass, firm deliberate articulation, dignified authority with a calm controlled volume."),
    ("M07", "남성", "만수 · 구수한 장터 상인", "40대 · 거칠고 구수한 중음", "A Korean man in his forties, a textured lightly raspy midrange baritone, friendly conversational storytelling, rounded vowels, no exaggerated accent."),
    ("M08", "남성", "덕수 · 온화한 어르신", "60대 · 부드러운 노년 음색", "A Korean man in his late sixties, a gentle slightly weathered low baritone, soft grain and reassuring warmth, steady supported speech without shaking."),
    ("M09", "남성", "영호 · 노련한 이야기꾼", "70대 · 담백한 노년 중음", "A Korean man in his seventies, a light aged midrange voice with a dry airy edge, articulate seasoned storytelling, stable rhythm without mumbling."),
    ("M10", "남성", "시우 · 소년", "10대 후반 · 맑고 높은 음색", "A Korean teenage boy about seventeen, a youthful high tenor, clear light resonance, sincere natural delivery, no exaggerated cartoon performance."),
    ("F01", "여성", "서연 · 고운 여성 해설", "30대 · 고운 아나운서 중음", "A Korean woman in her thirties, an elegant smooth mezzo-soprano, clear broadcast diction, warm composed storytelling suitable for older listeners."),
    ("F02", "여성", "하린 · 밝은 아가씨", "20대 · 밝고 맑은 고음", "A Korean woman in her early twenties, a bright crystalline soprano with light forward resonance, cheerful clean articulation without squeakiness."),
    ("F03", "여성", "소담 · 수줍은 청년", "20대 · 가늘고 부드러운 중고음", "A Korean woman in her twenties, a delicate soft high mezzo voice, intimate gentle resonance and restrained emotion, clear diction without whispering."),
    ("F04", "여성", "연화 · 단아한 여인", "30대 · 맑고 단단한 중음", "A Korean woman in her thirties, a pure centered mezzo voice with a firm clean tone, graceful poised articulation and even unhurried cadence."),
    ("F05", "여성", "미정 · 따뜻한 어머니", "40대 · 포근하고 둥근 중저음", "A Korean woman in her forties, a warm rounded low mezzo, comforting maternal resonance with soft consonants and clearly shaped words."),
    ("F06", "여성", "정희 · 위엄 있는 안주인", "50대 · 단단하고 깊은 저음", "A Korean woman in her fifties, a rich strong contralto, composed dignified authority, precise decisive consonants without harshness."),
    ("F07", "여성", "복순 · 정겨운 이웃", "50대 · 구수하고 탄력 있는 중음", "A Korean woman in her fifties, a textured slightly husky middle register, friendly earthy resonance, natural conversational rhythm and clear speech."),
    ("F08", "여성", "순덕 · 다정한 할머니", "60대 · 포근한 노년 중저음", "A Korean woman in her late sixties, a mellow aged low mezzo with a soft velvety grain, tender storytelling, stable support without trembling."),
    ("F09", "여성", "옥분 · 옛이야기 할머니", "70대 · 담백한 노년 중고음", "A Korean woman in her seventies, a light weathered middle-high voice with a dry delicate grain, expressive traditional storytelling, clear steady words."),
    ("F10", "여성", "나래 · 소녀", "10대 후반 · 가볍고 맑은 음색", "A Korean teenage girl about seventeen, a youthful light soprano, soft clear resonance, earnest natural delivery, no exaggerated cartoon performance."),
]
VOICEBANK = {key: dict(gender=gender, name=name, description=description,
                      seed=37100 + index, instruct=direction +
                      " Speak native Korean with standard Korean pronunciation. Read only the supplied text. "
                      "Use natural clear storytelling, with no music, sound effects or background voices.")
             for index, (key, gender, name, description, direction) in enumerate(_ROWS)}
