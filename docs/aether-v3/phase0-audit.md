# AETHER-I V3 — Phase 0: Full Codebase Audit

**Status:** Complete (read-only, no code modified)
**Date:** 2026-09-29
**Sources:**
- AETHER-I V2 (target): `Mark-LIV-main/Mark-LIV-main/` — 112 py files, 1674 KB
- Mark-LV (reference): `Mark-L-main/Mark-L-main/` — 34 py files, 628 KB

---

## 1. Executive Summary

**Headline finding: AETHER-I V2 is already a strict superset of Mark-LV, and its
central problem is not missing features — it is that a large fraction of its own
subsystems are built, tested, and then never wired into the running application.**

1. **Shared lineage confirmed.** Both codebases use PyQt6, class `JarvisLive` +
   class `JarvisUI` with a `_RootShim.mainloop()` compatibility layer, the same
   `set_state`/`show_content` signal-based UI bridge, the same accent theming API
   (`class C`, `apply_ui_accent`, `current_palette`, `retheme_all_widgets`,
   `HueWheel`), and 20 identically-named action modules of near-identical size.
   AETHER-I is an evolved fork of Mark-L, not a separate product.
2. **AETHER-I is stronger than the reference in nearly every V3-relevant area:**
   confirmation gate, undo, risk metadata, event bus, verifier, planner, echo
   guard, push-to-talk, wake word, persona system, visual generation, plugins,
   and a 900-test regression suite (Mark-LV has **zero tests**).
3. **Mark-LV has NO model manager, NO confirmation system, NO verify-after-act,
   NO event bus, NO tool risk metadata.** Its genuinely useful reference value is
   limited to: content-panel layout, accent theming (already inherited), startup
   sequencing (already inherited), and a few product features (clipboard panel,
   QR dashboard pairing, session-memory briefing) that AETHER already mirrors.
4. **The dormant-subsystem problem (the real Phase-1 target):** the following
   modules exist, are importable, and in most cases unit-tested — but have **zero
   runtime importers**:
   - `core/event_bus.py` (async event bus, `EventType` enum) — tests only
   - `core/planner.py` + `core/replanner.py` + `core/task_graph.py` — tests only
   - `core/reasoning.py`, `core/verifier.py`, `core/self_checker.py` — tests only
   - `core/tool_registry.py` (`Risk`, `ToolSchema`, `SchedulingMode`) — tests only
   - `core/memory_v2.py` (Working/Episodic/Procedural + `MemoryRetriever`) — tests only
   - `core/llm_client.py` — **not even tested** (21 KB, fully dormant)
   - `core/stt.py` + `core/tts.py` (20 KB combined) — **zero references anywhere**
   - `core/safety_manager.py` — only imported by `actions/safety_action.py`
   - `core/notification_manager.py` — only imported by `actions/notification_action.py`
5. **Real gaps vs the V3 spec** (independent of Mark-LV):
   - State vocabulary: only `LISTENING / THINKING / SPEAKING / SLEEPING`
     (+ `PROCESSING` in tools) are used (`main.py` `set_state` calls); the
     13-state V3 vocabulary does not exist yet.
   - No observe-verify loop in tools: `open_app` "verifies" launches with fixed
     `time.sleep(1.5/0.7/2.5)` calls (`actions/open_app.py:90-111`).
   - Tool declarations are plain Gemini `function_declarations` — no
     `risk/timeout/confirmation/undo` metadata (7 stray `"risk"` hits only).
   - Observability: logging is mostly `print()`; the event bus is unwired.
   - No performance measurements exist (startup, latency, memory).

**Therefore the V3 strategy is: WIRE AND HARDEN what exists, ADD only what the
spec requires that nothing provides, and adopt from Mark-LV only what is
genuinely better (very little).**

---

## 2. Codebase Facts

| Metric | AETHER-I V2 | Mark-LV |
|---|---|---|
| Python files (source) | 112 / 1674 KB | 34 / 628 KB |
| Entry points | `main.py` 118 KB, `ui.py` 265 KB | `main.py` 74 KB, `ui.py` 133 KB |
| Core modules | 31 + `spatial/` (10) + `visual/` (5) = 46 | 4 (llm_client, stt, tts, installer) |
| Actions | 25 (superset) | 20 |
| Tests | 26 files, **900 passing** | **none** |
| Plugins | calendar, gmail, quiz, `_template` | none |
| Dashboard | `server.py` 38 KB, token auth | `server.py` 33 KB |
| UI toolkit | PyQt6 | PyQt6 (`_RootShim` over QApplication) |
| Lineage markers | `JarvisLive` (main.py:531), `JarvisUI` (ui.py) | `JarvisLive` (main.py:553), `JarvisUI` (ui.py:3251) |

AETHER-I actions = all 20 Mark-LV actions (same names, near-same KB) **plus**
`file_intelligence`, `generate_image`, `memory_controller`, `notification_action`,
`safety_action`.

---

## 3. Comparison Matrix

Columns: AETHER CURRENT | MARK-LV | PROBLEM | BEST PATTERN | PROPOSED V3 | FILES | RISK | TEST PLAN

### 3.1 Application lifecycle
- **AETHER:** UI created first (`main.py:2433`), tray installed, AI session runs
  via `threading.Thread(runner)` + `asyncio.run(JarvisLive.run())` (`main.py:2441-2450`).
  Qt mainloop never blocks on the AI session. Blocking work moved with
  `asyncio.to_thread` (17 call sites).
- **Mark-LV:** identical pattern (`main.py:1521-1533`).
- **Problem:** startup time unmeasured; `ui.wait_for_api_key()` busy-polls
  (ui.py:6235-6237); wake-word install may run on the UI path (ui.py:5660).
- **V3:** keep pattern; instrument first (Phase 37), then lazy-load optional
  systems (Phase 14-16). **Files:** `main.py`, `ui.py`. **Risk:** low.
  **Test:** startup-time benchmark before/after.

### 3.2 UI / HUD / states
- **AETHER:** `set_state` is thread-safe via `_state_sig.emit` (ui.py:6230);
  HUD states rendered at ui.py:635-885; `HUD_STYLES=("face","core")`
  (config_manager.py:133); 232 `setStyleSheet` sites; many overlays
  (`ImageResultOverlay:1370`, `ConfirmBanner:2346`, `MemoryOverlay:2555`,
  `SessionHistoryOverlay:2751`, …); `ContentPanel` (ui.py:4801).
- **Mark-LV:** `HudCanvas` abstract arcs + blink tick (ui.py:340-577); same state
  set; content panel (`_build_content_panel:2763`, `show_content:3309`).
- **Problem:** V3's 13-state vocabulary (IDLE…RECOVERING) absent; state
  transitions scattered across main.py; `ui.py` 265 KB monolith.
- **V3:** central state machine emitting the full vocabulary through the existing
  `_state_sig` path; each state gets a real visual (no fake animation). Incremental
  UI extraction only — never a rewrite. **Files:** `main.py`, `ui.py`,
  new `core/voice_state.py` (small). **Risk:** HIGH (100+ state refs in ui.py).
  **Test:** state-transition unit tests + manual HUD smoke per milestone.
  **✅ M9:** the content surface is now one route — `_surface_content` in
  `main.py` is the only path from a tool result to `ContentPanel`
  (web_search keeps its mode/query label; declared content actions surface
  reference-shaped results past a length floor; failure-shaped results never
  appear as content; plugins included per §3.10; the call runs before the M8
  verification note is appended, so the panel shows the tool's answer, not
  the model's verdict; a panel crash never breaks dispatch). The mute path
  is likewise one named call — `_pc_mic_blocked` gates every PC-mic frame
  (`ui.muted` or `_phone_active` → reason string), replacing the old inline
  condition. 19 tests in `tests/test_content_surface.py`, incl. source
  guards that the old inline mute condition is gone and that the phone
  relay keeps its own muted check.

### 3.3 Avatar & lip sync
- **AETHER:** `core/avatar.py` (35 KB `HoloAvatar`) + `avatar_mesh.py` +
  `core/viseme.py` (`VisemeStream`, wired main.py:83) + `_pcm_visemes` audio
  analysis (main.py:159). UI imports `HoloAvatar` (ui.py:37).
- **Mark-LV:** `HudCanvas` eye-blink only — no mouth, no visemes.
- **Verdict:** AETHER already exceeds the reference. **Do not downgrade.**
- **V3:** bind avatar expression to state (listening focus, thinking gaze,
  error restraint) and drive visemes from Live-API audio + transcript; verify
  Tamil/Telugu/Unicode coverage (Phase 3-4). **Files:** `core/avatar.py`,
  `core/viseme.py`, `main.py`. **Risk:** medium (visual only).
  **Test:** viseme unit tests exist; add per-state expression assertions.

### 3.4 Theming
- **AETHER:** full accent engine inherited — `class C` (ui.py:73),
  `apply_ui_accent` (108), `current_palette` (146), `retheme_all_widgets` (151),
  `HueWheel` (1677), live change at runtime (5789-5812). Semantic colors kept
  fixed by design (Mark-LV ui.py:86 comment).
- **Mark-LV:** same engine (origin).
- **Verdict:** **at parity — no work needed beyond verification** (Phase 6 = audit
  semantic-color misuse + avatar/HUD tint coverage). **Risk:** low.

### 3.5 AI Core / Gemini / model selection
- **AETHER:** `core/gemini.py` already has tiered ladders —
  `FAST/SMART/SEARCH` tuples with fallbacks (gemini.py:117-127),
  `_LIVE_FALLBACK` (127), reconnect machinery (`_ReconnectSignal:493`,
  `_keep_context_of:518`, `_reconnect_event:2210`), oneshot live helper (353).
  `LIVE_MODEL` constant main.py:100.
- **Mark-LV:** one `LIVE_MODEL` (main.py:72) + one `generate_content(model=...)`
  (1238-1239). **No fallback, no ladder.**
- **Problem:** model names still referenced in ≥2 files; no measured
  latency/failure stats; no cooldown; no single `ModelManager` interface.
- **V3:** Phase 11-13 — formalize `ModelManager` + `LiveModelManager` **around
  the existing gemini.py tiers** (measure → order the ladder). Single source of
  truth for model strings. **Files:** `core/gemini.py` (new wrapper module),
  `main.py:100`. **Risk:** medium (session path is delicate). **Test:** fake-provider
  fallback tests (pattern already proven in visual engine tests).

### 3.6 STT / TTS
- **AETHER:** `core/stt.py` (4 KB) + `core/tts.py` (16 KB) — **dead code, zero
  references anywhere**. Voice I/O runs through Gemini Live native audio.
- **Mark-LV:** same files, also unreferenced from main.
- **Problem:** dead code contradicts "no duplicate architecture".
- **V3 decision (Phase 22):** ✅ **DECIDED at M5 — keep both files and wire
  them as degraded/offline fallback when the Live API is down** (option (a),
  the report's recommendation — it directly serves error-recovery).
  Implementation deferred: scheduled for a later milestone before RC; both
  files stay untouched (and unreferenced) until that wiring lands.
  **Files:** `core/stt.py`, `core/tts.py`, `main.py`. **Risk:** low.
  **✅ M14 — wired:** `core/degraded.py` (model-free `EndpointDetector` +
  lazy `OfflineVoice`) and `main.py`: three straight connection failures
  engage a fail-soft voice bridge — mic → local Whisper → REST ladder reply
  (`gemini.call`) → offline TTS — ended the moment a session reconnects;
  dashboard text arriving with no session now answers over the REST ladder
  instead of being dropped. The engines are optional installs: absent ones
  degrade that step to text and log it exactly once. 19 tests in
  `tests/test_degraded.py`.

### 3.7 Wake word / PTT / echo guard
- **AETHER:** `core/wake_word.py` (wired main.py:84, ui.py:5450, installer
  ui.py:5660), `core/hotkey.py` `PushToTalk` (main.py:1003), `core/echo.py`
  `EchoGuard` (main.py:82). Richer than reference.
- **Mark-LV:** fixed 4.0s mute cooldown after speaking (main.py:762-765) only.
- **V3:** keep AETHER; measure false-activation + CPU-while-asleep (Phase 24-25).
  **Risk:** low. **Test:** echo-guard tests exist? (add if missing).

### 3.8 Session management / continuity
- **AETHER:** resume/reconnect refs ×87 across `gemini.py`, `main.py`, dashboard;
  session summary (`pop_last_session`, main.py:1922/2043-44), `_session_log[-8:]`
  (2134), dedup helper `_is_repeat_chunk` (308).
- **Mark-LV:** session-memory summary consumed by morning briefing
  (main.py:34/860/1216/1244/1509) — AETHER mirrors this (proactive + brief config).
- **V3:** Phase 21 — formalize resume: on reconnect, restore context then fresh
  session; transcript dedup. **Risk:** medium. **Test:** reconnect unit tests.
  **✅ M6:** resume formalized through three testable seams —
  `_note_resumption_update` (only resumable handles kept, newest wins),
  `_is_resumption_rejection` (dead handle → fresh session, never masks real
  errors), `_prepare_voluntary_reconnect` (keep_context policy) — plus
  signal/group unwrapping, dedup and config-replay coverage: 34 tests in
  `tests/test_reconnect_resume.py`.

### 3.9 Tools / action registry
- **AETHER:** `core/action_loader.py` discovery (wired main.py:81), dispatch in
  `_execute_tool` (working — do not rewrite). `core/tool_registry.py` with
  `Risk/ToolSchema/SchedulingMode` **dormant (tests only)**. TOOL dicts carry
  `name/description/parameters` only (e.g. computer_control.py:518-525).
- **Mark-LV:** same declarations, **no risk metadata either** (0 hits).
- **V3:** Phase 17 — enrich `TOOL` dicts with
  `risk/timeout/requires_confirmation/supports_undo/verification` (JSON-schema
  compatible with Gemini declarations), enforce in dispatcher. Decide later
  whether `tool_registry` supersedes `action_loader` (recommend: metadata-first,
  no loader swap in V3). **Files:** all `actions/*.py`, `main.py` dispatcher.
  **Risk:** medium (25 files). **Test:** schema-validation test iterating every tool.

### 3.10 Plugins
- **AETHER:** `core/plugin_loader.discover_plugins` (main.py:77), 4 plugins,
  enable/disable UI (ui.py:2318), config save (3246). Mark-LV: none.
- **V3:** Phase 18 — lightweight discovery (metadata without importing plugin
  code), `importlib` lazy load (10 lazy-import sites already exist). **Risk:** low.
  **✅ M9:** plugin results reach the shared content surface on the same
  rule as actions — ≥120 chars surfaces with the plugin name as the label,
  anything shorter stays in the voice; the routing goes through the single
  `_surface_content` choke (shared with §3.2), so adding a content plugin
  needs no new special case.

### 3.11 Memory
- **AETHER:** `memory/memory_manager.py` is already **recall-shaped**:
  `format_memory_for_prompt` sends identity + recent + key-index only
  (memory_manager.py:194-215), plus `recall_memory` tool (main.py:440, 1274),
  `search_memory` scoring (349), short-term turns (478), sensitive-key masking
  (403), 200 K store guard (40). `core/memory_v2.py` (Working/Episodic/
  Procedural/`MemoryRetriever`) **dormant, tests only**.
- **Mark-LV:** simple 9 KB memory (dump-based).
- **V3:** Phase 20 — do **not** migrate wholesale. Keep v1 (it already does
  recall→relevance→selection), and selectively adopt v2 ideas (episodic
  trajectories for the planner) only when the planner is wired.
  **✅ DECIDED at M6:** keep v1 as the wired memory — `format_memory_for_prompt`
  stays the recall formatter feeding the system instruction (guarded by
  `test_system_instruction_is_assembled_by_v1_memory`). `core/memory_v2.py`
  episodic/trajectory ideas land only alongside planner wiring (M8+), never as
  a wholesale swap (ABSOLUTE RULE).
  **Risk:** low if untouched; HIGH if wholesale swap (forbidden by ABSOLUTE RULE).

### 3.12 Vision
- **AETHER:** `actions/screen_processor.py` + camera capture (main.py:65),
  vision cooldown guard pattern exists in reference (Mark-LV main.py:762-765),
  `core/spatial/` hand/gesture subsystem (10 files, wired main.py:672).
- **Problem:** sources (screen/camera/user image/generated image/browser) are not
  explicitly labeled at the perception boundary (Phase 29).
- **V3:** add source tagging in one choke point; keep spatial engine as-is.
  **Risk:** medium. **Test:** source-label unit tests.
  **✅ M7:** one module now owns the vocabulary — `core/vision_labels.py`
  defines the five sources and their labels (unknown source raises). The only
  two image send points route through it: main session
  `_flush_pending_vision` via `source_text`, side calls via
  `gemini.call(..., source=...)` which labels before the ladder (covers Live
  and REST). `file_processor` defaults to `user_image` and accepts a
  model-supplied `source` (junk falls back honestly); screen side-calls
  (`code_helper`, `computer_control`) pass `source="screen"`; browser and
  generated read-backs are hinted in the tool descriptions; `[SELF]` explains
  all five labels. 35 tests in `tests/test_vision_labels.py`, including choke
  guards that fail if any label is spelled outside the module.

### 3.13 Computer / browser control & verification
- **AETHER & Mark-LV:** identical pyautogui-based actions (AETHER supersets).
  **Neither verifies outcomes** — `open_app` uses fixed sleeps
  (open_app.py:90-111); zero `verif/observe` matches in computer/browser actions.
  `core/verifier.py` (`Verifier`, `VerificationRule`, strategies) **dormant**.
- **V3:** Phase 26 — wire `Verifier` for important actions (process exists?
  window visible? URL loaded?) before claiming success. **Files:**
  `actions/open_app.py`, `actions/computer_control.py`, `actions/browser_control.py`,
  `core/verifier.py`. **Risk:** medium (each tool needs a real check).
  **Test:** per-tool verify tests with fake process/window probes.
  **✅ M8:** Verifier wired through the dispatcher — the new
  `verification="verifier"` mode makes `action_loader` require a callable
  `verify` (both mismatch directions rejected at load), startup binds the
  probes (`_register_verifier_rules`), and `_dispatch_tool` runs each probe
  behind `VERIFY_TIMEOUT_S` and appends the verdict (`verification_note`:
  VERIFIED may be reported, FAILED must never be). Real checks:
  `open_app` proves a running process (psutil → tasklist fallback),
  `browser_control` reads the page URL back (PARTIAL for native opens,
  blank page = FAILED), `computer_control` checks the screenshot file and
  the focused window (pygetwindow) — everything else states that it has no
  observable post-state instead of rubber-stamping. Probe crashes fail
  closed. 43 tests in `tests/test_verifier_wiring.py`.

### 3.14 Confirmation & undo
- **AETHER:** `core/confirm.py` gate bound once to UI (main.py:2216-2220),
  `ConfirmBanner` (ui.py:2346), `resolve` (ui.py:5901); `core/undo.py` +
  `undo_stack.history/undo_last` (main.py:1283-1288).
- **Mark-LV:** **neither exists** (0 matches).
- **Verdict:** AETHER is the source of truth (spec Phase 30/31 satisfied).
  **V3 work:** coverage audit — every HIGH-risk tool routed through the gate;
  confirm the model can never answer the gate itself (inspect `confirm.resolve`
  callers). **Risk:** low. **Test:** risk-coverage test iterating all tools.
  **✅ M12:** the coverage audit is now a test file, and it found two real
  gaps that got fixed: (1) `memory_controller.clear` (safety: DANGEROUS) was
  self-authorized by the model writing `confirm_key` itself — the exact
  forgeable gate `core/confirm.py` exists to replace — and now parks behind
  `confirm.request` with the key fixed in code; (2) `desktop_control`
  organize/clean moved files with no protection at all — both now push a
  batch undo (journal pattern copied from `file_controller.organize_desktop`),
  and the tool declares `supports_undo: True`. The audit locks in the rest:
  safety's nine concrete DANGEROUS actions each map to a real mechanism
  (banner / undo / Gmail's trash-recoverable path), `params.get("confirmed"`
  and   `params.get("confirm_key")` appear nowhere in actions or plugins, and
  `confirm.resolve` remains UI-only. 13 tests in
  `tests/test_security_confirmation_audit.py` (existing `test_tool_metadata`
  keeps the dispatch-level gate tests: dangerous⇒gated, forbidden refusal,
  park-then-run).
  **✅ Post-RC (`0b62615`):** `shutdown_jarvis` now fails closed —
  `_execute_tool` refuses it with an honest sentence until real user input
  (mic transcript / typed command / web command) has reached the session, so
  the prompt rule ("only when the user explicitly ends the session") is
  enforced where a rule can actually be enforced: no banner, no pending
  action, nothing to confirm. The briefing never counts as input, every
  input is logged the moment it arrives (phantom speech must be visible in
  the log, not buried at turn-complete), and the tool description says the
  same. Two dispatcher tests in `tests/test_tool_metadata.py`.

### 3.15 Observability
- **AETHER:** `core/event_bus.py` (Priority, EventType, async handlers)
  **dormant**; runtime logging = `print()`; visual engine has structured `_evt()`
  events (proven pattern).
- **Mark-LV:** none (0 hits).
- **V3:** Phase 36 — wire the event bus behind `emit_event()`, keep `print()`
  compatibility shim, expose stream to dashboard + activity center (Phase 32).
  **Files:** `core/event_bus.py`, `main.py` (bind once), HUD consumers.
  **Risk:** medium. **Test:** event-emission assertions in existing tests.
  **✅ M11:** the bus is wired — `JarvisLive.__init__` constructs it, `run()`
  starts it before any emission point and subscribes one bridge;
  `emit_event()` is fire-and-forget (never raises/blocks; drops when the bus
  isn't running, including `__new__`-built test fakes) and every `print()`
  stays exactly where it was. Emissions: SYSTEM_STARTUP at boot,
  TOOL_RESULT/TOOL_ERROR from every `_dispatch_tool` turn, CRITICAL
  USER_INTERRUPT from dashboard emergency-stop. The bridge feeds both
  dashboard surfaces — the bounded `/api/events` ring and live `/ws` push
  (`broadcast(..., history=False)` so hot events never evict the chat
   replay buffer). 21 tests in `tests/test_dashboard_observability.py`.
   **✅ Phase 32:** the activity center — the line's second consumer.
   `core/activity.py` owns a bounded (200-row), lock-protected ring of
   one-line summaries; `_bridge_event` feeds it **first**, so the ring
   fills with or without a dashboard and a crashing dashboard cannot stop
   it; the HUD's drawer gained a 📡 ACTIVITY button opening
   `ui.ActivityOverlay`, a fixed-geometry panel that drains the ring on a
   GUI-thread QTimer (store/poll — the `set_audio_level` seam: no Qt
   object is ever touched from a bus callback), shows the whole backlog
   when opened, and offers CLEAR. Bound once from `run()` via
   `JarvisUI.bind_activity_feed`. 25 tests in
   `tests/test_activity_center.py`.

### 3.16 Dashboard
- **AETHER:** Bearer-token auth with per-token session keys, encrypted token
  storage, login page (dashboard/server.py:460-543). Strong.
- **V3:** expose status/health/events/emergency-stop (Phase 34) — never
  unauthenticated OS control. **Risk:** low-medium (security surface).
  **✅ M11:** three auth-gated routes — `GET /api/status` (server liveness +
  runtime health via a fail-soft status callback: state/session/muted/phone/
  bus stats), `GET /api/events` (the 200-deep event ring; live push rides
  the existing `/ws`), `POST /api/emergency-stop` (mutes the mic through the
  real toggle and flushes `audio_in_queue` so not one more byte reaches the
  model; idempotent, never raises, 503 when no runtime is attached).
  The §3.16 guarantee is under test: an unauthenticated client gets 401 on
  all three and the emergency callback is never invoked.

### 3.17 Security
- `config/api_keys.json` present on disk and **untracked** (verify `.gitignore`
  before every commit); no secrets observed in source; safety manager wired only
  via `actions/safety_action.py`. **V3:** confirm key file ignored, extend
  permission metadata (3.9) as the enforcement layer (Phase 40). **Risk:** low.
  **✅ M12:** the security half of the audit is executable —
  `.gitignore` covers `config/api_keys.json` and the test fails if the file
  ever becomes tracked; secret-shaped literals (Google/OpenAI/GitHub/Slack
  patterns) are banned from `actions/`, `plugins/`, `core/`, `dashboard/`,
  `memory/`, `main.py`, `ui.py`; and every dashboard route is classified —
  either `_auth`-gated, a documented query-token socket (`?token=` vs
  `self._tokens`), or one of the authentication-scheme endpoints (login /
  auto-login / device-login, each asserted to validate its one-time key or
   device token). Permission-metadata-as-enforcement remains Phase 40.
   **✅ Phase 40:** permission metadata is now the enforcement layer.
   Every TOOL dict — 21 actions and 8 inline tools — declares
   `permissions` from the closed vocabulary in `core/permissions.py`
   (capture / desktop / filesystem / generation / memory / messaging /
   network / process); `policy_from_meta` rejects anything outside it at
   load time; `_execute_tool` refuses a tool whose permission is revoked
   **before** the confirmation gate ever sees the call (availability
   first, consent second). Grants are two-tier per part-17: session-only
   changes, plus a permanent `config/permissions.json` (gitignored —
   asserted by test). A missing config file grants everything (the
   pre-Phase-40 behavior, so zero dispatch regression); a corrupt one
   revokes everything (fail closed); unknown names never grant. Plugins
   keep the safe defaults: no entry, no declared permissions, unchanged.
   The `_DEFAULT_PERMISSIONS` fallback exists only for `__new__`-built
   test fakes and grants everything — identical to a missing file.
   30 tests in `tests/test_permission_enforcement.py`, and the M4
   metadata suite now holds `permissions` as its sixth key.

### 3.18 Error recovery
- **AETHER:** bounded retry + backoff proven in visual engine (`_RETRYABLE`,
  generate_image.py), live reconnect (`_ReconnectSignal`), `self_checker` +
  `replanner` **dormant**.
- **V3:** adopt the visual-engine pattern as the house style for all retries;
  wire `self_checker` into the tool-result path (Phase 17/35). **Risk:** medium.

### 3.19 Performance
- **No measurements exist** (neither codebase). Suspects: `ui.py` 265 KB import,
  face asset load, wake-word install (ui.py:5660), plugin/action discovery,
  camera init, dashboard start.
- **V3:** Phase 37/14/15 — instrument first, then lazy-load optional systems.
  Never optimize by guess. **Risk:** low. **Test:** startup benchmark script.
  **✅ M13:** measured, V2 (`51f264a`) vs V3 (this tree), same machine/venv,
  `tools/bench_startup.py` run against a detached V2 worktree (bench +
  `core/perf.py` copied in; single-run wall clock, so treat as order-of-
  magnitude, not microbenchmark). Cold import cost (ms, V2 → V3):
  `main` 4034 → 997, `ui` 920 → 249, `google.genai` 1969 → 633,
  `actions.screen_processor` 692 → 208, `numpy` 330 → 110, `sounddevice`
  561 → 376, `memory.memory_manager` 107 → 12, `core.plugin_loader`
  158 → 27 (`core.spatial.engine` is a V3 addition: n/a → 840).
  Boot timeline (JarvisUI + JarvisLive, offscreen): **V2 5704 ms →
  V3 1910 ms (−67%)**, with V2's ui construction alone at 3786 ms vs
  V3's 1114 ms. Probes: wake first-touch 0 ms both; V2 had no camera
  probe at all (n/a = function absent, not broken) vs V3 1464 ms; spatial
  construct fails on this machine's mediapipe both as n/a in V3
  (`'HandTracker' object has no attribute '_hands'` — pre-existing,
  environment-level). Full outputs: `docs/aether-v3/bench-v2-vs-v3.txt`.

### 3.20 Testing (baseline)
- **900 tests pass today** (`pytest tests/ -q`), incl. 50 visual tests and
  foundation/planner/verifier/self_checker/event_bus/tool_registry/memory_v2/
  spatial suites covering dormant modules — **the dormant code is not stale,
  it is pre-tested**, which makes wiring it low-risk.
- Mark-LV: zero tests.
- **V3:** every milestone = full suite green + new tests for the milestone.
  **✅ M13:** full regression run at the M12 tree: **1177 passed, 0 failed**
  (baseline 900 + the milestone suites: vision labels 35, verifier wiring 43,
  content surface 19, dashboard observability 21, security/confirmation
  audit 13, plus the M2-M6 additions). The row's "900+" floor is cleared
  by a wide margin; every M2-M13 commit was gated on a full green run.
  **✅ M14 (RC):** final run at the release-candidate tree: **1196 passed,
  0 failed** (1177 + 19 `tests/test_degraded.py` covering the §3.6 wiring),
  in 24 s. Every M2-M14 commit gated on a full green run.
  **✅ Post-RC (`0b62615`):** **1198 passed, 0 failed** (1196 + the two
  shutdown-guard dispatcher tests), in 27 s.

---

## 4. Dormant-Subsystem Inventory (Phase-1 worklist)

| Module | Size | Status | Wiring target |
|---|---|---|---|
| `core/event_bus.py` | 8 KB | tests only | bind in `JarvisLive.__init__`, HUD + dashboard consumers |
| `core/verifier.py` | 13 KB | tests only | tool post-conditions (Phase 26) |
| `core/planner.py` + `replanner` + `task_graph` | 37 KB | tests only | multi-step tasks (M4/M8) |
| `core/self_checker.py` | 17 KB | tests only | tool-result sanity (Phase 35) |
| `core/tool_registry.py` | 11 KB | tests only | metadata source for Phase 17 |
| `core/reasoning.py` | 7 KB | tests only | planner support |
| `core/memory_v2.py` | 19 KB | tests only | selective, per M6 decision |
| `core/llm_client.py` | 21 KB | **not tested, unused** | delete-or-wire decision |
| `core/stt.py` + `core/tts.py` | 20 KB | **zero refs** | degraded-mode fallback or delete |
| `safety_manager` / `notification_manager` | 18 KB | actions only | ensure dispatcher routes exist |

---

## 5. Proposed V3 Milestone Plan (adapted from Phase 49)

| Milestone | Scope | Based on audit |
|---|---|---|
| **M1** | **AUDIT** | ✅ this report (checkpoint commit before proceeding) |
| **M2** | UI core: 13-state machine through existing `_state_sig`; real per-state visuals | §3.2 — ✅ (`3ad659d`) |
| **M3** | ModelManager + LiveModelManager wrapping existing `gemini.py` tiers; measured ladder | §3.5 — ✅ (`a26ae08`) |
| **M4** | Tool metadata (`risk/timeout/confirm/undo/verify`) + dispatcher enforcement | §3.9, §3.14 — ✅ (`60bfbb8`) |
| **M5** | Performance: instrument startup; lazy-load wake/camera/spatial/plugins; STT/TTS decision | §3.19, §3.6 — ✅ decision (`69efb40`), wiring in M14 |
| **M6** | Memory/session: keep v1 recall; reconnect-resume hardening | §3.11, §3.8 — ✅ (`47d82fc`) |
| **M7** | Vision: source labeling (screen/camera/user/generated/browser) | §3.12 — ✅ (`f1adaf4`) |
| **M8** | Computer agent: wire `Verifier` observe-verify loops | §3.13 — ✅ (`28d074c`) |
| **M9** | Content surface unification + video mic-mute verification | §3.2, §3.10 — ✅ (`a05262e`) |
| **M10** | Visual generation | ✅ shipped (`d93ed1e`) |
| **M11** | Dashboard: events/health/emergency-stop via event bus | §3.15, §3.16 — ✅ (`6fa10c9`) |
| **M12** | Security + confirmation coverage audit | §3.14, §3.17 — ✅ (`a363ec7`) |
| **M13** | Full regression: 900+ green, perf measured V2-vs-V3 | §3.20 — ✅ (`b2001d3`) |
| **M14** | V3 release candidate | ✅ RC — §3.6 wired, suite 1196 green (this commit) |

Rules enforced by this plan: no rewrites (only wiring + incremental edits),
checkpoint commit before each milestone (spec Phase 50), tests before/after,
AETHER-I identity preserved (Phase 53 — `JarvisLive` is internal historical
naming; user-facing strings normalize separately).

---

## 6. Key Risks

1. **`ui.py` (265 KB) is the highest-risk edit surface** — 100+ state refs, 232
   style sites. Mitigate: state machine behind existing signal seam; full test
   suite + manual HUD smoke each milestone.
2. **Dormant modules may have drifted from live APIs** — they are unit-tested but
   never exercised inside the running session. Wire one at a time behind flags.
3. **Model/session path is delicate** (Gemini Live reconnect semantics) — wrap,
   don't rewrite; measure every change (M3 requires latency/failure baselines).
4. **Never copy Mark-LV weaknesses**: no confirmation bypass, no fixed-sleep
   "verification", no single-hardcoded-model design, no untested monolith growth.
5. **Secrets hygiene**: `config/api_keys.json` must remain untracked — check
   `.gitignore` before every commit.

---

## 7. Definition-of-Done deltas (audit-grounded)

Items already satisfied by V2 (verify, don't rebuild): live theming, content
panel, confirmation, undo, echo guard, PTT, wake word, visual generation,
dashboard auth, session summary, recall-shaped memory, plugin system.
Items outstanding at audit time, now disposed: state vocabulary (✅ M2),
verify-after-act (✅ M8), tool risk metadata (✅ M4), wired event
bus/observability (✅ M11), ModelManager formalization (✅ M3), measured
performance (✅ M13: boot −67%, V2-vs-V3 numbers in
`docs/aether-v3/bench-v2-vs-v3.txt`), STT/TTS disposition (✅ M14: §3.6
degraded/offline wiring), vision source labels (✅ M7), startup
optimization (✅ M5 + M13: measured 1.9 s boot, judged sufficient).
Both items deliberately left open after RC are now closed: the activity
center (✅ Phase 32 — `core/activity.py` ring + the HUD's 📡 ACTIVITY
overlay, 25 tests) and permission-metadata enforcement in the dispatcher
(✅ Phase 40 — `core/permissions.py` two-tier ledger + refuse-before-gate
in `_execute_tool`, 30 tests; the M12 inventory test keeps guarding the
nine dangerous actions).
