"""Phase 6 — generator + starter templates."""
from __future__ import annotations

import unittest

from core.automation import (
    CapabilityCatalog, GoalModel, Workflow, WorkflowStatus,
    build_templates, generate, get_template, instantiate, parse_intent,
    validate,
)
from core.automation.generator import GenerationError, INVALID, \
    REQUIRES_CLARIFICATION, VALID
from tests.test_automation_schema import make_catalog


GOAL = {
    "objective": "send me a morning briefing",
    "trigger": {"type": "schedule", "config": {"time": "08:00"}},
    "steps": ["search news", "notify me"],
    "required_tools": ["web_search"],
    "variables": {},
    "constraints": [],
    "ambiguities": [],
    "notes": "",
}


def good_draft() -> dict:
    return {
        "name": "Briefing",
        "description": "news briefing",
        "trigger": {"type": "schedule", "config": {"time": "08:00"}},
        "variables": {},
        "nodes": [
            {"id": "n1", "type": "TRIGGER", "name": "start", "config": {}},
            {"id": "n2", "type": "TOOL", "name": "search",
             "config": {"tool": "web_search", "args": {"query": "news"}}},
            {"id": "n3", "type": "NOTIFICATION", "name": "notify",
             "config": {"message": "{$.nodes.n2.message}"}},
            {"id": "n4", "type": "END", "name": "done", "config": {}},
        ],
        "edges": [{"source": "n1", "target": "n2"},
                  {"source": "n2", "target": "n3"},
                  {"source": "n3", "target": "n4"}],
        "clarifications": [],
    }


def unknown_tool_draft() -> dict:
    d = good_draft()
    d["nodes"][1]["config"]["tool"] = "search_engine"
    d["name"] = "Bad draft"
    return d


class QueueAI:
    def __init__(self, *results):
        self.calls: list[str] = []
        self._results = list(results)

    def __call__(self, prompt, timeout_s=60.0):
        self.calls.append(prompt)
        if not self._results:
            raise AssertionError("unexpected extra model call")
        r = self._results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class TestGoalModel(unittest.TestCase):

    def test_parse_intent_ok(self):
        ai = QueueAI(dict(GOAL))
        goal = parse_intent("daily briefing please", ai)
        self.assertIsInstance(goal, GoalModel)
        self.assertEqual(goal.objective, GOAL["objective"])
        self.assertEqual(goal.trigger_type, "schedule")
        self.assertEqual(goal.trigger_config["time"], "08:00")
        self.assertEqual(len(ai.calls), 1)

    def test_parse_intent_rejects_junk(self):
        ai = QueueAI("definitely not json")
        with self.assertRaises(GenerationError):
            parse_intent("x", ai)

    def test_bad_trigger_type_falls_back_to_manual(self):
        ai = QueueAI({**GOAL, "trigger": {"type": "smoke_signal"}})
        goal = parse_intent("x", ai)
        self.assertEqual(goal.trigger_type, "manual")


class TestGenerate(unittest.TestCase):

    def test_valid_draft_passes_gate(self):
        ai = QueueAI(dict(GOAL), good_draft())
        res = generate("morning briefing at 8", make_catalog(), ai)
        self.assertEqual(res.status, VALID)
        self.assertTrue(res.ok)
        self.assertEqual(len(ai.calls), 2)
        self.assertEqual(res.workflow.status, WorkflowStatus.READY.value)
        self.assertTrue(res.report.valid)
        self.assertEqual(res.workflow.metadata.get("source"), "generator")
        self.assertEqual(res.goal.objective, GOAL["objective"])

    def test_goal_ambiguity_clarifies_before_drafting(self):
        ai = QueueAI({**GOAL, "ambiguities": ["What time should it run?"]})
        res = generate("a briefing", make_catalog(), ai)
        self.assertEqual(res.status, REQUIRES_CLARIFICATION)
        self.assertIn("What time", res.clarification)
        self.assertIsNone(res.workflow)
        self.assertEqual(len(ai.calls), 1)      # never reached the draft stage

    def test_draft_clarification_clarifies(self):
        draft = good_draft()
        draft["clarifications"] = ["Which folder should I watch?"]
        ai = QueueAI(dict(GOAL), draft)
        res = generate("organize my stuff", make_catalog(), ai)
        self.assertEqual(res.status, REQUIRES_CLARIFICATION)
        self.assertIn("Which folder", res.clarification)
        self.assertIsNone(res.workflow)

    def test_unknown_tool_repaired_in_one_round(self):
        ai = QueueAI(dict(GOAL), unknown_tool_draft(), good_draft())
        res = generate("briefing", make_catalog(), ai)
        self.assertEqual(res.status, VALID)
        self.assertTrue(res.ok)
        self.assertEqual(len(ai.calls), 3)
        self.assertIn("FAILED VALIDATION", ai.calls[2])
        self.assertIn("search_engine", ai.calls[2])
        self.assertEqual(res.errors, [])        # clean result, no fatal errors

    def test_still_invalid_after_repair_is_honest(self):
        ai = QueueAI(dict(GOAL), unknown_tool_draft(), unknown_tool_draft())
        res = generate("briefing", make_catalog(), ai)
        self.assertEqual(res.status, INVALID)
        self.assertIsNone(res.workflow)
        self.assertTrue(res.errors)
        self.assertIn("search_engine",
                      res.capability_report.get("missing_tools", []))

    def test_model_hard_failure_never_raises(self):
        ai = QueueAI(RuntimeError("engine down"), RuntimeError("still down"))
        res = generate("briefing", make_catalog(), ai)
        self.assertEqual(res.status, INVALID)
        self.assertIsNone(res.workflow)
        self.assertTrue(any("intent parse failed" in e or
                            "model call failed" in e for e in res.errors))

    def test_empty_request_rejected_without_model_call(self):
        ai = QueueAI()
        res = generate("   ", make_catalog(), ai)
        self.assertEqual(res.status, INVALID)
        self.assertEqual(ai.calls, [])

    def test_draft_with_unknown_trigger_type_repaired(self):
        d = good_draft()
        d["trigger"] = {"type": "psychic_vision", "config": {}}
        ai = QueueAI(dict(GOAL), d, good_draft())
        res = generate("briefing", make_catalog(), ai)
        self.assertEqual(res.status, VALID)

    def test_result_to_dict_is_serialisable(self):
        ai = QueueAI(dict(GOAL), good_draft())
        res = generate("briefing", make_catalog(), ai)
        import json
        blob = json.dumps(res.to_dict())
        self.assertIn("VALID", blob)

    def test_json_string_response_accepted(self):
        import json as _json
        ai = QueueAI(_json.dumps(GOAL), _json.dumps(good_draft()))
        res = generate("briefing", make_catalog(), ai)
        self.assertEqual(res.status, VALID)


class TestTemplates(unittest.TestCase):

    KEYS = {"daily_briefing", "email_summarizer", "internship_tracker",
            "file_organizer", "reminder_workflow", "system_monitor",
            "job_alerts"}

    def test_all_seven_templates_present_and_valid(self):
        cat = make_catalog()
        templates = build_templates(cat)
        self.assertEqual({t.key for t in templates}, self.KEYS)
        for t in templates:
            self.assertTrue(t.report.valid, f"{t.key}: {t.report.errors}")
            again = validate(t.workflow, cat)
            self.assertTrue(again.valid, f"{t.key}: {again.errors}")
            self.assertEqual(t.workflow.metadata.get("template"), t.key)

    def test_templates_skip_missing_capability(self):
        cat = make_catalog()
        del cat._caps["gmail"]
        keys = {t.key for t in build_templates(cat)}
        self.assertNotIn("email_summarizer", keys)
        self.assertNotIn("internship_tracker", keys)
        self.assertIn("reminder_workflow", keys)   # needs no tools
        self.assertEqual(len(keys), 5)

    def test_get_template_by_key(self):
        cat = make_catalog()
        t = get_template("job_alerts", cat)
        self.assertIsNotNone(t)
        self.assertIsNone(get_template("nope", cat))

    def test_instantiate_mints_fresh_ids_and_validates(self):
        cat = make_catalog()
        t = get_template("daily_briefing", cat)
        wf1 = instantiate(t, cat)
        wf2 = instantiate(t, cat)
        self.assertNotEqual(wf1.id, t.workflow.id)
        self.assertNotEqual(wf1.id, wf2.id)
        self.assertEqual(wf1.status, WorkflowStatus.DRAFT.value)
        self.assertNotEqual(wf1.nodes[0].id, t.workflow.nodes[0].id)
        self.assertTrue(validate(wf1, cat).valid,
                        validate(wf1, cat).errors)
        ids = [n.id for n in wf1.nodes]
        self.assertEqual(len(ids), len(set(ids)))

    def test_unavailable_capability_excluded(self):
        from dataclasses import replace
        cat = make_catalog()
        cat._caps["web_search"] = replace(cat._caps["web_search"],
                                          available=False)
        keys = {t.key for t in build_templates(cat)}
        self.assertNotIn("daily_briefing", keys)
        self.assertNotIn("job_alerts", keys)
        self.assertIn("email_summarizer", keys)    # gmail unaffected
        self.assertIn("reminder_workflow", keys)


if __name__ == "__main__":
    unittest.main()
