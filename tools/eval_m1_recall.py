"""M1 — memory retrieval relevance, Recall@5 on real transcript pairs.

    python tools/eval_m1_recall.py

EVALUATION_PLAN M1: "build >=100 (query, expected_key) pairs from real
session content; score Recall@5 using _score output; compare lexical
baseline vs candidate retrievers (memory_v2.MemoryRetriever). Decision:
adopt memory_v2 only if Recall@5 improves >=10 points (absolute) with
zero added dependencies."

Pair construction (no invented labels): for every user turn that is
answered by the next assistant turn in the same session file, the pair is
(query = the real user ask, entry = the real assistant answer it got).
Ground truth comes from the conversation structure itself: that answer is
the right entry for that ask. Corpus and queries are disjoint texts
(assistant vs user rows), so this is not self-match tautology.

Lexical baseline runs the REAL memory_manager.search_memory with its
store path pointed at the materialized corpus; the candidate runs the REAL
core.memory_v2.MemoryRetriever over the same keys/values.

Output: tests/eval/datasets/m1_recall.json (+ m1_store.json, local,
gitignored — built from personal transcripts).
"""
from __future__ import annotations

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
STORE = DATA / "m1_store.json"
OUT = DATA / "m1_recall.json"
LIMIT = 5


def build_pairs(min_q_words: int = 3, min_a_chars: int = 60) -> list[dict]:
    """(query, expected_key, answer) from real session content."""
    by_file: dict[str, list[dict]] = {}
    order: list[str] = []
    for line in SESSIONS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        f = row.get("file", "?")
        if f not in by_file:
            by_file[f] = []
            order.append(f)
        by_file[f].append(row)

    pairs = []
    for f in order:
        rows = by_file[f]
        for i in range(len(rows) - 1):
            u, a = rows[i], rows[i + 1]
            if u.get("role") != "user" or a.get("role") != "assistant":
                continue
            q = (u.get("text") or "").replace("User:", "").strip()
            ans = (a.get("text") or "").strip()
            words = [w for w in q.split() if len(w) > 2]
            if len(words) < min_q_words or len(ans) < min_a_chars:
                continue
            if ans.lower().startswith("aether-i: good morning"):   # generic
                continue
            pairs.append({
                "query": " ".join(q.split())[:300],
                "answer": ans,
                "date": u.get("date", ""),
                "file": f,
            })
    # dedupe identical (query) keeping first; keep all as distractors
    seen, uniq = set(), []
    for p in pairs:
        k = p["query"].lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq


def materialize_store(pairs: list[dict]) -> dict:
    store = {"notes": {}}
    for i, p in enumerate(pairs):
        store["notes"][f"pair_{i:04d}"] = {
            "value": p["answer"], "updated": p["date"] or "2026-01-01"}
    DATA.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(store, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    return store


def lexical_recall5(pairs: list[dict]) -> float:
    """Real search_memory, store redirected at the materialized corpus."""
    from memory import memory_manager as mm
    real_path = mm.MEMORY_PATH
    mm.MEMORY_PATH = STORE
    try:
        hits = 0
        for i, p in enumerate(pairs):
            out = mm.search_memory(p["query"], limit=LIMIT)
            prefix = f"notes/pair {i:04d}:"     # _pretty: underscores -> space
            if prefix in out:
                hits += 1
        return hits / max(1, len(pairs))
    finally:
        mm.MEMORY_PATH = real_path


def v2_recall5(pairs: list[dict], store: dict) -> tuple[float | None, str]:
    """Real memory_v2.MemoryRetriever over the same keys/values."""
    try:
        from core.memory_v2 import MemoryRetriever, WorkingMemory
        wm = WorkingMemory()
        for k, entry in store["notes"].items():
            wm.set(k, str(entry.get("value", "")))
        r = MemoryRetriever(wm, None, None)      # episodic/procedural unused
        hits = 0
        for i, p in enumerate(pairs):
            top = r.retrieve(p["query"], include_working=True,
                             include_episodic=False, include_procedural=False,
                             limit=LIMIT)
            got = [e.content.get("key") for e in top
                   if isinstance(e.content, dict)]
            if f"pair_{i:04d}" in got:
                hits += 1
        return hits / max(1, len(pairs)), ""
    except Exception as e:                        # honest skip, never a number
        return None, f"candidate could not run: {type(e).__name__}: {e}"


def main() -> int:
    if not SESSIONS.exists():
        print(f"missing {SESSIONS} — run tools/eval_export_datasets.py first")
        return 2
    pairs = build_pairs()
    if len(pairs) < 100:
        print(f"only {len(pairs)} qualifying pairs (plan needs >=100) — "
              f"NOT scoring, no partial number published")
        return 2
    store = materialize_store(pairs)
    lex = lexical_recall5(pairs)
    v2, reason = v2_recall5(pairs, store)
    delta = None if v2 is None else round(100.0 * (v2 - lex), 1)
    decision = ("keep lexical (default)"
                if v2 is None else
                ("adopt memory_v2 (>= +10 pts)" if delta >= 10.0
                 else "keep lexical (delta below +10 pt rule)"))
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "n_pairs": len(pairs),
        "n_corpus_entries": len(store["notes"]),
        "limit": LIMIT,
        "lexical_recall5": round(100.0 * lex, 1),
        "memory_v2_recall5": None if v2 is None
                             else round(100.0 * v2, 1),
        "memory_v2_skipped": reason or None,
        "delta_points": delta,
        "decision_rule": "adopt memory_v2 only at >= +10 absolute points, "
                         "zero added dependencies",
        "decision": decision,
        "method": "leave-one-out style pairing from real transcripts "
                  "(user ask -> the real assistant answer it received); "
                  "REAL search_memory / REAL MemoryRetriever",
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"M1 written: {OUT}")
    print(f"pairs={payload['n_pairs']} corpus={payload['n_corpus_entries']} "
          f"lexical Recall@5={payload['lexical_recall5']}%  "
          f"memory_v2="
          f"{payload['memory_v2_recall5'] if payload['memory_v2_recall5'] is not None else 'SKIPPED'}"
          f"{' (' + reason + ')' if reason else ''}  delta={delta}  "
          f"decision: {decision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
