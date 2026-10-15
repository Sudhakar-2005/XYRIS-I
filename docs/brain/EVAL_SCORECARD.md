# EVAL scorecard — M1–M9

Generated: 2026-10-15T12:23:42+00:00  
Command: `python tools/eval_scorecard.py`  
Session transcripts available for dataset building: 124  
Dataset export (local, gitignored): 959 turns from 122 sessions, 1 memory fact(s) — labels NOT invented by the exporter.  

| Metric | What | Status | Score | Note |
|---|---|---|---|---|
| M1 | Memory retrieval relevance | PASS | 109 passed (15.8s) | Relevance (Recall@5) needs the labeled query set — the anonymized export (tests/eval/datasets/, regenerated locally) carries the raw turns; pairs still need labeling (human/derived step). |
| M2 | Intent accuracy (routing) | PASS | 64 passed (9.7s) | Routing accuracy needs ground-truth labels (user's next accepted action) — raw turns exported, labels are a separate human step. |
| M3 | Planning correctness | PASS | 46 passed (1.2s) | Planner deleted in Phase 4 (M3 waiver: chaining + workflow generator are the only plan producers); generator/validate score. |
| M4 | Context utilization | MEASURED | 0.87% of prompt (104/11889 chars) | Core-block token overhead measured on this box (real memory + core/prompt.txt, char-exact); injection contract covered by the Phase 3 memory tests. The plan's reviewer-preference A/B half still needs human labels — NEEDS-DATA. |
| M5 | Verification accuracy | PASS | 48 passed (4.7s) | Verifier contract + wiring score; per-strategy confusion matrix needs labeled tool outcomes — NEEDS-DATA. |
| M6 | Failure recovery rate | PASS | 53 passed (3.8s) | Reconnect/degraded ladder tests; production recovery rate needs event-bus traces — contract score shown. |
| M7 | Model/provider selection | PASS | 45 passed (3.8s) | Ladder contract score; live provider mix from the RungStats dump — see snapshot below if present. |
| M8 | Automation reliability | PASS | 88 passed (14.1s) | Full automation suite score (runtime/actions/adapters). |
| M9 | Latency & resource usage | PASS | 19 passed (6.6s) | Startup + perf gate pass/fail with measured durations. |

**Totals:** 472 passed, 0 failed across all groups.

## RungStats snapshot (M7 live data — updated 2026-10-15T12:23:21+00:00)

```json
{
  "live": {
    "attempts": 1,
    "ok": 0,
    "fail": 1,
    "avg_latency": 0.0,
    "last_latency": 0.0,
    "cooling": false,
    "last_error": "live down"
  }
}
```
