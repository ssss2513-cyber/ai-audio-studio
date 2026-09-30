import os
import subprocess
from dataclasses import dataclass
from typing import List, Tuple
try:
    from pydub import AudioSegment
except Exception:
    AudioSegment = None
import mutagen.mp3

@dataclass
class AudioTiming:
    segment_index: int
    speaker: str
    text: str
    file_path: str
    start_ms: int
    end_ms: int
    duration_ms: int

class AudioProcessor:
    """
    개별 오디오 세그먼트를 무음(Silence)과 함께 초고속 병합하고,
    정확한 타임스탬프 정보를 산출하는 오디오 프로세서.
    """

    def __init__(self, pause_ms: int = 500):
        self.pause_ms = pause_ms

    @staticmethod
    def get_audio_duration_ms(file_path: str) -> int:
        try:
            audio = mutagen.mp3.MP3(file_path)
            return int(audio.info.length * 1000)
        except Exception:
            if AudioSegment is not None:
                try:
                    audio = AudioSegment.from_file(file_path)
                    return len(audio)
                except Exception:
                    pass
            return 0

    def merge_segments(
        self,
        segment_info_list: List[dict],
        output_file: str,
        pause_ms: int = 500
    ) -> Tuple[str, List[AudioTiming]]:
        """
        segment_info_list: [{'index': 1, 'speaker': '나레이션', 'text': '...', 'file_path': '...'}]
        output_file: 최종 결합된 mp3 파일 경로
        pause_ms: 대사 사이의 무음 길이 (밀리초, 기본 500ms)
        """
        if not segment_info_list:
            raise ValueError("병합할 오디오 세그먼트가 없습니다.")

        out_dir = os.path.dirname(os.path.abspath(output_file))
        os.makedirs(out_dir, exist_ok=True)

        timings: List[AudioTiming] = []
        valid_segments: List[dict] = []
        current_ms = 0

        # 1. 고속 타임스탬프 계산 (mutagen 기반)
        for i, seg in enumerate(segment_info_list):
            audio_path = seg['file_path']
            if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 100:
                raise ValueError(f"대사 {seg.get('index', i + 1)}번의 저장된 음성 파일을 찾을 수 없습니다.")

            duration_ms = self.get_audio_duration_ms(audio_path)
            if duration_ms <= 0:
                raise ValueError(f"대사 {seg.get('index', i + 1)}번의 음성 길이를 읽을 수 없습니다.")
            start_ms = current_ms
            end_ms = start_ms + duration_ms

            timings.append(AudioTiming(
                segment_index=seg.get('index', i + 1),
                speaker=seg.get('speaker', ''),
                text=seg.get('text', ''),
                file_path=audio_path,
                start_ms=start_ms,
                end_ms=end_ms,
                duration_ms=duration_ms
            ))

            valid_segments.append(seg)
            current_ms += duration_ms + pause_ms

        if not valid_segments:
            raise ValueError("병합할 수 있는 유효한 오디오 파일이 없습니다.")

        # 2. FFmpeg streams the merge; never decode the entire story into RAM.
        silence_file = os.path.join(out_dir, "temp_silence.mp3")
        if AudioSegment is not None:
            AudioSegment.silent(duration=pause_ms).export(silence_file, format="mp3", bitrate="192k")
        else:
            # ffmpeg command to create silent mp3
            cmd_silence = [
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "anullsrc=r=44100:cl=mono", "-t", str(max(pause_ms / 1000.0, 0.1)),
                "-c:a", "libmp3lame", "-b:a", "192k", silence_file
            ]
            subprocess.run(cmd_silence, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=30, check=True)

        concat_list_path = os.path.join(out_dir, "concat_list.txt")
        try:
            with open(concat_list_path, "w", encoding="utf-8") as f:
                for idx, seg in enumerate(valid_segments):
                    # Windows 역슬래시를 슬래시로 변경하여 ffmpeg 호환성 유지
                    safe_path = os.path.abspath(seg['file_path']).replace('\\', '/').replace("'", "'\\''")
                    f.write(f"file '{safe_path}'\n")
                    if idx < len(valid_segments) - 1 and pause_ms > 0:
                        safe_silence = silence_file.replace('\\', '/').replace("'", "'\\''")
                        f.write(f"file '{safe_silence}'\n")

            # ffmpeg concat 실행
            cmd = [
                "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_list_path,
                "-c:a", "libmp3lame", "-b:a", "192k", "-threads", "2",
                output_file
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
            if res.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
                return output_file, timings
            detail = res.stderr.decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError("전체 오디오 병합에 실패했습니다. 개별 음성은 보관됩니다. " + detail)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("오디오 병합 시간이 초과됐습니다. 개별 음성은 보관되며 이어서 생성하면 병합을 다시 진행합니다.") from exc
        finally:
            if os.path.exists(concat_list_path):
                try:
                    os.remove(concat_list_path)
                except OSError:
                    pass

        # An in-memory pydub fallback for 30–40 minute stories can exhaust the
        # shared site's RAM and disconnect every visitor. Keep the saved clips
        # and report the FFmpeg error instead of risking a process-wide crash.
