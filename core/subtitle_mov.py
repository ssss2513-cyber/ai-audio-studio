"""On-demand transparent subtitle movies, independent of every TTS queue."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from .audio_processor import AudioProcessor

VERSION = 'transparent-mov-v1'
FPS = 30
ACTIVE = ('queued', 'rendering', 'cancelling')
SIZES = ((1920, 1080), (1080, 1920))
_LOCK = threading.RLock()
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='subtitle-mov')
_JOBS = {}
_STAMP = r'(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})'
_RANGE = re.compile(r'^' + _STAMP + r'\s+-->\s+' + _STAMP + r'\s*$')


def _atomic_json(path, data):
    temporary = path.with_suffix('.part.json')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def _cues(data):
    text = data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    cues = []
    for block in re.split(r'\n\s*\n', text.strip()):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise ValueError('자막 번호 또는 내용이 올바르지 않습니다. 현재 결과의 SRT를 확인해주세요.')
        match = _RANGE.fullmatch(lines[1].strip())
        if not match:
            raise ValueError('SRT 자막 시간을 읽을 수 없습니다.')
        numbers = [int(value) for value in match.groups()]
        start, end = [(values[0] * 3600 + values[1] * 60 + values[2]) * 1000 + values[3]
                      for values in (numbers[:4], numbers[4:])]
        if end <= start or end > 8 * 3600000:
            raise ValueError('자막 시간 범위가 올바르지 않습니다.')
        content = '\n'.join(lines[2:]).strip()
        content = html.unescape(re.sub(r'</?(?:b|i|u|font)(?:\s[^>]*)?>', '', content, flags=re.I))
        # Treat script text as text, never as libass positioning/drawing commands.
        content = content.replace('\\', '＼').replace('{', '｛').replace('}', '｝')
        content = ''.join(char for char in content if ord(char) >= 32 or char == '\n')
        if not content.strip():
            raise ValueError('내용이 비어 있는 자막이 있습니다.')
        cues.append((start, end, content.replace('\n', r'\N')))
    if not cues or len(cues) > 20000:
        raise ValueError('변환할 SRT 자막을 확인해주세요.')
    return cues


def _ass_time(ms):
    # ASS uses centiseconds; the movie has the usual 30 fps frame resolution.
    seconds, fraction = divmod((ms + 5) // 10, 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}'


def _ass(cues, width, height, font_size):
    margin_x = round(width * 0.06)
    margin_y = round(height * 0.075)
    outline = max(2, round(font_size / 16))
    lines = [
        '[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {width}', f'PlayResY: {height}',
        'WrapStyle: 0', 'ScaledBorderAndShadow: yes', '', '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
        'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding',
        f'Style: Default,Noto Sans CJK KR,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HFF000000,'
        f'-1,0,0,0,100,100,0,0,1,{outline},0,2,{margin_x},{margin_x},{margin_y},1',
        '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    for start, end, text in cues:
        # Keep very short cues nonempty even when rounded to ASS centiseconds.
        end = max(end, ((start + 5) // 10 + 1) * 10)
        lines.append(f'Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}')
    return '\n'.join(lines) + '\n'


def _folder(work_dir, identity):
    if not re.fullmatch(r'[a-f0-9]{64}', str(identity)):
        raise ValueError('자막 영상 작업 번호가 올바르지 않습니다.')
    return Path(work_dir).resolve() / 'subtitle_mov' / identity


def get_export(work_dir, identity):
    if not identity:
        return {}
    folder = _folder(work_dir, identity)
    with _LOCK:
        try:
            state = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        if state.get('status') in ACTIVE and str(folder) not in _JOBS:
            state.update(status='interrupted', message='사이트가 재시작되어 MOV 변환이 중단되었습니다. MOV 만들기를 다시 눌러주세요. 음성은 다시 생성하지 않습니다.')
        if state.get('status') == 'complete' and not (folder / 'subtitles.mov').is_file():
            state.update(status='interrupted', message='보관된 MOV를 찾지 못했습니다. MOV 만들기를 다시 눌러주세요.')
        return state


def _update(folder, **values):
    with _LOCK:
        job = _JOBS[str(folder)]
        job['state'].update(values)
        _atomic_json(folder / 'state.json', job['state'])


def start_export(work_dir, srt_path, audio_path, width=1920, height=1080, font_size=64):
    if (width, height) not in SIZES or type(font_size) is not int or not 32 <= font_size <= 88:
        raise ValueError('자막 영상 크기와 글자 크기를 확인해주세요.')
    root = Path(work_dir).resolve()
    source, audio = Path(srt_path).resolve(), Path(audio_path).resolve()
    if not source.is_relative_to(root) or not audio.is_relative_to(root):
        raise ValueError('현재 접속의 완성 음성과 자막을 선택해주세요.')
    if not source.is_file() or source.stat().st_size > 4 * 1024**2 or not audio.is_file():
        raise ValueError('완성 음성 또는 SRT 파일을 찾을 수 없습니다.')
    data = source.read_bytes()
    cues = _cues(data)
    audio_ms = AudioProcessor.get_audio_duration_ms(str(audio))
    if audio_ms <= 0:
        raise ValueError('완성 음성의 길이를 읽지 못했습니다.')
    duration_ms = max(audio_ms, max(end for _, end, _ in cues))
    if duration_ms > 8 * 3600000:
        raise ValueError('8시간 이하의 결과에서 자막 MOV를 만들 수 있습니다.')
    settings = dict(version=VERSION, width=width, height=height, font_size=font_size,
                    fps=FPS, duration_ms=duration_ms)
    identity = hashlib.sha256(data + json.dumps(settings, sort_keys=True).encode()).hexdigest()
    folder = _folder(root, identity)
    with _LOCK:
        if str(folder) in _JOBS or get_export(root, identity).get('status') == 'complete':
            return identity
        if any(Path(key).parent == folder.parent for key in _JOBS):
            raise ValueError('현재 MOV 변환을 완료하거나 중단한 뒤 다른 설정으로 만들어주세요.')
        if len(_JOBS) >= 8:
            raise ValueError('자막 영상 변환 대기열이 가득 찼습니다. 잠시 후 MOV 만들기를 눌러주세요.')
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        (folder / 'subtitles.ass').write_text(_ass(cues, width, height, font_size), encoding='utf-8')
        state = dict(settings, id=identity, status='queued', progress=0,
                     message='자막 영상 변환 대기 중', file=str(folder / 'subtitles.mov'))
        _atomic_json(folder / 'state.json', state)
        _JOBS[str(folder)] = dict(state=state, cancel=threading.Event())
        try:
            _JOBS[str(folder)]['future'] = _POOL.submit(_render, folder)
        except Exception:
            _JOBS.pop(str(folder), None)
            raise
    return identity


def cancel_export(work_dir, identity):
    folder = _folder(work_dir, identity)
    with _LOCK:
        job = _JOBS.get(str(folder))
        if not job:
            return
        job['cancel'].set()
        if job['future'].cancel():
            _update(folder, status='cancelled', message='MOV 변환을 중단했습니다. 음성과 SRT는 보관됩니다.')
            _JOBS.pop(str(folder), None)
        else:
            _update(folder, status='cancelling', message='자막 영상 변환을 중단하고 있습니다.')


def _stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _render(folder):
    with _LOCK:
        job = _JOBS[str(folder)]
        state = dict(job['state'])
        cancel = job['cancel']
    process = None
    temporary = folder / 'subtitles.part.mov'
    try:
        if cancel.is_set():
            raise InterruptedError()
        for name in ('ffmpeg', 'ffprobe', 'fc-match'):
            if not shutil.which(name):
                raise RuntimeError('자막 영상 구성 요소 설치가 필요합니다: ' + name)
        font = subprocess.run(['fc-match', '-f', '%{family}', 'Noto Sans CJK KR'],
                              capture_output=True, text=True, timeout=20, check=True)
        if 'Noto Sans CJK' not in font.stdout:
            raise RuntimeError('한글 자막 글꼴 설치가 아직 완료되지 않았습니다. 잠시 후 다시 눌러주세요.')
        width, height = state['width'], state['height']
        frames = math.ceil(state['duration_ms'] * FPS / 1000)
        # libass paints alpha on transparent black. Convert premultiplied RGB
        # to straight alpha before lossless ARGB encoding, so text edges do not
        # acquire a dark fringe when placed over a light-coloured background.
        source = f'color=c=black@0.0:s={width}x{height}:r={FPS},format=rgba'
        filters = 'ass=filename=subtitles.ass:alpha=1,format=gbrap,unpremultiply=inplace=1,format=argb'
        command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                   '-filter_threads', '1', '-f', 'lavfi', '-i', source,
                   '-vf', filters, '-map', '0:v:0', '-an', '-c:v', 'qtrle', '-pix_fmt', 'argb',
                   '-threads', '1', '-g', '300', '-frames:v', str(frames),
                   '-video_track_timescale', '30000', '-progress', 'render.progress',
                   '-nostats', str(temporary)]
        if cancel.is_set():
            raise InterruptedError()
        _update(folder, status='rendering', message='투명 자막 MOV를 만들고 있습니다.')
        started = time.monotonic()
        with (folder / 'render.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=folder, stdout=subprocess.DEVNULL, stderr=log)
            while process.poll() is None:
                if cancel.wait(1):
                    raise InterruptedError()
                if time.monotonic() - started > max(1800, state['duration_ms'] / 1000 * 8):
                    raise RuntimeError('자막 영상 변환 시간이 초과됐습니다. 음성과 SRT는 그대로 보관됩니다.')
                if not folder.is_dir():
                    raise InterruptedError()
                if shutil.disk_usage(folder).free < 128 * 1024**2:
                    raise RuntimeError('MOV를 저장할 서버 공간이 부족합니다. 음성과 SRT는 보관됩니다.')
                try:
                    with (folder / 'render.progress').open('rb') as progress:
                        progress.seek(max(0, os.fstat(progress.fileno()).st_size - 4096))
                        counts = re.findall(rb'(?:^|\n)frame=(\d+)', progress.read())
                    if counts:
                        _update(folder, progress=min(0.99, int(counts[-1]) / frames))
                except FileNotFoundError:
                    pass
        if cancel.is_set():
            raise InterruptedError()
        if process.returncode:
            with (folder / 'render.log').open('rb') as log:
                log.seek(max(0, os.fstat(log.fileno()).st_size - 1200))
                detail = log.read().decode('utf-8', errors='replace')
            raise RuntimeError('MOV 변환을 완료하지 못했습니다. 음성과 SRT는 보관됩니다. ' + detail)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                '-of', 'json', str(temporary)], capture_output=True, text=True,
                               timeout=30, check=True)
        media = json.loads(probe.stdout)
        streams = media.get('streams', [])
        duration = float(media.get('format', {}).get('duration', 0))
        if (len(streams) != 1 or streams[0].get('codec_name') != 'qtrle'
                or streams[0].get('pix_fmt') != 'argb'
                or streams[0].get('width') != width or streams[0].get('height') != height
                or not math.isfinite(duration) or abs(duration - frames / FPS) > 2 / FPS
                or temporary.stat().st_size < 100):
            raise RuntimeError('투명도·크기·길이가 맞지 않아 MOV를 게시하지 않았습니다. 음성과 SRT는 보관됩니다.')
        with _LOCK:
            if cancel.is_set():
                raise InterruptedError()
            os.replace(temporary, folder / 'subtitles.mov')
            _update(folder, status='complete', progress=1.0, message='투명 자막 MOV가 준비되었습니다.')
    except InterruptedError:
        if folder.is_dir():
            _update(folder, status='cancelled', message='MOV 변환을 중단했습니다. 음성과 SRT는 보관됩니다.')
    except Exception as exc:
        if folder.is_dir():
            _update(folder, status='failed', message=str(exc)[:1500])
    finally:
        _stop(process)
        temporary.unlink(missing_ok=True)
        with _LOCK:
            _JOBS.pop(str(folder), None)
