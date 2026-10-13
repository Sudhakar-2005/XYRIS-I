"""BRAIN Phase 2 residuals — the two acceptance items left open on 2026-10-12.

Phase 2 of ``docs/brain/IMPLEMENTATION_ROADMAP.md`` allowed either wiring or
removal for both ``session_memory`` and the ``PLANNING``/``OBSERVING``
states. The wire-both decision was taken: one honest ``SessionMemory`` object
injected by main, real emitters at the two plan producers (dev_agent, the
automation generator) and at the vision window (capture → flush). These tests
lock the wiring so it cannot silently decay back into a promise.

Source guards follow the suite's established honesty pattern: the dispatch
paths are async and model-backed, so the seam is guarded where it lives
rather than through a live model call.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class TestSessionMemory(unittest.TestCase):
    def test_defaults_to_none(self):
        from core.session_memory import SessionMemory

        self.assertIsNone(SessionMemory().last_search)

    def test_set_last_search_roundtrip_and_copy(self):
        from core.session_memory import SessionMemory

        sm = SessionMemory()
        sm.set_last_search(query="weather in Berlin today", response="ok")
        got = sm.last_search
        self.assertEqual(got["query"], "weather in Berlin today")
        self.assertEqual(got["response"], "ok")
        # Consumers get a copy — mutating it must not corrupt the slot.
        got["query"] = "tampered"
        self.assertEqual(sm.last_search["query"], "weather in Berlin today")


class TestSessionMemoryWiring(unittest.TestCase):
    def test_main_passes_real_object_not_none(self):
        src = _read("main.py")
        self.assertNotIn("session_memory=None", src)
        # both dispatch contexts: the action _ctx dict and the plugin call
        self.assertIn('"session_memory": self._session_mem()', src)
        self.assertIn("session_memory=self._session_mem()", src)
        self.assertIn("from core.session_memory", src)
        self.assertIn("= SessionMemory()", src)

    def test_weather_consumer_matches_the_object_api(self):
        src = _read("actions/weather_report.py")
        self.assertIn("session_memory.set_last_search(", src)
        from core.session_memory import SessionMemory

        self.assertTrue(callable(SessionMemory().set_last_search))


class TestPlanningEmitters(unittest.TestCase):
    def test_dev_agent_emits_planning_then_executing(self):
        src = _read("actions/dev_agent.py")
        self.assertIn('_set_state(player, "PLANNING")', src)
        self.assertIn('_set_state(player, "EXECUTING")', src)

    def test_automation_generate_wraps_the_planner_model(self):
        src = _read("actions/automation.py")
        self.assertIn("def automation_action(parameters: dict, player=None)",
                      src)
        self.assertIn('player.set_state("PLANNING")', src)
        self.assertIn('player.set_state("EXECUTING")', src)

    def test_muted_huds_stay_untouched(self):
        src = _read("actions/dev_agent.py")
        self.assertIn('getattr(player, "muted", False)', src)


class TestObservingEmitter(unittest.TestCase):
    def test_capture_and_dispatch_end_cover_the_vision_window(self):
        src = _read("main.py")
        # capture moment (right after the frame is queued) …
        self.assertIn('self.ui.set_state("OBSERVING")', src)
        # … and the dispatch-end choice that keeps OBSERVING while a frame
        # is still waiting for the flush (never when the session is gone).
        self.assertIn('getattr(self, "_pending_vision", None)', src)
        self.assertGreaterEqual(src.count('"OBSERVING"'), 2)

    def test_app_state_docstring_no_longer_claims_missing_emitters(self):
        src = _read("core/app_state.py")
        self.assertNotIn("have no emitter yet", src)


if __name__ == "__main__":
    unittest.main()
