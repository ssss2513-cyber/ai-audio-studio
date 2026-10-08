"""Kaggle-only TensorRT FP32 estimator; keep weights and sampling unchanged.

Uses the ONNX estimator from the same pinned official model revision. Builds
once per model/GPU/software combination and shares the plan between the two
workers. Importing this module does not import Torch or start any GPU work.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

TRT_VERSION = '10.13.3.9'
ONNX_FILE = 'flow.decoder.estimator.fp32.onnx'
POLICY = 'strict-fp32-v1'


def _hash_file(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _build(trt, onnx, shapes, path):
    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    parser = trt.OnnxParser(network, logger)
    if not parser.parse_from_file(str(onnx)):
        raise RuntimeError('공식 FP32 ONNX 읽기 실패: ' + '; '.join(
            str(parser.get_error(i)) for i in range(min(parser.num_errors, 3))))
    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 1 << 32)
    # FP32 inputs alone do NOT disable TensorRT's default TF32 math.
    for name in ('TF32', 'FP16', 'BF16', 'INT8', 'FP8'):
        flag = getattr(trt.BuilderFlag, name, None)
        if flag is not None:
            config.clear_flag(flag)
    profile = builder.create_optimization_profile()
    for name, low, optimum, high in zip(shapes['input_names'], shapes['min_shape'],
                                      shapes['opt_shape'], shapes['max_shape']):
        if not profile.set_shape(name, low, optimum, high):
            raise RuntimeError('TensorRT 입력 길이 설정 실패: ' + name)
    config.add_optimization_profile(profile)
    for i in range(network.num_inputs):
        if network.get_input(i).dtype != trt.float32:
            raise RuntimeError('FP32가 아닌 ONNX 입력은 사용하지 않습니다.')
    for i in range(network.num_outputs):
        if network.get_output(i).dtype != trt.float32:
            raise RuntimeError('FP32가 아닌 ONNX 출력은 사용하지 않습니다.')
    encoded = builder.build_serialized_network(network, config)
    if encoded is None:
        raise RuntimeError('TensorRT FP32 엔진을 만들지 못했습니다.')
    temporary = path.with_suffix('.part')
    temporary.write_bytes(bytes(encoded))
    os.replace(temporary, path)


def install(model, model_dir, source, cache_root):
    """Return observed mode; failed setup leaves the original estimator intact."""
    result = dict(requested='tensorrt_fp32', engine='pytorch_fp32', cache_hit=False,
                  precision='fp32', note='')
    if os.environ.get('VOICE_STUDIO_ACCELERATOR') != 'tensorrt_fp32':
        result.update(requested='off', note='짧은 작업: TensorRT 최초 변환 대기 없이 FP32 최적화 사용')
        return result
    original = model.model.flow.decoder.estimator
    try:
        import fcntl
        import torch
        import tensorrt as trt
        from cosy_kaggle_contract import SOURCE_REVISION, MODELS
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'],
            text=True, timeout=5, stderr=subprocess.DEVNULL).strip()
        engine_name = {'CosyVoice2Model': 'cosyvoice', 'CosyVoice3Model': 'cosyvoice3'}.get(
            type(model.model).__name__)
        if (revision != SOURCE_REVISION or engine_name is None or model.model.fp16
                or trt.__version__ != TRT_VERSION or not hasattr(model.model, '_studio_acoustic_lock')):
            raise RuntimeError('고정 모델·FP32·TensorRT 버전 또는 음향 실행 잠금이 일치하지 않습니다.')
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        onnx = Path(model_dir) / ONNX_FILE
        if not onnx.is_file() or not onnx.stat().st_size:
            raise RuntimeError('공식 FP32 ONNX 파일이 준비되지 않았습니다.')
        shapes = model.model.get_trt_kwargs()
        drivers = subprocess.check_output(['nvidia-smi', '--query-gpu=driver_version',
            '--format=csv,noheader'], text=True, timeout=10).strip()
        identity = dict(policy=POLICY, source=revision, model=MODELS[engine_name]['revision'],
            onnx=_hash_file(onnx), gpu=torch.cuda.get_device_name(),
            capability=torch.cuda.get_device_capability(), driver=drivers,
            cuda=torch.version.cuda, torch=torch.__version__, tensorrt=trt.__version__, shapes=shapes)
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        root = Path(cache_root)
        root.mkdir(parents=True, exist_ok=True)
        plan = root / (key + '.plan')
        with (root / (key + '.lock')).open('a') as lock:
            print('가속 준비: TensorRT FP32 엔진 확인 · 두 GPU가 같은 가속 파일을 재사용합니다.', flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
            logger = trt.Logger(trt.Logger.WARNING)
            runtime = trt.Runtime(logger)
            compiled = None
            if plan.is_file() and plan.stat().st_size:
                try:
                    compiled = runtime.deserialize_cuda_engine(plan.read_bytes())
                except Exception:
                    compiled = None
            result['cache_hit'] = compiled is not None
            if compiled is None:
                print('가속 준비: TensorRT FP32 최초 변환 중 · 이번 준비에는 추가 시간이 걸립니다.', flush=True)
                _build(trt, onnx, shapes, plan)
                compiled = runtime.deserialize_cuda_engine(plan.read_bytes())
            if compiled is None:
                plan.unlink(missing_ok=True)
                raise RuntimeError('TensorRT 엔진을 불러오지 못했습니다.')
        context = compiled.create_execution_context()
        if context is None:
            raise RuntimeError('TensorRT 실행 메모리를 확보하지 못했습니다.')
        names = [compiled.get_tensor_name(i) for i in range(compiled.num_io_tensors)]
        inputs = [name for name in names if compiled.get_tensor_mode(name) == trt.TensorIOMode.INPUT]
        outputs = [name for name in names if compiled.get_tensor_mode(name) == trt.TensorIOMode.OUTPUT]
        if set(inputs) != {'x', 'mask', 'mu', 't', 'spks', 'cond'} or len(outputs) != 1:
            raise RuntimeError('공식 음향 엔진의 입력·출력 구성이 다릅니다.')
        if any(compiled.get_tensor_dtype(name) != trt.float32 for name in names):
            raise RuntimeError('가속 엔진에 FP32가 아닌 입출력이 있습니다.')

        class FP32Estimator(torch.nn.Module):
            # All forwards are inside the existing per-GPU acoustic lock.
            # Use the caller's stream, avoiding a device-wide wait every flow
            # step. Output is separate from x, as with the PyTorch estimator.
            def __init__(self):
                super().__init__()
                self.original = original.cpu()
                self.runtime = runtime
                self.engine = compiled
                self.context = context
                self.fallbacks = 0

            def forward(self, x, mask, mu, t, spks, cond, streaming=False):
                values = dict(x=x, mask=mask, mu=mu, t=t, spks=spks, cond=cond)
                supported = not streaming and all(value.dtype == torch.float32 for value in values.values())
                for name in inputs:
                    declared = tuple(self.engine.get_tensor_shape(name))
                    if -1 in declared:
                        low, _, high = self.engine.get_tensor_profile_shape(name, 0)
                    else:
                        low = high = declared
                    shape = tuple(values[name].shape)
                    supported = supported and len(shape) == len(low) and all(
                        a <= b <= c for a, b, c in zip(low, shape, high))
                if not supported:
                    self.fallbacks += 1
                    print('가속 범위 밖의 구간: 원본 FP32 계산 사용 · 대사·계산 단계 유지', flush=True)
                    self.original.to(x.device)
                    try:
                        return self.original(x, mask, mu, t, spks, cond, streaming=streaming)
                    finally:
                        torch.cuda.current_stream().synchronize()
                        self.original.cpu()
                values = {name: value.contiguous() for name, value in values.items()}
                for name, value in values.items():
                    if not self.context.set_input_shape(name, tuple(value.shape)):
                        raise RuntimeError('TensorRT 입력 길이 지정 실패: ' + name)
                    if not self.context.set_tensor_address(name, value.data_ptr()):
                        raise RuntimeError('TensorRT 입력 연결 실패: ' + name)
                shape = tuple(self.context.get_tensor_shape(outputs[0]))
                if shape != tuple(x.shape):
                    raise RuntimeError('TensorRT 음향 출력 길이가 올바르지 않습니다.')
                output = torch.empty(shape, device=x.device, dtype=torch.float32)
                if not self.context.set_tensor_address(outputs[0], output.data_ptr()):
                    raise RuntimeError('TensorRT 출력 연결 실패')
                if not self.context.execute_async_v3(torch.cuda.current_stream().cuda_stream):
                    raise RuntimeError('TensorRT FP32 계산 실패 · 음성을 저장하지 않습니다.')
                return output

        model.model.flow.decoder.estimator = FP32Estimator().eval()
        result['engine'] = 'tensorrt_fp32'
        print('가속 적용: TensorRT FP32 · FP16/BF16/TF32/양자화 사용 안 함 · '
              + ('기존 가속 파일 재사용' if result['cache_hit'] else '새 가속 파일 저장'), flush=True)
    except Exception as exc:
        # Setup only; never retry failed generation with changed settings.
        model.model.flow.decoder.estimator = original
        if 'torch' in locals():
            original.to(model.model.device)
        result['note'] = type(exc).__name__ + ': ' + str(exc)[:500]
        print('가속 미적용: 기존 FP32 계산으로 진행 · ' + result['note'], flush=True)
    return result
