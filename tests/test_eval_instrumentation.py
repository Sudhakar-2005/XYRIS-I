"""Instrumentation added for the Phase 8 NEEDS-DATA closeout:

* ModelManager's live RungStats dump (M7 provider mix — the scorecard's
  memory/model_stats.json reader finally has a writer).
* The M4 token-overhead measurement (char-exact, real memory + real prompt).
* The anonymized dataset exporter (EVALUATION_PLAN §3 "to add") and its
  secret-scrubbing, plus the Ollama drill's pure percentile math.

Everything here runs offline against tmp paths — no live server, no network,
and the real memory/ stores are never touched.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.model_manager import ModelManager, DUMP_MIN_INTERVAL_S  # noqa: E402


class TestRungStatsDump(unittest.TestCase):
    def _mgr(self, dump: Path) -> ModelManager:
        return ModelManager(ladders={}, dump_path=dump)

    def test_record_dumps_snapshot_with_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "model_stats.json"
            mgr = self._mgr(p)
            reason = mgr.record("gemini-2.5-flash", True, 1.25)
            self.assertEqual(reason, "")          # policy unchanged
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertIn("_meta", data)
            self.assertIn("updated_at", data["_meta"])
            self.assertEqual(data["_meta"]["source"], "ModelManager.record")
            self.assertEqual(data["gemini-2.5-flash"]["ok"], 1)
            self.assertEqual(data["gemini-2.5-flash"]["attempts"], 1)

    def test_dump_is_throttled_within_the_window(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "model_stats.json"
            mgr = self._mgr(p)
            mgr.record("m", True, 0.1)
            # Simulate fresh news landing in the file; a second record
            # inside the throttle window must NOT rewrite it.
            p.write_text('{"marker": "kept"}', encoding="utf-8")
            mgr.record("m", True, 0.2)
            self.assertEqual(p.read_text(encoding="utf-8"),
                             '{"marker": "kept"}')
            # Past the window the dump resumes.
            mgr._last_dump -= DUMP_MIN_INTERVAL_S + 1
            mgr.record("m", True, 0.3)
            self.assertIn("m", json.loads(p.read_text(encoding="utf-8")))

    def test_dump_failure_never_breaks_record(self):
        with tempfile.TemporaryDirectory() as td:
            # dump path whose PARENT directory does not exist -> write fails
            mgr = self._mgr(Path(td) / "no_such_dir" / "model_stats.json")
            self.assertFalse(mgr.dump_stats())
            self.assertEqual(mgr.record("m", True, 0.1), "")
            self.assertEqual(mgr.record("m", False, 0.1, error="503 boom"),
                             "provider")

    def test_quota_and_provider_reasons_survive_the_dump_hook(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = self._mgr(Path(td) / "s.json")
            self.assertEqual(
                mgr.record("m", False, 0.1, error="429 RESOURCE_EXHAUSTED"),
                "quota")


class TestM4Measurement(unittest.TestCase):
    def test_m4_overhead_is_real_and_bounded(self):
        from tools.eval_scorecard import m4_overhead
        m4 = m4_overhead()
        self.assertIsNotNone(m4)
        self.assertGreater(m4["prompt_chars"], 0)
        self.assertGreaterEqual(m4["injected_chars"], 0)
        self.assertGreaterEqual(m4["overhead_pct"], 0.0)
        # The core block is hard-capped at PROMPT_CORE_CHARS (900) + index,
        # so against a real system prompt the overhead cannot be absurd.
        self.assertLess(m4["injected_chars"], 2000)


class TestDatasetExporter(unittest.TestCase):
    def test_scrub_masks_secret_shapes(self):
        from tools.eval_export_datasets import scrub
        cases = [
            "key AIza" + "A" * 35 + " end",
            'Bearer ' + "a1b2c3d4e5f6g7h8i9j0" * 2,
            'api_key = "abcdefghijklmnopqrstuvwxyz012345"',
        ]
        for c in cases:
            self.assertNotEqual(scrub(c), c)
            self.assertIn("[SECRET]", scrub(c))

    def test_role_mapping_and_export_counts(self):
        from tools.eval_export_datasets import export, role_of
        self.assertEqual(role_of("AETHER-I: Hello"), "assistant")
        self.assertEqual(role_of("XYRIS I: Hello"), "assistant")
        self.assertEqual(role_of("User: Hi"), "user")
        self.assertEqual(role_of("weird"), "other")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sess = td / "sessions"
            sess.mkdir()
            (sess / "a.json").write_text(json.dumps({
                "date": "2026-01-01", "language": "English",
                "summary": "", "turn_count": 3,
                "turns": ["AETHER-I: ok", "XYRIS I: welcome back", "User: thanks"],
            }), encoding="utf-8")
            mem = td / "long_term.json"
            mem.write_text(json.dumps(
                {"notes": {"k": {"value": "v", "updated": "2026-01-01"}}}),
                encoding="utf-8")
            out = td / "out"
            counts = export(sess, mem, out)
            self.assertEqual(counts["sessions"], 1)
            self.assertEqual(counts["turns"], 3)
            self.assertEqual(counts["facts"], 1)
            rows = [json.loads(l) for l in
                    (out / "sessions.jsonl").read_text(
                        encoding="utf-8").splitlines()]
            self.assertEqual([r["role"] for r in rows],
                             ["assistant", "assistant", "user"])
            # nothing personal leaks into git: the real out dir is ignored
            self.assertTrue((out / "EXPORT_INFO.json").exists())


class TestDrillMath(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        from tools.ollama_latency_drill import percentile
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(percentile(vals, 50), 3.0)
        self.assertEqual(percentile(vals, 95), 5.0)
        self.assertEqual(percentile([], 50), 0.0)


if __name__ == "__main__":
    unittest.main()
