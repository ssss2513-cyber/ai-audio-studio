# -*- coding: utf-8 -*-
"""
AI Voice Studio - CosyVoice 서버 구동기 v4
pip 설치는 노트북 %%bash에서 이미 했으므로 여기선 패치+실행만!
"""
import os
import sys
import time
import subprocess
import re

print("=" * 60)
print("🎙️  CosyVoice 패치 적용 + 서버 시작")
print("=" * 60)
sys.stdout.flush()

# 이전 프로세스 정리
os.system("pkill -9 -f webui.py 2>/dev/null; pkill -9 -f cloudflared 2>/dev/null; true")

cosy_dir = "/content/CosyVoice"
model_dir = f"{cosy_dir}/pretrained_models/CosyVoice2-0.5B"

# ── [1/4] CosyVoice 소스 클론 ──────────────────────────────
print("\n⏳ [1/4] CosyVoice 소스코드 다운로드 중...")
sys.stdout.flush()
if not os.path.exists(cosy_dir):
    ret = os.system(f"git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git {cosy_dir}")
    if ret != 0:
        print("❌ CosyVoice 클론 실패!")
        sys.exit(1)
else:
    print(f"  ✅ 이미 존재, 최신화 중...")
    os.system(f"cd {cosy_dir} && git pull --quiet 2>/dev/null || true")
print("✅ [1/4] 완료")
sys.stdout.flush()

# ── [2/4] AI 모델 다운로드 ─────────────────────────────────
print("\n⏳ [2/4] AI 모델 다운로드 중 (1~2GB, 5~10분 소요)...")
sys.stdout.flush()
if not os.path.exists(model_dir) or len(os.listdir(model_dir)) < 3:
    try:
        from modelscope import snapshot_download
        snapshot_download("iic/CosyVoice2-0.5B", local_dir=model_dir)
    except Exception as e:
        print(f"  ⚠️ ModelScope 실패: {e}")
        print("  🔄 HuggingFace로 재시도...")
        sys.stdout.flush()
        os.system(f"git lfs install && git clone https://huggingface.co/FunAudioLLM/CosyVoice2-0.5B {model_dir} 2>/dev/null || true")
else:
    print("  ✅ 모델 이미 존재, 건너뜀")
print("✅ [2/4] 완료")
sys.stdout.flush()

# ── [3/4] 버그 패치 자동 적용 ─────────────────────────────
print("\n⏳ [3/4] 호환성 패치 적용 중...")
sys.stdout.flush()

# 패치A: config.json bfloat16→float32
cfg_file = f"{model_dir}/CosyVoice-BlankEN/config.json"
if os.path.exists(cfg_file):
    txt = open(cfg_file, encoding="utf-8").read()
    new = txt.replace('"torch_dtype": "bfloat16"', '"torch_dtype": "float32"')
    if new != txt:
        open(cfg_file, "w", encoding="utf-8").write(new)
        print("  ✅ [A] config.json float32 변환")

# 패치B: llm.py Qwen2 float32 강제
llm_file = f"{cosy_dir}/cosyvoice/llm/llm.py"
if os.path.exists(llm_file):
    txt = open(llm_file, encoding="utf-8").read()
    changed = False
    old = "self.model = Qwen2ForCausalLM.from_pretrained(pretrain_path)"
    new = "self.model = Qwen2ForCausalLM.from_pretrained(pretrain_path, torch_dtype=torch.float32).to(torch.float32)"
    if old in txt:
        txt = txt.replace(old, new); changed = True
    for pat, rep in [
        (r"(def forward\(self, xs: torch\.Tensor, xs_lens: torch\.Tensor\):\n)(\s+)(T = xs\.size\(1\))",
         r"\1\2xs = xs.to(torch.float32)\n\2\3"),
        (r"(def forward_one_step\(self, xs, masks, cache=None\):\n)(\s+)(input_masks)",
         r"\1\2xs = xs.to(torch.float32)\n\2\3"),
    ]:
        new2 = re.sub(pat, rep, txt)
        if new2 != txt:
            txt = new2; changed = True
    if changed:
        open(llm_file, "w", encoding="utf-8").write(txt)
        print("  ✅ [B] llm.py float32 강제")

# 패치C: f0_predictor.py 패딩
f0_file = f"{cosy_dir}/cosyvoice/hifigan/f0_predictor.py"
if os.path.exists(f0_file):
    txt = open(f0_file, encoding="utf-8").read()
    PAD = "        if x.shape[-1] < 4:\n            x = torch.nn.functional.pad(x, (0, 4 - x.shape[-1]), mode='replicate')\n"
    changed = False
    for pat, rep in [
        (r"(def forward\(self, x: torch\.Tensor\) -> torch\.Tensor:)\n(        x = self\.condnet\(x\))",
         r"\1\n" + PAD + r"        x = self.condnet(x)"),
        (r"(def forward\(self, x: torch\.Tensor, finalize: bool = True\) -> torch\.Tensor:)\n(        if finalize is True:)",
         r"\1\n" + PAD + r"        if finalize is True:"),
    ]:
        new2 = re.sub(pat, rep, txt)
        if new2 != txt:
            txt = new2; changed = True
    if changed:
        open(f0_file, "w", encoding="utf-8").write(txt)
        print("  ✅ [C] f0_predictor.py 패딩")

# 패치D: webui.py torchvision차단 + sf.info + share=True
webui_file = f"{cosy_dir}/webui.py"
if os.path.exists(webui_file):
    txt = open(webui_file, encoding="utf-8").read()
    changed = False
    hdr = "import sys\nsys.modules['torchvision']=None\nsys.modules['torchvision.ops']=None\nimport soundfile as sf\nimport torch\n"
    if "sys.modules['torchvision']=None" not in txt:
        txt = hdr + txt; changed = True
    new2 = re.sub(r"torchaudio\.info\(([^)]+)\)\.sample_rate", r"sf.info(\1).samplerate", txt)
    if new2 != txt:
        txt = new2; changed = True
    new2 = re.sub(r"demo\.launch\([^)]*\)", "demo.launch(server_name='0.0.0.0',server_port=args.port,share=True,show_error=True)", txt)
    if new2 != txt:
        txt = new2; changed = True
    if changed:
        open(webui_file, "w", encoding="utf-8").write(txt)
        print("  ✅ [D] webui.py 패치")

# 패치E: model.py autocast 비활성화
model_py = f"{cosy_dir}/cosyvoice/cli/model.py"
if os.path.exists(model_py):
    txt = open(model_py, encoding="utf-8").read()
    new2 = txt.replace("torch.cuda.amp.autocast(self.fp16)", "torch.cuda.amp.autocast(enabled=False)")
    if new2 != txt:
        open(model_py, "w", encoding="utf-8").write(new2)
        print("  ✅ [E] model.py autocast 비활성화")

# torchaudio.info 보완
try:
    import torchaudio as _ta
    code = open(_ta.__file__, encoding="utf-8").read()
    if "info = lambda" not in code:
        with open(_ta.__file__, "a", encoding="utf-8") as f:
            f.write("\nimport soundfile as _sf\nclass _AudioMetaData:\n    def __init__(self,s): self.sample_rate=s\ninfo=lambda p,**kw:_AudioMetaData(_sf.info(p).samplerate)\n")
        print("  ✅ [F] torchaudio.info 패치")
except:
    pass

print("✅ [3/4] 패치 완료")
sys.stdout.flush()

# ── [4/4] 서버 시작 ────────────────────────────────────────
print("\n⏳ [4/4] Cloudflare 터널 + 서버 시작...")
sys.stdout.flush()

tunnel_log = "/content/cosy_tunnel.log"
subprocess.Popen(
    f"cloudflared tunnel --url http://127.0.0.1:50000 --logfile {tunnel_log} >/dev/null 2>&1",
    shell=True
)
time.sleep(6)

public_url = None
if os.path.exists(tunnel_log):
    for line in open(tunnel_log):
        m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if m:
            public_url = m.group(0)
            break

print("\n" + "=" * 60)
print("🎉 준비 완료! 서버 기동 중...")
if public_url:
    print(f"🌐 Cloudflare 주소: {public_url}")
print("💡 아래 gradio.live 링크를 AI Voice Studio에 입력하세요!")
print("=" * 60 + "\n")
sys.stdout.flush()

os.chdir(cosy_dir)
os.system("python3 webui.py --port 50000 --model_dir pretrained_models/CosyVoice2-0.5B")
