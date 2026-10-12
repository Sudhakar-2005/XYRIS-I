"""Brain Phase 8 — one-command M1–M9 scorecard (CI-lite).

    python tools/eval_scorecard.py

Runs the mapped test groups (offscreen Qt), folds in live data where it
exists (session transcripts, RungStats), and writes docs/brain/EVAL_SCORECARD.md.
Metrics without a labeled dataset are reported honestly as NEEDS-DATA with
the dataset the EVALUATION_PLAN calls for — never a fabricated number.
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
    "M1": "Relevance (Recall@5) needs the labeled query set from "
          "memory/sessions/*.json — infra contract score shown; dataset next.",
    "M2": "Routing accuracy needs ground-truth labels (user's next accepted "
          "action) from transcripts — contract coverage shown.",
    "M3": "Planner deleted in Phase 4 (M3 waiver: chaining + workflow "
          "generator are the only plan producers); generator/validate score.",
    "M4": "Token-overhead labels not yet built — NEEDS-DATA.",
    "M5": "Verifier contract + wiring score; per-strategy confusion matrix "
          "needs labeled tool outcomes — NEEDS-DATA.",
    "M6": "Reconnect/degraded ladder tests; production recovery rate needs "
          "event-bus traces — contract score shown.",
    "M7": "Ladder contract score; live provider mix needs a running session "
          "dumping RungStats — see snapshot below if present.",
    "M8": "Full automation suite score (runtime/actions/adapters).",
    "M9": "Startup + perf gate pass/fail with measured durations.",
}


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
    """Live RungStats, if a dashboard dump left one behind."""
    p = ROOT / "memory" / "model_stats.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def main() -> int:
    rows, total_p, total_f = [], 0, 0
    for mid, (label, files) in GROUPS.items():
        passed, failed, dur = run_group(files)
        total_p += passed
        total_f += failed
        if not files:
            status = "NEEDS-DATA"
            score = "—"
        elif failed:
            status = "FAILING"
            score = f"{passed} pass / {failed} fail ({dur})"
        else:
            status = "PASS"
            score = f"{passed} passed ({dur})"
        rows.append((mid, label, status, score, NOTES[mid]))

    lines = [
        "# EVAL scorecard — M1–M9",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}  ",
        f"Command: `python tools/eval_scorecard.py`  ",
        f"Session transcripts available for dataset building: {session_count()}",
        "",
        "| Metric | What | Status | Score | Note |",
        "|---|---|---|---|---|",
    ]
    for mid, label, status, score, note in rows:
        lines.append(f"| {mid} | {label} | {status} | {score} | {note} |")
    snap = rung_snapshot()
    lines += ["", f"**Totals:** {total_p} passed, {total_f} failed "
                  f"across all groups."]
    if snap:
        lines += ["", "## RungStats snapshot (M7 live data)", "",
                  "```json", json.dumps(snap, indent=2), "```"]
    else:
        lines += ["", "_No live RungStats dump found (`memory/model_stats.json`) "
                     "— M7 provider mix pending a live session._"]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"scorecard written: {OUT}  ({total_p} passed, {total_f} failed)")
    return 1 if total_f else 0


if __name__ == "__main__":
    sys.exit(main())
