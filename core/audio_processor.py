import os
import math
import subprocess
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Tuple
try:
    from pydub import AudioSegment
except Exception:
    AudioSegment = None
import mutagen.mp3
from audio_join import copy_pcm_clip, wrap_pcm32, write_pcm_silence

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
        if not isinstance(pause_ms, (int, float)) or not math.isfinite(pause_ms) or pause_ms < 0:
            raise ValueError("대사 사이 간격은 0 이상의 숫자여야 합니다.")

        indices = [segment.get("index") for segment in segment_info_list]
        if any(type(index) is not int or index <= 0 for index in indices) or len(set(indices)) != len(indices):
            raise ValueError("대사 번호가 중복되거나 올바르지 않아 병합하지 않았습니다.")
        segment_info_list = sorted(segment_info_list, key=lambda segment: segment["index"])

        out_dir = os.path.dirname(os.path.abspath(output_file))
        os.makedirs(out_dir, exist_ok=True)

        timings: List[AudioTiming] = []
        valid_segments: List[dict] = []

        for i, seg in enumerate(segment_info_list):
            audio_path = seg['file_path']
            if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 100:
                raise ValueError(f"대사 {seg.get('index', i + 1)}번의 저장된 음성 파일을 찾을 수 없습니다.")

            valid_segments.append(seg)

        if not valid_segments:
            raise ValueError("병합할 수 있는 유효한 오디오 파일이 없습니다.")

        # Decode compressed clips separately so each decoder can honor its own
        # encoder delay/padding. Join PCM samples, never MP3 packet boundaries.
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
        if channels not in (1, 2) or sample_rate <= 0:
            raise ValueError("음성의 샘플레이트 또는 채널 형식이 올바르지 않습니다.")
        identical_pcm = len(set(profiles)) == 1 and profiles[0][0] == 'wav' and profiles[0][3] in (2, 4)
        width = profiles[0][3] if identical_pcm else 4

        def ffmpeg(arguments, timeout):
            result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y"] + arguments,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
            if result.returncode:
                detail = result.stderr.decode("utf-8", errors="replace")[-1000:]
                raise RuntimeError("전체 오디오 병합에 실패했습니다. 개별 음성은 보관됩니다. " + detail)

        try:
            with tempfile.TemporaryDirectory(prefix=".audio_merge_", dir=out_dir) as temporary:
                paths = [seg["file_path"] for seg in valid_segments]
                if not identical_pcm:
                    def normalize(index):
                        source = paths[index]
                        if profiles[index] == ("wav", sample_rate, channels, 4):
                            return source
                        raw = os.path.join(temporary, f"{index:06d}.pcm")
                        target = os.path.join(temporary, f"{index:06d}.wav")
                        # Keep high-resolution decoded PCM until the one final
                        # MP3 encode; don't quantize mixed input to 16 bit first.
                        ffmpeg(["-i", source, "-map", "0:a:0", "-vn", "-ar", str(sample_rate),
                                "-ac", str(channels), "-c:a", "pcm_s32le", "-f", "s32le",
                                "-threads", "1", raw], 120)
                        wrap_pcm32(raw, target, sample_rate, channels)
                        os.unlink(raw)
                        return target

                    with ThreadPoolExecutor(max_workers=2) as pool:
                        paths = list(pool.map(normalize, range(len(paths))))

                joined = os.path.join(temporary, "joined.wav")
                elapsed = 0
                pause_frames = round(sample_rate * pause_ms / 1000)
                with wave.open(joined, "wb") as destination:
                    destination.setparams((channels, width, sample_rate, 0, 'NONE', 'not compressed'))
                    for index, (source, seg) in enumerate(zip(paths, valid_segments)):
                        if index and pause_frames:
                            elapsed += write_pcm_silence(destination, pause_frames)
                        start_ms = round(elapsed * 1000 / sample_rate)
                        with wave.open(str(source), "rb") as audio:
                            elapsed += copy_pcm_clip(audio, destination)
                        end_ms = round(elapsed * 1000 / sample_rate)
                        timings.append(AudioTiming(
                            segment_index=seg['index'], speaker=seg.get('speaker', ''),
                            text=seg.get('text', ''), file_path=seg['file_path'],
                            start_ms=start_ms, end_ms=end_ms, duration_ms=end_ms - start_ms))
                merged = os.path.join(temporary, "merged.mp3")
                ffmpeg(["-i", joined, "-map", "0:a:0",
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
