"""Coverage for the residual-metric tools (scorecard's measured subtools):

* M5 — the fixture confusion-matrix runner (real Verifier executions;
  stubs must never pass).
* M1 — pair materialization from local transcripts + Recall@5 scoring.
* M2/M4 — label and A/B packet exports (structure + counts only; these
  files hold real transcripts and stay local/gitignored).

Everything local: no network, no model calls, real memory stores untouched.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = ROOT / "tests" / "eval" / "datasets"
SESSIONS = DATA / "sessions.jsonl"


def _load(name: str):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestM5MatrixTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("eval_m5_matrix")
        cls.rc = cls.mod.main()
        cls.data = json.loads(
            (ROOT / "tests" / "eval" / "datasets" / "m5_matrix.json")
            .read_text(encoding="utf-8"))

    def test_runs_clean_and_holds_honesty_property(self):
        self.assertEqual(self.rc, 0)
        self.assertEqual(self.data["stub_pass_count"], 0,
                         "unverifiable stubs must never score a pass")

    def test_every_group_has_50_executed_cases(self):
        for name, g in self.data["groups"].items():
            with self.subTest(group=name):
                self.assertEqual(g["n_good"], 25)
                self.assertEqual(g["n_bad"], 25)

    def test_real_strategies_meet_ship_rule(self):
        for name, ok in self.data["per_strategy_false_fail_ok"].items():
            with self.subTest(strategy=name):
                self.assertTrue(ok, f"{name} exceeds false-fail 5% rule")
        self.assertEqual(self.data["aggregate_false_pass_pct"], 0.0)

    def test_known_never_passes_and_good_lands_pass(self):
        for name, g in self.data["groups"].items():
            with self.subTest(group=name):
                self.assertEqual(g["matrix"]["bad"]["PASS"], 0)
        for name in ("FILE_EXISTS", "FILE_CHANGED", "EXIT_CODE",
                     "OUTPUT_PARSE", "CUSTOM", "APP_RUNNING"):
            g = self.data["groups"][name]
            self.assertEqual(g["matrix"]["good"]["PASS"], g["n_good"],
                             f"{name}: every known-good execution must verify")


@unittest.skipUnless(SESSIONS.exists(),
                     "local transcript export not present (gitignored)")
class TestM1RecallTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("eval_m1_recall")

    def test_pairs_come_from_real_content_and_are_sufficient(self):
        pairs = self.mod.build_pairs()
        self.assertGreaterEqual(len(pairs), 100,
                                "plan requires >= 100 (query, expected) pairs")
        for p in pairs[:50]:
            self.assertNotEqual(p["query"].strip(), p["answer"].strip(),
                                "query must not be the entry text itself")
            self.assertTrue(p["answer"].strip())

    def test_measured_run_publishes_decision_not_a_cherry_pick(self):
        rc = self.mod.main()
        self.assertIn(rc, (0, 2))
        if rc == 2:
            self.skipTest("insufficient qualifying pairs — tool withholds score")
        data = json.loads((DATA / "m1_recall.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(data["n_pairs"], 100)
        for key in ("lexical_recall5",):
            self.assertTrue(0.0 <= data[key] <= 100.0)
        self.assertIn(data["decision"],
                      ("keep lexical (default)",
                       "keep lexical (delta below +10 pt rule)",
                       "adopt memory_v2 (>= +10 pts)"))


@unittest.skipUnless(SESSIONS.exists(),
                     "local transcript export not present (gitignored)")
class TestPacketExports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("eval_m2_m4_packets")
        assert cls.mod.main() == 0

    def test_m2_packet_is_200_double_label_turns(self):
        with (DATA / "m2_labels.csv").open(encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 200)
        for col in ("intended_target_a", "intended_target_b",
                    "model_tool_choice"):
            self.assertTrue(all(col in r for r in rows))
            self.assertTrue(all(r[col] == "" for r in rows),
                            f"{col} must stay blank — humans fill labels")

    def test_m4_packet_offers_blind_preference_slots(self):
        md = (DATA / "m4_ab_packet.md").read_text(encoding="utf-8")
        self.assertIn("**Preference (A / B / tie):**", md)
        self.assertEqual(md.count("**Preference"), 10)
        self.assertIn("with injection", md)
        self.assertIn("without injection", md)

    def test_packet_info_states_awaiting_human(self):
        info = json.loads((DATA / "PACKET_INFO.json").read_text(
            encoding="utf-8"))
        self.assertIn("awaiting human labels", info["m2"]["status"])
        self.assertIn("awaiting human preference", info["m4"]["status"])


if __name__ == "__main__":
    unittest.main()
