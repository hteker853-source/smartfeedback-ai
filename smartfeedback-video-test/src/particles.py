"""Particle systems: data streams (comets + trails), shard bursts, orbits."""
import math
import random

from . import gfx, config, easings as E

def _screen(p):
    return p

class Stream:
    """Animated data stream along a quadratic bezier: glowing trail + moving comets."""

    def __init__(self, p0, p1, p2, color, size=3.2, travel=0.9, spacing=0.16,
                 t0=0.0, t1=999.0, n=46, trail_intensity=1.0, tail=0.35):
        self.p0, self.p1, self.p2 = p0, p1, p2
        self.color = color
        self.size = size
        self.travel = travel
        self.spacing = spacing
        self.t0, self.t1 = t0, t1
        self.trail_intensity = trail_intensity
        self.tail = tail
        self.pts = gfx.quad_bezier(p0, p1, p2, n)
        self.n = len(self.pts)

    def envelope(self, t):
        if t < self.t0:
            return 0.0
        # fade in
        fin = min(1.0, (t - self.t0) / 0.4)
        fout = 1.0
        if self.t1 < 999:
            fout = min(1.0, max(0.0, (self.t1 - t) / 0.5))
        return fin * fout

    def _draw_trail(self, light, t, cam, parallax):
        env = self.envelope(t)
        if env <= 0:
            return
        cx, cy, zoom = cam
        # place a soft glow at each sample point (denser near active section)
        step = max(1, self.n // 14)
        base_i = 0.0
        for i in range(0, self.n, step):
            x, y = self.pts[i]
            sx = config.W / 2 + (x - cx) * zoom * parallax
            sy = config.H / 2 + (y - cy) * zoom * parallax
            gfx.paste_glow(light, sx, sy, self.size * zoom * 1.4, self.color,
                           self.trail_intensity * env * 0.55)

    def _draw_comets(self, light, t, cam, parallax):
        if t < self.t0 or t > self.t1 + self.travel:
            return
        cx, cy, zoom = cam
        # comet spawn times
        k = 0
        while True:
            s = self.t0 + k * self.spacing
            if s > min(t, self.t1):
                break
            u = (t - s) / self.travel
            if 0.0 <= u <= 1.0:
                idx = u * (self.n - 1)
                i0 = int(idx)
                i1 = min(i0 + 1, self.n - 1)
                f = idx - i0
                x = self.pts[i0][0] * (1 - f) + self.pts[i1][0] * f
                y = self.pts[i0][1] * (1 - f) + self.pts[i1][1] * f
                sx = config.W / 2 + (x - cx) * zoom * parallax
                sy = config.H / 2 + (y - cy) * zoom * parallax
                # head glow + tail streak (motion blur)
                head = 0.55 + 0.45 * (1 - u)
                gfx.paste_glow(light, sx, sy, self.size * zoom * (0.7 + head), self.color, 1.0 * head)
                # tail: dim glow slightly behind
                ub = max(0.0, u - self.tail)
                ib = ub * (self.n - 1)
                j0 = int(ib)
                j1 = min(j0 + 1, self.n - 1)
                fb = ib - j0
                bx = self.pts[j0][0] * (1 - fb) + self.pts[j1][0] * fb
                by = self.pts[j0][1] * (1 - fb) + self.pts[j1][1] * fb
                sbx = config.W / 2 + (bx - cx) * zoom * parallax
                sby = config.H / 2 + (by - cy) * zoom * parallax
                gfx.paste_glow(light, sbx, sby, self.size * zoom * 0.8, self.color, 0.35 * head)
            k += 1

    def draw(self, light, t, cam, parallax=1.0):
        self._draw_trail(light, t, cam, parallax)
        self._draw_comets(light, t, cam, parallax)


class ShardBurst:
    """One-shot burst of glowing shards flying outward (data decomposition)."""

    def __init__(self, origin, t0, color, count=40, speed=0.9, size=2.6, seed=1):
        self.origin = origin
        self.t0 = t0
        self.color = color
        self.speed = speed
        self.size = size
        rng = random.Random(seed)
        self.parts = []
        for _ in range(count):
            ang = rng.uniform(0, math.tau)
            dist = rng.uniform(0.15, 1.0)
            self.parts.append({
                "ang": ang,
                "dist": dist,
                "delay": rng.uniform(0, 0.35),
                "size": rng.uniform(0.5, 1.4) * size,
            })

    def draw(self, light, t, cam, parallax=1.0):
        cx, cy, zoom = cam
        for p in self.parts:
            local = t - self.t0 - p["delay"]
            if local < 0:
                continue
            u = min(1.0, local / self.speed)
            e = 1 - (1 - u) ** 3          # ease out cubic
            d = p["dist"] * 420 * e
            x = self.origin[0] + math.cos(p["ang"]) * d
            y = self.origin[1] + math.sin(p["ang"]) * d
            fade = max(0.0, 1.0 - u * u * 1.2)
            sx = config.W / 2 + (x - cx) * zoom * parallax
            sy = config.H / 2 + (y - cy) * zoom * parallax
            gfx.paste_glow(light, sx, sy, p["size"] * zoom, self.color, fade * 0.9)


class Orbit:
    """Particles orbiting a node while it 'processes'."""

    def __init__(self, center, t0, t1, color, radius, count=10, seed=2):
        self.center = center
        self.t0, self.t1 = t0, t1
        self.color = color
        self.radius = radius
        rng = random.Random(seed)
        self.parts = []
        for _ in range(count):
            self.parts.append({
                "ang0": rng.uniform(0, math.tau),
                "speed": rng.uniform(0.6, 1.4) * (1 if rng.random() > 0.5 else -1),
                "rad": rng.uniform(0.75, 1.15),
                "size": rng.uniform(0.7, 1.6),
            })

    def draw(self, light, t, cam, parallax=1.0):
        if t < self.t0 or t > self.t1:
            return
        env = min(1.0, (t - self.t0) / 0.5) * min(1.0, (self.t1 - t) / 0.4 + 0.3)
        if env <= 0:
            return
        cx, cy, zoom = cam
        for p in self.parts:
            ang = p["ang0"] + (t - self.t0) * p["speed"] * 2.2
            r = self.radius * p["rad"]
            x = self.center[0] + math.cos(ang) * r
            y = self.center[1] + math.sin(ang) * r * 0.9
            sx = config.W / 2 + (x - cx) * zoom * parallax
            sy = config.H / 2 + (y - cy) * zoom * parallax
            gfx.paste_glow(light, sx, sy, p["size"] * zoom, self.color, env * 0.8)


class Ripple:
    """Expanding ring of light from a node (processing pulse)."""

    def __init__(self, center, t0, color, max_r, dur=1.3, points=22):
        self.center = center
        self.t0 = t0
        self.color = color
        self.max_r = max_r
        self.dur = dur
        self.points = points

    def draw(self, light, t, cam, parallax=1.0):
        u = (t - self.t0) / self.dur
        if not (0.0 <= u <= 1.0):
            return
        r = self.max_r * E.ease_out_cubic(u)
        a = (1.0 - u) * 0.5
        cx, cy, zoom = cam
        for i in range(self.points):
            ang = 2 * math.pi * i / self.points + self.t0 * 0.6
            x = self.center[0] + math.cos(ang) * r
            y = self.center[1] + math.sin(ang) * r
            sx = config.W / 2 + (x - cx) * zoom * parallax
            sy = config.H / 2 + (y - cy) * zoom * parallax
            gfx.paste_glow(light, sx, sy, 3.2 * zoom, self.color, a)
