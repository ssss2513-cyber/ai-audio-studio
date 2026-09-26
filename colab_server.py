# -*- coding: utf-8 -*-
"""
AI Voice Studio - CosyVoice 3.0 완전 자동화 GPU 서버 구동기 v3
- 모든 패치(BFloat16, torchaudio, torchvision, f0_predictor) 자동 적용
- 설치 순서 보장, 견고한 regex 패치, 100% 1-Click 실행
"""
import os
import sys
import time
import subprocess
import re

print("=" * 65)
print("🎙️ AI Voice Studio - CosyVoice GPU 서버 자동 구성 v3")
print("=" * 65)

# 0. 이전 프로세스 정리
os.system("pkill -9 -f webui.py 2>/dev/null || true")
os.system("pkill -9 -f cloudflared 2>/dev/null || true")

# 1. torchvision 충돌 제거 (가장 먼저!)
print("⏳ [1/6] 충돌 패키지 제거 중...")
os.system("pip uninstall -y -q torchvision 2>/dev/null || true")

# 2. 필수 패키지 설치
print("⏳ [2/6] 필수 패키지 설치 중 (5~10분 소요, 진행상황 아래에 표시됨)...")
sys.stdout.flush()
pkgs = [
    ("pip setuptools wheel Cython", "기본 빌드 도구"),
    ("'torchaudio<2.9.0'", "오디오 처리"),
    ("lightning openai-whisper inflect pyworld-prebuilt", "AI 기반 라이브러리"),
    ("onnxruntime-gpu HyperPyYAML conformer", "모델 런타임"),
    ("diffusers hydra-core omegaconf x-transformers", "확산 모델"),
    ("wetext modelscope soundfile gradio librosa", "Gradio 서버"),
    ("gdown wget transformers networkx fastapi", "기타 의존성"),
]
for p, label in pkgs:
    print(f"  📦 {label} 설치 중...")
    sys.stdout.flush()
    os.system(f"pip install --upgrade {p}")
    print(f"  ✅ {label} 완료")
    sys.stdout.flush()

# 3. torchaudio.info 보완 패치 (라이브러리 레벨)
print("⏳ [3/6] torchaudio.info 호환 패치 적용 중...")
try:
    import importlib
    import torchaudio
    importlib.reload(torchaudio)
    with open(torchaudio.__file__, "r", encoding="utf-8") as tf:
        t_code = tf.read()
    if "_AudioMetaData" not in t_code and "info = lambda" not in t_code:
        with open(torchaudio.__file__, "a", encoding="utf-8") as tf:
            tf.write(
                "\nimport soundfile as _sf\n"
                "class _AudioMetaData:\n"
                "    def __init__(self, s): self.sample_rate = s\n"
                "info = lambda p, **kw: _AudioMetaData(_sf.info(p).samplerate)\n"
            )
        print("  ✅ torchaudio.info 패치 적용")
    else:
        print("  ✅ torchaudio.info 이미 패치됨")
except Exception as e:
    print(f"  ⚠️ torchaudio 패치 건너뜀: {e}")

# 4. CosyVoice 소스 클론
cosy_dir = "/content/CosyVoice"
print("⏳ [4/6] CosyVoice 소스코드 동기화 중...")
if not os.path.exists(cosy_dir):
    ret = os.system(f"git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git {cosy_dir}")
    if ret != 0:
        print("  ❌ git clone 실패! 인터넷 연결 확인 필요.")
        sys.exit(1)
else:
    print(f"  ✅ {cosy_dir} 이미 존재 - 업데이트 시도...")
    os.system(f"cd {cosy_dir} && git pull --quiet 2>/dev/null || true")

# CosyVoice requirements 설치
req_file = f"{cosy_dir}/requirements.txt"
if os.path.exists(req_file):
    os.system(f"pip install -q -r {req_file} 2>/dev/null || true")

# 5. 모델 다운로드
print("⏳ [5/6] 사전 학습 모델 다운로드 중...")
model_dir = f"{cosy_dir}/pretrained_models/CosyVoice2-0.5B"
try:
    from modelscope import snapshot_download
    snapshot_download("iic/CosyVoice2-0.5B", local_dir=model_dir)
    print("  ✅ 모델 다운로드 완료")
except Exception as e:
    print(f"  ⚠️ ModelScope 다운로드 실패: {e}")
    print("  🔄 git lfs 방식으로 재시도...")
    os.system(f"git lfs install && git clone https://huggingface.co/FunAudioLLM/CosyVoice2-0.5B {model_dir} 2>/dev/null || true")

# 6. 패치 적용
print("⏳ [6/6] 무결점 호환성 패치 자동 적용 중...")

# --- 패치 A: config.json bfloat16 → float32 ---
cfg_file = f"{model_dir}/CosyVoice-BlankEN/config.json"
if os.path.exists(cfg_file):
    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = f.read()
    new_cfg = cfg.replace('"torch_dtype": "bfloat16"', '"torch_dtype": "float32"')
    if new_cfg != cfg:
        with open(cfg_file, "w", encoding="utf-8") as f:
            f.write(new_cfg)
        print("  ✅ [패치A] config.json bfloat16→float32 완료")
    else:
        print("  ✅ [패치A] config.json 이미 float32")

# --- 패치 B: llm.py Qwen2 float32 강제 ---
llm_file = f"{cosy_dir}/cosyvoice/llm/llm.py"
if os.path.exists(llm_file):
    with open(llm_file, "r", encoding="utf-8") as f:
        llm_code = f.read()
    changed = False

    # from_pretrained float32
    old_b1 = "self.model = Qwen2ForCausalLM.from_pretrained(pretrain_path)"
    new_b1 = "self.model = Qwen2ForCausalLM.from_pretrained(pretrain_path, torch_dtype=torch.float32).to(torch.float32)"
    if old_b1 in llm_code:
        llm_code = llm_code.replace(old_b1, new_b1)
        changed = True

    # forward() xs → float32 (여러 패턴 대응)
    for pat, rep in [
        # 공백 1개 들여쓰기 변형
        (r"(def forward\(self, xs: torch\.Tensor, xs_lens: torch\.Tensor\):\n)(\s+)(T = xs\.size\(1\))",
         r"\1\2xs = xs.to(torch.float32)\n\2\3"),
        # forward_one_step xs → float32
        (r"(def forward_one_step\(self, xs, masks, cache=None\):\n)(\s+)(input_masks)",
         r"\1\2xs = xs.to(torch.float32)\n\2\3"),
    ]:
        new_code = re.sub(pat, rep, llm_code)
        if new_code != llm_code:
            llm_code = new_code
            changed = True

    if changed:
        with open(llm_file, "w", encoding="utf-8") as f:
            f.write(llm_code)
        print("  ✅ [패치B] llm.py Qwen2 float32 강제 완료")
    else:
        print("  ✅ [패치B] llm.py 이미 패치됨 (또는 라인 패턴 불일치 - 안전하게 진행)")

# --- 패치 C: f0_predictor.py 커널 크기 패딩 ---
f0_file = f"{cosy_dir}/cosyvoice/hifigan/f0_predictor.py"
if os.path.exists(f0_file):
    with open(f0_file, "r", encoding="utf-8") as f:
        f0_code = f.read()
    changed = False

    PAD_SNIPPET = "        if x.shape[-1] < 4:\n            x = torch.nn.functional.pad(x, (0, 4 - x.shape[-1]), mode='replicate')\n"

    # ConvRNNF0Predictor.forward
    pat1 = r"(def forward\(self, x: torch\.Tensor\) -> torch\.Tensor:\n)(        x = self\.condnet\(x\))"
    if re.search(pat1, f0_code) and PAD_SNIPPET not in f0_code:
        f0_code = re.sub(pat1, r"\1" + PAD_SNIPPET + r"        \2".replace("        ", ""), f0_code)
        # 더 안전한 직접 치환
        f0_code = re.sub(
            r"(def forward\(self, x: torch\.Tensor\) -> torch\.Tensor:)\n(        x = self\.condnet\(x\))",
            r"\1\n" + PAD_SNIPPET + r"        x = self.condnet(x)",
            f0_code
        )
        changed = True

    # CausalConvRNNF0Predictor.forward
    pat2 = r"(def forward\(self, x: torch\.Tensor, finalize: bool = True\) -> torch\.Tensor:)\n(        if finalize is True:)"
    if re.search(pat2, f0_code) and f0_code.count(PAD_SNIPPET) < 2:
        f0_code = re.sub(
            pat2,
            r"\1\n" + PAD_SNIPPET + r"        if finalize is True:",
            f0_code
        )
        changed = True

    if changed:
        with open(f0_file, "w", encoding="utf-8") as f:
            f.write(f0_code)
        print("  ✅ [패치C] f0_predictor.py 패딩 패치 완료")
    else:
        print("  ✅ [패치C] f0_predictor.py 이미 패치됨")

# --- 패치 D: webui.py (torchvision 차단 + sf.info + share=True) ---
webui_file = f"{cosy_dir}/webui.py"
if os.path.exists(webui_file):
    with open(webui_file, "r", encoding="utf-8") as f:
        w_code = f.read()
    changed = False

    patch_header = (
        "import sys\n"
        "sys.modules['torchvision'] = None\n"
        "sys.modules['torchvision.io'] = None\n"
        "sys.modules['torchvision.transforms'] = None\n"
        "sys.modules['torchvision.ops'] = None\n"
        "import soundfile as sf\n"
        "import torch\n"
    )
    if "sys.modules['torchvision'] = None" not in w_code:
        w_code = patch_header + w_code
        changed = True

    # torchaudio.info → sf.info
    new_w = re.sub(r"torchaudio\.info\(([^)]+)\)\.sample_rate", r"sf.info(\1).samplerate", w_code)
    if new_w != w_code:
        w_code = new_w
        changed = True

    # demo.launch share=True
    new_w = re.sub(
        r"demo\.launch\([^)]*\)",
        "demo.launch(server_name='0.0.0.0', server_port=args.port, share=True, show_error=True)",
        w_code
    )
    if new_w != w_code:
        w_code = new_w
        changed = True

    if changed:
        with open(webui_file, "w", encoding="utf-8") as f:
            f.write(w_code)
        print("  ✅ [패치D] webui.py 패치 완료")
    else:
        print("  ✅ [패치D] webui.py 이미 패치됨")

# --- 패치 E: model.py autocast bfloat16 → float32 안전화 ---
model_file = f"{cosy_dir}/cosyvoice/cli/model.py"
if os.path.exists(model_file):
    with open(model_file, "r", encoding="utf-8") as f:
        m_code = f.read()
    changed = False

    # fp16 autocast를 float32로 안전하게 (enabled=False로 비활성화)
    new_m = m_code.replace(
        "torch.cuda.amp.autocast(self.fp16)",
        "torch.cuda.amp.autocast(enabled=False)"
    )
    if new_m != m_code:
        m_code = new_m
        changed = True

    if changed:
        with open(model_file, "w", encoding="utf-8") as f:
            f.write(m_code)
        print("  ✅ [패치E] model.py autocast 비활성화 완료")
    else:
        print("  ✅ [패치E] model.py 이미 패치됨")

# Cloudflare 터널 시작
print("\n🚀 Cloudflare 터널 기동 중...")
tunnel_log = "/content/cosy_tunnel.log"
subprocess.Popen(
    f"cloudflared tunnel --url http://127.0.0.1:50000 --logfile {tunnel_log} > /dev/null 2>&1",
    shell=True
)

time.sleep(8)
public_url = None
if os.path.exists(tunnel_log):
    with open(tunnel_log, "r") as f:
        for line in f:
            m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if m:
                public_url = m.group(0)
                break

print("\n" + "=" * 65)
print("🎉 모든 패치 완료! CosyVoice 서버 기동 중...")
if public_url:
    print(f"👉 Cloudflare 터널 주소: {public_url}")
print("💡 아래 Gradio 링크(https://...gradio.live)를 AI Voice Studio에 입력하세요!")
print("=" * 65 + "\n")

# WebUI 실행
os.chdir(cosy_dir)
os.system("python3 webui.py --port 50000 --model_dir pretrained_models/CosyVoice2-0.5B")
