# Phase 5 — Gesture Test Plan

**Scope:** how every claim in this integration gets proven — unit/probe tests (offline, deterministic), suite/scorecard gates, and the one protocol that cannot be automated (real camera). Results are recorded only from actual runs; anything unrun stays **Not tested** (spec §9).

---

## 1. Test environment (measured)

| Item | Value |
|---|---|
| Interpreter | `D:\MarkLIV-venv\Scripts\python.exe` — **Python 3.11.9** |
| UI toolkit | **PyQt6** (`PyQt6>=6.6,<7`) — direct PyQt5 imports fail in this venv |
| Headless mode | `$env:QT_QPA_PLATFORM='offscreen'` required for `MainWindow` tests; `QApplication` must exist before `MainWindow` (process dies `0xC0000409` otherwise) |
| Full suite cmd | `$env:QT_QPA_PLATFORM='offscreen'; D:\MarkLIV-venv\Scripts\python.exe -m pytest tests/ -q --tb=short` → baseline **1478 passed / 0 failed** |
| Scorecard cmd | `D:\MarkLIV-venv\Scripts\python.exe tools/eval_scorecard.py` → baseline **472 / 0** |
| Spatial baselines | `tests/test_spatial.py` = **31** tests; `tests/test_gesture_toggle.py` = **2** tests |
| mediapipe | optional (`requirements.txt` pin `==0.10.21`); tests requiring real MP must `pytest.skip` **with reason** if `HAS_MEDIAPIPE` is False (match `hand_tracker.py:15-18` feature-gating) |
| Flake policy | no wall-clock sleeps in unit tests — inject fake clocks (peace 8-frame / 2500 ms and tap 450 ms boundaries tested at N±1 and ms±1); seeded jitter only; zero network |

## 2. Test layers

### L1 — Probe battery (ported method, holo `?probe=1`, Phase 1 §8)

**New:** `tests/test_gesture_probe.py` (+ `tests/gesture_fixtures.py` for pose builders).

| Fixture builder | Geometry (source) | Used for |
|---|---|---|
| `pose_open(span)` | tips curl ratio `>1.45` (`holo.html:377`) | open palm, pause |
| `pose_fist(span)` | tips `<1.05` (`:379`) | fist/cancel |
| `pose_point(span)` | index `>1.45`, others `<1.15` (`:380`) | point/cursor |
| `pose_peace(span)` | index+middle `>1.45`, ring+pinky `<1.15` (`:381`) | peace detect + RESET timing |
| `pose_pinch(ratio)` | thumb-index distance = `ratio × span` (`:376`) | pinch gates/hysteresis/earn |
| `pose_twohand(dist, …)` | two `HandLandmarks` at set palm distance | SPREAD/CONTRACT/ROTATE gates, separation gate |
| jitter(seed, σ) | seeded landmark noise | property: no exceptions, no raw-string intents |

Runs the **real** `GestureRecognizer` + `GestureInterpreter` frame-by-frame (synthetic hands → real engine — identical structure to holo's probe: `synthHand` → `ingestHands`).

**Probe assertions by stage:**

| Group | Cases (representative) | Stage |
|---|---|---|
| Golden (flag defaults) | pinch `<.05` fires once then latch; `<300 ms` re-pinch ignored; tip-vs-PIP point/fist/open classify; SELECT only at hold ≥100 ms; short pinch → NONE; movement >.01 in SELECTING → DRAG_START; release → DRAG_END; two-hand velocity sign → SPREAD/CONTRACT; angle >.05 rad → ROTATE; low conf blocked; no-hands → NONE/0.0 | 3 |
| G1 normalized pinch | IN `.30` needs 2 consecutive frames; N−1 frame ⇒ nothing; OUT `.42` releases; span=0.5 vs 1.5 hands behave identically (invariance) | 4a |
| G2 pose earn | point flicker (3 frames) ⇒ no event; 4 frames ⇒ event; palm/fist N-frame bounds | 4b |
| G5 two-hand gate | close palms (`<separation`) ⇒ no SPREAD/ROTATE; just-appeared second hand grace | 4c |
| G6 One Euro | step response: still-hand converges (jitter ↓ vs EMA), fast move lag ≤ EMA lag; dt-clamp no NaN | 4d |
| G3 tap/flick | release 449 ms & drift 59 px ⇒ SELECT(tap); 451 ms ⇒ not tap; drift 61 ⇒ not tap; flick speed boundary at threshold ±ε ⇒ DISMISS metadata only | 5b/5c |
| G9 peace/RESET | 8th frame fires, 7th doesn't; re-fire blocked at 2499 ms, allowed 2501 ms (injected clock); flag off ⇒ never | 5d |
| Safety | intent mapping table contains **no** name in `SafetyController.CONFIRMATION_REQUIRED` (`safety_controller.py:31-42`); rate limiter still trips at >5 events/s; emergency stop silences dispatch | 5/6 |
| Router honesty | intent with empty registry ⇒ log line `no gesture target`; `NONE` ⇒ no dispatch; registered target ⇒ callback fired exactly once | 1/6 |

### L2 — Existing-suite guards (regression net)

- Full suite after **every** stage (1478 baseline must never drop); spatial 31 + toggle 2 keep passing untouched.
- `test_recognizer_never_stores_a_raw_string_intent` guards enum additions (stays green through Stage 5).
- `tests/test_gesture_toggle.py` guards config plumbing extended in Stage 4.

### L3 — UI routing tests (new, Qt offscreen)

`tests/test_gesture_ui_routing.py`:
- `QApplication` fixture → `MainWindow` (existing pattern from prior sessions: stop `_metric_tmr`/`_tmr` before close; `face.png` absence tolerated).
- Emit `emit_gesture_event("intent", …)` / `emit_gesture_state(…)` across the real Qt signal (`ui.py:8571-8581`) → assert cursor `set_state` transitions, log lines, dispatch outcomes.
- Proves Stage 1's fixes **through the same signal path production uses** (not by calling slots directly).

### L4 — Camera-failure tests (no hardware needed)

- Monkeypatch `cv2.VideoCapture` → unopened handle ⇒ `CameraManager.start()` False, `last_error='not_opened'` (Stage 2); exception path ⇒ `exception` kind.
- Toggle-failure UI line asserted via log capture.
- **Never** open a real camera in CI-style tests (Windows device contention + flakiness).

## 3. Explicitly not covered by automation (must use Stage 7 protocol)

| Item | Why | Handling |
|---|---|---|
| Real-hand classification accuracy | needs human+camera | Stage 7 checklist, honest per-item labels |
| Real-lighting false positives | environment-dependent | Stage 7 |
| Camera busy/blocked OS dialogs | interactive | Stage 7 |
| GPU/CPU delegate perf on this laptop | hardware-specific | optional measurement; if unmeasured → **Not tested** |
| holo upstream repo state | network provenance | stays **Claimed** |

## 4. Stage 7 live checklist (result template — filled only during the run)

For each: `PASS / PARTIAL / FAIL / NOT TESTED` + one-line evidence (log excerpt or observation).

1. Toggle ON → camera opens, log `Gesture control ON — show your hand`, cursor appears with hand.
2. Cursor follows index at two distances (near/far) — tests span-invariance motivation (G1).
3. Pinch hold ≥100 ms → SELECT on registered target (Stage 6) (else: honest `no gesture target`).
4. Quick tap (flag on) → tap SELECT; same with flag off → no action (legacy).
5. Drag + flick (flag on) → DISMISS on target; target recoverable (reversible claim proven).
6. Peace 8-frame hold (flag on) → RESET observed; <8 frames → nothing.
7. Two-hand zoom ×2 distances → no ghost storm (separation gate).
8. Hand withdrawn >1 s → cursor state/lost-hand honesty (no frozen cursor).
9. Toggle OFF → camera releases (verify device indicator OS-side / no further frames).
10. Second app grabs camera → start fails → honest log wording (no invented cause).
11. mediapipe-missing simulation (if feasible via env) → honest feature-gated message.

## 5. Reporting rules

- A test exists ⇒ it ran or it's listed Not tested with reason. No aspirational "should pass".
- Suite/scorecard numbers in `IMPLEMENTATION_REPORT` come from the final actual run (§1 cmd).
- Probe counts stated as `N assertions` from pytest output, not copied from holo's README.
- If any critical DoD item fails live (Stage 7) → report headline **PARTIALLY COMPLETE** (spec rule), failures itemized.

---
*This plan was written before implementation (audit-first order preserved); stage column maps 1:1 to `GESTURE_INTEGRATION_ROADMAP.md`.*
