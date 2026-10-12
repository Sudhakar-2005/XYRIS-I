# EVAL scorecard — M1–M9

Generated: 2026-10-12T15:41:05+00:00  
Command: `python tools/eval_scorecard.py`  
Session transcripts available for dataset building: 117

| Metric | What | Status | Score | Note |
|---|---|---|---|---|
| M1 | Memory retrieval relevance | PASS | 109 passed (4.4s) | Relevance (Recall@5) needs the labeled query set from memory/sessions/*.json — infra contract score shown; dataset next. |
| M2 | Intent accuracy (routing) | PASS | 64 passed (5.4s) | Routing accuracy needs ground-truth labels (user's next accepted action) from transcripts — contract coverage shown. |
| M3 | Planning correctness | PASS | 46 passed (1.3s) | Planner deleted in Phase 4 (M3 waiver: chaining + workflow generator are the only plan producers); generator/validate score. |
| M4 | Context utilization | NEEDS-DATA | — | Token-overhead labels not yet built — NEEDS-DATA. |
| M5 | Verification accuracy | PASS | 48 passed (5.4s) | Verifier contract + wiring score; per-strategy confusion matrix needs labeled tool outcomes — NEEDS-DATA. |
| M6 | Failure recovery rate | PASS | 53 passed (4.0s) | Reconnect/degraded ladder tests; production recovery rate needs event-bus traces — contract score shown. |
| M7 | Model/provider selection | PASS | 45 passed (3.5s) | Ladder contract score; live provider mix needs a running session dumping RungStats — see snapshot below if present. |
| M8 | Automation reliability | PASS | 88 passed (15.3s) | Full automation suite score (runtime/actions/adapters). |
| M9 | Latency & resource usage | PASS | 19 passed (6.2s) | Startup + perf gate pass/fail with measured durations. |

**Totals:** 472 passed, 0 failed across all groups.

_No live RungStats dump found (`memory/model_stats.json`) — M7 provider mix pending a live session._
