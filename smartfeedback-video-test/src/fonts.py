"""Font loading and text sprite rendering with letter-spacing support."""
import os
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

# style -> (family, weight)
_FONT_FILES = {
    # Inter family
    "light":    ("Inter-Light.ttf",),
    "regular":  ("Inter-Regular.ttf",),
    "medium":   ("Inter-Medium.ttf",),
    "semibold": ("Inter-SemiBold.ttf",),
    "bold":     ("Inter-Bold.ttf",),
    # Space Grotesk (display) family
    "display-light": ("SpaceGrotesk-Light.ttf",),
    "display":       ("SpaceGrotesk-Regular.ttf",),
    "display-medium":("SpaceGrotesk-Medium.ttf",),
    "display-bold":  ("SpaceGrotesk-Bold.ttf",),
}

_cache = {}

def font(style, size):
    key = (style, int(size))
    if key in _cache:
        return _cache[key]
    fname = _FONT_FILES[style][0]
    path = os.path.join(FONT_DIR, fname)
    f = ImageFont.truetype(path, int(size))
    _cache[key] = f
    return f

def text_size(text, style, size, tracking=0.0):
    f = font(style, size)
    if tracking == 0.0:
        return f.getbbox(text)[2:]
    total = 0
    for ch in text:
        w = f.getlength(ch)
        total += w
    total += tracking * max(len(text) - 1, 0)
    asc, desc = f.getmetrics()
    return (int(round(total)), asc + desc)

def _draw_tracked(draw, pos, text, f, fill, tracking):
    x, y = pos
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += f.getlength(ch) + tracking

def text_sprite(text, style, size, color, tracking=0.0, stroke=0, stroke_color=None):
    """Render text to a transparent RGBA image (with optional letter-spacing)."""
    f = font(style, size)
    w, h = text_size(text, style, size, tracking)
    pad = max(stroke, 2) + 2
    img = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if tracking == 0.0:
        d.text((pad, pad), text, font=f, fill=color, stroke_width=stroke, stroke_fill=stroke_color)
    else:
        _draw_tracked(d, (pad, pad), text, f, color, tracking)
    return img

_text_cache = {}

def cached_text(text, style, size, color, tracking=0.0):
    key = (text, style, int(size), tuple(color), round(tracking, 2))
    if key not in _text_cache:
        _text_cache[key] = text_sprite(text, style, size, color, tracking)
    return _text_cache[key]
