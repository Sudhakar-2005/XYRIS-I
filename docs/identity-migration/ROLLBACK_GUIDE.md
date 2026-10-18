# ROLLBACK_GUIDE — XYRIS I identity migration (STAGE F)

Scope: revert the identity migration on this machine/checkout. The
migration was done as **4 forward commits on `master`** (no history
rewrite), so rollback = `git revert`. Nothing else needs restoring.

## 1. Identify the migration commits

```bash
git log --oneline -6
```
Expected (newest first): stage 4 (reports) → stage 3 (docs+metadata) →
stage 2 (code+tests) → stage 1 (audit/plan docs) → `9d20ac6` (pre-migration
baseline).

## 2. Full rollback (all four commits, newest first)

```bash
git revert --no-edit HEAD~3..HEAD   # or revert each sha individually
$env:QT_QPA_PLATFORM='offscreen'; python -m pytest tests/ -q   # expect 1475 passed
```
`git revert --no-edit <sha>` per commit if the range form is undesired.
After revert: `APP_VERSION` is back to `AETHER-I`, title renders
`AETHER-I - AETHER-I` on this machine (until step 3), and the suite is
green because the branding asserts revert together with the code.

## 3. Local machine setting (NOT in git — revert by hand)

`config/api_keys.json` (gitignored, user setting):
```json
"assistant_name": "XYRIS I"
```
restore to:
```json
"assistant_name": "AETHER-I"
```
Edit the value only; never paste the file's other contents anywhere.
After revert the window title again reads `AETHER-I - AETHER-I`.

## 4. Per-commit (partial) rollback

| If the problem is… | Revert |
|---|---|
| docs wording only | stage-3 commit (docs) — code keeps XYRIS I |
| code strings / title logic | stage-2 commit — tests revert with it, suite stays green |
| reports/planning docs | stage-1/stage-4 commits — no code effect |

Each commit was built to be independently revertable (code+its tests in
one commit; docs separate from code).

## 5. What a rollback does NOT undo (by design)

- User data was **never modified**: `memory/*`, transcripts
  (`AETHER-I:` prefixes still on disk — the migration never rewrote them),
  `tests/eval/datasets/*`, `config/automations/`, certs.
- Compat identifiers were never removed (`AETHER_PERF`, `AETHER_HUD_PAINT`,
  `aether.state`/`aether.hud`, `aether_generated*`) — reverting only
  restores the old branding strings; the kept identifiers are identical
  on both sides.
- External things were never changed: GitHub remote/URL, LICENSE,
  trademark status (all deferred — nothing to roll back).

## 6. Forward fix instead of rollback

A single-string fix (e.g. change one dialog wording) is a normal edit +
commit; the inventory (`docs/identity-migration/IDENTITY_INVENTORY.md`)
maps every occurrence to file:line for targeted fixes, and
`tools/eval_export_datasets.py:54` shows exactly where prefix
compatibility lives (keep the `("AETHER-I", "XYRIS I")` tuple intact —
old transcripts depend on it).

## 7. Verification after any rollback

```bash
$env:QT_QPA_PLATFORM='offscreen'; python -m pytest tests/ -q    # 1475 passed
python tools/eval_scorecard.py                                   # 472 passed, 0 failed
git status                                                        # clean except intended
```
