"""Node, chip, card drawing (glass bodies, glows, labels, text)."""
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import config, gfx, fonts, timeline, easings as E

W, H = config.W, config.H
CYAN = config.CYAN
VIOLET = config.VIOLET
WHITE = config.WHITE
WHITE_DIM = config.WHITE_DIM
GREEN = config.GREEN
AMBER = config.AMBER

CHIP_COLORS = {"amber": AMBER, "cyan": CYAN, "violet": VIOLET}

AGENTS = {
    "FEEDBACK":        {"color": CYAN,          "count": 128,  "p": 0.62, "t0": 10.1, "ramp_end": 14.2},
    "INVESTIGATION":   {"color": VIOLET,        "count": 342,  "p": 1.00, "t0": 10.6, "ramp_end": 17.9},
    "CUSTOMER_TREND":  {"color": (150, 120, 255), "count": 1024, "p": 0.48, "t0": 11.1, "ramp_end": 14.2},
}

SUPERVISOR_R = 84.0
AGENT_R = 62.0
SUP_COLOR = (180, 230, 255)

T = timeline.T

def ramp(t, t0, t1, e=E.ease_out_cubic):
    if t <= t0: return 0.0
    if t >= t1: return 1.0
    return e((t - t0) / (t1 - t0))

def network_fade(t):
    return 1.0 - 0.84 * ramp(t, 19.5, 20.9)

def agent_active(t, t0):
    if t < t0: return 0.0
    dt = t - t0
    if dt < 0.4:
        return E.ease_out_back(dt / 0.4)
    return 0.5 + 0.5 * math.exp(-(dt - 0.4) / 3.0)

def _paste(img, spr, x, y, alpha=1.0, center=True):
    if alpha <= 0.0:
        return
    if center:
        x -= spr.width // 2
        y -= spr.height // 2
    if alpha < 1.0:
        a = spr.getchannel("A").point(lambda v: int(v * alpha))
        spr = spr.copy()
        spr.putalpha(a)
    img.paste(spr, (int(x), int(y)), spr)

# ================= glass pass =================
def glass_pass(img, t, cam):
    draw_message_card(img, t, cam)
    draw_supervisor(img, t, cam)
    for name in timeline.AGENT_ORDER:
        draw_agent_body(img, t, cam, name)
    draw_chips(img, t, cam)
    draw_decision_card(img, t, cam)

def _glass_disc(img, cx, cy, r, blur, tint_alpha=18, border_alpha=60):
    gfx.glass_card(img, (int(cx - r), int(cy - r), int(cx + r), int(cy + r)),
                   radius=int(r), blur=blur, tint_alpha=tint_alpha, border_alpha=border_alpha)

def draw_message_card(img, t, cam):
    alpha = 1.0
    if t < T["fade_in"]:
        alpha = ramp(t, 0.0, T["fade_in"])
    if t > T["decompose"]:
        alpha = 1.0 - ramp(t, T["decompose"], T["decompose"] + 0.7)
    if alpha <= 0.01:
        return
    sx, sy = config.world_to_screen(*timeline.LAYOUT["message"], cam)
    # slight scale-in
    sc = 0.9 + 0.1 * ramp(t, T["message_form"], T["message_done"], E.ease_out_back)
    w = 560 * cam[2] * sc
    h = 150 * cam[2] * sc
    if w < 10:
        return
    # pre-blur a copy region for glass only if alpha meaningful
    if alpha > 0.05:
        _glass_disc_rect(img, sx - w/2, sy - h/2, w, h, blur=14, tint_alpha=int(20*alpha), border_alpha=int(60*alpha))

def _glass_disc_rect(img, x0, y0, w, h, blur, tint_alpha, border_alpha):
    gfx.glass_card(img, (int(x0), int(y0), int(x0+w), int(y0+h)),
                   radius=int(min(w, h) * 0.36), blur=blur, tint_alpha=tint_alpha, border_alpha=border_alpha)

def draw_supervisor(img, t, cam):
    a = ramp(t, 5.2, 6.7) * network_fade(t)
    if a <= 0.01:
        return
    sx, sy = config.world_to_screen(*timeline.LAYOUT["supervisor"], cam)
    r = SUPERVISOR_R * cam[2] * (0.55 + 0.45 * a)
    _glass_disc(img, sx, sy, r, blur=14, tint_alpha=int(20 * a), border_alpha=int(70 * a))

def draw_agent_body(img, t, cam, name):
    i = timeline.AGENT_ORDER.index(name)
    a = ramp(t, 6.9 + i * 0.25, 7.8 + i * 0.25) * network_fade(t)
    if a <= 0.01:
        return
    sx, sy = config.world_to_screen(*timeline.LAYOUT["agents"][name], cam)
    r = AGENT_R * cam[2] * (0.55 + 0.45 * a)
    _glass_disc(img, sx, sy, r, blur=12, tint_alpha=int(16 * a), border_alpha=int(55 * a))

def draw_chips(img, t, cam):
    names = ["SENTIMENT", "SATISFACTION", "CATEGORY"]
    for i, name in enumerate(names):
        a = ramp(t, T["decompose"] + 0.15 + i * 0.18, T["chips_settle"] + i * 0.1, E.ease_out_back)
        fade = 1.0
        if t > 6.3:
            fade = 1.0 - ramp(t, 6.3, 7.0)
        if a <= 0.01 or fade <= 0.01:
            continue
        tgt = timeline.LAYOUT["chips"][name]
        src = timeline.LAYOUT["message"]
        # interpolate position message -> target with overshoot
        sxw, syw = E.lerp(src[0], tgt[0], a), E.lerp(src[1], tgt[1], a)
        sx, sy = config.world_to_screen(sxw, syw, cam)
        w = 250 * cam[2] * a
        h = 72 * cam[2] * a
        if w < 6:
            continue
        gfx.glass_card(img, (int(sx - w/2), int(sy - h/2), int(sx + w/2), int(sy + h/2)),
                       radius=14, blur=10, tint_alpha=int(16 * fade), border_alpha=int(55 * fade))

def draw_decision_card(img, t, cam):
    a = ramp(t, T["decision_form"], T["decision_impact"], E.ease_out_back)
    if a <= 0.01:
        return
    sx, sy = config.world_to_screen(*timeline.LAYOUT["decision"], cam)
    w = 620 * cam[2] * a
    h = 210 * cam[2] * a
    if w < 8:
        return
    gfx.glass_card(img, (int(sx - w/2), int(sy - h/2), int(sx + w/2), int(sy + h/2)),
                   radius=22, blur=18, tint_alpha=int(24 * a), border_alpha=int(80 * a))

# ================= light pass =================
def light_pass(light, t, cam):
    # supervisor core + agents core glows + chips glows
    draw_supervisor_light(light, t, cam)
    for name in timeline.AGENT_ORDER:
        draw_agent_light(light, t, cam, name)
    draw_chip_light(light, t, cam)
    draw_decision_light(light, t, cam)

def draw_supervisor_light(light, t, cam):
    a = ramp(t, 5.2, 6.7) * network_fade(t)
    if a <= 0.01:
        return
    sx, sy = config.world_to_screen(*timeline.LAYOUT["supervisor"], cam)
    r = SUPERVISOR_R * cam[2]
    energy = 0.55 * a
    # dispatch pulses add energy
    for i, name in enumerate(timeline.AGENT_ORDER):
        at = T["dispatch"] + i * T["dispatch_stagger"]
        if t >= at:
            energy += 0.3 * math.exp(-(t - at) * 2.2)
    gfx.paste_glow(light, sx, sy, r * 1.35, SUP_COLOR, 0.5 * energy)
    gfx.paste_glow(light, sx, sy, r * 0.55, CYAN, 0.5 * energy)
    gfx.paste_glow(light, sx, sy, r * 0.25, WHITE, 0.7 * energy)

def draw_agent_light(light, t, cam, name):
    i = timeline.AGENT_ORDER.index(name)
    a = ramp(t, 6.9 + i * 0.25, 7.8 + i * 0.25) * network_fade(t)
    if a <= 0.01:
        return
    meta = AGENTS[name]
    sx, sy = config.world_to_screen(*timeline.LAYOUT["agents"][name], cam)
    r = AGENT_R * cam[2]
    act = agent_active(t, meta["t0"])
    energy = (0.16 + 0.5 * act) * a
    if name == "INVESTIGATION":
        energy += 0.35 * ramp(t, T["investigate_focus"], T["investigate_peak"])
    gfx.paste_glow(light, sx, sy, r * 1.25, meta["color"], 0.55 * energy)
    gfx.paste_glow(light, sx, sy, r * 0.5, WHITE, 0.4 * energy)

def draw_chip_light(light, t, cam):
    names = ["SENTIMENT", "SATISFACTION", "CATEGORY"]
    for i, name in enumerate(names):
        a = ramp(t, T["decompose"] + 0.15 + i * 0.18, T["chips_settle"] + i * 0.1, E.ease_out_back)
        fade = 1.0
        if t > 6.3:
            fade = 1.0 - ramp(t, 6.3, 7.0)
        if a <= 0.01 or fade <= 0.01:
            continue
        tgt = timeline.LAYOUT["chips"][name]
        src = timeline.LAYOUT["message"]
        sxw, syw = E.lerp(src[0], tgt[0], a), E.lerp(src[1], tgt[1], a)
        sx, sy = config.world_to_screen(sxw, syw, cam)
        col = CHIP_COLORS[timeline.CHIP_DATA[name]["color"]]
        gfx.paste_glow(light, sx, sy, 46 * cam[2] * a, col, 0.5 * fade)

def draw_decision_light(light, t, cam):
    a = ramp(t, T["decision_form"], T["decision_impact"], E.ease_out_back)
    if a <= 0.01:
        return
    sx, sy = config.world_to_screen(*timeline.LAYOUT["decision"], cam)
    pulse = 0.85 + 0.15 * math.sin(t * 1.1)
    gfx.paste_glow(light, sx, sy, 220 * cam[2] * a, (140, 200, 255), 0.28 * a * pulse)

# ================= text pass =================
def text_pass(img, t, cam):
    draw_message_text(img, t, cam)
    draw_supervisor_label(img, t, cam)
    for name in timeline.AGENT_ORDER:
        draw_agent_text(img, t, cam, name)
    draw_chip_text(img, t, cam)
    draw_decision_text(img, t, cam)
    draw_brand(img, t)

def draw_message_text(img, t, cam):
    alpha = 1.0
    if t < T["fade_in"]:
        alpha = ramp(t, 0.0, T["fade_in"])
    if t > T["decompose"]:
        alpha = 1.0 - ramp(t, T["decompose"], T["decompose"] + 0.55)
    if alpha <= 0.01:
        return
    sx, sy = config.world_to_screen(*timeline.LAYOUT["message"], cam)
    full = "Patatesler soğuktu."
    # typing
    typed = int(round(len(full) * E.clamp((t - T["type_start"]) / (T["type_end"] - T["type_start"]), 0, 1)))
    if t < T["type_start"]:
        typed = 0
    txt = full[:typed]
    if typed == 0:
        return
    spr = fonts.cached_text(txt, "light", int(44 * cam[2]), WHITE)
    _paste(img, spr, sx, sy, alpha)
    # cursor
    if typed < len(full) and (int(t * 2.4) % 2 == 0):
        tw = fonts.text_size(txt, "light", int(44 * cam[2]))[0]
        cx = sx + tw // 2 + 6
        d = ImageDraw.Draw(img)
        d.line([(cx, sy - 22), (cx, sy + 22)], fill=(*CYAN, int(200 * alpha)), width=2)

def draw_supervisor_label(img, t, cam):
    a = ramp(t, 5.4, 6.8) * network_fade(t)
    if a <= 0.01:
        return
    sx, sy = config.world_to_screen(*timeline.LAYOUT["supervisor"], cam)
    r = SUPERVISOR_R * cam[2]
    label = fonts.cached_text("SUPERVISOR", "display-medium", int(22 * cam[2]), WHITE, tracking=5)
    _paste(img, label, sx, sy + r + 26 * cam[2], a)
    sub = fonts.cached_text("ORCHESTRATOR", "medium", int(13 * cam[2]), WHITE_DIM, tracking=3)
    _paste(img, sub, sx, sy + r + 54 * cam[2], a * 0.8)

def draw_agent_text(img, t, cam, name):
    i = timeline.AGENT_ORDER.index(name)
    a = ramp(t, 7.0 + i * 0.25, 7.9 + i * 0.25) * network_fade(t)
    if a <= 0.01:
        return
    meta = AGENTS[name]
    sx, sy = config.world_to_screen(*timeline.LAYOUT["agents"][name], cam)
    r = AGENT_R * cam[2]
    col = meta["color"]
    label = fonts.cached_text(name.replace("_", " "), "display-medium", int(20 * cam[2]), WHITE, tracking=3)
    _paste(img, label, sx, sy + r + 26 * cam[2], a)
    # ring + counter
    prog = agent_progress(t, name)
    gfx.draw_ring(img, (sx, sy), r + 12 * cam[2], prog, max(2, int(3 * cam[2])), col,
                  track_alpha=int(34 * a))
    count = int(prog * meta["count"])
    ctxt = f"{count:,}"
    cspr = fonts.cached_text(ctxt, "semibold", int(17 * cam[2]), col)
    _paste(img, cspr, sx, sy + r + 52 * cam[2], a)
    # status micro-copy
    if name == "INVESTIGATION":
        if t > T["analysis_done"]:
            stxt = "ANALYSIS COMPLETE"
            scol = GREEN
        elif t > meta["t0"]:
            stxt = "ANALYSING"
            scol = col
        else:
            stxt = None
        if stxt:
            spr2 = fonts.cached_text(stxt, "medium", int(13 * cam[2]), scol, tracking=2)
            _paste(img, spr2, sx, sy - r - 26 * cam[2], a)

def agent_progress(t, name):
    meta = AGENTS[name]
    t0, t1 = meta["t0"], meta["ramp_end"]
    if t <= t0:
        return 0.0
    if t >= t1:
        return meta["p"]
    return meta["p"] * E.ease_in_out_cubic((t - t0) / (t1 - t0))

def draw_chip_text(img, t, cam):
    names = ["SENTIMENT", "SATISFACTION", "CATEGORY"]
    for i, name in enumerate(names):
        a = ramp(t, T["decompose"] + 0.15 + i * 0.18, T["chips_settle"] + i * 0.1, E.ease_out_back)
        fade = 1.0
        if t > 6.3:
            fade = 1.0 - ramp(t, 6.3, 7.0)
        if a <= 0.01 or fade <= 0.01:
            continue
        tgt = timeline.LAYOUT["chips"][name]
        src = timeline.LAYOUT["message"]
        sxw, syw = E.lerp(src[0], tgt[0], a), E.lerp(src[1], tgt[1], a)
        sx, sy = config.world_to_screen(sxw, syw, cam)
        col = CHIP_COLORS[timeline.CHIP_DATA[name]["color"]]
        lab = fonts.cached_text(name, "medium", int(13 * cam[2]), WHITE_DIM, tracking=2)
        val = fonts.cached_text(timeline.CHIP_DATA[name]["value"], "semibold", int(18 * cam[2]), col)
        _paste(img, lab, sx, sy - 16 * cam[2], fade)
        _paste(img, val, sx, sy + 10 * cam[2], fade)

def draw_decision_text(img, t, cam):
    a = ramp(t, T["decision_form"], T["decision_impact"], E.ease_out_back)
    if a <= 0.01:
        return
    sx, sy = config.world_to_screen(*timeline.LAYOUT["decision"], cam)
    z = cam[2]
    kick = fonts.cached_text("SMARTFEEDBACK RESOLUTION", "medium", int(13 * z), WHITE_DIM, tracking=4)
    _paste(img, kick, sx, sy - 62 * z, a)
    title = fonts.cached_text("Kitchen heat-hold issue detected", "display-medium", int(27 * z), WHITE)
    _paste(img, title, sx, sy - 14 * z, a)
    sub = fonts.cached_text("Action routed → Operations", "regular", int(19 * z), CYAN)
    _paste(img, sub, sx, sy + 34 * z, a)
    # check mark
    if a > 0.6:
        ca = ramp(t, T["decision_impact"], T["decision_impact"] + 0.4)
        d = ImageDraw.Draw(img)
        ccx, ccy = sx - 210 * z, sy - 14 * z
        rr = 16 * z * (0.6 + 0.4 * ca)
        d.ellipse([ccx - rr, ccy - rr, ccx + rr, ccy + rr], outline=(*GREEN, int(220 * ca)), width=max(2, int(3 * z)))
        d.line([(ccx - 6 * z, ccy), (ccx - 1 * z, ccy + 6 * z), (ccx + 7 * z, ccy - 6 * z)],
               fill=(*GREEN, int(230 * ca)), width=max(2, int(3 * z)))

def draw_brand(img, t):
    a = ramp(t, T["brand_in"], T["brand_in"] + 1.0)
    if a <= 0.01:
        return
    b = fonts.cached_text("SmartFeedback", "display-bold", 30, WHITE)
    ai = fonts.cached_text("AI", "display-medium", 30, CYAN)
    _paste(img, b, 60 + b.width // 2, 60, a)
    _paste(img, ai, 60 + b.width + ai.width // 2 + 8, 60, a)
    tag = fonts.cached_text("agentic feedback intelligence", "regular", 15, WHITE_DIM)
    _paste(img, tag, 60 + tag.width // 2, 60 + 40, a * 0.85)
