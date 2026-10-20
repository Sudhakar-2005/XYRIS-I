# Phase 1 — Gesture Source Audit (holo-gestures)

**Scope:** read-only deep audit of `C:\Users\Toshiba\Downloads\holo-gestures-main\holo-gestures-main\` (source of truth: code with file:line citations; README claims checked against code).
**Method:** static code reading + one live verification run (probe battery, this machine, §8). No source files modified. ZIP untouched.
**Evidence labels (spec §9):** **Verified** = ran here / read directly in code · **Claimed** = README/comment says so, consistent with code but not independently proven · **Not tested** = not exercised in this environment.

---

## 1. Provenance & licensing

| Item | Finding | Status |
|---|---|---|
| Upstream | README `:17` → `github.com/zubair-trabzada/holo-gestures`; local copy is a ZIP **without `.git`** (no commit id obtainable locally) | Claimed |
| Original code license | `LICENSE` = **MIT © 2026 Zubair Trabzada / AI Workshop Studio LLC** | Verified |
| Vendor: MediaPipe Tasks Vision (JS/wasm/model) | `vendor/`, self-hosted + jsdelivr 1.0.1 fallback (`holo.html:1066-1068`); README license section: **Apache-2.0 (Google)** | Verified (files) / Claimed (license text not separately present in repo) |
| Vendor: three.js + GLTFLoader + BufferGeometryUtils | `vendor/three.module.js`, `GLTFLoader.js`, `BufferGeometryUtils.js`; README: **MIT** | Verified (files) / Claimed (license text not in repo) |
| **Modified third-party files** | README: `vendor/wasm/vision_wasm_*.js` carry a one-line **`HOLO SHIM`** patch fixing a MediaPipe wasm-glue crash — i.e., vendor files are **locally patched** (must be re-applied if re-downloaded; derivative-change notice obligation) | Claimed (README), file markers not individually opened |
| Props models | `props/` = 2 `.glb`, ~18.5 MB (Apollo 11 + Triceratops per README); README: Smithsonian **CC0** at 3d.si.edu | Claimed — **no license file ships with the models**; assets excluded from any reuse until verified |
| Downstream obligation if porting code | Carry MIT (holo) + Apache-2.0 (MediaPipe) + MIT (three.js) attribution; **no clearance claims** (spec §11) | Rule |

## 2. Architecture map (Verified — read in code)

```
server.py (stdlib, 127.0.0.1:4890)
  GET / → holo.html · /vendor|/props (traversal-guarded) · /api/notes · /api/tree · /api/props
  POST /api/state → state/holo-state.json (atomic tmp+replace, server.py:160-176)  [no reader yet]
  POST /api/diag  → state/holo-diag.json (server.py:146-159)
browser (holo.html, 1578 lines)
  load → startCamera() (holo.html:1168, unconditional)
    → HandLandmarker init: self-hosted vendor first, CDN fallback (1056-1069)
      floors .6/.6/.6, VIDEO, numHands 2 (1076-1080), GPU→CPU (1081-1083)
    → getUserMedia 1280×720 (1101); explicit status strings per outcome (1106-1110,1115)
  rAF tick (1116-1166): frame-change gate (1117-1118) → AMP 1.45 screen map (183)
    → detectForVideo (1120) → chirality-.5 sanity floor (1124-1131)
    → ghost-hand dedupe (1132-1160) → assignHands nearest-palm slots (392-405)
    → ingestHands (470-535)
      handMetrics (369-385) → span gate SPAN_MIN .025 (448,475)
      → One Euro smoothing (418-429) → per-gesture temporal gates (§4)
      → twoHand (589-643) → [ADV] clap/tidy (653-694)
  events: postState debounced 120 ms (709-715) · postDiag merged object (453-456)
  fallback: mouse pointerdown (362,1236) + keys h/v/d/f/j (1271-1274)
```

## 3. Gesture rule catalog (the portable core) — Verified

### 3.1 Static pose gates (`handMetrics`, `holo.html:369-385`)

Normalized by `span = dist(wrist, middle-MCP)`; `curl(i) = dist(tip_i, wrist)/span`.

| Pose | Rule | Line |
|---|---|---|
| pinch distance | `dist(thumb-tip, index-tip) / span` | `:376` |
| open | all four tips `> 1.45` | `:377` |
| openish | all `> 1.32` (relaxed real-hand parity) | `:378` |
| fist | all tips `< 1.05` | `:379` |
| point | index `> 1.45` && middle/ring/pinky `< 1.15` | `:380` |
| peace | index+middle `> 1.45` && ring+pinky `< 1.15` | `:381` |
| cursor | index fingertip `lm[8]` is the cursor | `:383` |

### 3.2 Dynamic gates & temporal patterns

| Gesture | Gate | Temporal logic | Line |
|---|---|---|---|
| **Pinch grab** | `PINCH_IN=.30` enter, `PINCH_OUT=.42` exit | needs `st.seen≥3` + **`PINCH_EARN=2` consecutive frames**; release above `.42` | `:368, 519-525` |
| **Quick pinch (tap)** | release `<450 ms` AND palm drift `<60 px` (from start palm) | field-tuned: real taps ≈400 ms/50 px; old 300 ms/34 px demanded lab-perfect taps | `:576-583` |
| **Flick** | release velocity `sp > 4` (peak over trail, dt-clamped ≥8 ms, ×16 ms normalize); fling event `> 6` | throw impulse `v × 1.35` | `:559-567, 574, 584` |
| **Peace reset** | peace pose, no pinch, reader closed | held **8 frames** → `restoreAll()`, **2500 ms** cooldown (replaced clap as the reliable reset — camera-hostility lesson) | `:526-531` |
| **Repulsor (ADV)** | `openish` held | **24 frames** + **1500 ms** per-hand cooldown; static hold replaced thrust (motion gestures "basically never fired on a real webcam") | `:497-512` |
| **Ink (ADV)** | point pose, `seen≥3` | **4-frame earn** (pose flicker must not ink); cap 700 points | `:489-496` |
| **Force-pull (ADV)** | open→fist within **320 ms** | single nearest card >220 px | `:485-486, 644-651` |
| **Clap (ADV)** | two hands, palm distance `< max(160, .18·min(W,H))` px, trail velocity `>14`, closing `>12 px/frame` | **1100 ms** cooldown; open-ness NOT in the gate (edge-on palms read closed) | `:653-667` |
| **Tidy sweep (ADV)** | both palms open, both moving down `>26 px/3 frames`, |Δx| smaller than |Δy|, y-alignment `< .3H` | **1400 ms** cooldown, reader must be closed | `:683-695` |
| **Two-hand pinch-zoom/rotate** | BOTH hands pinching; empty space; **separation ≥140 px** (ghost guard); scale step clamped `.94-1.06`, scene cap `.6-1.5` | baseline `d0/a0` re-anchored each frame | `:589-643, 627-642` |
| **Card stretch/squeeze** | both pinches, other hand within `240·scale` px of card | `d0` floored ≥40 px (**NaN guard** — ghost d≈0 → 0/0 → NaN transform bug, field 2026-08-31); note opens `>1.7×`, summary `s<.58` | `:595-623` |
| **Dock** | release when `x > DOCK_X()-80` | right-edge shelf | `:580` |
| **Reader scroll (open)** | reader open + open palm + trail ≥2 | content follows hand y, ×2.4 | `:513-517` |

### 3.3 Design laws extracted from dated comments (port as *rationale*, not just numbers)

1. **Static beats motion**: repulsor thrust → held pose; clap → peace ("same law", `:500, :526`).
2. **Earn-frames everywhere**: pinch 2, ink 4, peace 8, repulsor 24 — pose flicker must never trigger an action.
3. **Effort-to-usage**: rare powerful acts get high-effort two-hand gestures (tidy `:680-682`).
4. **Effects are opt-in**: ambient gestures misfire on real cameras (steal pinches) → behind `?adv`/F chip, **default OFF** (`:438-444`).
5. **Three-layer phantom kill**: tracker `.6` confidence + `SPAN_MIN` span gate + stale watchdog — because a persistent false detection survives a render gate (`:445-447`).
6. **Treat MediaPipe handedness score correctly**: it is chirality confidence, not detection quality; an `.8` gate threw away real hands (`:1124-1129`).

## 4. Robustness & signal-processing catalog (portable patterns) — Verified

| Pattern | Implementation | Line |
|---|---|---|
| **One Euro filter** (Casiez et al.) | per-hand state; dt clamped (sub-frame→1/60, max 0.2 s); derivative cutoff **2.5**, adaptive cutoff `1.3 + 0.009·speed` | `:415-429` |
| **Hand identity slots** | MediaPipe order is arbitrary & swaps; each detection claims nearest-palm slot (claim radius **260 px**, prefer slots with more `seen`) so state survives frame-to-frame | `:386-413` |
| **Ghost-hand dedupe** | two detections closer than **1.35 hand-spans** = same hand (keep higher score) — **unless** both long-established (that's a clap, never merge); second rule: much-smaller + `<.9` confidence = shirt/face phantom | `:1132-1160` |
| **Stale watchdog** | hand not fed for **280 ms** → `dropHand` (releases pinch, resets state) — kills the "parked cursor" bug | `:462-468` |
| **Span noise floor** | `<2.5%` of frame → ignored | `:448, 475` |
| **Hover earn** | `seen ≥ 3` before hover targets count | `:483` |
| **Trail/space caps** | trail 10, spans 8, ink 700 — bounded memory | `:481-482, 495` |
| **Frame-change gate** | `detectForVideo` only when `currentTime` advances | `:1117-1118` |
| **NaN/guard math** | `d0` floor, `dist` guards, `dt` clamps | `:602, 420` |
| **Crash reporting** | stage-tagged init failures (import/wasm/model/camera), merged diag object so failures don't clobber | `:1059, 1089-1092, 451-456` |
| **Self-hosted-first loading** | no CDN in critical path (ad-block/offline resilience), CDN only as fallback | `:1057-1069`, `server.py:124-137` |

## 5. Performance posture — Verified (design) / Not tested (real-CPU numbers)

- rAF-driven; detection skipped on unchanged video timestamps (`:1117-1118`).
- GPU delegate attempted, CPU fallback automatic (`:1081-1083`).
- Landmarks processed in-page; only tiny JSON posts leave the page (§7). Frame pixels never serialized anywhere in code (**Verified by absence of any canvas/readPixels/upload path**; browser `getUserMedia` → `<video>` → MediaPipe only).
- `LOOP_ERRS` counter gates the probe `noframeerr` assertion (`:1047, 1573`).
- **Not tested:** fps/frame-time on this machine with a real camera (probe run used fake device; no perf counters captured).

## 6. Failure & status vocabulary (port the *honesty pattern*) — Verified

| Condition | Exact status string | Line |
|---|---|---|
| init start | `loading hand tracking…` | `:1055` |
| bundle loaded | `tracking core loaded · initializing…` | `:1071` |
| live | `hands live · on-device` | `:1115` |
| permission denied | `camera BLOCKED · address-bar camera icon → Allow → RETRY` | `:1107` |
| no device | `no camera found on this machine` | `:1108` |
| device busy | `camera is busy in another app · close it, then RETRY` | `:1109` |
| other error | `camera unavailable (…) · RETRY` | `:1110` |
| init failure | `tracking failed @<stage>: <msg> · RETRY` | `:1088` |

Rule honored: status states *what actually happened* (platform-tailored remediation), never a fake success.

## 7. Privacy & security audit — Verified

| Control | Evidence | Verdict |
|---|---|---|
| Loopback-only server | `ThreadingHTTPServer(("127.0.0.1", PORT))` `server.py:180` | Good |
| Path traversal guard | `normpath` + `".." not in safe` + prefix allow-list `server.py:127-128` | Good |
| Frame egress | none in code (no upload/WS/canvas export of video) | Good (**Verified** by code absence) |
| State/diag egress | local files only, gitignored `state/` | Good |
| Third-party network | only CDN **JS/wasm fallback** `:1066-1068` (not frames); off if local bundle loads | Acceptable, note in Phase 6 |
| **Camera starts at page load** | `startCamera()` invoked unconditionally `:1168` — **no user gesture gate** (observed live: probe page reached `hands live · on-device` by itself) | Gap vs spec Phase 6 (opt-in) |
| **No camera stop/release** | no `getTracks()`/`track.stop()` anywhere; `camOn=false` only stops the rAF tick `:1116`; `visibilitychange` only resizes `:268` | Gap vs spec Phase 6 (stop control) |
| Auth gesture rules | gestures only drive UI (grab/zoom/reset) — **no file-delete or account action** reachable from a gesture; destructive-ish acts (restore/tidy) are reversible UI state | Aligned with spec §2; XYRIS side must keep this rule |

## 8. Live verification performed (this machine) — Verified

- Command chain: started `server.py` (loopback :4890) → Playwright **Chromium 153.0.8010.12 headless** (fake media device flags) → `GET /?probe=1` → read `document.title`.
- Result: **`PROBE 26/26` — all PASS**:
  `gates, openOrb, grab, drag, flick, dock, zoom, pull, clap, open, close, spin, target, dismiss, stretch, ink, push, scroll, squeeze, jarvis, tidy, peace, peaceObjs, explode, ghostnan, noframeerr`.
- Status element reached `hands live · on-device` (confirms camera-at-load behavior live).
- Server process stopped afterward; port 4890 closed. **No source file touched.**
- Caveats (honesty per spec §9): probe asserts engine behavior on **synthetic landmarks**; it does **not** prove accuracy on a real hand/real camera. Several assertions degrade to `PASS` via graceful-skip when an optional dependency (prop file) is absent (`holo.html:1403, 1422, 1440, 1464, 1480, 1553, 1571`). README's "26/26" claim is therefore **Verified as a run result**, but its coverage limits are noted.

## 9. README claims vs evidence

| README claim | Evidence | Status |
|---|---|---|
| One command install, Python 3 stdlib only | `server.py` imports `json, os, time, http.server` only (`:17-18`) | Verified |
| "Camera frames never leave the page" | no egress path in code (§7) | Verified (static) |
| Self-hosted tracking, CDN fallback | `:1056-1069`, `server.py:124-137` | Verified |
| Thresholds are named constants (`PINCH_IN/OUT/EARN`, `AMP`) | `:368`, `:183` | Verified |
| Probe battery 26/26 | run here: 26/26 PASS (§8) | **Verified live** |
| Mouse fallback everywhere | pointerdown handlers `:362, 1236` + legend text | Verified |
| Models CC0 (Smithsonian) | README only; no license file in `props/` | Claimed — assets excluded pending verification |
| `HOLO_PORT` override | `server.py:21` | Verified |
| Chrome recommended | MediaPipe JS/wasm + speech synthesis usage; **Not tested** on Firefox/Edge | Claimed |

## 10. Reuse verdict (input to Phases 2–3)

**PORT (concepts/constants/patterns — must be re-derived in Python + proven by XYRIS tests):**
pose gates (§3.1) · pinch in/out/earn, tap 450 ms/60 px, peace 8-frame/2500 ms, cooldowns (§3.2) · design laws (§3.3) · One Euro params, slot identity, ghost dedupe, span floor, stale watchdog, trail caps (§4) · honest status strings (§6) · synthetic-landmark-probe technique (§8) · effects-default-OFF rule (§3.3.4).

**DO NOT PORT:** three.js/WebGL deck, browser speech (JARVIS mode), `server.py` notes/props/tree APIs (XYRIS already has FastAPI dashboard), CDN-fallback loading (XYRIS vendors Python deps via pip), **holo's camera lifecycle** (load-time auto-start, no stop) — XYRIS's `CameraManager.stop()`/opt-in config is superior and spec-conformant.

**CANDIDATE NEW GESTURES for XYRIS** (not currently in its `GestureType` enum): **peace-reset-equivalent (reset/refresh intent)**, **flick (fling/dismiss intent)**, **quick-pinch tap (select vs. drag disambiguation)** — clap/tidy/ink/repulsor only as explicitly-gated extras (holo itself demoted them to opt-in after field misfires).

## 11. Open questions carried to Phase 2

1. Exact smoothing/confidence behavior of XYRIS `GestureRecognizer` internals (partially read) — complete read pending.
2. Whether XYRIS already disambiguates tap vs. drag (its `GestureSettings.pinch_cooldown_ms=300` vs holo's 450 ms/60 px rule).
3. How holo's thresholds map under XYRIS's `mirror_mode` + normalized coordinate space (holo uses `AMP=1.45` screen remap).
4. Where XYRIS's gesture settings UI lives (needed for Phase 6 status/indicator/stop control).
5. Real-camera false-positive data — requires live session (Phase 7+); until then any imported threshold is **Not tested** on this hardware.

---
*Compliance: audit-first (no production code changed); every §3-§7 number read from code with file:line; live results actually executed and reported with their caveats; licenses reported as found, no clearance claimed; ZIP unmodified.*
