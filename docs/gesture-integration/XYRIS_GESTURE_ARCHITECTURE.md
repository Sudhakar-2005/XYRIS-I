# Phase 3 — Target Gesture Architecture for XYRIS I

**Purpose:** define the target architecture for adapting *verified* holo-gestures capabilities into XYRIS I, given Phase 1 (source audit) and Phase 2 (compatibility incl. dead-seam findings §1.7). **Design only — no production code in this phase.**

---

## 1. Goals & non-goals

**Goals**
1. Keep and harden XYRIS's native `core/spatial` stack (it is the safer, typed, tested base — Phase 0 §12).
2. Port the *field-proven rules* from holo (normalized pinch, earn-frames, guards, One Euro, status vocabulary, probe testing) as **evidence-backed improvements behind flags**.
3. Add the **missing action vocabulary** (tap-select, flick-dismiss, peace-reset-equivalent) and **complete the dead intent→UI seam** (Phase 2 §1.7) so gestures can actually do something — safely, honestly, and reversibly.
4. Prove every behavior change with **probe-style synthetic-landmark tests** before any default flips.

**Non-goals (rejected with reasons, Phase 2 §4)**
- No browser/WebGL/three.js/deck/QWebEngine (new heavy dep, wrong UI stack).
- No change to holo's camera lifecycle (XYRIS's opt-in/stop/error model wins).
- No `mediapipe` version/API migration (pin `0.10.21` legacy `mp.solutions` respected).
- No global synthetic clicks / no gesture reaching OS-level input (pyautogui) — gestures act only on **explicitly registered XYRIS-owned targets**.
- No gesture ever authorizes a consequential action (spec §2); `SafetyController.CONFIRMATION_REQUIRED` (`safety_controller.py:31-42`) always consulted.
- No ambient/effect gestures enabled by default (holo demoted them itself after field misfires, `holo.html:438-444`).

## 2. Design principles

| # | Principle | Concrete consequence |
|---|---|---|
| P1 | Additive contracts only | new enum members/settings fields; no signature breaks; existing 1478 tests keep passing |
| P2 | Default = current behavior | every behavior change ships behind a config flag whose **default reproduces pre-task behavior**; defaults flip only after Phase 7 live verification |
| P3 | Evidence before effect | each imported rule arrives with its Phase 1 citation + a new pytest proving it |
| P4 | Honest labeling | every failure/absence surfaces a truthful log/status line; "no target" logs `no gesture target`, never silence or fake success |
| P5 | Safety first | all events traverse `validate_event` (emergency-stop, ≤5 actions/s, log) and action dispatch consults `requires_confirmation` |
| P6 | Smallest diff | prefer recognizer/interpreter-internal changes + one new routing seam; no framework, dependency, or thread-model changes |
| P7 | Reversible | each stage independently revertible: flag off, config key deleted, or single-commit git revert |

## 3. Target architecture (layers & change points)

```
CameraManager ── unchanged lifecycle (opt-in start, stop, release; + ADD: last_error capture, G8)
   │
HandTracker ── unchanged MediaPipe config + ADD: stale/lost-hand observation & optional ghost guard (G4, flag)
   │
GestureRecognizer ── UPGRADE POINT 1 (G1/G2/G5/G6):
   │    span-normalized pinch + IN/OUT hysteresis + 2-frame earn (flag)
   │    pose earn-frames for POINT/FIST/OPEN (flag)
   │    two-hand separation gate before SPREAD/CONTRACT/ROTATE (flag)
   │    One Euro smoothing replacing EMA (flag; config `gesture_smoothing` preserved)
   │    new GestureType.PEACE detection (pose, not action)
   │    flick = high peak-velocity DRAG_END observation → metadata (flag)
   GestureEvent (typed, unchanged fields + metadata) 
   │
SafetyController ── unchanged core; new intents pass through same gate (rate-limit/emergency/log)
   │
GestureInterpreter ── UPGRADE POINT 2 (G3):
   │    tap-vs-drag: SELECT now also fires on quick-release (<450 ms & drift <60 px) when flag on;
   │    default off = today's ignore-short-pinch semantics (gesture_interpreter.py:210-212)
   │    new intents: DISMISS (fling), RESET (peace held 8 frames / 2500 ms cooldown)
   │
Action Router ── NEW COMPONENT completing the dead seam (Phase 2 §1.7):
       ui.py `_on_gesture_event` gains the `intent` branch
       `_on_gesture_state` implemented from the already-emitted state signal (cursor state map)
       dispatch = registry lookup only (register_ui_target), safety consult, honest no-op logs
   │
Qt UI: GestureCursorOverlay (existing) + settings toggle (existing) + log lines (G8 upgrade)
```

Thread model unchanged: camera thread → engine daemon thread → Qt signal hop (`_gesture_sig`/`_gesture_state_sig`, `ui.py:5169-5170`) — all new work happens either inside the engine thread (recognizer/interpreter) or the main thread (routing), no new locks.

## 4. Component specifications

### 4.1 Recognizer upgrades (G1, G2, G5, G6) — `core/spatial/gesture_recognizer.py`

| Change | Spec | Source evidence | Flag (new `GestureSettings`/config key, default = old behavior) |
|---|---|---|---|
| Normalized pinch | pinch metric `dist(thumb,index)/span` with `PINCH_IN=.30 / PINCH_OUT=.42` hysteresis + **2-frame earn**, replacing absolute `.05` latch when enabled | Phase 1 §3.1-3.2, `holo.html:368, 519-524` | `pinch_normalized` (bool, **False**) + `pinch_hysteresis` params |
| Pose earn-frames | POINT/FIST/OPEN_PALM must persist N consecutive frames before emitting (N: point 4, palm/fist 3 initial values) | holo `:490, 502, 519-531` earn laws | `pose_earn_frames` (int, **0 = off**) |
| Two-hand separation gate | suppress SPREAD/CONTRACT/ROTATE while palm distance < `140 px`-equivalent normalized bound **or** second hand just appeared (< `seen` grace) | holo `:627-632` ghost-regression lesson | `two_hand_min_separation` (float, **0.0 = off**) |
| One Euro smoothing | speed-adaptive filter (deriv cutoff 2.5, base 1.3 + 0.009·v) inside `_smooth_pointer`, dt clamped as holo | `holo.html:415-429` | `smoothing_filter` ∈ {`ema`,`one_euro`}, default **`ema`** |
| PEACE pose | index+middle extended, ring+pinky curled (span-normalized variant: tips `>1.45 / <1.15`) → `GestureType.PEACE`, confidence reported as pose-rule honesty (tracker-derived, no invented number) | holo `:381, 528-531` | detection always safe (pose only); activation gated by intent layer |
| Flick observation | on pinch release with `peakVelocity > 4`-equivalent, attach `metadata["flick"] = {speed}` — **no action by itself** | holo `:559-567, 574, 584` | `flick_detect` (bool, False) |
| Chirality comment/test | document that MediaPipe handedness score ≠ detection quality; keep `.7` det floor | holo `:1124-1129` | docs/tests only |

### 4.2 Interpreter upgrades (G3) — `core/spatial/gesture_interpreter.py`

| Change | Spec | Flag (default = old) |
|---|---|---|
| Tap action | while PINCH_DETECTED/SELECTING: release earlier than `min_hold_duration_ms` **but** within `tap_max_ms=450` and pointer drift `< tap_max_drift` → emit `GestureIntent.SELECT` (tap-to-activate on hovered registered target) instead of NONE | `tap_action` (**False**) |
| DISMISS intent | pinch release with flick metadata while a drag target exists → `GestureIntent.DISMISS` (new enum member) carrying `metadata["speed"]` | `flick_detect` |
| RESET intent | `GestureType.PEACE` held 8 consecutive interpreted frames with 2500 ms cooldown → `GestureIntent.RESET` (new enum member) | `peace_reset` (**False**) |
| Semantics preserved when flags off | identical NONE/SELECT/DRAG flow as today (`:195-284`) | — |

**Action class of new intents:** UI-state-only (cursor/reset/panel-visibility), classified **reversible**; still passes `SafetyController`; if any future target maps to a `CONFIRMATION_REQUIRED` name (`"drop"`, `"delete"`, …), dispatch must route to the existing confirmation flow — **never** execute directly.

### 4.3 Action Router (new seam) — `ui.py` (+ small `main.py` emitter fix)

- **Fix A (dead intent branch):** add `elif name == "intent":` in `_on_gesture_event` → forward to `_handle_gesture_intent(intent_str)`.
- **Fix B (dead state styling):** implement `_on_gesture_state(state)` using the existing `state_map` (currently inside the unreachable `"state_change"` branch, `ui.py:5414-5423`) so cursor state finally updates; single emitter = `_gesture_state_sig` already emitted at `main.py:994`. Dead `"state_change"` branch removed in implementation (audit shows no other emitter).
- **Dispatch table (capability matrix, §5):** intent → registered-target operation or honest log line. Lookup failure → `_log.append_log("GESTURE <intent>: no gesture target")` (P4).
- **Target registration:** begin with **zero registrations** (today's reality); Phase 7 UI survey selects first XYRIS-owned candidates (dashboard cards / panel buttons) and calls the existing `register_ui_target` (`engine.py:200-202`). Registry contents are explicit, enumerable, and unit-tested.
- **Never:** global mouse synthesis, keyboard synthesis, plugin/API calls directly from intents.

### 4.4 Camera/status honesty upgrade (G8) — `camera_manager.py` + `ui.py`/`main.py`

- `CameraManager.start()` currently returns bool only (`:66-98`) → add **`last_error`** (kinds: `not_opened`, `exception:<msg>`), set on failure, cleared on success.
- `toggle_gesture_control` failure line upgraded (`ui.py:5449`): distinguish *busy/blocked/missing* as far as cv2 allows — **only claim distinctions we can actually detect**; otherwise generic-but-true `camera unavailable (index N)`.
- Status vocabulary mapping table (Phase 1 §6) adopted for the strings we can support natively; holo-only browser strings (address-bar camera icon) **not** copied where false for desktop.
- `_on_gesture_state` (stub today) at minimum stops being a no-op for `CAMERA_ERROR`/`INACTIVE` → log/indicator (P4).

### 4.5 Config surface — `memory/config_manager.py`

New keys under `DEFAULT_GESTURE_CFG` (all defaults preserve pre-task behavior, P2):
`gesture_pinch_normalized`, `gesture_pose_earn_frames`, `gesture_two_hand_separation`, `gesture_smoothing_filter`, `gesture_tap_action`, `gesture_flick_detect`, `gesture_peace_reset` (+ thresholds mirroring `GestureSettings` if exposed). `get_gesture_settings()`/`is_gesture_enabled()`/`set_gesture_setting()` patterns reused; unknown-key guard (`:444-446`) extended accordingly. Config file is gitignored — never printed/staged (existing rule).

### 4.6 Test architecture (G7 → detailed in Phase 5 plan)

- New `tests/test_gesture_probe.py`: **synthetic-landmark probe battery** — port holo's method (Phase 1 §8): build pose fixtures via existing `test_make_hand_landmarks` factory (extended with holo's pose geometry: open/fist/point/peace/pinch at varied spans), drive `GestureRecognizer` + `GestureInterpreter` frame-sequences, assert: pose gates, earn-frame behavior, hysteresis, tap/flick/peace timing, two-hand gates, **no-raw-string-intent** invariant retained, safety interplay (rate limit not tripped by pose flicker).
- Existing suites must stay green (1478 baseline + 33 spatial).
- Behavior-flag matrix: same probe runs twice (flag off ⇒ golden current behavior; flag on ⇒ new expectations).

## 5. Gesture → action capability matrix (target state)

| Gesture (source evidence) | XYRIS intent | Acts on | Safety class | Default |
|---|---|---|---|---|
| Point → cursor move (existing) | POINTER_MOVE | cursor overlay | harmless | **ON today** |
| Pinch hold ≥100 ms (existing) | SELECT | registered target (when routed) | reversible | flag off until router lands |
| Quick pinch tap (holo `:576-583`) | SELECT (tap) | registered target | reversible | `tap_action` **off** |
| Pinch+move (existing) | DRAG_START/MOVE/END | registered target | reversible | on (as today) |
| Flick on release (holo `:559-584`) | DISMISS | registered target (hide/reopen-able state only) | reversible | `flick_detect` **off** |
| Peace held 8 frames (holo `:526-531`) | RESET | gesture layer only: cursor state, drag cancel, registered-target reset | reversible | `peace_reset` **off** |
| Open palm / Fist (existing) | PAUSE / CANCEL | interaction state machine | reversible | as today |
| Two-hand SPREAD/CONTRACT/ROTATE (existing) | ZOOM/ROTATE | *(no dashboard consumer yet — honest no-op)* | harmless | as today |
| Clap/ink/repulsor/tidy (holo ADV) | — | **not ported** in scope | — | N/A (holo's own default-off law) |

Recurring rule: **no gesture in this matrix can send email, delete files, trigger shutdown, or authorize anything** — enforced by (a) intents map only to registered XYRIS UI targets, (b) `SafetyController` gate, (c) `CONFIRMATION_REQUIRED` consult on any action name, (d) tests asserting the mapping table contains no restricted action names.

## 6. Data-contract changes (all additive)

| Location | Addition |
|---|---|
| `interaction_state.GestureType` | `PEACE` |
| `interaction_state.GestureIntent` | `DISMISS`, `RESET` |
| `interaction_state.GestureSettings` | fields for §4.1/§4.2 flags (defaults = legacy) |
| `GestureEvent.metadata` | `flick:{speed}`, `tap:bool` (existing dict — no schema break) |
| `memory/config_manager.DEFAULT_GESTURE_CFG` | §4.5 keys |
| `CameraManager` | `last_error` property |
| `ui.py` | `intent` branch, `_on_gesture_state` implementation, log strings |
| tests | `test_gesture_probe.py` + targeted additions |

Removed/changed: only the unreachable `"state_change"` branch (dead code, proven by Phase 2 §1.7.2).

## 7. Failure-state & labeling plan

| Condition | Surface | Wording rule |
|---|---|---|
| engine start ok/fail | existing log (`ui.py:5444, 5447-5449`) + `last_error` detail | actual cv2 result, no invented causes |
| state changes | `_on_gesture_state` → cursor style + log on error states | state names as-is (`CAMERA_ERROR`, `LOST_HAND`…) |
| intent with no registered target | log `GESTURE <intent>: no gesture target` | honest silence avoided (P4) |
| mediapipe missing | existing boot message (`main.py:937-938`) + toggle-time message | keep feature-gating |
| flags on/off | settings toggle label reflects enabled feature set | no claim of "verified" until Phase 7 |
| real-camera accuracy | docs/UI never claim accuracy numbers | "Not tested on this camera" until Phase 7 run |

## 8. Rollback design

Per stage: (1) config flag off → legacy path (primary); (2) delete config key → factory default (secondary); (3) git revert of the single stage commit (tertiary). No migrations, no schema, no persisted-state rewrites. `config/api_keys.json` edits (if any) are gitignored runtime state — documented in ROLLBACK style already used by the identity migration.

## 9. Open decisions carried to Phase 4 (Roadmap)

1. **First registered targets:** which XYRIS-owned widgets become gesture-selectable (needs dashboard/panel widget survey — candidate classes only after reading `ui.py` dashboard code in Phase 7 planning; zero until then).
2. Whether cursor-state fix (Fix B) ships as its own micro-stage ahead of any recognizer change (proposed: **yes — Stage 1**, smallest observable completeness win with zero behavior risk).
3. Threshold initial values for Python side: adopt holo's `.30/.42` in XYRIS's coordinate space — requires a scale-mapping note (holo `AMP=1.45` vs XYRIS normalized `0..1` + `mirror_mode`) → validated by probe, adjustable via settings.
4. Naming in user-facing labels: keep established engine terms (`Gesture control`), avoid introducing holo product names ("HOLO/JARVIS mode") into XYRIS UI; internal citations only.

---
*Compliance: design derives only from Phases 0-2 evidence (file:line cited); every proposed behavior change is flag-gated with default = legacy; safety/confirmation/authorization rules restated and testable; no code modified in this phase.*
