# -*- coding: utf-8 -*-
"""Self-contained CosyVoice 2 Colab runner. No edits to upstream model code.

--setup: install an isolated Python 3.10 environment and start the local API.
--tunnel: optionally expose the ready API to AI Voice Studio.
--serve: internal worker, launched with the isolated Python interpreter.
"""
import argparse
import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager, nullcontext
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import queue
import secrets
import signal
import shutil
import socket
import subprocess
import struct
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.request

SOURCE_REVISION = '074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc'
COSY_MODEL_REVISION = 'eec1ae6c79877dbd9379285cf8789c9e0879293d'

# Retain the existing installation/cache path so an interrupted install can resume.
ENGINE = 'cosyvoice'
LABEL = 'CosyVoice 2'
ROOT = Path('/content/ai_voice_dual_v1/cosyvoice')
SOURCE = ROOT / 'CosyVoice'
PYTHON = ROOT / 'venv/bin/python'
MODEL = SOURCE / 'pretrained_models/CosyVoice2-0.5B'
STATE = ROOT / 'state.json'
SERVICE = 'ai-voice-studio-cosyvoice'
SERVER_VERSION = '2.9.11'
BATCH_CAPABILITY = 'ordered_batch_stream_v2910'
PARALLEL_CAPABILITY = 'adaptive_cuda_parallel_v2911'
LOSSLESS_TRANSPORT_CAPABILITY = 'lossless_transport_v299'
GENERATION_CAPABILITY = 'validated_generation_v293'
REFERENCE_CACHE_CAPABILITY = 'reference_cache_v294'
REFERENCE_TRANSPORT_CAPABILITY = 'reference_transport_v295'
DURATION_GUARD_CAPABILITY = 'reference_duration_guard_v296'
PERFORMANCE_CAPABILITY = 'fp32_performance_v297'
THROUGHPUT_CAPABILITY = 'fp32_throughput_v298'
UPSTREAM_COMMON_BLOB = '3f235a62e0455abbbf028635978b800e8be951af'
MODEL_REVISION = COSY_MODEL_REVISION


def run(args, label):
    print('\n▶ ' + label, flush=True)
    with (ROOT / 'setup.log').open('a', encoding='utf-8') as log:
        log.write('\n▶ ' + label + '\n')
        log.flush()
        proc = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            for line in proc.stdout:
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
            code = proc.wait()
        except BaseException:
            proc.terminate()
            raise
    if code:
        raise RuntimeError(f'{label} 실패 (종료 코드 {code}). 바로 위 오류를 확인해주세요.')


def read_state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def health(base, require_current=False):
    with urllib.request.urlopen(base + '/health', timeout=5) as response:
        data = json.load(response)
    return (data.get('service') == SERVICE and data.get('ready') is True
            and (not require_current or (data.get('server_version') == SERVER_VERSION
                 and GENERATION_CAPABILITY in data.get('capabilities', [])
                 and REFERENCE_CACHE_CAPABILITY in data.get('capabilities', [])
                 and PERFORMANCE_CAPABILITY in data.get('capabilities', [])
                 and THROUGHPUT_CAPABILITY in data.get('capabilities', [])
                 and data.get('acceleration', {}).get('requested') ==
                 ('off' if os.environ.get('COSY_ACCELERATION', 'fp32').lower() == 'off' else 'fp32'))))


def stop_previous_worker(state):
    """Replace only this runner's recorded, idle worker; retain model downloads."""
    import fcntl
    pid = state.get('pid')
    if not isinstance(pid, int) or pid <= 1:
        raise RuntimeError('기존 서버의 실행 정보를 확인할 수 없습니다. 런타임을 다시 시작한 뒤 1번을 실행해주세요.')
    proc_dir = Path('/proc') / str(pid)
    try:
        already_exited = (proc_dir / 'stat').read_text().split(') ', 1)[1].startswith('Z')
    except (OSError, IndexError):
        already_exited = not proc_dir.exists()
    if already_exited:
        STATE.unlink(missing_ok=True)
        return
    try:
        args = (proc_dir / 'cmdline').read_bytes().split(b'\0')
        args = [arg.decode(errors='replace') for arg in args if arg]
        script_index = 2 if len(args) > 1 and args[1] == '-u' else 1
        owned = (len(args) > script_index and Path(args[0]).absolute() == PYTHON.absolute()
                 and Path(args[script_index]).absolute() == Path(__file__).absolute()
                 and '--serve' in args and '--port' in args
                 and args[args.index('--port') + 1] == str(state.get('port')))
    except (OSError, IndexError):
        owned = False
    if not owned:
        raise RuntimeError('기존 서버를 안전하게 교체할 수 없습니다. 런타임을 다시 시작한 뒤 1번을 실행해주세요.')
    with (ROOT.parent / 'gpu.lock').open('a+') as gpu_lock:
        try:
            fcntl.flock(gpu_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('음성을 생성 중입니다. 완료된 뒤 1번을 실행하면 새 서버로 바뀝니다.') from None
        print('이전 CosyVoice 서버를 수정 버전으로 교체합니다. 설치와 모델 파일은 유지합니다.', flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            STATE.unlink(missing_ok=True)
            return
        for _ in range(50):
            try:
                # An exited child can remain as a zombie until its notebook reaps it.
                exited = (proc_dir / 'stat').read_text().split(') ', 1)[1].startswith('Z')
            except (OSError, IndexError):
                exited = True
            if exited:
                STATE.unlink(missing_ok=True)
                return
            time.sleep(0.2)
    raise RuntimeError('이전 서버가 아직 종료 중입니다. 잠시 뒤 1번을 다시 실행해주세요.')


def normalize_speech_text(value, label):
    value = unicodedata.normalize('NFC', value)
    value = re.sub('[\u200b\u200c\u200d\ufeff]', '', value)
    value = re.sub(r'\s+', ' ', value).strip()
    if not any(char.isalnum() for char in value):
        raise ValueError(label + '에 실제로 읽을 문장을 입력해주세요.')
    if '<|' in value or '|>' in value:
        raise ValueError(label + '에는 모델 제어 기호 없이 실제 대사만 입력해주세요.')
    return value


def speech_units(text):
    """A deliberately loose duration estimate, not speech recognition."""
    return sum(1 for char in text if char.isalnum() and not char.isascii()) + sum(
        max(1, len(word) / 3) for word in re.findall(r'[A-Za-z]+', text)) + sum(
        2 * len(number) for number in re.findall(r'[0-9]+', text))


def duration_limits(text, reference_units, reference_seconds):
    """Loose plausibility bounds, with reference pace and number pronunciation.

    Duration alone cannot verify the spoken words. Keep the bound finite even
    with a slow reference, without treating all speakers as equally paced.
    """
    units = speech_units(text)
    reference_rate = reference_units / max(reference_seconds, 0.8)
    reference_rate = min(10.0, max(1.5, reference_rate))
    pauses = min(4.0, len(re.findall(r'[,.!?，。！？]', text)) * 0.3)
    upper = min(90.0, max(10.0, units * 0.8 + 4.0,
                          units / reference_rate * 1.8 + 4.0 + pauses))
    return max(0.15, units / 30.0), upper


class GeneratedAudioValidationError(RuntimeError):
    pass


def synthesis_chunks(text, prompt_text, tokenizer):
    """Pack short sentences together, with a token budget for Korean inputs."""
    def tokens(value):
        return len(tokenizer.encode(value, allowed_special='all'))

    # The upstream frontend uses 60-80 tokens. Keep Korean out of its English
    # normalizer, but use a comparable token budget instead of 180 characters.
    chunks, current = [], ''
    for word in text.split():
        candidate = (current + ' ' + word).strip()
        if current and tokens(candidate) > 80:
            chunks.append(current)
            current = ''
        # A script without spaces must also respect the token budget.
        while tokens(word) > 80:
            low, high = 1, len(word)
            while low < high:
                middle = (low + high + 1) // 2
                if tokens(word[:middle]) <= 80:
                    low = middle
                else:
                    high = middle - 1
            chunks.append(word[:low])
            word = word[low:]
        current = (current + ' ' + word).strip()
        if (re.search(r'[.!?。！？]["”\']?$', current) and tokens(current) >= 60
                and len(current) >= len(prompt_text) / 2):
            chunks.append(current)
            current = ''
    if current:
        chunks.append(current)
    # Do not synthesize a tiny final fragment on its own if it fits the previous
    # chunk with a small, bounded extension to the normal budget.
    if len(chunks) > 1 and (tokens(chunks[-1]) < 25 or len(chunks[-1]) < len(prompt_text) / 2):
        joined = chunks[-2] + ' ' + chunks[-1]
        if tokens(joined) <= 100:
            chunks[-2:] = [joined]
    return chunks


def install_same_rule_sampling(llm):
    """Keep the pinned RAS distribution, stable sort and FP32 prefix sums.

    The upstream nucleus sampler synchronizes a CUDA scalar for every prefix
    comparison, then copies each shortlisted value back through the CPU. Copy
    the at-most-top_k prefix once; perform the *same ordered additions* in its
    original dtype on CPU and sample the original GPU probability vector.
    Never replace this with cumsum/topk, change a cutoff, or sample on CPU.
    """
    import functools
    import torch
    from cosyvoice.utils import common
    contents = Path(common.__file__).read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(contents)).encode() + b'\0' + contents).hexdigest()
    original = llm.sampling
    function = original.func if isinstance(original, functools.partial) else original
    if (blob != UPSTREAM_COMMON_BLOB or function is not common.ras_sampling
            or (isinstance(original, functools.partial) and original.args)):
        return False

    def same_rule_ras(weighted_scores, decoded_tokens, sampling,
                      top_p=0.8, top_k=25, win_size=10, tau_r=0.1):
        # Unknown/unsupported custom settings use the original function.
        if weighted_scores.dtype != torch.float32 or not isinstance(top_k, int) or top_k < 1:
            return common.ras_sampling(weighted_scores, decoded_tokens, sampling,
                                       top_p=top_p, top_k=top_k, win_size=win_size, tau_r=tau_r)
        sorted_value, sorted_idx = weighted_scores.softmax(dim=0).sort(descending=True, stable=True)
        prefix = sorted_value[:min(top_k, sorted_value.numel())].detach().cpu()
        cumulative = prefix.new_zeros(())
        count = 0
        for probability in prefix:
            if not bool(cumulative < top_p):
                break
            cumulative += probability
            count += 1
        if count == 0:
            return common.ras_sampling(weighted_scores, decoded_tokens, sampling,
                                       top_p=top_p, top_k=top_k, win_size=win_size, tau_r=tau_r)
        probabilities = sorted_value[:count].contiguous()
        indices = sorted_idx[:count]
        top_id = indices[probabilities.multinomial(1, replacement=True)].item()
        # The pinned decoder already stores Python integer IDs. Count the same
        # window on CPU, without another host/device transfer and scalar wait.
        window = decoded_tokens[-win_size:]
        if all(type(token) is int for token in window):
            repetitions = sum(token == top_id for token in window)
        else:
            repetitions = (torch.tensor(window).to(weighted_scores.device) == top_id).sum().item()
        if repetitions >= win_size * tau_r:
            weighted_scores[top_id] = -float('inf')
            top_id = common.random_sampling(weighted_scores, decoded_tokens, sampling)
        return top_id

    llm.sampling = functools.partial(same_rule_ras, **(original.keywords or {})) if isinstance(original, functools.partial) else same_rule_ras
    return True


def install_offline_cache_reuse(model):
    """Same complete-utterance path, with PyTorch's GPU allocator retained.

    The upstream non-streaming path starts a thread and immediately joins it.
    Execute that same job inline, propagate its errors, and release per-request
    tensors in finally. A CUDA synchronization is retained; empty_cache is not
    called for every successful chunk. Streaming/voice conversion use upstream.
    """
    import torch
    import uuid
    from types import MethodType
    original = model.model.tts

    # Mirror upstream defaults: instruct2 intentionally omits the LLM prompt
    # speech token, while cross-lingual mode also omits prompt_text.
    def offline_tts(self, text=torch.zeros(1, 0, dtype=torch.int32),
                    flow_embedding=torch.zeros(0, 192), llm_embedding=torch.zeros(0, 192),
                    prompt_text=torch.zeros(1, 0, dtype=torch.int32),
                    llm_prompt_speech_token=torch.zeros(1, 0, dtype=torch.int32),
                    flow_prompt_speech_token=torch.zeros(1, 0, dtype=torch.int32),
                    prompt_speech_feat=torch.zeros(1, 0, 80),
                    source_speech_token=torch.zeros(1, 0, dtype=torch.int32),
                    stream=False, speed=1.0, **kwargs):
        if stream or source_speech_token.shape[1] != 0:
            yield from original(text=text, flow_embedding=flow_embedding, llm_embedding=llm_embedding,
                                prompt_text=prompt_text, llm_prompt_speech_token=llm_prompt_speech_token,
                                flow_prompt_speech_token=flow_prompt_speech_token,
                                prompt_speech_feat=prompt_speech_feat, source_speech_token=source_speech_token,
                                stream=stream, speed=speed, **kwargs)
            return
        request = str(uuid.uuid4())
        with self.lock:
            self.tts_speech_token_dict[request], self.llm_end_dict[request] = [], False
            self.hift_cache_dict[request] = None
        try:
            self.llm_job(text, prompt_text, llm_prompt_speech_token, llm_embedding, request)
            tokens = torch.tensor(self.tts_speech_token_dict[request]).unsqueeze(dim=0)
            speech = self.token2wav(token=tokens, prompt_token=flow_prompt_speech_token,
                                    prompt_feat=prompt_speech_feat, embedding=flow_embedding,
                                    token_offset=0, uuid=request, finalize=True, speed=speed)
            yield {'tts_speech': speech.cpu()}
        finally:
            with self.lock:
                self.tts_speech_token_dict.pop(request, None)
                self.llm_end_dict.pop(request, None)
                self.hift_cache_dict.pop(request, None)
            if torch.cuda.is_available():
                torch.cuda.current_stream().synchronize()

    model.model.tts = MethodType(offline_tts, model.model)


def configure_fp32_acceleration(model):
    """Inference-only adapters; retain upstream files, weights and sampling.

    Qwen2Encoder.forward_one_step uses only the decoder's hidden state and KV
    cache. Transformers 4.51.3 also calculates full text-vocabulary logits in
    Qwen2ForCausalLM.forward; CosyVoice discards those and uses llm_decoder for
    speech tokens. Call the very same decoder with the same inputs directly.
    The optional flow JIT uses the official export_jit.py FP32 compilation path.
    No vLLM export (which casts to bfloat16), reduced precision or TensorRT
    dependency replacement is performed by an existing-installation upgrade.
    """
    import torch
    import transformers
    from types import MethodType
    result = {'precision': 'fp32', 'llm': 'standard', 'flow': 'pytorch',
              'sampling': 'standard', 'memory_cache': False, 'notes': [], 'requested': 'fp32'}
    if os.environ.get('COSY_ACCELERATION', 'fp32').lower() == 'off':
        result['requested'] = 'off'
        result['label'] = 'FP32 기본 계산'
        return result
    try:
        source_revision = subprocess.check_output(
            ['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True, timeout=5,
            stderr=subprocess.DEVNULL).strip()
        encoder = model.model.llm.llm
        supported = (source_revision == SOURCE_REVISION
                     and transformers.__version__ == '4.51.3'
                     and type(encoder).__name__ == 'Qwen2Encoder'
                     and type(encoder).__module__ == 'cosyvoice.llm.llm'
                     and type(encoder.model).__name__ == 'Qwen2ForCausalLM'
                     and type(encoder.model.model).__name__ == 'Qwen2Model'
                     and type(model.model).__name__ == 'CosyVoice2Model'
                     and type(model.model.llm).__name__ == 'Qwen2LM'
                     and not hasattr(model.model.llm, 'vllm'))
        if supported:
            def decoder_step(self, xs, masks, cache=None):
                outputs = self.model.model(
                    inputs_embeds=xs, attention_mask=masks[:, -1, :],
                    output_hidden_states=False, return_dict=True,
                    use_cache=True, past_key_values=cache,
                )
                return outputs.last_hidden_state, outputs.past_key_values

            encoder.forward_one_step = MethodType(decoder_step, encoder)
            result['llm'] = 'decoder_only'
            print('FP32 속도 개선: 사용하지 않는 문자 예측 계산을 생략합니다. 음성 토큰 계산·가중치·샘플링은 유지합니다.', flush=True)
            if install_same_rule_sampling(model.model.llm):
                result['sampling'] = 'same_rule_prefix_transfer'
                print('발음 후보 일괄 전송 적용: 후보 확률·정렬·누적 순서·반복 억제 설정을 유지합니다.', flush=True)
            else:
                result['notes'].append('발음 선택 코드가 고정 버전과 달라 기본 선택 방식을 사용합니다.')
            install_offline_cache_reuse(model)
            result['memory_cache'] = True
            print('구간별 GPU 메모리 캐시를 재사용합니다. 모델 계산 단계는 유지합니다.', flush=True)
        else:
            result['notes'].append('지원 모델·라이브러리 조합이 아니어서 기본 발음 계산을 사용합니다.')
    except Exception as exc:
        result['notes'].append('발음 계산 최적화 준비 실패: ' + type(exc).__name__)

    original_encoder = model.model.flow.encoder
    try:
        print('FP32 음향 인코더 가속 준비 중… 첫 준비에 시간이 조금 더 걸릴 수 있습니다.', flush=True)
        scripted = torch.jit.script(original_encoder)
        scripted = torch.jit.freeze(scripted)
        scripted = torch.jit.optimize_for_inference(scripted)
        model.model.flow.encoder = scripted
        result['flow'] = 'jit_fp32'
        print('FP32 음향 인코더 가속 준비 완료.', flush=True)
    except Exception as exc:
        model.model.flow.encoder = original_encoder
        result['notes'].append('음향 인코더 가속 준비 실패: ' + type(exc).__name__)
        print('음향 가속을 준비할 수 없어 기존 FP32 방식으로 계속합니다. ' + type(exc).__name__, flush=True)
    labels = ['FP32']
    if result['llm'] == 'decoder_only':
        labels.append('불필요 계산 생략')
    if result['flow'] == 'jit_fp32':
        labels.append('음향 인코더 가속')
    if result['sampling'] == 'same_rule_prefix_transfer':
        labels.append('발음 선택 대기 단축')
    if result['memory_cache']:
        labels.append('GPU 메모리 재사용')
    result['label'] = ' · '.join(labels)
    for note in result['notes']:
        logging.warning(note)
    return result


class RequestTimer(threading.local):
    """Sampling and decoder timers belong to the calling inference thread."""
    def __init__(self):
        self.values = {'llm_seconds': 0.0, 'sampling_seconds': 0.0}

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        self.values[key] = value


def install_generation_timer(model):
    """Measure LLM and candidate selection wall time without extra CUDA waits.

    The offline adapter runs the LLM job on the request's thread. Candidate
    selection time is included in LLM time, not added. No cross-request totals.
    """
    totals = RequestTimer()
    original_job = model.model.llm_job
    original_sampling = model.model.llm.sampling

    def timed_job(*args, **kwargs):
        started = time.monotonic()
        try:
            return original_job(*args, **kwargs)
        finally:
            totals['llm_seconds'] += time.monotonic() - started

    model.model.llm_job = timed_job
    def timed_sampling(*args, **kwargs):
        started = time.monotonic()
        try:
            return original_sampling(*args, **kwargs)
        finally:
            totals['sampling_seconds'] += time.monotonic() - started
    model.model.llm.sampling = timed_sampling
    return totals


class AutoConcurrency:
    """Estimate a memory budget from real work; never claim a measured speedup.

    The first requested clip is retained, not an extra test generation. Use its
    peak plus headroom and a 2 GiB floor per active utterance. An OOM lowers the
    session ceiling. 32 is the existing bounded batch protocol's hard limit,
    not a promise that any GPU can run 32 copies at once.
    """
    GIB = 1024 ** 3

    def __init__(self, snapshot, reset_peak, recover=lambda: None, enabled=True):
        self.snapshot, self.reset_peak, self.recover = snapshot, reset_peak, recover
        self.calibrated = False
        self.limit = 1
        self.ceiling = 32 if enabled else 1
        self.per_request = 2 * self.GIB
        self.reserve = 2 * self.GIB
        self.baseline = 0
        self.memory_retries = 0
        self.reason = '' if enabled else '현재 모델 조합은 순차 처리로 실행합니다.'

    def begin(self):
        if not self.calibrated:
            self.baseline = self.snapshot()['allocated']
            self.reset_peak()

    def calibrated_after_success(self):
        if not self.calibrated:
            info = self.snapshot()
            observed = max(0, info['peak'] - self.baseline)
            self.per_request = max(self.per_request, int(observed * 1.5 + self.GIB / 2))
            self.calibrated = True
        self.refresh()

    def refresh(self):
        """Only recalculate the total ceiling while no request is in flight."""
        if self.calibrated:
            info = self.snapshot()
            self.reserve = max(2 * self.GIB, int(info['total'] * 0.12))
            usable = info['free'] + max(0, info['reserved'] - info['allocated']) - self.reserve
            self.limit = max(1, min(self.ceiling, int(usable // self.per_request)))
        return self.limit

    def can_add(self, active):
        if active >= self.limit:
            return False
        if active == 0:
            return True
        info = self.snapshot()
        available = info['free'] + max(0, info['reserved'] - info['allocated'])
        return available >= self.reserve + self.per_request

    def memory_failure(self, active_count):
        self.ceiling = max(1, min(self.ceiling, active_count // 2))
        self.limit = min(self.limit, self.ceiling)
        self.reason = '메모리 부족이 발생해 동시 수를 줄였습니다. 음질 설정은 유지합니다.'

    def status(self):
        return dict(mode='auto', limit=self.limit, calibrated=self.calibrated,
                    hard_limit=32, estimated_request_gib=round(self.per_request / self.GIB, 2),
                    reserve_gib=round(self.reserve / self.GIB, 2),
                    memory_retries=self.memory_retries, reason=self.reason)


@contextmanager
def exclusive_gpu_lease():
    """One whole batch owns the GPU relative to GPT and notebook upgrades."""
    import fcntl
    from fastapi import HTTPException
    with (ROOT.parent / 'gpu.lock').open('a+') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(409, '다른 엔진이 음성을 생성 중입니다. 완료 후 실행해주세요.') from None
        yield


def install_request_streams(model):
    """Keep FP32 weights and per-UUID inference caches; separate CUDA streams.

    The pinned offline path is reentrant. Its original llm_context is a single
    reusable CUDA context manager and cannot be entered by multiple threads.
    Running the inline LLM on each request's current stream removes that shared
    context and also keeps the following flow/vocoder ordered on that stream.
    """
    import torch
    revision = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'],
                                       text=True, timeout=5).strip()
    if (revision != SOURCE_REVISION or type(model.model).__name__ != 'CosyVoice2Model' or model.model.fp16
            or hasattr(model.model.llm, 'vllm')):
        raise RuntimeError('동시 생성은 고정된 CosyVoice 2 FP32 모델에서만 사용할 수 있습니다.')
    install_offline_cache_reuse(model)
    model.model.llm_context = nullcontext()
    torch.cuda.synchronize()
    return True


def setup():
    if not Path('/content').is_dir():
        raise RuntimeError('이 파일은 Google Colab에서 실행해주세요.')
    ROOT.mkdir(parents=True, exist_ok=True)
    state = read_state()
    existing_ready = False
    try:
        existing_ready = bool(state.get('base')) and health(state['base'], require_current=False)
    except Exception:
        pass
    if existing_ready:
        if health(state['base'], require_current=True):
            print(f'✅ {LABEL} v{SERVER_VERSION} 준비 완료. 사이트 연결은 4번을 실행하세요.', flush=True)
            return
        stop_previous_worker(state)
    elif isinstance(state.get('pid'), int) and (Path('/proc') / str(state['pid'])).exists():
        stop_previous_worker(state)
    # A healthy earlier worker proves this installation already loaded. A
    # runner upgrade can reuse it without reinstalling packages/models.
    if existing_ready and PYTHON.is_file() and all((MODEL / name).is_file() for name in
            ('cosyvoice2.yaml', 'llm.pt', 'flow.pt', 'hift.pt', 'campplus.onnx', 'speech_tokenizer_v2.onnx')):
        print('기존 설치와 모델을 그대로 사용해 수정된 CosyVoice 서버를 시작합니다.', flush=True)
        start_server()
        return
    if shutil.which('nvidia-smi') is None:
        raise RuntimeError('GPU가 없습니다. 런타임 → 런타임 유형 변경 → T4 GPU를 선택해주세요.')
    run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'], 'GPU 확인')
    run(['apt-get', 'update', '-qq'], '시스템 패키지 목록 갱신')
    run(['apt-get', 'install', '-y', '-qq', 'ffmpeg', 'sox', 'libsox-dev',
         'libsndfile1', 'build-essential', 'git'], '오디오 도구 설치')
    if not PYTHON.is_file():
        run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', 'uv'], 'Python 환경 도구 설치')
        run([sys.executable, '-m', 'uv', 'python', 'install', '3.10'], '별도 Python 3.10 설치')
        run([sys.executable, '-m', 'uv', 'venv', '--python', '3.10', '--seed', ROOT / 'venv'], '독립 실행 환경 생성')
    if not (SOURCE / '.git').is_dir():
        run(['git', 'clone', '--filter=blob:none', 'https://github.com/FunAudioLLM/CosyVoice.git', SOURCE], '공식 CosyVoice 소스 다운로드')
    run(['git', '-C', SOURCE, 'checkout', '--detach', SOURCE_REVISION], '확인한 소스 버전 선택')
    run(['git', '-C', SOURCE, 'submodule', 'update', '--init', '--recursive'], 'Matcha-TTS 소스 준비')
    # Keep upstream versions except the documented inference-only changes below.
    requirements = []
    for line in (SOURCE / 'requirements.txt').read_text().splitlines():
        if line.startswith(('deepspeed', 'tensorrt', 'gradio', 'fastapi-cli')):
            continue  # JIT uses existing Torch; no additional accelerator install.
        if line.startswith('diffusers=='):
            line = 'diffusers==0.32.2'  # compatible with modern huggingface_hub (no cached_download import)
        requirements.append(line)
    requirements += ['huggingface-hub==0.30.2', 'python-multipart>=0.0.18,<0.1', 'setuptools<81']
    req_file = ROOT / 'inference-requirements.txt'
    req_file.write_text('\n'.join(requirements) + '\n')
    # Whisper's pinned source distribution imports pkg_resources while building.
    # The runtime setuptools pin alone does not apply inside pip's isolated build.
    build_constraints = ROOT / 'build-constraints.txt'
    build_constraints.write_text('setuptools<81\n')
    stamp = hashlib.sha256(req_file.read_bytes() + build_constraints.read_bytes()).hexdigest()
    marker = ROOT / 'dependencies.ok'
    if not marker.exists() or marker.read_text() != stamp:
        run([PYTHON, '-m', 'pip', 'install', 'pip>=25.3,<26', 'setuptools<81', 'wheel', 'Cython<4'], '설치 도구 준비')
        # Install the matching CUDA pair first; do not touch Colab's own Torch.
        run([PYTHON, '-m', 'pip', 'install', 'torch==2.3.1', 'torchaudio==2.3.1',
             '--index-url', 'https://download.pytorch.org/whl/cu121'], 'GPU용 Torch 및 오디오 패키지 설치')
        run([PYTHON, '-m', 'pip', 'install', '-r', req_file,
             '--build-constraint', build_constraints], 'CosyVoice 의존성 설치')
        run([PYTHON, '-m', 'pip', 'check'], '의존성 충돌 확인')
        marker.write_text(stamp)
    download = (
        'from huggingface_hub import snapshot_download; '
        f'snapshot_download("FunAudioLLM/CosyVoice2-0.5B", revision={MODEL_REVISION!r}, '
        f'local_dir={str(MODEL)!r}, allow_patterns=["cosyvoice2.yaml", "llm.pt", "flow.pt", '
        '"hift.pt", "campplus.onnx", "speech_tokenizer_v2.onnx", "CosyVoice-BlankEN/*"])'
    )
    run([PYTHON, '-c', download], 'CosyVoice 2 모델 준비 (첫 실행 시 다운로드)')
    missing = [name for name in ('cosyvoice2.yaml', 'llm.pt', 'flow.pt', 'hift.pt',
                                'campplus.onnx', 'speech_tokenizer_v2.onnx') if not (MODEL / name).is_file()]
    if missing:
        raise RuntimeError('모델 다운로드가 완전하지 않습니다: ' + ', '.join(missing))
    start_server()


def start_server():
    """Start the FP32 model with inference adapters from an existing installation."""
    env = os.environ.copy()
    # The isolated worker renders no notebook plots and may not have matplotlib_inline installed.
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONPATH'] = os.pathsep.join([str(SOURCE), str(SOURCE / 'third_party/Matcha-TTS')])
    libs = [str(p) for p in (ROOT / 'venv/lib/python3.10/site-packages/nvidia').glob('*/lib')]
    env['LD_LIBRARY_PATH'] = os.pathsep.join(libs + [env.get('LD_LIBRARY_PATH', '')])
    env['TOKENIZERS_PARALLELISM'] = 'false'
    env['COSY_ACCESS_TOKEN'] = secrets.token_urlsafe(24)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}/v1/{env["COSY_ACCESS_TOKEN"]}'
    log_path = ROOT / 'server.log'
    with log_path.open('w') as log:
        worker = subprocess.Popen([str(PYTHON), '-u', str(Path(__file__).resolve()), '--serve', '--engine', ENGINE, '--port', str(port)],
                                  cwd=SOURCE, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, start_new_session=True)
    print('\n▶ 모델 준비 기록을 아래에 실시간으로 표시합니다. 최대 10분 후에도 준비되지 않으면 중단합니다.', flush=True)
    ready = False
    started = time.monotonic()
    next_notice = started + 30
    token = env['COSY_ACCESS_TOKEN']
    live_log = log_path.open(encoding='utf-8', errors='replace')

    def show_new_logs():
        output = live_log.read()
        if output:
            print(output.replace(token, '[접속 키 숨김]'), end='' if output.endswith('\n') else '\n', flush=True)

    def error_tail():
        return log_path.read_text(errors='replace')[-7000:].replace(token, '[접속 키 숨김]')

    try:
        while time.monotonic() - started < 600:
            show_new_logs()
            if worker.poll() is not None:
                raise RuntimeError('모델 시작 실패:\n' + error_tail())
            try:
                ready = health(base)
            except Exception:
                pass
            if ready:
                state = {'base': base, 'pid': worker.pid, 'port': port, 'model': LABEL, 'engine': ENGINE}
                STATE.write_text(json.dumps(state))
                STATE.chmod(0o600)
                show_new_logs()
                print(f'✅ {LABEL} v{SERVER_VERSION} 준비 완료. 사이트 연결은 4번을 실행하세요.', flush=True)
                return
            now = time.monotonic()
            if now >= next_notice:
                elapsed = int(now - started)
                print(f'  ⏳ 준비 대기 {elapsed // 60}분 {elapsed % 60:02d}초 / 최대 10분 — 아직 준비 완료가 아닙니다. 위 마지막 단계와 기록을 확인해주세요.', flush=True)
                next_notice = now + 30
            time.sleep(2)
        show_new_logs()
        raise RuntimeError('모델 준비가 10분 안에 완료되지 않아 중단했습니다. 아래 실제 기록을 보내주세요:\n' + error_tail())
    finally:
        live_log.close()
        if not ready and worker.poll() is None:
            worker.terminate()


def install_batch_routes(app, synthesize, authorize, concurrency=None, gpu_lease=nullcontext):
    """Bound GPU work and stream indexed results; drain active work on stop/error."""
    from fastapi import Body, File, Form, HTTPException, UploadFile
    from fastapi.responses import StreamingResponse

    gate = threading.Lock()
    registry_lock = threading.Lock()
    active = {}

    @app.post('/v1/{token}/synthesize')
    def single(token: str, text: str = Form(...), prompt_text: str = Form(''),
               speed: float = Form(1.0), reference: UploadFile | None = File(None),
               style_instruction: str = Form(''), reference_id: str = Form(''),
               audio_format: str = Form('wav')):
        authorize(token)
        if not gate.acquire(blocking=False):
            raise HTTPException(409, '전체 음성을 생성 중입니다. 완료하거나 멈춘 뒤 실행해주세요.')
        try:
            with gpu_lease():
                return synthesize(token, text, prompt_text, speed, reference,
                                  style_instruction, reference_id, audio_format)
        finally:
            gate.release()

    @app.post('/v1/{token}/batch/{job_id}/stop')
    def stop(token: str, job_id: str):
        authorize(token)
        with registry_lock:
            event = active.get(job_id)
            if event is not None:
                event.set()
        return {'stopping': event is not None}

    @app.post('/v1/{token}/synthesize_batch')
    def batch(token: str, payload: dict = Body(...)):
        authorize(token)
        job_id = payload.get('job_id', '')
        items, references = payload.get('items'), payload.get('references')
        if (not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id)
                or not isinstance(items, list) or not 1 <= len(items) <= 32
                or not isinstance(references, dict) or not 1 <= len(references) <= 16):
            raise HTTPException(422, '일괄 생성 요청 형식이 올바르지 않습니다.')
        decoded, indices, total_bytes = {}, set(), 0
        try:
            for key, data in references.items():
                if not isinstance(data, str) or len(data) > 14 * 1024 * 1024:
                    raise ValueError('참조 음성 크기 초과')
                raw = base64.b64decode(data, validate=True)
                total_bytes += len(raw)
                if not raw or len(raw) > 10 * 1024 * 1024 or total_bytes > 32 * 1024 * 1024:
                    raise ValueError('참조 음성 크기 초과')
                decoded[key] = raw
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('대사 형식 오류')
                index = item.get('index')
                if type(index) is not int or index <= 0 or index in indices:
                    raise ValueError('대사 번호가 중복되거나 올바르지 않습니다.')
                indices.add(index)
                if item.get('reference') not in decoded:
                    raise ValueError('참조 음성이 없습니다.')
                if not isinstance(item.get('text'), str) or not 1 <= len(item['text']) <= 2000:
                    raise ValueError('대사는 1~2,000자여야 합니다.')
                if not isinstance(item.get('prompt_text'), str) or not 1 <= len(item['prompt_text']) <= 1000:
                    raise ValueError('참조 대사를 입력해주세요.')
                style = item.get('style_instruction', '')
                if not isinstance(style, str) or len(style) > 800 or '<|' in style or '|>' in style:
                    raise ValueError('스타일 지시문 형식 오류')
                if not 0.5 <= float(item.get('speed', 1.0)) <= 2.0:
                    raise ValueError('말하기 속도 범위 오류')
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if not gate.acquire(blocking=False):
            raise HTTPException(409, '다른 음성을 생성 중입니다. 완료하거나 멈춘 뒤 실행해주세요.')
        cancel, disconnected = threading.Event(), threading.Event()
        events = queue.Queue(maxsize=2)
        with registry_lock:
            active[job_id] = cancel

        def put(header, body=b''):
            blocked_at = time.monotonic()
            while not disconnected.is_set():
                try:
                    events.put((header, body), timeout=0.2)
                    return True
                except queue.Full:
                    # Also free the producer if a connection dies before its
                    # response iterator gets a chance to run its finally block.
                    if time.monotonic() - blocked_at > 90:
                        disconnected.set()
                        cancel.set()
            return False

        def produce():
            count, position = 0, 0
            failures, memory_failed = [], []
            pending = {}
            retry_count = 0

            def runtime():
                return concurrency.status() if concurrency else {'limit': 1, 'calibrated': True}

            def generate(item):
                index = item['index']
                if cancel.is_set() or disconnected.is_set():
                    return None
                if not put({'type': 'started', 'index': index, 'parallel': runtime()}):
                    return None
                if cancel.is_set() or disconnected.is_set():
                    return None
                started = time.monotonic()
                reference = UploadFile(filename='reference.wav', file=io.BytesIO(decoded[item['reference']]))
                try:
                    response = synthesize(token, item['text'], item['prompt_text'], float(item.get('speed', 1.0)),
                                          reference, item.get('style_instruction', ''), '', 'flac')
                    return ({'type': 'audio', 'index': index, 'headers': dict(response.headers),
                             'server_seconds': time.monotonic() - started, 'parallel': runtime(),
                             'memory_retries': retry_count}, bytes(response.body))
                finally:
                    reference.file.close()

            def save_result(result):
                nonlocal count
                if result is not None and put(*result):
                    count += 1

            def record_failure(item, exc, retrying=False):
                detail = getattr(exc, 'detail', str(exc))
                memory = isinstance(detail, dict) and detail.get('code') == 'cuda_memory_limit'
                if memory and concurrency and not retrying:
                    concurrency.memory_failure(max(1, len(pending) + 1))
                    memory_failed.append(item)
                    # Future exceptions otherwise retain every tensor in the
                    # failed model frame and prevent the idle OOM recovery.
                    failure = exc
                    visited = set()
                    while failure is not None and id(failure) not in visited:
                        visited.add(id(failure))
                        failure.__traceback__ = None
                        failure = failure.__cause__ or failure.__context__
                else:
                    failures.append((item['index'], exc))

            try:
                with gpu_lease():
                    if concurrency:
                        concurrency.begin()
                        concurrency.refresh()
                    with ThreadPoolExecutor(max_workers=32 if concurrency else 1,
                                            thread_name_prefix='cosy-inference') as pool:
                        while position < len(items) or pending or memory_failed:
                            stopped = cancel.is_set() or disconnected.is_set()
                            # Do not dispatch new work after any failure. Already
                            # running successes must reach the client before error.
                            while (position < len(items) and not stopped and not failures and not memory_failed
                                   and len(pending) < (concurrency.limit if concurrency else 1)
                                   and (not concurrency or concurrency.can_add(len(pending)))):
                                item = items[position]
                                pending[pool.submit(generate, item)] = item
                                position += 1
                            if pending:
                                ready, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                                for future in ready:
                                    item = pending.pop(future)
                                    try:
                                        save_result(future.result())
                                    except Exception as exc:
                                        record_failure(item, exc)
                                if not pending and concurrency and not failures and not memory_failed and count:
                                    concurrency.calibrated_after_success()
                                continue
                            if stopped or failures:
                                break
                            if memory_failed:
                                # All other CUDA work is finished. Clear only idle
                                # allocator cache, then retry failed clips once at
                                # one-at-a-time FP32 with unchanged voice settings.
                                concurrency.recover()
                                retry_items, memory_failed = memory_failed, []
                                for item in retry_items:
                                    if cancel.is_set() or disconnected.is_set():
                                        break
                                    retry_count += 1
                                    concurrency.memory_retries += 1
                                    try:
                                        save_result(generate(item))
                                    except Exception as exc:
                                        record_failure(item, exc, retrying=True)
                                        break
                                if not failures:
                                    concurrency.calibrated_after_success()
                                continue
                            if position >= len(items):
                                break
                if failures:
                    index, exc = failures[0]
                    detail = getattr(exc, 'detail', str(exc))
                    if isinstance(detail, dict):
                        detail = detail.get('message', str(detail))
                    put({'type': 'error', 'index': index, 'message': str(detail)[:1200],
                         'status': getattr(exc, 'status_code', 500)})
                else:
                    put({'type': 'end', 'done': count, 'paused': cancel.is_set(), 'parallel': runtime()})
            except Exception as exc:
                detail = getattr(exc, 'detail', str(exc))
                if isinstance(detail, dict):
                    detail = detail.get('message', str(detail))
                put({'type': 'error', 'index': None, 'message': str(detail)[:1200],
                     'status': getattr(exc, 'status_code', 500)})
            finally:
                with registry_lock:
                    active.pop(job_id, None)
                gate.release()

        def frames():
            try:
                while True:
                    try:
                        header, body = events.get(timeout=1.0)
                    except queue.Empty:
                        header, body = {'type': 'heartbeat'}, b''
                    header['size'] = len(body)
                    metadata = json.dumps(header, ensure_ascii=False).encode('utf-8')
                    yield struct.pack('!I', len(metadata)) + metadata
                    if body:
                        yield body
                    if header['type'] in ('end', 'error'):
                        return
            finally:
                disconnected.set()
                cancel.set()

        worker = threading.Thread(target=produce, name='cosy-batch-' + job_id[:8], daemon=True)
        try:
            worker.start()
        except BaseException:
            with registry_lock:
                active.pop(job_id, None)
            gate.release()
            raise
        return StreamingResponse(frames(), media_type='application/x-voice-studio-batch',
                                 headers={'X-Batch-Protocol': '1', 'Cache-Control': 'no-store',
                                          'X-Accel-Buffering': 'no'})


def serve(port):
    import faulthandler
    # A live process is not proof of progress. Expose where startup is waiting.
    faulthandler.enable()
    faulthandler.dump_traceback_later(120, repeat=True)
    print('[모델 준비 1/4] 오디오·Torch 실행 환경을 불러옵니다.', flush=True)
    import numpy as np
    import soundfile as sf
    import torch
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import Response
    import uvicorn
    import fcntl
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA GPU를 사용할 수 없습니다. T4 GPU 런타임인지 확인해주세요.')
    print('[모델 준비 2/4] GPU 확인 완료. CosyVoice 실행 코드를 불러옵니다.', flush=True)
    from cosyvoice.cli.cosyvoice import CosyVoice2
    print('[모델 준비 3/4] 음성 모델·토크나이저를 불러옵니다. 세부 기록이 이어집니다.', flush=True)
    model = CosyVoice2(model_dir=str(MODEL), load_jit=False, load_trt=False, fp16=False)
    acceleration = configure_fp32_acceleration(model)
    parallel_enabled = install_request_streams(model)
    generation_timer = install_generation_timer(model)
    def memory_snapshot():
        free, total = torch.cuda.mem_get_info()
        return dict(free=free, total=total, allocated=torch.cuda.memory_allocated(),
                    reserved=torch.cuda.memory_reserved(), peak=torch.cuda.max_memory_allocated())
    concurrency = AutoConcurrency(memory_snapshot, torch.cuda.reset_peak_memory_stats,
                                  torch.cuda.empty_cache, enabled=parallel_enabled)
    gpu_name = torch.cuda.get_device_name(0)
    print(f'실행 환경: {gpu_name} · {acceleration["label"]}', flush=True)
    faulthandler.cancel_dump_traceback_later()
    print('[모델 준비 4/4] 모델 로딩 완료. 연결 서버를 시작합니다.', flush=True)
    sample_rate = model.sample_rate
    if sample_rate != 24000:
        raise RuntimeError('CosyVoice 2 출력 설정이 올바르지 않습니다. 모델 설정을 다시 확인해주세요.')
    access_token = os.environ['COSY_ACCESS_TOKEN']
    reference_lock = threading.Lock()
    reference_pins = {}
    # Preparation/eviction is locked; inference reads pinned conditioning tensors.
    # The soft cache limit is 16 plus at most 32 currently pinned requests.
    reference_cache = OrderedDict()
    reference_cache_limit = 16
    instance_id = secrets.token_hex(12)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(token):
        if not secrets.compare_digest(token, access_token):
            raise HTTPException(403, '연결 주소가 올바르지 않습니다. 새 주소 전체를 복사해주세요.')

    @app.get('/')
    def index():
        return {'service': SERVICE, 'message': LABEL + ' 실행 중. 코랩에 표시된 전체 연결 주소를 사용하세요.'}

    @app.get('/v1/{token}')
    @app.get('/v1/{token}/health')
    def status(token: str):
        authorize(token)
        return {'service': SERVICE, 'api_version': 1, 'ready': True, 'model': LABEL, 'engine': ENGINE, 'cuda': True,
                'server_version': SERVER_VERSION, 'sample_rate': sample_rate, 'instance_id': instance_id,
                'gpu_name': gpu_name, 'acceleration': acceleration, 'concurrency': concurrency.status(),
                'capabilities': ['style_instruction', GENERATION_CAPABILITY, REFERENCE_CACHE_CAPABILITY,
                                 REFERENCE_TRANSPORT_CAPABILITY, DURATION_GUARD_CAPABILITY, PERFORMANCE_CAPABILITY,
                                 THROUGHPUT_CAPABILITY, LOSSLESS_TRANSPORT_CAPABILITY, BATCH_CAPABILITY, PARALLEL_CAPABILITY]}

    def synthesize(token: str, text: str = Form(...), prompt_text: str = Form(''),
                   speed: float = Form(1.0), reference: UploadFile | None = File(None),
                   style_instruction: str = Form(''), reference_id: str = Form(''),
                   audio_format: str = Form('wav')):
        authorize(token)
        if audio_format not in ('wav', 'flac'):
            raise HTTPException(422, '지원하는 전송 형식은 WAV 또는 FLAC입니다.')
        try:
            text = normalize_speech_text(text, '생성할 대사')
            prompt_text = normalize_speech_text(prompt_text, '참조 오디오 실제 대사')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if len(text) > 2000:
            raise HTTPException(422, '한 번에 2,000자 이하로 나눠 생성해주세요.')
        if len(prompt_text) > 1000:
            raise HTTPException(422, '참조 대사에는 3~30초 참고 음성에서 말한 내용만 입력해주세요.')
        if not np.isfinite(speed) or not 0.5 <= speed <= 2:
            raise HTTPException(422, '속도는 0.5~2.0 사이여야 합니다.')
        style_instruction = style_instruction.strip()
        if len(style_instruction) > 800 or '<|' in style_instruction or '|>' in style_instruction:
            raise HTTPException(422, '스타일 지시문 형식이 올바르지 않습니다.')
        pinned_reference = None
        stream = torch.cuda.Stream(device=0)
        stream.wait_stream(torch.cuda.default_stream(0))
        stream_context = torch.cuda.stream(stream)
        stream_context.__enter__()
        try:
            instruction = ('Speak in Korean. ' + style_instruction + '<|endofprompt|>') if style_instruction else ''
            payload = None
            if reference is not None:
                payload = reference.file.read(10 * 1024 * 1024 + 1)
                if not payload or len(payload) > 10 * 1024 * 1024:
                    raise HTTPException(422, '참조 음성은 10MB 이하 WAV 또는 MP3를 사용해주세요.')
                identity = json.dumps([hashlib.sha256(payload).hexdigest(), prompt_text,
                                       instruction, SERVER_VERSION], ensure_ascii=False)
                reference_id = 'studio_' + hashlib.sha256(identity.encode('utf-8')).hexdigest()
            elif not re.fullmatch(r'studio_[a-f0-9]{64}', reference_id):
                raise HTTPException(422, '참고 음성을 등록해주세요.')
            with tempfile.TemporaryDirectory(prefix='cosy_ref_') as folder:
                preparation_started = time.monotonic()
                with reference_lock:
                    cached = reference_cache.get(reference_id)
                    reference_hit = (cached is not None and reference_id in model.frontend.spk2info
                                     and cached.get('prompt_text') == prompt_text
                                     and cached.get('instruction') == instruction)
                    if payload is None and not reference_hit:
                        # This response is strictly before ANY model call. The client
                        # may restore the reference bytes without duplicating speech.
                        raise HTTPException(428, {'code': 'reference_required', 'synthesis_started': False,
                                                  'message': '참고 음성 정보를 다시 전송해주세요.'})
                    # Include the conditioning text and mode: instruction-mode voices
                    # must never reuse a basic-mode transcript (upstream issue #1400).
                    if reference_hit:
                        reference_cache.move_to_end(reference_id)
                        duration = cached['duration']
                        reference_spoken_seconds = cached['spoken_seconds']
                        reference_units = cached['speech_units']
                        print('참고 목소리 분석 결과를 재사용합니다.', flush=True)
                    else:
                        print('참고 목소리를 분석합니다. 같은 음성은 다음 대사부터 재사용합니다.', flush=True)
                        original = Path(folder) / 'reference.audio'
                        original.write_bytes(payload)
                        prepared = Path(folder) / 'reference.wav'
                        result = subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(original),
                                                 '-t', '31', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_f32le', str(prepared)],
                                                capture_output=True, text=True, timeout=60)
                        if result.returncode:
                            raise HTTPException(422, '참조 오디오를 읽을 수 없습니다. WAV 또는 MP3를 확인해주세요.')
                        audio, sr = sf.read(prepared, dtype='float32')
                        duration = len(audio) / sr
                        if not 3 <= duration <= 30:
                            raise HTTPException(422, f'참조 음성은 3~30초여야 합니다. 현재 {duration:.1f}초입니다.')
                        if not np.all(np.isfinite(audio)) or np.max(np.abs(audio)) < 0.00001:
                            raise HTTPException(422, '참조 음성에 들리는 목소리가 없습니다.')

                        # Remove only nearly silent outer padding; never cut spoken audio
                        # to a fixed length without also aligning its transcript.
                        frame = int(sr * 0.02)
                        padded = np.pad(audio, (0, (-len(audio)) % frame))
                        levels = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1))
                        active = np.flatnonzero(levels > max(0.0001, float(levels.max()) * 0.015))
                        if active.size == 0 or active.size * 0.02 < 0.8:
                            raise HTTPException(422, '참고 음성의 실제 발화가 너무 짧거나 조용합니다. 배경음 없이 한 사람이 문장을 말하는 녹음을 사용해주세요.')
                        spoken_seconds = active.size * 0.02
                        units = speech_units(prompt_text)
                        if (spoken_seconds > 6 and units / spoken_seconds < 0.75) or units / spoken_seconds > 25:
                            raise HTTPException(422, f'참고 음성 길이({duration:.1f}초)와 입력한 참고 대사 길이가 크게 다릅니다. 새로 만들 대사가 아니라 녹음 전체의 실제 대사를 입력해주세요.')
                        margin = int(sr * 0.15)
                        start = max(0, int(active[0]) * frame - margin)
                        end = min(len(audio), (int(active[-1]) + 1) * frame + margin)
                        audio = audio[start:end]
                        peak = float(np.max(np.abs(audio)))
                        # Leave ordinary recordings untouched and avoid amplifying noise.
                        if peak > 0.95:
                            audio = audio * (0.95 / peak)
                        elif peak < 0.1:
                            audio = audio * min(4.0, 0.1 / peak)
                        sf.write(prepared, audio, sr, subtype='FLOAT')
                        for oldest in list(reference_cache):
                            if len(reference_cache) < reference_cache_limit:
                                break
                            if not reference_pins.get(oldest):
                                reference_cache.pop(oldest)
                                model.frontend.spk2info.pop(oldest, None)
                        with torch.inference_mode():
                            model.add_zero_shot_spk(instruction or prompt_text, str(prepared), reference_id)
                        reference_cache[reference_id] = {'duration': duration, 'prompt_text': prompt_text,
                                                         'instruction': instruction,
                                                         'spoken_seconds': spoken_seconds, 'speech_units': units}
                        ready = torch.cuda.Event()
                        ready.record(stream)
                        reference_cache[reference_id]['ready'] = ready
                        reference_spoken_seconds = spoken_seconds
                        reference_units = units
                    stream.wait_event(reference_cache[reference_id]['ready'])
                    reference_pins[reference_id] = reference_pins.get(reference_id, 0) + 1
                    pinned_reference = reference_id
                preparation_seconds = time.monotonic() - preparation_started

                pieces = []
                chunks = synthesis_chunks(text, prompt_text, model.frontend.tokenizer)
                request_id = secrets.token_hex(4)
                logging.info('request=%s mode=%s ref_seconds=%.2f text_chars=%d chunks=%d',
                             request_id, 'style' if style_instruction else 'zero_shot', duration, len(text), len(chunks))
                synthesis_started = time.monotonic()
                generation_timer['llm_seconds'] = 0.0
                generation_timer['sampling_seconds'] = 0.0
                recovery_count, recovery_seconds = 0, 0.0
                # One recovery attempt per entire request, not an unbounded
                # per-chunk loop. No model/precision/voice/speed change on retry.
                recovery_remaining = 1
                with torch.inference_mode():
                    for index, chunk in enumerate(chunks, 1):
                        chunk_started = time.monotonic()
                        print(f'음성 생성 {index}/{len(chunks)} 구간 처리 중…', flush=True)
                        lower, upper = duration_limits(chunk, reference_units, reference_spoken_seconds)
                        is_recovery = False
                        while True:
                            attempt_started = time.monotonic()
                            try:
                                if style_instruction:
                                    generated = model.inference_instruct2(chunk, instruction, '',
                                                                         zero_shot_spk_id=reference_id,
                                                                         stream=False, speed=1.0, text_frontend=False)
                                else:
                                    generated = model.inference_zero_shot(chunk, prompt_text, '',
                                                                          zero_shot_spk_id=reference_id,
                                                                          stream=False, speed=1.0, text_frontend=False)
                                chunk_pieces = []
                                for item in generated:
                                    chunk_pieces.append(item['tts_speech'].detach().cpu().numpy().reshape(-1))
                            finally:
                                if is_recovery:
                                    recovery_seconds += time.monotonic() - attempt_started
                            if not chunk_pieces or sum(part.size for part in chunk_pieces) == 0:
                                raise GeneratedAudioValidationError(f'{index}번째 구간에서 빈 음성이 반환되어 저장하지 않았습니다.')
                            speech = np.concatenate(chunk_pieces)
                            seconds = speech.size / sample_rate
                            if not np.all(np.isfinite(speech)) or float(np.sqrt(np.mean(speech ** 2))) < 0.0001:
                                raise GeneratedAudioValidationError(f'{index}번째 구간의 음성이 무음이거나 손상되어 저장하지 않았습니다.')
                            if lower <= seconds <= upper:
                                break
                            logging.warning('request=%s chunk=%d duration=%.2f bounds=%.2f..%.2f units=%.1f ref_units=%.1f ref_spoken=%.2f',
                                            request_id, index, seconds, lower, upper, speech_units(chunk),
                                            reference_units, reference_spoken_seconds)
                            if recovery_remaining:
                                recovery_remaining -= 1
                                recovery_count += 1
                                is_recovery = True
                                print(f'{index}번째 구간 결과 길이({seconds:.1f}초)가 검사 범위를 벗어나 해당 구간만 한 번 다시 생성합니다. 음질 설정은 유지합니다.', flush=True)
                                del speech, chunk_pieces
                                continue
                            raise GeneratedAudioValidationError(
                                f'{index}번째 구간이 길이 검사를 통과하지 못했습니다 '
                                f'(생성 {seconds:.1f}초, 검사 범위 {lower:.1f}~{upper:.1f}초). '
                                '추가 생성은 요청당 한 번까지만 하며, 통과하지 않은 결과는 저장하지 않습니다. '
                                '길이만으로 참고 대사 오류 여부를 확정할 수 없습니다. 이전 완료 대사는 유지됩니다.')
                        logging.info('request=%s chunk=%d/%d chars=%d seconds=%.2f',
                                     request_id, index, len(chunks), len(chunk), seconds)
                        print(f'음성 생성 {index}/{len(chunks)} 완료 · 처리 {time.monotonic() - chunk_started:.1f}초 · 음성 {seconds:.1f}초', flush=True)
                        pieces.append(speech)
                        if index < len(chunks):
                            pieces.append(np.zeros(int(sample_rate * 0.12), dtype=np.float32))
                synthesis_seconds = time.monotonic() - synthesis_started
                postprocess_started = time.monotonic()
                if not pieces or sum(x.size for x in pieces) == 0:
                    raise RuntimeError('모델이 빈 음성을 반환했습니다. 참조 음성과 실제 대사를 확인해주세요.')
                speech = np.concatenate(pieces)
                if not np.all(np.isfinite(speech)) or np.max(np.abs(speech)) < 0.000001:
                    raise RuntimeError('모델 출력이 무음이거나 손상되었습니다. server.log를 확인해주세요.')
                peak = float(np.max(np.abs(speech)))
                if peak > 0.98:
                    speech = speech * (0.98 / peak)
                if speed != 1.0:
                    # Keep acoustic generation at its native rate. FFmpeg changes
                    # tempo afterwards instead of interpolating the model's mel.
                    native = Path(folder) / 'generated.wav'
                    adjusted = Path(folder) / 'tempo.wav'
                    sf.write(native, speech, sample_rate, subtype='FLOAT')
                    result = subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(native),
                                             '-af', f'atempo={speed}', '-c:a', 'pcm_f32le', str(adjusted)],
                                            capture_output=True, text=True, timeout=60)
                    if result.returncode:
                        raise RuntimeError('말하기 속도 조절에 실패했습니다. 파일을 저장하지 않았습니다.')
                    speech, _ = sf.read(adjusted, dtype='float32')
                    if not speech.size or not np.all(np.isfinite(speech)):
                        raise RuntimeError('속도 조절 결과가 비어 있거나 손상되었습니다.')
                    peak = float(np.max(np.abs(speech)))
                    if peak > 0.98:
                        speech = speech * (0.98 / peak)
                output = io.BytesIO()
                sf.write(output, speech, sample_rate, format='WAV', subtype='PCM_16')
                wav_bytes = output.getvalue()
                wire_bytes = wav_bytes
                if audio_format == 'flac':
                    # Quantize exactly once through the existing WAV writer.
                    # FLAC transports those same integer samples losslessly.
                    pcm, _ = sf.read(io.BytesIO(wav_bytes), dtype='int16')
                    compressed = io.BytesIO()
                    sf.write(compressed, pcm, sample_rate, format='FLAC', subtype='PCM_16')
                    wire_bytes = compressed.getvalue()
                postprocess_seconds = time.monotonic() - postprocess_started
                print(f'생성 완료 · 참고 분석 {preparation_seconds:.1f}초 · 음성 계산 {synthesis_seconds:.1f}초 '
                      f'(발음 순서 계산 {generation_timer["llm_seconds"]:.1f}초, 그중 후보 선택 {generation_timer["sampling_seconds"]:.1f}초) · 후처리 {postprocess_seconds:.1f}초 '
                      f'· 재시도 {recovery_count}회 / 추가 {recovery_seconds:.1f}초', flush=True)
                return Response(wire_bytes, media_type='audio/flac' if audio_format == 'flac' else 'audio/wav', headers={
                    'X-CosyVoice-Version': SERVER_VERSION, 'X-Request-ID': request_id,
                    'X-Audio-Duration': f'{len(speech) / sample_rate:.3f}',
                    'X-Audio-Format': audio_format,
                    'X-Audio-Bytes': str(len(wire_bytes)),
                    'X-WAV-Bytes': str(len(wav_bytes)),
                    'X-Text-Chunks': str(len(chunks)),
                    'X-Reference-Cache': 'hit' if reference_hit else 'miss',
                    'X-Reference-ID': reference_id,
                    'X-Reference-Upload': 'sent' if payload is not None else 'skipped',
                    'X-Reference-Seconds': f'{preparation_seconds:.3f}',
                    'X-Synthesis-Seconds': f'{synthesis_seconds:.3f}',
                    'X-Postprocess-Seconds': f'{postprocess_seconds:.3f}',
                    'X-LLM-Seconds': f'{generation_timer["llm_seconds"]:.3f}',
                    'X-Sampling-Seconds': f'{generation_timer["sampling_seconds"]:.3f}',
                    'X-Generation-Retries': str(recovery_count),
                    'X-Retry-Seconds': f'{recovery_seconds:.3f}',
                })
        except torch.cuda.OutOfMemoryError as exc:
            raise HTTPException(503, {'code': 'cuda_memory_limit',
                'message': 'GPU 메모리가 부족합니다. 동시 수를 줄여 실패한 대사만 한 번 다시 생성합니다.'}) from exc
        except GeneratedAudioValidationError as exc:
            logging.warning('%s output validation failed: %s', LABEL, exc)
            detail = str(exc) + f' (추가 생성 {recovery_count}회, 추가 처리 {recovery_seconds:.1f}초)'
            raise HTTPException(502, {'code': 'audio_validation_failed', 'message': detail,
                                     'synthesis_started': True, 'automatic_retry': False}) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logging.exception('%s synthesis failed', LABEL)
            raise HTTPException(500, f'{type(exc).__name__}: {str(exc)[:600]}') from exc
        finally:
            if reference is not None:
                reference.file.close()
            try:
                stream.synchronize()
            finally:
                stream_context.__exit__(None, None, None)
                if pinned_reference is not None:
                    with reference_lock:
                        reference_pins[pinned_reference] -= 1
                        if not reference_pins[pinned_reference]:
                            reference_pins.pop(pinned_reference)
                        for oldest in list(reference_cache):
                            if len(reference_cache) <= reference_cache_limit:
                                break
                            if not reference_pins.get(oldest):
                                reference_cache.pop(oldest)
                                model.frontend.spk2info.pop(oldest, None)

    install_batch_routes(app, synthesize, authorize, concurrency, exclusive_gpu_lease)
    uvicorn.run(app, host='127.0.0.1', port=port, access_log=False)


def tunnel():
    state = read_state()
    print('음성 서버 준비 상태를 확인합니다…', flush=True)
    try:
        ready = bool(state.get('base')) and health(state['base'])
    except Exception:
        ready = False
    if not ready:
        raise RuntimeError('음성 서버가 아직 준비되지 않았거나 중지됐습니다. 1번 셀에서 준비 완료 메시지가 나온 뒤 4번을 실행해주세요.')
    if state.get('public_base'):
        try:
            if health(state['public_base']):
                print(LABEL + ' 프로그램 연결 주소:\n' + state['public_base'], flush=True)
                return
        except Exception:
            pass
    executable = ROOT / 'cloudflared'
    if not executable.is_file():
        print('연결 도구 다운로드 중…', flush=True)
        temp = executable.with_suffix('.download')
        urllib.request.urlretrieve('https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64', temp)
        temp.chmod(0o755)
        temp.replace(executable)
    log_path = ROOT / 'tunnel.log'
    print('외부 연결을 여는 중입니다. 연결 주소가 확인될 때까지 기다려주세요 (최대 약 2분).', flush=True)
    with log_path.open('w') as log:
        proc = subprocess.Popen([str(executable), 'tunnel', '--no-autoupdate', '--url',
                                 f'http://127.0.0.1:{state["port"]}'], stdout=log, stderr=subprocess.STDOUT)
    success = False
    try:
        deadline = time.monotonic() + 120
        next_notice = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError('연결 도구가 종료되었습니다:\n' + log_path.read_text(errors='replace')[-2000:])
            match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', log_path.read_text(errors='replace'))
            if match:
                public = match.group(0) + state['base'].split(str(state['port']), 1)[1]
                try:
                    if health(public):
                        state.update(public_base=public, tunnel_pid=proc.pid)
                        STATE.write_text(json.dumps(state))
                        print('\n✅ ' + LABEL + ' 프로그램 연결 준비 완료\n프로그램 연결 주소:\n' + public, flush=True)
                        print(f'위 주소 전체를 AI Voice Studio의 {LABEL} 접속 주소에 붙여 넣으세요.', flush=True)
                        success = True
                        return
                except Exception:
                    pass
            if time.monotonic() >= next_notice:
                print('연결 주소를 확인하는 중… 아직 연결 완료가 아닙니다.', flush=True)
                next_notice = time.monotonic() + 15
            time.sleep(2)
        raise RuntimeError('외부 연결을 열지 못했습니다. 코랩 내부의 2~3번 셀은 계속 사용할 수 있습니다.')
    finally:
        if not success and proc.poll() is None:
            proc.terminate()


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--setup', action='store_true')
    modes.add_argument('--serve', action='store_true')
    modes.add_argument('--tunnel', action='store_true')
    parser.add_argument('--port', type=int, default=50000)
    parser.add_argument('--engine', choices=['cosyvoice'], default='cosyvoice')
    args = parser.parse_args()
    if args.setup:
        try:
            setup()
        except Exception:
            if ROOT.is_dir():
                with (ROOT / 'setup.log').open('a', encoding='utf-8') as log:
                    traceback.print_exc(file=log)
            raise
    elif args.serve:
        serve(args.port)
    else:
        tunnel()


if __name__ == '__main__':
    main()
