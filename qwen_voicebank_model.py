"""One-GPU VoiceDesign -> Base lifecycle, with durable reference audio reuse."""
import hashlib
import json
from pathlib import Path

from qwen_voicebank_catalog import BASE_MODEL, BANK_REVISION, DESIGN_MODEL, REFERENCE_TEXT, VOICEBANK


class VoiceBankModel:
    def __init__(self, root, dtype):
        self.root = Path(root) / 'voices' / BANK_REVISION
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.dtype = dtype
        self.model = None
        self.model_id = None
        self.prompts = {}

    def reference_path(self, voice):
        return self.root / (voice + '.wav')

    def _identity(self, voice):
        return hashlib.sha256(json.dumps([BANK_REVISION, REFERENCE_TEXT, VOICEBANK[voice]],
                                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def has_reference(self, voice):
        if voice not in VOICEBANK:
            return False
        try:
            import soundfile as sf
            path = self.reference_path(voice)
            metadata = json.loads(path.with_suffix('.json').read_text())
            info = sf.info(str(path))
            return metadata.get('identity') == self._identity(voice) and info.frames > 0 and 2 <= info.duration <= 30
        except (OSError, ValueError, RuntimeError):
            return False

    def _load(self, model_id):
        if self.model_id == model_id and self.model is not None:
            return
        import gc
        import torch
        from qwen_tts import Qwen3TTSModel
        # Never retain VoiceDesign and Base on the same GPU at the same time.
        self.prompts.clear()
        self.model = None
        self.model_id = None
        gc.collect()
        torch.cuda.empty_cache()
        print('▶ 모델 준비: ' + model_id, flush=True)
        self.model = Qwen3TTSModel.from_pretrained(model_id, device_map='cuda:0',
                         dtype=self.dtype, attn_implementation='sdpa')
        self.model.model.eval()
        self.model_id = model_id

    def prepare(self, voices, progress, cancelled=lambda: False):
        import numpy as np
        import soundfile as sf
        import torch
        from concurrent.futures import CancelledError
        voices = list(dict.fromkeys(voices))
        missing = [voice for voice in voices if not self.has_reference(voice)]
        if missing:
            progress('새 목소리 모델 다운로드·준비', len(voices) - len(missing), len(voices))
            self._load(DESIGN_MODEL)
            for position, voice in enumerate(missing):
                if cancelled():
                    raise CancelledError()
                progress('첫 목소리 준비 · ' + VOICEBANK[voice]['name'], len(voices) - len(missing) + position, len(voices))
                # A fixed seed helps reproduce the profile on a new runtime.
                # The saved WAV, rather than a fresh design, anchors later lines.
                with torch.random.fork_rng(devices=[0]), torch.inference_mode():
                    torch.manual_seed(VOICEBANK[voice]['seed'])
                    wavs, rate = self.model.generate_voice_design(text=REFERENCE_TEXT, language='Korean',
                        instruct=VOICEBANK[voice]['instruct'], non_streaming_mode=True)
                if len(wavs) != 1 or not np.isfinite(wavs[0]).all() or not 2 <= len(wavs[0]) / rate <= 30:
                    raise RuntimeError('참조 음성이 정상 길이로 생성되지 않았습니다. 자동으로 반복 생성하지 않습니다.')
                target = self.reference_path(voice)
                temporary = target.with_suffix('.part.wav')
                sf.write(str(temporary), wavs[0], rate, subtype='PCM_16')
                temporary.replace(target)
                metadata = target.with_suffix('.json')
                temp_meta = metadata.with_suffix('.tmp')
                temp_meta.write_text(json.dumps({'identity': self._identity(voice)}, ensure_ascii=False))
                temp_meta.replace(metadata)
                del wavs
        if cancelled():
            raise CancelledError()
        progress('대사 생성용 Base 모델 준비', len(voices), len(voices))
        self._load(BASE_MODEL)
        for voice in voices:
            if cancelled():
                raise CancelledError()
            self._prompt(voice)
        progress('선택한 목소리 준비 완료', len(voices), len(voices))

    def _prompt(self, voice):
        if voice not in self.prompts:
            if not self.has_reference(voice):
                raise RuntimeError('먼저 선택한 기본 목소리 준비를 완료해주세요.')
            self.prompts[voice] = self.model.create_voice_clone_prompt(
                ref_audio=str(self.reference_path(voice)), ref_text=REFERENCE_TEXT,
                x_vector_only_mode=False)[0]
        return self.prompts[voice]

    def generate_custom_voice(self, *, text, language, speaker, instruct, non_streaming_mode):
        self._load(BASE_MODEL)
        prompts = [self._prompt(voice) for voice in speaker]
        # Base does not accept CustomVoice style instructions. Keep the designed
        # reference's tone; never pretend a style selector controls this model.
        return self.model.generate_voice_clone(text=text, language=language,
                    voice_clone_prompt=prompts, non_streaming_mode=non_streaming_mode)
