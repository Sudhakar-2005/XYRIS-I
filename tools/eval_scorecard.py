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
    "M1": "Relevance (Recall@5) needs the labeled query set — the anonymized "
          "export (tests/eval/datasets/, regenerated locally) carries the raw "
          "turns; pairs still need labeling (human/derived step).",
    "M2": "Routing accuracy needs ground-truth labels (user's next accepted "
          "action) — raw turns exported, labels are a separate human step.",
    "M3": "Planner deleted in Phase 4 (M3 waiver: chaining + workflow "
          "generator are the only plan producers); generator/validate score.",
    "M4": "Token-overhead labels not yet built — NEEDS-DATA.",
    "M5": "Verifier contract + wiring score; per-strategy confusion matrix "
          "needs labeled tool outcomes — NEEDS-DATA.",
    "M6": "Reconnect/degraded ladder tests; production recovery rate needs "
          "event-bus traces — contract score shown.",
    "M7": "Ladder contract score; live provider mix from the RungStats dump "
          "— see snapshot below if present.",
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
    for mid, (label, files) in GROUPS.items():
        passed, failed, dur = run_group(files)
        total_p += passed
        total_f += failed
        note = NOTES[mid]
        if mid == "M4" and m4:
            status = "MEASURED"
            score = (f"{m4['overhead_pct']}% of prompt "
                     f"({m4['injected_chars']}/{m4['prompt_chars']} chars)")
            note = ("Core-block token overhead measured on this box (real "
                    "memory + core/prompt.txt, char-exact); injection "
                    "contract covered by the Phase 3 memory tests. The "
                    "plan's reviewer-preference A/B half still needs human "
                    "labels — NEEDS-DATA.")
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
