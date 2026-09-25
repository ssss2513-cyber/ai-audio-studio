import re
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class ScriptSegment:
    index: int
    speaker: str
    text: str
    raw_line: str = ""

class ScriptParser:
    """
    대본에서 화자와 대사를 유연하게 분리해내는 파서.
    
    지원하는 대표적인 패턴:
    1. 콜론형: '나레이션: 옛날 옛적에', '철수: 안녕!'
    2. 대괄호형: '[나레이션] 옛날 옛적에', '[철수] 안녕!'
    3. 대괄호+콜론형: '[철수]: 안녕!'
    4. 소괄호형: '(나레이션) 옛날 옛적에', '(철수) 안녕!'
    5. 따옴표형: '철수 "안녕!"', '영희: "반가워"'
    6. 화자 지정 없는 연속 문장: 이전 화자 유지 (첫 문장은 기본 '나레이션')
    """

    # 정규식 패턴들
    # 1. [화자]: 대사 or (화자): 대사 or 화자: 대사
    COLON_PATTERN = re.compile(
        r'^\s*(?:\[(?P<b_spk>[^\]]+)\]|\((?P<p_spk>[^)]+)\)|(?P<plain_spk>[가-힣a-zA-Z0-9_\-\s]{1,20}))\s*[:：]\s*(?P<text>.*)$'
    )
    
    # 2. [화자] 대사 (콜론 없음)
    BRACKET_PATTERN = re.compile(
        r'^\s*\[(?P<spk>[^\]]+)\]\s*(?P<text>.*)$'
    )
    
    # 3. (화자) 대사 (콜론 없음, 괄호 뒤에 텍스트가 바로 오는 경우)
    PAREN_PATTERN = re.compile(
        r'^\s*\((?P<spk>[^)]+)\)\s*(?P<text>.*)$'
    )
    
    # 4. 화자 "대사"
    QUOTE_SPEAKER_PATTERN = re.compile(
        r'^\s*(?P<spk>[가-힣a-zA-Z0-9_\-\s]{1,20})\s+["“](?P<text>[^"”]+)["”]\s*$'
    )

    # 지문/행동 지시문 (예: (웃으며), [한숨]) 제거용
    STAGE_DIR_PAREN = re.compile(r'\([^)]*\)')
    STAGE_DIR_BRACKET = re.compile(r'\[[^\]]*\]')

    def __init__(self, default_speaker: str = "나레이션"):
        self.default_speaker = default_speaker

    def clean_text(self, text: str, remove_stage_directions: bool = False) -> str:
        """대사 텍스트 정제 (화자 접두사 제거, 따옴표 제거, 지문 제거 등)"""
        text = text.strip()

        # 1. 혹시 대사 시작 부분에 화자 이름이 남아있다면 제거 (예: '성복: 대사', '[성복] 대사', '나레이션: 내용')
        text = re.sub(r'^\s*(?:\[[^\]]+\]|\([^)]+\)|[가-힣a-zA-Z0-9_\-\s]{1,15}\s*[:：\-])\s*', '', text)
        text = re.sub(r'^\s*\[[^\]]+\]\s*', '', text)
        text = re.sub(r'^\s*\([가-힣a-zA-Z0-9_\-\s]{1,15}\)\s*', '', text)

        if remove_stage_directions:
            text = self.STAGE_DIR_PAREN.sub('', text)
            text = self.STAGE_DIR_BRACKET.sub('', text)
        
        # 2. 앞뒤 따옴표 벗기기
        text = text.strip()
        if (text.startswith('"') and text.endswith('"')) or \
           (text.startswith('“') and text.endswith('”')) or \
           (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()

        # 3. 따옴표 안쪽에 화자명이 한 번 더 남아있다면 재차 제거
        text = re.sub(r'^\s*(?:\[[^\]]+\]|\([^)]+\)|[가-힣a-zA-Z0-9_\-\s]{1,15}\s*[:：\-])\s*', '', text)
        text = re.sub(r'^\s*\[[^\]]+\]\s*', '', text)

        return text.strip()

    def parse(self, script_text: str, remove_stage_directions: bool = False) -> List[ScriptSegment]:
        lines = script_text.strip().splitlines()
        segments: List[ScriptSegment] = []
        current_speaker = self.default_speaker
        segment_index = 1

        for line_num, line in enumerate(lines, 1):
            raw_line = line
            line_str = line.strip()
            
            # 빈 줄이나 주석 건너뛰기
            if not line_str or line_str.startswith("#") or line_str.startswith("//"):
                continue

            speaker: Optional[str] = None
            dialogue_text: Optional[str] = None

            # 1. 콜론 패턴 검사
            colon_match = self.COLON_PATTERN.match(line_str)
            if colon_match:
                speaker = (
                    colon_match.group("b_spk") or 
                    colon_match.group("p_spk") or 
                    colon_match.group("plain_spk")
                ).strip()
                dialogue_text = colon_match.group("text")
            
            # 2. 대괄호 패턴 [화자] 대사
            if not speaker:
                bracket_match = self.BRACKET_PATTERN.match(line_str)
                if bracket_match and bracket_match.group("text"):
                    speaker = bracket_match.group("spk").strip()
                    dialogue_text = bracket_match.group("text")

            # 3. 화자 "대사" 패턴
            if not speaker:
                quote_match = self.QUOTE_SPEAKER_PATTERN.match(line_str)
                if quote_match:
                    speaker = quote_match.group("spk").strip()
                    dialogue_text = quote_match.group("text")

            # 4. 소괄호 (화자) 대사 패턴
            if not speaker:
                paren_match = self.PAREN_PATTERN.match(line_str)
                if paren_match and paren_match.group("text"):
                    # 괄호 안의 내용이 일반적인 지문이 아니고 화자 이름일 경우
                    pot_spk = paren_match.group("spk").strip()
                    if len(pot_spk) <= 15:
                        speaker = pot_spk
                        dialogue_text = paren_match.group("text")

            # 화자가 명시되지 않은 경우 -> 이전 화자 계속 유지
            if speaker is None:
                speaker = current_speaker
                dialogue_text = line_str
            else:
                current_speaker = speaker

            cleaned_text = self.clean_text(dialogue_text, remove_stage_directions=remove_stage_directions)
            if not cleaned_text:
                continue

            segments.append(
                ScriptSegment(
                    index=segment_index,
                    speaker=speaker,
                    text=cleaned_text,
                    raw_line=raw_line
                )
            )
            segment_index += 1

        return segments

    def extract_speakers(self, segments: List[ScriptSegment]) -> List[str]:
        """등장 순서대로 중복 없는 화자 목록 반환"""
        seen = set()
        speakers = []
        for seg in segments:
            if seg.speaker not in seen:
                seen.add(seg.speaker)
                speakers.append(seg.speaker)
        return speakers
