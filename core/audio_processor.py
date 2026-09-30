import os
import subprocess
import tempfile
import wave
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
        if str(file_path).lower().endswith(".wav"):
            try:
                with wave.open(str(file_path), "rb") as audio:
                    return round(audio.getnframes() * 1000 / audio.getframerate())
            except (OSError, ValueError, wave.Error, EOFError, ZeroDivisionError):
                return 0
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

        # Match silence and clip sample rates/channels. The concat demuxer
        # requires compatible streams; mismatches can distort timing or pitch.
        # Normalize mixed-engine clips to lossless PCM on disk, never in RAM.
        profiles = []
        for seg in valid_segments:
            if str(seg["file_path"]).lower().endswith(".wav"):
                with wave.open(str(seg["file_path"]), "rb") as audio:
                    profiles.append(("wav", audio.getframerate(), audio.getnchannels(), audio.getsampwidth()))
            else:
                info = mutagen.mp3.MP3(seg["file_path"]).info
                profiles.append(("mp3", info.sample_rate, info.channels, 0))
        sample_rate = max(profile[1] for profile in profiles)
        channels = max(profile[2] for profile in profiles)

        def ffmpeg(arguments, timeout):
            result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y"] + arguments,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
            if result.returncode:
                detail = result.stderr.decode("utf-8", errors="replace")[-1000:]
                raise RuntimeError("전체 오디오 병합에 실패했습니다. 개별 음성은 보관됩니다. " + detail)

        try:
            with tempfile.TemporaryDirectory(prefix=".audio_merge_", dir=out_dir) as temporary:
                paths = [seg["file_path"] for seg in valid_segments]
                mixed_profiles = len(set(profiles)) > 1 or (profiles[0][0] == "wav" and profiles[0][3] != 2)
                use_pcm = mixed_profiles or profiles[0][0] == "wav"
                if mixed_profiles:
                    normalized = []
                    for index, source in enumerate(paths):
                        if profiles[index] == ("wav", sample_rate, channels, 2):
                            normalized.append(source)
                            continue
                        target = os.path.join(temporary, f"{index:06d}.wav")
                        ffmpeg(["-i", source, "-map", "0:a:0", "-vn", "-ar", str(sample_rate),
                                "-ac", str(channels), "-c:a", "pcm_s16le", "-threads", "2", target], 120)
                        normalized.append(target)
                    paths = normalized

                silence_file = None
                if pause_ms > 0 and len(paths) > 1:
                    silence_file = os.path.join(temporary, "silence.wav" if use_pcm else "silence.mp3")
                    codec_args = ["-c:a", "pcm_s16le"] if use_pcm else ["-c:a", "libmp3lame", "-b:a", "192k"]
                    ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={'mono' if channels == 1 else 'stereo'}",
                            "-t", str(pause_ms / 1000.0), "-ar", str(sample_rate), "-ac", str(channels)]
                           + codec_args + [silence_file], 30)

                concat_list_path = os.path.join(temporary, "concat.txt")
                with open(concat_list_path, "w", encoding="utf-8") as handle:
                    for index, source in enumerate(paths):
                        entries = [source]
                        if silence_file and index < len(paths) - 1:
                            entries.append(silence_file)
                        for entry in entries:
                            safe_path = os.path.abspath(entry).replace('\\', '/').replace("'", "'\\''")
                            handle.write(f"file '{safe_path}'\n")
                merged = os.path.join(temporary, "merged.mp3")
                ffmpeg(["-f", "concat", "-safe", "0", "-i", concat_list_path, "-map", "0:a:0",
                        "-ar", str(sample_rate), "-ac", str(channels), "-c:a", "libmp3lame",
                        "-b:a", "192k", "-threads", "2", merged], 600)
                if not os.path.isfile(merged) or os.path.getsize(merged) < 100 or self.get_audio_duration_ms(merged) <= 0:
                    raise RuntimeError("병합된 MP3 파일을 읽을 수 없습니다. 개별 음성은 보관됩니다.")
                os.replace(merged, output_file)
                return output_file, timings
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("오디오 병합 시간이 초과됐습니다. 개별 음성은 보관되며 이어서 생성하면 병합을 다시 진행합니다.") from exc

        # An in-memory pydub fallback for 30–40 minute stories can exhaust the
        # shared site's RAM and disconnect every visitor. Keep the saved clips
        # and report the FFmpeg error instead of risking a process-wide crash.
