# SmartFeedback AI — Motion Design Test Scene

A 25-second, 1920×1080@30fps cinematic motion-design test for **SmartFeedback AI**.
The goal is to validate a CPU-only, fully procedural motion-graphics pipeline that can
later be scaled into a 2–3 minute hackathon film.

Everything (visuals + sound) is generated from code — no external APIs, no paid services,
no GPU. The final deliverable is a single H.264 MP4 with a synced AAC soundtrack.

## Output

- **Final video:** `output/smartfeedback_scene.mp4` (25.0 s, H.264, 1920×1080, 30 fps, AAC 48 kHz)
- **Soundtrack:** `audio/score.wav` (procedurally synthesized)
- **QC frames:** `output/qc/*.png` (12 keyframes for visual inspection)

## Tech stack

| Concern            | Tool                                             |
| ------------------ | ------------------------------------------------ |
| Frame rendering    | Python 3 + Pillow + NumPy (CPU)                  |
| Motion / easing    | Custom easing engine (cubic-bezier, spring, back) |
| Visual effects     | Additive "screen" light buffer, Gaussian-blur glows, backdrop-blur glassmorphism |
| Particles          | Analytic streams (comets + trails), shard bursts, orbits, ripples |
| Sound design       | NumPy DSP synthesis (ambient bed, whoosh, pulse, impact, chime) |
| Encoding           | FFmpeg (libx264 + AAC, `+faststart`)             |
| Fonts              | Inter & Space Grotesk (open-source, bundled in `assets/fonts/`) |

## Render command

```bash
./scripts/render.sh            # full render -> output/smartfeedback_scene.mp4
./scripts/qc.sh                # verify metadata + frame/temporal analysis
python3 -m src.render --qc     # fast visual check (renders only the 12 QC frames)
```

## Project structure

```
smartfeedback-video-test/
├── assets/fonts/          # Inter + Space Grotesk TTFs
├── audio/                 # generated score.wav
├── output/                # final MP4 + qc frames
├── scripts/               # render.sh, qc.sh
├── src/
│   ├── config.py          # resolution, palette, world->screen transform
│   ├── easings.py         # easing functions + keyframe sampler
│   ├── fonts.py           # font loading + letter-spaced text sprites
│   ├── gfx.py             # glow sprites, additive light, glass, rings, paths
│   ├── background.py      # dark cinematic base (gradient, blobs, dust, vignette)
│   ├── particles.py       # Stream / ShardBurst / Orbit / Ripple
│   ├── nodes.py           # supervisor, agents, chips, message & decision cards
│   ├── timeline.py        # single source of truth: event times + camera path
│   ├── scene.py           # compositor: background -> glass -> light -> text -> post
│   ├── score.py           # procedural sound design
│   └── render.py          # CLI entry: frames -> ffmpeg (or --qc)
└── README.md
```

## Scene narrative (25 s)

1. **0–3 s** — Dark cinematic open. A glass card with the customer signal
   *"Patatesler soğuktu."* types in, camera close-up.
2. **3–5 s** — The message decomposes: text shatters into shards and separates into
   data chips (`SENTIMENT`, `SATISFACTION`, `CATEGORY`) that fly outward (camera push-in).
3. **5–8 s** — The chips stream back into a forming **SUPERVISOR** node while the camera
   pulls out to reveal the agent network.
4. **9–14 s** — SUPERVISOR dispatches data to **FEEDBACK / INVESTIGATION / CUSTOMER TREND**.
   Nodes pulse, progress rings fill, counters climb, ripples + orbits animate processing.
5. **14–18 s** — Camera eases toward INVESTIGATION; it completes analysis.
6. **18–22 s** — Camera returns to center; a **resolution** card assembles
   (*"Kitchen heat-hold issue detected"* → *"Action routed → Operations"*).
7. **22–25 s** — Brand wordmark fades in; calm settle.

## Sound design

The full soundtrack is synthesized procedurally and synced to the timeline in `src/score.py`:

- Ambient dark bed (warm drone + filtered noise, slow swell)
- Typing ticks, data-separation whoosh, supervisor reveal impact + chime
- Dispatch whoosh + per-agent activation pulses
- Processing ticks, camera-move whooshes, decision impact, UI confirmation chime

## How to scale to a 2–3 minute hackathon film

The pipeline is already modular and keyframe-driven, so extending it is mostly about
**adding scenes to `src/timeline.py` and `src/scene.py`** rather than new infrastructure:

1. **Add scenes** — each story beat becomes its own node/card/stream group with its own
   camera keyframes and event times; the compositor already supports arbitrary stacking.
2. **Reuse effects** — `Stream`, `ShardBurst`, `Orbit`, `Ripple`, glass cards and glows are
   reusable building blocks; new beats are mostly new layout + timing, not new code.
3. **Voiceover** — drop a VO track (or TTS) into `audio/` and mux it in FFmpeg alongside the
   existing SFX bed (which is already at a mix-ready level).
4. **Title cards / lower thirds** — add a `captions.py` scene module using the existing
   `fonts.cached_text` + glass helpers.
5. **Performance** — render at 2.5–3 fps on CPU today; parallelize frames across cores
   (each frame is independent) or pre-render heavy glow sprites for a ~4–8× speedup if needed.

> Render time for this 25 s test: ~5 minutes on CPU. A 2–3 min film scales roughly linearly;
> frame-level parallelism is the main lever if faster turnaround is needed.
