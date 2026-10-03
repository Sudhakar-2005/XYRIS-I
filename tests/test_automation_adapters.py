"""Phase 7 — adapters between the live systems and the automation runtime."""
from __future__ import annotations

import asyncio
import threading
import time
import unittest
from types import SimpleNamespace

from core.automation.adapters import (
    LiveLedger, build_ai_call, build_confirm, build_memory_recall,
    build_memory_save, build_notify, build_tool_call, looks_like_failure,
)
from core import confirm as confirm_gate


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(response={"result": text})


class TestFailureMarkers(unittest.TestCase):

    def test_markers_and_empty(self):
        for t in ("Tool 'x' failed: boom", "Action 'y' is not available.",
                  "Tool 'z' is currently disabled.",
                  "Tool 'x' timed out after 30s.", "Unknown tool: nope",
                  "", "   "):
            self.assertTrue(looks_like_failure(t), t)
        self.assertFalse(looks_like_failure("Done: searched 5 results."))


class _Pol:
    def __init__(self, risk="safe", perms=(), confirm=False):
        self.risk = risk
        self.permissions = tuple(perms)
        self.requires_confirmation = confirm
        self.timeout = 30


class TestToolCall(unittest.TestCase):

    def _run(self, coro):
        return asyncio.run(coro)

    def test_success_dispatches(self):
        calls = []

        async def dispatch(fc, policy):
            calls.append((fc.name, fc.args))
            return _resp("Done: 3 emails")

        tc = build_tool_call(dispatch=dispatch,
                             policy_of=lambda n: _Pol(perms=("network",)),
                             ledger=LiveLedger(lambda: SimpleNamespace(
                                 is_granted=lambda p: True)))
        out = self._run(tc("gmail", {"action": "list"}, 30))
        self.assertTrue(out["ok"])
        self.assertEqual(out["text"], "Done: 3 emails")
        self.assertEqual(calls, [("gmail", {"action": "list"})])

    def test_forbidden_refused_without_dispatch(self):
        async def dispatch(fc, policy):
            raise AssertionError("must not dispatch")

        tc = build_tool_call(dispatch=dispatch,
                             policy_of=lambda n: _Pol(risk="forbidden"))
        out = self._run(tc("shutdown_jarvis", {}, 30))
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("refused"))
        self.assertIn("forbidden", out["text"])

    def test_ungranted_permission_refused(self):
        async def dispatch(fc, policy):
            raise AssertionError("must not dispatch")

        tc = build_tool_call(dispatch=dispatch,
                             policy_of=lambda n: _Pol(perms=("messaging",)),
                             ledger=LiveLedger(lambda: SimpleNamespace(
                                 is_granted=lambda p: False)))
        out = self._run(tc("send_message", {}, 30))
        self.assertFalse(out["ok"])
        self.assertIn("messaging", out["text"])

    def test_confirmation_declined_refused(self):
        async def dispatch(fc, policy):
            raise AssertionError("must not dispatch")

        async def deny(title, detail):
            return False

        tc = build_tool_call(dispatch=dispatch,
                             policy_of=lambda n: _Pol(confirm=True),
                             confirm=deny)
        out = self._run(tc("send_message", {}, 30))
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("refused"))
        self.assertIn("not confirmed", out["text"])

    def test_confirmation_approved_dispatches(self):
        async def dispatch(fc, policy):
            return _resp("Message sent.")

        async def allow(title, detail):
            return True

        tc = build_tool_call(dispatch=dispatch,
                             policy_of=lambda n: _Pol(confirm=True),
                             confirm=allow)
        out = self._run(tc("send_message", {}, 30))
        self.assertTrue(out["ok"])

    def test_existing_pending_banner_refused(self):
        async def dispatch(fc, policy):
            raise AssertionError("must not dispatch")

        async def allow(title, detail):
            return True

        tc = build_tool_call(dispatch=dispatch,
                             policy_of=lambda n: _Pol(confirm=True),
                             confirm=allow, pending=lambda: "Other thing")
        out = self._run(tc("send_message", {}, 30))
        self.assertFalse(out["ok"])
        self.assertIn("already waiting", out["text"])

    def test_failure_shaped_text_is_not_success(self):
        async def dispatch(fc, policy):
            return _resp("Tool 'web_search' failed: quota")

        tc = build_tool_call(dispatch=dispatch, policy_of=lambda n: _Pol())
        out = self._run(tc("web_search", {"query": "x"}, 30))
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "error")

    def test_dispatch_exception_is_failure(self):
        async def dispatch(fc, policy):
            raise RuntimeError("handler exploded")

        tc = build_tool_call(dispatch=dispatch, policy_of=lambda n: _Pol())
        out = self._run(tc("x", {}, 30))
        self.assertFalse(out["ok"])
        self.assertIn("handler exploded", out["text"])


class TestConfirmGateIntegration(unittest.TestCase):
    """build_confirm against the REAL core/confirm module."""

    def setUp(self):
        self.shown = []
        confirm_gate.bind(show=lambda t, d: self.shown.append((t, d)),
                          hide=lambda: None, log=lambda m: None)
        confirm_gate.resolve(False)          # clear any leftovers

    def tearDown(self):
        confirm_gate.resolve(False)

    def test_human_approves(self):
        confirm = build_confirm(request=confirm_gate.request,
                                pending=confirm_gate.pending_title)

        async def scenario():
            task = asyncio.ensure_future(confirm("Delete files", "3 files"))
            for _ in range(100):
                if self.shown:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(self.shown)
            confirm_gate.resolve(True)       # human presses CONFIRM
            return await asyncio.wait_for(task, timeout=5)

        self.assertTrue(asyncio.run(scenario()))

    def test_human_declines(self):
        confirm = build_confirm(request=confirm_gate.request,
                                pending=confirm_gate.pending_title)

        async def scenario():
            task = asyncio.ensure_future(confirm("Delete files", ""))
            for _ in range(100):
                if self.shown:
                    break
                await asyncio.sleep(0.01)
            confirm_gate.resolve(False)      # human presses CANCEL
            return await asyncio.wait_for(task, timeout=5)

        self.assertFalse(asyncio.run(scenario()))

    def test_unbound_gate_refuses(self):
        confirm_gate.bind(show=None, hide=None, log=None)
        confirm = build_confirm(request=confirm_gate.request,
                                pending=confirm_gate.pending_title)
        self.assertFalse(asyncio.run(confirm("Anything", "")))
        confirm_gate.bind(show=lambda t, d: None, hide=lambda: None,
                          log=lambda m: None)


class TestMiscAdapters(unittest.TestCase):

    def test_ai_call_formats_and_truncates(self):
        seen = {}

        def fake_text(prompt, tier, timeout_ms=None, default=""):
            seen["prompt"] = prompt
            seen["tier"] = tier
            seen["timeout_ms"] = timeout_ms
            return "x" * 5000

        ai = build_ai_call(text_fn=fake_text)
        out = asyncio.run(ai(operation="summarize", objective="be brief",
                             input_text="some input", max_chars=50,
                             timeout_s=12))
        self.assertEqual(len(out), 50)
        self.assertIn("summarize", seen["prompt"])
        self.assertIn("be brief", seen["prompt"])
        self.assertIn("some input", seen["prompt"])
        self.assertGreaterEqual(seen["timeout_ms"], 12_000)

    def test_memory_and_notify_round_trip(self):
        saved = {}
        save = build_memory_save(lambda k, v, c: saved.update(
            {"k": k, "v": v, "c": c}) or "remembered")
        self.assertEqual(asyncio.run(save("key1", "value1")), "remembered")
        self.assertEqual(saved, {"k": "key1", "v": "value1",
                                 "c": "automation"})

        recall = build_memory_recall(lambda q, limit: f"found for {q}")
        self.assertEqual(asyncio.run(recall("trip")), "found for trip")

        seen = []
        notify = build_notify(seen.append)
        asyncio.run(notify("hello"))
        self.assertEqual(seen, ["hello"])

    def test_live_ledger_reads_through(self):
        granted = {"network": False}
        led = LiveLedger(lambda: SimpleNamespace(
            is_granted=lambda p: granted.get(p, True)))
        self.assertTrue(led.is_granted("filesystem"))
        self.assertFalse(led.is_granted("network"))
        none_led = LiveLedger(lambda: None)
        self.assertTrue(none_led.is_granted("anything"))


if __name__ == "__main__":
    unittest.main()
