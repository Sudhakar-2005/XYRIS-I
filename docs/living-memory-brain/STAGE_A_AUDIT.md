# Living Memory Brain — Stage A Audit & Approved Design

**Date:** 2026-10-20 · **Mode:** read-only audit — no project source was modified
during Stage A · **Status:** audit complete, design approved ("proceed"), Phase 1
implementation authorized.

Scope: integrate the Holo-Gestures interface (MIT, external, never modified in
place) as an interactive visualization of XYRIS I's *actual* memory data —
adapter → events → UI → optional gestures → validation. No invented data, no
fabricated relationships, read-only view, memory backend stays the source of
truth.

---

## A. XYRIS I — memory architecture (findings)

**Authoritative backend — `memory/memory_manager.py` (833 lines).**
Imported by `main.py`, `ui.py`, `actions/memory_controller.py`,
`background_monitor.py`, `actions/proactive.py`.

- Store: `memory/long_term.json`; six categories (`identity`, `preferences`,
  `projects`, `relationships`, `wishes`, `notes`) + a `sessions` list
  (`_empty_memory`, :48). Entries are `{value ≤380 chars, updated: YYYY-MM-DD}`
  (`MAX_VALUE_LENGTH` :19, `_recursive_update` :165 — **date granularity only,
  no times**).
- Writes: thread-locked, atomic (`_atomic_write` :119), 200 KB runaway guard
  that trims oldest-first with a notifier hook (`set_trim_notifier` :91 —
  **the existing pattern for plugging an observer into memory writes**).
- Prompt budgets separate from storage (`MEMORY_MAX_CHARS` 200 000 /
  `PROMPT_CORE_CHARS` 900 :41).
- Public API: `load_memory/save_memory/update_memory`, `remember` :427 /
  `forget` :440 / `forget_matching` :451, `search_memory` :363 (lexical,
  <1 ms, no embeddings), `all_entries_for_ui` :396 (flat rows sorted newest
  first), `get_memory_stats` :622, `get_memory_age` :795,
  `save_session_log` :573 → `memory/sessions/<ts>.json`,
  `get_saved_sessions` :598, `get_session_count` :616, `export/import` :676/:692
  (sensitive-key filtered), `clear_category/clear_all_memory` :758/:778
  (confirm-key), `is_sensitive_key_value` :417 (credential write-block).

**Secondary systems (non-authoritative):**

- `core/memory_v2.py` (460 lines): Memory 2.0 taxonomy (WORKING/EPISODIC/
  SEMANTIC/PROCEDURAL, `MemoryRetriever`, `WorkingMemory`) — **only imported by
  `tools/eval_m1_recall.py`**; not wired into the runtime; `memory/v2/` does not
  exist on disk. Must not be surfaced as if it held data.
- `core/session_memory.py` (41 lines): in-process last-search handoff slot.

**UI — `ui.py` (8,626 lines):**

- V3 Memory page `_build_mem_page` :6398 is **stats-only by design** — docstring:
  "counts only; the entry inspector is the existing MemoryOverlay … must not be
  duplicated". Filled by `_refresh_mem_stats` :6464 from `get_memory_stats()`
  **on demand** ("never a hidden timer").
- `MemoryOverlay._rebuild` :3279 lists `all_entries_for_ui()` with per-row ✕
  → `_forget` :3376 (calls `forget()`); `SessionHistoryOverlay._rebuild` :3441
  shows `get_saved_sessions(limit=30)`.
- Overlay pattern: `_HudOverlay` subclasses centred on the central widget via
  `_centre_overlay`; openers `_open_memory_panel` :8005, `_open_history_panel`
  :8010, `_open_activity_panel` :8015.

**Event flow — `core/event_bus.py` (248 lines):**

- Asyncio priority bus; `EventType.MEMORY_STORED / MEMORY_RETRIEVED /
  MEMORY_CONTRADICTION` defined (:46) — **but nothing in the codebase publishes
  them** (grep: definition + tests only).
- Live in `main.py`: `EventBus()` :792, started in `run()` :3166 with a single
  bridge subscriber `_bridge_event` :2917 → activity feed (`feed.append(event)`)
  + dashboard WebSocket push. `JarvisLive.emit_event` is the emission helper.

**Web surface — `dashboard/server.py`:**

- FastAPI/uvicorn HTTPS, binds **`0.0.0.0`** :8000 (+ :8001), TLS certs, QR
  login, WS broadcast, emergency stop; started fail-soft in `main.py` :3148
  ("[Dashboard] Disabled" on missing deps). Deps declared in `requirements.txt`
  (:41-45).

**Identity/deps:**

- `core/avatar.py` = existing **HoloAvatar**: QPainter software rendering, no
  GPU/OpenGL — the holographic identity already exists natively; requirements
  deliberately exclude heavy extras (comment :76 "Left out on purpose — several
  hundred megabytes"); **no PyQt6-WebEngine / pywebview**; mediapipe is an
  optional pin (`==0.10.21`, installed in the dev venv).
- Browser-open precedent: `webbrowser.open` / `os.startfile` in
  `actions/browser_control.py` :428/:439, `actions/weather_report.py`,
  `ui.py` :2154.

## B. Holo-Gestures — architecture (findings)

External source (never modified): `C:\Users\Toshiba\Downloads\
holo-gestures-main\holo-gestures-main\` — MIT; zip SHA-256 `927B2BBF…776`.

- **Server** `server.py` (180 lines): stdlib `BaseHTTPRequestHandler`;
  `ROOT`/`PORT = HOLO_PORT || 4890` :20-21; quiet handler, per-type MIME map
  :101. Endpoints: `GET /`+`/holo.html` (boot cache :110), `/api/props` (`.glb`
  [:6]), `/vendor/*`+`/props/*` static **with traversal guard** :124-129,
  `/api/notes`, `/api/tree`; `POST /api/diag` → `state/holo-diag.json`,
  `POST /api/state` → atomic `state/holo-state.json` :160-176 (debounced 120 ms
  client-side, `postState` holo.html :710).
- **Data seam** `load_tree` :62: one level deep — folders → ORBS
  (`name.upper()[:22]`), loose files → a `NOTES` folder; note shape
  `{name, title[:48], body[:420], full[:4000]}` :40; caps `limit=18` notes,
  `limit_files=14` per folder.
- **Page** `holo.html` (1,578 lines): stars canvas, 2-D physics deck (`physics`
  :274), `#dock` shelf, `#reader`, `#ring` status ring, HUD canvas, chips
  (HANDS/CAM/RESET/DEBUG/**JARVIS**/RETRY), `#legend`, `#caption`, `#perm`.
  Data load: `resetCards()` :258 → `fetch('/api/tree')` → `spawnOrbs` :222.
- **Hand pipeline (single ingestion point):** `ingestHands(rawHands)` :470 —
  rawHands = **array of 21-point landmark arrays**; camera path
  `startCamera` :1052 (auto-invoked :1168) uses MediaPipe HandLandmarker,
  **self-hosted wasm first with CDN fallback** :1061-1068, GPU→CPU :1081,
  confidence floors .6 :1076, ghost-hand dedupe :1132, and already surfaces
  `NotReadableError → "camera is busy in another app"` :1109. No-camera paths:
  `?sim=1` scripted demo (`runSim` :1218, `synthHand` :1171), `?probe=1`
  (drives `ingestHands` directly — reused by our Stage-4 gesture tests).
- **Gestures:** pinch grab/tap-open (release <450 ms), flick throw
  (`peakVelocity` :559, sp>4 px/16 ms), 2-hand stretch (big → reader, crush →
  TL;DR), fist force-pull :644, clap restore :653, tidy sweep :683, peace.
- **Merge intent is explicit in source:** holo.html :717-722 — JARVIS mode is
  local `speechSynthesis`, "the preview of what the real brain-studio merge
  will read" via the state file.
- Privacy: all tracking on-device; camera frames never leave the page; no keys.

## C. Integration options (dependency compatibility)

| # | Option | Verdict |
|---|--------|---------|
| 1 | PyQt6-WebEngine embed | ✗ ~100 MB+ Chromium dep — violates requirements philosophy (:76), VM/remote-desktop risk (avatar doc) |
| 2 | Port visuals to Qt | ✗ three.js scene + physics + wasm in QPainter — prohibitive, wrong ROI |
| 3 | Extend FastAPI dashboard | △ declared deps + WS exist, but `0.0.0.0`+QR-auth concerns, fail-soft disabled state, mixed concerns; possible later |
| **4** | **Loopback stdlib server + system browser** | **✓ zero new deps** — holo's own pattern, browser-open precedent, `127.0.0.1` = secure context (getUserMedia allowed), no firewall prompts |

**Option 4 approved.**

## D. Data inventory (real, on disk — nothing invented)

| Data | Reality (2026-10-20) |
|------|----------------------|
| `long_term.json` | 523 B; 1 entry (notes); all other categories **0**; 1 session summary |
| `memory/sessions/` | **139 transcripts** (2026-09-18 → 2026-10-20): `date, time, summary ≤280, language, turn_count, turns[]` |
| `memory/v2/` | absent — Memory 2.0 has no on-disk data |
| Activity feed | live, bus-fed ring |

Consequence: the sessions timeline is the visually rich part; five categories
are empty → the view must render **honest empty states** (counts, no fabricated
objects), cap the deck (e.g. newest 30 transcripts) and state overflow honestly
("+N more on disk").

## E. Gesture capabilities & the camera conflict

- XYRIS: committed spatial engine (Stages 1–5, commit `5f1404d`) — recognizer/
  interpreter/interaction-state/safety, `GestureType.PEACE`, intents
  SELECT/DISMISS/RESET, flags `tap_action`/`flick_detect`/`peace_reset`
  (default off), camera status + `gesture_enabled` opt-in, mediapipe 0.10.21
  **in Python (venv)**.
- Holo: in-page mediapipe wasm (its own `getUserMedia`), or synthetic feeds
  (`?sim=1`/`?probe=1`).
- **Conflict:** two webcam owners on Windows; holo auto-starts its camera and
  already reports "camera is busy in another app".
- **Resolution:** the page should never own the camera long-term —
  **XYRIS is the single tracking owner** and feeds landmark arrays (same shape
  `ingestHands` expects) to the page over localhost. v1 ships mouse/keyboard
  only (holo's own click/drag floor), with page-camera as an explicit opt-in
  only while the XYRIS gesture engine is off.

## F. Risks & assumptions (design inputs)

1. **Camera contention** — see E; v1 = no camera at all in the page.
2. **Ports** — 4890 (holo), 8000/8001 (dashboard) taken → view binds
   **127.0.0.1:4891** (env-overridable), loopback only, never `0.0.0.0`.
3. **Truncation caps** — holo's 18/14/48/420/22 limits must be raised/removed
   in the adapter; fact values (≤380) fit `body`, sessions' summaries (≤280) fit.
4. **Zero-data categories** — honest empties only; never synthesize entries.
5. **`MEMORY_*` events unpublished** — Phase 3 adds a publisher (notifier-hook
   pattern, like `set_trim_notifier`); polling stays the page-sync fallback
   because the bus only exists inside `run()`'s loop.
6. **memory_v2 non-authoritative** — do not render episodic/semantic orbs.
7. **CDN fallback** — the derived page must be local-only (no jsdelivr/google
   URLs): offline + privacy rules.
8. **MIT** — vendor nothing; derived file carries attribution; original holo
   extraction untouched.
9. **Traversal** — adapter re-implements the `normpath` + `..` guard for any
   static serving.
10. **Read-only** — `POST /api/state` maps to the activity feed only; deletes
    remain in the existing confirm-gated overlays.
11. **No secrets/reasoning** in payloads — values/categories/stats only;
    sensitive keys are already blocked at write time.
12. **Date-only stamps** — never invent times for facts (transcripts carry
    real times).
13. **Dashboard optional** — the view must not depend on it.
14. **ui.py rule** — add an entry button; do not duplicate MemoryOverlay.

## G. Approved design (Option 4)

```
XYRIS I (PyQt6)                          memory_holo (stdlib, 127.0.0.1:4891)
┌──────────────────────┐   read-only    ┌────────────────────────────────────┐
│ memory_manager       │◄──────────────►│ GET  /api/tree     categories→orbs │
│  long_term.json      │  (adapter)     │ GET  /api/stats    stats→#ring     │
│  sessions/*.json     │                │ POST /api/state    → activity feed │
│ EventBus ─► activity │                │ GET  /             memory_view    │
│          └► dashboard│                │      .html (holo-derived, no cam)  │
└──────────────────────┘                └────────────────────────────────────┘
      │ opens default browser ───────────────► mouse/keyboard (v1)
      │ v2: XYRIS landmarks ── localhost ───► ingestHands() (shape-compatible)
```

- **Module** `memory_holo/`: `adapter.py` (pure mapping — unit-testable without
  a server), `server.py` (ThreadingHTTPServer, fail-soft start like the
  dashboard), `memory_view.html` (derived: deck/dock/reader/ring/HUD + gesture
  state machine + `postState`; camera, three.js GLB scene, CDN fallback and
  auto-`startCamera()` removed; MIT attribution header).
- **Adapter mapping:** orbs = 6 categories + `SESSIONS` (real `updated` dates
  on cards; session cards = date/summary/turn_count; deck capped with honest
  overflow); title = prettified key, body = value, no invented fields.
- **Entry points (Phase 2):** V3 Memory page button next to "OPEN MEMORY
  INSPECTOR" (existing inspector untouched) + a voice/CLI action → opens the
  loopback URL in the default browser. App restart required to see it.
- **Sync (Phase 3):** baseline = cheap poll (`/api/stats` ETag); real =
  publish `MEMORY_STORED/MEMORY_RETRIEVED` from write/read paths via
  `emit_event`, bridged to the activity feed; `POST /api/state` → feed event
  (`source="memory_view"`).
- **Gestures:** v1 = mouse/keyboard (and holo's synthetic paths stay
  test-only); v2 = XYRIS engine → landmark feed → `ingestHands`.
- **Verification:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q`
  must stay green (baseline **1587 passed**); adapter/server unit tests for
  shape/limits/traversal/empty-states/state-mapping; page verified manually in
  a browser (and with playwright if its browsers are installed).

*Stage A wrote this file only; no source was touched during the audit.*
