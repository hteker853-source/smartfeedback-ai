"""Render pipeline: build frames -> pipe to ffmpeg (H.264 + AAC)."""
import os
import sys
import time
import subprocess
import argparse

from . import config, scene, score

def ensure_dirs():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.AUDIO_DIR, exist_ok=True)
    os.makedirs(config.QC_DIR, exist_ok=True)

QC_TIMES = [0.6, 1.8, 3.4, 4.2, 5.6, 7.8, 10.2, 12.5, 16.8, 20.4, 22.5, 24.0]

def render_qc():
    ensure_dirs()
    for i, t in enumerate(QC_TIMES):
        frame = scene.build_frame(t)
        p = os.path.join(config.QC_DIR, f"qc_{t:05.2f}.png")
        frame.save(p)
        print(f"[qc] saved {p}")

def render_video():
    ensure_dirs()
    print("[audio] synthesizing score ...")
    wav = score.render_wav()
    print(f"[audio] wrote {wav}")

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{config.W}x{config.H}", "-r", str(config.FPS),
        "-i", "-",
        "-i", wav,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        config.FINAL_MP4,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    total = config.TOTAL_FRAMES
    t0 = time.time()
    qc_done = set()
    for i in range(total):
        t = i / config.FPS
        frame = scene.build_frame(t)
        proc.stdin.write(frame.tobytes())
        # save QC frames if not already
        for qt in QC_TIMES:
            if qt not in qc_done and abs(t - qt) < 0.5 / config.FPS:
                qc_done.add(qt)
                frame.save(os.path.join(config.QC_DIR, f"qc_{qt:05.2f}.png"))
        if i % 30 == 0:
            el = time.time() - t0
            fps_render = (i + 1) / max(el, 1e-6)
            eta = (total - i - 1) / max(fps_render, 1e-6)
            print(f"[render] frame {i}/{total}  {t:5.2f}s  ~{fps_render:.1f} fps  ETA {eta:.0f}s", flush=True)

    proc.stdin.close()
    err = proc.stderr.read().decode(errors="replace")
    proc.wait()
    if proc.returncode != 0:
        print("[error] ffmpeg failed:")
        print(err[-4000:])
        sys.exit(1)
    print(f"[done] {config.FINAL_MP4}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qc", action="store_true", help="render QC frames only (fast visual check)")
    args = ap.parse_args()
    if args.qc:
        render_qc()
    else:
        render_video()

if __name__ == "__main__":
    main()
