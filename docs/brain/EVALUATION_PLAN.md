# BRAIN V0.1 — Evaluation Plan (spec §10)

**Rules:** no invented numbers — every baseline is *to be measured*; instrumentation must already exist or be added in the phase that needs it; scoring runs offline against recorded data so no live session is disturbed; secrets never enter datasets (reuse the pattern in `tests/test_security_confirmation_audit.py:145-148`).

## 1. Instruments available today (VERIFIED)

| Instrument | Source | Feeds |
|---|---|---|
| 1,454 pytest tests | `tests/` | regression baseline for every phase |
| Session transcripts | `memory/sessions/*.json` (`memory_manager.py:557`) | M1, M2, M3 datasets (ground truth = user's next accepted action) |
| `_session_log` + summaries | `main.py:795`, `memory_manager.py:500` | M2, M4 context labels |
| Automation run statistics | `store.update_statistics` + `config/automations/**` | M8 reliability |
| Model tiers/stats | `RungStats` (`core/model_manager.py`) - dumped to `memory/model_stats.json` throttled from `record()`, read by `tools/eval_scorecard.py` | M7 provider selection |
| Perf marks | `core/perf.py` used in `main.py` | M9 latency |
| Event bus + `correlation_id` | `core/event_bus.py:28`, `main.py:2819-2828` | M5/M6 tracing |
| Memory search scoring | `memory_manager.py:326-346` | M1 baseline scorer |
| Capability catalog | `core/automation/capability.py` | M3/M8 validation checks |

**Phase 1/8 additions (status 2026-10-13):** dataset export script DONE - `tools/eval_export_datasets.py` (JSONL, secret-scrubbed, local-only: `tests/eval/datasets/` is gitignored because it mirrors personal transcripts); gate denials print structured `[Policy]` lines at the seam and verifier verdicts now ride the TOOL_RESULT event data (`main.py` dispatch), giving M5/M6 a machine-readable outcome stream to trace.

**Residual closeout (status 2026-10-15):** the three metric residuals now have honest homes — **M1** scored with `tools/eval_m1_recall.py`: 247 (query, expected) pairs built by conversation structure from real transcripts (user ask → the real assistant answer it received), REAL `search_memory` vs REAL `MemoryRetriever` → lexical Recall@5 31.2% vs memory_v2 30.4% (−0.8 pts) → **keep lexical** under the ≥+10-point rule; **M5** scored with `tools/eval_m5_matrix.py`: 10 strategies/stub-groups × 50 executed fixtures → real confusion matrices, aggregate false-pass 0.0% / false-fail 0.0% over the six real strategies (ship rule met), stub paths (NONE/DOM/SCREENSHOT/no-rule) recorded 0 passes — the honesty property, executed; **M2/M4** human halves exported by `tools/eval_m2_m4_packets.py` (200-turn double-label CSV + 10-pair A/B preference packet) and reported as **NEEDS-HUMAN** on the scorecard — labels and reviewer preference are ground truth *by definition* of those metrics, so they are prepared to minutes of work, never auto-invented. Test coverage: `tests/test_eval_residual_tools.py` (9 tests + 26 subtests).

## 2. Metric definitions

### M1 — Memory retrieval relevance
- **Measures:** does `search_memory` return the right entry for a query?
- **Method:** build ≥100 (query, expected_key) pairs from real session content (Phase 3); score Recall@5 using `_score` output; compare lexical baseline vs candidate retrievers (e.g., `memory_v2.MemoryRetriever`).
- **Decision rule:** adopt `memory_v2` retrieval only if Recall@5 improves ≥10 points (absolute) with zero added dependencies — otherwise keep lexical.
- **Baseline:** measured at Phase 3 start.

### M2 — Intent accuracy (routing)
- **Measures:** deterministic fast-paths + model tool choice correctness.
- **Method:** label sessions with intended target (tool / persona / no-op); when a router fast-path exists, score its accuracy vs labels; separately score model tool-choice: `function_call` chosen vs the tool a human reviewer would pick (double-label 200 turns).
- **Baseline:** M2-model score captured **before** any router change (Phase 1) so the router must beat the model's own default, not zero.
- **Decision rule:** promote a fast-path only if accuracy ≥ model-default and latency ↓ (M9).

### M3 — Planning correctness
- **Measures:** multi-step decomposition quality.
- **Method:** 30 scripted multi-step tasks (chat): score (a) model chain: did steps execute in valid order with no user-intervened fix; (b) dormant `planner.py` after fixes: `graph.validate()` pass + plan length vs human plan. This is the evidence for Phase 4's fix-or-delete call.
- **Also:** automation generator already self-scores via `validate.py` — track generated-graph first-pass rate weekly from `store` stats (M8 overlaps).

### M4 — Context utilization
- **Measures:** does assembled context actually help; does mid-session memory injection work?
- **Method:** A/B replay: same next-turn prompt built with/without injection → reviewer preference + token cost (M9). Off-by-one token budgets from `PROMPT_*_CHARS` (`memory_manager.py:41-45`).
- **Decision rule:** keep injection if preference wins at ≤10% token overhead.

### M5 — Verification accuracy
- **Measures:** false-pass and false-fail of each strategy.
- **Method:** for each CUSTOM strategy, 50 known-good/known-bad executions (fixtures for screens/API where possible); report confusion matrix. Unverifiable-stub paths must score as "skip", never "pass" (the honesty property, `verifier.py:350,359`).
- **Decision rule:** a strategy ships only with false-fail ≤5% (target, tunable after baseline).

### M6 — Failure recovery rate
- **Measures:** how often failures end in clean recovery vs stuck state.
- **Method:** fault injection in tests (kill connection, quota-cool a rung, corrupt a workflow node, deny a permission); measure: typed failure surfaced, ladder/backoff engaged, no silent success. From transcripts: fraction of `error`-tagged turns that resolve within N turns without user escalation (reviewer-labeled).
- **Special check:** automation node that verifier-failed must be recorded failed (regression test for AUDIT §6 leak 1).

### M7 — Model/provider selection
- **Measures:** tier ladder effectiveness + adapter health.
- **Method:** consume `RungStats`: success rate, p95 latency, quota events per rung; provider swap drills (Phase 7): kill provider → typed unavailability, no silent tool execution; local-model latency measured on this box before any recommendation.
- **Decision rule:** a rung may move up/down a tier only with ≥100 observations.

### M8 — Automation reliability
- **Measures:** workflow completion, approval latency, trigger accuracy.
- **Method:** from `store` statistics: success% per workflow, retry counts, idempotency-cache hits, file-watch false-fire rate (polling diff, `triggers.py:135-156`), schedule drift vs cron expectation (±tick tolerance 5 s, `engine.py:233`).
- **Baseline:** captured from existing stores (nothing to add).

### M9 — Latency & resource usage
- **Measures:** cold boot, time-to-first-response, per-turn latency, memory/CPU.
- **Method:** `core/perf.py` marks already in `main.py`; add marks at `_build_config`, first `function_call`, gate evaluation; resources via `psutil` already imported by UI (`ui.py` usage). Track: import-time of heavy modules (`main.py:52-103`), JSON rewrite cost per `remember`.
- **Baselines:** measured Phase 1; regression threshold = +10% triggers review (initial thresholds provisional until baseline exists).

## 3. Harness shape (Phase 8 deliverable, PROPOSED layout)

```
tests/eval/            # pytest-collectable, deselected in normal runs via marker
  datasets/            # JSONL, anonymized, versioned
  test_m1_memory.py … test_m9_perf.py
tools/eval/run_scorecard.py   # one command → markdown scorecard for roadmap errata
```

- Markers: `@pytest.mark.eval` — excluded from the default 1,454-run command, run explicitly in Phase 8 cadence.
- Datasets regenerate from local session stores; **export filters secrets first**.
- Scorecard is pasted into `IMPLEMENTATION_ROADMAP.md` §Errata — metrics gate phase promotion.

## 4. What we do NOT measure (V0.1)

- User-satisfaction surveys / telemetry (no network exfiltration of usage data — offline scoring only).
- Training-loss or fine-tuning curves (out of scope, gap L).
- Benchmarks that would require new paid dependencies.

## 5. Baseline capture order (execution checklist)

1. Phase 1: M9 perf marks + log-based gate/verdict counters → first scorecard (also M2-model default).
2. Phase 3: M1 dataset + lexical Recall@5; M4 token overhead.
3. Phase 4: M3 planner-vs-chain result → fix-or-delete decision recorded.
4. Phase 5: M5 confusion matrices per strategy.
5. Phase 7: M7 ladder/provider drills incl. local-model latency.
6. Phase 8: full M1–M9 scorecard in CI-lite; thereafter each roadmap phase re-runs its own metrics.
