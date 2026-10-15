"""M5 — per-strategy verification confusion matrix (real executed fixtures).

    python tools/eval_m5_matrix.py

EVALUATION_PLAN M5: "for each CUSTOM strategy, 50 known-good/known-bad
executions (fixtures for screens/API where possible); report confusion
matrix. Unverifiable-stub paths must score as 'skip', never 'pass'."

This runs the REAL core.verifier.Verifier over controlled fixtures — every
verdict below is an executed result, not an assumption. Output:
tests/eval/datasets/m5_matrix.json (local, regenerable) + stdout table.

Columns: PASS = VERIFIED, FAIL = FAILED, PARTIAL = PARTIAL (abstain on a
half-check), SKIP = UNVERIFIABLE (never a pass — the honesty property).
Known-good should land PASS; known-bad must never land PASS.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.verifier import (  # noqa: E402
    VerificationRule,
    VerificationStatus,
    VerificationStrategy,
    Verifier,
)

OUT = ROOT / "tests" / "eval" / "datasets" / "m5_matrix.json"

COLS = ("PASS", "FAIL", "PARTIAL", "SKIP")
N_GOOD, N_BAD = 25, 25          # 50 executions per strategy, per the plan


def _col(status: VerificationStatus) -> str:
    return {
        VerificationStatus.VERIFIED: "PASS",
        VerificationStatus.FAILED: "FAIL",
        VerificationStatus.PARTIAL: "PARTIAL",
        VerificationStatus.UNVERIFIABLE: "SKIP",
    }[status]


def _run_group(v: Verifier, tool: str, cases: list[tuple[str, dict, object]]) -> dict:
    """cases: (label, args, output) with label 'good'|'bad'; returns matrix."""
    matrix = {"good": {c: 0 for c in COLS}, "bad": {c: 0 for c in COLS}}
    for label, args, output in cases:
        res = v.verify(tool, args, output)
        matrix[label][_col(res.status)] += 1
    good, bad = matrix["good"], matrix["bad"]
    false_pass = bad["PASS"] / max(1, sum(bad.values()))
    false_fail = (sum(good.values()) - good["PASS"]) / max(1, sum(good.values()))
    return {
        "matrix": matrix,
        "n_good": sum(good.values()),
        "n_bad": sum(bad.values()),
        "false_pass_pct": round(100.0 * false_pass, 1),
        "false_fail_pct": round(100.0 * false_fail, 1),
    }


def build_groups(tmp: Path) -> list[tuple[str, Verifier]]:
    """Each entry: (strategy name, Verifier pre-registered for its fixtures)."""
    groups: list[tuple[str, Verifier]] = []

    # FILE_EXISTS — real files vs paths that were never created
    v = Verifier()
    v.register_rule(VerificationRule("fx", VerificationStrategy.FILE_EXISTS))
    cases = []
    for i in range(N_GOOD):
        p = tmp / f"exists_good_{i}.txt"
        p.write_text("x", encoding="utf-8")
        cases.append(("good", {"file_path": str(p)}, None))
    for i in range(N_BAD):
        cases.append(("bad", {"file_path": str(tmp / f"never_created_{i}.txt")}, None))
    groups.append(("FILE_EXISTS", _wrap(v, cases)))

    # FILE_CHANGED — content written vs missing / empty (untouched)
    v = Verifier()
    v.register_rule(VerificationRule("fc", VerificationStrategy.FILE_CHANGED))
    cases = []
    for i in range(N_GOOD):
        p = tmp / f"changed_good_{i}.txt"
        p.write_text("modified content " * 5, encoding="utf-8")
        cases.append(("good", {"file_path": str(p)}, None))
    for i in range(N_BAD):
        if i < 17:
            cases.append(("bad", {"file_path": str(tmp / f"missing_{i}.txt")}, None))
        else:
            p = tmp / f"empty_{i}.txt"
            p.write_bytes(b"")                       # exists but never changed
            cases.append(("bad", {"file_path": str(p)}, None))
    groups.append(("FILE_CHANGED", _wrap(v, cases)))

    # EXIT_CODE — real dicts an executed command would produce
    v = Verifier()
    v.register_rule(VerificationRule("ec", VerificationStrategy.EXIT_CODE))
    cases = []
    for i in range(N_GOOD):
        out = {"exit_code": 0} if i % 2 == 0 else {"returncode": 0}
        cases.append(("good", {}, out))
    for i in range(N_BAD):
        out = {"exit_code": 1 + (i % 7)} if i < 17 else {"returncode": -9}
        cases.append(("bad", {}, out))
    groups.append(("EXIT_CODE", _wrap(v, cases)))

    # OUTPUT_PARSE — indicators on synthetic tool streams
    v = Verifier()
    v.register_rule(VerificationRule(
        "op", VerificationStrategy.OUTPUT_PARSE,
        success_indicators=["successfully", "done", "ok"],
        failure_indicators=["error", "failed"]))
    cases = []
    good_texts = (["task completed successfully"] * 9
                  + ["done: all 5 files written"] * 8
                  + ["ok - deployment finished"] * 8)
    for t in good_texts:
        cases.append(("good", {}, t))
    bad_texts = (["error: permission denied"] * 9
                 + ["failed to connect to host"] * 8
                 + ["nothing to report here"] * 8)   # no indicators -> abstain
    for t in bad_texts:
        cases.append(("bad", {}, t))
    groups.append(("OUTPUT_PARSE", _wrap(v, cases)))

    # CUSTOM — synthetic checker function (the plan's CUSTOM strategy)
    v = Verifier()

    def check(args, output):
        ok = isinstance(output, dict) and output.get("token") == args.get("token")
        return _mk(VerificationStatus.VERIFIED if ok else VerificationStatus.FAILED,
                   "token match" if ok else "token mismatch")

    v.register_rule(VerificationRule("cu", VerificationStrategy.CUSTOM,
                                     check_fn=check))
    cases = []
    for i in range(N_GOOD):
        tok = f"tok{i}"
        cases.append(("good", {"token": tok}, {"token": tok}))
    for i in range(N_BAD):
        tok = f"tok{i}"
        cases.append(("bad", {"token": tok}, {"token": f"tok{1000 + i}"}))
    groups.append(("CUSTOM", _wrap(v, cases)))

    # APP_RUNNING — launch reports (pid/status), the real output shape
    v = Verifier()
    v.register_rule(VerificationRule("ar", VerificationStrategy.APP_RUNNING))
    cases = []
    for i in range(N_GOOD):
        cases.append(("good", {"app_name": f"app{i}.exe"}, {"pid": 4000 + i}))
    for i in range(N_BAD):
        out = {} if i < 13 else {"status": "failed"}
        cases.append(("bad", {"app_name": f"app{i}.exe"}, out))
    groups.append(("APP_RUNNING", _wrap(v, cases)))

    # Honesty group NONE — configured as "no verification": must always SKIP
    v = Verifier()
    v.register_rule(VerificationRule("nn", VerificationStrategy.NONE))
    cases = ([("good", {}, {"pid": 1})] * N_GOOD) + ([("bad", {}, None)] * N_BAD)
    groups.append(("NONE", _wrap(v, cases)))

    # Honesty stubs DOM_CHECK / SCREENSHOT — placeholders must never PASS
    for strat, name in ((VerificationStrategy.DOM_CHECK, "DOM_CHECK"),
                        (VerificationStrategy.SCREENSHOT, "SCREENSHOT")):
        v = Verifier()
        v.register_rule(VerificationRule("st", strat))
        cases = ([("good", {"file_path": "x.png"},
                   {"html": "<div id='ok'>done</div>"})] * N_GOOD
                 + [("bad", {"file_path": "y.png"}, {"html": ""})] * N_BAD)
        groups.append((name, _wrap(v, cases)))

    # Honesty group NO_RULE — unverifiable stub path (verifier.py contract)
    v = Verifier()
    cases = ([("good", {"file_path": "whatever"}, "output")] * N_GOOD
             + [("bad", {"file_path": "whatever"}, None)] * N_BAD)
    groups.append(("NO_RULE", _wrap(v, cases)))

    return groups


class _wrap:
    """Small shim: holds a Verifier plus its fixed case list."""

    def __init__(self, v: Verifier, cases: list):
        self.v = v
        self.tool = next(iter(v._rules), "no_rule_tool")
        self.cases = cases


def _mk(status: VerificationStatus, message: str):
    from core.verifier import VerificationResult
    return VerificationResult(status=status, confidence=0.9, message=message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="m5fix_") as td:
        groups = build_groups(Path(td))
        results = {}
        for name, g in groups:
            results[name] = _run_group(g.v, g.tool, g.cases)
            results[name]["strategy"] = name

    total = {"good": {c: 0 for c in COLS}, "bad": {c: 0 for c in COLS}}
    for r in results.values():
        for k in ("good", "bad"):
            for c in COLS:
                total[k][c] += r["matrix"][k][c]

    stub_names = ("NONE", "DOM_CHECK", "SCREENSHOT", "NO_RULE")
    stub_passes = sum(results[n]["matrix"][k]["PASS"]
                      for n in stub_names for k in ("good", "bad"))
    real_names = [n for n in results if n not in stub_names]
    false_pass = total["bad"]["PASS"] / max(1, sum(total["bad"].values()))
    false_fail_good = 0
    for n in real_names:
        g = results[n]["matrix"]["good"]
        false_fail_good += sum(g.values()) - g["PASS"]
    good_real = sum(sum(results[n]["matrix"]["good"].values()) for n in real_names)

    payload = {
        "generated_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "method": "real Verifier over controlled fixtures "
                  f"({N_GOOD} known-good + {N_BAD} known-bad per strategy)",
        "groups": results,
        "total": total,
        "stub_pass_count": stub_passes,           # must be 0 (honesty property)
        "aggregate_false_pass_pct": round(100.0 * false_pass, 1),
        "aggregate_false_fail_pct": round(
            100.0 * false_fail_good / max(1, good_real), 1),
        "decision_rule": "ship only with false-fail <= 5% per strategy",
        "per_strategy_false_fail_ok": {
            n: results[n]["false_fail_pct"] <= 5.0 for n in real_names},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"M5 matrix written: {OUT}")
    print(f"{'strategy':<12} {'good P/F/PA/S':<16} {'bad P/F/PA/S':<16} "
          f"{'ffail%':>7} {'fpass%':>7}")
    for name, r in results.items():
        g, b = r["matrix"]["good"], r["matrix"]["bad"]
        gp = "/".join(str(g[c]) for c in COLS)
        bp = "/".join(str(b[c]) for c in COLS)
        print(f"{name:<12} {gp:<16} {bp:<16} "
              f"{r['false_fail_pct']:>7} {r['false_pass_pct']:>7}")
    print(f"aggregate: false-pass {payload['aggregate_false_pass_pct']}%  "
          f"false-fail {payload['aggregate_false_fail_pct']}%  "
          f"stub passes (must be 0): {stub_passes}")
    ok = stub_passes == 0
    print(f"honesty property (stubs never pass): {'OK' if ok else 'VIOLATED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
