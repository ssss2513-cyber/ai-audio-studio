import argparse
import json
import os
import sys
from typing import Dict
from core.parser import ScriptParser, ScriptSegment
from core.tts_engine import TTSEngine, VoiceConfig, KOREAN_VOICES
from core.audio_processor import AudioProcessor
from core.subtitle import SubtitleGenerator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 기본 자동 매핑 후보군 (화자별 다른 목소리 자동 배정)
VOICE_ROTATION = [
    "ko-KR-SunHiNeural",              # 1. 나레이션/여성 주연
    "ko-KR-InJoonNeural",             # 2. 남성 주연
    "en-US-AvaMultilingualNeural",    # 3. 여성 조연/발랄
    "ko-KR-HyunsuMultilingualNeural", # 4. 청년/소년
    "en-US-EmmaMultilingualNeural",   # 5. 감성 여성
    "en-US-AndrewMultilingualNeural", # 6. 친근한 남성
    "en-US-BrianMultilingualNeural"   # 7. 중년/중후한 남성
]

def auto_assign_voices(speakers: list[str]) -> Dict[str, VoiceConfig]:
    """화자 목록을 받아 중복되지 않도록 기본 음성을 자동 매핑"""
    mapping: Dict[str, VoiceConfig] = {}
    char_idx = 0
    for spk in speakers:
        if "나레이션" in spk or "해설" in spk:
            mapping[spk] = VoiceConfig(voice="ko-KR-SunHiNeural", rate="+0%", pitch="+0Hz")
        else:
            voice = VOICE_ROTATION[(char_idx + 1) % len(VOICE_ROTATION)]
            char_idx += 1
            mapping[spk] = VoiceConfig(voice=voice, rate="+0%", pitch="+0Hz")
    return mapping

def run_tts_pipeline(
    script_text: str,
    output_dir: str = "outputs",
    voice_map: Dict[str, VoiceConfig] = None,
    pause_ms: int = 500,
    remove_stage_dirs: bool = False,
    progress_callback = None
):
    os.makedirs(output_dir, exist_ok=True)
    segments_dir = os.path.join(output_dir, "segments")
    os.makedirs(segments_dir, exist_ok=True)

    # 1. 대본 파싱
    parser = ScriptParser()
    segments = parser.parse(script_text, remove_stage_directions=remove_stage_dirs)
    if not segments:
        raise ValueError("파싱된 대사가 없습니다. 대본을 확인해 주세요.")

    speakers = parser.extract_speakers(segments)
    if not voice_map:
        voice_map = auto_assign_voices(speakers)

    print(f"[정보] 감지된 화자 ({len(speakers)}명): {', '.join(speakers)}")
    for spk in speakers:
        cfg = voice_map.get(spk, VoiceConfig())
        v_info = KOREAN_VOICES.get(cfg.voice, {})
        v_display = v_info.get('name', cfg.voice)
        print(f"  - {spk} -> {v_display} (속도: {cfg.rate}, 피치: {cfg.pitch})")

    # 2. 개별 음성 세그먼트 생성
    total = len(segments)
    audio_info_list = []

    print(f"\n[진행] 총 {total}개 대사 TTS 생성 시작...")
    for idx, seg in enumerate(segments, 1):
        safe_spk = "".join(c for c in seg.speaker if c.isalnum() or c in (' ', '_', '-')).strip()
        filename = f"{seg.index:03d}_{safe_spk}.mp3"
        seg_file_path = os.path.join(segments_dir, filename)

        cfg = voice_map.get(seg.speaker, VoiceConfig())
        TTSEngine.generate_speech(seg.text, seg_file_path, cfg)

        audio_info_list.append({
            'index': seg.index,
            'speaker': seg.speaker,
            'text': seg.text,
            'file_path': seg_file_path
        })

        if progress_callback:
            progress_callback(idx, total, f"({idx}/{total}) [{seg.speaker}] {seg.text[:20]}...")
        else:
            print(f"  [{idx}/{total}] [{seg.speaker}] {seg.text[:30]} -> {filename}")

    # 3. 오디오 병합 (무음 삽입)
    print("\n[진행] 전체 오디오 트랙 병합 중...")
    audio_processor = AudioProcessor(pause_ms=pause_ms)
    full_audio_path = os.path.join(output_dir, "full_audio.mp3")
    merged_path, timings = audio_processor.merge_segments(
        audio_info_list,
        full_audio_path,
        pause_ms=pause_ms
    )

    # 4. SRT 및 VTT 자막 생성
    print("[진행] 자막 파일 생성 중...")
    srt_path = os.path.join(output_dir, "subtitles.srt")
    vtt_path = os.path.join(output_dir, "subtitles.vtt")
    SubtitleGenerator.generate_srt(timings, srt_path, include_speaker=True)
    SubtitleGenerator.generate_vtt(timings, vtt_path, include_speaker=True)

    print("\n[완료] 모든 작업이 성공적으로 완료되었습니다!")
    print(f"  - 개별 음성 폴더: {segments_dir}")
    print(f"  - 전체 병합 음성: {merged_path}")
    print(f"  - 자막 파일 (SRT): {srt_path}")
    print(f"  - 자막 파일 (VTT): {vtt_path}")

    return {
        "output_dir": output_dir,
        "segments_dir": segments_dir,
        "full_audio": merged_path,
        "srt": srt_path,
        "vtt": vtt_path,
        "timings": timings,
        "segments": segments
    }

def main():
    arg_parser = argparse.ArgumentParser(description="대본 기반 화자별 자동 TTS 생성기")
    arg_parser.add_argument("--script", "-s", type=str, help="대본 텍스트 파일 경로", required=True)
    arg_parser.add_argument("--output", "-o", type=str, default="outputs", help="출력 폴더 경로 (기본: outputs)")
    arg_parser.add_argument("--pause", "-p", type=int, default=500, help="대사 간 무음 간격(ms) (기본: 500)")
    arg_parser.add_argument("--clean-stage", action="store_true", help="지문(괄호 속 지시문) 제거 여부")
    args = arg_parser.parse_args()

    if not os.path.exists(args.script):
        print(f"[오류] 대본 파일을 찾을 수 없습니다: {args.script}")
        sys.exit(1)

    with open(args.script, "r", encoding="utf-8") as f:
        script_text = f.read()

    run_tts_pipeline(
        script_text=script_text,
        output_dir=args.output,
        pause_ms=args.pause,
        remove_stage_dirs=args.clean_stage
    )

if __name__ == "__main__":
    main()
