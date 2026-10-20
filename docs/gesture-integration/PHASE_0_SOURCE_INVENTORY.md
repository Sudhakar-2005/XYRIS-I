# Phase 0 — Source Inventory

**Task:** Gesture Intelligence Integration (holo-gestures → XYRIS I)
**Status:** Phase 0 COMPLETE — discovery only, **no production code changed** (spec §12 gate: implementation begins only after Phases 0–2 evidence).
**Date:** 2026-10-19

---

## 1. Recorded repository states (before any changes)

| Repository | State |
|---|---|
| XYRIS I (`C:\Users\Toshiba\Downloads\Mark-LIV-main`) | branch `master` @ `1ddb98b`, working tree **clean**, origin `https://github.com/Sudhakar-2005/xyris-i.git` |
| Gesture source archive | `C:\Users\Toshiba\Downloads\holo-gestures-main.zip` — 29,870,840 bytes, SHA-256 `927B2BBF4DEB1C2927D1ACAB5F5D645675DDF33F254E3451E82839BE20877876` (original ZIP will never be modified) |
| Gesture source extraction | `C:\Users\Toshiba\Downloads\holo-gestures-main\holo-gestures-main\` — **nested real root** (outer folder wraps inner folder); **no `.git` directory** in the extraction |
| XYRIS test baseline | `pytest tests/ -q` → **1478 passed / 0 failed**; `tools/eval_scorecard.py` → 472 / 0 |

---

## 2. Source location & provenance

- Local ZIP + nested extraction (paths above). Not a git working copy — provenance taken from its own `README.md:17`: upstream `https://github.com/zubair-trabzada/holo-gestures.git` ("HOLO", Jarvis V7 prototype).
- Author/license holder: `LICENSE` → **MIT © 2026 Zubair Trabzada / AI Workshop Studio LLC**.
- Claimed upstream origin was not network-verified in Phase 0 (audit detail, Phase 1).

## 3. File inventory (measured)

| Path | Size | Role |
|---|---|---|
| `holo.html` | 88,246 B / **1,578 lines** | The entire application: UI, CSS, hand-tracking pipeline, gesture logic, 3D deck, probe battery |
| `server.py` | 7,487 B / **180 lines** | Stdlib-only HTTP server, port 4890 |
| `holo.json` | 15 B | `{"folder": ""}` — markdown-notes root setting |
| `LICENSE` | 1,426 B | MIT |
| `README.md` | 5,152 B / 134 lines | Install + gesture table + claims |
| `.gitignore` | 31 B | ignores `state/`, `server.log`, `__pycache__/` |
| `vendor/` (root) | 5 files, ~9.2 MB | `hand_landmarker.task`, `vision_bundle.mjs`, `three.module.js`, `GLTFLoader.js`, `BufferGeometryUtils.js` |
| `vendor/wasm/` | 4 files, ~22.8 MB | MediaPipe wasm runtime |
| `props/` | 2 files, ~18.5 MB | `.glb` 3D models (incl. Smithsonian Apollo 11 scan per README) |
| `assets/` | 1 file, 334 KB | `banner.png` |
| `sample-notes/` | 6 small files | demo markdown (`BUILDS/`, `JARVIS/`) |
| `state/` | `holo-state.json`, `holo-diag.json` | runtime-written, gitignored |

**Absent (measured):** no `requirements.txt`, no `package.json`, no test directory/framework config, no CI, no `setup.py`, no `.git`.

## 4. Tech stack (measured from code, not README claims)

- **Client:** single-file browser app — vanilla JS + CSS 3D; **three.js** (self-hosted `vendor/three.module.js`).
- **Hand tracking:** Google **MediaPipe Tasks Vision** (`HandLandmarker`), **self-hosted** from `/vendor/` with **CDN fallback** only if local import fails (`holo.html:1056-1069`); `runningMode:'VIDEO'`, `numHands:2`, confidence floors `minHandDetection/Presence/Tracking = .6` (`holo.html:1076-1080`); **GPU delegate with CPU fallback** (`holo.html:1081-1083`).
- **Camera:** `navigator.mediaDevices.getUserMedia({video:{width:1280,height:720}})` (`holo.html:1101`); detection runs in a `requestAnimationFrame` tick that skips duplicate video frames via `lastVideoT` (`holo.html:1116-1120`).
- **Server:** Python 3 standard library only — `ThreadingHTTPServer(("127.0.0.1", PORT))` (`server.py:180`), port `HOLO_PORT` env / default **4890** (`server.py:21`). **Loopback bind only.**
- **No cloud, no API keys** in the tracking path (README claim consistent with observed code; browser speech synthesis used only in opt-in JARVIS mode, `holo.html:717-731`).

## 5. Server surface (file:line)

| Route | Direction | Behavior |
|---|---|---|
| `GET /`, `/holo.html` | serve | `holo.html` with boot-copy fallback (`server.py:107-115`) |
| `GET /vendor/*`, `/props/*` | serve | static; `normpath` + `".." not in safe` traversal guard, prefix allow-list (`server.py:124-137`) |
| `GET /api/props` | serve | first ≤6 `props/*.glb` names (`server.py:116-123`) |
| `GET /api/notes` | serve | markdown notes from `holo.json` folder (`server.py:138-139`) |
| `GET /api/tree` | serve | folder tree for orb spawn (`server.py:140-141`) |
| `POST /api/state` | ingest | `{event, card}` + `ts` → atomic write `state/holo-state.json` (`server.py:160-176`) — README: future Jarvis hook, **nothing reads it yet** |
| `POST /api/diag` | ingest | client crash report → `state/holo-diag.json` (`server.py:146-159`) |

## 6. Client pipeline & entry points (file:line)

- Boot: `requestAnimationFrame(loop)` (`holo.html:1048`) → `startCamera()` called at load (`holo.html:1168`).
- Track tick (`holo.html:1116-1166`): frame-change guard → `updateMap` (screen mapping, `AMP = 1.45` at `:183`) → `landmarker.detectForVideo` → handedness `.5` sanity floor (explicit comment: handedness score is **chirality confidence, not detection quality**, `:1124-1129`) → **ghost-hand dedupe** (`:1132-1160`, 1.35 hand-span merge for non-established pairs; never merge two established hands — that's a clap) → `assignHands` nearest-palm slot claiming (`:392-405`, 260 px claim radius) → `ingestHands`.
- Pose metrics (`handMetrics`, `holo.html:369-385`): `span = dist(wrist, middle-MCP)`; normalized curl ratios; derived booleans —
  `open: tips>1.45 all` (`:377`), `openish >1.32` (`:378`), `fist <1.05 all` (`:379`), `point` (`:380`), `peace` (`:381`), `pinch = dist(thumb-tip,index-tip)/span` (`:376`).
- **Pinch gates:** `PINCH_IN = .30`, `PINCH_OUT = .42`, `PINCH_EARN = 2` consecutive frames (`:368`, enforced `:519-524`); release above `.42` (`:524`).
- **Noise floor:** `SPAN_MIN = .025` — hands under 2.5% of frame dropped as background (`:448`, `:475`).
- Temporal earn patterns: peace held **8 frames** → full restore, 2500 ms cooldown (`:528-531`); ink strokes need 4 frames (`:490-496`); clap cooldown 1100 ms (`:655`); quick-pinch tap <450 ms window (README `:151` + tap logic).
- **Hand state:** per-slot state machine `smooth/trail/open/fist/pinch/seen/upT…` (`:431-432`), One-Euro-style smoothing, stale-hand watchdog `reapStale()` (`:1040`).
- **Outbound events:** `postState(event, card)` debounced 120 ms → `POST /api/state` (`:709-715`); `postDiag(patch)` → `POST /api/diag` (`:454-455`); statuses shown in `statusEl` with explicit strings (`:1055, 1071, 1115, 1106-1110, 1088`): `loading hand tracking… / tracking core loaded · initializing… / hands live · on-device / camera BLOCKED · …Allow → RETRY / no camera found / camera is busy in another app / tracking failed @stage · RETRY`.
- **Fallbacks:** mouse (`pointerdown` on deck/reader, `:362, :1236`) + keyboard (`h/v/d/f/j`, `:1271-1274`); UI toggles for hands/camvis/debug/fx/reset (`:1253-1270`).
- **Crash/robustness:** WebGL init failures posted via `postDiag` (`:832-835`), tracking-init failures carry stage/error/stack (`:1089-1092`), `LOOP_ERRS` counter surfaced in probe (`:1047, :1573`).

## 7. Gesture vocabulary (source side)

| Gesture | Rule (holo.html) | Effect |
|---|---|---|
| Pinch | `.30` in / `.42` out + 2-frame earn | grab/drag cards |
| Quick pinch (tap) | pinch <450 ms | fan out orb / open note |
| Flick | velocity throw | dismiss card off-screen (`:296`) |
| Two-pinch | both hands pinch | scene zoom / twist rotate |
| Two-hand stretch (held card) | spread apart | read big / crush small → spoken summary |
| Peace ✌ held 8 frames | `:528-531` | full reset (2.5 s cooldown) |
| Fist | `tips<1.05` | cancel / force-pull (effects mode) |
| Point | index ext + others curled | cursor + ink (effects mode) |
| Open palm | `tips>1.45` | pause / repulsor (effects mode) |
| Clap | two palms + rush heuristic (`:653-664`) | clear ink/restore |
| Tidy sweep | two-palm sweep (ADV) | regrid loose cards (`:700-707`) |

*(Mapping against XYRIS enums in Phase 2.)*

## 8. Dependencies & licenses (inventory level)

| Component | Version/source | License |
|---|---|---|
| holo-gestures itself | local ZIP/README | **MIT** (Zubair Trabzada / AI Workshop Studio LLC 2026) |
| MediaPipe Tasks Vision (JS + wasm + model) | self-hosted `vendor/` (+ jsdelivr 1.0.1 fallback) | **Apache-2.0** (Google) |
| three.js + examples (`GLTFLoader`, `BufferGeometryUtils`) | self-hosted `vendor/` | **MIT** |
| Smithsonian Apollo 11 `.glb` (README attribution) | `props/` | README states CC0 — *license file for models not present in repo; verify in Phase 1 before reuse* |
| Python server | stdlib only | no third-party Python deps; **nothing to pip-install** |

Reuse consequence: any copied code/paths must carry MIT + Apache-2.0 + MIT attribution notices (spec §11: no clearance claims; this inventory asserts **license text as found**, not legal advice).

## 9. Tests present in source

- **No unit-test framework.** In-page probe battery: append `?probe=1` → 1,500+ line async battery (`holo.html:1288-1574`) drives **synthetic hands through the real engine** (`synthHand` `:1171+`, `ingestHands`), asserts named conditions via `ok(name, cond)` (`:1289-1290`), counts frame errors (`:1573`), and reports **`PROBE <pass>/<total>` in `document.title`** (`:1575`); prior run recorded **26/26** (session evidence, re-run in Phase 5 before quoting as current).
- `?sim=1` scripted demo choreography (`:1170+`, `:163`), `?adv` effects flag (`:444`).
- Playback modes stay positional by design (comment `:1162-1163`).

## 10. Security & privacy observations (inventory level; full treatment in Phase 6)

- Server binds **127.0.0.1 only** (`server.py:180`); path traversal guarded (`server.py:127-128`); state files local, gitignored.
- Camera frames processed **in-page**; no frame upload path observed (README `:13` claim consistent with code). CDN import is the only network fallback, and only for JS/wasm assets (`:1066-1068`).
- **Privacy gaps to carry forward (evidence, not verdicts):** (a) `startCamera()` fires at page load (`:1168`) — no explicit user gesture before camera; (b) **no stop/release function found** — no `getTracks()`/`track.stop()` anywhere in `holo.html`; `camOn` gate only stops the detection tick (`:1116`); (c) `visibilitychange` handler only resizes canvases (`:268`).

## 11. XYRIS I side — existing gesture system discovered (critical compatibility fact)

XYRIS I **already ships** a native spatial/gesture subsystem — `core/spatial/` (10 modules):

| Module | Role (measured) |
|---|---|
| `camera_manager.py` (5,938 B) | cv2 capture thread, `start/stop/switch_camera/detect_cameras` |
| `hand_tracker.py` (6,642 B) | **MediaPipe** landmark detection, `import mediapipe as mp` guarded `HAS_MEDIAPIPE` (`:15-18`), legacy `mp.solutions.hands.Hands` (`:65`), idempotent `release()` (`:186-191`) |
| `gesture_recognizer.py` (10,310 B) | single-hand (`:71`) + two-hand (`:166`) recognition, finger counting (`:245`) |
| `gesture_interpreter.py` (11,159 B) | per-state interpretation: tracking/hovering/pinch/select/drag/paused |
| `interaction_state.py` (8,021 B) | **`HandLandmarks` (:62), `GestureEvent` (:76), `GestureSettings` (:92), `InteractionStateMachine` with validated transitions (:106+)** |
| `engine.py` (9,524 B) | `SpatialInteractionEngine`: `start/stop/emergency_stop`, callbacks `on_gesture_event/on_state_change/on_pointer_move`, processing loop, `get_debug_info` |
| `safety_controller.py`, `ui_controller.py`, `gesture_cursor.py` | safety gating + PyQt6 cursor overlay |

- **Enums:** `GestureType` ∈ {NONE, POINT, PINCH, OPEN_PALM, FIST, SPREAD, CONTRACT, ROTATE}; `GestureIntent` ∈ {POINTER_MOVE, SELECT, DRAG_START/MOVE/END, PAUSE, RESUME, CANCEL, ZOOM_IN/OUT, ROTATE_CW/CCW} (`interaction_state.py:26-59`).
- **Settings contract:** `GestureSettings(confidence_threshold=.7, pinch_threshold=.05, pinch_cooldown_ms=300, pointer_smoothing=.3, min_hold_duration_ms=100, debounce_ms=50, mirror_mode=True, camera_index=0, enabled=False)` (`:92-103`).
- **Config:** `memory/config_manager.py:426-458` — `DEFAULT_GESTURE_CFG` (`gesture_enabled: **False**` by default), `is_gesture_enabled()/set_gesture_setting()`; persisted flag read at `main.py:933`.
- **Wiring:** `main.py:950-963` constructs engine + assigns `on_gesture_event` → `main.py:987-993` `_on_gesture_event` → `ui.emit_gesture_event("pointer_move"/"intent", …)` → Qt signal hop `ui.py:5351/5407/8575`.
- **Boot behavior (current machine):** with `gesture_enabled=True` in the (gitignored) user config, boot log shows `[Spatial] Gesture control enabled` + `Camera 0 opened: 640x480` + `Spatial Interaction Engine started` — **camera opens at app boot when enabled** (relevant to spec Phase 6; config default is off).
- **Optional dependency:** `mediapipe==0.10.21` pinned in `requirements.txt` comments (legacy `mp.solutions` API requirement), feature disabled gracefully without it.
- **Tests:** `tests/test_spatial.py` → **31 collected**; `tests/test_gesture_toggle.py` → **2 collected**; suite baseline 1478/0.

## 12. Initial compatibility assessment (expanded in Phase 2 doc)

| Dimension | holo-gestures | XYRIS I | Consequence |
|---|---|---|---|
| Language/runtime | Browser JS + stdlib Python server | Python 3.11.9 + PyQt6 desktop app | **No code-file reuse possible**; integration = *concept/threshold/rule port* behind XYRIS's existing contracts |
| Hand tracking | MediaPipe Tasks Vision (JS/wasm, `HandLandmarker`, VIDEO, 2 hands) | MediaPipe 0.10.21 legacy `mp.solutions.hands` (optional) | Same vendor lineage, different API generations — landmark sets align (21 pts), detection semantics comparable |
| Hand model | `numHands:2`, ghost dedupe, nearest-palm slot identity | `HandLandmarks` + `second_hand` on `GestureEvent` | Two-hand gestures natively representable |
| Event contract | `postState(event,card)` → JSON file (no reader) | typed `GestureEvent{gesture,intent,confidence,pointer_position,…}` + Qt signal to UI | holo's *file-post* pattern is **not** needed; integrate at `GestureEvent`/intent layer |
| UI rendering | three.js WebGL deck | PyQt6 software rendering | holo's deck/3D layer **out of scope** (would require QtWebEngine — not a dependency, rejected) |
| Gesture vocabulary | 11 gestures incl. **peace, flick, quick-pinch-tap, clap, tidy** | 8 `GestureType`s (point/pinch/open/fist/spread/contract/rotate) | Overlap large; **new** = peace/flick/tap/clap candidates for Phase 3 design |
| Threshold maturity | field-tuned constants with dated rationale comments (`:366-368` etc.) | `GestureSettings` tunables + confidence gate `.7` | holo's proven constants (`.30/.42/2-frame`, `SPAN_MIN`, earn-frames) are prime candidates to validate against XYRIS recognizer |
| Camera lifecycle | starts at load, **no stop API** | `CameraManager.stop()`, engine `emergency_stop()`, config opt-in | **Do not port holo's lifecycle**; keep XYRIS's (spec Phase 6 favors XYRIS behavior, with indicator/stop UI to verify in Phase 6) |
| Testing | in-page probe battery on synthetic hands (26 assertions) | pytest (33 gesture tests today) | holo's *synthetic-landmark-through-real-engine* technique is portable to pytest (Phase 5) |
| Crash/status UX | explicit status strings per failure stage + `postDiag` | boot-log based | port the *status vocabulary* idea to honest UI labels |

**Bottom line:** XYRIS I is not starting from zero — it has the safer, typed, tested architecture. holo-gestures is an **evidence source** (field-tuned rules, robustness patterns, probe technique, richer gesture vocabulary, status/privacy copy), not a drop-in module. Integration targets the existing `core/spatial` contracts; no browser engine, no new heavy dependency.

## 13. Missing / to verify in later phases (no assumptions made)

1. Upstream repo reachability + exact upstream commit of this ZIP (Phase 1).
2. `.glb`/Smithsonian model license file absent — do not reuse assets without verification.
3. Probe battery total/count not re-run on this machine yet (26/26 is prior-session evidence — re-run or label as unverified per spec §9).
4. holo-specific gestures' false-positive rates on *this* machine's camera — untested (Phase 7+ requires live verification or honest "Not tested").
5. Whether XYRIS `GestureRecognizer` already computes confidence per gesture (recognizer internals partially read) — full read scheduled Phase 2.
6. Existing gesture settings UI location (settings panel wiring) — discover before Phase 6 UI work.
7. holo JARVIS-mode speech uses browser TTS — unrelated to XYRIS voice stack; excluded by default.

---
*Phase 0 rule compliance: source audited before design; all numbers above measured or cited with file:line; no production code modified; ZIP untouched; license texts reported as found, no clearance claimed.*
