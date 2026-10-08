"""Two independent Kaggle batch jobs, then one lossless, ordered site merge.

Only start() submits GPU work. Reconnect/restore only read existing jobs.
"""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
import wave
import zipfile

from . import kaggle_jobs as jobs
from cosy3_voicebank_catalog import ATTRIBUTION
from audio_join import copy_pcm_clip, write_pcm_silence

ENGINES = {'cosyvoice': ('코지2', 'CosyVoice2_Kaggle_DualGPU.ipynb'),
           'cosyvoice3': ('코지3', 'CosyVoice3_Kaggle_DualGPU.ipynb')}


def split_plan(plan, engine):
    part = deepcopy(plan)
    part['items'] = [item for item in part['items'] if item['engine'] == engine]
    refs = {item.get('reference_id') for item in part['items']}
    part['references'] = {key: value for key, value in part['references'].items() if key in refs}
    part['site_parallel_shard'] = True
    return part


def _archive(part, original):
    target = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(
            target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as out:
        out.writestr('plan.json', json.dumps(part, ensure_ascii=False))
        out.writestr('VOICE_ATTRIBUTION.txt', ATTRIBUTION)
        for name in part['references'].values():
            out.writestr(name, source.read(name))
    return target.getvalue()


def start(work_dir, plan, archive, credentials, force=False):
    if not credentials:
        raise ValueError('내 캐글 계정을 먼저 연결해주세요.')
    with jobs.LOCK:
        old = jobs.get_job(work_dir)
        if jobs.is_running(work_dir) or jobs.monitoring(work_dir):
            raise ValueError('현재 캐글 작업이 진행 중입니다. 완료 후 다음 작업을 시작해주세요.')
        if old and old.get('status') == 'needs_check':
            raise ValueError('이전 캐글 작업의 상태 확인이 필요합니다. ‘이전 작업 상태 확인·결과 받기’를 눌러주세요.')
        plan = deepcopy(plan)
        request_signature = jobs.fingerprint(plan)
        reuse = (not force and old and old.get('kind') == 'parallel_cosy'
                 and old.get('request_signature', old.get('signature')) == request_signature
                 and old.get('owner') == credentials['username'])
        if reuse and old.get('status') == 'complete' and Path(old.get('result', {}).get('full_audio', '')).is_file():
            return old
        if reuse:
            state = old
            plan = json.loads((Path(state['folder']) / 'plan.json').read_text(encoding='utf-8'))
        else:
            if force:
                plan['generation_nonce'] = secrets.token_hex(16)
            ident = time.strftime('%Y%m%d%H%M%S', time.gmtime()) + '-' + secrets.token_hex(4)
            folder = Path(work_dir) / 'kaggle_jobs' / ('parallel-' + ident)
            folder.mkdir(parents=True, mode=0o700)
            state = dict(kind='parallel_cosy', id=ident, folder=str(folder), owner=credentials['username'],
                         signature=jobs.fingerprint(plan), request_signature=request_signature,
                         total=len(plan['items']), done=0, children={}, started=time.time())
        for engine in ENGINES:
            (Path(state['folder']) / engine).mkdir(exist_ok=True, mode=0o700)
        (Path(state['folder']) / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        state.update(status='uploading', error='', message='코지2·코지3를 별도 캐글 작업으로 동시에 요청합니다.')
        state.pop('result', None)
        jobs._save(work_dir, state)
        _launch(work_dir, state, deepcopy(credentials), archive=archive, submit=True)
        return state


def _snapshot(state):
    state['children'] = {engine: jobs.get_job(Path(state['folder']) / engine)
                         for engine in ENGINES}
    state['done'] = sum(child.get('done', 0) for child in state['children'].values() if child)


def _backup(state, plan):
    children = state.get('children', {})
    if not all((children.get(engine) or {}).get('ref') for engine in ENGINES):
        return
    payload = dict(format='voice-studio-parallel-v1', plan=plan,
                   refs={engine: children[engine]['ref'] for engine in ENGINES})
    path = Path(state['folder']) / 'parallel_recovery.json'
    content = json.dumps(payload, ensure_ascii=False)
    if not path.is_file() or path.read_text(encoding='utf-8') != content:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.chmod(0o600)
        os.replace(temporary, path)
    state['recovery'] = str(path)


def _launch(work_dir, state, credentials, archive=None, submit=False):
    key = str(Path(work_dir).resolve())
    with jobs.LOCK:
        if key in jobs.WORKERS:
            return False
        def target():
            try:
                _coordinate(work_dir, state, credentials, archive, submit)
            except Exception as exc:
                message = str(exc)
                for field in ('token', 'key'):
                    if credentials.get(field):
                        message = message.replace(credentials[field], '[인증정보]')
                state.update(status='needs_check', error=message,
                             message='두 작업의 상태·결과를 다시 확인해주세요. 자동으로 재생성하지 않습니다.')
                jobs._save(work_dir, state)
            finally:
                with jobs.LOCK:
                    jobs.WORKERS.pop(key, None)
        thread = threading.Thread(target=target, daemon=True, name='cosy-parallel-coordinator')
        jobs.WORKERS[key] = thread
        thread.start()
    return True


def _coordinate(work_dir, state, credentials, archive, submit):
    plan = json.loads((Path(state['folder']) / 'plan.json').read_text(encoding='utf-8'))
    start_errors = {}
    # start_job launches a separate submission/monitor thread immediately.
    # Neither engine waits for the other to install, load, or synthesize.
    for engine, (_, notebook) in ENGINES.items():
        folder = Path(state['folder']) / engine
        child = jobs.get_job(folder)
        try:
            if jobs.monitoring(folder):
                continue
            if child and (child.get('status') in jobs.ACTIVE or child.get('status') == 'needs_check'):
                jobs.reconnect(folder, credentials)
            elif submit and child and child.get('status') == 'complete' and Path(
                    child.get('result', {}).get('clips_archive', '')).is_file():
                continue
            elif submit:
                part = split_plan(plan, engine)
                jobs.start_job(folder, part, _archive(part, archive), credentials, notebook)
            elif child:
                jobs.reconnect(folder, credentials)
            else:
                start_errors[engine] = '제출된 작업이 없습니다. 생성 버튼을 눌러 시작해주세요.'
        except (ValueError, OSError, jobs.KaggleError) as exc:
            start_errors[engine] = str(exc)
    while True:
        _snapshot(state)
        _backup(state, plan)
        children = state['children']
        state['start_errors'] = start_errors
        live = any(jobs.is_running(Path(state['folder']) / engine)
                   or jobs.monitoring(Path(state['folder']) / engine) for engine in ENGINES)
        if not live:
            break
        state.update(status='running', error='',
                     message='코지2·코지3가 별도 작업에서 생성 중입니다. 먼저 끝난 모델은 결과를 보관합니다.')
        jobs._save(work_dir, state)
        threading.Event().wait(3)
    if all(children.get(engine) and children[engine].get('status') == 'complete' for engine in ENGINES):
        state.update(status='receiving', error='', message='두 모델 생성 완료 · 원본 WAV를 대본 순번대로 합칩니다.')
        jobs._save(work_dir, state)
        state['result'] = merge(plan, state)
        state.update(status='complete', done=len(plan['items']), error='',
                     message='코지2·코지3 동시 생성 완료 · 대본 순서대로 합친 MP3 한 파일이 준비되었습니다.')
    else:
        uncertain = any(child and child.get('status') == 'needs_check' for child in children.values())
        errors = []
        for engine, (label, _) in ENGINES.items():
            child = children.get(engine) or {}
            if child.get('status') != 'complete':
                errors.append(label + ': ' + (start_errors.get(engine) or child.get('error') or '작업을 완료하지 못했습니다.'))
        state.update(status='needs_check' if uncertain else 'failed', error='\n\n'.join(errors),
            message='완료된 모델의 음성은 보관했습니다. 실패한 작업을 확인한 뒤 이어서 생성할 수 있습니다.')
    jobs._save(work_dir, state)


def reconnect(work_dir, state, credentials):
    if state.get('owner', '').lower() != credentials['username'].lower():
        raise ValueError('두 작업을 만든 캐글 계정으로 연결해주세요.')
    with jobs.LOCK:
        if jobs.monitoring(work_dir):
            return False
        state.update(status='checking', error='', message='코지2·코지3의 기존 작업을 각각 확인합니다.')
        jobs._save(work_dir, state)
        return _launch(work_dir, state, deepcopy(credentials))


def restore(work_dir, payload, credentials):
    """A downloaded recovery file restores both refs without GPU submission."""
    if not isinstance(payload, dict) or payload.get('format') != 'voice-studio-parallel-v1':
        raise ValueError('코지2·3 동시 작업 복구 JSON 파일을 선택해주세요.')
    plan, refs = payload.get('plan'), payload.get('refs')
    if not isinstance(plan, dict) or not isinstance(refs, dict) or set(refs) != set(ENGINES):
        raise ValueError('복구 파일에 대본 또는 두 작업 주소가 없습니다.')
    items = plan.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= 4096 or not isinstance(plan.get('references'), dict):
        raise ValueError('복구 파일의 대본 형식이 올바르지 않습니다.')
    indices = [item.get('index') for item in items if isinstance(item, dict)]
    if len(indices) != len(items) or any(type(i) is not int or i < 1 for i in indices) or len(set(indices)) != len(indices):
        raise ValueError('복구 파일의 대사 순번이 올바르지 않습니다.')
    if {item.get('engine') for item in items} != set(ENGINES):
        raise ValueError('코지2·3 혼합 대본 복구 파일이 필요합니다.')
    for ref in refs.values():
        if not isinstance(ref, str) or not jobs.REF_PATTERN.fullmatch(ref):
            raise ValueError('복구할 캐글 작업 주소가 올바르지 않습니다.')
        if ref.split('/')[0].lower() != credentials['username'].lower():
            raise ValueError('두 작업을 만든 캐글 계정으로 연결해주세요.')
    with jobs.LOCK:
        if jobs.is_running(work_dir) or jobs.monitoring(work_dir):
            raise ValueError('현재 작업이 끝난 뒤 복구해주세요.')
        ident = secrets.token_hex(12)
        folder = Path(work_dir) / 'kaggle_jobs' / ('parallel-' + ident)
        folder.mkdir(parents=True, mode=0o700)
        (folder / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        for engine, ref in refs.items():
            child_root = folder / engine
            child_root.mkdir(mode=0o700)
            job_id = ref.split('/voice-studio-', 1)[1]
            child_folder = child_root / 'kaggle_jobs' / job_id
            child_folder.mkdir(parents=True, mode=0o700)
            jobs._save(child_root, dict(id=job_id, ref=ref, folder=str(child_folder),
                signature=jobs.fingerprint(split_plan(plan, engine)), parallel_shard=True,
                total=sum(item['engine'] == engine for item in items), done=0,
                status='checking', started=time.time(), error=''))
        request_plan = deepcopy(plan)
        request_plan['generation_nonce'] = ''
        state = dict(kind='parallel_cosy', id=ident, folder=str(folder), owner=credentials['username'],
            signature=jobs.fingerprint(plan), total=len(items), done=0, children={}, started=time.time(),
            request_signature=jobs.fingerprint(request_plan),
            status='checking', error='', message='저장된 코지2·코지3 작업을 불러옵니다.')
        jobs._save(work_dir, state)
        _launch(work_dir, state, deepcopy(credentials))


def _timestamp(ms, comma=True):
    seconds, fraction = divmod(max(0, round(ms)), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}' + (',' if comma else '.') + f'{fraction:03d}'


def merge(plan, state):
    """Stream checked PCM clips in original order; encode only the final MP3."""
    folder = Path(state['folder']) / 'merged'
    folder.mkdir(exist_ok=True, mode=0o700)
    clips = {}
    for engine in ENGINES:
        archive_path = state['children'][engine]['result']['clips_archive']
        expected = {item['index'] for item in plan['items'] if item['engine'] == engine}
        with zipfile.ZipFile(archive_path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 2 * 1024**3:
                raise ValueError('원본 음성 묶음이 수신 한도를 넘습니다.')
            if archive.getinfo('plan.json').file_size > 16 * 1024**2 or archive.getinfo('clip_index.json').file_size > 4 * 1024**2:
                raise ValueError('대본 또는 순번 정보가 너무 큽니다.')
            remote_plan = json.loads(archive.read('plan.json'))
            if jobs.fingerprint(remote_plan) != jobs.fingerprint(split_plan(plan, engine)):
                raise ValueError('모델별 대본 설정이 원본과 달라 합치기를 중단했습니다.')
            index = json.loads(archive.read('clip_index.json'))
            if not isinstance(index, list) or len(index) != len(expected) or {row['index'] for row in index} != expected:
                raise ValueError('누락되거나 중복된 대사가 있어 합치기를 중단했습니다.')
            for row in index:
                name = row['file']
                if not re.fullmatch(r'clips/[a-f0-9]{64}\.wav', name):
                    raise ValueError('원본 음성 파일명이 올바르지 않습니다.')
                path = folder / f"{row['index']:04d}.wav"
                digest = hashlib.sha256()
                with archive.open(name) as src, path.open('wb') as dst:
                    while block := src.read(1024 * 1024):
                        digest.update(block)
                        dst.write(block)
                if digest.hexdigest() != row['sha256']:
                    raise ValueError('원본 음성 수신이 불완전합니다. 상태·결과 다시 확인을 눌러주세요.')
                clips[row['index']] = path
    items = sorted(plan['items'], key=lambda item: item['index'])
    elapsed, rate = 0, None
    srt, vtt, timings = [], ['WEBVTT\n'], []
    wav_path = folder / 'full_audio.wav'
    wav_temporary = folder / 'full_audio.part.wav'
    with wave.open(str(wav_temporary), 'wb') as dst:
        for ordinal, item in enumerate(items, 1):
            with wave.open(str(clips[item['index']]), 'rb') as src:
                if src.getnchannels() != 1 or src.getsampwidth() != 2 or src.getcomptype() != 'NONE' or not src.getnframes():
                    raise ValueError('합칠 원본 WAV 형식이 올바르지 않습니다.')
                if rate is None:
                    rate = src.getframerate()
                    dst.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                if src.getframerate() != rate:
                    raise ValueError('두 모델의 원본 샘플레이트가 달라 합치기를 중단했습니다.')
                if ordinal > 1:
                    silence = round(rate * max(0, min(10000, int(plan.get('pause_ms', 500)))) / 1000)
                    elapsed += write_pcm_silence(dst, silence)
                begin = elapsed * 1000 / rate
                elapsed += copy_pcm_clip(src, dst)
            end = elapsed * 1000 / rate
            text = item['text'].replace('\r', ' ').replace('\n', ' ')
            if plan.get('include_speaker'):
                text = item['speaker'] + ': ' + text
            srt.append(f'{ordinal}\n{_timestamp(begin)} --> {_timestamp(end)}\n{text}\n')
            vtt.append(f'{ordinal}\n{_timestamp(begin, False)} --> {_timestamp(end, False)}\n{text}\n')
            timings.append(dict(index=item['index'], speaker=item['speaker'], engine=item['engine'],
                                start_ms=round(begin), end_ms=round(end)))
    os.replace(wav_temporary, wav_path)
    temporary = folder / 'full_audio.part.mp3'
    subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    '-i', str(wav_path), '-codec:a', 'libmp3lame', '-b:a', '192k', str(temporary)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=1800)
    os.replace(temporary, folder / 'full_audio.mp3')
    (folder / 'subtitles.srt').write_text('\n'.join(srt), encoding='utf-8-sig')
    (folder / 'subtitles.vtt').write_text('\n'.join(vtt), encoding='utf-8')
    (folder / 'timing.json').write_text(json.dumps(timings, ensure_ascii=False), encoding='utf-8')
    (folder / 'VOICE_ATTRIBUTION.txt').write_text(ATTRIBUTION, encoding='utf-8')
    with zipfile.ZipFile(folder / 'complete_audio.zip', 'w', compression=zipfile.ZIP_STORED) as archive:
        for name in ('full_audio.mp3', 'subtitles.srt', 'subtitles.vtt', 'timing.json', 'VOICE_ATTRIBUTION.txt'):
            archive.write(folder / name, name)
    return {key: str(folder / name) for key, name in (
        ('full_audio', 'full_audio.mp3'), ('srt', 'subtitles.srt'),
        ('vtt', 'subtitles.vtt'), ('main_zip', 'complete_audio.zip'))}
