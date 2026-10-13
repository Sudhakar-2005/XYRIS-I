# AETHER-I Living Neural Core — Design & State Mapping (Phase 1)

## Stack decision (Phase 0 audit)

- **UI:** PyQt6, custom-painted `HudCanvas` (`ui.py:392`), software
  `QPainter`, 60 Hz `QTimer` (`_tmr`), repaint throttled to ~20–30 Hz.
  **No React/Three.js/WebGL exists or is introduced** — the equivalent of
  "Canvas" here is QPainter layers on the existing widget.
- **Source of truth:** `core/app_state.py` — one vocabulary
  (`AppState`), `transition()` with listeners, `WORKING_STATES`.
  The HUD reads `self.state`, set only through that machine.
- **Backend→UI:** states set from asyncio/tool threads via
  `MainWindow.set_state → _state_transition` (`ui.py:6884`); confirmation
  via `core/confirm.py` (banner shows the real action title/detail);
  real audio level via `_live_amp → _amp_disp`; automation shows in the
  existing `AutomationCanvas`/dashboard from `RunRecord`s.
- **Existing reactor:** `_paint_core` (`ui.py:701`) — atmosphere, frame
  marks, crosshair, graduations, 5 sweeping arcs (state-set speed), voice
  ring, inner name ring. All preserved and extended, never replaced.

## The five layers

| Layer | Implementation |
|---|---|
| A Central reactor | existing atmosphere/gradient + inner name ring (kept) |
| B Neural sphere | `core/reactor_visual.py` graph (42-node Fibonacci lattice, 2-nearest edges), Y-rotation projection, 3 brightness tiers, ≤5 travelling signals |
| C Rings | existing 5 arcs + graduations **plus** new segmented status ring at r·1.035 — distinct speed/direction, state sweep only (never progress %) |
| D Particles | bounded ≤28 (`MAX_PARTICLES`), deterministic seed, orbit+twinkle only, 0 under reduced motion, stepped in `_step()` not in paint |
| E HUD | existing status line/glyphs/waveform kept; typography and state glyph are the accessibility channel (never colour alone) |

## State → visual mapping (authoritative)

| AppState | Motion | Accent | Notes |
|---|---|---|---|
| IDLE / SLEEPING | breathe, 4/2 particles | default cyan | |
| LISTENING | medium spin, green accent, waveform = real mic level | green | mic-off ⇒ MUTED, never implies listening |
| UNDERSTANDING / THINKING / PLANNING | fast rings, signals 0.42–0.50, glow ≤0.12 | amber | PLANNING/OBSERVING have **no emitter yet** (app_state docstring); they render honestly only if ever set |
| EXECUTING / OBSERVING | fastest, 18 particles | amber | real dispatch only (`_execute_tool` sets it) |
| VERIFYING | + scan arc | amber | set only while a real check runs (Phase 5 of BRAIN roadmap) |
| SPEAKING | voice ring + pulses = real playback amplitude | orange | |
| WAITING_CONFIRMATION | slow, 0.35 lit sweep | orange | banner shows the actual pending action (`confirm.pending_title`) |
| RECOVERING | medium, ↻ glyph | orange | |
| ERROR | still (0.5 rate), static 1.0 red ring — **no flashing** | red | clears only when app state changes |
| unknown | calm `_DEFAULT` | cyan | truthful general state |

**Transition pulses** (`pulse_kind_for`): a real `app_state` transition
briefly brightens the sphere (≤1.2 s smoothstep) — pulses are emitted from
state changes only, never from timers.

**PAUSED / COMPLETED at core level:** the core vocabulary has no such
states. Workflow pause/completion is shown where that truth lives — the
automation HUD/dashboard `RunRecord` status — so the core never fakes them.

## Honesty rules

1. No fabricated progress numbers anywhere; segmented ring = state sweep.
2. Reduced motion (`QStyleHints.reducedMotion`) ⇒ rates ×0.12, particles 0,
   no pulses; colours + state text remain → still fully informative.
3. Neural layer is labelled a *visual representation of system activity*
   (brightness driven by real state/amplitude), not model internals.
4. Every count bounded; no per-frame allocation of node tables (computed
   tuples are cached; particles stepped at 60 Hz, painted only).

## Phase 9 — verified results

**Commits:** `59d17ad` (model + this doc), `0c6415e` (HudCanvas layers),
`c87163e` (tests). Suite: **1366 passed, 0 failed** (1349 baseline + 17 new
in `tests/test_core_visual.py`), 51 pre-existing warnings unchanged.

**Bug found & fixed during Phase 8 tests:** `_paint_core`'s frame-marks
section rebound the local `m` (motion profile) to a corner margin, so every
layer that later read `m.spin` / `m.ring_rate` raised `AttributeError`
mid-paint under a real widget paint (grab/render), aborting the QPainter and
hard-crashing Qt (0xC0000409). Fixed by renaming the margin to `mrg`
(`ui.py:952`). Diagnostic path: direct method calls passed but `grab()`
crashed → monkeypatch bisect → shadowed local.

**Boot verification (offscreen-safe, live app):** started `main.py`
(PID 15236); stderr shows normal startup (persona, spatial camera, event
bus) with **no Traceback, no paint/reactor errors**; stdout shows live
audio + vision exchanges; the app continued processing speech after boot.
One pre-existing non-fatal `WNDPROC/WPARAM TypeError` (PyQt6 tooltip
dispatch) and the known `QPropertyAnimation smoothPos` warning are
unrelated to this work.

**DoD checklist:**
- [x] Driven only by real state (`app_state.transition` listener; pulses
      deduped at 150 ms; timer never invents state).
- [x] No fake telemetry / progress % (status ring = `status_lit(state)`).
- [x] Status never colour-only (existing glyph/text line unchanged).
- [x] Bounded particles (hard cap 28, shrink-to-budget, seed-deterministic).
- [x] Reduced motion: rates ×0.12, particles 0, pulses suppressed,
      colour + text retained (tested in `TestHudCanvasWiring`).
- [x] Single state system (no second source of truth; legacy
      `PROCESSING` mapped, not duplicated).
- [x] Existing features preserved (full suite green; app boots and runs).
- [x] Incremental git checkpoints + push.
- [x] WAITING_CONFIRMATION/VERIFYING driven by real emitters only;
      PAUSED/COMPLETED stay in the automation HUD.
