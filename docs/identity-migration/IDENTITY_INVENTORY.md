# IDENTITY_INVENTORY — Phase 1 (every AETHER / MARK / JARVIS occurrence, classified)

Source: full-repo scans of 2026-10-18 (case-insensitive `aether` ×1266
lines incl. data; 400 files scanned excluding `.git`, `.venv`,
`__pycache__`, logs, and gitignored data dirs). Categories per spec §3
Rule 2: **1** user-facing branding · **2** internal identifier ·
**3** historical reference · **4** compatibility identifier · **5**
external integration · **6** test fixture/sample data ·
**7** security-sensitive · **8** irrelevant/no-action.

Disposition: **CHANGE** / **KEEP** / **PRESERVE-HISTORY** /
**NEVER-TOUCH (data)** / **DEFER-approval**.

## 0. Canonical names per layer (spec §5)

| Layer | Canonical value | Notes |
|---|---|---|
| Display name / window title / tray / HUD / dialogs / status logs | **XYRIS I** | Roman numeral I permanent; exact spelling; space preserved |
| Docs headings (active) | **XYRIS I** | Historical records keep AETHER-I |
| New machine-readable slug (future repo/package) | `xyris-i` | No package manifest exists today; slug adoption = deferred external step |
| Assistant persona name (settings default, wake word, dashboard persona prose) | `JARVIS` (kept) | Compat/UX identity, not the project identity |
| Repo slug + remote | `Mark-LIV` (kept) | External; renaming needs owner approval |

---

## A. Code — user-facing branding (CHANGE → XYRIS I)

| File:line | Existing value | Cat | New value | Compat impact | Validation | Risk |
|---|---|---|---|---|---|---|
| `ui.py:90` | `APP_VERSION = "AETHER-I"` | 1 | `"XYRIS I"` | Window titles, HUD watermark (`ui.py:6051` uses constant) follow automatically | `tests/test_dashboard_v3.py:58` updated & run | **Med** (assert exists) |
| `ui.py:5188`, `ui.py:7915` | `setWindowTitle(f"{_display} - {APP_VERSION}")` → today renders "AETHER-I - AETHER-I" | 1 | dedup: if `_display == APP_VERSION` show `XYRIS I` alone → title exactly **XYRIS I** | none (cosmetic branch) | `test_dashboard_v3` windowTitle assert | Low |
| `ui.py:1812` | `"Select a file for AETHER-I"` | 1 | `… for XYRIS I` | none | visual/manual boot | Low |
| `ui.py:2216` | `"Configure AETHER-I before first boot."` | 1 | `Configure XYRIS I…` | none | boot (setup screen) | Low |
| `ui.py:2625` | docstring `six AETHER-I personas` | 1/2 | `six XYRIS I personas` | none | none needed | Low |
| `ui.py:3134,3137` | `what AETHER-I hears/talks` | 1 | `XYRIS I` | none | boot | Low |
| `ui.py:3285` | `"WHAT AETHER-I REMEMBERS"` | 1 | `"WHAT XYRIS I REMEMBERS"` | none | boot (memory overlay) | Low |
| `ui.py:3878,3919` | `"?  AETHER-I - INITIALISING"` / `f"?  AETHER-I - {state}"` | 1 | `XYRIS I` | none | boot; grep label | Low |
| `ui.py:4482,4520` | `"ask AETHER-I to design…"` | 1 | `ask XYRIS I…` | none | boot (automations empty state) | Low |
| `ui.py:5116` | `"Phone connected - AETHER-I ready"` | 1 | `XYRIS I ready` | none | dashboard flow manual | Low |
| `ui.py:5459` | `"AETHER-I is running in the background…"` | 1 | `XYRIS I is running…` | none | manual (minimize-to-tray prompt) | Low |
| `ui.py:7636` | `"…shows what AETHER-I is "` | 1 | `XYRIS I` | none | boot (avatar overlay text) | Low |
| `main.py:3244` | `"SYS: AETHER-I online - sleeping. Say 'Hey Jarvis' to wake me."` | 1 | `"SYS: XYRIS I online - sleeping. Say 'Hey Jarvis'…"` (**wake word kept**) | none | boot log line + manual | Low |
| `main.py:3248` | `"SYS: AETHER-I online."` | 1 | `SYS: XYRIS I online.` | none | boot | Low |
| `core/tray_manager.py:76` | `QAction("Quit AETHER-I", …)` | 1 | `Quit XYRIS I` | none | boot + tray manual | Low |
| `core/tray_manager.py:82` | `setToolTip("AETHER-I  (Ctrl+Alt+J to open)")` | 1 | `XYRIS I  (…)` | none | tray manual | Low |
| `dashboard/static/app.html:6` | `<title>JARVIS Dashboard</title>` | 1 | `<title>XYRIS I Dashboard</title>` (page label; persona prose below kept) | none (no test asserts) | open page / grep | Low |
| `dashboard/static/login.html:6` | `<title>JARVIS</title>` | 1 | `<title>XYRIS I</title>` | none | grep | Low |
| `config/api_keys.json:5` (local, gitignored) | `"assistant_name": "AETHER-I"` | 1/7 | `"XYRIS I"` — reversible settings edit, **value only, file never printed** | Drives title/log-prefix/persona references on this machine; no test asserts value (verified) | `test_config_manager` (type-only) + boot shows "XYRIS I online" | Med (user setting — reversible, recorded) |
| `readme.md:1` | `# ?? MARK LIV (54)` | 1 | `# ?? XYRIS I` + rename note (see §D) | none | manual read | Low |
| `readme.md:12,18` | current-product overview sentences start "MARK LIV is…" / "Mark LIV rebuilt…" | 1/3 | reword to XYRIS I, keep "historically MARK LIV / AETHER-I" note | none | manual read | Low |
| `setup.py:2,56,76` | `MARK LIV — one-time setup` / `MARK LIV needs at least Python` / `MARK LIV setup` | 1 | `XYRIS I …` (3 occurrences) | none | `python setup.py` prints (manual, deps already installed) | Low |
| `run.bat:2` | `title MARK LIV` | 1 | `title XYRIS I` | none | run.bat manual | Low |
| `requirements.txt:1` | `# MARK LIV - Python dependencies` | 1/2 | `# XYRIS I - Python dependencies` | none | none | Low |
| `.gitignore:1` | `# … MARK LIII …` (stylized MARK LIV) | 2/8 | `# … XYRIS I …` | none (pattern lines untouched) | `git status` clean check | Low |
| `core/installer.py:2` | `MARK XL - Dependency auto-installer.` (comment) | 3/8 | `XYRIS I - Dependency auto-installer.` | none | none | Low |

## B. Code — compatibility identifiers (KEEP — do not rename)

| File:line(s) | Value | Cat | Why kept | Validation |
|---|---|---|---|---|
| `core/perf.py:16,88,89,94`; `main.py:39,3414`; `tools/bench_startup.py:11,55`; `tests/test_startup_perf.py:85,89`; `ui.py:462,465,1541`; `tests/test_core_visual_v2.py:393,399` | env `AETHER_PERF`, `AETHER_HUD_PAINT` (+ comments naming them) | 4/5 | external contract; scripts/tests/docs depend on exact names (spec Rule 5 example) | suite green unchanged |
| `ui.py:67` → logger `aether.hud`; `core/app_state.py:30` → `aether.state`; asserts `tests/test_core_visual_v2.py:414,481` | logger names | 4/5 | log parsers, `assertLogs` contracts | suite green unchanged |
| `actions/generate_image.py:182,184,190,204,650`; `tests/test_visual_generation.py:299,672,678` | filename/folder `aether_generated*`, `Pictures/aether_generated` | 4 | existing user files + tests assert exact names | `test_visual_generation` unchanged green |
| `ui.py:2165` | default save name `aether_image.png` | 4 | same filename family as above | none (consistent decision) |
| `tools/eval_export_datasets.py:54` | `if prefix == "AETHER-I":` | 4 **+ change-permitted** | must keep parsing 678 stored `AETHER-I:` turns; **additive** `"XYRIS I"` acceptance required so *new* turns classify | extend `test_eval_instrumentation` (both prefixes) |
| `tools/eval_m1_recall.py:70` | heuristic literal `aether-i: good morning` | 4/6 | matches historical transcript content | unchanged green |
| `tests/test_eval_instrumentation.py:105,115` | fixtures `role_of("AETHER-I: Hello")`, `turns: ["AETHER-I: ok", …]` | 6 | historical-format fixtures — spec says don't erase; **extend, don't replace** | new assertion added |
| `config_manager.py:56,73`; `ui.py:413,2412,7842,8244`; `main.py:1393` | default assistant name `"JARVIS"`/`"J.A.R.V.I.S"` | 4 | persona default; changing fresh-install default = separate product decision (DEFER-G) | `test_config_manager`, `test_foundation`, `test_phase6` unchanged |
| `main.py:3244` wake word phrase, `core/wake_word.py`, readme "Hey Jarvis" | wake word | 4/5 | trained model + user habit | manual wake test (blocked offscreen) |
| `ui.py:5817,5828` | `config/jarvis.ico`, shortcut `J.A.R.V.I.S.lnk` | 4 | existing user shortcut; asset path referenced by code; icon has no old-name text (inspected: binary icon, generic) | boot (tray icon visible) |
| `dashboard/server.py:221`; persona prose `JARVIS is …` ×~450 lines | assistant persona references | 4 | persona identity, kept deliberately | boot |

## C. Code — internal comments/docstrings naming current product (CHANGE, zero behavior)

| File:line(s) | Count | Change to |
|---|---|---|
| `core/spatial/*` header comments (`camera_manager, engine, gesture_cursor, gesture_interpreter, gesture_recognizer, hand_tracker, interaction_state, safety_controller, ui_controller` :4; `spatial/__init__:2,4`) | 11 lines | `AETHER-I` → `XYRIS I` |
| `core/automation/__init__.py:6` | 1 | `existing XYRIS I systems` |
| `core/visual/provider.py:4,11`, `core/visual/__init__.py:1` | 3 | `XYRIS I` |
| `core/app_state.py:1`, `core/event_bus.py:4`, `core/memory_v2.py:1`, `core/persona.py:1,49`, `core/reactor_visual.py:1`, `core/verifier.py:4,7` | 9 | `XYRIS I` |
| `core/perf.py:3` (`AETHER-I's audit (3.19)`) | 1 | **PRESERVE** (historical audit citation) |
| `actions/generate_image.py:1,543` | 2 | `XYRIS I` |
| `tests/test_app_state.py:1`, `tests/test_model_manager.py:1`, `tests/test_tool_metadata.py:1` | 3 | docstring `AETHER-I V3` → `XYRIS I V3` |
| `tests/test_visual_generation.py:6` | 1 | `XYRIS routes…` |

## D. Documentation

| File:line(s) | Disposition | Detail |
|---|---|---|
| `docs/architecture/00-master.md:1,5,41,45,46` | **CHANGE** | H1 → `# XYRIS I Intelligence Architecture V0.1`, scope/stage lines |
| `docs/architecture/part-1:3,5` · `part-2:9,11,13,18,24,27` · `part-4:16,54` · `part-10:5` · `part-12:5` · `part-13` (10 lines) · `part-15:3` · `part-16:6` · `part-18:41` · `part-20:5,26,28,38` · `part-21:59,64,85` | **CHANGE** (active current/future-state architecture) | `AETHER…` → `XYRIS…` preserving phrase structure (`AETHER FOUNDATION MODEL` → `XYRIS FOUNDATION MODEL`, `AETHER Intelligence Benchmark (AIB)` → `XYRIS Intelligence Benchmark (XIB)`? — **no**: keep benchmark acronym AIB unless doc already defines; per-file judgment during implementation, recorded in IMPLEMENTATION_REPORT) |
| `docs/brain/BRAIN_AUDIT.md:1` · `CORE_VISUAL_DESIGN.md:1` · `CORE_VISUAL_V2_DESIGN.md:1,201` · `DASHBOARD_V3_DESIGN.md:110,152,242,305` | **CHANGE** | active design docs: headings + product-name prose. **KEEP** `CORE_VISUAL_V2_DESIGN:29,120,205,234,240` (literal env/logger identifiers) and `DASHBOARD_V3_DESIGN:282` (real file path `%TEMP%\aether_v3_boot.log`) |
| `docs/automation/phase14-definition-of-done.md:1` | **CHANGE** | heading |
| `docs/persona_engine_plan.md:1,5` | **CHANGE** | title + `**Project**: XYRIS I (formerly AETHER-I / Mark-LIV) — a PyQt6…` (history kept inline) |
| `docs/aether-v3/phase0-audit.md` (33) + `bench-v2-vs-v3.txt` (1) | **PRESERVE-HISTORY** | point-in-time audit/benchmark record of the AETHER-I V2↔V3 comparison; not rewritten (spec §7E). Directory name also kept (self-referenced at `phase0-audit.md:444,534`) |
| `docs/identity-migration/*` (this folder) | **new** | records the rename explicitly |
| `readme.md:84,167,282,283,311` (`What's New in Mark LIV`, `every Mark from LII`, clone URL, tree) | **PRESERVE-HISTORY/external** | release-history prose + upstream URL (`FatihMakes/Mark-LIV`) are historical/external — not rewritten |
| `LICENSE:1` `MARK LIV - JARVIS` | **CHANGE (STAGE G, user-approved)** | line 1 only → `XYRIS I — JARVIS`; copyright + CC BY-NC 4.0 grants byte-identical |

## E. Data — NEVER TOUCH (user data / sample fixtures)

| Location | Content | Cat |
|---|---|---|
| `memory/sessions/*.json` | 678 `AETHER-I:` + 344 `User:` turns (read-only inspection only) | user data |
| `memory/long_term.json`, `model_stats.json`, `config/automations/`, `config/permissions.json` | personal memory/workflows | user data |
| `tests/eval/datasets/{sessions.jsonl (633), m1_store.json (247), m2_labels.csv (200), m4_ab_packet.md (10)}` | exported transcripts + eval packets (gitignored) | 6 |
| `obsidian_conversation_log.txt`, `fde_roadmap.txt` | personal notes | user data |
| `config/api_keys.json` | API key values (never printed; only `assistant_name` value edited) | 7 |
| `config/certs/jarvis.key`, `jarvis.crt` | TLS material (filenames referenced by server) | 7 |

## F. External / DEFER (approval required)

> **STAGE G note:** every row below was resolved after explicit user
> approval — resolutions and evidence in **§I** (bottom of file).

| Item | Reason |
|---|---|
| GitHub repo slug `Mark-LIV` → `xyris-i` | external service; owner action; old URLs/remote break → document, do not execute silently (spec Rule 5 / external) |
| `git remote` URL | follows repo rename; keep until approved |
| `readme.md` clone URL `FatihMakes/Mark-LIV` | external upstream reference; may not even be ours to change |
| Trademark / domain / handle availability | **not checked, not claimed** (spec §11) |
| New XYRIS I icon/wordmark asset | current `jarvis.ico` shows no old-name text; redesign optional (spec §7C "if needed") → approval |
| Default assistant name `JARVIS` → `XYRIS I` on fresh installs | persona/product decision affecting setup UX + 9 code sites → approval |
| Wake word rebrand | trained model + habit → keep |
| Window-title dedup branch (proposed) | small logic change — see MIGRATION_PLAN §A (STAGE C risk review) |

## G. Occurrence totals (for Phase 5 comparison)

- Source (`.py/.html/.bat/.txt/.json`, excl. data dirs): **~100 `AETHER`
  lines** — of which user-facing ~35, compat ~40 (env/logger/filenames),
  comments/docstrings ~25.
- Data (gitignored): **1100 lines** (633+247+200+10+10) — untouched.
- Docs: **~155 lines** — ~120 CHANGE (active), ~35 PRESERVE
  (`docs/aether-v3`, env-literal mentions, historical Mark prose).
- Tests: 12 lines — 5 CHANGE (branding asserts + docstrings),
  4 KEEP (historical prefix/env/logger/filename), 3 extended.

Phase 5 audit will re-scan and re-classify every remaining `AETHER`
occurrence against this table.

## H. Addendum — findings from STAGE D implementation (same day)

Occurrences discovered after the initial scan and classified:

| File:line | Value | Cat | Disposition |
|---|---|---|---|
| `core/installer.py:2`, `core/llm_client.py:2`, `core/stt.py:2`, `core/tts.py:2` | `MARK XL` headers (4th legacy brand variant) | 2/3 | **CHANGE** → XYRIS I (comment-only) |
| `tools/bench_startup.py:1` | `"""Startup benchmark for AETHER-I V3…` | 1/2 | **CHANGE** → XYRIS I (lines 11/55 env refs KEEP) |
| `core/audio_devices.py:20` | `Mark LV learned this…` | 3 | **PRESERVE-HISTORY** |
| `docs/brain/DASHBOARD_V3_DESIGN.md:85` | `APP_VERSION = "MARK LIV"` (V2-state record) | 3 | **PRESERVE-HISTORY** |
| `docs/persona_engine_plan.md:6` | repo path `…\Mark-LIV-main\Mark-LIV-main` | 5 | **KEEP** (path) |
| `docs/brain/BRAIN_AUDIT.md:3` | path + commit `db7689c` | 3 | **KEEP** (record) |
| `readme.md:163,167,169,264,266,274,282,283,311,405` | release-series prose (`Mark LI–LIII`, roadmap table, clone URL, tree, "Mark 100") | 3/5 | **PRESERVE-HISTORY/external** |
| `docs/architecture/part-15:3,44-47`, `part-21:104` | `AIB` benchmark/tier ids | 2 | **CHANGE** → `XIB` (unreleased docs, 0 external refs — STAGE C judgment) |
| `dashboard/static/app.html:189` | `JARVIS is ready for your next command` | 4 | **KEEP** (persona prose; only `<title>` labels changed) |
| `readme.md:4` (new) | identity note mentioning AETHER-I/MARK LIV history | 3 | **intentional** history mention |

Section G totals updated by the implementation: 131 scripted edits +
1 test-data fix = **132 changes applied**; post-migration re-scan found
**0 mismatches** against this table (see VALIDATION_REPORT §5).

## I. Addendum — STAGE G resolution of deferred items (same day, user-approved)

User instruction: "Complete all the todos" → the four approval-gated
items were executed; one was resolved as KEEP by ruling (R4):

| Item (§F) | Resolution |
|---|---|
| GitHub repo slug | **DONE** — `Sudhakar-2005/Mark-LIV` → `Sudhakar-2005/xyris-i` (owner-authenticated API `PATCH /repos/… {name}`); `origin` remote updated; `git ls-remote origin HEAD` = `981340d`; GitHub redirects old URLs. |
| `git remote` URL | **DONE** — `https://github.com/Sudhakar-2005/xyris-i.git` |
| `readme.md` clone URL `FatihMakes/Mark-LIV` | **KEEP** (unchanged) — external upstream reference (R6 / §D); not this repo's slug. |
| Trademark / domain / handle | **PRELIMINARY SCAN DOCUMENTED — no clearance claimed** (spec §11); findings in MIGRATION_PLAN §G + VALIDATION_REPORT §8. |
| New XYRIS I icon | **DONE** — `_build_jarvis_icon` → `_build_app_icon`; glowing-X mark; fresh `config/xyris.ico` (sizes 16–256); `config/jarvis.ico` retained (rollback + existing shortcuts). |
| Fresh-install shortcut labels | **DONE** — `XYRIS I.lnk` / `XYRIS I.app` / `XYRIS I.desktop`, `CFBundleName` `XYRIS I`, Description `XYRIS I AI Assistant`, macOS launcher `XYRIS`. |
| Default assistant name `JARVIS` → `XYRIS I` | **DONE** — 13 lines / 16 default-value occurrences: `memory/config_manager.py:55,56(×2),73`, `main.py:1393,1396`, `ui.py:413,1652,2412,2618,5180,7842(×2),7913,8244(×2)`. |
| Wake word rebrand | **RESOLVED AS KEEP** (R4 / spec §3 Rule 4) — "Hey Jarvis" + `WAKE_MODEL="hey_jarvis"` untouched; not executed by design. |
| Window-title dedup | already DONE in STAGE C. |

**JARVIS occurrences surfaced during Stage G (post-audit scan) and classified:**

| File:line | Value | Cat | Disposition |
|---|---|---|---|
| `main.py` ×34 (`[JARVIS] …` console prints) | log-channel labels | 4 | **KEEP** — persona/log layer (R4), same class as wake word; audit gap, now recorded |
| `dashboard/server.py:401` | cert `ORGANIZATION_NAME "JARVIS"` | 7 | **KEEP** — existing paired TLS certs |
| `main.py:2327,2832`, `core/activity.py:73`, tests | event `source: "jarvis"` id | 2 | **KEEP** — event schema compat |
| `ui.py:6112,7917` | `("JARVIS","J.A.R.V.I.S")` subtitle conditions | 4 | **KEEP** — still fires if a user sets the name to JARVIS |
| `ui.py:1675` | `startswith("jarvis:")` (+ new `startswith("aether-i:")`) | 2 | **KEEP + extended** — legacy session line-tags keep rendering as AI lines |
| `ui.py:7442,7465,7469,7450,7475,7495` | autostart ids `JARVIS_AI`, `com.jarvis.assistant*`, `jarvis.desktop` | 5/7 | **KEEP** — changing check+write ids would double-register existing installs; internal identifiers only |
| `core/wake_word.py:192`, `WAKE_MODEL` | wake model name | 4 | **KEEP** — trained model file |
