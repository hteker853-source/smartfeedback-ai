"""Dark cinematic background: gradient, vignette, ambient glow, dust."""
import math
import numpy as np
import random

from . import gfx, config

BG_TOP = config.BG_TOP
BG_BOTTOM = config.BG_BOTTOM
CYAN = config.CYAN
VIOLET = config.VIOLET
W, H = config.W, config.H

_static = None

def make_base():
    """Pre-render the static background: gradient + subtle glow + vignette."""
    global _static
    if _static is not None:
        return _static
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    # vertical gradient
    top = np.array(BG_TOP, dtype=np.float32)
    bot = np.array(BG_BOTTOM, dtype=np.float32)
    base = top[None, None, :] * (1 - yy[..., None]) + bot[None, None, :] * yy[..., None]

    # faint baked ambient glows (screen)
    def blob(cx, cy, r, color, strength):
        nonlocal base
        d2 = ((xx - cx) ** 2 + (yy - cy) ** 2) / (r * r)
        a = np.exp(-d2 * 2.5) * strength
        col = np.array(color, dtype=np.float32)
        l = a[..., None] * col[None, None, :]
        b = base / 255.0
        ll = np.clip(l, 0, 255) / 255.0
        base = (1 - (1 - b) * (1 - ll)) * 255.0

    blob(0.22, 0.22, 0.55, CYAN, 0.26)
    blob(0.80, 0.78, 0.60, VIOLET, 0.28)
    blob(0.50, 0.35, 0.70, (40, 70, 130), 0.13)
    # ambient fields behind the agent nodes (so glass blur reads clearly)
    blob(0.50, 0.50, 0.52, (90, 160, 220), 0.24)
    blob(0.28, 0.28, 0.34, CYAN, 0.20)
    blob(0.73, 0.33, 0.36, VIOLET, 0.21)
    blob(0.50, 0.74, 0.36, VIOLET, 0.18)

    # vignette
    d = np.sqrt((xx - 0.5) ** 2 + (yy - 0.5) ** 2)
    vig = np.clip(1.0 - (d / 0.75) ** 3, 0.0, 1.0)
    base = base * (0.50 + 0.50 * vig[..., None])

    base = np.clip(base, 0, 255).astype(np.uint8)
    _static = base
    return _static

# ---- ambient drift blobs (drawn additively each frame) ----
def ambient_light(light, t):
    # two slow drifting color fields
    a = 0.5 + 0.5 * math.sin(t * 0.25)
    b = 0.5 + 0.5 * math.cos(t * 0.21 + 1.3)
    x1 = W * (0.2 + 0.06 * math.sin(t * 0.13))
    y1 = H * (0.25 + 0.08 * math.cos(t * 0.11))
    x2 = W * (0.82 + 0.05 * math.cos(t * 0.09))
    y2 = H * (0.76 + 0.06 * math.sin(t * 0.12))
    gfx.paste_glow(light, x1, y1, 520 + 40 * a, CYAN, 0.22 + 0.06 * a)
    gfx.paste_glow(light, x2, y2, 560 + 40 * b, VIOLET, 0.24 + 0.06 * b)

# ---- depth dust ----
_dust = None

def _make_dust():
    global _dust
    if _dust is not None:
        return _dust
    rng = random.Random(42)
    dust = []
    for _ in range(130):
        dust.append({
            "x": rng.uniform(-0.1, 1.1) * W,
            "y": rng.uniform(-0.05, 1.05) * H,
            "z": rng.uniform(0.4, 1.6),       # parallax depth
            "r": rng.uniform(1.0, 3.4),
            "b": rng.uniform(0.04, 0.16),
            "c": rng.choice([CYAN, VIOLET, config.WHITE_DIM, config.WHITE]),
            "ph": rng.uniform(0, 6.28),
        })
    _dust = dust
    return _dust

def draw_dust(light, t, cam):
    dust = _make_dust()
    cx, cy, zoom = cam
    for p in dust:
        tw = 0.5 + 0.5 * math.sin(t * 0.4 + p["ph"])
        sx = W / 2 + (p["x"] - cx) * zoom * p["z"]
        sy = H / 2 + (p["y"] - cy) * zoom * p["z"]
        if sx < -20 or sx > W + 20 or sy < -20 or sy > H + 20:
            continue
        r = p["r"] * zoom * p["z"] * 1.6
        if r < 0.6:
            r = 0.6
        gfx.paste_glow(light, sx, sy, r, p["c"], p["b"] * (0.6 + 0.4 * tw))

def draw_background(light, t, cam):
    ambient_light(light, t)
    draw_dust(light, t, cam)
