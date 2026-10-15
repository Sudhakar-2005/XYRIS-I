# EVAL scorecard — M1–M9

Generated: 2026-10-15T14:41:05+00:00  
Command: `python tools/eval_scorecard.py`  
Session transcripts available for dataset building: 126  
Dataset export (local, gitignored): 959 turns from 122 sessions, 1 memory fact(s) — labels NOT invented by the exporter.  

| Metric | What | Status | Score | Note |
|---|---|---|---|---|
| M1 | Memory retrieval relevance | MEASURED | Recall@5: lexical 31.2% vs memory_v2 30.4% (delta -0.8 pts) on 247 real transcript pairs | REAL search_memory vs REAL MemoryRetriever; decision: keep lexical (delta below +10 pt rule) (+ contract tests 109 passed, 5.4s) |
| M2 | Intent accuracy (routing) | NEEDS-HUMAN | contract tests 64 passed (5.5s); 200-turn double-label packet ready | labels are ground truth by design (plan: human intent) — packet at tests/eval/datasets/m2_labels.csv (local, gitignored); model-tool-choice baseline needs a live replay after labels exist |
| M3 | Planning correctness | PASS | 46 passed (1.0s) | Planner deleted in Phase 4 (M3 waiver: chaining + workflow generator are the only plan producers); generator/validate score. |
| M4 | Context utilization | MEASURED | 0.87% of prompt (104/11889 chars) | Core-block token overhead measured on this box (real memory + core/prompt.txt, char-exact); injection contract covered by the Phase 3 memory tests; A/B reviewer packet ready: 10 pairs at tests/eval/datasets/m4_ab_packet.md. |
| M5 | Verification accuracy | MEASURED | false-pass 0.0%, false-fail 0.0% over 25 known-good + 25 known-bad per strategy; stub passes 0 (must be 0) | executed-fixture matrix via tools/eval_m5_matrix.py; ship rule (false-fail<=5%): ALL real strategies pass; wiring tests 48 passed (4.5s) |
| M6 | Failure recovery rate | PASS | 53 passed (3.4s) | Reconnect/degraded ladder tests; production recovery rate needs event-bus traces — contract score shown. |
| M7 | Model/provider selection | PASS | 45 passed (3.0s) | Ladder contract score; live provider mix from the RungStats dump — see snapshot below if present. |
| M8 | Automation reliability | PASS | 88 passed (18.9s) | Full automation suite score (runtime/actions/adapters). |
| M9 | Latency & resource usage | PASS | 19 passed (20.7s) | Startup + perf gate pass/fail with measured durations. |

**Totals:** 472 passed, 0 failed across all groups.

## RungStats snapshot (M7 live data — updated 2026-10-15T14:40:25+00:00)

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
