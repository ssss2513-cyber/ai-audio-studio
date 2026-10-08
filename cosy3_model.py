"""CosyVoice 3 reference bank and official FP32 inference, used only in Colab."""
from collections import OrderedDict
from contextlib import nullcontext
import hashlib
import math
import os
from pathlib import Path
import re
import threading
import unicodedata

from cosy3_voicebank_catalog import VOICEBANK, VOICE_REPO, VOICE_REVISION

PREFIX = 'You are a helpful assistant.'
END = '<|endofprompt|>'


def spoken(value, limit=12000):
    value = unicodedata.normalize('NFC', str(value)).strip()
    value = re.sub('[\u200b\u200c\u200d\ufeff]', '', value)
    if not value or len(value) > limit or not any(c.isalnum() for c in value):
        raise ValueError('대사는 비어 있지 않은 12,000자 이하의 문장이어야 합니다.')
    if '<|' in value or '|>' in value:
        raise ValueError('대사에는 모델 제어 기호를 넣을 수 없습니다.')
    return value


def split_text(text, tokenizer):
    """Keep Korean text intact; bound tokens without the English normalizer."""
    def count(value):
        return len(tokenizer.encode(value, allowed_special='all'))
    result, current = [], ''
    for word in text.split():
        if current and count(current + ' ' + word) > 90:
            result.append(current)
            current = ''
        while count(word) > 90:
            low, high = 1, len(word)
            while low < high:
                middle = (low + high + 1) // 2
                if count(word[:middle]) <= 90:
                    low = middle
                else:
                    high = middle - 1
            result.append(word[:low])
            word = word[low:]
        current = (current + ' ' + word).strip()
    if current:
        result.append(current)
    if len(result) > 1 and count(result[-1]) < 20 and count(result[-2] + ' ' + result[-1]) <= 110:
        result[-2:] = [result[-2] + ' ' + result[-1]]
    return result


class Cosy3Model:
    def __init__(self, model_dir, root):
        import torch
        from cosyvoice.cli.cosyvoice import CosyVoice3
        if not torch.cuda.is_available():
            raise RuntimeError('CosyVoice 3는 이 코랩의 GPU가 필요합니다. 런타임 유형을 T4 GPU로 바꿔주세요.')
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        self.root = Path(root)
        self.model = CosyVoice3(model_dir=str(model_dir), load_trt=False, load_vllm=False, fp16=False)
        self.sample_rate = self.model.sample_rate
        self.reference_lock = threading.RLock()
        self.cache = OrderedDict()
        self.gpu = torch.cuda.get_device_name(0)
        self.kaggle_parallel = os.environ.get('VOICE_STUDIO_KAGGLE_QUEUE') == '3'
        if self.kaggle_parallel:
            # Same non-streaming tts/token2wav path as the pinned upstream
            # CosyVoice3Model (inherits CosyVoice2Model.tts). Keep the LLM's
            # caches per UUID and protect the shared flow/vocoder until CUDA
            # completes. Each caller owns its own CUDA stream below.
            from colab_server import install_offline_cache_reuse
            if (type(self.model.model).__name__ != 'CosyVoice3Model'
                    or self.model.model.fp16 or hasattr(self.model.model.llm, 'vllm')):
                raise RuntimeError('캐글 병렬 생성은 고정된 CosyVoice 3 FP32 모델이 필요합니다.')
            install_offline_cache_reuse(self.model)
            self.model.model.llm_context = nullcontext()
            torch.cuda.synchronize()

    def reference(self, voice):
        from huggingface_hub import hf_hub_download
        if voice not in VOICEBANK:
            raise ValueError('CosyVoice 3 목소리를 다시 선택해주세요.')
        return hf_hub_download(VOICE_REPO, filename=VOICEBANK[voice]['filename'],
                               revision=VOICE_REVISION, cache_dir=str(self.root / 'voice_cache'))

    def conditioning(self, path, transcript):
        import soundfile as sf
        import torch
        digest = hashlib.sha256(Path(path).read_bytes() + transcript.encode()).hexdigest()
        with self.reference_lock, torch.inference_mode():
            if digest in self.cache:
                self.cache.move_to_end(digest)
                return self.cache[digest], True
            audio, rate = sf.read(path, dtype='float32', always_2d=True)
            import numpy as np
            duration = len(audio) / rate
            if not 3 <= duration <= 30 or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 0.001:
                raise ValueError('참고 음성은 잡음·배경음 없이 말한 3~30초 녹음이어야 합니다.')
            # Upstream performs its own resampling. No pitch/rate conversion.
            prepared = self.model.frontend.frontend_zero_shot(
                '', PREFIX + END + transcript, str(path), self.sample_rate, '')
            prepared.pop('text', None)
            prepared.pop('text_len', None)
            prepared = {key: value.detach().cpu() if torch.is_tensor(value) else value
                        for key, value in prepared.items()}
            self.cache[digest] = prepared
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
            return prepared, False

    def generate(self, text, voice, *, style='', speed=1.0, ref_path='', prompt_text='', progress=None):
        if not self.kaggle_parallel:
            return self._generate(text, voice, style=style, speed=speed, ref_path=ref_path,
                                  prompt_text=prompt_text, progress=progress)
        import torch
        stream = torch.cuda.Stream(device=0)
        stream.wait_stream(torch.cuda.default_stream(0))
        with torch.cuda.stream(stream), torch.inference_mode():
            try:
                return self._generate(text, voice, style=style, speed=speed, ref_path=ref_path,
                                      prompt_text=prompt_text, progress=progress)
            finally:
                stream.synchronize()

    def _generate(self, text, voice, *, style='', speed=1.0, ref_path='', prompt_text='', progress=None):
        import numpy as np
        import torch
        text = spoken(text)
        if not math.isfinite(speed) or not 0.8 <= speed <= 1.2:
            raise ValueError('읽기 속도는 0.8~1.2 범위에서 선택해주세요.')
        style = str(style).strip()
        if len(style) > 1200 or '<|' in style or '|>' in style:
            raise ValueError('음성 스타일 지시가 올바르지 않습니다.')
        if voice == 'custom':
            prompt_text = spoken(prompt_text, 2000)
            if not ref_path:
                raise ValueError('한국어 참고 음성을 등록해주세요.')
            reference = ref_path
        else:
            reference = self.reference(voice)
            prompt_text = ''  # cross-language: never pair an invented transcript.
        prepared, cached = self.conditioning(reference, prompt_text)
        parts = split_text(text, self.model.frontend.tokenizer)
        outputs = []
        with torch.inference_mode():
            for number, part in enumerate(parts, 1):
                if progress:
                    progress(number - 1, len(parts), cached)
                inputs = {key: value.to(self.model.frontend.device) if torch.is_tensor(value) else value
                          for key, value in prepared.items()}
                if style:
                    instruction = PREFIX + ' Speak Korean. Preserve the reference speaker identity. ' + style + END
                    inputs['prompt_text'], inputs['prompt_text_len'] = self.model.frontend._extract_text_token(instruction)
                    inputs.pop('llm_prompt_speech_token', None)
                    inputs.pop('llm_prompt_speech_token_len', None)
                    spoken_part = part
                elif voice != 'custom':
                    # Official CosyVoice 3 cross-language prefix belongs to text.
                    for key in ('prompt_text', 'prompt_text_len', 'llm_prompt_speech_token', 'llm_prompt_speech_token_len'):
                        inputs.pop(key, None)
                    spoken_part = PREFIX + END + part
                else:
                    spoken_part = part
                inputs['text'], inputs['text_len'] = self.model.frontend._extract_text_token(spoken_part)
                # Model weights, FP32, flow steps and sampling remain upstream defaults.
                chunks = [row['tts_speech'].detach().float().cpu().reshape(-1).numpy()
                          for row in self.model.model.tts(**inputs, stream=False, speed=speed)]
                if not chunks:
                    raise RuntimeError('모델이 음성을 반환하지 않았습니다.')
                samples = np.concatenate(chunks)
                if not len(samples) or not np.isfinite(samples).all() or np.max(np.abs(samples)) < 0.00001:
                    raise RuntimeError('정상적인 음성이 생성되지 않았습니다. 자동 재생성하지 않았습니다.')
                outputs.append(samples)
                if progress:
                    progress(number, len(parts), cached)
        return np.concatenate(outputs), self.sample_rate
