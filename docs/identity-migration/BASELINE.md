# BASELINE — Phase 0 repository audit (pre-migration)

Generated: 2026-10-18 · Stages: STAGE A (audit only) · No files outside
`docs/identity-migration/` have been modified as of this document.

## 1. Working tree & repository

| Item | Value |
|---|---|
| Root | `C:\Users\Toshiba\Downloads\Mark-LIV-main` |
| Branch | `master`, in sync with `origin/master` |
| HEAD (baseline commit) | `9d20ac6` — "eval residuals: M2/M4 review sidecars + scorer…" |
| Remote | `https://github.com/Sudhakar-2005/Mark-LIV.git` |
| Uncommitted changes | **None** (`git status --short` empty; only `.gitignore`d runtime files present) |
| History | Not rewritten; this migration will be normal forward commits only |
| CI | **None** (no `.github/` directory) |
| Nested `Mark-LIV-main/` dir | junk (`\.pytest_cache` only), not tracked, ignored by cleanliness of status |

## 2. Detected architecture (inspected, not assumed)

Python 3.11.9 desktop application (PyQt6) — **no** package.json, **no**
Electron, **no** installer scripts (`.iss`/`.nsi`/`.spec` absent), **no**
TypeScript, **no** lint/typecheck configuration.

- **Entry points:** `main.py` (~168 KB app core + launch), `ui.py` (~374 KB
  PyQt6 UI), `run.bat` (console launcher).
- **Desktop config:** `config/` — `api_keys.json` (gitignored), `certs/`
  (gitignored), `automations/` (gitignored), tracked asset `jarvis.ico`.
- **Modules:** `core/` (persona, verifier, event_bus, `spatial/` camera
  gestures w/ TensorFlow Lite, `visual/`, `automation/`, `memory_v2`,
  `perf`, `tray_manager`, `installer` (dependency auto-installer), wake
  word), `actions/` (tool implementations), `plugins/` (calendar, gmail,
  quiz), `dashboard/` (HTTPS web dashboard + QR pairing, port 8000),
  `memory/` (user data), `tools/` (bench + eval scorers), `tests/`.
- **Model runtime:** Google Gemini Live API (`core/gemini.py`),
  provider ladder in `core/model_manager.py`.
- **Package manager:** pip via `requirements.txt` (OS-marker filtered);
  `setup.py` = one-time dependency + Playwright browser installer.
- **Version file:** `VERSION` = `1.0.0-stable`.

## 3. Build / run / test commands (as discovered)

| Purpose | Command | Verified |
|---|---|---|
| Setup | `python setup.py` | Inspected, not re-run (deps already installed) |
| Run app | `run.bat` or `python main.py` | Verified 2026-10-18 (app booted, PID 20668, 0 tracebacks in `%TEMP%\aether_boot_err.log`) |
| Full test suite | `QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q` | Verified baseline (below) |
| Scorecard | `python tools/eval_scorecard.py` | Verified baseline (below) |
| Startup bench | `python tools/bench_startup.py` (needs `AETHER_PERF=1`) | Available; not re-run for baseline |
| Lint / typecheck | **None configured** | n/a |

## 4. Baseline results (evidence — executed 2026-10-18, pre-migration)

| Check | Result |
|---|---|
| Full suite | **1475 passed, 0 failed**, 51 warnings, 26 subtests, 40.79 s |
| Scorecard groups | **472 passed, 0 failed** (all M1–M9 groups) |
| Live boot | App reached ready state: plugins 3/3, automation engine+scheduler, spatial engine+camera, event bus, news fetch HTTP 200 |
| Known pre-existing warnings (baseline, not regressions) | 1) `win10toast` `classAtom` AttributeError in background threads (notification tests); 2) `QPropertyAnimation: non-existing property smoothPos` (`core/spatial/gesture_cursor.py:51`) |
| Known limitations | No lint/typecheck baseline exists; hardware-dependent features (camera/mic) only exercised indirectly offscreen |

## 5. Identity layers found (the audit's central finding)

The repository carries **three stacked identities** plus the repo slug:

| Layer | Where it lives | Examples |
|---|---|---|
| **AETHER-I** (app display identity — *the migration target*) | `ui.py:90` `APP_VERSION`, ~100 lines across UI strings, tray, status logs, docstrings; `config/api_keys.json` `assistant_name`; docs headings; transcript speaker prefix | `setWindowTitle(f"{_display} - {APP_VERSION}")` (`ui.py:5188`), "Quit AETHER-I" tray action |
| **MARK LIV / Mark-LIV / MARK XL** (historical repo/release identity) | `readme.md` H1 + prose, `setup.py`, `run.bat`, `requirements.txt` comment, `LICENSE` line 1, `.gitignore` comment, `core/installer.py:2` header, clone URL | `# ?? MARK LIV (54)` |
| **JARVIS / J.A.R.V.I.S** (assistant persona + wake word + dashboard) | default `assistant_name` "JARVIS" (9 code sites), wake word "Hey Jarvis", `dashboard` `<title>JARVIS…</title>`, desktop shortcut `J.A.R.V.I.S.lnk`, `jarvis.ico`, `jarvis.key` certs | `config_manager.get_assistant_name()` default |
| **Repo slug `Mark-LIV`** (external) | remote URL; `readme.md` clone URL points to a *different* upstream (`FatihMakes/Mark-LIV`) | external, not changed without approval |

## 6. User data & sensitive files (protected — never touched)

- **Gitignored secrets:** `config/api_keys.json` (contains
  `assistant_name` + API keys), `config/certs/jarvis.key`/`.crt`,
  `.env*`, `*credentials*.json`, `**/token*.json`. Never printed,
  never staged, never moved.
- **Gitignored personal data:** `memory/sessions/*.json`,
  `memory/long_term.json`, `memory/model_stats.json`,
  `config/automations/`, `config/permissions.json`,
  `obsidian_conversation_log.txt`, `tests/eval/datasets/` (exported
  transcripts).
- **Transcript format (data, read-only):** 1022 logged turns inspected by
  prefix only: `[AETHER-I] ×678`, `[User] ×344`. The speaker prefix is
  **part of stored user data** — it must keep parsing forever.

## 7. Compatibility-sensitive identifiers discovered (must survive)

| Identifier | Kind | Evidence |
|---|---|---|
| `AETHER_PERF`, `AETHER_HUD_PAINT` env vars | external contract (scripts, tests, docs) | `core/perf.py:89,94`, `ui.py:465`, `tools/bench_startup.py:55`, tests |
| Logger names `aether.hud`, `aether.state` | log-parsing/monitoring contract | `ui.py:67`, `core/app_state.py:30`, `assertLogs` in tests |
| Turn prefix `AETHER-I:` | **stored user data** (1022 turns) + `tools/eval_export_datasets.py:54 role_of()` | sessions on disk |
| Default filename/folder `aether_generated*`, `aether_image.png` | user-visible paths, existing files, tests assert exact names | `actions/generate_image.py:182-204`, `tests/test_visual_generation.py:299,672,678` |
| Default assistant name `"JARVIS"` / `"J.A.R.V.I.S"` | persona + setup-screen default, deep in 9 sites | `config_manager.py:56`, `ui.py:413,2412,7842,8244` |
| Wake word "Hey Jarvis" | user habit + model | `main.py:3244`, `readme.md` |
| Desktop shortcut `J.A.R.V.I.S.lnk` + `config/jarvis.ico` | existing user shortcut, path-referenced asset | `ui.py:5817,5828` |
| Directory `docs/aether-v3/` | self-referenced doc path | `docs/aether-v3/phase0-audit.md:444,534` |

## 8. Migration constraints (from this audit)

1. No blind global replace — every occurrence is classified first
   (IDENTITY_INVENTORY.md).
2. Exactly two test files assert branding
   (`tests/test_dashboard_v3.py:58,60`) and must follow the rename;
   `tests/test_eval_instrumentation.py:105,115` asserts the *historical*
   transcript prefix and must **not** change.
3. `config/api_keys.json` is local user data: `assistant_name`
   currently "AETHER-I"; changing it is a reversible settings edit, no
   test asserts its value (verified `test_config_manager.py:40-41` only
   checks type; `test_phase6_hardening.py` uses temp files).
4. Window title format is `{assistant_name.upper()} - {APP_VERSION}`
   (`ui.py:5188`, `ui.py:7915`) → currently renders
   "AETHER-I - AETHER-I" on this machine; an exact "XYRIS I" title needs
   a dedup condition (small, reversible — proposed in MIGRATION_PLAN).
5. The app is currently **running** (PID 20668): must be closed before
   the Phase 4 startup regression check (port 8000 conflict).
6. Trademark/domain/GitHub-slug availability: **not checked, not
   claimed** — recorded as pending (spec §11).

## 9. Baseline artifacts

- Suite log: this session's pytest run (1475 passed) — reproduced with
  the command in §3.
- Boot logs: `%TEMP%\aether_boot_out.log`, `%TEMP%\aether_boot_err.log`.
- Scorecard snapshot: `docs/brain/EVAL_SCORECARD.md`
  (Generated 2026-10-18T13:02:28+00:00, 472 passed / 0 failed).
