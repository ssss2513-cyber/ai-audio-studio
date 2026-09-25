import os
from typing import List
from core.audio_processor import AudioTiming

class SubtitleGenerator:
    """
    오디오 타이밍 정보를 바탕으로 SRT 및 VTT 자막 파일을 생성하는 생성기.
    """

    @staticmethod
    def _ms_to_srt_time(ms: int) -> str:
        hours = ms // 3600000
        ms %= 3600000
        minutes = ms // 60000
        ms %= 60000
        seconds = ms // 1000
        millis = ms % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    @staticmethod
    def _ms_to_vtt_time(ms: int) -> str:
        hours = ms // 3600000
        ms %= 3600000
        minutes = ms // 60000
        ms %= 60000
        seconds = ms // 1000
        millis = ms % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"

    @classmethod
    def generate_srt(
        cls,
        timings: List[AudioTiming],
        output_file: str,
        include_speaker: bool = True
    ) -> str:
        lines = []
        for i, t in enumerate(timings, 1):
            start = cls._ms_to_srt_time(t.start_ms)
            end = cls._ms_to_srt_time(t.end_ms)
            speaker_prefix = f"[{t.speaker}] " if include_speaker and t.speaker else ""
            content = f"{speaker_prefix}{t.text}"

            lines.append(f"{i}")
            lines.append(f"{start} --> {end}")
            lines.append(content)
            lines.append("")

        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return output_file

    @classmethod
    def generate_vtt(
        cls,
        timings: List[AudioTiming],
        output_file: str,
        include_speaker: bool = True
    ) -> str:
        lines = ["WEBVTT", ""]
        for i, t in enumerate(timings, 1):
            start = cls._ms_to_vtt_time(t.start_ms)
            end = cls._ms_to_vtt_time(t.end_ms)
            speaker_prefix = f"[{t.speaker}] " if include_speaker and t.speaker else ""
            content = f"{speaker_prefix}{t.text}"

            lines.append(f"{i}")
            lines.append(f"{start} --> {end}")
            lines.append(content)
            lines.append("")

        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return output_file
