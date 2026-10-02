"""Optional Kaggle downloads. Existing Colab connections remain independent."""
import hashlib
import io
import json
import math
from pathlib import Path
import secrets
import zipfile

import streamlit as st

from cosy_kaggle_contract import (FORMAT, VERSION, MODELS, MAX_ITEMS,
    MAX_REFERENCE_BYTES, MAX_REFERENCES_BYTES)
from cosy3_voicebank_catalog import BANK_REVISION, ATTRIBUTION
from .cosy3_client import request_payload
from .tts_engine import TTSEngine, VoiceConfig, clean_spoken_text

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOKS = {
    'cosyvoice': ('CosyVoice 2', 'CosyVoice2_Kaggle_DualGPU.ipynb'),
    'cosyvoice3': ('CosyVoice 3', 'CosyVoice3_Kaggle_DualGPU.ipynb'),
    'auto': ('CosyVoice 2·3 혼합 대본', 'CosyVoice2_3_Kaggle_DualGPU.ipynb'),
}


def notebook_download(engine, key):
    label, filename = NOTEBOOKS[engine]
    path = ROOT / filename
    if path.is_file():
        st.download_button('⬇️ ' + label + ' 캐글 노트북', path.read_bytes(),
            file_name=filename, mime='application/x-ipynb+json', key=key)
    else:
        st.caption('캐글 노트북 파일 반영 중입니다. 잠시 후 새로고침해주세요.')


def render_downloads():
    with st.expander('🚀 캐글 GPU 2개 · 코지2·3'):
        st.caption('기존 코랩도 계속 사용할 수 있습니다. 캐글은 대본 파일을 올려 직접 생성하는 추가 옵션입니다.')
        for engine in NOTEBOOKS:
            notebook_download(engine, 'kaggle_notebook_sidebar_' + engine)
        guide = ROOT / 'Kaggle_CosyVoice_Guide.txt'
        if guide.is_file():
            st.download_button('⬇️ 캐글 사용법', guide.read_bytes(),
                file_name=guide.name, mime='text/plain', key='kaggle_guide')
        st.caption('대본 분석과 화자 설정 후, 3번 생성 영역에서 ‘캐글 GPU 2개’를 선택하세요.')


def prepare_plan(segments, settings, pause_ms, include_speaker):
    """Export selected identities and conditioning, never URLs or API keys."""
    if not 1 <= len(segments) <= MAX_ITEMS:
        raise ValueError('캐글 대본은 1~4096개 대사까지 지원합니다.')
    items, references, by_path = [], {}, {}
    seen = set()
    for segment in segments:
        row = settings.get(segment.speaker, {})
        engine = row.get('engine')
        if engine not in MODELS:
            raise ValueError('캐글 생성은 선택한 모든 화자가 CosyVoice 2 또는 3일 때 사용할 수 있습니다.')
        if type(segment.index) is not int or segment.index < 1 or segment.index in seen:
            raise ValueError('대사 순번이 중복되거나 잘못되었습니다. 대본을 다시 분석해주세요.')
        seen.add(segment.index)
        config = VoiceConfig(engine=engine, voice=row.get('voice', ''),
            style=row.get('style', '🎤 기본'), speed_factor=float(row.get('speed', 1)),
            ref_audio_path=row.get('ref_audio_path', ''), prompt_text=row.get('prompt_text', ''))
        if engine == 'cosyvoice':
            prepared = TTSEngine.cosyvoice_request(segment.text, config)
            payload = {key: prepared[key] for key in ('text', 'prompt_text', 'speed', 'style_instruction')}
            ref_path = prepared['ref_path']
        else:
            payload = request_payload(clean_spoken_text(segment.text), config)
            ref_path = config.ref_audio_path if config.voice == 'custom' else ''
        text = payload['text']
        low, high = (0.5, 2.0) if engine == 'cosyvoice' else (0.8, 1.2)
        if not text.strip() or len(text) > (2000 if engine == 'cosyvoice' else 12000):
            raise ValueError(f'{segment.index}번 대사가 비어 있거나 너무 깁니다. 대사를 나눠주세요.')
        if '<|' in text or '|>' in text:
            raise ValueError(f'{segment.index}번 대사에서 모델 제어 문자를 제거해주세요.')
        if not math.isfinite(payload['speed']) or not low <= payload['speed'] <= high:
            raise ValueError(f'{segment.speaker}: 말하기 속도를 {low}~{high}로 설정해주세요.')
        needs_ref = engine == 'cosyvoice' or payload.get('voice') == 'custom'
        if needs_ref:
            path = Path(ref_path) if ref_path else None
            if not path or not path.is_file() or not payload.get('prompt_text', '').strip():
                raise ValueError(f'{segment.speaker}: 참고 음성과 실제 녹음 대사를 등록해주세요.')
            if str(path) not in by_path:
                if not 0 < path.stat().st_size <= MAX_REFERENCE_BYTES:
                    raise ValueError(f'{segment.speaker}: 참고 음성은 10MB 이하여야 합니다.')
                data = path.read_bytes()
                ref_id = hashlib.sha256(data).hexdigest()
                references[ref_id] = data
                by_path[str(path)] = ref_id
                if len(references) > 64 or sum(map(len, references.values())) > MAX_REFERENCES_BYTES:
                    raise ValueError('참고 음성은 최대 64개, 합계 64MB까지 넣을 수 있습니다.')
            payload['reference_id'] = by_path[str(path)]
        items.append(dict(payload, engine=engine, index=segment.index, speaker=segment.speaker))
    plan = dict(format=FORMAT, exporter_version=VERSION, bank_revision=BANK_REVISION,
        items=sorted(items, key=lambda item: item['index']),
        references={key: 'references/' + key + '.audio' for key in references},
        pause_ms=int(pause_ms), include_speaker=bool(include_speaker), generation_nonce='')
    return plan, references


def project_zip(plan, references):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr('plan.json', json.dumps(plan, ensure_ascii=False, indent=2))
        for key, data in references.items():
            archive.writestr(plan['references'][key], data)
        archive.writestr('VOICE_ATTRIBUTION.txt', ATTRIBUTION)
    return out.getvalue()


def render_export(segments, settings, pause_ms, include_speaker, force_overwrite, busy):
    engines = {settings.get(segment.speaker, {}).get('engine') for segment in segments}
    if not engines or not engines.issubset(MODELS):
        return False
    mode = st.radio('코지 전체 생성 위치', ['기존 코랩 연결', '캐글 GPU 2개'],
        horizontal=True, key='cosy_batch_location', disabled=busy,
        help='코랩은 기존 사이트 연결로 생성합니다. 캐글은 아래 파일을 받아 캐글 안에서 생성합니다. 화자 설정은 그대로 유지됩니다.')
    if mode != '캐글 GPU 2개':
        return False
    st.info('캐글에서 두 GPU가 각각 대사를 생성하고, 먼저 끝난 GPU가 다음 대사를 바로 이어서 만듭니다. 완료 후 대본 순서대로 MP3 하나로 합칩니다.')
    engine = next(iter(engines)) if len(engines) == 1 else 'auto'
    if len(engines) > 1:
        st.caption('혼합 대본은 코지2를 두 GPU로 생성한 다음 코지3를 두 GPU로 생성합니다. 최종 합치기는 원래 대본 순서입니다.')
    notebook_download(engine, 'kaggle_notebook_selected')
    try:
        plan, references = prepare_plan(segments, settings, pause_ms, include_speaker)
        signature = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        identity = (signature, bool(force_overwrite))
        cached = st.session_state.get('_cosy_kaggle_export', {})
        if cached.get('identity') != identity:
            if force_overwrite:
                plan['generation_nonce'] = secrets.token_hex(16)
            cached = dict(identity=identity, data=project_zip(plan, references))
            st.session_state['_cosy_kaggle_export'] = cached
        st.download_button('⬇️ 캐글용 대본·목소리 받기', cached['data'],
            file_name='CosyVoice_Kaggle_Project.zip', mime='application/zip',
            key='kaggle_project', disabled=busy, type='primary')
        st.caption(f'선택한 대사 {len(segments)}개 · 현재 화자·스타일·속도·참고 녹음 포함')
    except (OSError, ValueError, TypeError) as exc:
        st.warning(str(exc))
    st.markdown('1. [캐글 노트북 만들기](https://www.kaggle.com/code)에서 **Import Notebook**으로 위 노트북을 올립니다.\n'
                '2. **GPU T4 ×2**, **Internet ON**을 선택하고, **Add Input → Upload**로 대본 ZIP을 **비공개**로 추가합니다.\n'
                '3. **Run All**로 실행합니다. 완료 후 4번 결과에서 **full_audio.mp3** 또는 **complete_audio.zip**을 받습니다.')
    st.caption('캐글 작업은 캐글 화면에서 시작하고 확인합니다. 첫 설치·모델 준비 시간이 있으며 실제 처리 속도는 대사 길이와 GPU에 따라 달라집니다. 코랩 미리듣기와 기존 연결은 계속 사용할 수 있습니다.')
    return True
