"""Easing functions and keyframe helpers for cinematic motion."""
import math

def clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)

def clamp(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)

# ---- primitive easings ----
def linear(t):
    return clamp01(t)

def ease_in_quad(t):
    t = clamp01(t); return t * t

def ease_out_quad(t):
    t = clamp01(t); return 1 - (1 - t) * (1 - t)

def ease_in_out_quad(t):
    t = clamp01(t)
    return 2 * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 2) / 2

def ease_in_cubic(t):
    t = clamp01(t); return t * t * t

def ease_out_cubic(t):
    t = clamp01(t); return 1 - (1 - t) ** 3

def ease_in_out_cubic(t):
    t = clamp01(t)
    return 4 * t ** 3 if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2

def ease_in_quart(t):
    t = clamp01(t); return t ** 4

def ease_out_quart(t):
    t = clamp01(t); return 1 - (1 - t) ** 4

def ease_in_out_quart(t):
    t = clamp01(t)
    return 8 * t ** 4 if t < 0.5 else 1 - ((-2 * t + 2) ** 4) / 2

def ease_in_expo(t):
    t = clamp01(t); return 0.0 if t == 0 else 2 ** (10 * t - 10)

def ease_out_expo(t):
    t = clamp01(t); return 1.0 if t == 1 else 1 - 2 ** (-10 * t)

def ease_in_out_expo(t):
    t = clamp01(t)
    if t == 0 or t == 1: return t
    if t < 0.5: return (2 ** (20 * t - 10)) / 2
    return (2 - 2 ** (-20 * t + 10)) / 2

def ease_out_back(t, s=1.70158):
    t = clamp01(t); t -= 1
    return 1 + (s + 1) * t ** 3 + s * t ** 2

def ease_in_out_back(t, s=1.70158):
    t = clamp01(t)
    c2 = s * 1.525
    if t < 0.5:
        return ((2 * t) ** 2 * ((c2 + 1) * 2 * t - c2)) / 2
    return ((2 * t - 2) ** 2 * ((c2 + 1) * (2 * t - 2) + c2) + 2) / 2

def ease_out_elastic(t):
    t = clamp01(t)
    if t == 0 or t == 1: return t
    c4 = (2 * math.pi) / 3
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * c4) + 1

def ease_in_out_elastic(t):
    t = clamp01(t)
    if t == 0 or t == 1: return t
    c5 = (2 * math.pi) / 4.5
    if t < 0.5:
        return -(2 ** (20 * t - 10) * math.sin((20 * t - 11.125) * c5)) / 2
    return (2 ** (-20 * t + 10) * math.sin((20 * t - 11.125) * c5)) / 2 + 1

# damped-spring settle (overshoot + settle to 1)
def ease_out_spring(t, stiffness=12.0, damping=2.6):
    t = clamp01(t)
    w = math.sqrt(max(stiffness, 1e-6))
    return 1 - math.exp(-damping * t) * math.cos(w * t)

# ---- cubic bezier (CSS style) ----
def _bezier_xy(p, t):
    mt = 1 - t
    a = mt ** 3
    b = 3 * mt * mt * t
    c = 3 * mt * t * t
    d = t ** 3
    x = a * p[0][0] + b * p[1][0] + c * p[2][0] + d * p[3][0]
    y = a * p[0][1] + b * p[1][1] + c * p[2][1] + d * p[3][1]
    return x, y

def cubic_bezier(x1, y1, x2, y2):
    p = [(0, 0), (x1, y1), (x2, y2), (1, 1)]
    def fn(t):
        t = clamp01(t)
        # solve param u for given x using Newton
        u = t
        for _ in range(12):
            xu, _ = _bezier_xy(p, u)
            # derivative dx/du
            mt = 1 - u
            dx = 3 * mt * mt * (p[1][0] - p[0][0]) + 6 * mt * u * (p[2][0] - p[1][0]) + 3 * u * u * (p[3][0] - p[2][0])
            if abs(dx) < 1e-6:
                break
            u = u - (xu - t) / dx
            u = clamp01(u)
        _, y = _bezier_xy(p, u)
        return y
    return fn

# named presets
EASE_IN_OUT = ease_in_out_cubic
EASE_OUT = ease_out_cubic
EASE_OUT_SOFT = cubic_bezier(0.25, 0.1, 0.25, 1.0)
EASE_SMOOTH = cubic_bezier(0.4, 0.0, 0.2, 1.0)
EASE_SNAP = cubic_bezier(0.2, 0.8, 0.2, 1.0)

# ---- helpers ----
def lerp(a, b, t):
    return a + (b - a) * t

def smoothstep(t):
    t = clamp01(t); return t * t * (3 - 2 * t)

def smootherstep(t):
    t = clamp01(t); return t * t * t * (t * (t * 6 - 15) + 10)

# ---- keyframe sampling ----
# each keyframe: dict(t=time, v=value, e=easing[optional], v2=optional end value)
def sample_keyframes(kfs, t, default_ease=EASE_IN_OUT):
    if not kfs:
        return 0.0
    if t <= kfs[0]["t"]:
        return kfs[0]["v"]
    if t >= kfs[-1]["t"]:
        return kfs[-1]["v"]
    for i in range(len(kfs) - 1):
        a, b = kfs[i], kfs[i + 1]
        if a["t"] <= t <= b["t"]:
            span = b["t"] - a["t"]
            if span <= 0:
                return b["v"]
            e = b.get("e", a.get("e", default_ease))
            local = (t - a["t"]) / span
            f = e(local)
            v1 = a["v"]
            v2 = b.get("v2", b["v"])
            return lerp(v1, v2, f)
    return kfs[-1]["v"]
