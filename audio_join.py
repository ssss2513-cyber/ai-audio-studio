"""Bounded-memory PCM joining with a sample-count-preserving edge guard.

Only nonzero outer boundaries are softened, for at most 2 ms. Speech is never
trimmed or overlapped, and interior PCM bytes, pitch, speed and level remain
unchanged. No denoiser, compressor or loudness normalization is applied.
"""
from array import array
import math
from pathlib import Path
import sys
import wave

EDGE_MS = 2.0
BLOCK_FRAMES = 65536


def _soften(raw, channels, sample_width, fade_in):
    samples = array('h' if sample_width == 2 else 'i')
    samples.frombytes(raw)
    if sys.byteorder != 'little':
        samples.byteswap()
    frames = len(samples) // channels
    if frames < 2:
        return raw
    threshold = max(1, round((1 << (sample_width * 8 - 1)) * 0.0001))
    boundary = 0 if fade_in else (frames - 1) * channels
    changed = [channel for channel in range(channels) if abs(samples[boundary + channel]) > threshold]
    if not changed:
        return raw
    for frame in range(frames):
        # Raised-cosine ramp has a flat slope at both ends. Gain is always
        # between zero and one: smoothing cannot introduce an over-range peak.
        gain = 0.5 - 0.5 * math.cos(math.pi * frame / (frames - 1))
        if not fade_in:
            gain = 1.0 - gain
        for channel in changed:
            index = frame * channels + channel
            samples[index] = round(samples[index] * gain)
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples.tobytes()


def copy_pcm_clip(source, destination):
    """Copy an opened PCM WAV into an identically configured WAV writer.

    Reads only small buffers and checks every declared frame. Returns the exact
    frame count for subtitle timing. The source file is opened read-only.
    """
    channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
    frames = source.getnframes()
    if (source.getcomptype() != 'NONE' or channels not in (1, 2)
            or width not in (2, 4) or rate <= 0 or frames <= 0):
        raise ValueError('합칠 음성은 비어 있지 않은 모노/스테레오 PCM WAV여야 합니다.')
    if (destination.getnchannels(), destination.getsampwidth(), destination.getframerate()) != (channels, width, rate):
        raise ValueError('대사별 음성 형식이 달라 합치기를 중단했습니다.')
    if source.tell() != 0:
        raise ValueError('음성의 처음부터 합쳐야 합니다.')
    frame_bytes = channels * width

    def read_exact(count):
        data = source.readframes(count)
        if len(data) != count * frame_bytes:
            raise ValueError('끝까지 저장되지 않은 대사가 있어 합치기를 중단했습니다. 원본 음성은 보관됩니다.')
        return data

    edge = min(round(rate * EDGE_MS / 1000), frames // 4)
    if edge < 2:
        edge = 0
    if edge:
        destination.writeframesraw(_soften(read_exact(edge), channels, width, True))
    remaining = frames - edge * 2
    while remaining:
        count = min(BLOCK_FRAMES, remaining)
        destination.writeframesraw(read_exact(count))
        remaining -= count
    if edge:
        destination.writeframesraw(_soften(read_exact(edge), channels, width, False))
    return frames


def write_pcm_silence(destination, frames):
    """Write an exact pause without allocating the whole pause in memory."""
    if type(frames) is not int or frames < 0:
        raise ValueError('대사 사이 무음 길이가 올바르지 않습니다.')
    frame_bytes = destination.getnchannels() * destination.getsampwidth()
    remaining = frames
    while remaining:
        count = min(BLOCK_FRAMES, remaining)
        destination.writeframesraw(b'\0' * (count * frame_bytes))
        remaining -= count
    return frames


def wrap_pcm32(raw_path, wav_path, rate, channels):
    """Wrap decoded signed-32-bit little-endian PCM without another quantize.

    Python writes a regular PCM WAV header even on Python 3.10. This avoids
    depending on support for FFmpeg's optional WAVE_FORMAT_EXTENSIBLE header.
    """
    size = Path(raw_path).stat().st_size
    frame_bytes = channels * 4
    if size <= 0 or size % frame_bytes:
        raise ValueError('복원된 음성 데이터가 불완전해 합치기를 중단했습니다.')
    with open(raw_path, 'rb') as source, wave.open(str(wav_path), 'wb') as destination:
        destination.setparams((channels, 4, rate, 0, 'NONE', 'not compressed'))
        while block := source.read(BLOCK_FRAMES * frame_bytes):
            destination.writeframesraw(block)
