"""Procedural sound design: ambient bed + synced SFX, rendered to a WAV."""
import math
import os
import wave
import numpy as np

from . import config, timeline

SR = 48000
T = timeline.T

def _t(dur):
    return np.arange(int(dur * SR), dtype=np.float32) / SR

def tone(freq, dur, gain=1.0, harmonics=((1.0, 1.0),)):
    t = _t(dur)
    s = np.zeros_like(t)
    for h, a in harmonics:
        s += a * np.sin(2 * math.pi * freq * h * t)
    return s * gain

def adsr(n, a, d, s, r, sl=0.6):
    env = np.zeros(n, dtype=np.float32)
    na, nd, nr = int(a * SR), int(d * SR), int(r * SR)
    ns = n - na - nd - nr
    if ns < 0:
        ns = 0
    for i in range(min(na, n)):
        env[i] = i / na if na else 1.0
    for i in range(na, min(na + nd, n)):
        env[i] = 1.0 - (1.0 - sl) * ((i - na) / nd if nd else 0)
    for i in range(na + nd, min(na + nd + ns, n)):
        env[i] = sl
    for i in range(na + nd + ns, n):
        j = i - (na + nd + ns)
        env[i] = sl * (1.0 - j / nr) if nr else 0.0
    return env

def _add(buf, sig, t0, gain=1.0):
    i0 = int(t0 * SR)
    n = len(sig)
    if i0 < 0:
        sig = sig[-i0:]
        i0 = 0
    if i0 + n > len(buf):
        n = len(buf) - i0
        sig = sig[:n]
    if n <= 0:
        return
    buf[i0:i0 + n] += sig[:n] * gain

# ---- primitives ----
def whoosh(dur, f0, f1, gain=1.0, curve=1.0, back_down=False):
    n = int(dur * SR)
    x = np.random.RandomState(123 + int(f0) + int(f1)).randn(n).astype(np.float32) * 0.3
    f = np.linspace(f0, f1, n, dtype=np.float32)
    if back_down:
        f = np.concatenate([np.linspace(f0, f1, n // 2), np.linspace(f1, f0, n - n // 2)]).astype(np.float32)
    alpha = 1.0 - np.exp(-2 * math.pi * f / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(n):
        acc += alpha[i] * (x[i] - acc)
        y[i] = acc
    # envelope (asymmetric: fast rise, slower fall)
    env = np.sin(math.pi * (np.arange(n) / n)) ** (0.7 * curve)
    y = y * env
    y /= (np.abs(y).max() + 1e-9)
    return y * gain

def impact(gain=1.0, base=58.0, dur=1.2):
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    f = base * np.exp(-t * 1.1) + 28.0
    phase = np.cumsum(2 * math.pi * f / SR)
    thump = np.sin(phase)
    env = np.exp(-t * 4.5)
    s = thump * env
    # airy transient
    x = np.random.RandomState(7).randn(n).astype(np.float32)
    alpha = 1.0 - np.exp(-2 * math.pi * 3000 / SR)
    acc = 0.0
    hp = np.empty_like(x)
    for i in range(n):
        acc += alpha * (x[i] - acc)
        hp[i] = x[i] - acc
    hp = hp * np.exp(-t * 60)
    s = s + hp * 0.5
    s /= (np.abs(s).max() + 1e-9)
    return s * gain

def pulse(freq=820, gain=1.0, dur=0.28):
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    s = np.sin(2 * math.pi * freq * t) * np.exp(-t * 18)
    s += 0.4 * np.sin(2 * math.pi * freq * 2 * t) * np.exp(-t * 26)
    s /= (np.abs(s).max() + 1e-9)
    return s * gain

def pluck(freq=740, gain=1.0, dur=0.4):
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    s = np.sin(2 * math.pi * freq * t) * np.exp(-t * 7)
    s += 0.5 * np.sin(2 * math.pi * freq * 1.5 * t) * np.exp(-t * 11)
    s /= (np.abs(s).max() + 1e-9)
    return s * gain

def tick(freq=1600, gain=1.0, dur=0.03):
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    s = np.sin(2 * math.pi * freq * t) * np.exp(-t * 120)
    return s * gain

def chime(gain=1.0, dur=0.9):
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    s = 0.6 * np.sin(2 * math.pi * 880 * t) + 0.18 * np.sin(2 * math.pi * 1760 * t)
    s += 0.5 * np.sin(2 * math.pi * 1318 * t) + 0.12 * np.sin(2 * math.pi * 3954 * t)
    s *= adsr(n, 0.005, 0.4, 0.1, 0.4, 0.0)
    s /= (np.abs(s).max() + 1e-9)
    return s * gain

def ambient_bed(dur, gain=1.0):
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    # warm drone
    s = 0.5 * np.sin(2 * math.pi * 55 * t)
    s += 0.3 * np.sin(2 * math.pi * 82.4 * t)
    s += 0.2 * np.sin(2 * math.pi * 110 * t + 1.0)
    # slow tremolo
    trem = 0.7 + 0.3 * np.sin(2 * math.pi * 0.13 * t)
    s *= trem
    # dark noise bed
    x = np.random.RandomState(3).randn(n).astype(np.float32)
    brown = np.cumsum(x) * 0.02
    brown -= brown.mean()
    brown /= (np.abs(brown).max() + 1e-9)
    s += brown * 0.35
    # global swell
    swell = np.ones(n, dtype=np.float32)
    fade = int(2.0 * SR)
    swell[:fade] = np.linspace(0, 1, fade)
    tail = int(2.5 * SR)
    swell[-tail:] *= np.linspace(1, 0, tail)
    s *= swell
    s /= (np.abs(s).max() + 1e-9)
    return s * gain

def build_score():
    dur = config.DURATION
    buf = np.zeros(int(dur * SR), dtype=np.float32)

    _add(buf, ambient_bed(dur, 0.55), 0.0)

    # message appear
    _add(buf, whoosh(0.9, 300, 1400, 0.16), 0.35)
    # typing ticks (word-ish)
    for i, tt in enumerate([1.25, 1.55, 1.85, 2.15, 2.35]):
        _add(buf, tick(1500 + i * 60, 0.5 + i * 0.06), tt)

    # decompose: data separation
    _add(buf, whoosh(1.1, 500, 3500, 0.30), T["decompose"] - 0.05)
    _add(buf, pulse(620, 0.28), T["decompose"] + 0.05)

    # stream into supervisor (rising)
    _add(buf, whoosh(1.4, 400, 4200, 0.26), T["stream_in"] - 0.1)

    # supervisor reveal impact
    _add(buf, impact(0.5, base=60), T["supervisor_reveal"] - 0.03)
    _add(buf, chime(0.30), T["supervisor_reveal"] + 0.1)

    # dispatch whoosh
    _add(buf, whoosh(1.0, 300, 3000, 0.22), T["dispatch"] - 0.05)

    # agent activation pulses
    for i, at in enumerate(T["agent_activate"]):
        _add(buf, pulse(700 + i * 140, 0.30), at)
        _add(buf, whoosh(0.5, 500, 1800, 0.12), at + 0.02)

    # camera move to investigation
    _add(buf, whoosh(0.8, 250, 2000, 0.16), T["investigate_focus"] - 0.05)

    # investigation processing ticks
    n_ticks = 6
    span = T["analysis_done"] - 16.4
    for i in range(n_ticks):
        _add(buf, tick(1800 + i * 120, 0.4 + i * 0.05), 16.4 + i * span / n_ticks)

    # camera return
    _add(buf, whoosh(0.8, 1800, 300, 0.16, back_down=True), T["return_center"] - 0.05)

    # decision impact
    _add(buf, impact(0.55, base=70), T["decision_impact"] - 0.03)
    _add(buf, pluck(520, 0.3), T["decision_impact"] + 0.05)

    # UI confirmation (brand)
    _add(buf, chime(0.4), T["brand_in"])
    _add(buf, pulse(920, 0.2), T["brand_in"] + 0.15)

    # normalize + soft clip
    peak = np.abs(buf).max() + 1e-9
    buf = buf / peak * 0.82
    buf = np.tanh(buf * 1.15)
    return buf

def render_wav():
    buf = build_score()
    pcm = (buf * 32767).astype(np.int16)
    path = config.FINAL_WAV
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path
