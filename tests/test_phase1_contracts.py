"""Brain roadmap Phase 1 — contracts: one gate, one verdict, one reader.

Guards the three fixes of this phase:
* core/gates.evaluate_gates is the ONLY rule implementation, applied by
  BOTH execution paths (the automation adapter previously re-implemented
  the rules and had lost the shutdown_jarvis user-turn fail-closed rule);
* structured verifier verdicts (response["verification"]) end automation
  nodes honestly instead of failure-shaped string sniffing;
* the capability catalog reads the real (list-shaped) ActionRegistry so
  required-param validation is no longer inert.
"""
from __future__ import annotations

import asyncio
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.gates import (
    ALLOW, BUSY, CONFIRM, DENY, FORBIDDEN, GATE_UNAVAILABLE, PERMISSION,
    USER_TURN, evaluate_gates, message_for,
)
from core.automation.adapters import build_tool_call
from core.verifier import VerificationResult, VerificationStatus, verdict_dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Pol:
    """Same duck-shape as ToolPolicy / the adapters' fallback namespace."""

    def __init__(self, risk="safe", perms=(), confirm=False):
        self.risk = risk
        self.permissions = tuple(perms)
        self.requires_confirmation = confirm
        self.timeout = 30


class _Ledger:
    def __init__(self, granted=()):
        self._granted = set(granted)

    def is_granted(self, permission):
        return permission in self._granted


class TestEvaluateGates(unittest.TestCase):
    """The rules, in order, with no caller in sight."""

    def test_forbidden_beats_everything(self):
        g = evaluate_gates("x", _Pol(risk="forbidden", perms=("a",),
                                     confirm=True),
                           ledger=_Ledger(), busy=True, heard_user_text=True)
        self.assertEqual((g.decision, g.rule), (DENY, FORBIDDEN))

    def test_permission_denial_collects_denied(self):
        g = evaluate_gates("x", _Pol(perms=("network", "memory")),
                           ledger=_Ledger(granted=("network",)))
        self.assertEqual((g.decision, g.rule), (DENY, PERMISSION))
        self.assertEqual(g.denied, ("memory",))

    def test_no_ledger_means_granted(self):
        g = evaluate_gates("x", _Pol(perms=("network",)), ledger=None)
        self.assertEqual(g.decision, ALLOW)

    def test_shutdown_fails_closed_without_user_input(self):
        g = evaluate_gates("shutdown_jarvis", _Pol(confirm=True),
                           heard_user_text=False)
        self.assertEqual((g.decision, g.rule), (DENY, USER_TURN))

    def test_shutdown_parks_once_the_user_has_spoken(self):
        g = evaluate_gates("shutdown_jarvis", _Pol(confirm=True),
                           heard_user_text=True)
        self.assertEqual((g.decision, g.rule), (CONFIRM, "confirmation"))

    def test_busy_beats_consent(self):
        g = evaluate_gates("send_message", _Pol(confirm=True), busy=True,
                           heard_user_text=True)
        self.assertEqual((g.decision, g.rule), (DENY, BUSY))

    def test_missing_gate_is_refused(self):
        g = evaluate_gates("send_message", _Pol(confirm=True),
                           confirm_available=False)
        self.assertEqual((g.decision, g.rule), (DENY, GATE_UNAVAILABLE))

    def test_plain_tool_allows(self):
        g = evaluate_gates("web_search", _Pol(perms=("network",)),
                           ledger=_Ledger(granted=("network",)))
        self.assertEqual(g.decision, ALLOW)

    def test_user_turn_ranks_before_busy_and_gate_state(self):
        # same refusal regardless of banner state — the fail-closed rule
        # must not depend on an unrelated confirmation being on screen.
        for kwargs in ({"busy": True}, {"confirm_available": False}):
            g = evaluate_gates("shutdown_jarvis", _Pol(confirm=True),
                               heard_user_text=False, **kwargs)
            self.assertEqual((g.decision, g.rule), (DENY, USER_TURN), kwargs)


class TestSharedWording(unittest.TestCase):

    def test_busy_message_serves_both_paths(self):
        m = message_for(BUSY, "send_message")
        self.assertIn("already a confirmation", m)   # chat test assertion
        self.assertIn("already waiting", m)          # automation test assertion

    def test_permission_message_keeps_the_config_hint(self):
        m = message_for(PERMISSION, "x", ("network",))
        self.assertIn("network", m)
        self.assertIn("config/permissions.json", m)

    def test_user_turn_message_says_why(self):
        m = message_for(USER_TURN, "shutdown_jarvis")
        self.assertIn("Refused", m)
        self.assertIn("briefing does not count", m)

    def test_forbidden_message_is_the_shared_sentence(self):
        m = message_for(FORBIDDEN, "doomsday")
        self.assertIn("marked forbidden", m)
        self.assertIn("Nothing was done", m)


class TestAdapterRunsTheSharedGate(unittest.TestCase):
    """The rules now reach the automation path — especially the one it
    used to be missing."""

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    def test_shutdown_refused_without_user_input(self):
        async def dispatch(fc, policy):
            raise AssertionError("must not dispatch")

        tc = build_tool_call(dispatch=dispatch,
                             policy_of=lambda n: _Pol(confirm=True))
        out = self._run(tc("shutdown_jarvis", {}, 30))
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("refused"))
        self.assertIn("requires the user", out["text"])

    def test_shutdown_dispatches_once_the_user_has_spoken(self):
        async def dispatch(fc, policy):
            return SimpleNamespace(response={"result": "shutting down"})

        async def allow(title, detail):
            return True

        tc = build_tool_call(dispatch=dispatch,
                             policy_of=lambda n: _Pol(confirm=True),
                             confirm=allow,
                             heard_user_text=lambda: True)
        out = self._run(tc("shutdown_jarvis", {}, 30))
        self.assertTrue(out["ok"], out)

    def test_forbidden_still_refused_before_dispatch(self):
        async def dispatch(fc, policy):
            raise AssertionError("must not dispatch")

        tc = build_tool_call(dispatch=dispatch,
                             policy_of=lambda n: _Pol(risk="forbidden"))
        out = self._run(tc("web_search", {}, 30))
        self.assertTrue(out.get("refused"))
        self.assertIn("forbidden", out["text"])

    def test_busy_banner_refused_via_the_shared_rule(self):
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


class TestVerdictContract(unittest.TestCase):

    @staticmethod
    def _v(status, msg="m", conf=0.9):
        return VerificationResult(status=status, confidence=conf, message=msg)

    def test_failed_is_the_only_failure(self):
        self.assertFalse(
            verdict_dict(self._v(VerificationStatus.FAILED), "t")["passed"])
        self.assertTrue(
            verdict_dict(self._v(VerificationStatus.VERIFIED), "t")["passed"])
        self.assertTrue(
            verdict_dict(self._v(VerificationStatus.PARTIAL), "t")["passed"])
        self.assertTrue(
            verdict_dict(self._v(VerificationStatus.UNVERIFIABLE),
                         "t")["passed"])

    def test_verdict_shape(self):
        v = verdict_dict(self._v(VerificationStatus.VERIFIED, "up", 0.8),
                         "open_app")
        self.assertEqual(v, {"tool": "open_app", "status": "verified",
                             "passed": True, "confidence": 0.8,
                             "detail": "up"})

    @staticmethod
    def _tool_call(response):
        async def dispatch(fc, policy):
            return SimpleNamespace(response=response)

        tc = build_tool_call(dispatch=dispatch, policy_of=lambda n: _Pol())
        return asyncio.run(tc("open_app", {}, 30))

    def test_failed_verdict_ends_the_node_even_with_pretty_text(self):
        # the honesty leak: prose that never matched a failure marker
        out = self._tool_call({"result": "Opened X.",
                               "verification": {"passed": False,
                                                "status": "failed"}})
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["verification"], "failed")

    def test_passing_verdict_passes_through_to_the_runtime(self):
        out = self._tool_call({"result": "Opened X.",
                               "verification": {"passed": True,
                                                "status": "verified"}})
        self.assertTrue(out["ok"])
        self.assertEqual(out["verification"], "verified")

    def test_no_verdict_keeps_marker_behaviour(self):
        out = self._tool_call({"result": "Tool 'x' failed: boom"})
        self.assertFalse(out["ok"])
        self.assertNotIn("verification", out)


class _ListRegistry:
    """A registry shaped like the real one: _all_records is a LIST."""

    def __init__(self, rec):
        self._all_records = [rec]

    def names(self):
        return [self._all_records[0].name]

    def policy(self, name):
        from core.action_loader import ToolPolicy
        return ToolPolicy(risk="caution")

    def describe(self, name):
        return "control the browser"


class TestCapabilityReadsTheRealShape(unittest.TestCase):

    def test_list_shaped_registry_yields_parameters(self):
        from core.action_loader import ActionRecord
        from core.automation.capability import CapabilityCatalog
        rec = ActionRecord(
            name="browser_control", valid=True,
            parameters={"type": "OBJECT",
                        "properties": {"url": {"type": "STRING"}},
                        "required": ["url"]})
        cat = CapabilityCatalog(action_registry=_ListRegistry(rec))
        cap = cat.get("browser_control")
        self.assertIsNotNone(cap)
        self.assertEqual(cap.required_params, ("url",))
        self.assertIn("url", cap.properties)

    def test_dict_shaped_fakes_still_work(self):
        from core.automation.capability import CapabilityCatalog

        class Rec:
            valid = True
            parameters = {"type": "OBJECT", "required": ["a"]}

        class DictFake:
            _all_records = {"t": Rec()}

            def names(self):
                return ["t"]

            def policy(self, name):
                return _Pol()

            def describe(self, name):
                return "d"

        cat = CapabilityCatalog(action_registry=DictFake())
        self.assertEqual(cat.get("t").required_params, ("a",))

    def test_real_registry_exposes_record(self):
        from core.action_loader import ActionRecord, ActionRegistry
        rec = ActionRecord(name="a", valid=True)
        reg = ActionRegistry({"a": rec}, logger=lambda m: None)
        self.assertIs(reg.record("a"), rec)
        self.assertIsNone(reg.record("missing"))

    def test_missing_required_param_is_rejected_by_validation(self):
        from core.action_loader import ActionRecord, ToolPolicy
        from core.automation import Edge, Node, Trigger, Workflow, validate
        from core.automation.capability import CapabilityCatalog
        rec = ActionRecord(
            name="browser_control", valid=True,
            policy=ToolPolicy(),
            parameters={"type": "OBJECT",
                        "properties": {"url": {"type": "STRING"}},
                        "required": ["url"]})
        cat = CapabilityCatalog(action_registry=_ListRegistry(rec))
        wf = Workflow(id="wf", name="w", nodes=[
            Node(id="trg", type="TRIGGER", name="start"),
            Node(id="t1", type="TOOL", name="go",
                 config={"tool": "browser_control", "args": {}}),
            Node(id="end", type="END", name="done")],
            edges=[Edge("trg", "t1"), Edge("t1", "end")],
            trigger=Trigger())
        report = validate(wf, catalog=cat)
        joined = " ".join(report.errors)
        self.assertTrue(report.errors, report)
        self.assertIn("missing required params", joined)
        self.assertIn("url", joined)


class TestLoggingConfig(unittest.TestCase):

    def test_configure_logging_is_idempotent(self):
        import main as main_mod
        main_mod._configure_logging()
        first = len(logging.getLogger().handlers)
        self.assertGreater(first, 0)
        main_mod._configure_logging()
        self.assertEqual(len(logging.getLogger().handlers), first)


class TestOneImplementation(unittest.TestCase):
    """Acceptance greps for the phase, as tests."""

    def test_both_paths_call_the_shared_gate(self):
        main_src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        ad_src = (PROJECT_ROOT / "core" / "automation" /
                  "adapters.py").read_text(encoding="utf-8")
        self.assertIn("evaluate_gates(", main_src)
        self.assertIn("evaluate_gates(", ad_src)
        gates_src = (PROJECT_ROOT / "core" / "gates.py").read_text(
            encoding="utf-8")
        self.assertEqual(gates_src.count("def evaluate_gates"), 1)

    def test_single_failure_matcher(self):
        hits = []
        for rel in ["main.py", "core", "actions", "plugins", "memory",
                    "dashboard", "ui.py"]:
            base = PROJECT_ROOT / rel
            files = [base] if base.is_file() else base.rglob("*.py")
            for p in files:
                if p.is_file() and "FAILURE_MARKERS = (" in p.read_text(
                        encoding="utf-8", errors="ignore"):
                    hits.append(p.relative_to(PROJECT_ROOT).as_posix())
        self.assertEqual(hits, ["core/automation/adapters.py"])
        self.assertEqual(
            (PROJECT_ROOT / "core" / "automation" / "schema.py")
            .read_text(encoding="utf-8").count("def looks_like_failure"), 0)

    def test_risk_vocabularies_are_one_vocabulary(self):
        from core.action_loader import RISK_LEVELS
        from core.automation.risk import RISK_ORDER
        self.assertEqual(tuple(RISK_LEVELS), tuple(RISK_ORDER))

    def test_both_paths_carry_the_shutdown_fail_closed_rule(self):
        gates_src = (PROJECT_ROOT / "core" / "gates.py").read_text(
            encoding="utf-8")
        self.assertIn('name == "shutdown_jarvis"', gates_src)
        ad_src = (PROJECT_ROOT / "core" / "automation" /
                  "adapters.py").read_text(encoding="utf-8")
        self.assertIn("heard_user_text", ad_src)


if __name__ == "__main__":
    unittest.main()
