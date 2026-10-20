# Phase 4 — Gesture Integration Roadmap

**Purpose:** staged implementation plan for Phases 7-10, derived strictly from Phases 0-3. Each stage = small diff → tests → suite green → commit. **No stage flips a default without live verification (Stage 7).**

**Baseline recorded:** XYRIS `master` @ `1ddb98b` clean before this task; suite `1478 passed / 0 failed`; scorecard `472 / 0`; spatial tests `31 + 2`. Gesture source ZIP SHA-256 `927B2BBF…877876` untouched. Test command: `$env:QT_QPA_PLATFORM='offscreen'; D:\MarkLIV-venv\Scripts\python.exe -m pytest tests/ -q --tb=short`.

---

## Stage map

| Stage | Phase | Content | Behavior change? | Risk |
|---|---|---|---|---|
| 0 | 0-6 | Audits & design docs (this folder) | none | none |
| 1 | 7 | Dead-seam completion: cursor state + intent router skeleton | observable (cursor states now update; intents log honestly) | Low |
| 2 | 7 | Status honesty: `CameraManager.last_error` + truthful toggle/state logs | log text only | Low |
| 3 | 7 | Probe harness: synthetic-landmark fixtures + **golden tests of current behavior** | none (tests only) | Low |
| 4 | 7 | Recognizer hardening **behind flags** (G1/G2/G5/G6) | none while flags default off | Med |
| 5 | 7 | New vocabulary **behind flags** (tap/DISMISS/peace-RESET, G3/G9) | none while flags default off | Med |
| 6 | 7 | First registered gesture targets (UI survey → explicit registrations) | cursor/intent → real target actions (reversible ones only) | Med |
| 7 | 8 | Live verification on this machine's camera; honest result labels; default-flip decision | possibly flips flags ON in user config only (gitignored) | Med |
| 8 | 9-10 | Full validation (suite + scorecard + py_compile + docs sync) + IMPLEMENTATION_REPORT | none | Low |

---

## Stage 1 — Dead-seam completion (smallest completeness win first, Phase 3 §9.2)

**Files:** `ui.py` (`_on_gesture_event`, `_on_gesture_state`, new `_handle_gesture_intent`), possibly `main.py` (leave emitters as-is: `intent` + state signal already emitted).
**Diff intent:**
1. Implement `_on_gesture_state(state)` with the state→cursor map lifted from the unreachable `"state_change"` branch (`ui.py:5414-5423`); keep `"state_change"` branch removed only after grep proves no emitter (Phase 2 §1.7.2) — removal happens in this stage as dead-code cleanup with test proof.
2. Add `elif name == "intent":` → `_handle_gesture_intent(data)`; ignore `"none"`; empty registry ⇒ `_log.append_log("GESTURE <intent>: no gesture target")` (P4 honesty), rate-limited/deduped to avoid log spam (one line per intent kind per state session, design detail at implementation).
3. Registry: use existing `register_ui_target`/`UIInteractionController` if its API fits the Qt-side dispatch; otherwise a thin intent→callable dict inside `ui.py` gated by explicit registration — **decision made by reading `ui_controller.process_event` in implementation prep** (no guessing; if its target model doesn't fit Qt slots, the thin dict is chosen and documented).
**Tests:** new `tests/test_gesture_ui_routing.py` (offscreen): intent signal → router logs no-target line; state signal → cursor state transitions; `NONE` intent produces no dispatch; dead-branch removal proof (grep-based assertion not needed — compile + behavior tests suffice).
**DoD:** suite green (1478+new); manual offscreen boot shows cursor state changes on emitted states.
**Rollback:** revert single commit.

## Stage 2 — Status honesty (G8)

**Files:** `core/spatial/camera_manager.py` (`last_error`: `not_opened` / `exception`), `ui.py:5449` wording, `_on_gesture_state` error-state logging (`CAMERA_ERROR` at minimum).
**Wording rule:** only distinctions cv2 can actually provide; never invent "blocked vs busy" on Windows where cv2 gives `isOpened()==False` generically → honest generic string with index (`Phase 3 §4.4`).
**Tests:** unit tests simulating failed open (monkeypatch `cv2.VideoCapture` to return unopened handle) → `last_error` set; toggle-failure log assertion; no real camera needed.
**DoD:** suite green; boot-with-gesture-disabled unchanged output.

## Stage 3 — Probe harness (G7) — golden baseline BEFORE any hardening

**Files:** new `tests/test_gesture_probe.py` (+ shared fixture helper `tests/gesture_fixtures.py` if size warrants).
**Content:** holo-style pose synthesis on `HandLandmarks` (span-varied open/fist/point/peace/pinch/two-hand sets) through **unmodified** `GestureRecognizer` + `GestureInterpreter`, asserting **today's** outcomes (golden): pinch absolute-.05 gate + 300 ms cooldown latch, tip-vs-PIP flags, SELECT after 100 ms hold, short pinch → NONE, DRAG thresholds, two-hand velocity/angle rules, low-confidence blocked, no-raw-string intent, rate-limit interplay.
**Also ported:** a mini-"noframeerr" equivalent — repeated `recognize()` calls never raise across randomized-but-seeded landmark jitter (property-style, seeded — deterministic, no flakiness).
**DoD:** new tests green with **zero** production diffs in this stage (tests-only commit); this locks pre-task behavior permanently (spec regression guarantee).
**Explicitly Not tested here:** real camera accuracy (deferred to Stage 7, labeled honestly).

## Stage 4 — Recognizer hardening behind flags (G1/G2/G5/G6)

**Order within stage (separate commits):** 4a normalized pinch + hysteresis + earn → 4b pose earn-frames → 4c two-hand separation gate → 4d One Euro smoothing (replacing EMA only when `smoothing_filter=one_euro`).
**Rule:** every flag default = legacy (Phase 3 P2) → **Stage 3 golden tests must pass unchanged with defaults**; each flag additionally has probe tests in ON state with Phase 1 citations in docstrings (`holo.html:368` etc.).
**Settings plumbing:** `GestureSettings` fields + `DEFAULT_GESTURE_CFG` keys + `main.py:955-961` mapping extension.
**DoD per sub-stage:** suite green (golden + new ON tests); no config ⇒ byte-identical decisions (asserted by golden suite).

## Stage 5 — New vocabulary behind flags (G3/G9)

**Order:** 5a `GestureType.PEACE` detection (pure pose, safe) → 5b interpreter `tap_action` (SELECT on quick release, `<450 ms & drift<60 px` semantics from `holo.html:576-583`) → 5c `DISMISS` intent on flick metadata → 5d `RESET` intent on 8-frame peace + 2500 ms cooldown.
**Enum additions** in `interaction_state.py` (additive; regression test `test_recognizer_never_stores_a_raw_string_intent` must keep passing).
**Safety:** each new intent asserted to bypass `CONFIRMATION_REQUIRED` names (mapping-table test from Phase 3 §5); rate limiter still bounds 5/s.
**DoD:** suite green with defaults off; ON-state probe coverage for timing windows (earn/cooldown boundaries: 7 vs 8 frames, 2499 vs 2501 ms — clock injected, no sleeps).

## Stage 6 — First registered targets

**Pre-work (read-only survey):** enumerate XYRIS-owned dashboard/panel widgets suitable as *reversible* gesture targets (open/close/toggle-class only). **Candidates named only after reading the code** — no assumptions in this doc.
**Diff:** register 1-2 explicit targets; wire SELECT/DISMISS/RESET semantics to reversible UI ops; log every dispatch; if `CONFIRMATION_REQUIRED` name would ever match, route to existing confirmation flow (test forces this path).
**Tests:** offscreen end-to-end: emit intent through signal → registered target callback fired; unregistered → honest no-target line; confirmation-name mapping blocked test.
**DoD:** suite green; no pyautogui/global input anywhere (grep proof recorded in report).

## Stage 7 — Live verification (Phase 8)

**Setup:** real camera on this machine, app running normally (not offscreen) — camera lifecycle: user toggle path (`ui.py:7646`) observed working; indicator/log lines verified.
**Protocol (honest labels):** scripted checklist — hand present/lost, pinch hold/tap/flick, peace hold, two-hand zoom at 2 distances, camera busy (second app), camera off toggle. Record each: works / partially / fails / not tested. **No screenshots needed unless UI evidence requested; no numbers invented.**
**Optional flag-flip decision:** if verified, flip **user config only** (gitignored `config/api_keys.json` gesture keys or documented `set_gesture_setting` calls) — **repository defaults remain legacy** unless the spec's DoD explicitly requires default-on after evidence; decision recorded in IMPLEMENTATION_REPORT with the evidence table.
**Failure rule:** any critical item failing ⇒ report states PARTIALLY COMPLETE (spec), no workarounds claiming success.

## Stage 8 — Validation & reporting (Phases 9-10)

1. Full suite + scorecard + `py_compile` on touched files; confirm 1478-baseline never regressed.
2. Docs sync: this roadmap's status table updated (done/not-done per stage), `GESTURE_TEST_PLAN` results section filled with real runs only.
3. `IMPLEMENTATION_REPORT.md` in the spec's 17-item format with evidence categories (Verified/Implemented/Tested/Partially tested/Not tested/Blocked).
4. Commit strategy: one commit per stage (auditable, revertable), pushed only after suite green; ZIP + vendor source never committed into XYRIS repo.

## Explicit exclusions (carried from Phase 3 §1)

Browser/deck/three.js/QWebEngine · holo server APIs (`/api/notes|tree|props|state|diag`) · holo camera lifecycle · CDN fallback patterns · ambient ADV gestures (clap/ink/repulsor/tidy) · `mediapipe` pin change · global synthetic clicks/keyboard · wake-word/audio changes · identity-migration regressions (JARVIS labels, autostart ids, clone URL — untouched per rulings).

## Definition-of-Done checkpoints (spec §13 mapping)

- [ ] Every stage's tests exist and pass; golden baseline untouched by flag defaults.
- [ ] `gesture≠authorization` test: restricted-action names never reachable from intents.
- [ ] Camera opt-in + stop control verified in Stage 7 (existing behavior, documented with evidence).
- [ ] Honest status labels implemented (Stage 2) and no accuracy claims anywhere.
- [ ] All 8 required docs present; final report uses evidence categories; PARTIALLY COMPLETE declared if any critical DoD fails.
- [ ] Repo state recorded before/after; suite/scorecard numbers real.
