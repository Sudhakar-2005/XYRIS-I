# Phase 2 — XYRIS I Architecture Compatibility Assessment

**Question answered:** how does the audited holo-gestures source (Phase 1) map onto XYRIS I's *existing* spatial/gesture architecture, and where is the smallest safe integration surface?
**Method:** static reading of XYRIS `core/spatial/*`, its wiring in `main.py`/`ui.py`, config layer, and tests — all file:line verified. **No code changed.**

---

## 1. XYRIS I's current gesture architecture (Verified)

### 1.1 Data flow

```
CameraManager (cv2 thread, camera_manager.py:144 capture loop)
  → frame callback → engine._on_camera_frame (engine.py:208-211, latest-frame + lock)
  → SpatialInteractionEngine._processing_loop (engine.py:213-276, daemon thread, ~30 fps cap :272)
      frame.copy() → HandTracker.detect (hand_tracker.py:65 MediaPipe mp.solutions.hands,
                        static_image_mode=False, max_num_hands=2, det .7 / track .6 :52-54)
      → GestureRecognizer.recognize (gesture_recognizer.py:39)          → GestureEvent
      → SafetyController.validate_event (safety_controller.py:63)        (blocks / rate-limits)
      → GestureInterpreter.interpret (state machine semantics)          → intent-bearing event
      → UIInteractionController.process_event (target hit-testing)
      → callbacks: on_gesture_event / on_pointer_move (engine.py:252-262, try/except-isolated)
  → main.py:963 _on_gesture_event (main.py:987-993): ui.emit_gesture_event("pointer_move"|"intent", …)
  → Qt signal hop _gesture_sig(str, object) (ui.py:5169, 5351) → ui.py:5407 _on_gesture_event
      → GestureCursorOverlay (lazy, ui.py:5399-5405) + intent handling
  ← state changes: _gesture_state_sig(str) (ui.py:5170, 5352) → ui.py:5429 (e.g. CAMERA_ERROR→"error")
```

### 1.2 Lifecycle & user controls (Verified)

| Mechanism | Evidence |
|---|---|
| Default OFF | `DEFAULT_GESTURE_CFG["gesture_enabled"] = False` (`memory/config_manager.py:426-433`); comment "off by default" (`ui.py:5386`) |
| Boot auto-start only if already enabled | `main.py:927-941` — builds + `start()` when config says on; logs `[Spatial] Gesture control enabled` / init errors |
| Lazy build | `_ensure_gesture_engine` idempotent, never starts (`main.py:943-965`); mediapipe import happens only on first use |
| User toggle | settings button `_gesture_btn` → `_toggle_gesture_control` (`ui.py:6762, 7646-7655`): flips persisted `set_gesture_enabled`, builds engine, starts; UI button state read at `ui.py:7624-7625` |
| Honest on/off feedback | log lines `"Gesture control OFF"` / `"Gesture control ON - show your hand"` (`ui.py:5444, 5447`) |
| Stop semantics | `engine.stop()` stops camera + joins thread + resets state (`engine.py:172-188`); `emergency_stop()` via safety (`:190-193`) |
| Camera error surfaced | state → `CAMERA_ERROR` (`engine.py:146`) → UI state string mapping incl. `"CAMERA_ERROR": "error"` (`ui.py:5421`) |
| Safety layer | emergency-stop blocks everything; **rate limiting**; `requires_confirmation()` — "Actions that always require confirmation regardless of input method" (`safety_controller.py:31, 63-98`); action log |
| Missing mediapipe | feature-guarded (`hand_tracker.py:15-18, 56-59`); boot prints `[Spatial] Gesture engine unavailable` (`main.py:937-938`) — never crashes the app |

**Verdict:** XYRIS already satisfies the structural privacy/lifecycle expectations the spec's Phase 6 asks for (opt-in config, real stop, error states, confirmation gate for consequential actions) — *better* than holo's load-time camera start with no stop API (Phase 1 §7).

### 1.3 Contracts (Verified — `core/spatial/interaction_state.py`)

- **`HandLandmarks`** (:62): 21 landmarks + handedness + confidence + palm/tips/hand_width.
- **`GestureEvent`** (:76): `gesture, intent, confidence, timestamp, hand, second_hand, pointer_position, drag_delta, zoom_delta, rotation_angle, metadata{}` — **extensible via `metadata`**; typed enums end-to-end (regression-tested: `test_recognizer_never_stores_a_raw_string_intent`).
- **`GestureSettings`** (:92): `confidence_threshold=.7, pinch_threshold=.05, pinch_cooldown_ms=300, pointer_smoothing=.3, min_hold_duration_ms=100, movement_threshold=.01, debounce_ms=50, mirror_mode=True, camera_index=0, enabled=False`.
- **`GestureType`** (:34): NONE, POINT, PINCH, OPEN_PALM, FIST, SPREAD, CONTRACT, ROTATE.
- **`GestureIntent`** (:46): POINTER_MOVE, SELECT, DRAG_START/MOVE/END, PAUSE, RESUME, CANCEL, ZOOM_IN/OUT, ROTATE_CW/CCW.
- **`InteractionStateMachine`** (:106+): validated transitions incl. LOST_HAND, LOW_CONFIDENCE, CAMERA_ERROR — anti-false-positive design ("Enforces valid state transitions and anti-false-positive logic", :109-110).
- Config→object mapping (`main.py:955-961`) passes 5 keys; **pinch/hold/cooldown/movement stay at dataclass defaults** (not user-configurable today).

### 1.4 Recognition logic detail (Verified — `gesture_recognizer.py`)

| Aspect | Implementation | Line |
|---|---|---|
| Pinch gate | `dist(thumb_tip, index_tip) < pinch_threshold` — **absolute normalized distance (.05), NOT hand-span-normalized** | `:83-84` |
| Pinch debounce | latch `_pinch_active` + `pinch_cooldown_ms=300` | `:86-91` |
| Pinch confidence | formula `0.8 + (1 - d/thr)·0.2` (cosmetic — derived from the same gate) | `:94` |
| Drag | pinch active + pointer delta > `movement_threshold (.01)` → `DRAG_MOVE` | `:103-120` |
| Finger extension | **tip-above-PIP y-comparison** (`lm[8][1] < lm[6][1]`, …) — orientation/mirror dependent, no span normalization | `:251-261` |
| Thumb | tip vs IP x by handedness | `:263-269` |
| Point/Open/Fist | boolean combos of extension flags, confidences **hard-coded** (0.9 / 0.85 / 0.8) | `:122-152` |
| Smoothing | EMA `alpha = pointer_smoothing (.3)`, history `deque(maxlen=5)` | `:283-294` |
| Two-hand zoom | palm-distance velocity (hist 10) > `.01` → SPREAD/CONTRACT, conf .75 | `:174-200` |
| Two-hand rotate | angle delta > 0.05 rad (~3°) → ROTATE, conf .7, raw-string intent **into metadata** | `:202-233` |
| Anti-noise | empty hands → NONE/0.0 (`:53-59`); interpreter gates low confidence (`test_low_confidence_blocked`); no per-frame identity/ghost logic (MediaPipe tracking provides within-Hands consistency) | — |

### 1.5 Interpreter semantics (Verified — `gesture_interpreter.py`)

- PINCH → `PINCH_DETECTED` → **held ≥ `min_hold_duration_ms (100)` → `SELECTING` → intent SELECT**; **release sooner → back to TRACKING, intent NONE** (`:195-219`) — *quick pinches are deliberately ignored*.
- In SELECTING, movement > threshold → `DRAG_START` (`:221-243`); release → `DRAG_END`/`HOVERING` (`:255-253`).
- OPEN_PALM → PAUSE; FIST → CANCEL (state map + tests `test_interpret_palm_pauses`, `test_interpret_fist_cancels`).

### 1.6 Test coverage today (Verified — `tests/`)

- `test_spatial.py` — **31 tests**: state machine (11), recognizer (**3**: no-hands, landmarks factory, `smooth_pointer`), interpreter (5, incl. low-confidence blocked), UI controller (4), safety (6 incl. rate-limiting, confirmation, action log), intent regression (2).
- `test_gesture_toggle.py` — **2 tests** (config toggle behavior).
- **Explicit gaps:** no geometric assertions that a synthetic point/pinch/fist/open pose actually classifies; no threshold/cooldown regressions; no camera-loss/`LOST_HAND` timing tests; no engine-loop tests. *(Exactly the coverage style holo's probe battery provides — Phase 1 §8.)*

### 1.7 Dead / incomplete seams discovered (governs Phase 3 design)

Verified by grep across app code (excluding tests):

1. **`"intent"` events are emitted but never handled.** `main.py:992-993` emits `emit_gesture_event("intent", str(event.intent.value))`, but `ui.py:_on_gesture_event` only branches on `"pointer_move"` and `"state_change"` (`ui.py:5410, 5413`) — **no `intent` branch exists** → intents are silently dropped.
2. **The `"state_change"` branch has no emitter.** `ui.py:5413-5427` maps states → cursor visuals, but nothing in app code emits name `"state_change"` (main emits `"intent"`) → cursor state styling is unreachable today.
3. **`_on_gesture_state` is a stub:** `pass  # Could update status bar or indicator` (`ui.py:5429-5431`), even though `main.py:994` emits the engine state name on every gesture event.
4. **No UI targets are registered anywhere:** zero `register_ui_target`/`register_target` calls in app code (only tests) → `UIInteractionController.process_event` (`engine.py:249`) has no targets to act on.

**Net effect today:** the gesture pipeline is complete through recognition/safety/interpretation and moves a **cursor overlay only** (`main.py:991` → `ui.py:5410-5412`); **no gesture has ever performed a UI action in XYRIS I.** Consequence for Phases 3–7: integrating holo's gesture *vocabulary* also requires completing this action-routing seam — with honest "no target" behavior when nothing is registered, and always through `SafetyController` (§1.2) with `CONFIRMATION_REQUIRED` respected (`safety_controller.py:31-42`, includes `"drop"`, `"delete"`, …).

## 2. holo ↔ XYRIS comparison matrix (evidence-backed)

| Dimension | holo-gestures (Phase 1) | XYRIS I (§1) | Compat |
|---|---|---|---|
| Runtime | Browser JS, rAF loop | Python 3.11 threads + Qt signals | Concepts only — **no code sharing** |
| Landmarks | MediaPipe Tasks JS, 21 pts, VIDEO, 2 hands | MediaPipe 0.10.21 `mp.solutions.hands`, 21 pts, 2 hands | **Aligned** (same landmark topology/vendor lineage) |
| Detection floors | det/presence/track `.6` + handedness `.5` sanity (chirality≠quality) | det `.7`, track `.6` (`hand_tracker.py:53-54`), confidence_threshold `.7` | Comparable; holo's documented chirality insight worth a comment/test on XYRIS side |
| Pinch metric | `d/thumb-index / span` (hand-size invariant), IN .30 / OUT .42 hysteresis, **2-frame earn** | absolute `d < .05`, latch + 300 ms cooldown, no hysteresis, no earn | **Divergent — highest-risk difference** (zoom/lens invariant vs fixed distance) |
| Pose rules | span-normalized curl ratios (open 1.45 / fist 1.05 / point / peace) | tip-above-PIP y-flags | Divergent; holo's is rotation/mirror-robust, XYRIS's is simpler |
| Tap vs drag | tap = release <450 ms & drift <60 px → **action** | tap <100 ms → **ignored**; SELECT only after 100 ms hold | Philosophies differ; both are deliberate, field-dated |
| Smoothing | One Euro (speed-adaptive; `1.3+.009v`, deriv 2.5) | EMA α=.3 | holo reduces lag-without-jitter; drop-in replace candidate behind `pointer_smoothing` config |
| Identity/ghosts | nearest-palm slot claim (260 px), ghost dedupe (1.35 spans / established-pair exemption), `SPAN_MIN`, 280 ms stale watchdog | relies on MediaPipe internal tracking + state machine LOST_HAND | holo adds *explicit* defenses XYRIS lacks; port as recognizer-level guards |
| Two-hand | pinch-both + ≥140 px separation + NaN floor + per-frame baseline re-anchor + scale clamps | any 2 palms + velocity/angle deltas, conf .75/.7, **no separation gate** | holo's separation/NaN guards fix real misfire classes (documented regressions) |
| Confidence | none per-pose (rule-based + tracker floors) | typed float, hard-coded per pose, gate `.7` in interpreter | XYRIS keeps contract; imported rules should produce honest confidence *ranges* |
| Camera lifecycle | auto-start at load, **no stop API** | config opt-in, stop/emergency-stop, error states | **XYRIS wins** — do not import holo's |
| Feedback UX | explicit status strings per failure (Phase 1 §6) | toggle log lines + state signal + cursor states | holo's finer failure vocabulary (BLOCKED/busy/no-device) worth mirroring in UI log text |
| Effects gating | ambient gestures default OFF behind `?adv`/F | N/A (no ambient gestures) | holo's "effects off by default" law governs which new gestures get enabled-by-default |
| Tests | probe battery: 26 assertions on synthetic hands, **verified 26/26 here** | 33 pytest tests, geometric-recognition gap | Probe technique directly portable to pytest via `HandLandmarks` factory (already exists: `test_make_hand_landmarks`) |
| Deps | none for server; JS vendored | optional `mediapipe==0.10.21` (pinned, feature-gated) | **No new dependency needed for any candidate port** |

## 3. Gap analysis — what holo proves that XYRIS lacks (prioritized)

| # | Gap in XYRIS | holo evidence | Impact if unaddressed | Port cost |
|---|---|---|---|---|
| G1 | **Pinch not span-normalized** (`gesture_recognizer.py:84` absolute .05) | holo `:366-376` field-tuned normalized metric + hysteresis | pinch works differently at camera distance/hand size; no IN/OUT hysteresis → chatter | Low — internal to recognizer, contract unchanged |
| G2 | **No earn-frames/temporal gating for poses** (state machine has hold≥100 ms for SELECT only) | holo laws §3.3: 2/4/8/24-frame earns killed flicker misfires | pose flicker can emit POINT/FIST/OPEN events at frame rate | Low-Med — recognizer/interpreter counters |
| G3 | **No tap action & no explicit flick/drag-release velocity** | holo tap rule + `peakVelocity` flick (`:559-584`) | no dismiss/quick-open vocabulary; DRAG_END carries no throw semantics | Med — new intents (`dismiss`/`fling` candidates) |
| G4 | **No ghost/hand-identity/stale guards** | holo three-layer phantom kill + slot identity + 280 ms reap (`:386-413, 445-468, 1132-1160`) | duplicate-hand or frozen-cursor class bugs possible (MediaPipe JS had them; Python side untested) | Med — add to tracker/recognizer with tests |
| G5 | **Two-hand actions lack separation/NaN guards** | holo `:599-603, 627-632` documented regressions | ghost second hand could trigger zoom/rotate storms | Low — gate before SPREAD/CONTRACT/ROTATE |
| G6 | **EMA-only pointer smoothing** | holo One Euro params (`:418-429`) | jitter vs lag trade-off fixed by speed-adaptive filter | Low — swap inside `_smooth_pointer` keeping config override |
| G7 | **Geometric recognition untested** | probe: synthetic hands → real engine → 26 named assertions | threshold changes unverifiable (spec demands regression proof) | Low — pytest port of probe pattern |
| G8 | **Failure vocabulary coarse** | 7 distinct status strings (Phase 1 §6) | user can't distinguish blocked/busy/no-camera | Low — log text only |
| G9 | **No PEACE-class "reset" gesture** | holo peace 8-frame/2500 ms (`:526-531`) | (optional UX gain) | Med — new GestureType + intent + UI action; **must pass safety/confirmation review** |

## 4. Conflicts / do-not-merge list

1. **Pinch philosophies** (normalized hysteresis vs absolute+cooldown): do not silently change XYRIS `.05` semantics without before/after evidence + tests; treat as experiment behind `GestureSettings` (G1 rollout must be reversible).
2. **Tap semantics**: holo treats <450 ms as *the action*; XYRIS treats <100 ms as *noise*. Adopting holo's tap changes SELECT behavior — needs its own test + doc, not a side effect.
3. **Confidence numbers**: holo has none per pose; do not invent fake confidences for imported poses — map to tracker-derived honest ranges or label "rule-based" (spec §2/§9).
4. **Camera lifecycle**: never port auto-start/no-stop (Phase 1 §7).
5. **Browser/server/deck/three.js/TTS**: out of scope entirely (Phase 0 §12).
6. **Ambient/effect gestures** (clap/ink/repulsor/tidy): holo itself demoted these after misfires (`:438-444`) — if ever added to XYRIS they ship **default OFF** with explicit labeling.
7. **`mediapipe` pin**: any assumption about MP API must respect `0.10.21` legacy `mp.solutions` constraint (`requirements.txt` comment) — no API migration smuggled in via this task.

## 5. Integration surface verdict

- **Ready-made seams (zero contract breakage):** `GestureEvent.metadata`, additive `GestureType`/`GestureIntent` enum members, `GestureSettings` fields, `SafetyController.validate_event` gate, Qt signal hop, `config_manager` `DEFAULT_GESTURE_CFG` pattern, lazy engine build, existing `test_make_hand_landmarks` factory.
- **No new dependencies required** for any prioritized gap (G1-G9): pure-python recognizer/tracker changes + tests.
- **Smallest safe integration = recognizer/interpreter hardening + new intent vocabulary + probe-style tests**, *not* a module merge, *not* a UI-framework change, *not* camera-policy change.
- **Risk register:** R1 threshold regression (mitigate: reversible settings + probe tests); R2 SELECT behavior change (mitigate: separate switch/tests); R3 real-camera false positives (mitigate: everything default-labeled, honest "Not tested on real camera" until Phase 7 live run); R4 mediapipe-missing machines (mitigate: keep feature-gating; tests must skip-with-reason, matching existing pattern).

## 6. Unknowns resolved vs remaining

**Resolved this phase:** XYRIS wiring, lifecycle, contracts, recognizer/interpreter semantics, safety layer, test inventory, settings toggle path, camera-at-boot gating, mediapipe pin & defaults.
**Remaining for Phase 3 (design):** exact picker of which candidate gestures map to which UI actions in XYRIS (needs a survey of dashboard/card interactions to define SELECT/DISMISS/RESET meaning safely); whether `_on_gesture_state` currently emits states beyond CAMERA_ERROR (grep shows mapping only); UI log line wording inventory for status upgrade (G8).

---
*Compliance: audit-first; file:line evidence for every architectural claim; no production code modified; distinctions between "holo has it", "XYRIS has it", and "proposed" kept explicit.*
