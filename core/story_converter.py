import re
from typing import List, Tuple, Optional

# 주요 등장인물 및 식별 키워드
CHARACTERS = [
    ("사또", ["사또", "한 사또", "대감", "본관"]),
    ("경헌", ["경헌", "감독관", "감독관 경헌", "경헌 나리"]),
    ("잉손", ["잉손", "어르신", "짚신장이 잉손", "잉손 어르신", "노인", "늙은이"]),
    ("남 포졸", ["남 포졸", "포졸 나리", "포졸 하나", "포졸"]),
    ("계순", ["계순", "아내", "부인", "며느리"]),
    ("노모", ["노모", "시어머니", "어머니", "어머님"]),
    ("장인", ["장인어른", "계순의 아버지", "친정아버지", "친정아버님"]),
    ("장모", ["계순의 어머니", "장모님"]),
    ("훈장", ["서당 훈장", "훈장님", "훈장"]),
    ("박씨 노인", ["박씨 어르신", "박씨 노인", "박씨"]),
    ("젊은 아낙", ["젊은 아낙", "아낙"]),
    ("청년", ["청년", "젊은이"]),
    ("성복", ["성복", "성복이", "창고지기", "창고지기 성복", "소인", "남편"])
]

SPEECH_VERBS = [
    "말했", "말하", "물었", "물으", "외쳤", "청하", "아뢰", "속삭", "소리쳤", 
    "깨웠", "되물", "단호", "선언", "중얼거렸", "웃으며", "답변", "대답", "간청", 
    "다독였", "맞이하", "일렀", "이야기", "청을"
]

def find_subject_in_sentence(sentence: str) -> Optional[str]:
    """한 문장에서 주어가 되는 등장인물 찾기"""
    sentence = sentence.strip()
    # 인물 뒤에 조사가 붙은 형태: 은, 는, 이, 가, 의
    best_char = None
    best_pos = -1

    for char_name, aliases in CHARACTERS:
        for alias in aliases:
            # 주격/관형격 조사 매칭
            match = re.search(rf'{alias}(?:은|는|이|가|의|께서|도|에게)?', sentence)
            if match:
                pos = match.start()
                if pos > best_pos:
                    best_pos = pos
                    best_char = char_name

    return best_char

def convert_story_to_script_v2(story_text: str) -> str:
    paragraphs = [p.strip() for p in story_text.splitlines() if p.strip()]
    script_segments: List[Tuple[str, str]] = [] # (speaker, text)

    current_dialogue_pair = ["성복", "경헌"]
    last_speaker = "성복"

    quote_pattern = re.compile(r'["“]([^"”]+)["”]')

    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]
        quotes = list(quote_pattern.finditer(para))

        # 1. 따옴표가 아예 없는 순수 나레이션 단락
        if not quotes:
            script_segments.append(("나레이션", para))
            # 나레이션에서 언급된 주된 인물들로 dialogue_pair 갱신
            mentioned = []
            for c_name, aliases in CHARACTERS:
                if any(a in para for a in aliases):
                    mentioned.append(c_name)
            if len(mentioned) >= 2:
                current_dialogue_pair = mentioned[:2]
            elif len(mentioned) == 1 and mentioned[0] not in current_dialogue_pair:
                current_dialogue_pair = [mentioned[0], current_dialogue_pair[0]]
            i += 1
            continue

        # 2. 단락 전체가 따옴표 대사 하나인 경우 (가장 흔한 대화 단락)
        if len(quotes) == 1 and quotes[0].start() == 0 and quotes[0].end() == len(para):
            quote_text = quotes[0].group(1).strip()
            
            speaker = None
            # 다음 단락에서 화자 힌트 확인 (예: '성복이 간곡히 청하자', '남 포졸이 되물었습니다')
            if i + 1 < len(paragraphs):
                next_para = paragraphs[i + 1]
                next_quotes = list(quote_pattern.finditer(next_para))
                # 다음 단락이 나레이션이고 말하기 관련 내용이 있는 경우
                if not next_quotes or next_quotes[0].start() > 0:
                    first_sent = next_para.split(".")[0]
                    if any(v in first_sent for v in SPEECH_VERBS):
                        found = find_subject_in_sentence(first_sent)
                        if found:
                            speaker = found

            # 이전 단락 마지막 문장에서 화자 힌트 확인 (예: '계순은 서둘러 남편을 깨웠습니다.')
            if not speaker and i > 0:
                prev_para = paragraphs[i - 1]
                prev_sentences = [s.strip() for s in prev_para.split(".") if s.strip()]
                if prev_sentences:
                    last_sent = prev_sentences[-1]
                    found = find_subject_in_sentence(last_sent)
                    if found:
                        speaker = found

            # 턴 테이킹 (이전 화자의 상대방)
            if not speaker:
                if len(current_dialogue_pair) == 2 and last_speaker in current_dialogue_pair:
                    speaker = current_dialogue_pair[1] if last_speaker == current_dialogue_pair[0] else current_dialogue_pair[0]
                else:
                    speaker = "성복"

            script_segments.append((speaker, f'"{quote_text}"'))
            last_speaker = speaker
            i += 1
            continue

        # 3. 따옴표와 나레이션이 한 단락 안에 섞여 있는 경우
        last_idx = 0
        for q in quotes:
            q_start, q_end = q.span()
            before_text = para[last_idx:q_start].strip()
            quote_text = q.group(1).strip()
            after_text = para[q_end:].strip()

            if before_text:
                script_segments.append(("나레이션", before_text))
                # before_text에서 화자 찾기
                spk_hint = find_subject_in_sentence(before_text)
            else:
                spk_hint = None

            if not spk_hint and after_text:
                first_sent = after_text.split(".")[0]
                if any(v in first_sent for v in SPEECH_VERBS):
                    spk_hint = find_subject_in_sentence(first_sent)

            if not spk_hint:
                if len(current_dialogue_pair) == 2 and last_speaker in current_dialogue_pair:
                    spk_hint = current_dialogue_pair[1] if last_speaker == current_dialogue_pair[0] else current_dialogue_pair[0]
                else:
                    spk_hint = "성복"

            script_segments.append((spk_hint, f'"{quote_text}"'))
            last_speaker = spk_hint
            last_idx = q_end

        remaining = para[last_idx:].strip()
        if remaining:
            script_segments.append(("나레이션", remaining))

        i += 1

    # 포맷팅
    output_lines = [f"{spk}: {txt}" for spk, txt in script_segments]
    return "\n".join(output_lines)
