"""Scene orchestrator: composes background, nodes, effects, and post-processing."""
import numpy as np
from PIL import Image

from . import background, config, gfx, nodes, particles, timeline, easings as E

W, H = config.W, config.H
CYAN = config.CYAN
VIOLET = config.VIOLET
WHITE = config.WHITE
GREEN = config.GREEN
T = timeline.T
L = timeline.LAYOUT

_effects = None

def _mid(p0, p2, pull):
    mx = (p0[0] + p2[0]) / 2.0
    my = (p0[1] + p2[1]) / 2.0
    dx = mx - p0[0]
    dy = my - p0[1]
    return (mx + dx * pull, my + dy * pull)

def _build_effects():
    global _effects
    if _effects is not None:
        return _effects
    streams = []
    bursts = []
    orbits = []
    ripples = []

    chip_names = ["SENTIMENT", "SATISFACTION", "CATEGORY"]
    for i, name in enumerate(chip_names):
        col = nodes.CHIP_COLORS[timeline.CHIP_DATA[name]["color"]]
        p0 = L["chips"][name]
        p2 = L["supervisor"]
        c = _mid(p0, p2, 0.5)
        streams.append(particles.Stream(
            p0, c, p2, col, size=3.2, travel=0.85, spacing=0.20,
            t0=T["stream_in"] + i * 0.24, t1=6.9, trail_intensity=0.9))

    for i, name in enumerate(timeline.AGENT_ORDER):
        col = nodes.AGENTS[name]["color"]
        p0 = L["supervisor"]
        p2 = L["agents"][name]
        c = _mid(p0, p2, 0.35)
        streams.append(particles.Stream(
            p0, c, p2, col, size=4.4, travel=1.0, spacing=0.19,
            t0=T["dispatch"] + i * T["dispatch_stagger"], t1=14.6, trail_intensity=1.2))

    # shard bursts
    bursts.append(particles.ShardBurst(L["message"], T["decompose"], WHITE, count=48, speed=0.9, size=2.8, seed=5))
    bursts.append(particles.ShardBurst(L["supervisor"], T["supervisor_reveal"], CYAN, count=30, speed=0.7, size=2.2, seed=7))
    bursts.append(particles.ShardBurst(L["decision"], T["decision_impact"], CYAN, count=26, speed=0.6, size=2.0, seed=9))

    # orbits while processing
    for i, name in enumerate(timeline.AGENT_ORDER):
        meta = nodes.AGENTS[name]
        orbits.append(particles.Orbit(
            L["agents"][name], meta["t0"] + 0.6, 15.0, meta["color"],
            nodes.AGENT_R + 24, count=13, seed=20 + i))
    orbits.append(particles.Orbit(
        L["agents"]["INVESTIGATION"], 15.2, 18.6, VIOLET,
        nodes.AGENT_R + 30, count=18, seed=40))

    # ripple pulses while agents process
    for i, name in enumerate(timeline.AGENT_ORDER):
        meta = nodes.AGENTS[name]
        k = 0
        t0 = meta["t0"] + 0.5
        while t0 < 14.8:
            ripples.append(particles.Ripple(
                L["agents"][name], t0, meta["color"], nodes.AGENT_R + 34, dur=1.4))
            k += 1
            t0 = meta["t0"] + 0.5 + k * 1.3
    # supervisor ripple on dispatch
    ripples.append(particles.Ripple(L["supervisor"], T["dispatch"], CYAN, 130, dur=1.6, points=28))

    _effects = (streams, bursts, orbits, ripples)
    return _effects

def draw_effects(light, t, cam):
    streams, bursts, orbits, ripples = _build_effects()
    for s in streams:
        s.draw(light, t, cam)
    for b in bursts:
        b.draw(light, t, cam)
    for o in orbits:
        o.draw(light, t, cam)
    for r in ripples:
        r.draw(light, t, cam)

def _post(frame, t):
    npimg = np.asarray(frame.convert("RGB")).astype(np.float32)
    # global fade-in from black
    if t < T["fade_in"]:
        f = t / T["fade_in"]
        npimg *= E.ease_out_cubic(f)
    # gentle final dim (settle)
    if t > T["settle"]:
        f = 1.0 - 0.10 * E.clamp((t - T["settle"]) / (T["end"] - T["settle"]), 0, 1)
        npimg *= f
    # subtle film grain
    rng = np.random.default_rng(int(t * config.FPS) + 7)
    grain = rng.normal(0, 2.2, npimg.shape).astype(np.float32)
    npimg += grain
    npimg = np.clip(npimg, 0, 255)
    return Image.fromarray(npimg.astype(np.uint8), "RGB")

def build_frame(t):
    cam = timeline.cam_at(t)
    base = background.make_base()
    img = Image.fromarray(base, "RGB").convert("RGBA")

    nodes.glass_pass(img, t, cam)

    light = gfx.new_light(H, W)
    background.draw_background(light, t, cam)
    nodes.light_pass(light, t, cam)
    draw_effects(light, t, cam)

    comp = np.asarray(img.convert("RGB"))
    frame = gfx.screen_blend(comp, light)
    frame = Image.fromarray(frame, "RGB").convert("RGBA")

    nodes.text_pass(frame, t, cam)

    return _post(frame, t)
