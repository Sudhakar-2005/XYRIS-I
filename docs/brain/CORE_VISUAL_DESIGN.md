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
