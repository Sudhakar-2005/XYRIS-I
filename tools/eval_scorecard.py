"""Brain Phase 8 — one-command M1–M9 scorecard (CI-lite).

    python tools/eval_scorecard.py

Runs the mapped test groups (offscreen Qt), folds in live data where it
exists (session transcripts, RungStats), and writes docs/brain/EVAL_SCORECARD.md.

Measured-metric subtools (outputs read from tests/eval/datasets/, local):
    tools/eval_m1_recall.py     M1 Recall@5 on real transcript pairs
    tools/eval_m2_m4_packets.py M2 label packet + M4 A/B preference packet
    tools/eval_m2_m4_review.py  M2/M4 review sidecars (validate + stats)
    tools/eval_m5_matrix.py     M5 per-strategy confusion matrix (fixtures)

Metrics awaiting a human step report NEEDS-HUMAN (labels/preferences are
ground truth by design); metrics with no dataset yet report NEEDS-DATA —
never a fabricated number.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "brain" / "EVAL_SCORECARD.md"

# metric → (label, pytest file list or [] when dataset-gated)
GROUPS: dict[str, tuple[str, list[str]]] = {
    "M1": ("Memory retrieval relevance",
           ["tests/test_memory_system.py", "tests/test_memory_v2.py"]),
    "M2": ("Intent accuracy (routing)",
           ["tests/test_phase1_contracts.py",
            "tests/test_permission_enforcement.py"]),
    "M3": ("Planning correctness",
           ["tests/test_automation_generator.py",
            "tests/test_automation_validate.py"]),
    "M4": ("Context utilization", []),          # token-overhead labels: pending
    "M5": ("Verification accuracy",
           ["tests/test_verifier_wiring.py"]),
    "M6": ("Failure recovery rate",
           ["tests/test_degraded.py", "tests/test_reconnect_resume.py"]),
    "M7": ("Model/provider selection",
           ["tests/test_model_api.py", "tests/test_model_manager.py"]),
    "M8": ("Automation reliability",
           ["tests/test_automation_runtime.py", "tests/test_automation_actions.py",
            "tests/test_automation_adapters.py"]),
    "M9": ("Latency & resource usage",
           ["tests/test_startup_perf.py"]),
}

NOTES = {
    "M1": "Relevance (Recall@5) needs the labeled query set — run "
          "tools/eval_m1_recall.py to materialize it from local transcripts.",
    "M2": "Routing accuracy needs ground-truth labels (user's next accepted "
          "action) — run tools/eval_m2_m4_packets.py to export the 200-turn "
          "double-label packet; scoring is a human step by design.",
    "M3": "Planner deleted in Phase 4 (M3 waiver: chaining + workflow "
          "generator are the only plan producers); generator/validate score.",
    "M4": "Token overhead measured by m4_overhead(); the reviewer-preference "
          "A/B half needs the exported packet (tools/eval_m2_m4_packets.py).",
    "M5": "Verifier contract + wiring score; run tools/eval_m5_matrix.py for "
          "the per-strategy confusion matrix over executed fixtures.",
    "M6": "Reconnect/degraded ladder tests; production recovery rate needs "
          "event-bus traces — contract score shown.",
    "M7": "Ladder contract score; live provider mix from the RungStats dump "
          "— see snapshot below if present.",
    "M8": "Full automation suite score (runtime/actions/adapters).",
    "M9": "Startup + perf gate pass/fail with measured durations.",
}


def _eval_json(name: str) -> dict:
    p = ROOT / "tests" / "eval" / "datasets" / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}



def run_group(files: list[str]) -> tuple[int, int, str]:
    """Run pytest on `files`; returns (passed, failed, duration)."""
    if not files:
        return 0, 0, "0.0s"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    t0 = datetime.now(timezone.utc)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", *files],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    dur = (datetime.now(timezone.utc) - t0).total_seconds()
    out = proc.stdout + proc.stderr
    m = re.search(r"(\d+) passed", out)
    passed = int(m.group(1)) if m else 0
    f = re.search(r"(\d+) failed", out)
    failed = int(f.group(1)) if f else 0
    return passed, failed, f"{dur:.1f}s"


def session_count() -> int:
    d = ROOT / "memory" / "sessions"
    return len(list(d.glob("*.json"))) if d.exists() else 0


def rung_snapshot() -> dict:
    """Live RungStats, if a session dump left one behind."""
    p = ROOT / "memory" / "model_stats.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def m4_overhead() -> dict | None:
    """Token-budget overhead of the memory core block — exact characters.

    Built from the REAL memory file and the REAL system prompt
    (core/prompt.txt), so the number is measured, not modelled: no token
    estimate is invented (the plan's PROMPT_*_CHARS budgets are char
    budgets). Returns None if the memory module cannot be imported, in
    which case M4 honestly falls back to NEEDS-DATA.
    """
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from memory.memory_manager import (format_memory_for_prompt,
                                           load_memory)
        prompt = (ROOT / "core" / "prompt.txt").read_text(
            encoding="utf-8", errors="replace")
        injected = format_memory_for_prompt(load_memory())
        return {
            "prompt_chars": len(prompt),
            "injected_chars": len(injected),
            "overhead_pct": round(100.0 * len(injected)
                                  / max(1, len(prompt)), 2),
        }
    except Exception:
        return None


def export_info() -> dict:
    """Counts from the anonymized dataset export, when it has been run."""
    p = ROOT / "tests" / "eval" / "datasets" / "EXPORT_INFO.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def main() -> int:
    rows, total_p, total_f = [], 0, 0
    m4 = m4_overhead()
    m1 = _eval_json("m1_recall.json")
    m5 = _eval_json("m5_matrix.json")
    packets = _eval_json("PACKET_INFO.json")
    for mid, (label, files) in GROUPS.items():
        passed, failed, dur = run_group(files)
        total_p += passed
        total_f += failed
        note = NOTES[mid]
        if mid == "M1" and m1:
            status = "MEASURED"
            v2 = m1.get("memory_v2_recall5")
            score = (f"Recall@5: lexical {m1['lexical_recall5']}% vs "
                     f"memory_v2 "
                     f"{v2 if v2 is not None else 'skipped'}% "
                     f"(delta {m1.get('delta_points')} pts) on "
                     f"{m1['n_pairs']} real transcript pairs")
            note = (f"REAL search_memory vs REAL MemoryRetriever; decision: "
                    f"{m1.get('decision')} (+ contract tests {passed} "
                    f"passed, {dur})")
        elif mid == "M2" and packets.get("m2", {}).get("turns"):
            n = packets["m2"]["turns"]
            rev = _eval_json("m2_review.json")
            if rev.get("labels"):
                st = rev["stats"]
                status = "REVIEWED"
                score = (f"{st['n']}-turn two-pass target labels, "
                         f"self-agreement {st['agreement']:.0%} "
                         f"{st['pass_a_counts']}; contract tests {passed} "
                         f"passed ({dur})")
                nb = len(st.get("borderline_ids") or [])
                ds = len(st.get("disagree_ids") or [])
                note = ("labels in local sidecar m2_review.json "
                        "(gitignored, packets stay blank); same-model passes "
                        f"— NOT human gold; {ds} disagreements + {nb} borderline "
                        "ids flagged for human adjudication; model_tool_choice "
                        "needs a live replay (never offline-invented); "
                        "validate via tools/eval_m2_m4_review.py")
            else:
                status = "NEEDS-HUMAN"
                score = f"contract tests {passed} passed ({dur}); {n}-turn double-label packet ready"
                note = ("labels are ground truth by design (plan: human intent) "
                        "— packet at tests/eval/datasets/m2_labels.csv "
                        "(local, gitignored); model-tool-choice baseline needs a "
                        "live replay after labels exist")
        elif mid == "M4" and m4:
            status = "MEASURED"
            score = (f"{m4['overhead_pct']}% of prompt "
                     f"({m4['injected_chars']}/{m4['prompt_chars']} chars)")
            pref = ""
            if packets.get("m4", {}).get("pairs"):
                pref = (f"; A/B reviewer packet ready: {packets['m4']['pairs']} "
                        f"pairs at tests/eval/datasets/m4_ab_packet.md")
            m4p = _eval_json("m4_preferences.json")
            if m4p.get("preferences"):
                st = m4p["stats"]
                pref += (f"; review: {st['A']}A/{st['B']}B/{st['tie']} ties -> "
                         f"{m4p['decision_rule']['result'].split(':')[0]} "
                         "(placeholder note only)")
            note = ("Core-block token overhead measured on this box (real "
                    "memory + core/prompt.txt, char-exact); injection "
                    "contract covered by the Phase 3 memory tests"
                    + pref + ".")
        elif mid == "M5" and m5:
            status = "MEASURED"
            score = (f"false-pass {m5['aggregate_false_pass_pct']}%, "
                     f"false-fail {m5['aggregate_false_fail_pct']}% "
                     f"over {m5['method'].split('(')[1].split(')')[0]}; "
                     f"stub passes {m5['stub_pass_count']} (must be 0)")
            ok = m5.get("per_strategy_false_fail_ok", {})
            fail = [k for k, v in ok.items() if not v]
            note = (f"executed-fixture matrix via tools/eval_m5_matrix.py; "
                    f"ship rule (false-fail<=5%): "
                    f"{'ALL real strategies pass' if not fail else 'FAILING: ' + ', '.join(fail)}"
                    f"; wiring tests {passed} passed ({dur})")
        elif mid == "M4":
            status = "NEEDS-DATA" if not m4 else "MEASURED"
            score = "—" if not m4 else (
                f"{m4['overhead_pct']}% "
                f"({m4['injected_chars']}/{m4['prompt_chars']} chars)")
        elif not files:
            status = "NEEDS-DATA"
            score = "—"
        elif failed:
            status = "FAILING"
            score = f"{passed} pass / {failed} fail ({dur})"
        else:
            status = "PASS"
            score = f"{passed} passed ({dur})"
        rows.append((mid, label, status, score, note))

    info = export_info()
    export_line = ""
    if info.get("counts"):
        c = info["counts"]
        export_line = (f"Dataset export (local, gitignored): {c.get('turns', 0)} "
                       f"turns from {c.get('sessions', 0)} sessions, "
                       f"{c.get('facts', 0)} memory fact(s) — labels NOT "
                       f"invented by the exporter.  ")
    lines = [
        "# EVAL scorecard — M1–M9",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}  ",
        f"Command: `python tools/eval_scorecard.py`  ",
        f"Session transcripts available for dataset building: {session_count()}  ",
        export_line,
        "",
        "| Metric | What | Status | Score | Note |",
        "|---|---|---|---|---|",
    ]
    for mid, label, status, score, note in rows:
        lines.append(f"| {mid} | {label} | {status} | {score} | {note} |")
    snap = rung_snapshot()
    meta = snap.pop("_meta", None) if snap else None
    lines += ["", f"**Totals:** {total_p} passed, {total_f} failed "
                  f"across all groups."]
    if snap:
        when = (meta or {}).get("updated_at", "unknown time")
        stale = ""
        try:
            age_h = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(when)).total_seconds() / 3600
            if age_h > 48:
                stale = f" — STALE ({age_h:.0f}h old)"
        except Exception:
            pass
        lines += ["", f"## RungStats snapshot (M7 live data — updated {when}"
                      f"{stale})", "",
                  "```json", json.dumps(snap, indent=2), "```"]
    else:
        lines += ["", "_No live RungStats dump found (`memory/model_stats.json`) "
                         "— M7 provider mix pending a live session._"]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"scorecard written: {OUT}  ({total_p} passed, {total_f} failed)")
    return 1 if total_f else 0


if __name__ == "__main__":
    sys.exit(main())
