"""Global render configuration and shared constants."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

W, H = 1920, 1080
FPS = 30
DURATION = 25.0
TOTAL_FRAMES = int(DURATION * FPS)

OUTPUT_DIR = os.path.join(ROOT, "output")
AUDIO_DIR = os.path.join(ROOT, "audio")
QC_DIR = os.path.join(OUTPUT_DIR, "qc")
FINAL_MP4 = os.path.join(OUTPUT_DIR, "smartfeedback_scene.mp4")
FINAL_WAV = os.path.join(AUDIO_DIR, "score.wav")

# palette
BG_TOP = (5, 7, 13)
BG_BOTTOM = (9, 12, 24)
CYAN = (0, 210, 255)
CYAN_SOFT = (60, 190, 240)
VIOLET = (138, 92, 255)
VIOLET_SOFT = (120, 100, 235)
MAGENTA = (255, 70, 170)
WHITE = (235, 244, 255)
WHITE_DIM = (170, 185, 205)
GREEN = (90, 255, 180)
AMBER = (255, 190, 90)

def world_to_screen(wx, wy, cam, parallax=1.0):
    cx, cy, zoom = cam
    sx = W / 2.0 + (wx - cx) * zoom * parallax
    sy = H / 2.0 + (wy - cy) * zoom * parallax
    return sx, sy
