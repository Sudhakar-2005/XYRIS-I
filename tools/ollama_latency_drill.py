"""Phase 7 / M7 — local-model latency echo drill (real numbers, this box).

    python tools/ollama_latency_drill.py [--n 10] [--model NAME]

Sends N short echo prompts to a local Ollama server (streaming off) and
reports wall-clock latency percentiles plus the throughput Ollama itself
reports in the response (eval_count / eval_duration — actual generated-token
timing, not an estimate). Nothing is extrapolated: if the server is
unreachable or the model missing, the script says so and exits 2.

Why it exists: the EVALUATION_PLAN requires "local-model latency measured on
this box before any recommendation" before any rung of the ladder may be
reordered toward a local model. This is that measurement.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://localhost:11434"


def _get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_model(base: str, wanted: str | None) -> str:
    if wanted:
        return wanted
    tags = _get_json(f"{base}/api/tags")
    models = [m.get("name", "") for m in tags.get("models", [])]
    if not models:
        raise RuntimeError("ollama has no models installed")
    return models[0]


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (ceil) — small N, no interpolation."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(pct / 100.0 * len(ordered))
    idx = max(0, min(len(ordered) - 1, max(1, rank) - 1))
    return ordered[idx]


def drill(base: str, model: str, n: int,
          prompt: str = "Say OK and nothing else.") -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        t0 = time.perf_counter()
        try:
            res = _post_json(f"{base}/api/generate", {
                "model": model,
                "prompt": prompt if i == 0 else f"{prompt} (round {i + 1})",
                "stream": False,
                "options": {"num_predict": 16},
            })
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"request {i + 1} failed: {e}") from e
        wall = time.perf_counter() - t0
        eval_count = int(res.get("eval_count") or 0)
        eval_ns = int(res.get("eval_duration") or 0)
        rows.append({
            "wall_s": round(wall, 3),
            "total_s": round((res.get("total_duration") or 0) / 1e9, 3),
            "prompt_tokens": int(res.get("prompt_eval_count") or 0),
            "gen_tokens": eval_count,
            "gen_tps": round(eval_count / (eval_ns / 1e9), 1)
                       if eval_ns > 0 else 0.0,
            "response": str(res.get("response", "")).strip()[:40],
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--model", default=None)
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()

    try:
        model = pick_model(args.base, args.model)
    except Exception as e:
        print(f"DRILL UNAVAILABLE — cannot reach ollama at {args.base}: {e}")
        print("No numbers invented; run this on a box with a local model.")
        return 2

    print(f"ollama echo drill: model={model} n={args.n} base={args.base}")
    try:
        rows = drill(args.base, model, args.n)
    except RuntimeError as e:
        print(f"DRILL FAILED — {e}")
        return 2

    walls = [r["wall_s"] for r in rows]
    tps = [r["gen_tps"] for r in rows if r["gen_tps"] > 0]
    for i, r in enumerate(rows, 1):
        print(f"  #{i:>2}  wall={r['wall_s']:>6.3f}s  "
              f"gen={r['gen_tokens']:>3} tok  {r['gen_tps']:>5.1f} tok/s  "
              f"-> {r['response']!r}")
    print(f"wall latency: p50={percentile(walls, 50):.3f}s  "
          f"p95={percentile(walls, 95):.3f}s  "
          f"mean={statistics.mean(walls):.3f}s")
    if tps:
        print(f"generation throughput: mean={statistics.mean(tps):.1f} tok/s "
              f"(Ollama's own eval_count/eval_duration)")
    print(json.dumps({"model": model, "n": len(rows),
                      "wall_p50_s": percentile(walls, 50),
                      "wall_p95_s": percentile(walls, 95),
                      "wall_mean_s": round(statistics.mean(walls), 3),
                      "gen_tps_mean": round(statistics.mean(tps), 1)
                                      if tps else None}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
