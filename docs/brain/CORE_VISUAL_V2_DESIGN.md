# AETHER-I Living Neural Core V2 — Audit & Design (Phases 0–1)

Supersedes nothing: `CORE_VISUAL_DESIGN.md` (V1) stays the record of what was
built first. This document is the V2 spec (premium reactor + real-time
intelligence UI) — audit first, then the design, then the DoD checklist that
Phase 9 must tick.

**Git checkpoint (Phase 0 baseline): `fffca19`** — clean tree, 1383 tests
green, app verified running. All V2 work starts from this commit.

---

## Phase 0 — repository audit

| Aspect | Finding | Where |
|---|---|---|
| Framework | Python 3.11 + PyQt6 desktop; asyncio backend bridged to Qt via signals. No web stack. | `main.py`, `ui.py` |
| UI architecture | `MainWindow` hosts one custom-painted `HudCanvas`; overlays are separate widgets. | `ui.py:4543`, `ui.py:398` |
| Existing AI Core | Two centrepieces: `hud_style == "face"` → `HoloAvatar`, else → reactor `_paint_core`. **V2 upgrades the reactor path only; the face path and `AutomationCanvas` are untouched.** | `ui.py:1093-1117` |
| Animation | Single 60 Hz `QTimer` (`_step`), paint throttled (~20 Hz idle), particles stepped in `_step` not in paint. No duplicate loops. | `ui.py:460-462`, `ui.py:622` |
| State management | `core/app_state.py`: 13 V3 states + `MUTED`/`INITIALISING`, thread-safe `transition()`, listener list. `HudCanvas` attaches exactly one listener, removed in `closeEvent`. | `core/app_state.py:58-68`, `ui.py:485`, `ui.py:518` |
| Backend→UI | `JarvisUI.set_state` → `_state_sig` (Qt signal, thread-safe) → `app_state.transition` → listeners. States set only from real transitions (transcript, tool, verification, automation, capture-flush). | `ui.py:7348`, `main.py` call sites 1008/1670/1737/1841/2238… |
| Event system | `core/event_bus.py`: `EventBus` + `EventType` (24 kinds) + `Event` envelope (`event_id/timestamp/priority/correlation_id/source`). Exactly one subscriber, `main._bridge_event` → ActivityFeed + dashboard. **The HUD today sees only app_state transitions, not raw bus events.** | `core/event_bus.py`, `main.py:2882` |
| Voice/speech states | `set_state("SPEAKING")`, mute flag, viseme schedule from the playback thread (`push_visemes`), real mic PCM level (`_pcm_level` → `set_audio_level`). | `main.py:1302`, `ui.py:540`, `ui.py:593` |
| Automation | `core/automation/runtime.py::_emit_event` emits run/node/approval kinds → `main._automation_emit` forwards **`run_*` + `approval_waiting` only** to the bus (`EventType.CUSTOM`, source=automation, correlation_id=run_id); node kinds stay out (feed-flood protection) and live in the run record. | `runtime.py:180-697`, `main.py:2871-2880` |
| Automation kinds that really exist | `run_started/paused/resumed/completed/failed/cancelled/dry_run/waiting_approval`, `approval_waiting`, `node_started/node_retry/node_finished`. **No validation events exist — V2 will not invent them.** | `runtime.py`, `core/activity.py:33-41` |
| Window lifecycle | Resize rebuilds caches keyed on size; `closeEvent` stops the timer and removes the state listener. | `ui.py:1083`, `ui.py:518` |
| Rendering libs | Software `QPainter` only; cached grid pixmap; node/edge tables precomputed at import (deterministic Fibonacci lattice, seeded particles). No GL/WebGL/Three.js exists. | `core/reactor_visual.py:115-136` |
| Perf measurement | `core/perf.mark` startup marks + `AETHER_PERF=1` reporting exist. **Frame/paint time is not measured yet — V2 adds an env-gated paint timer.** | `core/perf.py`, `tests/test_startup_perf.py` |
| Known bottleneck | Software AA rasterisation of many primitives per frame. V1 mitigations: cached grid, pre-blended opaque colours (no per-draw compositing), paint throttle, bounded particles, cached geometry. V2 keeps all of these. | `ui.py:916-923` |
| Historical failure | A mid-paint `AttributeError` (variable shadowing) hard-crashed Qt (0xC0000409). **V2 wraps the reactor paint in a fail-soft boundary + simple-orb fallback** (spec §14). | fixed at `ui.py:952`, V1 record `ea14218` |

**Stack decision (no Three.js):** the app is a desktop Qt widget, not a
browser page — there is no DOM/canvas/WebGL surface to host Three.js, and
introducing an offscreen GL renderer would add context-loss, driver and
fallback problems the current stack does not have. Software QPainter with
perspective projection and depth cues delivers the required look inside the
existing architecture (spec §2: "select the simplest rendering approach";
§5 explicitly allows a Canvas-class implementation with convincing layered
depth "without pretending it is genuine 3D geometry").

**Components that must remain unchanged:** backend (`main.py` dispatch,
automation engine, event bus semantics), `app_state` vocabulary, the face
centrepiece, `AutomationCanvas`, all non-reactor HUD widgets, test suite.

**Integration points (V2):**

1. `app_state` listener → state-driven motion/accents/pulses (exists, keep).
2. `set_audio_level` / `push_visemes` → real audio reactivity (exists, keep).
3. **New — automation seam:** `main._automation_emit` additionally forwards
   every kind (incl. `node_*`) to a dedicated Qt signal consumed by
   `HudCanvas.on_automation_event`. Same store/poll + signal pattern as
   `_state_sig`; **not a second event bus** (bus → feed/dashboard unchanged).
4. Reduced-motion hint → one flag drives all budgets (exists, keep).
5. `closeEvent` cleanup for the new signal connection (exists, extend).

---

## Phase 1 — visual architecture (six coordinated layers)

| Spec layer | V1 baseline | V2 build |
|---|---|---|
| **A Central energy reactor** | atmosphere radial gradients (2), canvas frame marks, crosshair, name ring, amp-driven voice ring | layered luminous shells (inner disc + translucent shell ring with fine circular technical markings — tick ring + notch numerals-as-ticks), controlled soft bloom via pre-blended gradients, energy pulse that reuses the existing real-transition pulse kinds |
| **B 3D neural sphere** | 42-node Fibonacci lattice, 2-nearest edges, depth brightness/size, ≤5 travelling signals, deterministic | perspective projection (mild foreshortening: depth scales radius, not just brightness), subtle deterministic node drift (per-node seeded phase micro-motion), brighter far/near contrast, pathway shimmer along edges driven by state signal speed |
| **C Holographic rings** | 5 sweeping arcs with distinct rate/direction, graduations, segmented status ring at r·1.035 | formal ring set with **individual motion profiles stored per ring** (rate, direction, phase, span): outer structural (slow, CCW), segmented energy ring (CW, state-driven), calibration ring with fine ticks (CCW, independent), orbital arcs (existing 4), inner reactor ring (existing voice ring). Restrained: total simultaneous motion stays bounded |
| **D Signal propagation** | real app_state transitions → ring-segment light + sphere pulse + VERIFYING scan arc | + **automation events** → dedicated indicator band: `run_started` lights the band, `node_*` advances a small step ladder **only for runs with measurable node counts in the payload**, `run_paused`→amber hold, `run_failed`→red stop, `run_completed`→confirmation flash (success tint only when verification was part of the run — the adapter already turns a failed verdict into `run_failed`), `approval_waiting`→amber attention. Metaphor only; no claim of model internals |
| **E Particle environment** | ≤28 bounded, seeded, state-budgeted, 0 under reduced motion, depth parallax | keep; add slight pointer-parallax offset (≤4 px) gated by reduced-motion |
| **F HUD** | status glyph+text per state (13+2), shared accent, waveform | keep as-is; non-color channels verified in Phase 9 |

### State → visual mapping (authoritative for V2)

All 15 real states, driven only by `app_state`:

| State | Visual behaviour | Honest source |
|---|---|---|
| IDLE / SLEEPING | gentle reactor pulse, slow rings, low neural activity | default |
| LISTENING | audio-reactive illumination from **real mic PCM** (`_live_amp`), green accent | `main._pcm_level → set_audio_level` |
| UNDERSTANDING / THINKING / PLANNING | raised glow (≤0.12), faster rings, active signal paths | state transitions; PLANNING from real plan producers |
| EXECUTING / OBSERVING | fastest motion profile, current-operation emphasis | tool dispatch; capture→flush window |
| VERIFYING | focused scan arc, stabilized rings; result shown only when the verdict arrives | real verification run + `TOOL_RESULT` verdict |
| SPEAKING | voice ring + pulse from real playback amplitude/visemes | playback thread |
| WAITING_CONFIRMATION | steady amber, pending action readable in the banner | `confirm.pending_title` |
| RECOVERING | medium motion, amber, ↻ glyph | state machine |
| ERROR | restrained red, static lit ring, success animations stop | real error |
| MUTED / INITIALISING | dimmed / startup profile | system states |
| unknown | calm `_DEFAULT`, generic glyph | pass-through (honest fallback) |
| **PAUSED** *(spec §6/§7)* | **not an app_state** — shown only as the automation indicator's amber hold while a real run is paused | `run_paused` event |
| **COMPLETED** *(spec §6/§7)* | **not an app_state** — short cyan-green confirmation animation on the automation band only after `run_completed` (which the adapter refuses to emit when the required verifier failed); neutral cyan "done" when the workflow required no verification | `run_completed` + verifier verdict |

### Event contract (spec §9)

- Reuse: `app_state` listeners for states; `Event` envelope for the bus;
  ActivityFeed untouched for feed consumers.
- Extension (minimal, typed): `HudCanvas.on_automation_event(kind: str,
  payload: dict, run_id: str)` — accepts only the kinds inventory listed in
  the audit; unknown kinds ignored; duplicates within 150 ms deduped;
  out-of-order/stale tolerated (age-based fade, same as state pulses);
  payload fields read defensively (`payload.get` only).
- No second event bus: one Qt signal in `main` (thread-safe, same pattern as
  `_state_sig`), the bus keeps its current subscribers.

### Animation engine (spec §10)

- Per-ring motion profiles as data (rate/direction/phase), no shared driver.
- One timer; no per-frame allocation of geometry (tables cached at import);
  no state updates from the renderer; cleanup in `closeEvent`.
- Renderer never executes tools or mutates workflow state — events only
  flow *into* the canvas.

### Audio (spec §11)

Existing seams only: mic PCM while listening, playback levels/visemes while
speaking, smoothing in `_step`. No new mic permissions requested; with no
audio data, state-driven animation continues (already the case).

### Performance (spec §12) + measurement plan

- Keep: cached grid, opaque pre-blend, paint throttle, bounded particles,
  lazy grid rebuild, resource cleanup.
- Add: env-gated paint timer (`AETHER_HUD_PAINT=1`) — `time.perf_counter`
  around the reactor paint, rolling window, printed via `core.perf`/log
  every N frames. **Any FPS/latency claim in the docs must come from this
  measurement** (spec: no claimed frame rate without testing).
- Reduced effects: reduced-motion already zeroes signals/particles/rates;
  low-effects path = paint throttle already reduces frame rate when idle.

### Accessibility (spec §13)

State = glyph + text + colour (three channels); keyboard/controls unchanged
(existing widgets); contrast from the shared palette; reduced-motion verified
by tests; the core remains informative with all motion disabled.

### Failure handling (spec §14)

- New: the reactor paint runs inside `try/except`; on failure the exception
  is logged once, a `self._core_paint_failed` flag drops subsequent detail
  paints, and a simple pre-blended orb (atmosphere + status ring) renders
  instead. A visual failure never reaches the app loop.
- Missing/stale events: age-based decay handles late/out-of-order automation
  events; no event → no indication (honest default).
- Renderer init failure: there is no renderer init (pure paint) — the
  fallback above is the equivalent path.

### Testing (spec §15) — Phase 9 additions

Layer geometry determinism, motion-profile distinctness (no two rings share
rate+direction), state→visual mapping for all 15 states, automation kind
mapping incl. unknown/duplicate/stale kinds, paint-failure fallback, reduced
motion, pointer-parallax gating, paint-timer smoke, plus full regression run
(existing `tests/test_core_visual.py` 17 tests must stay green).

### Implementation phases (spec §16)

| Phase | Scope | Checkpoint target |
|---|---|---|
| 0 | this audit | `fffca19` (pre-work baseline) |
| 1 | this design doc | doc commit |
| 2 | reactor shells + technical markings + ring set | code commit + tests |
| 3 | sphere perspective/drift/shimmer | code commit + tests |
| 4 | particles/parallax polish + signal propagation | code commit + tests |
| 5 | state events verification (all 15 honest) | tests |
| 6 | automation event seam + indicator band | code commit + tests |
| 7 | audio-reactivity verification (real seams only) | tests |
| 8 | perf measurement + optimization + accessibility | measurement record + tests |
| 9 | full regression, DoD checklist, limitations | final commit |

---

## Definition of Done (spec §17) — Phase 9 verification (2026-10-15)

- [x] Core matches the neural-reactor direction — all six layers A–F built
      (shells/tech-markings/reactor heart, perspective sphere, profile ring
      set, event-driven signals + automation band, bounded parallax
      particles, HUD)
- [x] Branding intact — name ring, `Courier` status typography, palette
      class `C` untouched; the face centrepiece path unchanged
- [x] Reactor visually prominent — single focal centre, paint order keeps it
      the first-read element
- [x] Nodes/pathways render correctly — deterministic `node_positions`
      (bounds/determinism/drift tests), smoke across all states
- [x] Rings have distinct motion profiles — `RING_PROFILES` invariant
      (no two rings share rate+direction) tested
- [x] Particles within resource limits — `MAX_PARTICLES=28` hard cap +
      per-state budgets (V1 tests, still green)
- [x] Real application states drive the visualization — all 15
      `app_state.VALID_STATES` paint-tested; motion/accent/pulse maps
      complete; unknown → calm default
- [x] Automation events represented accurately — backend kind inventory ⊆
      `AUTO_KINDS` tested; unknown kinds never paint; labels present for
      every kind; dedup/close/late-event rules tested
- [x] Success appears only after required verification — `AUTO_DONE` is
      `{run_completed}` only, and the adapter refuses `run_completed` when a
      required verifier failed (Phase 5/6 record); dry-run/cancel are
      neutral "clear", never success-coloured
- [x] Errors and confirmations clearly represented — ERROR: static red
      ring + `✕` glyph, no flashing; WAITING_CONFIRMATION: amber + `!`
      steady glyph + banner with the real pending action
- [x] Reduced-motion mode works — rates ×0.12, particles drained, drift
      frozen, parallax pinned, signals off, static band/ring windows
      (tested each)
- [x] Renderer failure does not crash AETHER-I — fail-soft boundary +
      sticky simple-orb fallback, one logged error, tested with a synthetic
      paint exception (`test_paint_failure_falls_back_and_sticks`)
- [x] Performance has been measured — `tools/hud_paint_bench.py` numbers
      below + env-gated `AETHER_HUD_PAINT=1` frame timer in the live app;
      no frame rate is claimed, only measured cost vs the 16.67 ms budget
- [x] Existing functionality operational — full regression **1413 passed,
      0 failed** (baseline before V2: 1383)
- [x] Relevant tests pass — core-visual suites: 17 V1 + 30 V2 = 47 green
- [x] Limitations documented — below

## Measured performance (Phase 8 record — real numbers, this machine)

`python tools/hud_paint_bench.py` (offscreen `QWidget.render`, software
QPainter, warm caches, 200 frames/scenario). App-realistic canvas size
490×470:

| Scenario | avg ms | p50 | p95 | max | vs 16.67 ms budget |
|---|---|---|---|---|---|
| IDLE | 5.97 | 5.20 | 10.02 | 33.31 | avg/p95 within |
| IDLE reduced-motion | 5.43 | 5.06 | 7.99 | 14.86 | within |
| EXECUTING | 12.99 | 12.52 | 20.14 | 27.11 | avg within, **p95 over** |
| EXECUTING reduced | 10.85 | 10.14 | 17.10 | 27.53 | avg within, p95 marginal |
| VERIFYING | 13.29 | 12.75 | 21.77 | 63.64 | avg within, **p95 over** |
| VERIFYING reduced | 12.82 | 13.09 | 19.00 | 23.10 | avg within, **p95 over** |

Read honestly: **averages stay inside the 60 Hz budget in every
scenario; the tail (p95) exceeds it in the heavy states**, i.e. occasional
over-budget frames (skipped vsync) while working — on this software raster
path, with run-to-run variance (640×420 run measured EXECUTING avg 9.84 ms;
900×560 runs ranged 5.97–13.45 ms depending on system load).

**Live app measurement (the authoritative one)** — the real window launched
with `AETHER_HUD_PAINT=1`, 32 consecutive 240-frame windows over ~7 minutes
of active use (mic, model, dashboard running): **avg 11.08 – 18.87 ms per
frame**, i.e. most windows within the 16.67 ms budget and a few clearly
over it while the system was busy. Takeaway: the frame cost sits near the
budget, not comfortably under it — a fast machine or the cache candidates
listed below are required for sustained 60 Hz. Watch live with
`AETHER_HUD_PAINT=1` (`aether.hud` log lines: `hud paint measured: avg …
over 240 frames (60 Hz budget = 16.67 ms)`) — that log is the only source
an FPS claim may ever cite. Optimization candidates if the tail matters:
cache the atmosphere/shell gradients per quantised lift bucket, pre-bake
static graduations per (size, colour).

## Phase 9 record

| Phase | Commit |
|---|---|
| 0+1 audit/design | `aff9299` (baseline `fffca19`) |
| 2 reactor + ring set + fail-soft | `9267115` |
| 3 sphere perspective/drift/shimmer | `d6f190b` |
| 4 pointer parallax | `10f7a41` |
| 5+6 automation seam + band | `05aba97` |
| 7+8 audio tests + paint timer + bench | `8efeb5e` |
| 9 this document + final regression | (this commit) |

Regression after every phase; final: **1413 passed, 0 failed** (V1 suites
untouched and green — no existing behaviour removed).

## Limitations (honest record, Phase 9)

- Software rasterisation: perspective projection + shading cues, not true
  3D geometry, lighting or GPU bloom (spec §5's Canvas-class option).
- PAUSED/COMPLETED exist only as the automation band's `PAUSED` hold and
  `DONE` flash — the global vocabulary has no such states by design; the
  band never hijacks global state.
- No percentages anywhere: node payloads carry no totals, so the band shows
  activity/labels only. Validation events do not exist in the backend and
  are therefore not drawn.
- Band freshness: persistent classes expire after 300 s without run events;
  terminal flashes expire on their TTL; late events after a terminal are
  ignored (terminal-wins); no automation traffic ⇒ no band (honest default).
- Performance: bench averages within the 60 Hz budget but p95 tails over it
  in heavy states; live-app 240-frame windows ranged 11.08–18.87 ms avg,
  some over budget while the machine was busy (all measured on this box);
  no FPS claim is made.
- Optional features degrade silently: no pointer moves ⇒ no parallax; no
  automation events ⇒ no band; no audio ⇒ state-driven animation only.

