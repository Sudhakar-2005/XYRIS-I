"""XYRIS I V3 — Phase 3 ModelManager / LiveModelManager tests.

Two halves: pure unit tests of core/model_manager.py (ladder order, stats,
cooldown, session slots) and fake-provider tests that drive core/gemini.py's
`call()` through its ladder without touching the network — the same
fake-provider pattern the visual engine tests proved. Plus source-level
guards that the model strings really are defined in exactly one file.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import model_manager as mm
from core.model_manager import (
    ModelManager, LiveModelManager,
    FAST, SMART, SEARCH, LIVE, LIVE_MODEL,
    LADDERS, COOLDOWN_SECONDS,
    MODEL_MANAGER, LIVE_MANAGER,
)
from core import gemini

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_STRING_FILES = ("main.py", "core/gemini.py", "core/model_manager.py")


class TestSingleSourceOfTruth(unittest.TestCase):
    """The audit gap: model names written in ≥2 files. They may exist in
    exactly one — core/model_manager.py."""

    def _quoted_model_literals(self, rel: str) -> list[str]:
        src = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        return [lit for lit in (
            '"models/gemini', '"gemini-2.5', '"gemini-flash',
            "'models/gemini", "'gemini-2.5", "'gemini-flash",
        ) if lit in src]

    def test_no_model_literals_outside_model_manager(self):
        for rel in ("main.py", "core/gemini.py"):
            self.assertEqual(self._quoted_model_literals(rel), [],
                             f"model string literal leaked into {rel}")

    def test_live_model_literal_defined_exactly_once(self):
        literal = '"models/gemini-3.1-flash-live-preview"'
        total = 0
        where = []
        for rel in MODEL_STRING_FILES:
            n = (PROJECT_ROOT / rel).read_text(encoding="utf-8").count(literal)
            if n:
                where.append((rel, n))
            total += n
        self.assertEqual(total, 1, f"literal defined {total} times: {where}")
        self.assertEqual(where[0][0], "core/model_manager.py")

    def test_main_and_gemini_share_the_same_string(self):
        # importing main is heavy but proves the real wiring end to end
        import main
        self.assertEqual(main.LIVE_MODEL, LIVE_MODEL)
        self.assertEqual(gemini._live_model(), LIVE_MODEL)

    def test_tier_constants_reexported_from_gemini(self):
        for name in ("FAST", "SMART", "SEARCH", "LIVE"):
            self.assertEqual(getattr(gemini, name), getattr(mm, name), name)


class TestLadderOrder(unittest.TestCase):
    """The measured order (gemini.py module docstring) is the contract."""

    def setUp(self):
        self.mgr = ModelManager()

    def test_measured_orders(self):
        self.assertEqual(self.mgr.ladder(FAST),
                         (LIVE, "gemini-2.5-flash-lite", "gemini-2.5-flash"))
        self.assertEqual(self.mgr.ladder(SMART),
                         (LIVE, "gemini-2.5-flash", "gemini-2.5-flash-lite"))
        self.assertEqual(self.mgr.ladder(SEARCH),
                         ("gemini-2.5-flash", "gemini-flash-latest",
                          "gemini-2.5-flash-lite"))

    def test_order_matches_ladder_when_nothing_cooling(self):
        for tier in (FAST, SMART, SEARCH):
            self.assertEqual(self.mgr.order(tier), self.mgr.ladder(tier), tier)

    def test_order_demotes_cooling_rung_to_the_back(self):
        self.mgr.cool("gemini-2.5-flash-lite")
        self.assertEqual(self.mgr.order(FAST),
                         (LIVE, "gemini-2.5-flash", "gemini-2.5-flash-lite"))

    def test_order_keeps_every_run_when_all_cooling(self):
        for m in self.mgr.ladder(FAST):
            self.mgr.cool(m)
        self.assertEqual(self.mgr.order(FAST), self.mgr.ladder(FAST))

    def test_unknown_tier_leads_with_the_explicit_model(self):
        ladder = self.mgr.ladder("user-picked-model")
        self.assertEqual(ladder[0], "user-picked-model")
        self.assertEqual(ladder[1:], tuple(m for m in self.mgr.ladder(SMART)
                                           if m != "user-picked-model"))

    def test_constructor_copies_the_ladders(self):
        custom = {FAST: (LIVE, "only-this")}
        mgr = ModelManager(ladders=custom)
        custom[FAST] = ("mutated",)
        self.assertEqual(mgr.ladder(FAST), (LIVE, "only-this"))


class TestStatsAndCooldown(unittest.TestCase):
    def setUp(self):
        self.mgr = ModelManager()

    def test_record_success(self):
        self.assertFalse(self.mgr.record("m", True, 0.5))  # not a quota fail
        snap = self.mgr.snapshot()["m"]
        self.assertEqual(snap["attempts"], 1)
        self.assertEqual(snap["ok"], 1)
        self.assertEqual(snap["fail"], 0)
        self.assertEqual(snap["avg_latency"], 0.5)
        self.assertFalse(snap["cooling"])

    def test_record_failure_is_not_quota(self):
        self.assertFalse(self.mgr.record("m", False, 0.2, error="boom"))
        self.assertFalse(self.mgr.is_cooling("m"))
        self.assertEqual(self.mgr.snapshot()["m"]["fail"], 1)
        self.assertEqual(self.mgr.snapshot()["m"]["last_error"], "boom")

    def test_429_record_returns_quota_and_starts_cooldown(self):
        self.assertTrue(self.mgr.record("m", False, 0.2,
                                        error="429 RESOURCE_EXHAUSTED"))
        self.assertTrue(self.mgr.is_cooling("m"))
        self.assertTrue(self.mgr.snapshot()["m"]["cooling"])

    def test_resource_exhausted_alone_is_quota(self):
        self.assertTrue(self.mgr.record("m", False, 0.1,
                                        error="ResourceExhausted: quota"))
        self.assertTrue(self.mgr.is_cooling("m"))

    def test_cooldown_expires(self):
        self.mgr.cool("m", seconds=0.01)
        self.assertTrue(self.mgr.is_cooling("m"))
        import time as _t
        _t.sleep(0.03)
        self.assertFalse(self.mgr.is_cooling("m"))

    def test_avg_latency_spans_successes_only(self):
        self.mgr.record("m", True, 1.0)
        self.mgr.record("m", False, 99.0, error="x")
        self.mgr.record("m", True, 3.0)
        self.assertEqual(self.mgr.snapshot()["m"]["avg_latency"], 2.0)

    def test_snapshot_is_a_detached_copy(self):
        self.mgr.record("m", True, 1.0)
        snap = self.mgr.snapshot()
        snap["m"]["ok"] = 999
        self.assertEqual(self.mgr.snapshot()["m"]["ok"], 1)

    def test_last_error_is_bounded(self):
        self.mgr.record("m", False, 0.1, error="E" * 1000)
        self.assertEqual(len(self.mgr.snapshot()["m"]["last_error"]), 200)

    def test_format_stats_empty_and_populated(self):
        self.assertIn("no attempts", self.mgr.format_stats())
        self.mgr.record("gemini-2.5-flash", True, 0.8)
        out = self.mgr.format_stats()
        self.assertIn("gemini-2.5-flash", out)
        self.assertIn("1/1", out)

    def test_reset_for_tests(self):
        self.mgr.record("m", True, 1.0)
        self.mgr.reset_for_tests()
        self.assertEqual(self.mgr.snapshot(), {})

    def test_singleton_starts_clean(self):
        # the process-wide manager must not be poisoned by other tests
        self.assertEqual(MODEL_MANAGER.snapshot(), {})


class TestLiveSlots(unittest.TestCase):
    def test_model_is_the_shared_string(self):
        self.assertEqual(LiveModelManager().model, LIVE_MODEL)
        self.assertEqual(LIVE_MANAGER.model, LIVE_MODEL)

    def test_slots_are_reentrant_up_to_the_cap(self):
        live = LiveModelManager(slots=2, slot_wait=0.0)
        with live.slot():
            with live.slot():
                pass  # both held and released cleanly

    def test_full_slots_raise_the_rest_fallback_signal(self):
        live = LiveModelManager(slots=1, slot_wait=0.0)
        with live.slot():
            with self.assertRaises(RuntimeError) as ctx:
                with live.slot():
                    pass
            self.assertIn("no free Live slot", str(ctx.exception))

    def test_slot_released_even_when_the_body_raises(self):
        live = LiveModelManager(slots=1, slot_wait=0.0)
        with self.assertRaises(ValueError):
            with live.slot():
                raise ValueError("side call blew up")
        with live.slot():   # would raise if the slot had leaked
            pass


class _FakeModels:
    """Scripted generate_content: model -> reply string or an exception."""

    def __init__(self, script: dict):
        self.script = script
        self.seen: list[str] = []

    def generate_content(self, **kw):
        model = kw["model"]
        self.seen.append(model)
        act = self.script.get(model, "ok")
        if isinstance(act, Exception):
            raise act
        return SimpleNamespace(text=act)


class TestGeminiCallFallback(unittest.TestCase):
    """gemini.call() walking its ladder against a fake provider — no network."""

    def setUp(self):
        self.mgr = ModelManager()
        self.models = _FakeModels({})
        self.live_behaviour: object = Exception("live down")   # non-quota
        self.live_calls = 0

        def fake_client(**kw):
            return SimpleNamespace(models=self.models)

        def fake_live(contents, config, timeout_ms, key):
            self.live_calls += 1
            b = self.live_behaviour
            if isinstance(b, Exception):
                raise b
            return gemini._Reply(b)

        for p in (mock.patch.object(gemini, "MODEL_MANAGER", self.mgr),
                  mock.patch.object(gemini, "api_key",
                                    lambda refresh=False: "test-key"),
                  mock.patch.object(gemini, "client", fake_client),
                  mock.patch.object(gemini, "_live_call", fake_live)):
            p.start()
            self.addCleanup(p.stop)

    def test_live_fails_then_rest_rung_answers(self):
        self.models.script["gemini-2.5-flash-lite"] = "lite says hi"
        resp = gemini.call("hello", tier=FAST)
        self.assertEqual(resp.text, "lite says hi")
        self.assertEqual(self.models.seen, ["gemini-2.5-flash-lite"])
        snap = self.mgr.snapshot()
        self.assertEqual(snap[LIVE]["fail"], 1)
        self.assertEqual(snap[LIVE]["attempts"], 1)
        self.assertEqual(snap["gemini-2.5-flash-lite"]["ok"], 1)
        self.assertNotIn("gemini-2.5-flash", snap)   # third rung never needed

    def test_live_answers_first(self):
        self.live_behaviour = "live says hi"
        resp = gemini.call("hello", tier=SMART)
        self.assertEqual(resp.text, "live says hi")
        self.assertEqual(self.models.seen, [])
        self.assertEqual(self.mgr.snapshot()[LIVE]["ok"], 1)

    def test_quota_on_one_rung_cools_it_and_next_call_skips_it(self):
        self.models.script["gemini-2.5-flash-lite"] = Exception("429 quota")
        self.models.script["gemini-2.5-flash"] = "flash saves the day"
        resp = gemini.call("hello", tier=FAST)
        self.assertEqual(resp.text, "flash saves the day")
        self.assertTrue(self.mgr.is_cooling("gemini-2.5-flash-lite"))

        self.models.seen.clear()
        resp = gemini.call("hello", tier=FAST)
        self.assertEqual(resp.text, "flash saves the day")
        # live retried (never cooled), lite skipped (cooling), flash answered
        self.assertEqual(self.models.seen, ["gemini-2.5-flash"])

    def test_every_rung_fails_returns_none(self):
        self.models.script = {
            "gemini-2.5-flash-lite": Exception("lite down"),
            "gemini-2.5-flash": Exception("flash down"),
        }
        self.assertIsNone(gemini.call("hello", tier=FAST))
        snap = self.mgr.snapshot()
        self.assertEqual(snap[LIVE]["attempts"], 1)
        self.assertEqual(snap["gemini-2.5-flash-lite"]["attempts"], 1)
        self.assertEqual(snap["gemini-2.5-flash"]["attempts"], 1)
        self.assertEqual(snap["gemini-2.5-flash"]["fail"], 1)

    def test_text_and_as_json_degrade_to_their_defaults(self):
        self.models.script = {
            "gemini-2.5-flash-lite": Exception("x"),
            "gemini-2.5-flash": Exception("y"),
        }
        self.assertEqual(gemini.text("hi", tier=FAST), "")
        self.assertEqual(gemini.text("hi", tier=FAST, default="fallback"),
                         "fallback")
        self.assertIsNone(gemini.as_json("hi", tier=FAST))

    def test_as_json_parses_a_rest_reply(self):
        self.models.script["gemini-2.5-flash-lite"] = '```json\n{"a": 1}\n```'
        self.assertEqual(gemini.as_json("hi", tier=FAST), {"a": 1})

    def test_explicit_model_as_tier_still_degrades(self):
        # a user-picked model leads; its outage falls to the SMART ladder
        self.models.script["user-picked-model"] = Exception("outage")
        self.models.script["gemini-2.5-flash"] = "smart answered"
        resp = gemini.call("hi", tier="user-picked-model")
        self.assertEqual(resp.text, "smart answered")
        # live was tried after the explicit rung, before the remaining REST
        # rungs (the unknown tier leads, the SMART ladder sits behind it)
        self.assertEqual(self.live_calls, 1)
        self.assertEqual(self.models.seen, ["user-picked-model",
                                            "gemini-2.5-flash"])

    def test_no_key_short_circuits_without_touching_stats(self):
        with mock.patch.object(gemini, "api_key", lambda refresh=False: ""):
            self.assertIsNone(gemini.call("hi", tier=FAST))
        self.assertEqual(self.mgr.snapshot(), {})

    def test_latency_is_recorded_per_attempt(self):
        self.models.script["gemini-2.5-flash-lite"] = "ok"
        gemini.call("hi", tier=FAST)
        live = self.mgr.snapshot()[LIVE]
        lite = self.mgr.snapshot()["gemini-2.5-flash-lite"]
        self.assertGreaterEqual(live["last_latency"], 0.0)
        self.assertGreaterEqual(lite["last_latency"], 0.0)


class TestPolicyConstants(unittest.TestCase):
    def test_cooldown_is_five_minutes(self):
        self.assertEqual(COOLDOWN_SECONDS, 300)

    def test_ladders_are_the_documented_tuples(self):
        self.assertEqual(LADDERS[FAST], (LIVE, "gemini-2.5-flash-lite",
                                         "gemini-2.5-flash"))
        self.assertEqual(len(LADDERS), 3)


if __name__ == "__main__":
    unittest.main()
