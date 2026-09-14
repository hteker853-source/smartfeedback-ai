"""Single source of truth: event timing, camera path, scene layout."""
from . import easings as E

# ---------------- scene layout (world coordinates) ----------------
LAYOUT = {
    "message": (960.0, 540.0),
    "supervisor": (960.0, 540.0),
    "agents": {
        "FEEDBACK": (540.0, 300.0),
        "INVESTIGATION": (1400.0, 360.0),
        "CUSTOMER_TREND": (960.0, 800.0),
    },
    "chips": {
        "SENTIMENT": (700.0, 420.0),
        "SATISFACTION": (1230.0, 420.0),
        "CATEGORY": (965.0, 665.0),
    },
    "decision": (960.0, 470.0),
}

CHIP_DATA = {
    "SENTIMENT":    {"value": "NEGATIVE",     "color": "amber"},
    "SATISFACTION": {"value": "2.1 / 5",      "color": "cyan"},
    "CATEGORY":     {"value": "FOOD QUALITY", "color": "violet"},
}

AGENT_ORDER = ["FEEDBACK", "INVESTIGATION", "CUSTOMER_TREND"]

# ---------------- event timeline (seconds) ----------------
T = {
    "fade_in": 0.9,            # global fade from black
    "message_form": 0.4,
    "message_done": 1.5,
    "type_start": 1.05,
    "type_end": 2.45,
    "decompose": 3.0,          # text -> shards + chips
    "chips_settle": 4.7,
    "stream_in": 5.1,          # chips -> supervisor
    "supervisor_reveal": 6.7,  # supervisor node forms (impact)
    "network_shown": 7.7,
    "dispatch": 9.3,           # supervisor -> agents
    "dispatch_stagger": 0.5,
    "agent_activate": [10.1, 10.6, 11.1],
    "process_hold": 12.0,
    "investigate_focus": 14.4,  # camera starts toward investigation
    "investigate_peak": 16.3,   # camera arrives at investigation
    "analysis_done": 17.9,      # investigation completes
    "return_center": 18.6,      # camera returns
    "decision_form": 19.6,      # decision card assembles
    "decision_impact": 20.3,
    "brand_in": 21.6,
    "settle": 23.0,
    "end": 25.0,
}

# ---------------- camera path ----------------
# keyframes: t, (cx, cy, zoom), easing
CAM_KEYFRAMES = [
    {"t": 0.0,  "x": 960, "y": 540, "z": 1.32, "e": E.linear},
    {"t": 1.2,  "x": 960, "y": 540, "z": 1.32, "e": E.EASE_IN_OUT},
    {"t": 3.0,  "x": 960, "y": 540, "z": 1.32, "e": E.EASE_IN_OUT},
    {"t": 5.2,  "x": 960, "y": 540, "z": 1.40, "e": E.ease_in_out_cubic},
    {"t": 6.7,  "x": 960, "y": 540, "z": 1.40, "e": E.ease_out_cubic},
    {"t": 8.8,  "x": 960, "y": 540, "z": 0.98, "e": E.ease_out_cubic},
    {"t": 14.4, "x": 960, "y": 540, "z": 0.98, "e": E.EASE_IN_OUT},
    {"t": 16.3, "x": 1225, "y": 462, "z": 1.16, "e": E.ease_in_out_cubic},
    {"t": 18.0, "x": 1225, "y": 462, "z": 1.16, "e": E.EASE_IN_OUT},
    {"t": 20.0, "x": 960, "y": 540, "z": 1.00, "e": E.ease_in_out_cubic},
    {"t": 22.2, "x": 960, "y": 540, "z": 1.03, "e": E.EASE_IN_OUT},
    {"t": 25.0, "x": 960, "y": 540, "z": 1.06, "e": E.ease_in_out_cubic},
]

def cam_at(t):
    """Return (cx, cy, zoom) camera state at time t (with subtle handheld drift)."""
    if t <= CAM_KEYFRAMES[0]["t"]:
        x, y, z = CAM_KEYFRAMES[0]["x"], CAM_KEYFRAMES[0]["y"], CAM_KEYFRAMES[0]["z"]
    elif t >= CAM_KEYFRAMES[-1]["t"]:
        x, y, z = CAM_KEYFRAMES[-1]["x"], CAM_KEYFRAMES[-1]["y"], CAM_KEYFRAMES[-1]["z"]
    else:
        x = y = z = 0.0
        found = False
        for i in range(len(CAM_KEYFRAMES) - 1):
            a, b = CAM_KEYFRAMES[i], CAM_KEYFRAMES[i + 1]
            if a["t"] <= t <= b["t"]:
                span = b["t"] - a["t"]
                if span <= 0:
                    x, y, z = b["x"], b["y"], b["z"]
                else:
                    f = b["e"]((t - a["t"]) / span)
                    x = E.lerp(a["x"], b["x"], f)
                    y = E.lerp(a["y"], b["y"], f)
                    z = E.lerp(a["z"], b["z"], f)
                found = True
                break
        if not found:
            x, y, z = CAM_KEYFRAMES[-1]["x"], CAM_KEYFRAMES[-1]["y"], CAM_KEYFRAMES[-1]["z"]
    # subtle handheld breathing
    import math
    x += 4.0 * math.sin(t * 0.55 + 1.0)
    y += 3.0 * math.cos(t * 0.47 + 2.2)
    z *= 1.0 + 0.008 * math.sin(t * 0.40 + 0.5)
    return x, y, z
