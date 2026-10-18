"""M2/M4 review scorer — validates the local label sidecars, reports stats.

The packets themselves stay blank by design (tests regenerate
m2_labels.csv / m4_ab_packet.md every run; the CSV columns are human
ground truth). The review lives in append-only sidecars next to them:

    tests/eval/datasets/m2_review.json       two-pass AI target labels
    tests/eval/datasets/m4_preferences.json  10 A/B picks + decision rule

    python tools/eval_m2_m4_review.py

exit 0 = sidecars present and internally valid (stats recomputed),
exit 1 = a sidecar is missing or invalid — no score invented either way.

What this tool does NOT claim: model-vs-label accuracy. That needs a
live replay of the model's tool choice over the 200 turns
(`model_tool_choice` stays blank until then — no offline invention),
and the two label passes are same-model self-consistency, not human
gold. Both caveats are asserted below, not just printed.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "eval" / "datasets"
OUT = DATA / "m2_m4_review_report.json"

TARGETS = ("tool", "persona", "no-op")
PICKS = ("A", "B", "tie")


def _load(name: str) -> dict | None:
    p = DATA / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def check_m2(doc: dict) -> tuple[dict, list[str]]:
    errs: list[str] = []
    labels = doc.get("labels") or {}
    if len(labels) != 200:
        errs.append(f"labels n={len(labels)} != 200")
    if sorted(int(k) for k in labels) != list(range(200)):
        errs.append("label ids are not exactly 0..199")
    rev = doc.get("review") or {}
    if not str(rev.get("independence", "")).startswith("NOT_INDEPENDENT"):
        errs.append("review.independence must declare NOT_INDEPENDENT "
                    "(same-model passes are not human gold)")
    mtc = str(rev.get("model_tool_choice", ""))
    if not ("live" in mtc and "replay" in mtc):
        errs.append("review.model_tool_choice must state the live-replay gap")
    counts_a: dict[str, int] = {}
    counts_b: dict[str, int] = {}
    agree = 0
    borderline = []
    for k, v in sorted(labels.items(), key=lambda kv: int(kv[0])):
        a, b = v.get("a") or {}, v.get("b")
        if a.get("target") not in TARGETS:
            errs.append(f"{k}: pass-a target invalid: {a.get('target')!r}")
            continue
        if b not in TARGETS:
            errs.append(f"{k}: pass-b target invalid: {b!r}")
            continue
        top = a.get("top", "")
        if (a["target"] != "no-op") != bool(top):
            errs.append(f"{k}: top must be non-empty iff target != no-op")
        if v.get("tool_choice", "") != "":
            errs.append(f"{k}: tool_choice must stay blank (live replay)")
        if v.get("human", None) is not None:
            errs.append(f"{k}: human field must be null until adjudicated")
        if bool(v.get("agree")) != (a["target"] == b):
            errs.append(f"{k}: agree flag inconsistent with passes")
        if "borderline" in a.get("note", ""):
            borderline.append(int(k))
        counts_a[a["target"]] = counts_a.get(a["target"], 0) + 1
        counts_b[b] = counts_b.get(b, 0) + 1
        agree += int(a["target"] == b)
    stats = doc.get("stats") or {}
    if stats.get("n") != 200:
        errs.append("stats.n != 200")
    if stats.get("pass_a_counts") != counts_a:
        errs.append("stats.pass_a_counts stale")
    if stats.get("pass_b_counts") != counts_b:
        errs.append("stats.pass_b_counts stale")
    if stats.get("disagree_ids") != [int(k) for k, v in sorted(
            labels.items(), key=lambda kv: int(kv[0]))
            if (v.get("a") or {}).get("target") != v.get("b")]:
        errs.append("stats.disagree_ids stale")
    if sorted(stats.get("borderline_ids") or []) != sorted(borderline):
        errs.append("stats.borderline_ids stale")
    if stats.get("agreement") != round(agree / 200.0, 4):
        errs.append("stats.agreement stale")
    return {"n": 200, "agreement": round(agree / 200.0, 4),
            "pass_a_counts": counts_a, "pass_b_counts": counts_b,
            "disagree_ids": stats.get("disagree_ids"),
            "borderline_ids": sorted(borderline)}, errs


def check_m4(doc: dict) -> tuple[dict, list[str]]:
    errs: list[str] = []
    prefs = doc.get("preferences") or {}
    if sorted(int(k) for k in prefs) != list(range(1, 11)):
        errs.append("preferences must be exactly pairs 1..10")
    counts = {"A": 0, "B": 0, "tie": 0}
    for k, v in prefs.items():
        if v.get("pick") not in PICKS:
            errs.append(f"pair {k}: pick invalid: {v.get('pick')!r}")
            continue
        counts[v["pick"]] += 1
        if not str(v.get("reason", "")).strip():
            errs.append(f"pair {k}: reason missing")
    stats = doc.get("stats") or {}
    if stats.get("n") != 10:
        errs.append("stats.n != 10")
    for p in PICKS:
        if stats.get(p) != counts[p]:
            errs.append(f"stats.{p} stale")
    rule = doc.get("decision_rule") or {}
    for field in ("rule", "overhead_pct", "within_budget", "result", "action"):
        if field not in rule:
            errs.append(f"decision_rule.{field} missing")
    if rule.get("within_budget") is not True:
        errs.append("decision_rule.within_budget must be verified true "
                    "or the field must honestly reflect the measurement")
    if isinstance(rule.get("overhead_pct"), (int, float)) and \
            rule["overhead_pct"] > 10.0:
        errs.append("overhead exceeds the plan's 10% budget")
    rev = doc.get("review") or {}
    for field in ("injection_content_on_this_machine",
                  "limitation", "method", "independence"):
        if not str(rev.get(field, "")).strip():
            errs.append(f"review.{field} missing or empty")
    return {"n": 10, "counts": counts,
            "overhead_pct": rule.get("overhead_pct"),
            "within_budget": rule.get("within_budget"),
            "result": rule.get("result"),
            "action": rule.get("action")}, errs


def main() -> int:
    m2_doc, m4_doc = _load("m2_review.json"), _load("m4_preferences.json")
    errors: list[str] = []
    summary: dict = {"generated_utc": datetime.now(timezone.utc)
                     .isoformat(timespec="seconds")}

    if m2_doc is None:
        errors.append("m2_review.json missing (local sidecar not created)")
        summary["m2"] = None
    else:
        s, errs = check_m2(m2_doc)
        summary["m2"] = s
        errors += [f"m2: {e}" for e in errs]
    if m4_doc is None:
        errors.append("m4_preferences.json missing (local sidecar not created)")
        summary["m4"] = None
    else:
        s, errs = check_m4(m4_doc)
        summary["m4"] = s
        errors += [f"m4: {e}" for e in errs]

    summary["errors"] = errors
    summary["status"] = "ok" if not errors else "invalid"
    summary["caveats"] = [
        "two AI passes = same-model self-consistency, not independent human gold",
        "model_tool_choice unscored: needs live model replay over the 200 turns",
        "M4 measured on placeholder memory note only; value verdict needs real notes",
    ]
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")

    if errors:
        for e in errors:
            print(f"INVALID: {e}")
        print(f"report: {OUT}")
        return 1
    m2, m4 = summary["m2"], summary["m4"]
    print(f"M2: {m2['n']} turns, pass-a {m2['pass_a_counts']}, "
          f"self-agreement {m2['agreement']:.0%}, "
          f"disagree={m2['disagree_ids']}, borderline={m2['borderline_ids']}")
    print(f"M4: {m4['n']} pairs, picks {m4['counts']}, "
          f"overhead {m4['overhead_pct']}% (budget<=10%) -> {m4['result']}")
    print("caveats: same-model passes (not human gold); model baseline "
          "needs live replay; M4 note is placeholder")
    print(f"report: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
