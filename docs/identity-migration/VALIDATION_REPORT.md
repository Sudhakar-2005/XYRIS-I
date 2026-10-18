# VALIDATION_REPORT — XYRIS I identity migration (STAGE E)

Date: 2026-10-18 · Migration base: `9d20ac6` · All results below are
actual command outputs captured this session.

## 1. Automated verification of the edit set

| Check | Result |
|---|---|
| Migration script | 131 planned edits applied; 0 write errors |
| Independent verifier (`xyris_verify.py`, 132 assertions: new strings present, stale strings gone, KEEP tokens counted, history intact, config updated) | **132/132 PASS** |
| `py_compile` on all 37 edited `.py` files (+ `setup.py`) | **OK, 0 errors** |

## 2. Test suite (primary DoD gate)

```
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
→ 1475 passed, 51 warnings, 26 subtests passed in 61.38s
```
- **Baseline (pre-migration):** 1475 passed / 51 warnings / 26 subtests
  → **identical counts, 0 regressions.**
- One intermediate failure occurred during implementation
  (`test_role_mapping_and_export_counts`: exported role list after the
  both-prefixes fixture) — the assertion data was updated to the
  correct 3-row expectation; the failure itself **proved** `XYRIS I:`
  turns classify as `assistant` end-to-end.
- Warnings are the pre-existing set (win10toast thread AttributeError ×
  N, unchanged from baseline).
- Branding canary: `test_dashboard_v3::test_branding_constants` now
  asserts `APP_VERSION == "XYRIS I"` and title contains `XYRIS I`.

## 3. Scorecard

```
python tools/eval_scorecard.py
→ scorecard written: docs/brain/EVAL_SCORECARD.md  (472 passed, 0 failed)
```
Identical to the pre-migration scorecard run (472/0).

## 4. Startup regression + identity verification (live)

App relaunched after migration (detached `python main.py`, PID 10592):

| Evidence | Result |
|---|---|
| Port 8000 (dashboard) | **LISTENING** |
| `aether_boot_err.log` tracebacks at first check | **0** |
| Actions/plugins/automation scheduler/spatial engine/camera/audio devices/news fetch (HTTP 200) | all loaded/started (boot log) |
| `aether_boot_out.log` identity scan | **0 occurrences of AETHER** (JARVIS persona lines present by design) |
| Window title, constructed live offscreen via real `ui.MainWindow("face.png")` | `WINDOW_TITLE: XYRIS I` / `TITLE_EXACT_XYRIS_I: True` (dedup branch works; local `assistant_name=XYRIS I` applied) |
| stderr after session ran ~1 min | Gemini Live websocket `APIError 1008` — **pre-existing**: historic `app.err.log` contains **43 prior** `APIError: 1008` + 43 `ConnectionClosedError` occurrences from the AETHER-I era; `git diff main.py` shows only the 2 log-string lines, receive path untouched; app stayed alive (PID + port up) |
| `SYS: XYRIS I online…` line | string verified statically + by suite; it renders in the **UI log widget** (`write_log` → widget only; historic `app.out.log` contains 0 such lines in any era, so stdout redirection never captures it). Visible to the user in the app log pane |
| Tray `Quit XYRIS I` / tooltip / balloon strings | covered by static verification; visible on minimize (manual) |

Known non-regressions observed: `smoothPos` QPropertyAnimation warning
(pre-existing, `gesture_cursor.py`), `QSystemTrayIcon: No Icon set`
(Qt warning during tray init sequence), pynvml FutureWarning — all
present in baseline behavior/inspection.

## 5. Phase 5 identity re-scan (post-migration)

Full-repo scan (code + docs + configs, excluding gitignored data):

- **Changed surface:** every current-product occurrence now reads
  `XYRIS I`/`XYRIS` (131 edits).
- **Remaining `AETHER` occurrences (all classified KEEP/PRESERVE/data):**
  env `AETHER_PERF`/`AETHER_HUD_PAINT` (7 files), loggers
  `aether.state`/`aether.hud` (2 + asserts), filenames
  `aether_generated*`/`aether_image.png` (2 + 3 test asserts),
  `role_of` old-prefix compatibility tuple (1), historical heuristic
  (`eval_m1_recall.py:70`), deliberate test fixtures
  (`test_eval_instrumentation` ×2), `core/perf.py:3` audit citation,
  `docs/aether-v3/*` (34 lines), 6 env-literal rows + 1 temp-path row in
  brain docs, readme identity note (intentional history mention),
  persona plan "(formerly AETHER-I / MARK LIV)" (intentional).
- **`MARK` remaining:** release-history prose in readme (9 sites),
  `DASHBOARD_V3_DESIGN:85` V2-state record, `audio_devices.py:20`
  "Mark LV" history, external URLs/paths — all PRESERVE.
- **`JARVIS`:** persona layer untouched (defaults, wake word, shortcut,
  dashboard persona prose; only the two HTML `<title>` labels changed).
- **Mismatch count vs inventory: 0.**

## 6. Data & scope safety

- `git status`: **61 files modified** (60 migration files + regenerated
  `docs/brain/EVAL_SCORECARD.md`), 1 untracked dir
  (`docs/identity-migration/`). Nothing from `memory/`, `config/automations/`,
  `tests/eval/datasets/`, `config/certs/`, or personal logs staged or modified.
- `config/api_keys.json`: only the `assistant_name` value edited
  (gitignored anyway; value never printed; reversible — see ROLLBACK_GUIDE).
- `LICENSE`, `.gitignore` patterns, `docs/aether-v3/`, `.ico`/`.lnk`
  assets: untouched.
- Line endings: diffs show content-only changes (142 insertions / 139
  deletions across 61 files — no line-ending churn).

## 7. DoD checklist (spec §10)

| Criterion | Status | Evidence |
|---|---|---|
| XYRIS I canonical, old names explained | ✅ | inventory §0/§H, readme identity note, persona plan history line |
| No blind replace; occurrence table | ✅ | IDENTITY_INVENTORY + Phase 5 re-scan, 0 mismatches |
| Backward compatibility preserved | ✅ | KEEP lists verified by 132-check script; `role_of` accepts both prefixes (test-proven) |
| Tests pass 100% | ✅ | 1475 passed / 0 failed; scorecard 472/0 |
| Startup verified with new identity | ✅ | port up, 0 initial tracebacks, 0 AETHER in logs, live title `XYRIS I` |
| No personal data altered | ✅ | §6 |
| No secrets exposed | ✅ | config values never printed; certs untouched |
| All 6 deliverable docs complete | ✅ | this folder (reports: this + IMPLEMENTATION + ROLLBACK) |
| Pending items not overclaimed | ✅ | trademark/remote rename/LICENSE = deferred, listed in MIGRATION_PLAN §G |
