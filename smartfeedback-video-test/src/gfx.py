"""Low-level graphics primitives: glows, glass, blend, paths, rings."""
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

# ---------------- glow sprites (additive light) ----------------
_glow_cache = {}

def _glow_alpha(radius, falloff=3.0):
    radius = max(radius, 1)
    d = int(2 * radius) + 1
    c = (d - 1) / 2.0
    yy, xx = np.mgrid[0:d, 0:d].astype(np.float32)
    dist = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / radius
    a = np.exp(-(dist ** 2) * falloff)
    a = np.clip(a, 0.0, 1.0)
    a = a ** 1.0
    return a

def get_glow(radius, falloff=3.0):
    key = (round(radius, 1), falloff)
    if key not in _glow_cache:
        _glow_cache[key] = _glow_alpha(round(radius, 1), falloff)
    return _glow_cache[key]

# ---------------- additive light buffer ----------------
def new_light(h, w):
    return np.zeros((h, w, 3), dtype=np.float32)

def paste_glow(light, cx, cy, radius, color, intensity=1.0):
    """Add a soft radial glow (additive) into the light buffer."""
    if radius <= 0 or intensity <= 0:
        return
    alpha = get_glow(radius)
    d = alpha.shape[0]
    half = d // 2
    x0 = int(round(cx)) - half
    y0 = int(round(cy)) - half
    x1 = x0 + d
    y1 = y0 + d
    # clip
    H, W = light.shape[:2]
    sx0, sy0 = max(x0, 0), max(y0, 0)
    sx1, sy1 = min(x1, W), min(y1, H)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    ax0 = sx0 - x0
    ay0 = sy0 - y0
    ax1 = ax0 + (sx1 - sx0)
    ay1 = ay0 + (sy1 - sy0)
    a = alpha[ay0:ay1, ax0:ax1]
    col = np.array(color, dtype=np.float32)
    light[sy0:sy1, sx0:sx1] += (a[..., None] * col[None, None, :]) * intensity

def paste_sprite_glow(light, spr_alpha, cx, cy, color, intensity=1.0):
    """Paste a pre-made alpha sprite (numpy 2D) additively."""
    d = spr_alpha.shape[0]
    half = d // 2
    x0 = int(round(cx)) - half
    y0 = int(round(cy)) - half
    H, W = light.shape[:2]
    x1, y1 = x0 + d, y0 + d
    sx0, sy0 = max(x0, 0), max(y0, 0)
    sx1, sy1 = min(x1, W), min(y1, H)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    a = spr_alpha[(sy0 - y0):(sy1 - y0), (sx0 - x0):(sx1 - x0)]
    col = np.array(color, dtype=np.float32)
    light[sy0:sy1, sx0:sx1] += (a[..., None] * col[None, None, :]) * intensity

# ---------------- blending ----------------
def screen_blend(base, light, light_strength=1.0):
    """Screen blend an additive light buffer over an RGB numpy base (uint8)."""
    b = base.astype(np.float32) / 255.0
    l = np.clip(light, 0.0, 255.0) / 255.0 * light_strength
    out = 1.0 - (1.0 - b) * (1.0 - l)
    out = np.clip(out, 0.0, 1.0)
    return (out * 255.0).astype(np.uint8)

# ---------------- rounded rect + glass ----------------
def rounded_mask(size, radius):
    m = Image.new("L", (int(size[0]), int(size[1])), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return m

def glass_card(img, box, radius=28, blur=16, tint=(255, 255, 255), tint_alpha=16,
               border_alpha=55, brighten=1.10, border_color=(255, 255, 255)):
    """Composite a glassmorphism panel over an RGB/RGBA image (backdrop blur)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    pad = blur + 10
    cw, ch = w + pad * 2, h + pad * 2
    region = img.crop((max(0, x0 - pad), max(0, y0 - pad),
                       min(img.width, x1 + pad), min(img.height, y1 + pad)))
    region = region.filter(ImageFilter.GaussianBlur(blur))
    region = ImageEnhance.Brightness(region).enhance(brighten)
    if region.size != (cw, ch):
        region = region.resize((cw, ch), Image.BILINEAR)

    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    card.paste(region, (-pad, -pad))

    # translucent white tint
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=(*tint, tint_alpha))
    # top edge highlight (glass sheen)
    grad = Image.new("L", (w, h), 0)
    for y in range(h):
        v = int(255 * max(0.0, 1.0 - y / max(1, h * 0.5)) * 0.5)
        grad.paste(v, (0, y, w, y + 1))
    sheen_mask = rounded_mask((w, h), radius)
    sheen = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    white = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    white.putalpha(grad)
    sheen.paste(white, (0, 0), sheen_mask)
    card = Image.alpha_composite(card, sheen)
    card = Image.alpha_composite(card, overlay)

    # border
    bd = ImageDraw.Draw(card)
    bd.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius,
                         outline=(*border_color, border_alpha), width=1)

    img.paste(card, (x0, y0), card)

# ---------------- bezier paths ----------------
def quad_bezier(p0, p1, p2, n):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
        y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts

def quad_point(p0, p1, p2, t):
    mt = 1 - t
    x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
    y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
    return (x, y)

# ---------------- progress ring ----------------
def draw_ring(img, center, radius, progress, width, color, track_color=None,
              track_alpha=40, glow=False, start_angle=-90):
    """Draw a circular progress ring with optional glow track."""
    x, y = center
    box = [x - radius, y - radius, x + radius, y + radius]
    d = ImageDraw.Draw(img)
    if track_color is not None:
        d.ellipse(box, outline=(*track_color, track_alpha), width=width)
    else:
        d.ellipse(box, outline=(*color, track_alpha), width=width)
    if progress > 0:
        end = start_angle + 360 * progress
        d.arc(box, start_angle, end, fill=(*color, 255), width=width)
