#!/usr/bin/env python3
"""Yadam validator V11.22 — deterministic script and intro metrics.

Uses only the Python standard library. Semantic judgments remain with the agent;
this program supplies counts, exact anchor positions, and hard format checks.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


STYLE_LOCK = (
    "unmistakable flat 2D Korean cel-webtoon, clean closed outlines, opaque "
    "flat colors, hard-edged cel shadows, simplified illustrated textures; "
    "no photorealism, no live action, no realistic skin or material texture, "
    "no cinematic photo look, no 3D render"
)

KEY_BEAT_RANGES = {
    4: (8.0, 12.0),
    6: (18.0, 22.0),
    9: (47.0, 53.0),
    11: (68.0, 75.0),
    13: (78.0, 83.0),
    14: (80.0, 100.0),
}

RECURRING_PHRASES = (
    "손끝",
    "마을 사람",
    "그날 밤",
    "이튿날",
    "며칠 뒤",
    "며칠이 지나도록",
    "그러나",
    "고개를 끄덕",
    "마음이 무거",
    "회심의 미소",
    "믿어주시",
    "폐를 끼",
)

# Narration may use the senior-yadam endings `~지요`, `~더군요`, and a
# literary curiosity question ending in `~까요`.  Other sentence-final
# `~요` speech and familiar/casual endings are treated as modern narration.
# Direct dialogue is removed before this pattern is applied.
MODERN_NARRATION_ENDING_PATTERN = re.compile(
    r"("
    r"(?:했|였|었|았|던|겠)?는데요|거든요|잖아요|네요|죠|(?:으)?니까요|"
    r"더라고요|(?:라|다|자|냐)고요|(?:이)?에요|예요|거예요|"
    r"인가요|려고요|게요|(?<!더)군요|"
    r"(?!(?:지|까|군)요)[가-힣]요|"
    r"거든|잖아|구나|더라|던데|는데|거야|거지|거네|말이야|"
    r"같아|싶어|할걸|[가-힣](?:어|아|지|네)|야|냐|니|해|돼|했다"
    r")$"
)


def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


def compact(text: str) -> str:
    text = re.sub(r"@[A-Za-z0-9_가-힣-]+\s*", "", text)
    return re.sub(r"[\s\"'“”‘’.,!?…·:;()\[\]{}<>—–-]+", "", text)


def normalize_newlines(text: str) -> str:
    """Only normalize platform line endings; never discard story text."""
    return text.replace("\r\n", "\n").replace("\r", "\n")



_CHAPTER_NUMBER = r'(?:\d{1,3}|[일이삼사오육칠팔구십백천]+|[一二三四五六七八九十百]+|[IVXLCDM]+)'
_CHAPTER_PREFIX = re.compile(
    r'^(?:(?:제\s*)?' + _CHAPTER_NUMBER + r'\s*(?:장|막|부|화)|'
    r'(?:chapter|chap\.?|ch\.?)\s*' + _CHAPTER_NUMBER + r')'
    r'(?=$|[\s|｜:：\-—–·.\]\)】》>])', re.I
)


def is_script_heading(line):
    """Recognize standalone production headings; preserve spoken prose."""
    value = line.strip()
    if not value or value.startswith(('"', '“')):
        return False
    if re.match(r'^#{1,6}(?:\s|(?=제\s*\d))', value):
        return True
    if re.fullmatch(r'(?:[-*_]\s*){3,}', value):
        return True
    unwrapped = value
    for left, right in (('**', '**'), ('__', '__'), ('[', ']'), ('【', '】'), ('(', ')')):
        if unwrapped.startswith(left) and unwrapped.endswith(right):
            unwrapped = unwrapped[len(left):-len(right)].strip()
    if not _CHAPTER_PREFIX.match(unwrapped):
        return False
    # Plain mentions of a book chapter inside a complete narration are content.
    has_separator = bool(re.search(r'[|｜:：—–]|\s-\s', unwrapped))
    if unwrapped == value and not has_separator and re.search(
        r'(?:[.!?。！？…]|습니다|입니다|답니다|지요|더군요|까요|하오|했소)$', value
    ):
        return False
    return True



def plain_script_format(text: str) -> dict[str, Any]:
    errors = []
    prefixes = []
    headings = []
    for number, line in enumerate(normalize_newlines(text).split("\n"), 1):
        if not line.strip():
            continue
        if is_script_heading(line):
            headings.append({"line": number, "text": line, "reason": "낭독 원고에 장 번호·제목·헤더·구분선 금지"})
        if re.match(r"^(?:@[A-Za-z][A-Za-z0-9_]*|[가-힣A-Za-z][가-힣A-Za-z0-9 _-]{0,29})\s*[:：]", line):
            prefixes.append({"line": number, "text": line[:140]})
        if "“" in line or "”" in line:
            errors.append({"line": number, "reason": '직접 대사는 ASCII 큰따옴표 "만 사용', "text": line[:140]})
        if '"' in line and not re.fullmatch(r'"[^"\n]+"', line):
            errors.append({"line": number, "reason": '기본 대사의 한 줄은 "대사"만 포함', "text": line[:140]})
    return {
        "status": "FAIL" if errors or prefixes or headings else "PASS",
        "heading_status": "FAIL" if headings else "PASS",
        "heading_count": len(headings),
        "heading_examples": headings[:20],
        "speaker_prefix_status": "FAIL" if prefixes else "PASS",
        "speaker_prefix_count": len(prefixes),
        "speaker_prefix_examples": prefixes[:10],
        "dialogue_line_status": "FAIL" if errors else "PASS",
        "dialogue_line_errors": errors[:10],
    }


def narration_rhythm(text: str) -> dict[str, Any]:
    narration = re.sub(r'"[^"\n]*"|“[^”\n]*”', '', text)
    families = (
        ("FORMAL", r"(?:습니다|입니다)$"),
        ("STORY", r"(?:답니다|랍니다)$"),
        ("JIYO", r"지요$"),
        ("OBSERVATION", r"더군요$"),
        ("QUESTION", r"(?:하였을까요|였을까요|것이었을까요)$"),
    )
    rows = []
    errors = []
    for sentence in re.split(r"[.!?…]+|\n+", narration):
        sentence = sentence.strip()
        if not sentence:
            continue
        family = next((key for key, pattern in families if re.search(pattern, sentence)), None)
        rows.append({"sentence": sentence, "family": family})
        if len(rows) >= 3 and family and all(r["family"] == family for r in rows[-3:]):
            errors.append({"family": family, "sentences": [r["sentence"] for r in rows[-3:]]})
    return {"status": "FAIL" if errors else "PASS", "repeated_family_count": len(errors), "examples": errors[:10]}


def dialogue_file_metrics(
    script: str,
    labeled: str | None,
    speaker_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate an EXTRA labeled copy without writing either input file.

    The map must be read independently from the basic story, not inferred from
    the labels being tested. Matching that map is not a semantic guarantee.
    """
    if labeled is None:
        return {"status": "UNVERIFIED", "reason": "추가 대사 파일 미제공; 기본 대본은 그대로 검사", "content_match_status": "UNVERIFIED", "label_status": "UNVERIFIED", "speaker_map_match_status": "UNVERIFIED"}
    source = normalize_newlines(script)
    labeled = normalize_newlines(labeled)
    restored = []
    spoken = []
    errors = []
    narration_count = 0
    for number, line in enumerate(labeled.split("\n"), 1):
        if not line.strip():
            restored.append(line)
            continue
        match = re.fullmatch(r"([가-힣][가-힣0-9 ]{0,29}): (.+)", line)
        if not match:
            errors.append({"line": number, "reason": "나레이션: 본문 또는 실제인물명: \"대사\" 형식 필요", "text": line[:140]})
            restored.append(line)
            continue
        label, content = match.groups()
        restored.append(content)
        if is_script_heading(content):
            errors.append({"line": number, "reason": "장 번호·제목·헤더·구분선을 나레이션으로 넣을 수 없음", "text": content})
        is_dialogue = re.fullmatch(r'"[^"\n]+"', content) is not None
        if label != label.strip():
            errors.append({"line": number, "reason": "표시명 앞뒤 불필요한 공백"})
        if is_dialogue:
            spoken.append({"line": number, "speaker": label, "text": content})
            if label == "나레이션":
                errors.append({"line": number, "reason": "직접 대사는 실제 화자 이름으로 표시"})
        else:
            narration_count += 1
            if label != "나레이션":
                errors.append({"line": number, "reason": "모든 나레이션 구간에 정확히 나레이션: 표시 필요"})
            if any(q in content for q in ('"', '“', '”')):
                errors.append({"line": number, "reason": "나레이션과 직접 대사를 같은 줄에 섞지 않음"})
        if re.search(r"@[A-Za-z][A-Za-z0-9_]*", line):
            errors.append({"line": number, "reason": "대사 파일에 영문 @인물태그 금지"})
    recovered = "\n".join(restored)
    content_matches = recovered == source
    differences = []
    if not content_matches:
        for entry in difflib.unified_diff(source.splitlines(), recovered.splitlines(), fromfile="기본 대본", tofile="대사 파일에서 표시 제거", lineterm="", n=1):
            differences.append(entry)
            if len(differences) >= 30:
                break
        if not differences:
            differences = ["마지막 줄바꿈 또는 공백이 다름; 줄바꿈 종류 외 문자·공백은 원문 그대로 유지"]
    map_errors = []
    map_status = "UNVERIFIED"
    mapping = speaker_map or {}
    expected = mapping.get("dialogue_speakers")
    allowed = mapping.get("allowed_speakers")
    first_body_speaker = mapping.get("first_body_speaker")
    if mapping and any(value for value in mapping.values()):
        if not isinstance(expected, list) or not expected or not all(isinstance(name, str) and re.fullmatch(r"[가-힣][가-힣0-9 ]{0,29}", name) and name == name.strip() and name != "나레이션" for name in expected):
            map_errors.append("dialogue_speakers에는 원문의 모든 발화 화자를 실제 순서대로 기록")
        if not isinstance(allowed, list) or not allowed or not all(isinstance(name, str) and re.fullmatch(r"[가-힣][가-힣0-9 ]{0,29}", name) and name == name.strip() and name != "나레이션" for name in allowed):
            map_errors.append("allowed_speakers에는 원문에서 확인한 한글 인물 표시명을 기록")
        if not isinstance(first_body_speaker, str) or not first_body_speaker.strip():
            map_errors.append("first_body_speaker에 본편 첫 실제 발화 화자 기록")
        if not map_errors:
            if len(expected) != len(spoken):
                map_errors.append(f"화자 지도 {len(expected)}개와 대사 발화 {len(spoken)}개의 개수가 다름")
            for index, (name, row) in enumerate(zip(expected, spoken), 1):
                if name not in allowed or row["speaker"] not in allowed or name != row["speaker"]:
                    map_errors.append(f"발화 {index}: 실제 지도={name}, 대사 파일={row['speaker']}")
            if expected[0] != first_body_speaker or not spoken or spoken[0]["speaker"] != first_body_speaker:
                map_errors.append("본편 첫 발화의 실제 인물 이름 불일치")
        map_status = "FAIL" if map_errors else "PASS"
    status = "FAIL" if errors or not content_matches or map_status == "FAIL" else ("UNVERIFIED" if map_status == "UNVERIFIED" else "PASS")
    return {
        "status": status,
        "label_status": "FAIL" if errors else "PASS",
        "content_match_status": "PASS" if content_matches else "FAIL",
        "source_unchanged_policy": "기본 대본 읽기 전용; 비교 오류를 맞추려고 원본 수정 금지",
        "comparison_normalization": "CRLF/CR → LF만 허용; 문장·따옴표·문장부호·공백·순서 보존",
        "narration_segments": narration_count,
        "dialogue_segments": len(spoken),
        "errors": errors[:20],
        "difference_preview": differences,
        "speaker_map_match_status": map_status,
        "speaker_map_errors": map_errors[:20],
        "semantic_review": "기본 대본과 story_facts에서 지도 독립 작성; 문맥상 실제 화자·챕터별 상호 대화는 Claude가 근거와 함께 확인",
    }


def build_speaker_summary(plain_text, title, dialogue_speakers, allowed_speakers, first_body_speaker):
    """Group original utterances, preserving their first 20 Unicode characters."""
    if not isinstance(title, str) or not title.strip() or '\n' in title or '\r' in title:
        raise ValueError('SUMMARY_TITLE_INVALID')
    if not isinstance(dialogue_speakers, list) or not dialogue_speakers:
        raise ValueError('SUMMARY_SPEAKER_MAP_MISSING')
    if not isinstance(allowed_speakers, list) or not allowed_speakers or any(
        not isinstance(name, str)
        or not re.fullmatch(r'[가-힣][가-힣0-9 ]{0,29}', name)
        or name != name.strip() or name in {'나레이션', '화자 미상'}
        for name in allowed_speakers
    ):
        raise ValueError('SUMMARY_SPEAKER_NAME_INVALID')
    if any(not isinstance(name, str) or name not in allowed_speakers for name in dialogue_speakers):
        raise ValueError('SUMMARY_SPEAKER_NOT_ALLOWED')
    if dialogue_speakers[0] != first_body_speaker or first_body_speaker not in allowed_speakers:
        raise ValueError('SUMMARY_FIRST_BODY_SPEAKER_MISMATCH')
    plain = plain_text.replace('\r\n', '\n').replace('\r', '\n')
    lines = [line for line in plain.split('\n') if line.strip()]
    if not lines:
        raise ValueError('SUMMARY_BODY_MISSING')
    utterances = []
    for line in lines:
        if '“' in line or '”' in line:
            raise ValueError('SUMMARY_ASCII_QUOTES_REQUIRED')
        match = re.fullmatch(r'"([^"\n]+)"', line)
        if '"' in line and not match:
            raise ValueError('SUMMARY_SOURCE_DIALOGUE_FORMAT_INVALID')
        if match:
            if not match.group(1).strip():
                raise ValueError('SUMMARY_EMPTY_UTTERANCE')
            utterances.append(match.group(1))
    if len(utterances) != len(dialogue_speakers):
        raise ValueError('SUMMARY_SPEAKER_COUNT_MISMATCH')
    groups = {}
    for speaker, utterance in zip(dialogue_speakers, utterances):
        groups.setdefault(speaker, []).append(utterance)
    output = [f'{title} — 화자별 대사 정리 (앞 20자)', '', f'총 화자 수: {len(groups)}명', '']
    for speaker, speeches in groups.items():
        output.append(f'=== {speaker} ({len(speeches)}회) ===')
        for speech in speeches:
            excerpt = speech[:20] + ('...' if len(speech) > 20 else '')
            output.append(f'"{excerpt}"')
        output.append('')
    return '\n'.join(output)


def speaker_summary_metrics(script, summary, speaker_map=None, story_title=None):
    """Read-only comparison with full source + independently verified speakers."""
    if summary is None:
        return {'status': 'UNVERIFIED', 'reason': '화자별 앞 20자 정리본 미제공; 나머지 파일 검사는 계속', 'title_status': 'UNVERIFIED'}
    summary = normalize_newlines(summary)
    header = re.fullmatch(r'(.+) — 화자별 대사 정리 \(앞 20자\)', summary.split('\n')[0])
    if not header:
        return {'status': 'FAIL', 'reason': '첫 줄에 작품 제목 — 화자별 대사 정리 (앞 20자) 형식 필요', 'title_status': 'FAIL'}
    supplied_title = header.group(1)
    known_title = isinstance(story_title, str) and bool(story_title.strip())
    title_status = ('PASS' if supplied_title == story_title else 'FAIL') if known_title else 'UNVERIFIED'
    mapping = speaker_map or {}
    if not mapping or not any(mapping.values()):
        return {'status': 'FAIL' if title_status == 'FAIL' else 'UNVERIFIED', 'reason': '원문에서 확인한 화자 지도가 없어 정리본의 인물·횟수·앞 20자를 확정할 수 없음', 'title_status': title_status}
    try:
        expected = build_speaker_summary(
            script, story_title if known_title else supplied_title,
            mapping.get('dialogue_speakers'), mapping.get('allowed_speakers'), mapping.get('first_body_speaker'),
        )
    except ValueError as exc:
        return {'status': 'FAIL', 'reason': str(exc), 'title_status': title_status}
    # Final newline style does not affect this lookup sheet. Interior blanks do.
    matches = summary.rstrip('\n') == expected.rstrip('\n')
    differences = []
    if not matches:
        for entry in difflib.unified_diff(expected.splitlines(), summary.splitlines(), fromfile='원문·독립 화자 지도에서 만든 기대 형식', tofile='제공된 화자별 정리본', lineterm='', n=1):
            differences.append(entry)
            if len(differences) >= 30:
                break
    counts = Counter(mapping['dialogue_speakers'])
    return {
        'status': 'FAIL' if not matches or title_status == 'FAIL' else ('PASS' if known_title else 'UNVERIFIED'),
        'title_status': title_status,
        'format_and_excerpt_status': 'PASS' if matches else 'FAIL',
        'speaker_count': len(counts),
        'utterance_count': sum(counts.values()),
        'speaker_counts': dict(counts),
        'difference_preview': differences,
        'reason': '확정 작품 제목을 --story-title 또는 감사 지도 story_title로 지정' if not known_title else '',
        'count_rule': '실제 발화 한 줄=1회; 첫 등장 순서; 인물별 원문 순서; 중복 발화 유지',
        'excerpt_rule': '대사를 감싼 큰따옴표 제외, 공백·문장부호 포함 원문 첫 20자; 초과할 때만 ... 추가',
        'semantic_review': '지도 자체의 화자 정답은 기본 대본·인물표에서 별도 확인. 정리본에서 지도 역산 금지',
    }


def audit_educational_value(body, audit_map):
    """Check evidence locations only; story meaning needs Claude's close reading."""
    data = (audit_map or {}).get('educational_value')
    if data is None or data == {}:
        return {'status': 'UNVERIFIED', 'evidence_status': 'UNVERIFIED',
                'semantic_status': 'UNVERIFIED', 'reason': '교육적 가치 근거 지도 미작성; 원문을 읽고 내부 작성', 'anchors': []}
    if not isinstance(data, dict):
        return {'status': 'FAIL', 'evidence_status': 'FAIL', 'semantic_status': 'UNVERIFIED',
                'reason': 'educational_value는 객체여야 함', 'anchors': []}
    required = ('learning_focus', 'choice_anchor', 'consequence_anchor', 'payoff_anchor')
    errors, missing, rows = [], [], []
    for key in required:
        value = data.get(key)
        if value is None or value == '':
            missing.append(key)
        elif not isinstance(value, str):
            errors.append(f'{key}: 문자열 필요')
        elif not value.strip():
            missing.append(key)
    for key, label in (('choice_anchor', '인물의 선택'), ('consequence_anchor', '선택의 결과'), ('payoff_anchor', '마지막 회수')):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        occurrences = body.count(value)
        if occurrences != 1:
            errors.append(f'{label}: 본편의 고유 원문 필요 (현재 {occurrences}회)')
        else:
            position = body.index(value)
            rows.append({'kind': key, 'label': label, 'anchor': value,
                         'character_position': position, 'line': body[:position].count('\n') + 1})
    spans = sorted((row['character_position'], row['character_position'] + len(row['anchor'])) for row in rows)
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        errors.append('각 역할의 근거 원문은 겹치지 않도록 구별해서 지정')
    evidence = 'FAIL' if errors else ('UNVERIFIED' if missing else 'PASS')
    return {
        'status': 'FAIL' if errors else 'UNVERIFIED',
        'evidence_status': evidence,
        'semantic_status': 'UNVERIFIED',
        'learning_focus': data.get('learning_focus', ''),
        'missing_fields': missing,
        'errors': errors,
        'anchors': rows,
        'reason': '자동 결과는 근거 위치만 확인. 배움·재미·인과·설교성·사실 정확성은 Claude가 원문 근거로 별도 판정. 회상 구조의 제시 순서는 강제하지 않음',
    }


def audit_intensity(body: str, audit_map: dict[str, Any] | None, selected_mode=None) -> dict[str, Any]:
    """Locate supplied dramatic anchors; never infer quality or policy approval."""
    data = (audit_map or {}).get("intensity") or {}
    if not isinstance(data, dict):
        return {"status": "FAIL", "evidence_status": "FAIL", "semantic_status": "UNVERIFIED",
                "errors": ["intensity는 JSON 객체여야 함"], "anchors": []}
    mode = selected_mode or data.get("mode") or "UNSELECTED"
    result = {"mode": mode, "selection_source": "current_request" if selected_mode else data.get("selection_source", "unconfirmed"),
              "status": "UNVERIFIED", "evidence_status": "UNVERIFIED", "semantic_status": "UNVERIFIED",
              "pressure_mechanism": data.get("pressure_mechanism", ""), "loss_at_stake": data.get("loss_at_stake", ""),
              "anchors": [], "errors": [], "missing_fields": [], "review_notes": [],
              "reason": "앵커 존재·고유성·제시 순서만 자동 확인. Claude가 원문으로 갈등·선택·대가·결과·재미를 검토해야 함"}
    if mode == "UNSELECTED":
        result["missing_fields"].append("현재 요청 또는 사용자가 확정한 작품 강도")
        result["reason"] = "강도 미확정; 기존형으로 가정하지 않으며 다른 검토는 계속함"
        return result
    if mode not in ("existing", "strong"):
        result["errors"].append("mode는 existing 또는 strong이어야 함")
    points = data.get("kill_points") or []
    if not isinstance(points, list):
        result["errors"].append("kill_points는 배열이어야 함")
        points = []
    if mode == "strong":
        for key in ("pressure_mechanism", "loss_at_stake"):
            if not data.get(key):
                result["missing_fields"].append(key)
        ids = [item.get("id") for item in points if isinstance(item, dict)]
        if len(points) != 2 or sorted(str(i) for i in ids) != ["KP1", "KP2"]:
            result["missing_fields"].append("KP1·KP2 두 킬포인트")
    elif mode == "existing" and not points and not result["errors"]:
        result.update(status="NOT_APPLICABLE", evidence_status="NOT_APPLICABLE",
                      reason="기존형에 강화형 킬포인트를 강제하지 않음. 선택 구조와 원문 의미는 별도 검토")
        return result
    for index, item in enumerate(points, 1):
        if not isinstance(item, dict):
            result["errors"].append(f"킬포인트 {index}: JSON 객체 필요")
            continue
        pid = str(item.get("id") or f"KP{index}")
        clues = item.get("clue_anchors") or []
        if not isinstance(clues, list) or len(clues) != 2:
            result["missing_fields"].append(f"{pid}.clue_anchors 두 개")
            clues = []
        fields = [(f"clue_{i}", a) for i, a in enumerate(clues, 1)]
        fields += [("turn", item.get("turn_anchor")), ("consequence", item.get("consequence_anchor"))]
        positions = {}
        for label, anchor in fields:
            if not isinstance(anchor, str) or not anchor.strip():
                result["missing_fields"].append(f"{pid}.{label}")
                continue
            count = body.count(anchor)
            if count != 1:
                result["errors"].append(f"{pid}.{label}: 원문 일치 {count}곳, 고유한 본편 원문 필요")
                continue
            offset = body.find(anchor)
            positions[label] = (offset, offset + len(anchor))
            result["anchors"].append({"id": pid, "role": label, "anchor": anchor,
                                       "line": body[:offset].count("\n") + 1, "offset": offset})
        if len(positions) == 4:
            ordered = sorted(positions.values())
            if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
                result["errors"].append(f"{pid}: 단서·전환·결과는 구별되는 원문으로 지정")
            if not (max(positions["clue_1"][1], positions["clue_2"][1]) <= positions["turn"][0]
                    and positions["turn"][1] <= positions["consequence"][0]):
                result["review_notes"].append(f"{pid}: 비선형 제시 또는 순서 불일치. 실제 인과·단서의 공정성을 원문으로 검토")
    if result["errors"]:
        result.update(status="FAIL", evidence_status="FAIL")
    elif result["missing_fields"]:
        result["evidence_status"] = "UNVERIFIED"
    elif result["review_notes"]:
        result["evidence_status"] = "REVIEW_REQUIRED"
    else:
        result["evidence_status"] = "PASS"
    return result


def audit_chapters(body: str, audit_map: dict[str, Any] | None) -> dict[str, Any]:
    """Measure body chapter ranges without adding chapter headings to scripts."""
    chapters = (audit_map or {}).get("chapters")
    if not chapters:
        return {"status": "UNVERIFIED", "reason": "챕터별 시작 앵커 미제공; 합본 수치는 별도로 측정", "chapters": []}
    if not isinstance(chapters, list) or not all(isinstance(c, dict) for c in chapters):
        return {"status": "FAIL", "reason": "chapters는 챕터 객체 목록이어야 함", "chapters": []}
    starts = []
    for chapter in chapters:
        anchor = chapter.get("start_anchor")
        if not isinstance(anchor, str) or not anchor.strip():
            return {"status": "UNVERIFIED", "reason": "실제 챕터 시작 원문 미확인; 임의 등분 금지", "chapters": []}
        if body.count(anchor) != 1:
            return {"status": "FAIL", "reason": "각 start_anchor는 본편에서 한 번만 등장하는 챕터 첫 원문이어야 함", "chapters": []}
        starts.append(body.index(anchor))
    if body[:starts[0]].strip() or any(a >= b for a, b in zip(starts, starts[1:])):
        return {"status": "FAIL", "reason": "챕터 경계 누락 또는 순서 역전", "chapters": []}
    rows = []
    for index, chapter in enumerate(chapters):
        end = starts[index + 1] if index + 1 < len(starts) else len(body)
        metrics = text_metrics(body[starts[index]:end])
        rows.append({"id": chapter.get("id", index + 1), "characters_raw": metrics["characters_raw"], "dialogue_ratio_percent": metrics["dialogue_ratio_percent"], "dialogue_ratio_status": metrics["dialogue_ratio_status"]})
    return {"status": "FAIL" if any(row["dialogue_ratio_status"] == "FAIL" for row in rows) else "PASS", "chapters": rows, "semantic_review": "각 챕터에 다른 인물 2명 이상·응답이 이어지는 3턴 이상 실제 대화 1회 이상인지 별도 확인"}


def audit_fixed_layout(body: str, intro: str | None, audit_map: dict[str, Any] | None) -> dict[str, Any]:
    """Check fixed chapter metadata, quotas and evidence locations, not story meaning."""
    result = {"status": "UNVERIFIED", "errors": [], "missing": [], "chapters": [],
              "semantic_status": "MANUAL_REVIEW_REQUIRED",
              "reason": "자동 검사는 5막 20챕터 형식·할당량·근거 위치만 확인. 역할·인과·정서는 원문으로 별도 검토"}
    layout = (audit_map or {}).get("layout")
    if not isinstance(layout, dict):
        result["missing"].append("고정 틀 layout 기록")
        return result
    expected = {"format": "FIVE_ACTS_20_CHAPTERS", "act_count": 5, "chapter_count": 20,
                "chapters_per_act": 4, "target_chars": 35000, "length_scope": "intro_plus_body"}
    for key, value in expected.items():
        actual = layout.get(key)
        if actual is None or actual == "":
            result["missing"].append(key)
        elif actual != value or isinstance(value, int) and type(actual) is not int:
            result["errors"].append(f"{key}: {value} 필요")
    for key in ("main_emotion", "ending_method"):
        if not isinstance(layout.get(key), str) or not layout[key].strip():
            result["missing"].append(key)
    primary = (audit_map or {}).get("primary_structure")
    support = (audit_map or {}).get("support_structure")
    valid = {f"STR{i:02d}" for i in range(1, 11)}
    if not isinstance(primary, str) or primary not in valid:
        result["missing"].append("실제 주 작법 STR01~STR10")
    if support not in (None, "", "없음", "none", "NONE"):
        if not isinstance(support, str) or support not in valid or support == primary:
            result["errors"].append("보조 작법은 주 작법과 다른 STR 코드 1개 또는 없음")
    chapters = (audit_map or {}).get("chapters")
    if not isinstance(chapters, list) or not chapters:
        result["missing"].append("실제 20개 챕터 지도")
        chapters = []
    if chapters and (len(chapters) != 20 or not all(isinstance(c, dict) for c in chapters)):
        result["errors"].append("chapters는 20개 객체여야 함")
        chapters = []
    if intro is None or not intro.strip():
        result["missing"].append("별도 인트로의 전체 실제 발화 원문")
    starts = []
    for index, chapter in enumerate(chapters, 1):
        expected_act = (index - 1) // 4 + 1
        if type(chapter.get("id")) is not int or chapter["id"] != index:
            result["errors"].append(f"CH{index:02d}: 챕터 id 순서 불일치")
        if type(chapter.get("act")) is not int or chapter["act"] != expected_act:
            result["errors"].append(f"CH{index:02d}: {expected_act}막이어야 함")
        if chapter.get("role_id") != f"CH{index:02d}":
            result["errors"].append(f"CH{index:02d}: 고정 역할 코드 불일치")
        anchor = chapter.get("start_anchor")
        if not isinstance(anchor, str) or not anchor.strip():
            result["missing"].append(f"CH{index:02d}: 첫 문장 근거")
            continue
        if body.count(anchor) != 1:
            result["errors"].append(f"CH{index:02d}: 첫 문장은 본편에 정확히 한 번 있어야 함")
            continue
        starts.append(body.index(anchor))
    if len(starts) == 20:
        if body[:starts[0]].strip() or any(a >= b for a, b in zip(starts, starts[1:])):
            result["errors"].append("챕터 시작 경계 누락·역전")
        elif intro is not None and intro.strip():
            needed = 35000 - len(intro.strip()) - 38
            if needed <= 0:
                result["errors"].append("인트로 원문 식별 오류: 분량 계산 불가")
            else:
                base, remainder = divmod(needed, 20)
                for index, chapter in enumerate(chapters):
                    end = starts[index + 1] if index < 19 else len(body)
                    chunk = body[starts[index]:end].strip()
                    quota = base + (1 if index < remainder else 0)
                    evidence = chapter.get("role_evidence_anchor")
                    evidence_status = "UNVERIFIED"
                    if not isinstance(evidence, str) or not evidence.strip():
                        result["missing"].append(f"CH{index+1:02d}: 역할 원문 근거")
                    elif chunk.count(evidence) != 1 or body.count(evidence) != 1:
                        result["errors"].append(f"CH{index+1:02d}: 역할 근거가 해당 챕터의 고유 원문과 다름")
                        evidence_status = "FAIL"
                    else:
                        evidence_status = "PASS"
                    if len(chunk) < quota:
                        result["errors"].append(f"CH{index+1:02d}: 최소 {quota}자 중 {len(chunk)}자")
                    result["chapters"].append({"id": index+1, "act": index//4+1,
                        "characters": len(chunk), "minimum": quota,
                        "length_status": "PASS" if len(chunk) >= quota else "FAIL",
                        "role_evidence_status": evidence_status})
                ending = layout.get("ending_evidence_anchor")
                last_chunk = body[starts[-1]:].strip()
                if not isinstance(ending, str) or not ending.strip():
                    result["missing"].append("CH20 결말 정서 원문 근거")
                elif last_chunk.count(ending) != 1 or body.count(ending) != 1:
                    result["errors"].append("결말 정서 근거는 CH20의 고유 원문이어야 함")
    result["status"] = "FAIL" if result["errors"] else "UNVERIFIED" if result["missing"] else "PASS"
    return result


def audit_story_quality(body: str, audit_map: dict[str, Any] | None) -> dict[str, Any]:
    """Locate evidence for the three story checks; do not score empathy or fun."""
    result = {"status": "UNVERIFIED", "evidence_status": "UNVERIFIED",
              "semantic_status": "MANUAL_REVIEW_REQUIRED", "errors": [], "missing": [],
              "review_notes": [], "anchors": [], "major_scene_count": 0,
              "reason": "원문 존재·위치·목록 대응만 측정. 공감·인과·변화의 설득력은 원문 의미 검토 필요"}
    data = (audit_map or {}).get("story_quality")
    if data is None:
        result["missing"].append("story_quality 근거 지도")
        return result
    if not isinstance(data, dict):
        result.update(status="FAIL", evidence_status="FAIL")
        result["errors"].append("story_quality는 객체여야 함")
        return result

    def required_text(obj, key, label):
        value = obj.get(key)
        if not isinstance(value, str) or not value.strip():
            result["missing"].append(label)
            return None
        return value

    def anchor(obj, key, label):
        value = required_text(obj, key, label)
        if value is None:
            return None
        count = body.count(value)
        if count != 1:
            result["errors"].append(f"{label}: 본편 고유 원문 필요, 일치 {count}곳")
            return None
        offset = body.index(value)
        result["anchors"].append({"field": label, "text": value, "offset": offset,
                                   "line": body[:offset].count("\n") + 1})
        return offset

    def obj_at(key):
        value = data.get(key)
        if not isinstance(value, dict):
            result["missing"].append(key + " 객체")
            return {}
        return value

    # Boundaries come from original chapter anchors, never from equal division.
    chapter_rows = (audit_map or {}).get("chapters")
    bounds = None
    if isinstance(chapter_rows, list) and len(chapter_rows) == 20:
        starts = []
        for row in chapter_rows:
            a = row.get("start_anchor") if isinstance(row, dict) else None
            if not isinstance(a, str) or not a or body.count(a) != 1:
                break
            starts.append(body.index(a))
        if len(starts) == 20 and not body[:starts[0]].strip() and all(a < b for a,b in zip(starts,starts[1:])):
            bounds = starts + [len(body)]
    if bounds is None:
        result["missing"].append("초반·후반 위치를 확인할 실제 20챕터 경계")

    scope = obj_at("method_scope")
    required_text(scope, "primary_usage", "method_scope.primary_usage")
    required_text(scope, "support_usage", "method_scope.support_usage(없으면 없음)")
    checks = scope.get("common_checks")
    if not isinstance(checks, list) or not all(k in checks for k in ("causality", "story_spine", "scene_change")):
        result["missing"].append("공통 인과·연결 요약·장면 변화 점검")
    spine = data.get("story_spine")
    if not isinstance(spine, list) or len(spine) != 6 or not all(isinstance(x,str) and x.strip() for x in spine):
        result["missing"].append("실제 사건을 잇는 story_spine 여섯 문장")

    care = obj_at("early_care")
    required_text(care, "why_care", "early_care.why_care")
    care_chapter = care.get("chapter")
    if care_chapter is None:
        result["missing"].append("early_care.chapter")
    elif type(care_chapter) is not int or care_chapter not in (1, 2):
        result["errors"].append("초반 공감 행동은 CH01~CH02")
    for key in ("action_anchor", "cost_anchor"):
        position = anchor(care, key, "early_care." + key)
        if position is not None and bounds:
            if not bounds[0] <= position < bounds[2]:
                result["errors"].append("early_care." + key + ": CH01~CH02 밖의 원문")
            elif type(care_chapter) is int and care_chapter in (1,2) and not bounds[care_chapter-1] <= position < bounds[care_chapter]:
                result["errors"].append("early_care.chapter와 실제 원문 위치 불일치")

    change = obj_at("changed_choice")
    kind = change.get("change_type")
    if not kind:
        result["missing"].append("changed_choice.change_type")
    elif kind not in ("growth", "steadfast", "tragic", "relationship"):
        result["errors"].append("change_type: growth/steadfast/tragic/relationship 중 실제 변화 유형")
    required_text(change, "change_reason", "changed_choice.change_reason")
    positions = {}
    for key in ("initial_anchor", "pressure_anchor", "choice_anchor", "consequence_anchor"):
        pos = anchor(change, key, "changed_choice." + key)
        if pos is not None:
            positions[key] = pos
            if bounds and key == "initial_anchor" and not bounds[0] <= pos < bounds[4]:
                result["errors"].append("초기 선택 근거는 CH01~CH04")
            if bounds and key != "initial_anchor" and not bounds[15] <= pos < bounds[20]:
                result["errors"].append("마지막 갈림길·선택·결과 근거는 CH16~CH20")
    if len(positions) == 4 and list(positions.values()) != sorted(positions.values()):
        result["review_notes"].append("초기 선택→후반 갈림길→행동→결과의 제시 순서 확인; 비선형 구성은 의미 검토")

    ids = data.get("major_scene_ids")
    rows = data.get("scene_checks")
    valid_ids = isinstance(ids, list) and bool(ids) and all(isinstance(x,str) and x.strip() for x in ids)
    valid_rows = isinstance(rows, list) and bool(rows) and all(isinstance(x,dict) for x in rows)
    if not valid_ids:
        result["missing"].append("기획·원문에서 독립 확인한 major_scene_ids")
    elif len(set(ids)) != len(ids):
        result["errors"].append("major_scene_ids 중복")
    if not valid_rows:
        result["missing"].append("모든 주요 장면의 scene_checks")
    else:
        result["major_scene_count"] = len(rows)
        row_ids = [row.get("scene_id") for row in rows]
        if not all(isinstance(x,str) and x.strip() for x in row_ids):
            result["missing"].append("scene_checks의 scene_id")
        elif valid_ids and (set(row_ids) != set(ids) or len(row_ids) != len(set(row_ids))):
            result["errors"].append("계획한 주요 장면과 검토 목록의 누락·추가·중복")
        for index, row in enumerate(rows,1):
            label = str(row.get("scene_id") or index)
            chapter = row.get("chapter")
            if chapter is None:
                result["missing"].append(label + ".chapter")
            elif type(chapter) is not int or not 1 <= chapter <= 20:
                result["errors"].append(label + ": chapter는 1~20 정수")
            for key in ("trigger_anchor", "choice_anchor", "result_anchor"):
                pos = anchor(row, key, label + "." + key)
                if pos is not None and bounds and type(chapter) is int and 1 <= chapter <= 20:
                    if not bounds[chapter-1] <= pos < bounds[chapter]:
                        result["errors"].append(label + "." + key + ": 해당 챕터 밖의 근거")
            for key in ("state_before", "state_after", "causal_link", "next_action"):
                required_text(row, key, label + "." + key)
            if row.get("state_before") and row.get("state_before") == row.get("state_after"):
                result["review_notes"].append(label + ": 전후 상태가 같은 설명; 실제 변화 확인")
    state = "FAIL" if result["errors"] else "UNVERIFIED" if result["missing"] else "PASS"
    result.update(status=state, evidence_status=state)
    return result


def combined_length_metrics(body: str, intro: str | None, target: int) -> dict[str, Any]:
    body_count = len(body.strip())
    intro_count = len(intro.strip()) if intro is not None else None
    total = body_count + intro_count if intro_count is not None else None
    return {"status": "UNVERIFIED" if total is None else "PASS" if total >= target else "FAIL",
            "characters_raw": total, "body_characters": body_count, "intro_characters": intro_count,
            "minimum": target, "difference_from_target": total-target if total is not None else None,
            "counting_rule": "본편.strip + 별도 실제 인트로 발화.strip; 가상 파일 경계 줄바꿈 제외"}


def narration_breathing_candidates(sentences: list[str]) -> dict[str, Any]:
    """Supply candidates for semantic comma review, never a comma quota.

    These are text counts, not voice timing. The analyzer never edits sources.
    """
    rows = []
    for number, sentence in enumerate(sentences, 1):
        content = re.sub(r"[.!?…]+$", "", sentence).strip()
        if not content:
            continue
        rows.append({
            "narration_sentence_number": number,
            "sentence": sentence,
            "characters_no_whitespace": len(re.sub(r"\s", "", content)),
            "comma_count": content.count(",") + content.count("，"),
            "longest_segment_without_comma": max(len(re.sub(r"\s", "", part)) for part in re.split(r"[,，]", content)),
        })
    rows.sort(key=lambda row: (-row["longest_segment_without_comma"], -row["characters_no_whitespace"], row["narration_sentence_number"]))
    return {
        "status": "MANUAL_REVIEW_REQUIRED" if rows else "NOT_APPLICABLE",
        "narration_sentence_count": len(rows),
        "candidates": rows[:5],
        "rule": "가장 긴 무쉼표 구간부터 검토; 길이·쉼표 개수만으로 실패 판정하지 않음. 의미 경계와 과한 끊김을 사람이 확인",
        "timing_measured": False,
    }


def text_metrics(text: str) -> dict[str, Any]:
    ascii_quote_count = text.count('"')
    curly_open = text.count("“")
    curly_close = text.count("”")
    quoted = re.findall(r'"([^\"]*)"|“([^”]*)”', text, flags=re.S)
    quoted_text = "".join(a or b for a, b in quoted)
    narration_only = re.sub(r'"[^"]*"|“[^”]*”', "", text, flags=re.S)
    narration_sentences = [
        item.strip()
        for item in re.findall(r"[^.!?\n]+(?:[.!?]+|$)", narration_only)
        if item.strip()
    ]
    informal_sentences = [
        sentence for sentence in narration_sentences
        if re.search(r"(?<!니)다$", re.sub(r"[.!?…]+$", "", sentence).rstrip())
    ]
    modern_narration_endings = []
    for sentence in narration_sentences:
        sentence_without_punctuation = re.sub(
            r"[.!?…]+$", "", sentence.strip()
        )
        ending_match = MODERN_NARRATION_ENDING_PATTERN.search(
            sentence_without_punctuation
        )
        if ending_match:
            modern_narration_endings.append(
                {
                    "ending": ending_match.group(1),
                    "sentence": sentence,
                }
            )
    body_tags = re.findall(r"@[A-Za-z][A-Za-z0-9_]*", text)
    dialogue_chars = len(re.sub(r'\s', '', quoted_text))
    ratio_denominator = len(re.sub(r'[\s"]', '', text))
    dialogue_ratio = round(dialogue_chars / max(1, ratio_denominator) * 100, 4)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    duplicate_paragraphs = [
        {"count": count, "text": para[:160]}
        for para, count in Counter(paragraphs).most_common()
        if count > 1
    ][:10]
    phrase_counts = {
        phrase: text.count(phrase)
        for phrase in RECURRING_PHRASES
        if text.count(phrase) >= 3
    }
    return {
        "characters_raw": len(text),
        "characters_no_whitespace": len(re.sub(r"\s", "", text)),
        "hangul_syllables": len(re.findall(r"[가-힣]", text)),
        "whitespace_tokens": len(text.split()),
        "paragraphs": len(paragraphs),
        "quoted_spans": len(quoted),
        "quoted_characters": len(quoted_text),
        "dialogue_ratio_percent": dialogue_ratio,
        "dialogue_ratio_status": "PASS" if ratio_denominator > 0 and dialogue_chars * 100 <= ratio_denominator * 30 else "FAIL",
        "dialogue_characters_no_whitespace": dialogue_chars,
        "dialogue_ratio_denominator": ratio_denominator,
        "dialogue_ratio_rule": "공백·줄바꿈·ASCII 큰따옴표 제외; 최대 30%; 반올림 전 정수로 판정",
        "plain_format": plain_script_format(text),
        "narration_rhythm": narration_rhythm(text),
        "narration_breathing": narration_breathing_candidates(narration_sentences),
        "informal_narration_status": "PASS" if not informal_sentences else "FAIL",
        "informal_narration_count": len(informal_sentences),
        "informal_narration_examples": informal_sentences[:10],
        "modern_narration_ending_status": (
            "PASS" if not modern_narration_endings else "FAIL"
        ),
        "modern_narration_ending_count": len(modern_narration_endings),
        "modern_narration_ending_examples": modern_narration_endings[:10],
        "body_character_tag_status": "PASS" if not body_tags else "FAIL",
        "body_character_tag_count": len(body_tags),
        "body_character_tag_examples": body_tags[:10],
        "quote_balance": {
            "status": "PASS"
            if ascii_quote_count % 2 == 0 and curly_open == curly_close
            else "FAIL",
            "ascii_double_quotes": ascii_quote_count,
            "curly_open": curly_open,
            "curly_close": curly_close,
        },
        "duplicate_paragraphs": duplicate_paragraphs,
        "recurring_phrase_counts": phrase_counts,
    }


def load_audit_map(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    data = json.loads(read_text(path))
    if not isinstance(data, dict):
        raise ValueError("audit map must be a JSON object")
    return data


def extract_intro_script_block(text: str) -> str:
    """Return only the integrated [INTRO SCRIPT] block when present."""
    match = re.search(
        r"(?ms)^\[INTRO SCRIPT\]\s*(.*?)(?=^\[[^\n]+\]|\Z)",
        text,
    )
    return match.group(1).strip() if match else ""


def intro_script_lines(text: str) -> list[str]:
    """Return all editorial voice-track lines, without blank lines."""
    block = extract_intro_script_block(text) or text
    return [line.strip() for line in block.splitlines() if line.strip()]


def intro_script_text(text: str) -> str:
    return "\n".join(intro_script_lines(text))


def split_intro_body(
    script: str,
    intro_path: str | None,
    requested_marker: str | None,
    intro_reference: str | None = None,
    script_layout: str = "body_only",
) -> tuple[str | None, str, dict[str, Any]]:
    reference = read_text(intro_path).strip() if intro_path else intro_reference
    if script_layout == "body_only":
        intro = intro_script_text(reference) if reference else None
        expected = intro_script_lines(intro) if intro else []
        actual = [line.strip() for line in script.splitlines() if line.strip()]
        duplicate = bool(expected and actual[:len(expected)] == expected)
        return intro, script, {
            "method": "body_only", "offset": 0,
            "intro_duplicate_prefix": duplicate,
            "intro_reference": "separate_file" if intro_path else "integrated_direction" if reference else None,
            "reason": "주대본 전체가 본편; 인트로 미제공이어도 본편 검사 계속",
        }
    if script_layout != "legacy_merged":
        raise ValueError("script_layout은 body_only 또는 legacy_merged여야 함")
    if reference:
        intro = intro_script_text(reference)
        expected = intro_script_lines(intro)
        nonempty = [m for m in re.finditer(r"(?m)^.*$", script) if m.group().strip()]
        prefix = [m.group().strip() for m in nonempty[:len(expected)]]
        if expected and prefix == expected:
            offset = nonempty[len(expected) - 1].end()
            return intro, script[offset:].lstrip(), {
                "method": "exact_intro_prefix", "offset": offset,
                "intro_reference": "separate_file" if intro_path else "integrated_direction",
            }
        return intro, script, {
            "method": "intro_prefix_mismatch", "marker": requested_marker,
            "reason": "INTRO SCRIPT 전체와 기본 대본 시작부 불일치; 본편 경계를 추정하지 않음",
        }

    marker = requested_marker
    if marker:
        pos = script.find(marker)
        if pos > 0:
            return (
                script[:pos].strip(),
                script[pos:],
                {"method": "explicit_marker", "marker": marker, "offset": pos},
            )
        return None, script, {"method": "marker_not_found", "marker": marker}

    patterns = (
        r"(?m)^옛날 옛적(?:[,.\s]|$)",
        r"(?m)^아주 먼 옛날(?:[,.\s]|$)",
        r"(?m)^옛날 어느(?:[,.\s]|$)",
    )
    candidates: list[tuple[int, str]] = []
    for pattern in patterns:
        match = re.search(pattern, script)
        if match and match.start() > 0:
            candidates.append((match.start(), match.group(0).strip()))
    if candidates:
        pos, found = min(candidates)
        return (
            script[:pos].strip(),
            script[pos:],
            {"method": "auto_marker", "marker": found, "offset": pos},
        )
    return None, script, {"method": "not_detected", "marker": None}


def intro_metrics(intro: str | None) -> dict[str, Any]:
    if intro is None:
        return {
            "status": "UNVERIFIED",
            "reason": "별도 인트로 또는 확실한 본편 시작 경계를 찾지 못함",
        }
    raw = len(intro)
    hangul = len(re.findall(r"[가-힣]", intro))
    return {
        "status": "PASS",
        "characters_raw": raw,
        "characters_no_whitespace": len(re.sub(r"\s", "", intro)),
        "hangul_syllables": hangul,
        "timing_basis": "가변 씬 최종 편집 타임코드 합계 60초; 실제 영상/음원은 미검증",
    }


def length_metrics(script: str, target: int | None) -> dict[str, Any]:
    if target is None:
        return {
            "status": "UNVERIFIED",
            "characters_raw": len(script.strip()),
            "reason": "선택한 합본 목표 글자 수 없음",
        }
    return {
        "status": "PASS" if len(script.strip()) >= target else "FAIL",
        "characters_raw": len(script.strip()),
        "minimum": target,
        "difference_from_target": len(script.strip()) - target,
    }


def find_anchor(text: str, anchor: str) -> dict[str, Any]:
    if not anchor:
        return {"found": False, "reason": "empty_anchor"}
    count = text.count(anchor)
    pos = text.find(anchor)
    return {
        "found": pos >= 0,
        "offset": pos if pos >= 0 else None,
        "occurrences": count,
        "unique": count == 1,
    }


def audit_beats(body: str, audit_map: dict[str, Any] | None) -> dict[str, Any]:
    primary = (audit_map or {}).get("primary_structure")
    if not primary:
        return {"status": "UNVERIFIED", "reason": "주 구조 미확인; 15비트를 임의 강제하지 않음"}
    if primary not in {f"STR{i:02d}" for i in range(1, 11)}:
        return {"status": "FAIL", "reason": "primary_structure는 STR01~STR10 중 실제 확정 구조 ID여야 함"}
    if primary != "STR08":
        return {"status": "NOT_APPLICABLE", "reason": f"주 구조 {primary}; STR08 보조 사용 여부와 관계없이 15비트 위치 강제 제외. 선택 구조의 실제 기능은 별도 의미 검증"}
    if not audit_map or not audit_map.get("beats"):
        return {"status": "UNVERIFIED", "reason": "15비트 감사 지도 없음"}

    rows = []
    positions: list[tuple[int, int]] = []
    hard_fail = False
    ids_seen: set[int] = set()
    for item in audit_map.get("beats", []):
        beat_id = int(item.get("id", 0) or 0)
        ids_seen.add(beat_id)
        anchor = str(item.get("anchor", ""))
        found = find_anchor(body, anchor)
        pct = None
        if found.get("found"):
            offset = int(found["offset"])
            pct = round(offset / max(1, len(body)) * 100, 2)
            positions.append((beat_id, offset))
        expected = KEY_BEAT_RANGES.get(beat_id)
        if not found.get("found"):
            verdict = "FAIL"
            hard_fail = True
        elif found.get("occurrences") != 1:
            verdict = "WARN"
        elif expected and not (expected[0] <= float(pct) <= expected[1]):
            verdict = "FAIL"
            hard_fail = True
        else:
            verdict = "PASS"
        rows.append(
            {
                "id": beat_id,
                "name": item.get("name", ""),
                "anchor": anchor,
                "found": found.get("found", False),
                "occurrences": found.get("occurrences", 0),
                "position_percent": pct,
                "expected_percent": list(expected) if expected else None,
                "verdict": verdict,
            }
        )

    complete = ids_seen == set(range(1, 16)) and len(rows) == 15
    ordered = all(
        positions[i][0] < positions[i + 1][0]
        and positions[i][1] < positions[i + 1][1]
        for i in range(len(positions) - 1)
    ) and len(positions) == 15
    if not complete or not ordered:
        hard_fail = True

    by_id = {row["id"]: row for row in rows}
    midpoint_all_lost_distinct = "UNVERIFIED"
    if 9 in by_id and 11 in by_id and by_id[9]["found"] and by_id[11]["found"]:
        distance = float(by_id[11]["position_percent"]) - float(
            by_id[9]["position_percent"]
        )
        midpoint_all_lost_distinct = "PASS" if distance >= 8.0 else "FAIL"
        if midpoint_all_lost_distinct == "FAIL":
            hard_fail = True

    return {
        "status": "FAIL" if hard_fail else "PASS",
        "complete_15": complete,
        "strictly_ordered": ordered,
        "midpoint_all_lost_separation": midpoint_all_lost_distinct,
        "beats": rows,
    }


def audit_questions(body: str, audit_map: dict[str, Any] | None) -> dict[str, Any]:
    if not audit_map or not audit_map.get("questions"):
        return {"status": "UNVERIFIED", "reason": "궁금증 감사 지도 없음"}
    rows = []
    hard_fail = False
    for item in audit_map.get("questions", []):
        open_result = find_anchor(body, str(item.get("open_anchor", "")))
        answer_result = find_anchor(body, str(item.get("answer_anchor", "")))
        partial_anchor = str(item.get("partial_answer_anchor", ""))
        partial_result = find_anchor(body, partial_anchor) if partial_anchor else None
        clues = []
        for anchor in item.get("clue_anchors", []):
            clues.append({"anchor": anchor, **find_anchor(body, str(anchor))})
        found_clues = [c for c in clues if c.get("found")]
        distinct_offsets = {c.get("offset") for c in found_clues}
        chronological = (
            bool(open_result.get("found"))
            and bool(answer_result.get("found"))
            and int(open_result["offset"]) < int(answer_result["offset"])
            and all(
                int(open_result["offset"]) < int(c["offset"]) < int(answer_result["offset"])
                for c in found_clues
            )
        )
        verdict = (
            "PASS"
            if open_result.get("found")
            and answer_result.get("found")
            and len(distinct_offsets) >= 2
            and chronological
            else "FAIL"
        )
        if verdict == "FAIL":
            hard_fail = True
        rows.append(
            {
                "id": item.get("id", ""),
                "question": item.get("question", ""),
                "open": open_result,
                "clues": clues,
                "partial_answer": partial_result,
                "answer": answer_result,
                "chronological": chronological,
                "verdict": verdict,
                "semantic_answer_requires_manual_review": True,
            }
        )
    return {"status": "FAIL" if hard_fail else "PASS", "questions": rows}


SCENE_HEADER = re.compile(
    r"\[SCENE\s+(\d+)\s*\|\s*(?:TIMECODE|전체 타임코드)\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*(?:초)?\s*-\s*([0-9]+(?:\.[0-9]+)?)\s*(?:초)?\s*\|\s*"
    r"(?:DURATION|클립 길이)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:s|초)"
    r"(?:\s*\|\s*(?:TENSION|긴장도)\s*([0-9]+(?:\.[0-9]+)?)/10)?\s*\]",
    flags=re.I,
)

REQUIRED_SCENE_LABELS = (
    "GENERATE DURATION:",
    "EDIT TRIM:",
    "SOURCE ANCHOR:",
    "BGM CONTINUITY:",
    "CHARACTER TAGS:",
    "HOOK / REHOOK:",
    "DIALOGUE / NARRATION:",
    "SPEAKER / LIP SYNC:",
    "SFX / AMBIENCE:",
    "CHARACTER DESCRIPTION:",
    "ACTION / MOTION:",
    "ENVIRONMENT / PROPS:",
    "IMAGE PROMPT:",
    "VIDEO PROMPT:",
    "CAMERA:",
    "ENDING / TRANSITION:",
    "CONTINUITY LOCK:",
    "NEGATIVE PROMPT:",
)

TAG_PATTERN = re.compile(r"@[A-Za-z][A-Za-z0-9_]*")
BRIDGE_HEADER = "[BODY OPENING BRIDGE — AFTER 60.0s]"
JOSEON_PROVINCES = (
    "경기도",
    "강원도",
    "충청도",
    "전라도",
    "경상도",
    "황해도",
    "평안도",
    "함경도",
)


def section_between(block: str, start_label: str, end_label: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(start_label)}\s*(.*?)(?=^{re.escape(end_label)})",
        block,
    )
    return match.group(1) if match else ""


def trailing_blank_lines(text: str) -> int:
    count = 0
    for line in reversed(text.splitlines()):
        if line.strip():
            break
        count += 1
    return count


def first_declared_character_mentions_tagged(
    text: str,
    visible_tags: list[str],
) -> tuple[bool, list[str]]:
    """Check each character's first mention, not the prompt's first word."""
    untagged: list[str] = []
    for tag in visible_tags:
        bare = tag[1:]
        match = re.search(
            rf"(?<![A-Za-z0-9_])(@?{re.escape(bare)})(?![A-Za-z0-9_])",
            text,
            flags=re.I,
        )
        if match and not match.group(1).startswith("@"):
            untagged.append(tag)
    return not untagged, untagged


def prompt_field(block: str, label: str) -> str:
    """Return one top-level field including continuation lines."""
    found = re.search(
        rf"(?m)^{re.escape(label)}:[ \t]*(.*(?:\n(?![A-Z][A-Z0-9 /_-]*:|\[)[^\n]*)*)",
        block,
    )
    return found.group(1).strip() if found else ""


def exact_lines(text: str) -> list[str]:
    return [line.strip() for line in normalize_newlines(text).splitlines() if line.strip()]


def direction_metrics(
    direction: str | None,
    intro: str | None,
    style: str | None,
    body: str | None = None,
) -> dict[str, Any]:
    """Check the 60-second editing plan, never claim to measure rendered media."""
    if direction is None:
        return {"status": "UNVERIFIED", "reason": "통합 인트로 파일 없음"}
    direction = normalize_newlines(direction)
    matches = list(SCENE_HEADER.finditer(direction))
    if not matches:
        return {"status": "FAIL", "reason": "SCENE 헤더 또는 초 단위 타임코드 없음"}
    errors: list[str] = []
    required_sections = (
        "[INTRO SCRIPT]", "[대본 분석 데이터]", "[60초 시간표]",
        "[CHARACTER TAG MAP]", "[RETENTION HOOK MAP]",
        "[CAMERA MOVEMENT PLAN]", "[BGM MASTER]", BRIDGE_HEADER,
    )
    for label in required_sections:
        if direction.count(label) != 1:
            errors.append(f"필수 블록 1개 필요: {label}")
    voice_lines = exact_lines(extract_intro_script_block(direction))
    voice_ok = bool(voice_lines) and bool(re.fullmatch(r'"[^"\n]+"', voice_lines[0]))
    voice_ok = voice_ok and all(
        re.search(r"[가-힣]", line)
        and not TAG_PATTERN.search(line)
        and not re.match(r"(?:SCENE|NONE|나레이션\s*:|[가-힣 ]{1,20}\s*:)", line, re.I)
        for line in voice_lines
    )
    if not voice_ok:
        errors.append("INTRO SCRIPT는 무표시 실제 발화 원문이며 첫 줄은 직접 대사여야 함")

    def named_section(label: str) -> str:
        found = re.search(rf"(?ms)^{re.escape(label)}[ \t]*\n(.*?)(?=^\[|\Z)", direction)
        return found.group(1).strip() if found else ""

    tag_map = named_section("[CHARACTER TAG MAP]")
    registered_tags = set(TAG_PATTERN.findall(tag_map))
    bgm = named_section("[BGM MASTER]")
    bgm_id = re.search(r"(?m)^BGM ID:[ \t]*(\S+)[ \t]*$", bgm)
    bgm_duration = re.search(r"(?m)^DURATION:[ \t]*([\d.]+)s[ \t]*$", bgm)
    bgm_ok = bool(bgm_id and bgm_id.group(1) == "MASTER_01" and bgm_duration)
    if bgm_ok:
        try:
            bgm_ok = abs(float(bgm_duration.group(1)) - 60.0) < 0.001
        except ValueError:
            bgm_ok = False
    if not prompt_field(bgm, "BGM PROMPT"):
        bgm_ok = False
    if not bgm_ok:
        errors.append("MASTER_01 하나의 60초 음악과 실제 BGM PROMPT 필요")

    speech_pattern = re.compile(
        r'^([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)s\s*\|\s*'
        r'(@[A-Za-z][A-Za-z0-9_]*|NARRATOR)\s*\|\s*'
        r'(ON_SCREEN|OFF_SCREEN)\s*\|\s*("[^"\n]+")$'
    )
    video_speech_pattern = re.compile(
        r'(?m)^(DIALOGUE|OFFSCREEN DIALOGUE|NARRATION) at '
        r'([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)s: '
        r'(@[A-Za-z][A-Za-z0-9_]*|NARRATOR) says in Korean exactly: ("[^"\n]+")\s*$'
    )
    rows = []
    spoken_in_order: list[str] = []
    character_speakers: list[str] = []
    previous_end = 0.0
    total = 0.0
    actual_image_count = len(re.findall(r"(?m)^IMAGE PROMPT:", direction))
    actual_video_count = len(re.findall(r"(?m)^VIDEO PROMPT:", direction))
    for index, match in enumerate(matches):
        stop = matches[index + 1].start() if index + 1 < len(matches) else direction.find(BRIDGE_HEADER, match.end())
        if stop < 0:
            stop = len(direction)
        block = direction[match.start():stop]
        number = int(match.group(1))
        start, end, declared = map(float, match.group(2, 3, 4))
        tension = float(match.group(5)) if match.group(5) is not None else None
        issues = []
        missing = [label for label in REQUIRED_SCENE_LABELS if not re.search(rf"(?m)^{re.escape(label)}", block)]
        empty_fields = [label for label in REQUIRED_SCENE_LABELS if label not in missing and not prompt_field(block, label[:-1])]
        missing.extend(empty_fields)
        if missing:
            issues.append("필수 필드 누락: " + ", ".join(missing))
        timing_ok = number == index + 1 and abs(start - previous_end) < 0.001 and end > start and abs(end - start - declared) < 0.001
        if not timing_ok:
            issues.append("씬 번호·편집 타임코드·사용 길이 불일치")
        if tension is None or not 7 <= tension <= 10:
            issues.append("긴장도 7~10 범위와 실제 행동 근거 필요")
        generated_match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", prompt_field(block, "GENERATE DURATION"))
        trim_match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)s", prompt_field(block, "EDIT TRIM"))
        generated = float(generated_match.group(1)) if generated_match else 0.0
        trim_start, trim_end = (map(float, trim_match.groups()) if trim_match else (0.0, 0.0))
        trim_ok = bool(generated_match and trim_match) and 0 <= trim_start < trim_end <= generated and abs(trim_end - trim_start - declared) < 0.001
        if not trim_ok:
            issues.append("생성 길이·원본 사용 구간·편집 길이 불일치")

        image_segment = section_between(block, "IMAGE PROMPT:", "VIDEO PROMPT:")
        video_segment = section_between(block, "VIDEO PROMPT:", "CAMERA:")
        image_text, video_text = image_segment.strip(), video_segment.strip()
        image_count = len(re.findall(r"(?m)^IMAGE PROMPT:", block))
        video_count = len(re.findall(r"(?m)^VIDEO PROMPT:", block))
        if not image_text or not video_text or image_count != 1 or video_count != 1:
            issues.append("씬마다 IMAGE/VIDEO 실제 본문 각각 1개 필요")
        iv_blank, vc_blank = trailing_blank_lines(image_segment), trailing_blank_lines(video_segment)
        if min(iv_blank, vc_blank) < 3:
            issues.append("IMAGE→VIDEO 및 VIDEO→CAMERA 사이 빈 줄 최소 3줄 필요")
        if style == "2d" and not (STYLE_LOCK in image_text and STYLE_LOCK in video_text):
            issues.append("양쪽 프롬프트의 고정 2D 화풍 누락")
        if "16:9" not in image_text or "16:9" not in video_text:
            issues.append("양쪽 프롬프트의 16:9 화면비 누락")
        prompt_duration = re.match(r"Generate an? ([0-9]+(?:\.[0-9]+)?)-second video clip\.", video_text)
        use_match = re.search(r"Use ([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)s of this clip for global ([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)s\.", video_text)
        duration_ok = bool(prompt_duration and use_match)
        if duration_ok:
            duration_ok = abs(float(prompt_duration.group(1)) - generated) < 0.001 and all(
                abs(float(got) - expected) < 0.001
                for got, expected in zip(use_match.groups(), (trim_start, trim_end, start, end))
            )
        if not duration_ok:
            issues.append("VIDEO 본문 첫 줄의 생성 초수 또는 본문 편집 사용 구간 누락/불일치")

        visible_field = prompt_field(block, "CHARACTER TAGS")
        visible_tags = sorted(set(TAG_PATTERN.findall(visible_field)))
        tag_line_ok = bool(visible_tags) or visible_field.startswith("NONE")
        image_missing = sorted(set(visible_tags) - set(TAG_PATTERN.findall(image_text)))
        video_missing = sorted(set(visible_tags) - set(TAG_PATTERN.findall(video_text)))
        image_first, image_bad = first_declared_character_mentions_tagged(image_text, visible_tags)
        video_first, video_bad = first_declared_character_mentions_tagged(video_text, visible_tags)
        all_prompt_tags = set(TAG_PATTERN.findall(image_text + "\n" + video_text))
        tag_ok = tag_line_ok and not image_missing and not video_missing and image_first and video_first and all_prompt_tags <= registered_tags
        if not tag_ok:
            issues.append("캐릭터 지도·보이는 인물·IMAGE/VIDEO 첫 언급의 @태그 불일치")

        dialogue_lines = exact_lines(prompt_field(block, "DIALOGUE / NARRATION"))
        plan_lines = exact_lines(prompt_field(block, "SPEAKER / LIP SYNC"))
        plan = []
        plan_ok = True
        silent = dialogue_lines == ["NONE"]
        if silent:
            plan_ok = plan_lines == ["NONE"]
        else:
            for line in plan_lines:
                item = speech_pattern.fullmatch(line)
                if not item:
                    plan_ok = False
                    continue
                a, b = map(float, item.group(1, 2))
                speaker, placement, quote = item.group(3, 4, 5)
                if not trim_start <= a < b <= trim_end or (plan and a < plan[-1][1] - 0.001):
                    plan_ok = False
                if speaker == "NARRATOR" and placement != "OFF_SCREEN":
                    plan_ok = False
                if speaker != "NARRATOR" and (speaker not in registered_tags or (placement == "ON_SCREEN" and speaker not in visible_tags)):
                    plan_ok = False
                kind = "NARRATION" if speaker == "NARRATOR" else ("DIALOGUE" if placement == "ON_SCREEN" else "OFFSCREEN DIALOGUE")
                plan.append((a, b, speaker, placement, quote, kind))
            expected_lines = [item[4][1:-1] if item[2] == "NARRATOR" else item[4] for item in plan]
            plan_ok = plan_ok and bool(plan) and dialogue_lines == expected_lines
        video_plan = [(float(m.group(2)), float(m.group(3)), m.group(4), m.group(5), m.group(1)) for m in video_speech_pattern.finditer(video_text)]
        expected_video = [(a, b, speaker, quote, kind) for a, b, speaker, placement, quote, kind in plan]
        video_voice_ok = plan_ok and video_plan == expected_video
        # Catch unstructured extra spoken dialogue that the line parser did not consume.
        if len(re.findall(r"says in Korean exactly:", video_text, re.I)) != len(video_plan):
            video_voice_ok = False
        if silent:
            video_voice_ok = video_voice_ok and all(token in video_text.upper() for token in ("NO VOICE", "NO DIALOGUE", "NO LIP SYNC"))
        elif re.search(r"NO VOICE|NO DIALOGUE|ALL MOUTHS CLOSED", video_text, re.I):
            video_voice_ok = False
        if not video_voice_ok:
            issues.append("발화 계획·VIDEO 실제 한국어 원문·화자·순서·구간 또는 무음 지시 불일치")
        if not silent:
            spoken_in_order.extend(dialogue_lines)
            character_speakers.extend(item[2] for item in plan if item[2] != "NARRATOR")

        music_line = prompt_field(block, "BGM CONTINUITY")
        music_match = re.fullmatch(r"MASTER_01\s*\|\s*GLOBAL ([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)s\s*\|\s*CONTINUE, NO RESTART", music_line)
        music_video = re.search(r"Use continuous BGM MASTER_01 at global ([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)s in final edit; do not restart\.", video_text)
        music_ok = bool(music_match and music_video) and "NO GENERATED MUSIC" in video_text
        if music_ok:
            music_ok = all(abs(float(got) - expected) < 0.001 for got, expected in zip(music_match.groups() + music_video.groups(), (start, end, start, end)))
        if not music_ok:
            issues.append("MASTER_01 연속 음악 구간/NO GENERATED MUSIC 누락 또는 불일치")
        rows.append({
            "scene": number, "start": start, "end": end, "declared_length": declared,
            "actual_length": end - start, "generate_seconds": generated,
            "edit_trim": [trim_start, trim_end], "duration_in_video": "PASS" if duration_ok else "FAIL",
            "tension": tension, "tension_required": 7, "missing_fields": missing,
            "image_prompt_nonempty": "PASS" if image_text else "FAIL",
            "video_voice_policy": "PASS" if video_voice_ok else "FAIL",
            "voice_count": len(plan), "character_tags": visible_tags,
            "image_missing_character_tags": image_missing, "video_missing_character_tags": video_missing,
            "image_first_character_mention_tagged": "PASS" if image_first else "FAIL",
            "video_first_character_mention_tagged": "PASS" if video_first else "FAIL",
            "image_untagged_first_mentions": image_bad, "video_untagged_first_mentions": video_bad,
            "image_video_blank_lines": iv_blank, "video_camera_blank_lines": vc_blank,
            "bgm_continuity": "PASS" if music_ok else "FAIL",
            "errors": issues, "verdict": "FAIL" if issues else "PASS",
        })
        previous_end, total = end, total + declared

    total_ok = abs(total - 60.0) < 0.001 and abs(previous_end - 60.0) < 0.001
    if not total_ok:
        errors.append("최종 편집 타임코드 합계가 60.0초가 아님")
    prompt_count_ok = actual_image_count == actual_video_count == len(rows)
    if not prompt_count_ok:
        errors.append("씬 수와 IMAGE/VIDEO 본문 수 불일치")
    speech_exact = bool(voice_lines) and voice_lines == spoken_in_order
    coverage_exact = intro is not None and voice_lines == exact_lines(intro)
    first_exact = bool(voice_lines and spoken_in_order) and voice_lines[0] == spoken_in_order[0]
    if not speech_exact:
        errors.append("전체 씬 발화와 INTRO SCRIPT 내용·순서 불일치")
    if not coverage_exact:
        errors.append("INTRO SCRIPT와 별도 인트로 기준 원문 불일치")
    if rows and rows[0]["voice_count"] == 0:
        errors.append("Scene 1 실제 첫 대사가 없음")

    character_set = sorted(set(character_speakers))
    speaker_switches = sum(a != b for a, b in zip(character_speakers, character_speakers[1:]))
    dialogue_ok = len(character_set) >= 2 and speaker_switches >= 1
    if not dialogue_ok:
        errors.append("본편 인물 최소 두 명의 실제 교대 발화 필요; 자연스러운 응답 의미는 별도 검토")

    bridge_block = named_section(BRIDGE_HEADER)
    bridge = prompt_field(bridge_block, "BODY OPENING")
    transition_mode = prompt_field(bridge_block, "TRANSITION MODE")
    handoff_labels = ("LAST INTRO LINE", "LAST FRAME", "BODY FIRST FRAME", "MATCH POINT", "AUDIO BRIDGE", "BODY OPENING")
    handoff = {label: prompt_field(bridge_block, label) for label in handoff_labels}
    missing_handoff = [label for label, value in handoff.items() if not value]
    last_line_exact = bool(voice_lines) and handoff["LAST INTRO LINE"] == voice_lines[-1]
    mode_ok = transition_mode in {"CONTINUOUS_ACTION", "DIALOGUE_RESPONSE", "SOUND_BRIDGE"}
    bridge_lines = exact_lines(bridge)
    bridge_plain = bool(bridge_lines) and all(
        re.search(r"[가-힣]", line) and not TAG_PATTERN.search(line)
        and not re.match(r"(?:나레이션\s*:|[가-힣 ]{1,20}\s*:|#{1,6}\s)", line)
        for line in bridge_lines
    )
    after_last = direction.find(BRIDGE_HEADER) > matches[-1].start()
    body_match = "UNVERIFIED" if body is None else (
        "PASS" if bridge_lines and exact_lines(body)[:len(bridge_lines)] == bridge_lines else "FAIL"
    )
    bridge_ok = bool(mode_ok and not missing_handoff and last_line_exact and bridge_plain and after_last and body_match != "FAIL")
    if not bridge_ok:
        errors.append("60초 연결 블록 누락/형식 오류 또는 마지막 발화·본편 첫 원문 불일치")
    return {
        "status": "FAIL" if errors or any(row["verdict"] == "FAIL" for row in rows) else "PASS",
        "errors": errors, "scene_count": len(rows), "scene_count_rule": "variable",
        "total_exact_60_seconds": "PASS" if total_ok else "FAIL", "edit_duration_seconds": total,
        "image_prompt_label_count": actual_image_count, "video_prompt_label_count": actual_video_count,
        "prompt_count_matches_scenes": "PASS" if prompt_count_ok else "FAIL",
        "intro_script_line_count": len(voice_lines), "intro_script_format": "PASS" if voice_ok else "FAIL",
        "scene_speech_matches_intro": "PASS" if speech_exact else "FAIL",
        "first_dialogue_exact": "PASS" if first_exact else "FAIL",
        "bgm_master": "PASS" if bgm_ok else "FAIL",
        "source_coverage": {"status": "PASS" if coverage_exact else "FAIL", "comparison_basis": "별도 INTRO SCRIPT 기준 ↔ 모든 씬의 실제 발화 원문·순서"},
        "body_opening_bridge": {"status": "PASS" if bridge_ok else "FAIL", "text": bridge,
            "after_last_scene": "PASS" if after_last else "FAIL", "transition_mode": transition_mode,
            "missing_fields": missing_handoff, "last_intro_line_exact": "PASS" if last_line_exact else "FAIL",
            "body_opening_match": body_match, "handoff": handoff,
            "semantic_continuity": "MANUAL_REVIEW_REQUIRED"},
        "character_dialogue": {"status": "PASS" if dialogue_ok else "FAIL", "speakers": character_set,
            "speaker_switches": speaker_switches, "natural_response_semantics": "MANUAL_REVIEW_REQUIRED"},
        "rendered_media_status": "UNVERIFIED", "hook_semantics": "MANUAL_REVIEW_REQUIRED",
        "camera_continuity_semantics": "MANUAL_REVIEW_REQUIRED", "music_listening_status": "UNVERIFIED",
        "scenes": rows,
    }


def title_warnings(path: str | Path) -> list[str]:
    name = Path(path).stem
    checks = {
        "쫒": "표준어는 '쫓'인지 확인",
        "아니였다": "표준 표현은 '아니었다'",
        "알고보니": "일반적인 띄어쓰기는 '알고 보니'",
    }
    return [message for token, message in checks.items() if token in name]


def overall_status(report: dict[str, Any]) -> str:
    hard_sections = (
        report["length"].get("status"),
        report["script_metrics"]["quote_balance"].get("status"),
        report["script_metrics"].get("informal_narration_status"),
        report["script_metrics"].get("modern_narration_ending_status"),
        report["script_metrics"].get("body_character_tag_status"),
        report["script_metrics"]["plain_format"].get("status"),
        report["script_metrics"]["narration_rhythm"].get("status"),
        report["body_metrics"].get("dialogue_ratio_status"),
        report["dialogue_file"].get("status"),
        report["speaker_summary"].get("status"),
        report["educational_value"].get("status"),
        report.get("intensity", {}).get("status"),
        report["chapters"].get("status"),
        report.get("fixed_layout", {}).get("status"),
        report.get("intro_separation_status"),
        report.get("story_quality", {}).get("status"),
        report["intro"].get("status"),
        report["beats"].get("status"),
        report["questions"].get("status"),
        report["direction"].get("status"),
    )
    if "FAIL" in hard_sections:
        return "FAIL"
    if "UNVERIFIED" in hard_sections:
        return "UNVERIFIED"
    return "PASS"


def format_markdown(report: dict[str, Any]) -> str:
    m = report["script_metrics"]
    lines = [
        "# 야담 자동 측정 보고서",
        "",
        f"- 종합 자동 판정: **{report['overall_status']}**",
        f"- 주대본 파일 글자 수: {m['characters_raw']:,}",
        f"- 별도 인트로+본편 합계: {report['length'].get('characters_raw')} (가상 경계 제외)",
        f"- 공백 제외 글자 수: {m['characters_no_whitespace']:,}",
        f"- 한글 음절 수: {m['hangul_syllables']:,}",
        f"- 입력 대본 파일의 대사 비중: {m['dialogue_ratio_percent']:.4f}% (본편 상한은 아래 본편 검사; 구형 합본은 참고값)",
        f"- 인트로 제외 본편 대사 비중: {report['body_metrics']['dialogue_ratio_percent']:.4f}% (30% 이하: {report['body_metrics']['dialogue_ratio_status']})",
        f"- 챕터별 대사 비중: {report['chapters']['status']}",
        f"- 기본 대본에 화자 표시 없음 / 대사 독립 줄 형식: {m['plain_format']['status']}",
        f"- 기본 대본의 장 번호·제목·헤더·구분선: {m['plain_format']['heading_count']}건 ({m['plain_format']['heading_status']})",
        f"- 같은 나레이션 종결 계열 3연속: {m['narration_rhythm']['repeated_family_count']}건 ({m['narration_rhythm']['status']})",
        f"- 반말형 나레이션: {m['informal_narration_count']}건 ({m['informal_narration_status']})",
        f"- 현대식 나레이션 종결: {m['modern_narration_ending_count']}건 ({m['modern_narration_ending_status']})",
        f"- 본편 인물 태그: {m['body_character_tag_count']}건 ({m['body_character_tag_status']})",
        f"- 따옴표 짝: {m['quote_balance']['status']}",
        f"- 목표 분량: {report['length']['status']}",
        f"- 인트로 원문 식별: {report['intro']['status']}",
        f"- 주대본·인트로 분리: {report['intro_separation_status']}",
        f"- STR08 주 구조 전용 15비트 검사: {report['beats']['status']}",
        f"- 궁금증 지도: {report['questions']['status']}",
        f"- 인트로 연출: {report['direction']['status']}",
    ]
    quality = report.get("story_quality", {})
    lines.extend(["", "## 이야기 완성도 근거", "",
                  f"- 원문 근거: {quality.get('evidence_status', 'UNVERIFIED')}",
                  f"- 공감·인과·마지막 선택의 의미: {quality.get('semantic_status', 'MANUAL_REVIEW_REQUIRED')}",
                  f"- 주요 장면 검토 기록: {quality.get('major_scene_count', 0)}개",
                  "- 코드가 재미나 변화의 설득력을 판정하지 않습니다. 원문 의미 검토를 별도로 기록하세요."])
    for key in ("errors", "missing", "review_notes"):
        for message in quality.get(key, []):
            lines.append(f"- 확인: {message}")
    layout = report.get("fixed_layout", {})
    lines.extend(["", "## 고정 5막 20챕터", "",
                  f"- 형식·분량·역할 근거 위치: {layout.get('status', 'UNVERIFIED')}",
                  f"- {layout.get('reason', '')}"])
    for row in layout.get("chapters", []):
        lines.append(f"- {row['act']}막 CH{row['id']:02d}: {row['characters']}자 / 최소 {row['minimum']}자 ({row['length_status']}), 역할 근거 {row['role_evidence_status']}")
    for key in ("errors", "missing"):
        for message in layout.get(key, []):
            lines.append(f"- 확인할 항목: {message}")
    intensity = report.get("intensity", {})
    lines.extend(["", "## 강도·킬포인트 근거 확인", "",
                  f"- 선택 모드: {intensity.get('mode', '미확인')}",
                  f"- 근거 확인: {intensity.get('evidence_status', 'UNVERIFIED')}",
                  f"- 의미 검토: {intensity.get('semantic_status', 'UNVERIFIED')}",
                  f"- {intensity.get('reason', '')}"])
    for row in intensity.get("anchors", []):
        lines.append(f"- {row['id']} {row['role']} / 본편 {row['line']}줄: {row['anchor']}")
    for key in ("errors", "missing_fields", "review_notes"):
        for item in intensity.get(key, []):
            lines.append(f"- 확인할 항목: {item}")
    lines.extend(["", "## 정책 의미 검토 범위", "",
                  "- 삭제 위험 / 연령 제한 / 광고 친화성 / 채널 독창성: 자동 판정하지 않음.",
                  "- Claude가 원문과 공식 기준으로 네 범주를 따로 검토하고 내부 표현 기준과 구분할 것.",
                  "- 자동 PASS는 플랫폼 승인·제재 없음·수익화 보장이 아님. 미제공 매체·채널 전체는 미확인."])
    education = report["educational_value"]
    lines.extend(["", "## 교육적 가치·재미 근거 확인", "",
                  f"- 근거 위치 확인: {education['evidence_status']}",
                  f"- 교육적 가치·재미 의미 검토: {education['semantic_status']}",
                  f"- 핵심 배움: {education.get('learning_focus', '')}",
                  f"- {education.get('reason', '')}"])
    for row in education.get('anchors', []):
        lines.append(f"- {row['label']} / 본편 {row['line']}줄: {row['anchor']}")
    for error in education.get('errors', []):
        lines.append(f"- 근거 지도 보완: {error}")
    if education.get('missing_fields'):
        lines.append("- 미작성 내부 항목: " + ', '.join(education['missing_fields']))
    labeled = report["dialogue_file"]
    lines.extend([
        "", "## 추가 대사 파일", "",
        f"- 판정: {labeled['status']}",
        f"- 모든 나레이션·대사 구간 표시: {labeled['label_status']}",
        f"- 표시 제거 후 기본 대본과 내용·순서·문장부호 일치: {labeled['content_match_status']}",
        f"- 독립 작성한 화자 지도와 대조: {labeled['speaker_map_match_status']}",
        "- 이름·나레이션 표시는 기본 대본 분량과 대사 비중에 합산하지 않았습니다.",
        "- 이 도구는 입력 파일을 수정하지 않습니다. 표시 오류 수정 대상은 추가 대사 파일입니다.",
    ])
    if labeled.get("reason"):
        lines.append(f"- {labeled['reason']}")
    for error in labeled.get("errors", []):
        lines.append(f"- {error['line']}줄: {error['reason']}")
    lines.extend(f"- {error}" for error in labeled.get("speaker_map_errors", []))
    if labeled.get("difference_preview"):
        lines.extend(["", "```diff", *labeled["difference_preview"], "```"])
    summary = report["speaker_summary"]
    lines.extend(["", "## 화자별 대사 정리본 — 앞 20자", "",
                  f"- 판정: {summary['status']}",
                  f"- 작품 제목 일치: {summary['title_status']}",
                  f"- 화자·횟수·순서·앞 20자·말줄임표 일치: {summary.get('format_and_excerpt_status', 'UNVERIFIED')}"])
    if "speaker_count" in summary:
        lines.append(f"- 실제 화자 {summary['speaker_count']}명 / 원문 발화 {summary['utterance_count']}회")
        lines.extend(f"- {name}: {count}회" for name, count in summary["speaker_counts"].items())
    if summary.get("reason"):
        lines.append(f"- {summary['reason']}")
    if summary.get("difference_preview"):
        lines.extend(["", "```diff", *summary["difference_preview"], "```"])
    if report["chapters"].get("chapters"):
        lines.extend(["", "## 챕터별 대사 비중", "", "| 챕터 | 글자 수 | 대사 비중 | 30% 이하 |", "|---|---:|---:|---|"])
        for row in report["chapters"]["chapters"]:
            lines.append(f"| {row['id']} | {row['characters_raw']} | {row['dialogue_ratio_percent']:.4f}% | {row['dialogue_ratio_status']} |")
    elif report["chapters"].get("reason"):
        lines.append(f"- 챕터 측정 근거: {report['chapters']['reason']}")
    for error in m["plain_format"]["speaker_prefix_examples"]:
        lines.append(f"- 기본 대본 {error['line']}줄에 불필요한 화자 표시: {error['text']}")
    for error in m["plain_format"]["dialogue_line_errors"]:
        lines.append(f"- 기본 대본 {error['line']}줄: {error['reason']}")
    for error in m["plain_format"]["heading_examples"]:
        lines.append(f"- 삭제할 독립 제목/구분선 {error['line']}줄: {error['text']}")
    for error in m["narration_rhythm"]["examples"]:
        lines.append(f"- 종결 계열 반복({error['family']}): " + " / ".join(error['sentences']))
    breathing = m["narration_breathing"]
    if breathing["candidates"]:
        lines.extend([
            "", "## 나레이션 호흡 쉼표 검토 후보", "",
            "길거나 쉼표가 없다는 사실만으로 오류가 아닙니다. 다음 문장을 읽고 의미 경계의 쉼표 필요 여부와 과한 끊김을 확인합니다. 실제 낭독 시간은 측정하지 않았습니다.",
        ])
        for row in breathing["candidates"]:
            lines.append(f"- 나레이션 {row['narration_sentence_number']}번째 문장: {row['sentence']} (공백 제외 {row['characters_no_whitespace']}자 / 쉼표 {row['comma_count']}개 / 최장 무쉼표 구간 {row['longest_segment_without_comma']}자)")
        lines.append("- 표시만 수정하는 요청에서는 기본 대본·추가본의 쉼표를 그대로 두고 제안을 보고서에만 기록합니다.")
    if report["intro"].get("characters_raw") is not None:
        lines.extend(
            [
                "",
                "## 인트로",
                "",
                f"- 글자 수: {report['intro']['characters_raw']:,}",
                f"- 한글 음절 수: {report['intro']['hangul_syllables']:,}",
            ]
        )
    beat_rows = report["beats"].get("beats", [])
    if beat_rows:
        lines.extend(
            [
                "",
                "## 15비트 위치",
                "",
                "| 비트 | 이름 | 위치 | 목표 | 판정 |",
                "|---:|---|---:|---:|---|",
            ]
        )
        for row in beat_rows:
            pct = "-" if row["position_percent"] is None else f"{row['position_percent']:.2f}%"
            exp = "-" if row["expected_percent"] is None else f"{row['expected_percent'][0]:g}~{row['expected_percent'][1]:g}%"
            lines.append(f"| {row['id']} | {row['name']} | {pct} | {exp} | {row['verdict']} |")
    question_rows = report["questions"].get("questions", [])
    if question_rows:
        lines.extend(
            [
                "",
                "## 궁금증 앵커",
                "",
                "| ID | 질문 | 단서 수 | 시간순 | 판정 |",
                "|---|---|---:|---|---|",
            ]
        )
        for row in question_rows:
            found_clues = sum(1 for c in row["clues"] if c.get("found"))
            lines.append(
                f"| {row['id']} | {row['question']} | {found_clues} | "
                f"{'PASS' if row['chronological'] else 'FAIL'} | {row['verdict']} |"
            )
    direction = report["direction"]
    if direction.get("scenes"):
        lines.extend(["", "## 60초 통합 인트로", "",
            f"- 씬 수: {direction['scene_count']}개 (고정 제한 없음)",
            f"- 편집 합계: {direction['edit_duration_seconds']:g}초 / {direction['total_exact_60_seconds']}",
            f"- VIDEO 안 생성 초수와 편집 구간: 씬별 검사 참조",
            f"- INTRO SCRIPT ↔ 모든 씬 발화: {direction['scene_speech_matches_intro']}",
            f"- 별도 INTRO SCRIPT ↔ 모든 씬의 발화: {direction['source_coverage']['status']}",
            f"- IMAGE/VIDEO 수와 씬 수 일치: {direction['prompt_count_matches_scenes']}",
            f"- 60초 단일 MASTER_01: {direction['bgm_master']}",
            "- 실제 영상 길이·립싱크·음악 청감: 미검증 (이 코드는 제작 문서만 검사)",
            "- 훅·재후킹·카메라·행동·음악의 의미/연속성: 원문 의미 검토 필요",
            "", "| 씬 | 편집 구간 | 생성 초수 | 사용 구간 | VIDEO 초수 | 실제 발화 | 음악 연결 | 판정 |",
            "|---:|---|---:|---|---|---|---|---|"])
        for row in direction["scenes"]:
            lines.append(f"| {row['scene']} | {row['start']:g}–{row['end']:g} | {row['generate_seconds']:g} | {row['edit_trim'][0]:g}–{row['edit_trim'][1]:g} | {row['duration_in_video']} | {row['video_voice_policy']} | {row['bgm_continuity']} | {row['verdict']} |")
        for error in direction.get("errors", []):
            lines.append(f"- 인트로: {error}")
        for row in direction["scenes"]:
            lines.extend(f"- Scene {row['scene']}: {error}" for error in row['errors'])
        bridge = direction.get("body_opening_bridge", {})
        lines.extend(["", "## 60초 끝과 본편 첫 장면 연결", "",
            f"- 판정: {bridge.get('status', 'UNVERIFIED')}",
            f"- 연결 방식: {bridge.get('transition_mode') or '누락'}",
            f"- 마지막 발화 일치: {bridge.get('last_intro_line_exact', 'UNVERIFIED')}",
            f"- 본편 첫 원문: {bridge.get('text') or '누락'}",
            f"- 실제 본편 시작과 일치: {bridge.get('body_opening_match', 'UNVERIFIED')}",
            f"- 인물 교대 발화: {direction.get('character_dialogue', {}).get('status', 'UNVERIFIED')}",
            "- 대화 자연스러움·행동/소리 연결·도입 재설명 여부: 원문 의미 검토 필요"])
    elif direction.get("reason"):
        lines.append(f"- 인트로 확인: {direction['reason']}")
    if m["recurring_phrase_counts"]:
        lines.extend(["", "## 반복 표현 계수", ""])
        for phrase, count in m["recurring_phrase_counts"].items():
            lines.append(f"- `{phrase}`: {count}회")
    if m["modern_narration_ending_examples"]:
        lines.extend(["", "## 조선시대 야담 구연체 수정 대상", ""])
        for item in m["modern_narration_ending_examples"]:
            lines.append(
                f"- `~{item['ending']}`: {item['sentence']}"
            )
    if report["title_warnings"]:
        lines.extend(["", "## 파일명·제목 확인", ""])
        lines.extend(f"- {item}" for item in report["title_warnings"])
    lines.extend(
        [
            "",
            "> 자동 측정은 의미상 정답 여부, 화자 일치, 인과와 복선의 공정성을 대신하지 않는다. 스킬의 의미 검증을 이어서 수행할 것.",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    if args.dialogue and Path(args.script).resolve() == Path(args.dialogue).resolve():
        raise ValueError("--script와 --dialogue에는 서로 다른 기본 대본·추가 대사 파일을 지정하세요")
    if args.speaker_summary and Path(args.speaker_summary).resolve() in {
        Path(p).resolve() for p in (args.script, args.dialogue) if p
    }:
        raise ValueError("화자별 정리본에는 기본 대본·전체 대사 파일과 다른 파일을 지정하세요")
    script = read_text(args.script)
    audit_map = load_audit_map(args.audit_map)
    speaker_map = load_audit_map(args.speaker_map) if args.speaker_map else (audit_map or {}).get("speaker_map")
    if speaker_map is not None and not isinstance(speaker_map, dict):
        raise ValueError("speaker_map은 JSON 객체여야 합니다")
    labeled = read_text(args.dialogue) if args.dialogue else None
    summary = read_text(args.speaker_summary) if args.speaker_summary else None
    story_title = args.story_title or (audit_map or {}).get("story_title")
    marker = args.body_marker or (audit_map or {}).get("body_marker")
    direction = read_text(args.direction) if args.direction else None
    intro_reference = extract_intro_script_block(direction) if direction else None
    intro, body, boundary = split_intro_body(script, args.intro, marker, intro_reference, args.script_layout)
    question_source = script
    if intro is not None and not compact(script).startswith(compact(intro)):
        question_source = intro + "\n\n" + script
    report = {
        "script_path": str(Path(args.script).resolve()),
        "intro_path": str(Path(args.intro).resolve()) if args.intro else None,
        "direction_path": str(Path(args.direction).resolve()) if args.direction else None,
        "dialogue_path": str(Path(args.dialogue).resolve()) if args.dialogue else None,
        "boundary": boundary,
        "script_metrics": text_metrics(script),
        "body_metrics": text_metrics(body),
        "dialogue_file": dialogue_file_metrics(script, labeled, speaker_map),
        "speaker_summary": speaker_summary_metrics(script, summary, speaker_map, story_title),
        "speaker_summary_path": str(Path(args.speaker_summary).resolve()) if args.speaker_summary else None,
        "chapters": audit_chapters(body, audit_map),
        "fixed_layout": audit_fixed_layout(body, intro, audit_map),
        "educational_value": audit_educational_value(body, audit_map),
        "story_quality": audit_story_quality(body, audit_map),
        "intensity": audit_intensity(body, audit_map, getattr(args, "intensity", None)),
        "policy_review": {"community_removal": "UNVERIFIED", "age_restriction": "UNVERIFIED",
                          "ad_suitability": "UNVERIFIED", "channel_originality": "UNVERIFIED",
                          "channel_internal_standard": "UNVERIFIED",
                          "reason": "원문·공식 기준·실제 제공 매체를 읽는 별도 의미 검토 필요; 자동 정책 승인 금지"},
        "intro_separation_status": (
            "FAIL" if boundary.get("intro_duplicate_prefix") else
            "PASS" if intro is not None and args.script_layout == "body_only" else
            "NOT_APPLICABLE" if args.script_layout == "legacy_merged" else "UNVERIFIED"
        ),
        "length": combined_length_metrics(body, intro, args.target),
        "intro": intro_metrics(intro),
        "beats": audit_beats(body, audit_map),
        "questions": audit_questions(question_source, audit_map),
        "direction": direction_metrics(direction, intro, args.style, body),
        "title_warnings": title_warnings(args.script),
    }
    # The old merged 30% gate must not reject freely dialogued intros.
    if args.script_layout == "legacy_merged":
        report["script_metrics"]["dialogue_ratio_status"] = "REFERENCE_ONLY"
        report["script_metrics"]["dialogue_ratio_rule"] = "구형 합본 참고 수치; 본편/본편 챕터만 최대 30%"
    if args.script_layout == "legacy_merged" and (intro is None or boundary.get("method") == "intro_prefix_mismatch"):
        report["body_metrics"]["dialogue_ratio_status"] = "UNVERIFIED"
        report["body_metrics"]["dialogue_ratio_rule"] = "본편 경계 미확인; 인트로 포함 수치에 상한 강제 금지"
        report["chapters"] = {"status": "UNVERIFIED", "chapters": [], "reason": "본편 시작 경계 미확인"}
        report["fixed_layout"] = {"status": "UNVERIFIED", "chapters": [], "reason": "본편·인트로 경계 미확인; 20등분해 통과시키지 않음"}
    report["overall_status"] = overall_status(report)
    return report


def self_test() -> int:
    sample = (
        '"문을 열지 마시오."\n\n젖은 짐승 발자국이 방 안으로 이어졌습니다.\n\n'
        "옛날 옛적, 한 장수가 살았습니다.\n\n"
        '"보이는 것이 전부는 아니란다."\n\n그는 문을 열었습니다.'
    )
    intro, body, boundary = split_intro_body(sample, None, None, script_layout="legacy_merged")
    assert intro is not None and body.startswith("옛날 옛적")
    assert boundary["method"] == "auto_marker"
    metrics = text_metrics(sample)
    assert metrics["quote_balance"]["status"] == "PASS"
    assert metrics["modern_narration_ending_status"] == "PASS"
    assert intro_metrics(intro)["status"] == "PASS"
    assert compact("@두만 안녕하시오") == compact("안녕하시오")

    modern_ending_sample = (
        "그는 문을 열었는데요. 그 까닭은 따로 있었거든요. "
        "마을 사람들도 이미 수상하잖아요. 눈 위에 발자국이 보이네요. "
        "뒤쪽 문은 굳게 닫혔죠. 밤새 눈이 내렸으니까요. "
        "그는 마침내 진실을 말해요. 그리고 홀로 행동했다. "
        '"하지만 저는 문을 열었는데요?"'
    )
    modern_metrics = text_metrics(modern_ending_sample)
    assert modern_metrics["modern_narration_ending_status"] == "FAIL"
    assert modern_metrics["modern_narration_ending_count"] == 8
    expanded_modern_ending_sample = (
        "그는 이미 문을 열었어요. 그 여인은 주모예요. "
        "안에는 아무도 없어요. 아마 다시 오겠어요. "
        "그 흔적은 이상하더라고요. 이것이 단서인가요? "
        "곧 돌아올게요. 진실을 밝히려고요. 그랬다고요. "
        "사실은 달랐어. 모두 속은 거야. 이제 알겠지. "
        "참으로 놀랍네. "
        '"제가 곧 돌아올게요."'
    )
    expanded_metrics = text_metrics(expanded_modern_ending_sample)
    assert expanded_metrics["modern_narration_ending_status"] == "FAIL"
    assert expanded_metrics["modern_narration_ending_count"] == 13
    approved_ending_sample = (
        "그는 문을 열었습니다. 범인은 이웃이었답니다. "
        "그날 밤 진실을 밝혔지요. 방 안에는 등잔이 있었지요. "
        "숨은 자국이 선명하더군요. 모든 일은 함정이었던 것이었지요. "
        "그 발자국은 대체 누구의 것이었을까요?"
    )
    assert (
        text_metrics(approved_ending_sample)["modern_narration_ending_status"]
        == "PASS"
    )

    # Positive audit-map fixture with all key beats inside their hard ranges.
    chars = list("가" * 20000)

    def place(offset: int, token: str) -> None:
        chars[offset : offset + len(token)] = list(token)

    beat_offsets = (0, 400, 1000, 2000, 3000, 4000, 5000, 7000, 10000, 12000, 14000, 15200, 16000, 17000, 19400)
    beat_items = []
    for number, offset in enumerate(beat_offsets, start=1):
        token = f"<B{number:02d}>"
        place(offset, token)
        beat_items.append({"id": number, "name": f"비트{number}", "anchor": token})
    for offset, token in ((1200, "<QOPEN>"), (2500, "<CLUE1>"), (5500, "<CLUE2>"), (18000, "<QANSWER>")):
        place(offset, token)
    audit_body = "".join(chars)
    audit_map = {
        "primary_structure": "STR08",
        "beats": beat_items,
        "questions": [
            {
                "id": "Q1",
                "question": "누구인가",
                "open_anchor": "<QOPEN>",
                "clue_anchors": ["<CLUE1>", "<CLUE2>"],
                "answer_anchor": "<QANSWER>",
            }
        ],
    }
    assert audit_beats(audit_body, audit_map)["status"] == "PASS"
    assert audit_questions(audit_body, audit_map)["status"] == "PASS"

    # Legacy fixed-five-scene intro fixtures removed; body checks below are unchanged.
    # V11.13: separate the plain story from the EXTRA labeled copy.
    source = '"문을 열지 마시오."\n\n성복은 문 앞에 섰지요.\n\n"밖에 누구 계시오?"\n\n연화는 빗장을 잡았답니다.\n\n"나 성복이오. 문을 열어 주시오."\n'
    labeled = '성복: "문을 열지 마시오."\n\n나레이션: 성복은 문 앞에 섰지요.\n\n연화: "밖에 누구 계시오?"\n\n나레이션: 연화는 빗장을 잡았답니다.\n\n성복: "나 성복이오. 문을 열어 주시오."\n'
    speakers = {"dialogue_speakers": ["성복", "연화", "성복"], "allowed_speakers": ["성복", "연화"], "first_body_speaker": "성복"}
    pair = dialogue_file_metrics(source, labeled, speakers)
    assert pair["status"] == "PASS" and pair["narration_segments"] == 2
    assert plain_script_format(source)["status"] == "PASS"
    assert plain_script_format(labeled)["status"] == "FAIL"
    assert dialogue_file_metrics(source.replace("\n", "\r\n"), labeled, speakers)["status"] == "PASS"
    assert dialogue_file_metrics(source, None, speakers)["status"] == "UNVERIFIED"
    assert dialogue_file_metrics(source, labeled)["status"] == "UNVERIFIED"
    for bad in (
        labeled.replace("나레이션: ", "", 1),
        labeled.replace("나레이션: ", "나래이션: ", 1),
        labeled.replace("성복은 문 앞에 섰지요.", "성복은 문을 열었지요."),
        labeled.replace("나레이션: 연화는 빗장을 잡았답니다.\n\n", ""),
        labeled + '나레이션: 연화는 빗장을 잡았답니다.\n',
        labeled.replace('성복: "문을 열지 마시오."', '나레이션: "문을 열지 마시오."'),
        labeled.replace('성복: "문을 열지 마시오."', '@seongbok: "문을 열지 마시오."'),
        labeled.rstrip("\n"),
    ):
        assert dialogue_file_metrics(source, bad, speakers)["status"] == "FAIL"
    wrong_speaker = dialogue_file_metrics(source, labeled.replace('연화: "밖에', '성복: "밖에'), speakers)
    assert wrong_speaker["content_match_status"] == "PASS" and wrong_speaker["speaker_map_match_status"] == "FAIL"
    assert dialogue_file_metrics(source, labeled, {**speakers, "dialogue_speakers": ["성복"]})["status"] == "FAIL"
    assert dialogue_file_metrics(source, labeled, {**speakers, "first_body_speaker": "연화"})["status"] == "FAIL"
    assert plain_script_format('그가 말했다. "안녕하시오."')["status"] == "FAIL"
    assert plain_script_format('“안녕하시오.”')["status"] == "FAIL"
    threshold = '"' + '가' * 30 + '"\n' + '나' * 70
    assert text_metrics(threshold)["dialogue_ratio_status"] == "PASS"
    assert text_metrics('"' + '가' * 29999 + '"\n' + '나' * 70001)["dialogue_ratio_status"] == "PASS"
    assert text_metrics('"' + '가' * 70 + '"\n' + '나' * 30)["dialogue_ratio_status"] == "FAIL"
    assert narration_rhythm("그는 이장이었습니다. 문을 닫았습니다. 안에는 없었습니다.")["status"] == "FAIL"
    assert narration_rhythm("그는 갔지요. 문이 있었지요. 함정인 것이었지요.")["status"] == "FAIL"
    assert narration_rhythm('그는 이장이었습니다.\n"문을 열었어요."\n비밀을 지켰답니다. 문이 닫혔지요.')["status"] == "PASS"
    assert audit_beats("원문", {"primary_structure": "STR01", "support_structure": "STR08"})["status"] == "NOT_APPLICABLE"
    assert audit_beats("원문", {})["status"] == "UNVERIFIED"
    chapters = '첫째' + '나' * 300 + '\n"' + '가' * 100 + '"\n둘째' + '나' * 300 + '\n"' + '가' * 100 + '"\n'
    chapter_map = {"chapters": [{"id": 1, "start_anchor": "첫째"}, {"id": 2, "start_anchor": "둘째"}]}
    assert audit_chapters(chapters, chapter_map)["status"] == "PASS"
    assert audit_chapters("빠진 앞부분" + chapters, chapter_map)["status"] == "FAIL"
    assert audit_chapters(chapters, None)["status"] == "UNVERIFIED"
    assert audit_chapters(chapters + '"' + '가' * 1000 + '"', chapter_map)["status"] == "FAIL"
    # V11.14: punctuation is reviewed by meaning, never auto-inserted.
    breath_source = '"인물 대사의 쉼표, 는 검토 후보에서 빼시오."\n긴 복도를 지나 문 앞에 다다랐을 때 안에서 발소리가 들려왔지요.'
    breath = text_metrics(breath_source)["narration_breathing"]
    assert breath["narration_sentence_count"] == 1 and breath["candidates"][0]["comma_count"] == 0
    assert breath["status"] == "MANUAL_REVIEW_REQUIRED" and breath["timing_measured"] is False
    edited_breath = text_metrics(breath_source.replace('때 안에서', '때, 안에서'))["narration_breathing"]
    assert edited_breath["candidates"][0]["comma_count"] == 1
    assert edited_breath["candidates"][0]["longest_segment_without_comma"] < breath["candidates"][0]["longest_segment_without_comma"]
    assert text_metrics('"인물 대사만 있는 예시요."')["narration_breathing"]["status"] == "NOT_APPLICABLE"
    assert text_metrics('문이 열렸지요.')["narration_breathing"]["status"] != "FAIL"
    assert dialogue_file_metrics(source, labeled.replace('문 앞에 섰지요.', '문 앞에, 섰지요.'), speakers)["content_match_status"] == "FAIL"
    # V11.15: chapter headings belong in internal plans, not spoken scripts.
    for heading in ('## 제6장|빈집의 여인', '## 제6장 | 빈집의 여인', '제6장', '[제6장]', '6장: 빈집의 여인', 'Chapter 6', '제 6 장 — 빈집의 여인', '**제육장 | 빈집의 여인**', '---'):
        assert is_script_heading(heading), heading
        assert plain_script_format(source + heading)["heading_status"] == "FAIL"
        with_heading = dialogue_file_metrics(source + heading, labeled + '나레이션: ' + heading, speakers)
        assert with_heading['content_match_status'] == 'PASS' and with_heading['status'] == 'FAIL'
    for story_line in ('"제6장에는 무엇이 적혀 있소?"', '제6장에는 빈집의 주인에 관한 글이 적혀 있었지요.', '그 여인이 빈집의 주인이었답니다.', '제6장 뒤에는 그림이 붙어 있었지요.'):
        assert not is_script_heading(story_line), story_line
    # V11.16: maximum 30%, including the boundary before display rounding.
    assert text_metrics('"' + '가' * 30001 + '"\n' + '나' * 69999)["dialogue_ratio_status"] == "FAIL"
    assert text_metrics('"' + '가' * 3000001 + '"\n' + '나' * 6999999)["dialogue_ratio_status"] == "FAIL"
    assert text_metrics('나레이션만 있는 문장이지요.')["dialogue_ratio_status"] == "PASS"
    assert text_metrics('')["dialogue_ratio_status"] == "FAIL"
    # Golden lookup sheet: short, exactly 20, longer, spaces, repeated speeches.
    phrases = ['문을 여시오.', '가' * 20, '가' * 19 + ' 나', '문을 여시오.']
    lookup_source = '\n나레이션이 이어졌지요.\n'.join(f'"{p}"' for p in phrases)
    lookup_map = {"dialogue_speakers": ["성복", "연화", "성복", "성복"], "allowed_speakers": ["성복", "연화"], "first_body_speaker": "성복"}
    golden = ('검증 작품 — 화자별 대사 정리 (앞 20자)\n\n총 화자 수: 2명\n\n'
              '=== 성복 (3회) ===\n"문을 여시오."\n"' + '가' * 19 + ' ..."\n"문을 여시오."\n\n'
              '=== 연화 (1회) ===\n"' + '가' * 20 + '"\n')
    assert build_speaker_summary(lookup_source, '검증 작품', **lookup_map) == golden
    lookup = speaker_summary_metrics(lookup_source, golden, lookup_map, '검증 작품')
    assert lookup['status'] == 'PASS' and lookup['utterance_count'] == 4
    assert lookup['speaker_counts'] == {'성복': 3, '연화': 1}
    assert speaker_summary_metrics(lookup_source, golden.replace('\n', '\r\n'), lookup_map, '검증 작품')['status'] == 'PASS'
    for bad in (
        golden.replace('총 화자 수: 2명', '총 화자 수: 3명'),
        golden.replace('성복 (3회)', '성복 (2회)'),
        golden.replace(' ...', '...'),  # space was the twentieth character
        golden.replace('가' * 20 + '"', '가' * 20 + '..."'),
        golden.replace('"문을 여시오."\n', '', 1),
        golden.replace('=== 연화', '=== 나레이션'),
        golden.replace('검증 작품 —', '다른 작품 —'),
        golden.replace('"문을 여시오."', '"문을 닫으시오."', 1),
    ):
        assert speaker_summary_metrics(lookup_source, bad, lookup_map, '검증 작품')['status'] == 'FAIL'
    assert speaker_summary_metrics(lookup_source, None, lookup_map, '검증 작품')['status'] == 'UNVERIFIED'
    assert speaker_summary_metrics(lookup_source, golden, None, '검증 작품')['status'] == 'UNVERIFIED'
    assert speaker_summary_metrics(lookup_source, golden, lookup_map)['status'] == 'UNVERIFIED'
    bad_map = {**lookup_map, 'dialogue_speakers': ['성복', '성복', '연화', '성복']}
    assert speaker_summary_metrics(lookup_source, golden, bad_map, '검증 작품')['status'] == 'FAIL'
    bad_count = {**lookup_map, 'dialogue_speakers': ['성복']}
    assert speaker_summary_metrics(lookup_source, golden, bad_count, '검증 작품')['status'] == 'FAIL'
    # V11.17: evidence existence must not masquerade as educational quality.
    education_body = '그는 소문 대신 당사자를 찾아갔지요.\n오해가 풀리고 거래를 되돌렸답니다.\n이후에는 서명을 받기 전에 내용을 살폈지요.'
    evidence = {'learning_focus': '전언을 그대로 믿기 전에 사실을 확인한다',
                'choice_anchor': '그는 소문 대신 당사자를 찾아갔지요.',
                'consequence_anchor': '오해가 풀리고 거래를 되돌렸답니다.',
                'payoff_anchor': '이후에는 서명을 받기 전에 내용을 살폈지요.'}
    checked = audit_educational_value(education_body, {'educational_value': evidence})
    assert checked['evidence_status'] == 'PASS' and checked['status'] == 'UNVERIFIED'
    assert checked['semantic_status'] == 'UNVERIFIED' and len(checked['anchors']) == 3
    assert audit_educational_value(education_body, {})['status'] == 'UNVERIFIED'
    for bad in ({**evidence, 'choice_anchor': '원문에 없는 문장'},
                {**evidence, 'choice_anchor': evidence['consequence_anchor']},
                {**evidence, 'payoff_anchor': 1}):
        assert audit_educational_value(education_body, {'educational_value': bad})['status'] == 'FAIL'
    assert audit_educational_value(education_body, {'educational_value': {**evidence, 'learning_focus': ''}})['status'] == 'UNVERIFIED'
    nonlinear = '\n'.join(reversed(education_body.splitlines()))
    assert audit_educational_value(nonlinear, {'educational_value': evidence})['evidence_status'] == 'PASS'
    print("BODY SELF-TEST PASS — 본편 검사만; 60초 인트로 검증 코드의 테스트를 의미하지 않음")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", help="표시 없는 본편 전용 대본 TXT/MD; 인트로는 별도")
    parser.add_argument("--dialogue", help="추가 대사 TXT; 나레이션: / 실제인물명: 표시본")
    parser.add_argument("--speaker-summary", help="화자별 대사 정리 TXT; 각 발화 원문 앞 20자")
    parser.add_argument("--story-title", help="확정된 실제 작품 제목; 생략 시 감사 지도 story_title")
    parser.add_argument("--speaker-map", help="원문에서 독립 작성한 내부 화자 지도 JSON; 사용자에게 요구하지 않음")
    parser.add_argument("--intro", help="별도 인트로 TXT/MD")
    parser.add_argument("--direction", help="60초 가변 씬 통합 인트로 TXT/MD; 실제 생성 초수·대화·MASTER_01 포함")
    parser.add_argument("--target", type=int, choices=(35000,), default=35000, help="고정 5막 20챕터: 별도 인트로 발화+본편 합계 최소 35000자")
    parser.add_argument("--script-layout", choices=("body_only", "legacy_merged"), default="body_only", help="기본 본편 전용; 구형 합본만 legacy_merged 명시")
    parser.add_argument("--body-marker", help="합본 안 본편이 시작되는 정확한 문자열")
    parser.add_argument("--audit-map", help="선택 구조·챕터·궁금증·화자 지도 JSON; STR08 주 구조일 때만 15비트 검사")
    parser.add_argument("--intensity", choices=("existing", "strong"), help="현재 요청의 강도 선택; 생략 시 감사 지도, 없으면 미확정")
    parser.add_argument("--style", choices=("2d", "other"))
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--self-test", action="store_true", help="기존 본편 측정 함수 자체 점검만; 인트로/실제 영상 점검 아님")
    args = parser.parse_args(argv)
    if not args.self_test and not args.script:
        parser.error("--script is required unless --self-test is used")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.self_test:
        return self_test()
    try:
        report = build_report(args)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_markdown(report))
    return 1 if report["overall_status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
