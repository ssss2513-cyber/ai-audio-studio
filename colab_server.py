# -*- coding: utf-8 -*-
"""
AI Voice Studio - CosyVoice 완전 자동화 설치 + 서버 구동기 v5
"""
import os, sys, time, subprocess, re

def run(cmd, label=""):
    """명령 실행 + 라벨 출력"""
    if label:
        print(f"\n{'='*50}")
        print(f"⏳ {label}")
        print(f"{'='*50}")
        sys.stdout.flush()
    ret = os.system(cmd)
    if label:
        ok = "✅ 완료!" if ret == 0 else "⚠️ 완료 (일부 경고 있을 수 있음)"
        print(ok)
        sys.stdout.flush()
    return ret

print("\n" + "🎙️  AI Voice Studio - CosyVoice 자동 설치 시작".center(55))
print("⏱️  총 10~15분 소요됩니다. 아래 진행 상황을 지켜봐 주세요.")
sys.stdout.flush()

# ─── 1. GPU 확인 ─────────────────────────────────────────────
print("\n⏳ GPU 확인 중...")
sys.stdout.flush()
os.system("nvidia-smi | head -12")
sys.stdout.flush()

# ─── 2. 시스템 패키지 ─────────────────────────────────────────
run("apt-get update -q 2>&1 | tail -3",
    "[1/7] 시스템 업데이트")

run("apt-get install -y -q ffmpeg sox libsox-dev build-essential python3-dev git-lfs curl wget 2>&1 | tail -5",
    "[2/7] 시스템 패키지 (ffmpeg, sox, build-essential...)")

# ─── 3. Cloudflare 터널 ───────────────────────────────────────
run("wget -q -nc 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64' -O /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared",
    "[3/7] Cloudflare 터널")

# ─── 4. 충돌 패키지 제거 ──────────────────────────────────────
run("pip uninstall -y torchvision 2>/dev/null || true",
    "[4/7] torchvision 충돌 제거")

# ─── 5. Python 패키지 설치 (핵심) ────────────────────────────
packages = (
    "'torchaudio<2.9.0' lightning openai-whisper inflect pyworld-prebuilt "
    "onnxruntime-gpu HyperPyYAML conformer diffusers hydra-core omegaconf "
    "x-transformers wetext modelscope soundfile gradio librosa "
    "gdown transformers networkx fastapi Cython wheel"
)
run(f"pip install {packages} 2>&1 | grep -E '(Successfully|already|ERROR|WARNING)' || true",
    "[5/7] Python AI 패키지 설치 (5~8분 소요)")

# ─── 6. CosyVoice 소스 + 모델 ────────────────────────────────
cosy_dir = "/content/CosyVoice"
model_dir = f"{cosy_dir}/pretrained_models/CosyVoice2-0.5B"

run(f"git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git {cosy_dir} 2>&1 | tail -3"
    if not os.path.exists(cosy_dir)
    else f"cd {cosy_dir} && git pull 2>&1 | tail -2",
    "[6/7] CosyVoice 소스코드 다운로드")

print("\n⏳ [6/7] AI 모델 다운로드 중 (1~2GB, 5~10분)...")
sys.stdout.flush()
if not os.path.exists(model_dir) or len(os.listdir(model_dir)) < 3:
    try:
        from modelscope import snapshot_download
        snapshot_download("iic/CosyVoice2-0.5B", local_dir=model_dir)
        print("✅ 모델 다운로드 완료!")
    except Exception as e:
        print(f"⚠️ ModelScope 실패: {e}")
        print("🔄 HuggingFace로 재시도...")
        sys.stdout.flush()
        os.system(f"git lfs install && git clone https://huggingface.co/FunAudioLLM/CosyVoice2-0.5B {model_dir} 2>&1 | tail -5")
else:
    print("✅ 모델 이미 존재, 건너뜀")
sys.stdout.flush()

# ─── 7. 패치 적용 ────────────────────────────────────────────
print(f"\n{'='*50}")
print("⏳ [7/7] 버그 패치 자동 적용 중...")
print(f"{'='*50}")
sys.stdout.flush()

# 패치A: config.json bfloat16→float32
cfg = f"{model_dir}/CosyVoice-BlankEN/config.json"
if os.path.exists(cfg):
    t = open(cfg, encoding="utf-8").read()
    n = t.replace('"torch_dtype": "bfloat16"', '"torch_dtype": "float32"')
    if n != t:
        open(cfg, "w", encoding="utf-8").write(n)
        print("  ✅ [A] config.json float32")

# 패치B: llm.py float32
llm = f"{cosy_dir}/cosyvoice/llm/llm.py"
if os.path.exists(llm):
    t = open(llm, encoding="utf-8").read(); c = False
    old = "self.model = Qwen2ForCausalLM.from_pretrained(pretrain_path)"
    new = "self.model = Qwen2ForCausalLM.from_pretrained(pretrain_path, torch_dtype=torch.float32).to(torch.float32)"
    if old in t: t = t.replace(old, new); c = True
    for p, r in [
        (r"(def forward\(self, xs: torch\.Tensor, xs_lens: torch\.Tensor\):\n)(\s+)(T = xs\.size\(1\))",
         r"\1\2xs = xs.to(torch.float32)\n\2\3"),
        (r"(def forward_one_step\(self, xs, masks, cache=None\):\n)(\s+)(input_masks)",
         r"\1\2xs = xs.to(torch.float32)\n\2\3"),
    ]:
        n2 = re.sub(p, r, t)
        if n2 != t: t = n2; c = True
    if c: open(llm, "w", encoding="utf-8").write(t); print("  ✅ [B] llm.py float32")

# 패치C: f0_predictor.py 패딩
f0 = f"{cosy_dir}/cosyvoice/hifigan/f0_predictor.py"
if os.path.exists(f0):
    t = open(f0, encoding="utf-8").read(); c = False
    PAD = "        if x.shape[-1] < 4:\n            x = torch.nn.functional.pad(x, (0, 4 - x.shape[-1]), mode='replicate')\n"
    for p, r in [
        (r"(def forward\(self, x: torch\.Tensor\) -> torch\.Tensor:)\n(        x = self\.condnet\(x\))",
         r"\1\n" + PAD + r"        x = self.condnet(x)"),
        (r"(def forward\(self, x: torch\.Tensor, finalize: bool = True\) -> torch\.Tensor:)\n(        if finalize is True:)",
         r"\1\n" + PAD + r"        if finalize is True:"),
    ]:
        n2 = re.sub(p, r, t)
        if n2 != t: t = n2; c = True
    if c: open(f0, "w", encoding="utf-8").write(t); print("  ✅ [C] f0_predictor.py 패딩")

# 패치D: webui.py
webui = f"{cosy_dir}/webui.py"
if os.path.exists(webui):
    t = open(webui, encoding="utf-8").read(); c = False
    hdr = "import sys\nsys.modules['torchvision']=None\nsys.modules['torchvision.ops']=None\nimport soundfile as sf\nimport torch\n"
    if "sys.modules['torchvision']=None" not in t: t = hdr + t; c = True
    n2 = re.sub(r"torchaudio\.info\(([^)]+)\)\.sample_rate", r"sf.info(\1).samplerate", t)
    if n2 != t: t = n2; c = True
    n2 = re.sub(r"demo\.launch\([^)]*\)", "demo.launch(server_name='0.0.0.0',server_port=args.port,share=True,show_error=True)", t)
    if n2 != t: t = n2; c = True
    if c: open(webui, "w", encoding="utf-8").write(t); print("  ✅ [D] webui.py 패치")

# 패치E: model.py autocast
mp = f"{cosy_dir}/cosyvoice/cli/model.py"
if os.path.exists(mp):
    t = open(mp, encoding="utf-8").read()
    n2 = t.replace("torch.cuda.amp.autocast(self.fp16)", "torch.cuda.amp.autocast(enabled=False)")
    if n2 != t: open(mp, "w", encoding="utf-8").write(n2); print("  ✅ [E] model.py autocast off")

# torchaudio.info 패치
try:
    import torchaudio as _ta
    code = open(_ta.__file__, encoding="utf-8").read()
    if "info = lambda" not in code:
        with open(_ta.__file__, "a", encoding="utf-8") as f:
            f.write("\nimport soundfile as _sf\nclass _AudioMetaData:\n    def __init__(self,s): self.sample_rate=s\ninfo=lambda p,**kw:_AudioMetaData(_sf.info(p).samplerate)\n")
        print("  ✅ [F] torchaudio.info 패치")
except: pass

print("✅ [7/7] 모든 패치 완료!")
sys.stdout.flush()

# ─── 서버 시작 ────────────────────────────────────────────────
print(f"\n{'='*55}")
print("🚀 Cloudflare 터널 + CosyVoice 서버 시작!")
print(f"{'='*55}\n")
sys.stdout.flush()

tunnel_log = "/content/cosy_tunnel.log"
subprocess.Popen(
    f"cloudflared tunnel --url http://127.0.0.1:50000 --logfile {tunnel_log} >/dev/null 2>&1",
    shell=True
)
time.sleep(8)

url = None
if os.path.exists(tunnel_log):
    for line in open(tunnel_log):
        m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if m: url = m.group(0); break

if url:
    print(f"🌐 Cloudflare: {url}")
print("💡 아래 gradio.live 링크 → AI Voice Studio 사이드바에 입력!")
sys.stdout.flush()

os.chdir(cosy_dir)
os.system("python3 webui.py --port 50000 --model_dir pretrained_models/CosyVoice2-0.5B")
