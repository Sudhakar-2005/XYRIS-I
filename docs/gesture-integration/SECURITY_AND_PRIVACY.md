# Phase 6 — Security & Privacy Design (Gesture Integration)

**Purpose:** the security/privacy rules this integration must uphold, the evidence that XYRIS I already upholds them, what holo-gestures gaps we deliberately do **not** import, and how compliance is verified. Evidence = file:line; no clearance/legal claims (spec §11).

---

## 1. Asset & data-flow inventory (what exists today — Verified)

| Asset | Flow | Egress |
|---|---|---|
| Camera frames | `cv2.VideoCapture` → RAM (`CameraManager._frame`, `camera_manager.py:144+`) → copied in engine loop (`engine.py:220-222`) → MediaPipe **in-process** (`hand_tracker.py:65`) → 21 landmarks only | **None.** No code path serializes frames; landmarks stay in-process (Phase 1 §7 method applied to XYRIS side: absence verified by grep — no frame/landmark upload in `core/spatial/*`; app's network layers (FastAPI dashboard, Gemini, search) are fed neither) |
| Landmarks/events | recognizer → safety → interpreter → Qt signals (`ui.py:5169-5170`) → cursor/log | In-process only |
| Config flags | `config/api_keys.json` (gitignored) — incl. `gesture_enabled`, `assistant_name` | never printed/staged (existing migration rule) |
| holo source ZIP/vendor | read-only inputs for audit | never committed into XYRIS repo (roadmap Stage 8) |

**Camera lifecycle (already spec-conformant, Phase 2 §1.2):** default **OFF** (`config_manager.py:427`), start only via persisted opt-in (boot `main.py:933-936` or settings toggle `ui.py:7646`), real **stop** releasing the device (`engine.py:172-188` → `camera_manager.py:100-118` `cap.release()`), `emergency_stop` (`engine.py:190-193`), `CAMERA_ERROR` surfaced (`engine.py:146` → `ui.py:5421`), user-visible toggle + log lines (`ui.py:5444-5449`).

**Deliberately NOT imported from holo (Phase 1 §7 gaps):** load-time auto-`startCamera()` with no user gate; absence of any `track.stop()`; browser CDN-fallback loading. holo is a *source of rules*, not of lifecycle.

## 2. Hard rules (spec §2/§6 restated as testable constraints)

| # | Rule | Enforcement point(s) | Verification |
|---|---|---|---|
| R1 | **Gesture ≠ authorization.** No gesture dispatch may execute a restricted action name | `SafetyController.CONFIRMATION_REQUIRED` (`safety_controller.py:31-42`: send_email, delete, remove, uninstall, format, drop, kill, shutdown, restart) consulted by Action Router before dispatch; restricted names must route to existing confirmation flow or refuse | Unit test: capability-mapping table (Phase 3 §5) asserted disjoint from `CONFIRMATION_REQUIRED`; forced-match test proves refusal/confirmation path (Stage 5/6 tests) |
| R2 | **No global input synthesis.** No `pyautogui`/keyboard/mouse takeover reachable from gesture intents | Router dispatches only to explicitly registered XYRIS-owned targets (Stage 6); grep-proof of `pyautogui` absence in gesture files recorded in report | Code grep during Stage 6 DoD + report evidence |
| R3 | **Camera is opt-in with visible control + stop** | existing toggle path + boot gating; Stage 2 adds honest failure text; Stage 7 checklist items 1/9/10 | Live checklist (Test Plan §4), plus toggle unit tests |
| R4 | **Honest status labels** — never fake success, never invented causes | Stage 2 wording rules (`GESTURE_TEST_PLAN` §L4); no-registry intents log `no gesture target` | Unit tests for log lines; report evidence categories |
| R5 | **Rate limit + emergency stop apply to all events incl. new intents** | `validate_event` (`safety_controller.py:63-88`, 5 actions/s, emergency blocks all, 1000-entry audit log) — new intents pass through unchanged engine gate (`engine.py:240-243`) | Existing safety tests (6) + new-intent pass-through test; emergency-stop silences router test |
| R6 | **Consequential UI actions keep their confirmations** regardless of input method | Router consults `requires_confirmation` (`safety_controller.py:90-92`) for any action string it would invoke | Forced-match unit test |
| R7 | **Secrets hygiene** | config keys gitignored; never print/stage; ZIP SHA recorded not copied; no keys introduced by this task | `git status` review before commits; no new secret files |
| R8 | **No new network surfaces** | no new HTTP endpoints/sockets; holo `server.py` APIs not adopted; loopback dashboard unrelated to gestures | Diff review (grep for `socket|uvicorn|requests` in touched files = none) |
| R9 | **Optional-dependency safety** | mediapipe absent ⇒ feature disables with honest message (`hand_tracker.py:56-59`, `main.py:937-938`); tests skip-with-reason | Existing boot path + Test Plan §1 skip policy |
| R10 | **Vendor/license hygiene** | holo MIT + MediaPipe Apache-2.0 + three MIT attribution noted in report; **no asset/code copy without attribution**; CC0 props *not* reused (unverified) | Report §license section; no `props/`/`vendor/` files in repo |

## 3. Threat notes & residual risks (honest)

| Risk | Assessment | Handling |
|---|---|---|
| Malicious hand data → UI action | landmarks are numeric; worst case = false SELECT/DISMISS on *registered reversible targets*; confirmation-gated actions unreachable (R1) | safety gate + reversible-only target set + rate limit |
| Frozen/duplicate phantom cursor (holo's field bugs) | possible on XYRIS side too (untested) → misleading pointer | Stage 4 guards (earn/span/separation) + Stage 7 item 8 (hand-withdrawal honesty) |
| Camera device contention / other app peeking | cv2 device is exclusive on Windows while open; OS-level indicators belong to the OS | Stage 7 item 10; stop control verified |
| Log-line information leak | logs contain state names/intent strings only — no frames, no landmarks payloads, no secrets | existing log surfaces reviewed in Stage 2 |
| Insider tampering of flags in config | gitignored runtime state; toggles only affect gesture layer, never auth | config writes limited to `set_gesture_setting` allow-list (`config_manager.py:444-446`) |
| holo provenance (ZIP without git) | license texts present locally; upstream not network-verified | labeled **Claimed** where applicable (Phase 1 §1); no clearance asserted |

## 4. Privacy-facing user communication (honesty plan)

- Toggle ON log: `Gesture control ON — show your hand` (existing) + Stage 2 honest failure variants.
- Docs/report state plainly: *frames are processed in-process and never transmitted by the gesture stack* — phrased as code-verified absence (static), and live-verified only to the extent Stage 7 actually observes (e.g., no network capture performed ⇒ we do **not** claim runtime network-proofing unless a capture is actually run; if run, it is recorded with its method).
- No accuracy/`AI-powered` marketing language; status strings describe actual machine state (holo vocabulary adapted to desktop truth).

## 5. Compliance checklist (spec §6/§12 gates)

- [ ] R1-R10 each map to ≥1 test or recorded observation before final report.
- [ ] Camera opt-in/stop/indicator evidenced (existing code citations + Stage 7).
- [ ] No gesture path to `CONFIRMATION_REQUIRED` actions (disjointness test).
- [ ] No global input synthesis in gesture files (grep evidence in report).
- [ ] No new network surface (diff evidence).
- [ ] ZIP/vendor untouched; nothing from `props/` reused; attributions listed.
- [ ] Config/secrets rules unchanged; `git status` clean of unintended files at each commit.
- [ ] Residual risks listed above restated in `IMPLEMENTATION_REPORT` (no risk silently dropped).

---
*Written before implementation; every "already compliant" claim cites code read in Phases 0-2. Verification happens in Stages 1-7 — claims stay pending until their cited test/observation actually runs.*
