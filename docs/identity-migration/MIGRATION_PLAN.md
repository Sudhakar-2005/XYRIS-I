# MIGRATION_PLAN — Phase 2 (approved implementation plan + STAGE C risk review)

Input: `BASELINE.md` (Phase 0) + `IDENTITY_INVENTORY.md` (Phase 1).
Constraint recap: AETHER-I → **XYRIS I** is the migration; MARK LIV
(8 releases of history), JARVIS (persona/wake word) and all compat
identifiers are preserved by design. Every group below maps to an
inventory section. No file is touched unless listed here.

## STAGE C — risk review (decisions before code)

| # | Decision point | Ruling | Rationale / reversibility |
|---|---|---|---|
| R1 | Window title: currently `{name.upper()} - {APP_VERSION}` = "AETHER-I - AETHER-I"; spec wants exact **XYRIS I** | **CHANGE** — add dedup branch in both title sites (`ui.py:5188`, `ui.py:7915`): if `_display == APP_VERSION`, title = `_display` alone | ~4 lines, no test depends on the hyphen form except title-content assert (updated together); reversible |
| R2 | `role_of()` prefix parser | **ADDITIVE ONLY**: accept `{"AETHER-I", "XYRIS I"}` — never remove old | 678 stored turns must classify forever; new turns get `XYRIS I:` prefix; reversible, backward-compatible |
| R3 | Local `config/api_keys.json` `assistant_name` | **CHANGE value to "XYRIS I"** — user setting on this machine, no test asserts it, recorded for rollback | gitignored → not part of the commit; rollback = set back to "AETHER-I" |
| R4 | Persona default `"JARVIS"` (setup screen default, wake word, `J.A.R.V.I.S.lnk`) | **KEEP** | persona/UX layer (spec §3 Rule 4); changing defaults = product decision, listed as DEFER-G |
| R5 | Dashboard `<title>` tags ("JARVIS Dashboard"/"JARVIS") | **CHANGE to XYRIS I…** (page *label*), **KEEP** persona sentence at `app.html:189` | no test asserts either; labels are project identity, prose is persona |
| R6 | `readme.md` MARK LIV prose | **CHANGE** H1 + current-product overview lines; **KEEP** "What's New in Mark LIV" release history, clone URLs, `Mark LIV/` tree | preserves history (spec §7E); no test |
| R7 | `LICENSE` line 1, GitHub slug, remote URL, trademark search | **DEFER** (external/approval/legal) | spec §11: never claim legal clearance; no remote ops without approval |
| R8 | `aether_generated` / `aether_image.png` / env vars / logger names / `docs/aether-v3/` path | **KEEP** | Rule 5 compat + self-referenced paths + tests assert exact values |
| R9 | Running app (PID 20668) | **CLOSE before Phase 4 startup regression** (port 8000 bind + real title check) | otherwise boot test hits "address in use" |
| R10 | Historical records (`docs/aether-v3/*`, `CORE_VISUAL_V2:29,120,…` env literals, `perf.py:3`, `readme` release history, eval transcript fixtures) | **PRESERVE** | point-in-time truth; rewriting falsifies history |

**Minimal migration set** = groups A–E below. F/G/H are external or
approval-gated and produce documentation entries only.

---

## Group A — user-facing branding, core code (low risk)

**Files:** `ui.py`, `main.py`, `core/tray_manager.py`, `dashboard/static/app.html`, `dashboard/static/login.html`

| Change | Lines (inventory ref) |
|---|---|
| `APP_VERSION = "XYRIS I"` | ui.py:90 |
| Title dedup branch | ui.py:5188, 7915 (R1) |
| 13 dialog/label/overlay strings | ui.py:1812, 2216, 2625, 3134, 3137, 3285, 3878, 3919, 4482, 4520, 5116, 5459, 7636 |
| Boot status lines (wake word kept) | main.py:3244, 3248 |
| Tray action + tooltip | tray_manager.py:76, 82 |
| Page titles only | app.html:6, login.html:6 |

**Reason:** every place a human sees the old project name.
**Deps:** none. **Risk:** Low (string-only), except assert in
`test_dashboard_v3.py:58,60` → updated in group D.
**Tests:** `test_dashboard_v3` (branding), full suite after.
**Rollback:** `git revert` commit; local `assistant_name` edit recorded
separately.

## Group B — comments/docstrings naming current product (zero behavior)

**Files:** `core/spatial/*` (11 header lines), `core/automation/__init__.py:6`,
`core/visual/{provider,__init__}.py` (3), `core/{app_state,event_bus,memory_v2,persona,reactor_visual,verifier}.py` (9),
`actions/generate_image.py:1,543`, `core/installer.py:2` ("MARK XL"),
`tests/test_{app_state,model_manager,tool_metadata,visual_generation}.py` (4 docstrings).

**KEEP within these files:** `core/perf.py:3` (historical audit cite),
all env/logger literals, `aether_generated` strings.
**Risk:** None (comments). **Tests:** none needed; suite still run.

## Group C — documentation (active docs CHANGE, history PRESERVE)

| Action | Files |
|---|---|
| CHANGE headings + product refs | `docs/architecture/00-master.md` + parts 1,2,4,10,12,13,15,16,18,20,21; `docs/brain/{BRAIN_AUDIT,CORE_VISUAL_DESIGN,CORE_VISUAL_V2_DESIGN,DASHBOARD_V3_DESIGN}.md` (name lines only); `docs/automation/phase14-definition-of-done.md`; `docs/persona_engine_plan.md:1,5` |
| CHANGE repo metadata (MARK LIV branding) | `readme.md:1,12,18` (+ explicit "renamed from AETHER-I / MARK LIV" note), `setup.py:2,56,76`, `run.bat:2`, `requirements.txt:1`, `.gitignore:1` |
| KEEP literal identifiers inside docs | `CORE_VISUAL_V2_DESIGN` env/logger lines, `DASHBOARD_V3_DESIGN:282` real temp path |
| PRESERVE history | `docs/aether-v3/*`, readme release history/clone URL/tree, `LICENSE:1` (deferred) |

**Risk:** Low (docs). **Rollback:** per-commit revert; history files
never staged.

## Group D — tests (follow the rename; extend, never delete)

| File | Change |
|---|---|
| `tests/test_dashboard_v3.py:58` | `assertEqual(APP_VERSION, "XYRIS I")` |
| `tests/test_dashboard_v3.py:60` | `assertIn("XYRIS I", …windowTitle())` |
| `tests/test_eval_instrumentation.py` | **add** `role_of("XYRIS I: Hello") == "assistant"` case; keep both `AETHER-I:` fixtures (R2) |
| docstrings | group B test files |

**Risk:** these asserts are the canary — they fail if group A missed a
site. **Rollback:** revert.

## Group E — local config (NOT committed)

| Action | File |
|---|---|
| `"assistant_name": "XYRIS I"` | `config/api_keys.json` (gitignored; value-only edit, file never printed/staged) |

**Why:** makes this machine's real title/log-prefix match the new
identity (boot log will read `SYS: XYRIS I online.`).
**Rollback:** set value back to `AETHER-I`. **Risk:** Med (user setting)
— but reversible and unasserted by tests (verified).

## Group F — eval dataset compatibility (part of D, additive)

`tools/eval_export_datasets.py:54`: `if prefix in ("AETHER-I", "XYRIS I")`.
Old exports + new exports both classify. No dataset file rewritten.

## Group G — external / approval-gated (document only, do NOT execute)

GitHub repo + remote rename to `xyris-i`; `readme` clone URL upstream;
trademark/domain/handle search (pending item, never claimed);
redesign of `jarvis.ico`; fresh-install default name `JARVIS` →
`XYRIS I`; wake word rebrand.

## Group H — storage & security (read-only guarantees)

- Never stage `config/*`, `memory/*`, `tests/eval/datasets/*`,
  `*.log`, `.env*`.
- Never print values from `api_keys.json`; only the `assistant_name`
  key is edited, via script that writes the file back without echoing
  secrets.
- Cert files untouched (names referenced by dashboard TLS).
- Session transcripts untouched; only parsers extended (R2).

---

## Execution order & dependencies

1. **D+F** tests first (expect red — proves asserts see old name)
   → actually: implement A→B→C→D→F in one code pass, then run suite;
   the asserts are updated in the same pass (they encode the *target*
   state).
2. Group E (local config) before boot regression.
3. **Phase 4 regression:** close app → full suite → scorecard →
   relaunch → verify title/tray/status line + boot logs.
4. **Phase 5 audit:** re-scan `aether`/`AETHER-I`/`MARK LIV` repo-wide;
   every remaining hit must map to KEEP/PRESERVE/NEVER-TOUCH in
   inventory — mismatches → fix or record in IMPLEMENTATION_REPORT.
5. **Phase 6 docs:** IMPLEMENTATION_REPORT, VALIDATION_REPORT,
   ROLLBACK_GUIDE with real command output.
6. Commits: (1) docs: identity migration audit+plan, (2) code+tests
   rename, (3) docs rename, (4) reports+validation. Push each.

## Acceptance (spec §10) mapped to evidence sources

| DoD item | Evidence |
|---|---|
| XYRIS I canonical, old names explained | inventory §0 + IMPLEMENTATION_REPORT |
| No blind replace; occurrence table | IDENTITY_INVENTORY + Phase 5 re-scan |
| Compat preserved | inventory §B + suite green incl. old-prefix fixtures |
| Tests pass 100% | VALIDATION_REPORT (1475+ baseline compare) |
| Startup verified w/ new identity | boot log `SYS: XYRIS I online.` + window title `XYRIS I` |
| No personal data altered | `git status` shows only intended files; memory/ never staged |
| Rollback rehearsed | ROLLBACK_GUIDE (revert commits + config value) |
| All 6 docs filled | this folder |
