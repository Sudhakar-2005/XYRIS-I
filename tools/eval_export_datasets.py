"""Phase 8 — anonymized dataset export (EVALUATION_PLAN §3, "to add").

    python tools/eval_export_datasets.py [--out tests/eval/datasets]

Reads the local session store (memory/sessions/*.json) and long-term memory,
scrubs secret-shaped strings with the exact patterns the security audit
guards source with, and writes JSONL:

  sessions.jsonl   one row per transcript turn
                   {file, date, language, role, text, summary}
  memory_facts.jsonl  one row per stored fact
                   {category, key, value_chars, updated}

HONESTY / PRIVACY RULES
  * Rows carry only what the transcripts actually say — no labels are
    invented here; ground-truth labeling (M1 pairs, M2 double-labels) is a
    separate, human step on top of this export.
  * memory/sessions and memory/long_term.json are gitignored personal data.
    The export lands in tests/eval/datasets/ which is ALSO gitignored: the
    dataset regenerates on each box and never travels to the remote.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Same secret shapes tests/test_security_confirmation_audit.py rejects from
# source — reused verbatim so a dataset can never carry what code may not.
SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"['\"]sk-[A-Za-z0-9_\-]{16,}['\"]"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_\.=]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[=:]\s*['\"]?[A-Za-z0-9\-_\.=]{16,}"),
]
MASK = "[SECRET]"


def scrub(text: str) -> str:
    out = str(text)
    for pat in SECRET_PATTERNS:
        out = pat.sub(MASK, out)
    return out


def role_of(turn: str) -> str:
    prefix = turn.split(":", 1)[0]
    if prefix == "AETHER-I":
        return "assistant"
    if prefix == "User":
        return "user"
    return "other"


def export(sessions_dir: Path, memory_path: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {"sessions": 0, "turns": 0, "facts": 0, "scrubbed": 0}

    sess_out = out_dir / "sessions.jsonl"
    with sess_out.open("w", encoding="utf-8") as fh:
        for path in sorted(sessions_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            counts["sessions"] += 1
            summary = scrub(data.get("summary", "") or "")
            for turn in data.get("turns", []):
                text = scrub(str(turn))
                if text != str(turn):
                    counts["scrubbed"] += 1
                fh.write(json.dumps({
                    "file": path.name,
                    "date": data.get("date", ""),
                    "language": data.get("language", ""),
                    "role": role_of(str(turn)),
                    "text": text,
                    "summary": summary,
                }, ensure_ascii=False) + "\n")
                counts["turns"] += 1

    facts_out = out_dir / "memory_facts.jsonl"
    with facts_out.open("w", encoding="utf-8") as fh:
        if memory_path.exists():
            try:
                mem = json.loads(memory_path.read_text(encoding="utf-8"))
            except Exception:
                mem = {}
            for category, entries in (mem or {}).items():
                if not isinstance(entries, dict):
                    continue
                for key, entry in entries.items():
                    value = ""
                    updated = ""
                    if isinstance(entry, dict):
                        value = str(entry.get("value", ""))
                        updated = str(entry.get("updated", ""))
                    else:
                        value = str(entry)
                    fh.write(json.dumps({
                        "category": category,
                        "key": scrub(str(key)),
                        "value_chars": len(value),
                        "updated": updated,
                    }, ensure_ascii=False) + "\n")
                    counts["facts"] += 1

    (out_dir / "EXPORT_INFO.json").write_text(
        json.dumps({
            "generator": "tools/eval_export_datasets.py",
            "counts": counts,
            "notes": "labels are NOT invented here; personal data stays "
                     "local (tests/eval/datasets is gitignored)",
        }, indent=2) + "\n", encoding="utf-8")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions", default=str(ROOT / "memory" / "sessions"))
    ap.add_argument("--memory", default=str(ROOT / "memory" / "long_term.json"))
    ap.add_argument("--out", default=str(ROOT / "tests" / "eval" / "datasets"))
    args = ap.parse_args()

    counts = export(Path(args.sessions), Path(args.memory), Path(args.out))
    print(f"exported: {counts['turns']} turns from {counts['sessions']} "
          f"sessions, {counts['facts']} memory fact(s), "
          f"{counts['scrubbed']} row(s) scrubbed -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
