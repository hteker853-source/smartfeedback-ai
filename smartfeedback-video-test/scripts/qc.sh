#!/usr/bin/env bash
# Quality control: verify the produced MP4 and analyze frames.
set -e
cd "$(dirname "$0")/.."

MP4=output/smartfeedback_scene.mp4
echo "=== MP4 metadata ==="
ffprobe -v error \
  -show_entries format=duration,size,bit_rate \
  -show_entries stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels \
  -of default=noprint_wrappers=1 "$MP4"

echo
echo "=== Frame analysis (from renderer) ==="
python3 - "$MP4" <<'PY'
import sys, numpy as np
from PIL import Image
sys.path.insert(0, ".")
from src import scene, config

print(f"resolution {config.W}x{config.H}  fps {config.FPS}  duration {config.DURATION}s")
for t in [1.8, 4.2, 7.8, 12.5, 16.8, 20.4, 22.5]:
    a = np.asarray(scene.build_frame(t).convert("L")).astype(np.float32)
    print(f"  t={t:5} mean={a.mean():5.1f}  std={a.std():5.1f}  max={a.max():3.0}")

# temporal activity (fraction of pixels changing significantly between 0.25s samples)
prev = None
dead = []
for i in range(0, 100):
    t = i * 0.25
    f = np.asarray(scene.build_frame(t).convert("L")).astype(np.float32)
    if prev is not None:
        pct = (np.abs(f - prev) > 20).mean() * 100
        if pct < 0.10:
            dead.append((t - 0.25, t))
    prev = f
print("low-activity windows (<0.10% changed):")
for a, b in dead:
    print(f"  {a:.2f}-{b:.2f}s")
PY
