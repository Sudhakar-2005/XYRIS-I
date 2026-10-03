"""Phase 7 — the model-facing automation action + engine loop wiring."""
from __future__ import annotations

import asyncio
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.automation import (
    Edge, GenerationResult, Node, Trigger, Workflow, WorkflowStatus,
)
from core.automation.engine import AutomationEngine, set_engine
from core.automation.store import AutomationStore
from core.automation.validate import REQUIRES_CLARIFICATION
from core import confirm as confirm_gate
from core.action_loader import discover_actions
from actions.automation import automation_action, TOOL as AUTO_TOOL
from tests.test_automation_schema import make_catalog, make_workflow


class FakeEngine:
    def __init__(self):
        self.catalog = make_catalog()
        self.ledger = None
        self.runtime = SimpleNamespace(active_run_for=lambda _id: None)
        self.saved: list = []
        self.deleted: list = []
        self.started: list = []
        self.set_statuses: list = []
        self._wf = Workflow.from_dict(make_workflow().to_dict())
        self._wf.id = "a1"

    def list(self, status=None):
        return [{"id": "a1", "name": "Alpha", "status": "DRAFT",
                 "trigger": "manual"}]

    def explain(self, wf_id):
        if wf_id != "a1":
            raise KeyError("nope")
        return "Alpha (v1) — DRAFT\nsteps: ..."

    def get(self, wf_id):
        if wf_id != "a1":
            return None
        return self._wf

    def start_run(self, wf_id, *, mode="live", trigger="manual",
                  variables=None):
        if wf_id != "a1":
            raise KeyError("nope")
        self.started.append((wf_id, mode))
        return "run_xyz123"

    def pause(self, wf_id):
        return True, "'Alpha' paused"

    def resume(self, wf_id):
        return True, "'Alpha' active again"

    def stop(self, wf_id):
        return True, "run stopping"

    def delete(self, wf_id):
        self.deleted.append(wf_id)
        return True

    def save(self, wf):
        self.saved.append(wf)
        return wf

    def validate(self, wf):
        return validate_with(wf, self.catalog)

    def set_status(self, wf_id, status):
        self.set_statuses.append((wf_id, status))
        self._wf.status = status
        return self._wf

    def runs(self, wf_id=None, limit=30):
        return [{"run_id": "run_1", "status": "COMPLETED",
                 "trigger": "manual", "duration_ms": 2100}]

    def status_summary(self):
        return {"total": 1, "active": 0, "running": 0,
                "by_status": {"DRAFT": 1}}


def validate_with(wf, catalog):
    from core.automation import validate
    return validate(wf, catalog)


class ActionTestBase(unittest.TestCase):
    def setUp(self):
        self.eng = FakeEngine()
        set_engine(self.eng)
        confirm_gate.bind(show=lambda t, d: None, hide=lambda: None,
                          log=lambda m: None)
        confirm_gate.resolve(False)          # clear leftover banners

    def tearDown(self):
        confirm_gate.resolve(False)
        confirm_gate.bind(show=lambda t, d: None, hide=lambda: None,
                          log=lambda m: None)
        set_engine(None)


class TestActionVerbs(ActionTestBase):

    def test_no_engine_is_honest(self):
        set_engine(None)
        out = automation_action({"action": "list"})
        self.assertIn("not available", out)

    def test_unknown_action_lists_verbs(self):
        out = automation_action({"action": "explode"})
        self.assertIn("Unknown automation action", out)
        self.assertIn("generate", out)

    def test_list(self):
        out = automation_action({"action": "list"})
        self.assertIn("a1", out)
        self.assertIn("Alpha", out)

    def test_explain_requires_id(self):
        out = automation_action({"action": "explain"})
        self.assertIn("Refused", out)
        self.assertIn("needs an automation id", out)

    def test_explain(self):
        out = automation_action({"action": "explain", "id": "a1"})
        self.assertIn("Alpha", out)

    def test_run_starts_background_and_returns_run_id(self):
        out = automation_action({"action": "run", "id": "a1"})
        self.assertIn("run_xyz123", out)
        self.assertEqual(self.eng.started, [("a1", "live")])

    def test_test_verb_is_dry(self):
        out = automation_action({"action": "test", "id": "a1"})
        self.assertEqual(self.eng.started, [("a1", "dry")])
        self.assertIn("dry", out.lower())

    def test_run_unknown_id(self):
        out = automation_action({"action": "run", "id": "zzz"})
        self.assertIn("No such automation", out)

    def test_pause_resume_stop_passthrough(self):
        self.assertIn("paused", automation_action({"action": "pause",
                                                   "id": "a1"}))
        self.assertIn("active again", automation_action({"action": "resume",
                                                         "id": "a1"}))
        self.assertIn("stopping", automation_action({"action": "stop",
                                                     "id": "a1"}))

    def test_status_summary_and_detail(self):
        out = automation_action({"action": "status"})
        self.assertIn("1 total", out)
        detail = automation_action({"action": "status", "id": "a1"})
        self.assertIn("run_1", detail)
        self.assertIn("COMPLETED", detail)

    def test_templates_lists_validated_templates(self):
        out = automation_action({"action": "templates"})
        self.assertIn("daily_briefing", out)
        self.assertIn("reminder_workflow", out)

    def test_create_from_template(self):
        out = automation_action({"action": "create",
                                 "template": "reminder_workflow"})
        self.assertIn("Created", out)
        self.assertIn("validated", out)
        self.assertEqual(len(self.eng.saved), 1)
        self.assertEqual(self.eng.saved[0].metadata.get("template"),
                         "reminder_workflow")

    def test_create_unknown_template_is_honest(self):
        out = automation_action({"action": "create", "template": "nope"})
        self.assertIn("not available", out)
        self.assertEqual(self.eng.saved, [])

    def test_create_invalid_spec_not_saved(self):
        spec = {"name": "Broken", "nodes": [], "edges": []}
        out = automation_action({"action": "create", "spec": spec})
        self.assertIn("invalid", out.lower())
        self.assertEqual(self.eng.saved, [])

    def test_activate(self):
        out = automation_action({"action": "activate", "id": "a1"})
        self.assertIn("ACTIVE", out)
        self.assertEqual(self.eng.set_statuses,
                         [("a1", WorkflowStatus.ACTIVE.value)])

    def test_delete_waits_for_human_approval(self):
        shown = []
        confirm_gate.bind(show=lambda t, d: shown.append(t), hide=lambda: None,
                          log=lambda m: None)
        out = automation_action({"action": "delete", "id": "a1"})
        self.assertEqual(self.eng.deleted, [])          # not yet
        self.assertTrue(shown)
        self.assertIn("Delete", shown[0])
        confirm_gate.resolve(True)                      # human confirms
        for _ in range(50):
            if self.eng.deleted:
                break
            time.sleep(0.02)
        self.assertEqual(self.eng.deleted, ["a1"])

    def test_delete_declined_by_human(self):
        confirm_gate.bind(show=lambda t, d: None, hide=lambda: None,
                          log=lambda m: None)
        out = automation_action({"action": "delete", "id": "a1"})
        confirm_gate.resolve(False)
        time.sleep(0.05)
        self.assertEqual(self.eng.deleted, [])

    def test_delete_without_ui_changes_nothing(self):
        confirm_gate.bind(show=None, hide=None, log=None)
        out = automation_action({"action": "delete", "id": "a1"})
        self.assertIn("nothing was deleted", out)
        self.assertEqual(self.eng.deleted, [])

    def test_delete_unknown_id(self):
        out = automation_action({"action": "delete", "id": "zzz"})
        self.assertIn("No such automation", out)
        self.assertEqual(self.eng.deleted, [])

    def test_generate_clarification_is_relayed_verbatim(self):
        res = GenerationResult(status=REQUIRES_CLARIFICATION,
                               clarification="1. What time should it run?")
        with mock.patch("actions.automation.generate", return_value=res):
            out = automation_action({"action": "generate", "text": "brief me"})
        self.assertIn("NEEDS CLARIFICATION", out)
        self.assertIn("What time", out)

    def test_generate_invalid_reports_errors(self):
        res = GenerationResult(status="INVALID",
                               errors=["node 'n2' needs unknown tool 'x'"])
        with mock.patch("actions.automation.generate", return_value=res):
            out = automation_action({"action": "generate", "text": "brief me"})
        self.assertIn("Could not design", out)
        self.assertIn("unknown tool", out)

    def test_generate_needs_text(self):
        out = automation_action({"action": "generate"})
        self.assertIn("needs text", out)


class TestDiscovery(unittest.TestCase):

    def test_action_loader_finds_automation(self):
        reg = discover_actions(Path(__file__).resolve().parent.parent /
                               "actions",
                               reserved_names=set(),
                               logger=lambda m: None)
        self.assertTrue(reg.has("automation"))
        pol = reg.policy("automation")
        self.assertEqual(pol.timeout, 120)
        self.assertEqual(pol.risk, "caution")
        self.assertFalse(pol.requires_confirmation)
        self.assertTrue(reg.describe("automation"))
        self.assertEqual(AUTO_TOOL["parameters"]["required"], ["action"])


class TestEngineLoop(unittest.TestCase):

    def test_start_run_without_loop_is_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = AutomationEngine(store=AutomationStore(tmp),
                                   catalog=make_catalog())
            wf = Workflow.from_dict(make_workflow().to_dict())
            eng.save(wf)
            with self.assertRaises(RuntimeError):
                eng.start_run(wf.id)

    def test_start_run_unknown_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = AutomationEngine(store=AutomationStore(tmp),
                                   catalog=make_catalog())
            with self.assertRaises(KeyError):
                eng.start_run("missing")

    def test_start_run_schedules_and_completes(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                eng = AutomationEngine(store=AutomationStore(tmp),
                                       catalog=make_catalog())
                wf = Workflow(
                    id="wf_pure", name="Pure",
                    nodes=[Node(id="trg", type="TRIGGER", name="start"),
                           Node(id="end", type="END", name="done")],
                    edges=[Edge("trg", "end")], trigger=Trigger())
                eng.save(wf)
                eng.loop = asyncio.get_running_loop()
                rid = eng.start_run(wf.id, mode="dry")
                self.assertTrue(rid.startswith("run_"))
                for _ in range(100):
                    runs = eng.runs(wf.id)
                    if runs and runs[0].get("finished_at"):
                        break
                    await asyncio.sleep(0.02)
                runs = eng.runs(wf.id)
                self.assertTrue(runs, "run was not recorded")
                self.assertEqual(runs[0]["run_id"], rid)
                self.assertEqual(runs[0]["status"], "COMPLETED")
        asyncio.run(scenario())

    def test_start_run_invalid_workflow_fails_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = AutomationEngine(store=AutomationStore(tmp),
                                   catalog=make_catalog())
            bad = Workflow(id="wf_bad", name="Bad", nodes=[], edges=[],
                           trigger=Trigger())
            eng.save(bad)
            with self.assertRaises(ValueError):
                eng.start_run(bad.id)


class TestRealEngineEndToEnd(ActionTestBase):
    """Real engine + real action handler, no LLM and no UI."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.eng = AutomationEngine(store=AutomationStore(self.tmp.name),
                                    catalog=make_catalog())
        set_engine(self.eng)

    def tearDown(self):
        super().tearDown()
        self.tmp.cleanup()

    def test_create_list_status_run_cycle(self):
        out = automation_action({"action": "list"})
        self.assertIn("No automations yet", out)

        out = automation_action({"action": "create",
                                 "template": "reminder_workflow"})
        self.assertIn("Created", out)
        m = re.search(r"\((wf_[A-Za-z0-9_]+)\)", out)
        self.assertIsNotNone(m, out)
        wf_id = m.group(1)

        out = automation_action({"action": "list"})
        self.assertIn(wf_id, out)

        out = automation_action({"action": "status", "id": wf_id})
        self.assertIn("Reminder", out)

        out = automation_action({"action": "explain", "id": wf_id})
        self.assertIn("schedule", out)

        out = automation_action({"action": "run", "id": wf_id})
        # no engine loop in this test — the refusal must be explicit
        self.assertIn("loop is not running", out)


if __name__ == "__main__":
    unittest.main()
