"""Explainable style suggestions from speaker roles and their own dialogue."""
from dataclasses import dataclass
import re
import unicodedata


def _style_title(value):
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).replace("\ufe0f", "")
    value = re.sub(r"^[^A-Za-z0-9가-힣]+", "", value)
    return " ".join(value.split())


def resolve_style(value, styles, fallback="🎤 기본"):
    """Map old display labels back to catalog keys without changing the style.

    Gendered/neutral emoji are decoration, never a second style definition.
    Empty or removed choices keep the last usable configured style.
    """
    for candidate in (value, fallback, "🎤 기본"):
        if isinstance(candidate, str) and candidate in styles:
            return candidate
        title = _style_title(candidate)
        matches = [key for key in styles if title and _style_title(key) == title]
        if len(matches) == 1:
            return matches[0]
    return next(iter(styles))


def style_display_label(style):
    # Stable across gender/engine changes; the selected voice owns gender.
    return style.replace("👴", "🧓").replace("👵", "🧓")


def style_description(style, styles):
    return styles.get(resolve_style(style, styles), {}).get("desc", "선택한 음성 스타일입니다.")


@dataclass(frozen=True)
class StyleRecommendation:
    style: str
    reason: str


def speaker_gender(speaker, profile=""):
    """Use explicit identity only, never a person addressed in the dialogue."""
    for identity in (profile.strip().lower(), speaker.lower()):
        if not identity:
            continue
        male = bool(re.search(r'남성|남자|\bmale\b|\bman\b', identity))
        female = bool(re.search(r'여성|여자|\bfemale\b|\bwoman\b', identity))
        if male != female:
            return "남성" if male else "여성"
        if male and female:
            return ""
        male = bool(re.search(r'할아버|노옹|영감|아버지|남편|소년|사내|사나이|머슴', identity))
        female = bool(re.search(r'할머|노모|노파|노부인|어머니|아내|소녀|아낙|여인|낭자|유모', identity))
        if male != female:
            return "남성" if male else "여성"
        if male and female:
            return ""
    return ""


def speaker_lines(speaker, segments):
    return [seg.text for seg in segments if seg.speaker == speaker and seg.text.strip()]


def preview_text(speaker, segments):
    """Audition a line from the current script, never a different story's preset."""
    lines = speaker_lines(speaker, segments)
    if not lines:
        return "안녕하세요. 오늘도 편안하게 이야기를 들어주세요."
    text = lines[0].strip().strip('"“”')
    if len(text) > 180:
        sentences = re.split(r'(?<=[.!?。！？])\s+', text)
        if len(sentences[0]) <= 180:
            return sentences[0]
        cut = text.rfind(' ', 60, 180)
        return text[:cut if cut >= 60 else 180].rstrip(' ,') + '.'
    return text


def recommend_style(speaker, segments, profile=""):
    # Age and role come from the speaker label or an explicit profile, not from
    # someone they address (a child saying "할머니" must not become elderly).
    identity = (profile.strip() or speaker).lower()
    dialogue = ' '.join(speaker_lines(speaker, segments))[:12000]
    def has(pattern):
        return re.search(pattern, identity) is not None
    def choice(style, reason):
        return StyleRecommendation(style, reason)

    if has(r'나레이션|내레이션|해설|narrator'):
        return choice('🌙 차분하고 따뜻하게', '해설 역할에는 오래 들어도 편안하고 또렷한 낭독을 추천합니다.')
    elder = has(r'시니어|노인|할머|할아버|노모|노파|노부인|노옹|영감|어르신|[6789]0대|[6789]\d\s*세|칠순|팔순|환갑|고희')
    if elder:
        if has(r'감성|회상|그리움|슬픔') or re.search(r'그립|그리워|먼저 간|지나온 세월|그때가|옛날에는', dialogue):
            return choice('🎭 시니어 감성적인 (70대 이상)', '노년 인물의 회상과 그리움이 드러나 차분하고 감성적인 연기를 추천합니다.')
        if has(r'할머|노모|노파|노부인') or (has(r'여성|여자') and has(r'70대|80대|칠순|팔순')):
            return choice('👵 시니어 따뜻한 (70대)', '노년 인물에 어울리는 다정하고 포근한 말투입니다. 선택한 성우의 성별은 유지합니다.')
        if has(r'안정|또렷|정확|65\s*세'):
            return choice('📰 시니어 안정적인 (65세)', '연륜을 살리면서 발음과 전달력을 우선하는 역할입니다.')
        if has(r'60대|6\d\s*세|환갑|중후|위엄'):
            return choice('👴 시니어 중후한 (60대)', '60대 또는 중후한 어르신이라는 인물 정보에 맞춘 추천입니다.')
        return choice('🧙 시니어 지혜로운 (70~80대)', '노년 인물의 경험과 지혜가 느껴지는 여유로운 말투를 추천합니다.')
    if has(r'속삭|비밀'):
        return choice('🤫 속삭이듯', '인물 정보에 은밀하거나 속삭이는 말투가 지정되어 있습니다.')
    if has(r'분노|격양|호통') or re.search(r'네 이놈|네놈|감히|닥쳐|용서치|죄를 고하', dialogue):
        return choice('😠 분노/격양', '이 화자의 대사나 인물 정보에 호통과 강한 대립의 단서가 있습니다.')
    if has(r'긴박|다급') or re.search(r'살려\s*주|도망|서둘러|큰일 났', dialogue):
        return choice('⚡ 긴박하게', '도움 요청이나 급박한 상황을 나타내는 대사가 있습니다.')
    if has(r'슬프|슬픔|비통') or re.search(r'흑흑|돌아와|보고 싶|눈물이|어찌 떠나', dialogue):
        return choice('😢 슬프게', '상실과 슬픔을 표현하는 단서가 있습니다.')
    if has(r'사또|현감|원님|군수|장군|왕|엄격|위엄'):
        return choice('🎭 진지하게', '권위와 책임이 있는 역할에 맞춰 무게감 있고 또렷한 말투를 추천합니다.')
    if has(r'훈장|스승|선생|학자'):
        return choice('🎓 강의/교육', '설명하고 가르치는 역할에 맞춰 차분한 전달을 추천합니다.')
    if has(r'소년|소녀|어린|아이|발랄|쾌활|밝은|청년'):
        return choice('😊 밝고 활기차게', '젊거나 활기찬 인물이라는 정보에 맞춰 경쾌한 말투를 추천합니다.')
    if has(r'어머니|아버지|부모|유모|다정|따뜻|온화') or re.search(r'걱정 말|괜찮|고맙|고마워', dialogue):
        return choice('🌙 차분하고 따뜻하게', '보살핌과 안심시키는 말투가 어울립니다.')
    if has(r'감성|부드|다정한 아내') or re.search(r'사랑|그대|당신 곁', dialogue):
        return choice('💌 부드럽고 감성적', '애정과 섬세한 감정을 표현하는 역할입니다.')
    if re.search(r'하옵|소인|결백|억울', dialogue):
        return choice('🎭 진지하게', '정중한 호소와 진지한 태도가 드러나는 대사입니다.')
    return choice('🎤 기본', '나이·성격을 확정할 단서가 적어 자연스러운 기본 톤을 추천합니다. 인물 정보를 적으면 추천이 달라집니다.')


def style_note(engine, style, styles):
    if engine == 'qwen':
        return 'Qwen 1.7B CustomVoice에 감정·말투 지시를 별도로 전달합니다. 선택한 성우·성별을 유지하며, 결과는 보이스와 대사에 따라 달라집니다.'
    if engine == 'chirp':
        return 'Chirp 3 HD는 선택한 목소리와 읽기 속도를 사용합니다. Gemini의 감정·연령 지시문은 보내지 않습니다.'
    if engine == 'gpt-sovits':
        return ('이 선택은 참조 음성을 고르는 가이드입니다. GPT-SoVITS는 스타일 이름으로 나이·감정을 바꾸지 않습니다. '
                '시니어 역할은 해당 연령과 말투로 녹음된 참조 음성을 등록해주세요.')
    if engine == 'supertonic':
        info = styles.get(style, {})
        return (f"속도 {info.get('rate', 0):+d}% · 음량 {info.get('volume', 0) / 5:+g}dB 보정. "
                'Supertonic은 스타일 선택으로 노년 음색이나 감정 연기를 만들지는 않습니다. 연령 연기는 Gemini를 사용해주세요.')
    if engine == 'cosyvoice':
        if style == '🎤 기본':
            return '참조 음성과 실제 대사로 목소리를 복제합니다.'
        return ('CosyVoice 코랩에 연기 지시를 전달합니다. 나이·음색은 참조 목소리의 영향을 받으므로 '
                '시니어 역할에는 시니어 참조 음성을 권장합니다.')
    if engine == 'gemini':
        return '선택한 성우와 성별을 유지하도록 지시하고, 연령·감정·말투를 반영합니다. 추천 스타일은 성우 선택을 바꾸지 않습니다.'
    return '속도·피치·음량을 조절합니다. 연령 자체를 변환하는 기능은 아닙니다.'
