import asyncio
import ast
from contextlib import nullcontext
import io
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import wave

import numpy as np
import pytest

from core.parser import ScriptSegment
from core.tts_engine import TTSEngine, VoiceConfig, VOICE_STYLES
from core.voice_recommendations import recommend_style, preview_text


def wav_bytes(seconds=0.2):
    samples = (np.sin(np.arange(int(24000 * seconds)) * 0.1) * 2000).astype('<i2')
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(samples.tobytes())
    return out.getvalue()


@pytest.mark.parametrize('style', [s for s in VOICE_STYLES if '시니어' in s])
@pytest.mark.parametrize('quota_fallback', [False, True])
def test_gemini_sends_senior_direction_including_fallback(monkeypatch, tmp_path, style, quota_fallback):
    from google import genai
    captured = []
    def generate(**kwargs):
        captured.append(kwargs)
        if quota_fallback and len(captured) <= 2:
            raise RuntimeError('429 RESOURCE_EXHAUSTED')
        part = SimpleNamespace(inline_data=SimpleNamespace(data=wav_bytes()))
        candidate = SimpleNamespace(finish_reason='STOP', content=SimpleNamespace(parts=[part]))
        return SimpleNamespace(candidates=[candidate])
    monkeypatch.setattr(genai, 'Client', lambda **kw: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    async def no_wait(*args):
        pass
    monkeypatch.setattr(asyncio, 'sleep', no_wait)
    target = tmp_path / 'voice.mp3'
    cfg = VoiceConfig(engine='gemini', voice='Gacrux', style=style, api_key='test-key')
    asyncio.run(TTSEngine.generate_gemini_speech_async('오늘도 편안히 쉬세요.', str(target), cfg, retries=1))
    assert target.stat().st_size > 0
    assert len(captured) == (3 if quota_fallback else 1)
    for request in captured:
        assert VOICE_STYLES[style]['gemini_prompt'] in request['contents']
        assert request['contents'].split('# TRANSCRIPT\n', 1)[1] == '오늘도 편안히 쉬세요.'


def test_cosy_style_and_reference_transcript_remain_separate(monkeypatch, tmp_path):
    from core import cosy_colab_client as client
    reference = tmp_path / 'reference.wav'; reference.write_bytes(wav_bytes(4))
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs['data'])
        return SimpleNamespace(ok=True, content=wav_bytes())
    monkeypatch.setattr(client.requests, 'post', post)
    monkeypatch.setattr(client, 'check_connection', lambda *a, **kw: (True, '', {'capabilities':['style_instruction']}))
    style = '👵 시니어 따뜻한 (70대)'
    cfg = VoiceConfig(engine='cosyvoice', cosyvoice_url='https://test.trycloudflare.com/v1/example',
                      ref_audio_path=str(reference), prompt_text='참조 녹음의 대사입니다.', style=style)
    TTSEngine.generate_cosyvoice_speech('새로 읽을 대사입니다.', str(tmp_path / 'out.wav'), cfg)
    assert captured[0]['prompt_text'] == '참조 녹음의 대사입니다.'
    assert captured[0]['text'] == '새로 읽을 대사입니다.'
    assert captured[0]['style_instruction'] == VOICE_STYLES[style]['gemini_prompt']
    monkeypatch.setattr(client, 'check_connection', lambda *a, **kw: (True, '', {}))
    with pytest.raises(RuntimeError, match='이전 버전'):
        TTSEngine.generate_cosyvoice_speech('대사', str(tmp_path / 'old.wav'), cfg)
    assert len(captured) == 1  # Never spend a GPU job on a silently ignored style.
    cfg.style = '🎤 기본'
    TTSEngine.generate_cosyvoice_speech('대사', str(tmp_path / 'basic.wav'), cfg)
    assert captured[-1]['style_instruction'] == ''


def test_cosy_server_uses_instruction_mode_only_for_selected_styles(monkeypatch, tmp_path):
    import colab_server as server
    from fastapi.testclient import TestClient
    import uvicorn
    calls = []
    class Tensor:
        def detach(self): return self
        def cpu(self): return self
        def numpy(self): return np.ones((1, 2400), dtype=np.float32) * 0.1
    class Model:
        sample_rate = 24000
        def __init__(self, **kw): pass
        def inference_instruct2(self, text, instruction, reference, **kw):
            calls.append(('instruct', text, instruction, reference))
            yield {'tts_speech':Tensor()}
        def inference_zero_shot(self, text, transcript, reference, **kw):
            calls.append(('clone', text, transcript, reference))
            yield {'tts_speech':Tensor()}
    fake_torch = ModuleType('torch')
    fake_torch.cuda = SimpleNamespace(is_available=lambda:True)
    fake_torch.inference_mode = nullcontext
    monkeypatch.setitem(sys.modules, 'torch', fake_torch)
    fake_cosy = ModuleType('cosyvoice.cli.cosyvoice'); fake_cosy.CosyVoice2 = Model
    monkeypatch.setitem(sys.modules, 'cosyvoice.cli.cosyvoice', fake_cosy)
    apps = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kw: apps.append(app))
    monkeypatch.setattr(server, 'ROOT', tmp_path / 'cosy')
    monkeypatch.setenv('COSY_ACCESS_TOKEN', 'test-token')
    server.serve(54321)
    client = TestClient(apps[0])
    assert 'style_instruction' in client.get('/v1/test-token/health').json()['capabilities']
    data = {'text':'새 대사입니다.', 'prompt_text':'참조 대사입니다.', 'speed':'1.0'}
    for style in ['', 'Speak warmly as an elder.']:
        response = client.post('/v1/test-token/synthesize', data={**data, 'style_instruction':style},
                               files={'reference':('ref.wav',wav_bytes(4),'audio/wav')})
        assert response.status_code == 200, response.text
    assert calls[0][:3] == ('clone','새 대사입니다.','참조 대사입니다.')
    assert calls[1][0] == 'instruct' and calls[1][2].endswith('<|endofprompt|>')
    assert calls[1][2].count('<|endofprompt|>') == 1
    assert '참조 대사입니다.' not in calls[1][2]


def test_speaker_roles_do_not_leak_between_speakers():
    segments = [ScriptSegment(1,'아이','할머니, 여기로 오세요.'),
                ScriptSegment(2,'할머니','이 할미가 너를 기다렸단다.'),
                ScriptSegment(3,'사또','네 이놈, 감히 속이려 드느냐!')]
    assert recommend_style('아이',segments).style == '😊 밝고 활기차게'
    assert recommend_style('할머니',segments).style == '👵 시니어 따뜻한 (70대)'
    assert recommend_style('사또',segments).style == '😠 분노/격양'
    assert recommend_style('새 인물',segments).style == '🎤 기본'
    assert recommend_style('연희',segments,'65세, 또렷하고 안정적인 어르신').style == '📰 시니어 안정적인 (65세)'
    assert recommend_style('복길',segments,'60대 중후한 남성').style == '👴 시니어 중후한 (60대)'
    assert recommend_style('복길',segments,'80대, 지혜로운 노인').style == '🧙 시니어 지혜로운 (70~80대)'
    assert recommend_style('연희',segments,'70대, 그리움이 많은 노년 여성').style == '🎭 시니어 감성적인 (70대 이상)'
    assert preview_text('아이',segments) == '할머니, 여기로 오세요.'


def test_bundled_notebook_contains_current_server():
    root = Path(__file__).resolve().parents[1]
    notebook = json.loads((root/'CosyVoice_Colab_API.ipynb').read_text())
    for cell in notebook['cells']:
        if cell['cell_type'] == 'code':
            ast.parse(''.join(cell['source']))
    source = ''.join(notebook['cells'][1]['source'])
    assign = next(n for n in ast.parse(source).body if isinstance(n,ast.Assign)
                  and any(isinstance(t,ast.Name) and t.id=='server_source' for t in n.targets))
    assert ast.literal_eval(assign.value) == (root/'colab_server.py').read_text()
