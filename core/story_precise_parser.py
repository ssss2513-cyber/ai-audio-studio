# -*- coding: utf-8 -*-
import re
import os
from typing import List, Tuple, Optional, Dict, Set, Any

INVALID_ACTOR_WORDS = {
    # 신체 부위 및 상태
    "눈", "코", "입", "귀", "손", "발", "고개", "얼굴", "낯빛", "안색", "눈빛", "눈물", "표정", "모습",
    "어깨", "무릎", "턱", "머리", "등", "배", "가슴", "심장", "허리", "팔", "다리", "손끝", "발끝",
    "목소리", "말문", "냄새", "입술", "혀", "볼", "이마", "목", "숨", "한숨", "걸음", "발걸음", "침묵", "기척", "인기척",
    
    # 사물 및 도구
    "짚신", "신발", "가죽신", "발자국", "창고", "문", "문소리", "흙", "잔돌", "돌", "함", "빗장", "등잔",
    "등잔불", "자루", "물동이", "옷", "두루마기", "칼", "창", "밧줄", "바가지", "밥그릇", "술잔", "등불",
    "불빛", "길", "산길", "마당", "방", "처소", "처마", "지붕", "벽", "기둥", "방바닥", "바닥", "화롯불",
    "됫박", "저울", "저울추", "곡식", "자루", "곡물전", "수레", "배", "마차", "돈", "엽전", "보따리", "가방",
    "책", "장부", "편지", "문서", "열쇠", "곳간", "밑판", "흔적", "도둑",
    
    # 자연 및 배경
    "하늘", "땅", "구름", "바람", "비", "눈발", "햇살", "햇빛", "노을", "봄빛", "겨울", "봄", "여름", "가을",
    "계절", "밤", "아침", "새벽", "저녁", "낮", "시간", "날", "해", "달", "별", "물", "불", "바다", "강",
    "산", "산등성", "언덕", "들판", "숲", "나무", "꽃", "풀", "마을", "고을", "관아", "공방", "시장", "장터", "주막",
    
    # 추상 명사
    "결백", "증거", "시련", "희망", "안도", "분노", "두려움", "슬픔", "기쁨", "걱정", "근심", "생각",
    "기억", "진실", "거짓", "사건", "사고", "이유", "사연", "까닭", "이야기", "사정", "형편", "죄",
    "판결", "처벌", "심문", "조사", "대화", "질문", "대답", "말", "뜻", "마음", "심정", "기분", "감정",
    "순간", "찰나", "동안", "사이", "좌중", "웅성거림", "소문", "소란", "평정", "놀라움", "인연", "시작",
    "일", "것", "바", "수", "때문", "뿐", "만", "척", "체", "듯", "줄", "리", "턱", "즈음", "나위", "겨를",
    
    # 대명사 및 수사
    "그", "그녀", "그들", "이것", "저것", "그것", "자신", "본인", "스스로", "사람", "사람들", "누구",
    "아무", "아무개", "어디", "언제", "무엇", "어떻게", "왜", "우리", "저희", "너", "당신", "자네", "임자",
    "하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉", "열", "스물", "서른", "마흔", "쉰",
    "몇", "여러", "모든", "온갖", "전부", "전체", "일부", "절반", "반", "다른", "다른이",
    
    # 부사 및 의성/의태어
    "끄덕", "살며시", "조용히", "가만히", "서둘러", "버럭", "깜짝", "불쑥", "울컥", "더듬", "왈칵",
    "벌떡", "퍼뜩", "번쩍", "힐끔", "흠칫", "두리번", "허겁지겁", "은근히", "문득", "문뜩", "마침",
    "이내", "하마터면", "어쩌면", "차마", "결코", "전혀", "도무지", "마침내", "드디어", "결국", "여전히",
    "오히려", "그저", "다만", "오직", "비로소", "이미", "벌써", "미리", "다시", "또", "또한", "함께",
    "서로", "각자", "일제히", "다행히", "유난히", "유독", "대체", "어찌", "어찌하여", "왜", "어떻게",
    "어서", "제발", "부디", "활짝", "뚝", "털썩", "삐걱", "겸연쩍", "멋쩍", "나지막", "글썽", "힘없이", "빈틈없이",
    "떨리", "떨림", "떨리는", "울먹", "울먹임", "흐느낌", "망설임", "머뭇", "한숨", "미소", "고개", "눈물",
    "발걸음", "손길", "표정", "목소리", "인기척", "눈치", "기색", "태도", "모습", "사흘", "과연", "옛날", "그런데"
}

SPEECH_VERB_REGEX = re.compile(
    r'(?:말했|말하|말씀하|물었|물어|물으면|답했|대답했|대답을\s*흐|외쳤|소리쳤|되물었|중얼거렸|속삭였|탄식했|다그쳤|호통쳤|'
    r'웃었|울먹였|청했|일렀|전했|설명했|되받아쳤|끄덕였|눈짓했|손짓했|권했|부탁했|간청했|투덜거렸|'
    r'흥얼거렸|읊조렸|외치며|말하며|물으며|소리치며|중얼거리며|속삭이며|소리높여|입을\s*열|말을\s*받|말을\s*이|대꾸했|'
    r'말을\s*던|핀잔을\s*주|나무라|타일렀|소리치|되뇌|속으로\s*말)'
)

# [화자] 대사 or (화자) 대사 or 화자: 대사 (공백 포함 이름 지원: 송 노인, 이방 오익환 등)
ALREADY_SCRIPT_LINE_REGEX = re.compile(
    r'^\s*(?:\[(?P<b_spk>[^\]]+)\]\s*[:：]?|\((?P<p_spk>[^)]+)\)\s*[:：]?|(?P<plain_spk>[가-힣a-zA-Z0-9_\-][가-힣a-zA-Z0-9_\-\s]{0,10}?)\s*[:：])\s*(?P<text>.*)$'
)

QUOTE_REGEX = re.compile(r'["“]([^"”]+)["”]')
CITATION_AFTER_QUOTE = re.compile(r'^(?:라|라고|이라|이라는|이라며|처럼|마냥)\s*(?:부르|불리|여기|생각하|수군거리|칭하|알려지|소문나)')

VERB_ADJ_ENDINGS = (
    "하다", "되다", "없다", "있다", "이다", "롭다", "럽다", "맞다", "않다", 
    "같다", "쉽다", "어렵다", "좋다", "나쁘다", "크다", "작다", "많다", "적다", 
    "높다", "낮다", "깊다", "얕다", "거리다", "대다", "치다", "어쩌다", "그러다",
    "이러다", "저러다", "못하다", "못하", "못한", "않은", "안타깝", "어이없", "당황하"
)

def is_valid_character_name(cand: str) -> bool:
    """단어가 유효한 등장인물 이름인지 검증 (동사/형용사/수식어 원천 차단)"""
    if not cand:
        return False
    cand = cand.strip()
    if cand in ("나레이션", "해설"):
        return True
    if len(cand) < 2 or len(cand) > 12:
        return False
    if not re.fullmatch(r'[가-힣a-zA-Z0-9_\-\s]+', cand):
        return False
    cleaned = re.sub(r'(?:이|가|은|는|을|를|의|에|로|으로|에서|에게|도|만|과|와)$', '', cand).strip()
    if not cleaned or len(cleaned) < 2:
        return False
    if cleaned in INVALID_ACTOR_WORDS or cand in INVALID_ACTOR_WORDS:
        return False
    if cleaned.endswith(("며", "면서", "듯이", "듯", "게", "서", "고", "자", "려고", "려", "더니", "다가", "린")):
        return False
    for ending in VERB_ADJ_ENDINGS:
        if cleaned.endswith(ending) or cand.endswith(ending):
            return False
    if cleaned.startswith(("못", "안", "더", "덜", "잘", "다시")):
        return False
    return True

def scan_real_characters(text: str, custom_characters: Optional[List[str]] = None) -> List[str]:
    """소설 텍스트 전체를 정밀 스캔하여 실제 발화 주어가 되는 등장인물 목록 추출"""
    characters = []
    
    # 1. 사용자가 명시적으로 입력한 인물이 있으면 최우선 등록
    if custom_characters:
        for c in custom_characters:
            c_clean = c.strip()
            if c_clean and c_clean not in characters and c_clean != "나레이션":
                characters.append(c_clean)
        return characters

    # 2. 'X가/은/는/께서 + 발화동사' 문맥 스캔 (부사적 수식어 '듯', '척' 배제)
    freq: Dict[str, int] = {}
    pattern = re.compile(r'([가-힣]{2,4})(?:이|가|은|는|께서)\s*(?!(?:듯|듯이|체|척|양|모양|채|바람에|통에)\b)(?:[^\n.,]{0,12})?' + SPEECH_VERB_REGEX.pattern)
    for m in pattern.finditer(text):
        cand = m.group(1).strip()
        if is_valid_character_name(cand):
            freq[cand] = freq.get(cand, 0) + 2

    # 3. 대본형태 태그 [X] 또는 X: 에서 화자 스캔
    explicit_pattern = re.compile(r'^\s*(?:\[(?P<b_spk>[가-힣]{2,6})\]|(?P<plain_spk>[가-힣]{2,6})\s*[:：])', re.MULTILINE)
    for m in explicit_pattern.finditer(text):
        cand = (m.group("b_spk") or m.group("plain_spk")).strip()
        if is_valid_character_name(cand) and cand != "나레이션":
            freq[cand] = freq.get(cand, 0) + 5

    # 빈도순으로 상위 인물 선별 (잡음 제거: 최소 2회 이상 발화 주어로 나타난 인물 우선)
    valid_candidates = [(cand, count) for cand, count in sorted(freq.items(), key=lambda x: x[1], reverse=True) if count >= 4]
    if not valid_candidates and freq:
        valid_candidates = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:3]

    for cand, count in valid_candidates:
        if cand not in characters:
            characters.append(cand)

    # 기본 최대 5명으로 지능형 제한 (일반 단편 소설/오디오 드라마의 표준 적정 배역 수)
    if len(characters) > 5 and not custom_characters:
        characters = characters[:5]

    return characters

def is_already_formatted_script(text: str) -> bool:
    """텍스트가 이미 '화자: 대사' 또는 '[화자] 대사' 형태인지 판별"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    sample = lines[:min(50, len(lines))]
    script_like_count = sum(1 for line in sample if ALREADY_SCRIPT_LINE_REGEX.match(line))
    return script_like_count >= 5 or (script_like_count / len(sample)) >= 0.20

def parse_story_precisely(raw_text: str, custom_characters: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """
    일반 소설, 이야기 글, 또는 대본 텍스트를 분석하여 
    (화자, 대사/서술) 튜플 리스트로 자동 변환합니다.
    """
    raw_text = raw_text.strip()
    if not raw_text:
        return []

    # 1. 이미 '화자: 대사' 또는 '[화자] 대사' 형식으로 잘 정리된 대본인 경우 안전하게 그대로 파싱
    if is_already_formatted_script(raw_text):
        results: List[Tuple[str, str]] = []
        for line in raw_text.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith(("#", "//")):
                continue
            m = ALREADY_SCRIPT_LINE_REGEX.match(line_str)
            if m:
                spk = (m.group("b_spk") or m.group("p_spk") or m.group("plain_spk")).strip()
                dlg = m.group("text").strip()
                if not dlg:
                    # '송 노인:', '[덕쇠]' 등 단독 화자 헤더 라인은 나레이션으로 만들지 않고 건너뛰기
                    continue
                # 만약 dlg가 또 다른 화자 콜론으로 시작하면 (예: '송 노인: "대사"')
                sub_m = ALREADY_SCRIPT_LINE_REGEX.match(dlg)
                if sub_m and sub_m.group("text").strip():
                    spk = (sub_m.group("b_spk") or sub_m.group("p_spk") or sub_m.group("plain_spk")).strip()
                    dlg = sub_m.group("text").strip()

                if spk in ("나레이션", "해설"):
                    results.append(("나레이션", dlg))
                elif not is_valid_character_name(spk):
                    results.append(("나레이션", dlg if dlg else line_str))
                elif custom_characters and spk not in custom_characters and spk != "나레이션":
                    results.append(("나레이션", dlg if dlg else line_str))
                else:
                    results.append((spk, dlg))
            else:
                if line_str.startswith("#"):
                    continue
                clean_line = re.sub(r'^(?:나레이션|해설)\s*[:：]\s*', '', line_str).strip()
                if re.match(r'^[가-힣a-zA-Z0-9_\-\s]{1,10}\s*[:：]\s*$', clean_line):
                    continue
                results.append(("나레이션", line_str))
        return results

    # 2. 소설 텍스트 정밀 문맥 분석
    known_chars = scan_real_characters(raw_text, custom_characters)
    paragraphs = [p.strip() for p in raw_text.splitlines() if p.strip()]
    results: List[Tuple[str, str]] = []
    
    last_speaker = known_chars[0] if known_chars else "인물1"

    for i, para in enumerate(paragraphs):
        quotes = list(QUOTE_REGEX.finditer(para))
        if not quotes:
            # 순수 서술문 (나레이션)
            results.append(("나레이션", para))
            continue

        last_end = 0
        for q in quotes:
            q_start, q_end = q.span()
            dialogue_text = q.group(1).strip()
            before_text = para[last_end:q_start].strip()
            after_text = para[q_end:].strip()

            # "거지 사위"라 부르며 등 서술어 수식 따옴표인 경우 대사로 분리하지 않음
            if len(dialogue_text) <= 8 and CITATION_AFTER_QUOTE.match(after_text):
                continue

            # 대사 앞 서술문 추가
            if before_text:
                results.append(("나레이션", before_text))

            # 화자 결정
            speaker = None

            # A. 대사 직전 문맥에서 화자 찾기
            recent_before = before_text[-50:] if before_text else (paragraphs[i-1][-50:] if i > 0 else "")
            if SPEECH_VERB_REGEX.search(recent_before):
                for c in known_chars:
                    if f"{c}이" in recent_before or f"{c}가" in recent_before or f"{c}은" in recent_before or f"{c}는" in recent_before or f"{c}께서" in recent_before:
                        speaker = c
                        break

            # B. 대사 직후 문맥에서 화자 찾기
            if not speaker:
                recent_after = after_text[:50]
                if SPEECH_VERB_REGEX.search(recent_after):
                    for c in known_chars:
                        if f"{c}이" in recent_after or f"{c}가" in recent_after or f"{c}은" in recent_after or f"{c}는" in recent_after:
                            speaker = c
                            break

            # C. 직전 대화 상대방과 턴 테이킹 (번갈아 말하기)
            if not speaker:
                if len(known_chars) >= 2:
                    speaker = known_chars[1] if last_speaker == known_chars[0] else known_chars[0]
                elif known_chars:
                    speaker = known_chars[0]
                else:
                    speaker = "인물1"

            results.append((speaker, f'"{dialogue_text}"'))
            last_speaker = speaker
            last_end = q_end

        remaining = para[last_end:].strip()
        if remaining:
            results.append(("나레이션", remaining))

    return results

def parse_story_with_gemini(
    raw_text: str,
    api_key: str,
    model: str = "gemini-3.8-flash",
    custom_characters: Optional[List[str]] = None,
    progress_callback: Optional[Any] = None
) -> List[Tuple[str, str]]:
    """
    Google Gemini AI를 활용하여 소설 속 등장인물과 대사를 100% 인간 수준의 정확도로 분리 및 대본화합니다.
    (이미 '화자: 대사' 대본인 경우 0.05초 즉시 로컬 파싱, 대용량 소설의 경우 청크 단위 분할 처리)
    """
    raw_text = raw_text.strip()
    if not raw_text:
        return []

    # 1. 이미 '화자: 대사' 또는 '[화자] 대사' 형태의 대본인 경우 -> 0.05초 즉시 반환
    if is_already_formatted_script(raw_text):
        return parse_story_precisely(raw_text, custom_characters=custom_characters)

    if not api_key:
        raise ValueError("Gemini API 키가 설정되지 않았습니다. 사이드바에 키를 입력해주세요.")
        
    from google import genai
    import re

    keys_list = [k.strip() for k in re.split(r'[,;\s\n]+', api_key) if k.strip()]
    first_key = keys_list[0] if keys_list else api_key
    client = genai.Client(api_key=first_key)

    char_guide = ""
    if custom_characters:
        char_guide = f"\n[지정된 핵심 등장인물]: {', '.join(custom_characters)}\n반드시 위 인물들과 '나레이션'만 화자로 사용하세요."

    # 2. 텍스트 분할 (청크당 최대 약 3,000자, 토큰 초과 방지 및 빠른 응답 보장)
    lines = raw_text.splitlines()
    chunks: List[str] = []
    curr_lines: List[str] = []
    curr_len = 0
    max_chunk = 3000

    for l in lines:
        l_len = len(l) + 1
        if curr_len + l_len > max_chunk and curr_lines:
            chunks.append("\n".join(curr_lines))
            curr_lines = [l]
            curr_len = l_len
        else:
            curr_lines.append(l)
            curr_len += l_len
    if curr_lines:
        chunks.append("\n".join(curr_lines))

    # 후보 모델 목록
    candidates = []
    if model:
        candidates.append(model)
    candidates.extend(["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"])
    
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    all_parsed_results: List[Tuple[str, str]] = []

    for chunk_idx, chunk_text in enumerate(chunks, 1):
        if progress_callback:
            try:
                progress_callback(chunk_idx, len(chunks))
            except Exception:
                pass

        prompt = f"""당신은 전문 오디오북 및 라디오 드라마 대본 작가입니다.
아래 소설/이야기를 정밀 분석하여, 등장인물들의 대사와 나레이션을 완벽하게 분리한 낭독 대본으로 변환해주세요.

[필수 규칙]
1. 본문에 실제로 등장하여 말을 하는 인물들만 정확한 이름으로 화자를 지정하세요. (본문에 없는 인물을 절대 지어내지 마세요){char_guide}
2. 모든 서술 및 지문은 반드시 '나레이션'으로 분류하세요.
3. 본문 중 단어를 강조하거나 별명을 칭하기 위해 쓰인 따옴표(예: "거지 사위"라 부르며, "도둑"이라며 등)는 별도의 대사로 쪼개지 말고 '나레이션' 서술 문장 안에 자연스럽게 그대로 포함시키세요.
4. 출력 형식은 반드시 매 줄마다:
   [화자이름] 대사내용
   또는
   [나레이션] 서술내용
   형태로만 한 줄씩 출력하세요. 앞뒤의 인사말이나 마크다운 설명(``` 등)은 일절 출력하지 마세요.

[변환할 소설 원문 파트 {chunk_idx}/{len(chunks)}]:
{chunk_text}
"""

        response = None
        last_err = None
        for target_model in unique_candidates:
            try:
                response = client.models.generate_content(
                    model=target_model,
                    contents=prompt
                )
                if response and response.text:
                    break
            except Exception as e:
                last_err = e
                continue

        if response is None:
            # 청크 변환 실패 시 해당 청크는 안전하게 로컬 정밀 파서로 폴백
            fallback_segs = parse_story_precisely(chunk_text, custom_characters=custom_characters)
            all_parsed_results.extend(fallback_segs)
            continue

        result_text = response.text or ""
        for line in result_text.splitlines():
            line = line.strip()
            if not line or line.startswith("```"):
                continue
            m = re.match(r'^\s*\[(?P<spk>[^\]]+)\]\s*(?P<text>.*)$', line)
            if m:
                spk = m.group("spk").strip()
                dlg = m.group("text").strip()
                if dlg:
                    all_parsed_results.append((spk, dlg))
            else:
                colon_m = re.match(r'^\s*([가-힣a-zA-Z0-9_\-\s]{1,15})\s*[:：]\s*(.*)$', line)
                if colon_m:
                    spk = colon_m.group(1).strip()
                    dlg = colon_m.group(2).strip()
                    if dlg:
                        all_parsed_results.append((spk, dlg))
                else:
                    all_parsed_results.append(("나레이션", line))

    return all_parsed_results
