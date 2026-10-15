"""M2 + M4 packet export — makes the human labeling steps fill-in-ready.

    python tools/eval_m2_m4_packets.py

M2 (EVALUATION_PLAN): "label sessions with intended target (tool / persona /
no-op) ... double-label 200 turns".  This exports exactly 200 sampled user
turns with their real assistant context into a two-reviewer CSV — the
scoring itself stays human (the plan defines ground truth as human intent;
auto-guessed labels would be fabricated ground truth).

M4: "A/B replay: same next-turn prompt built with/without injection ->
reviewer preference + token cost".  This renders both variants per sampled
next-turn from the REAL core/prompt.txt and the REAL
format_memory_for_prompt(memory), records char cost per pair, and leaves the
preference field blank for the reviewer.

Outputs (local, gitignored — personal transcripts):
  tests/eval/datasets/m2_labels.csv
  tests/eval/datasets/m4_ab_packet.md
  tests/eval/datasets/PACKET_INFO.json
"""
from __future__ import annotations

import csv
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = ROOT / "tests" / "eval" / "datasets"
SESSIONS = DATA / "sessions.jsonl"
M2_CSV = DATA / "m2_labels.csv"
M4_MD = DATA / "m4_ab_packet.md"
INFO = DATA / "PACKET_INFO.json"

SEED = 20261015
N_M2 = 200
N_M4 = 10
M4_CTX_CHARS = 400


def user_turns() -> list[dict]:
    out, last_asst = [], ""
    rows = []
    for line in SESSIONS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    for i, r in enumerate(rows):
        if r.get("role") == "assistant":
            last_asst = (r.get("text") or "").strip()
            continue
        if r.get("role") == "user":
            text = (r.get("text") or "").replace("User:", "").strip()
            if len(text.split()) >= 3:
                out.append({"text": text, "file": r.get("file", ""),
                            "date": r.get("date", ""),
                            "context": last_asst})
    return out


def write_m2(users: list[dict]) -> int:
    rng = random.Random(SEED)
    picks = rng.sample(users, min(N_M2, len(users)))
    with M2_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow([
            "id", "session_file", "date", "turn_text",
            "context_assistant_prev", "intended_target_a",
            "intended_target_b", "tool_or_persona", "model_tool_choice",
            "reviewer_notes",
        ])
        for i, u in enumerate(picks):
            w.writerow([
                i, u["file"], u["date"], u["text"],
                u["context"][:300], "", "", "", "", "",
            ])
    return len(picks)


def write_m4(users: list[dict]) -> int:
    prompt = (ROOT / "core" / "prompt.txt").read_text(
        encoding="utf-8", errors="replace")
    from memory.memory_manager import format_memory_for_prompt, load_memory
    injected = format_memory_for_prompt(load_memory())
    rng = random.Random(SEED + 1)
    picks = rng.sample(users, min(N_M4, len(users)))
    lines = [
        "# M4 A/B reviewer packet — memory injection preference",
        "",
        "Method (EVALUATION_PLAN M4): same next-turn prompt built with and",
        "without the memory-injection block. Variant A = WITH injection",
        "(what the app actually sends), Variant B = WITHOUT. Reviewer picks",
        "which answer they'd prefer AFTER judging the context — record the",
        "preference per pair below. Token cost is char-exact here (the",
        "plan's budgets are char budgets); no token estimate is invented.",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Core prompt: {len(prompt)} chars | Injection block: "
        f"{len(injected)} chars "
        f"({100.0 * len(injected) / max(1, len(prompt)):.2f}% of prompt)",
        "",
    ]
    for i, u in enumerate(picks):
        lines += [
            f"## Pair {i + 1}",
            "",
            f"**Next turn (context):** {u['text'][:M4_CTX_CHARS]}",
            "",
            f"**Prev assistant:** {u['context'][:200].strip() or '—'}",
            "",
            f"- Variant A (with injection): system prompt = "
            f"{len(prompt) + len(injected)} chars",
            "- **A system block:**",
            "",
            "```",
            injected.strip()[:1200] or "(memory block empty)",
            "```",
            f"- Variant B (without injection): system prompt = "
            f"{len(prompt)} chars (core prompt only)",
            "",
            "**Preference (A / B / tie):** ______",
            "",
            "---",
            "",
        ]
    M4_MD.write_text("\n".join(lines), encoding="utf-8")
    return len(picks)


def main() -> int:
    if not SESSIONS.exists():
        print(f"missing {SESSIONS} — run tools/eval_export_datasets.py first")
        return 2
    users = user_turns()
    n2 = write_m2(users)
    n4 = write_m4(users)
    info = {
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "m2": {
            "file": str(M2_CSV.relative_to(ROOT)),
            "turns": n2,
            "sample": f"seed={SEED}",
            "columns_intended_target_a/b": "fill by two reviewers "
                "(double-label; disagreement = adjudicate)",
            "intended_target_values": "tool | persona | no-op",
            "model_tool_choice": "blank until the M2-model baseline is "
                "captured by replay (needs a live model run over these "
                "turns — no offline invention)",
            "status": "awaiting human labels",
        },
        "m4": {
            "file": str(M4_MD.relative_to(ROOT)),
            "pairs": n4,
            "status": "awaiting human preference (token overhead already "
                      "measured by tools/eval_scorecard.py)",
        },
        "note": "packets regenerate with the same seeds; files are local "
                "only (tests/eval/datasets/ is gitignored: real transcripts)",
    }
    INFO.write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"m2 packet: {M2_CSV} ({n2} turns)")
    print(f"m4 packet: {M4_MD} ({n4} pairs)")
    print(f"info: {INFO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
