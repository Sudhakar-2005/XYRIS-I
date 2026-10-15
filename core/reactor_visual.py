"""Reactor visual model — pure, Qt-free logic for the AETHER-I neural core.

Everything paint-independent lives here so state→motion mapping, the neural
graph, particles and signals can be unit-tested without a display. ui.py
(HudCanvas) consumes these functions from its QPainter code.

Honesty contract (see docs/brain/CORE_VISUAL_DESIGN.md):
* Motion intensity derives ONLY from the real AppState; unknown states get
  the calm default — never fabricated activity.
* No progress percentages anywhere: the segmented status ring shows a state
  sweep, not completion. Segments never imply task progress.
* Particle and signal counts are bounded constants — no spawning storms.
* Reduced motion collapses every rate toward stillness but keeps colors and
  the state label so the UI stays informative.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

NODE_COUNT = 42
EDGE_NEIGHBOURS = 2
MAX_PARTICLES = 28
PARTICLE_SEED = 1234
SIGNAL_COUNT = 5
PULSE_TTL = 1.2          # seconds a transition pulse stays visible


# ── motion profile per state ────────────────────────────────────────────────

@dataclass(frozen=True)
class Motion:
    ring_rate: float       # multiplier for the existing sweeping arcs
    spin: float            # neural sphere rotation, rad/s
    particle_budget: int   # bounded particle count for this state
    signal_speed: float    # edge traversal per second (0..1 fraction)
    glow: float            # extra central lift (0..0.2), never a fake meter
    scan: bool             # draw the verification-style scan arc


# Calm default — also the truthful answer for unknown/unmapped states.
_DEFAULT = Motion(1.0, 0.10, 6, 0.18, 0.00, False)

STATE_MOTION: dict[str, Motion] = {
    "IDLE":                Motion(1.0, 0.07,  4, 0.12, 0.00, False),
    "SLEEPING":            Motion(0.6, 0.05,  2, 0.08, 0.00, False),
    "LISTENING":           Motion(1.2, 0.16, 10, 0.28, 0.06, False),
    "MUTED":               Motion(0.7, 0.06,  2, 0.10, 0.00, False),
    "UNDERSTANDING":       Motion(2.2, 0.30, 12, 0.42, 0.10, False),
    "THINKING":            Motion(2.6, 0.34, 14, 0.46, 0.12, False),
    "PLANNING":            Motion(2.4, 0.32, 14, 0.50, 0.10, False),
    "EXECUTING":           Motion(3.0, 0.40, 18, 0.58, 0.14, False),
    "OBSERVING":           Motion(2.0, 0.26, 12, 0.40, 0.08, False),
    "VERIFYING":           Motion(2.4, 0.30, 12, 0.44, 0.10, True),
    "SPEAKING":            Motion(2.8, 0.36, 16, 0.52, 0.12, False),
    "WAITING_CONFIRMATION": Motion(0.6, 0.08, 6, 0.14, 0.04, False),
    "RECOVERING":          Motion(1.4, 0.18, 8, 0.26, 0.05, False),
    "ERROR":               Motion(0.5, 0.06, 4, 0.10, 0.02, False),
    "INITIALISING":        Motion(1.6, 0.22, 10, 0.32, 0.06, False),
}
# V3 aliases for reading clarity; identical profiles are fine.
STATE_MOTION.setdefault("PROCESSING", STATE_MOTION["EXECUTING"])

# States whose transition earns a visible neural pulse. None → no pulse.
_PULSE_KINDS = {
    "LISTENING": "listen", "UNDERSTANDING": "think", "THINKING": "think",
    "PLANNING": "plan", "EXECUTING": "tool", "OBSERVING": "observe",
    "VERIFYING": "verify", "SPEAKING": "speak",
    "WAITING_CONFIRMATION": "confirm", "ERROR": "error",
    "RECOVERING": "recover",
}


def motion_for(state: str | None) -> Motion:
    """Motion profile for *state*. Unknown → calm default (truthful general
    state, never fabricated intensity)."""
    return STATE_MOTION.get(str(state or "").strip().upper(), _DEFAULT)


def pulse_kind_for(state: str | None) -> str | None:
    return _PULSE_KINDS.get(str(state or "").strip().upper())


def pulse_alpha(age: float, ttl: float = PULSE_TTL) -> float:
    """0..1 brightness boost for a transition pulse; 1 at birth → 0 at ttl."""
    if age < 0.0:
        return 0.0
    if age >= ttl:
        return 0.0
    x = 1.0 - age / ttl
    return x * x * (3.0 - 2.0 * x)        # smoothstep out


def reduced_motion_profile() -> Motion:
    """Everything nearly still; colors and the state label carry meaning."""
    return Motion(0.12, 0.01, 0, 0.0, 0.0, False)


# ── neural graph (deterministic, no RNG at import) ──────────────────────────

def _fib_sphere(n: int) -> tuple[tuple[float, float, float], ...]:
    """Evenly distributed unit-sphere points (Fibonacci lattice) — stable
    across runs, so tests and visuals never jitter between launches."""
    ga = math.pi * (3.0 - math.sqrt(5.0))
    pts = []
    for i in range(n):
        y = 1.0 - (i / max(1, n - 1)) * 2.0
        rad = math.sqrt(max(0.0, 1.0 - y * y))
        th = ga * i
        pts.append((math.cos(th) * rad, y, math.sin(th) * rad))
    return tuple(pts)


NODES = _fib_sphere(NODE_COUNT)
TIERS: tuple[int, ...] = tuple(0 if i % 5 == 0 else (1 if i % 2 == 0 else 2)
                               for i in range(NODE_COUNT))


def _nearest_edges(n: int, k: int) -> tuple[tuple[int, int], ...]:
    seen: set[tuple[int, int]] = set()
    edges: list[tuple[int, int]] = []
    for i in range(n):
        dists = sorted(
            ((sum((a - b) ** 2 for a, b in zip(NODES[i], NODES[j])), j)
             for j in range(n) if j != i),
            key=lambda t: t[0])
        for _, j in dists[:k]:
            key = (i, j) if i < j else (j, i)
            if key not in seen:
                seen.add(key)
                edges.append(key)
    return tuple(edges)


EDGES = _nearest_edges(NODE_COUNT, EDGE_NEIGHBOURS)


def rotate_y(pt: tuple[float, float, float], ang: float) -> tuple[float, float, float]:
    x, y, z = pt
    c, s = math.cos(ang), math.sin(ang)
    return (x * c + z * s, y, -x * s + z * c)


def project_nodes(ang: float) -> tuple[tuple[float, float, float, int], ...]:
    """Rotate the graph about Y and project to 2D.

    Returns ((sx, sy, depth, tier), ...): sx/sy in [-1, 1], depth in [0, 1]
    (1 = nearest the viewer) for brightness/size, plus the node tier.
    """
    out = []
    for pt, tier in zip(NODES, TIERS):
        x, y, z = rotate_y(pt, ang)
        out.append((x, y, (z + 1.0) * 0.5, tier))
    return tuple(out)


# Per-node micro-motion (V2 Layer B): deterministic constants derived from
# the node index — no RNG at import, identical across launches. Amplitudes
# stay tiny so the lattice never visibly comes apart.
DRIFT: tuple[tuple[float, float, float], ...] = tuple(
    (0.5 + (i * 0.37) % 1.7,          # angular frequency, rad/s
     (i * 1.13) % math.tau,           # phase offset
     0.010 + 0.006 * (i % 3))         # amplitude in unit-sphere space
    for i in range(NODE_COUNT))


def perspective_scale(depth: float) -> float:
    """Mild forward perspective for the neural sphere: near nodes render
    slightly larger/outward, far ones smaller/inward. A depth cue inside a
    2D painter — not a claim of true 3D geometry (V2 design, spec §5)."""
    return 0.82 + 0.40 * max(0.0, min(1.0, depth))


def node_positions(ang: float, t: float,
                   reduced: bool = False) -> tuple[tuple[float, float, float, int], ...]:
    """Rotated + micro-drifted + perspective-scaled node screen positions.

    Deterministic in (ang, t). ``reduced=True`` freezes the drift (rotation
    still follows *ang* — the caller zeroes that too). Bounds: |sx|,|sy| ≤ 1.35.
    """
    out = []
    for i, (x, y, d, tier) in enumerate(project_nodes(ang)):
        if not reduced:
            freq, ph, amp = DRIFT[i]
            x += amp * math.sin(t * freq + ph)
            y += amp * 0.7 * math.cos(t * freq * 0.83 + ph * 1.7)
        s = perspective_scale(d)
        out.append((x * s, y * s, d, tier))
    return tuple(out)


def signal_positions(t: float, speed: float,
                     count: int = SIGNAL_COUNT) -> tuple[tuple[int, float], ...]:
    """(edge_index, frac) for each travelling signal — frac in [0,1] is how
    far along the edge the pulse currently is. Deterministic in *t*."""
    if speed <= 0.0 or not EDGES:
        return ()
    out = []
    step = max(1, len(EDGES) // max(1, count))
    for i in range(min(count, len(EDGES))):
        frac = (t * speed + i / max(1, count)) % 1.0
        out.append(((i * step) % len(EDGES), frac))
    return tuple(out)


# ── status ring (state sweep — never task progress) ─────────────────────────

def status_lit(state: str | None) -> float:
    """Fraction of the segmented ring shown 'hot' for *state*.

    This is a STATE intensity, not completion: 1.0 for ERROR (fully
    attention), ~0.3 sweep for working states, 0.12 rest. It never rises
    with task progress because no task-progress source exists here.
    """
    s = str(state or "").strip().upper()
    if s == "ERROR":
        return 1.0
    if s == "WAITING_CONFIRMATION":
        return 0.35
    if s in ("IDLE", "SLEEPING", "MUTED"):
        return 0.12
    if s in ("LISTENING", "RECOVERING", "INITIALISING"):
        return 0.25
    if s in ("UNDERSTANDING", "THINKING", "PLANNING", "EXECUTING",
             "OBSERVING", "VERIFYING", "SPEAKING", "PROCESSING"):
        return 0.30
    return 0.12                                     # unknown → rest


# ── holographic ring set (per-ring motion profiles) ─────────────────────────
#
# Layer C of the V2 design: every ring carries its own rate, direction, span
# and phase, so the set can never degenerate into one collective spin. The
# tested invariant is that no two rings share rate AND direction. `rate` is
# deg/sec at multiplier 1.0 (calm idle); the state's Motion.ring_rate (plus
# the speaking boost) multiplies it in the renderer, exactly as V1 did —
# so ring speeds remain state-driven, never decorative.

@dataclass(frozen=True)
class RingProfile:
    name: str
    radius: float          # fraction of reactor radius r
    span: float            # degrees of each arc
    count: int             # arcs around the circle
    direction: int         # +1 clockwise, -1 counter-clockwise
    rate: float            # base deg/sec at multiplier 1.0
    phase: float           # static phase offset (degrees)
    colour: str            # "main" (cyan) | "acc" (state accent)
    alpha: float
    width: float


RING_PROFILES: tuple[RingProfile, ...] = (
    RingProfile("outer",       1.005,  40.0, 3, +1,  3.0,  0.0, "main", 0.30, 1.4),
    RingProfile("energy",      0.955, 118.0, 2, +1,  9.0,  0.0, "acc",  0.75, 2.0),
    RingProfile("calibration", 0.845,  82.0, 3, -1, 15.0, 40.0, "main", 0.38, 1.3),
    RingProfile("orbital_a",   0.760, 150.0, 1, +1, 21.0,  0.0, "acc",  0.45, 1.6),
    RingProfile("orbital_b",   0.660,  64.0, 4, -1, 27.0,  0.0, "main", 0.26, 1.1),
    RingProfile("orbital_c",   0.545, 128.0, 2, +1, 33.0,  0.0, "main", 0.30, 1.2),
)

# Fine radial markings (Layer A): a slow technical tick band on the reactor
# itself, rotating on its own constant profile — deliberately NOT state-scaled,
# so restraint survives even when the working rings hurry.
TECH_MARKINGS = {
    "radius_base": 0.740,     # every tick starts here (fraction of r)
    "radius_fine": 0.752,     # normal tick tip
    "radius_major": 0.765,    # every major_every-th tick reaches further out
    "count": 90,              # one tick per 4 degrees
    "major_every": 6,
    "rate": 4.0,              # deg/sec, own profile
}

# Calibration ring's fine ticks follow that ring's own base angle.
CALIBRATION_TICKS = {"count": 36, "radius_in": 0.800, "radius_out": 0.817,
                     "alpha": 0.16}


def ring_bases(t: float, ring_rate: float = 1.0,
               reduced: bool = False) -> tuple[float, ...]:
    """Base angle (degrees) for each ring profile at time *t*.

    ``ring_rate`` is the state multiplier (V1 semantics: idle ~1, working up
    to ~3, speaking adds, reduced motion ×0.12 applied by the caller).
    ``reduced=True`` freezes rotation entirely — geometry stays readable and
    motionless. Deterministic in *t*; no allocation of geometry.
    """
    out = []
    for prof in RING_PROFILES:
        if reduced:
            out.append(prof.phase % 360.0)
        else:
            out.append((prof.phase + t * prof.rate * ring_rate
                        * prof.direction) % 360.0)
    return tuple(out)


# ── Layer D: automation event → indicator band (V2, honest kinds only) ───────
#
# Only the kinds core/automation/runtime.py really emits. Unknown kinds →
# None → no band. The band never shows a percentage: node payloads carry no
# totals (node_started = {node, type}; node_finished = {node, ok, ...}), so
# progress would have to be invented — it is not drawn. Completion flashes
# only on run_completed, which the automation adapter refuses to emit when a
# required verification failed (V1 Phase 5/6), so success is verifier-gated.

AUTO_ACTIVE = frozenset({"run_started", "run_resumed", "node_started",
                         "node_finished", "node_retry"})
AUTO_HOLD = frozenset({"run_paused", "run_waiting_approval",
                       "approval_waiting"})
AUTO_STOP = frozenset({"run_failed"})
AUTO_FADE = frozenset({"run_dry_run", "run_cancelled"})
AUTO_DONE = frozenset({"run_completed"})
AUTO_KINDS = AUTO_ACTIVE | AUTO_HOLD | AUTO_STOP | AUTO_FADE | AUTO_DONE

AUTO_STALE_S = 300.0                # a silent run band expires (stale info)
AUTO_OUTCOME_TTL = {                # seconds each terminal flash stays
    "run_completed": 1.6,
    "run_failed": 4.0,
    "run_dry_run": 2.5,
    "run_cancelled": 3.0,
}
AUTO_LABELS = {
    "run_started": "RUN", "run_resumed": "RUN",
    "node_started": "RUN", "node_finished": "RUN", "node_retry": "RUN",
    "run_paused": "PAUSED", "run_waiting_approval": "APPROVE",
    "approval_waiting": "APPROVE",
    "run_failed": "FAILED", "run_cancelled": "CANCELLED",
    "run_completed": "DONE", "run_dry_run": "DRY RUN",
}


def auto_band(kind: str, now: float, last_at: float,
              at: float) -> tuple[str, float, str, float] | None:
    """Automation indicator recipe: (cls, alpha, label, age_frac) or None.

    * active/hold — persistent while events are recent (``AUTO_STALE_S``);
      alpha constant, age_frac 0.
    * terminal kinds — visible for ``AUTO_OUTCOME_TTL[kind]`` seconds from
      *at*, alpha decaying with age; age_frac runs 0→1 then expires.
    Pure and testable: no clock reads, no drawing.
    """
    k = str(kind or "")
    if k not in AUTO_KINDS:
        return None
    if k in AUTO_ACTIVE or k in AUTO_HOLD:
        if now - last_at > AUTO_STALE_S:
            return None
        cls = "active" if k in AUTO_ACTIVE else "hold"
        return (cls, 0.75, AUTO_LABELS.get(k, "RUN"), 0.0)
    ttl = AUTO_OUTCOME_TTL.get(k, 0.0)
    age = now - at
    if ttl <= 0.0 or age < 0.0 or age > ttl:
        return None
    frac = age / ttl
    if k in AUTO_DONE:
        return ("done", 0.90 * (1.0 - frac), AUTO_LABELS[k], frac)
    if k in AUTO_STOP:
        return ("stop", 0.85 * (1.0 - 0.6 * frac), AUTO_LABELS[k], frac)
    return ("clear", 0.55 * (1.0 - frac), AUTO_LABELS[k], frac)


# ── particles (bounded, deterministic seeding, no physics) ──────────────────

@dataclass
class Particle:
    theta: float     # azimuth
    phi: float       # polar angle
    dist: float      # 0..1 shell distance (0 = centre, 1 = rim)
    speed: float     # azimuth drift, rad/s
    twinkle: float   # phase offset for brightness


def spawn_particles(budget: int, seed: int = PARTICLE_SEED) -> list[Particle]:
    """Exactly *budget* particles (hard-capped at MAX_PARTICLES), seeded so
    the field is reproducible. No uncontrolled spawning exists by design."""
    budget = max(0, min(int(budget), MAX_PARTICLES))
    rng = random.Random(seed)
    return [Particle(theta=rng.uniform(0, math.tau),
                     phi=rng.uniform(0.15, math.pi - 0.15),
                     dist=rng.uniform(0.45, 1.0),
                     speed=rng.uniform(0.05, 0.22) * (1 if rng.random() < .5 else -1),
                     twinkle=rng.uniform(0, math.tau))
            for _ in range(budget)]


def step_particles(parts: list[Particle], dt: float,
                   spin: float) -> list[Particle]:
    """Advance particles; keeps the list at its current (bounded) size.

    Particles orbit with the sphere and breathe along the shell. If the
    caller shrank the budget, extras are dropped here — never added.
    """
    dt = max(0.0, min(dt, 0.1))                     # clamp: no jumps on resume
    for p in parts:
        p.theta += (p.speed + spin * 0.6) * dt
        p.dist += math.sin(p.twinkle + dt) * 0.0    # shell stays bounded
        p.twinkle += dt * 1.7
    return parts


def project_particle(p: Particle, ang: float) -> tuple[float, float, float]:
    """→ (sx, sy, depth) in [-1,1]x[-1,1]x[0,1], coherent with the sphere."""
    sp, cp = math.sin(p.phi), math.cos(p.phi)
    x = sp * math.cos(p.theta) * p.dist
    y = cp * p.dist
    z = sp * math.sin(p.theta) * p.dist
    xr, _, zr = rotate_y((x, y, z), ang)
    return xr, y, (zr + 1.0) * 0.5


def particle_alpha(p: Particle, t: float) -> float:
    """Twinkle 0.10..0.55 — subtle, never screen glow."""
    return 0.10 + 0.22 * (1.0 + math.sin(p.twinkle + t * 1.3)) * 0.5 + 0.11
