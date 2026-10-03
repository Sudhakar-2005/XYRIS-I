"""Workflow validator contracts (Phase 4 / spec §16)."""
from __future__ import annotations

import unittest

from core.automation import Edge, Node, Trigger, Workflow, validate
from core.automation.validate import (
    INVALID, REQUIRES_PERMISSION, VALID,
)
from tests.test_automation_schema import make_catalog, make_workflow


class FakeLedger:
    def __init__(self, denied=()):
        self._denied = set(denied)

    def is_granted(self, name):
        return name not in self._denied


class TestHappyPath(unittest.TestCase):

    def test_minimal_workflow_is_valid(self):
        rep = validate(make_workflow(), make_catalog())
        self.assertEqual(rep.status, VALID, rep.errors)
        self.assertTrue(rep.valid)

    def test_full_feature_workflow_is_valid(self):
        wf = make_workflow()
        wf.nodes[1:1] = [
            Node(id="cond", type="CONDITION",
                 config={"left": "$.vars.flag", "op": "bool"}),
            Node(id="ai1", type="AI",
                 config={"operation": "summarize", "objective": "condense it"}),
            Node(id="tr1", type="TRANSFORM",
                 config={"op": "extract", "path": "$.nodes.t1.message",
                         "out": "summary"}),
            Node(id="mem", type="MEMORY", config={"action": "save"}),
            Node(id="ap1", type="APPROVAL", config={"title": "Proceed?"}),
            Node(id="del", type="DELAY", config={"seconds": 2}),
            Node(id="ver", type="VERIFICATION", config={"assert": "not_empty",
                                                         "path": "$.nodes.t1"}),
            Node(id="nt", type="NOTIFICATION", config={"message": "all done"}),
        ]
        wf.edges = [
            Edge("trg", "t1"), Edge("t1", "cond"),
            Edge("cond", "ai1", when="true"), Edge("cond", "tr1", when="false"),
            Edge("ai1", "mem"), Edge("mem", "ap1"), Edge("ap1", "del"),
            Edge("del", "ver"), Edge("tr1", "nt"), Edge("ver", "nt"),
            Edge("nt", "end"),
        ]
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, VALID, rep.errors)

    def test_schedule_trigger_valid(self):
        wf = make_workflow()
        wf.trigger = Trigger(type="schedule", config={"time": "09:00"})
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, VALID, rep.errors)

    def test_loop_workflow_valid(self):
        wf = make_workflow()
        wf.nodes.append(Node(id="b1", type="TRANSFORM",
                             config={"op": "extract", "path": "$.item"}))
        wf.nodes.append(Node(id="lp", type="LOOP",
                             config={"items_path": "$.nodes.t1.data.items",
                                     "body": ["b1"], "max_iterations": 10}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "lp"), Edge("lp", "end")]
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, VALID, rep.errors)


class TestStructureErrors(unittest.TestCase):

    def test_requires_exactly_one_trigger(self):
        wf = make_workflow()
        wf.nodes.append(Node(id="trg2", type="TRIGGER"))
        wf.edges.append(Edge("trg2", "end"))
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertTrue(any("exactly one TRIGGER" in e for e in rep.errors), rep.errors)

    def test_requires_end_node(self):
        wf = make_workflow()
        wf.nodes = [n for n in wf.nodes if n.type != "END"]
        wf.edges = [Edge("trg", "t1")]
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)

    def test_unreachable_node_rejected(self):
        wf = make_workflow()
        wf.nodes.append(Node(id="orphan", type="DELAY", config={"seconds": 1}))
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertTrue(any("unreachable" in e for e in rep.errors))

    def test_cycle_rejected(self):
        wf = make_workflow()
        wf.nodes.insert(1, Node(id="x1", type="DELAY", config={"seconds": 1}))
        wf.nodes.insert(2, Node(id="x2", type="DELAY", config={"seconds": 1}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "x1"), Edge("x1", "x2"),
                    Edge("x2", "x1"), Edge("x2", "end")]
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertTrue(any("cycle" in e for e in rep.errors))

    def test_duplicate_ids_rejected(self):
        wf = make_workflow()
        wf.nodes.append(Node(id="t1", type="DELAY", config={"seconds": 1}))
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertTrue(any("duplicate" in e for e in rep.errors))

    def test_edge_to_missing_node_rejected(self):
        wf = make_workflow()
        wf.edges.append(Edge("t1", "ghost"))
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)

    def test_empty_workflow_rejected(self):
        rep = validate(Workflow(id="e", name=""), make_catalog())
        self.assertEqual(rep.status, INVALID)

    def test_two_outgoing_edges_rejected(self):
        wf = make_workflow()
        wf.edges.append(Edge("t1", "end", when="true"))
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertTrue(any("branch" in e for e in rep.errors), rep.errors)


class TestNodeContractErrors(unittest.TestCase):

    def test_unknown_tool_reports_missing(self):
        wf = make_workflow()
        wf.node("t1").config["tool"] = "voice_call"
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertIn("voice_call", rep.missing_tools)

    def test_unavailable_tool_reported(self):
        wf = make_workflow()
        wf.node("t1").config["tool"] = "broken_tool"
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertIn("broken_tool", rep.unavailable_tools)

    def test_missing_required_param(self):
        wf = make_workflow()
        wf.node("t1").config["args"] = {}
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertTrue(any("missing required params" in e for e in rep.errors))

    def test_unknown_param_is_warning_not_error(self):
        wf = make_workflow()
        wf.node("t1").config["args"] = {"query": "x", "bogus": 1}
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, VALID, rep.errors)
        self.assertTrue(any("unknown params" in w for w in rep.warnings))

    def test_condition_requires_true_branch(self):
        wf = make_workflow()
        wf.nodes.insert(1, Node(id="c1", type="CONDITION",
                                config={"left": "$.x", "op": "eq", "right": 1}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "c1"), Edge("c1", "end")]
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertTrue(any("true-branch" in e for e in rep.errors))

    def test_bad_condition_op(self):
        wf = make_workflow()
        wf.nodes.insert(1, Node(id="c1", type="CONDITION",
                                config={"left": "$.x", "op": "regex_exec"}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "c1"),
                    Edge("c1", "end", when="true")]
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)

    def test_loop_iteration_cap_enforced(self):
        wf = make_workflow()
        wf.nodes.append(Node(id="b1", type="DELAY", config={"seconds": 1}))
        wf.nodes.append(Node(id="lp", type="LOOP",
                             config={"items_path": "$.a", "body": ["b1"],
                                     "max_iterations": 99999}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "lp"), Edge("lp", "end")]
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertTrue(any("max_iterations" in e for e in rep.errors))

    def test_loop_body_cannot_leak_into_main_graph(self):
        wf = make_workflow()
        wf.nodes.append(Node(id="b1", type="DELAY", config={"seconds": 1}))
        wf.nodes.append(Node(id="lp", type="LOOP",
                             config={"items_path": "$.a", "body": ["b1"]}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "lp"), Edge("lp", "end"),
                    Edge("t1", "b1")]
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)
        self.assertTrue(any("loop body" in e for e in rep.errors), rep.errors)

    def test_delay_bounds(self):
        wf = make_workflow()
        wf.nodes.insert(1, Node(id="d1", type="DELAY", config={"seconds": 999999}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "d1"), Edge("d1", "end")]
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)

    def test_node_timeout_range(self):
        wf = make_workflow()
        wf.node("t1").timeout_s = 0
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)


class TestTriggerAndPermissions(unittest.TestCase):

    def test_bad_schedule_time(self):
        wf = make_workflow()
        wf.trigger = Trigger(type="schedule", config={"time": "25:99"})
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)

    def test_schedule_needs_config(self):
        wf = make_workflow()
        wf.trigger = Trigger(type="schedule", config={})
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)

    def test_file_watch_needs_path(self):
        wf = make_workflow()
        wf.trigger = Trigger(type="file_watch", config={})
        rep = validate(wf, make_catalog())
        self.assertEqual(rep.status, INVALID)

    def test_revoked_permission_blocks_activation(self):
        wf = make_workflow()
        rep = validate(wf, make_catalog(),
                       ledger=FakeLedger(denied={"network"}))
        self.assertEqual(rep.status, REQUIRES_PERMISSION)
        self.assertEqual(rep.denied_permissions, ("network",))
        self.assertEqual(rep.required_permissions, ("network",))

    def test_granted_permissions_allow_valid(self):
        wf = make_workflow()
        rep = validate(wf, make_catalog(), ledger=FakeLedger())
        self.assertEqual(rep.status, VALID)

    def test_forbidden_risk_refused(self):
        wf = make_workflow()
        wf.node("t1").config["tool"] = "send_message"
        wf.node("t1").config["args"] = {"receiver": "a", "message_text": "m",
                                        "platform": "wa"}
        wf.nodes.insert(2, Node(id="nuker", type="TOOL",
                                config={"tool": "send_message",
                                        "args": {"receiver": "b",
                                                 "message_text": "x",
                                                 "platform": "wa"}}))
        wf.node("nuker").risk = ""  # recomputed by analyze()
        cat = make_catalog()
        # simulate a forbidden capability by poisoning the catalog entry
        import dataclasses
        cap = cat.get("send_message")
        cat._caps["send_message"] = dataclasses.replace(cap, risk="forbidden")
        wf.edges = [Edge("trg", "t1"), Edge("t1", "end")]
        rep = validate(wf, cat)
        self.assertEqual(rep.status, INVALID)
        self.assertTrue(any("forbidden" in e for e in rep.errors))


if __name__ == "__main__":
    unittest.main()
