"""Automation schema + capability + risk contracts (Phases 1-3)."""
from __future__ import annotations

import unittest

from core.automation import (
    Capability, CapabilityCatalog, Edge, Node, NodeResult, NodeType,
    RetryPolicy, Trigger, Workflow, WorkflowStatus, analyze, resolve_path,
    set_path,
)
from core.automation.schema import (
    HARD_MAX_ITERATIONS, MAX_NODE_TIMEOUT_S, MIN_NODE_TIMEOUT_S, NodeStatus,
    RunStatus, VerificationState,
)


def _tool_node(nid="t1", tool="web_search", **cfg):
    config = {"tool": tool, "args": {"query": "x"}}
    config.update(cfg)
    return Node(id=nid, type="TOOL", name=tool, config=config)


def make_workflow() -> Workflow:
    nodes = [
        Node(id="trg", type="TRIGGER", name="start"),
        _tool_node("t1"),
        Node(id="end", type="END", name="done"),
    ]
    edges = [Edge("trg", "t1"), Edge("t1", "end")]
    return Workflow(id="wf_test", name="Test WF", nodes=nodes, edges=edges,
                    trigger=Trigger())


def make_catalog() -> CapabilityCatalog:
    cat = CapabilityCatalog()
    cat._caps = {
        "web_search": Capability(name="web_search", kind="action",
                                 description="search the web",
                                 parameters={"type": "OBJECT",
                                             "properties": {"query": {"type": "STRING"}},
                                             "required": ["query"]},
                                 risk="safe", timeout=60, permissions=("network",)),
        "gmail": Capability(name="gmail", kind="plugin", description="mail",
                            parameters={"type": "OBJECT",
                                        "properties": {"action": {"type": "STRING"}},
                                        "required": ["action"]}),
        "send_message": Capability(name="send_message", kind="action",
                                   description="send a message",
                                   parameters={"type": "OBJECT",
                                               "properties": {"receiver": {},
                                                              "message_text": {},
                                                              "platform": {}},
                                               "required": ["receiver", "message_text", "platform"]},
                                   risk="dangerous", requires_confirmation=True,
                                   permissions=("messaging",)),
        "broken_tool": Capability(name="broken_tool", kind="action",
                                  description="x", available=False),
        "generic": Capability(name="generic", kind="action",
                              description="generic no-param tool",
                              parameters={"type": "OBJECT",
                                          "properties": {}},
                              risk="safe", timeout=30, permissions=()),
    }
    return cat


class TestSchemaRoundTrip(unittest.TestCase):

    def test_workflow_round_trip_is_stable(self):
        wf = make_workflow()
        wf.variables = {"limit": 5}
        wf.permissions = ("network",)
        wf.status = WorkflowStatus.READY.value
        back = Workflow.from_dict(wf.to_dict())
        self.assertEqual(back.to_dict(), wf.to_dict())
        self.assertEqual(back.status, "READY")
        self.assertEqual(back.trigger.type, "manual")
        self.assertEqual(len(back.nodes), 2 + 1)
        self.assertEqual(back.edges[0].source, "trg")

    def test_node_round_trip_clamps_bad_values(self):
        n = Node.from_dict({"id": "a", "type": "TOOL", "timeout_s": "999999",
                            "retry": {"max_attempts": 99, "backoff_s": -3},
                            "config": {"tool": "x"}})
        self.assertEqual(n.timeout_s, MAX_NODE_TIMEOUT_S)
        self.assertEqual(n.retry.max_attempts, 5)
        self.assertEqual(n.retry.backoff_s, 0.0)
        n2 = Node.from_dict({"id": "b", "type": "TOOL", "timeout_s": 0})
        self.assertEqual(n2.timeout_s, MIN_NODE_TIMEOUT_S)

    def test_retry_policy_tolerates_junk(self):
        self.assertEqual(RetryPolicy.from_dict(None).max_attempts, 1)
        self.assertEqual(RetryPolicy.from_dict({"max_attempts": "nope"}).max_attempts, 1)

    def test_workflow_helpers(self):
        wf = make_workflow()
        self.assertEqual(wf.node("t1").type, "TOOL")
        self.assertIsNone(wf.node("missing"))
        self.assertEqual(wf.successors("trg"), ["t1"])
        self.assertEqual(wf.trigger_nodes()[0].id, "trg")
        self.assertEqual(wf.main_nodes(), wf.nodes)

    def test_loop_body_membership(self):
        wf = make_workflow()
        body = [Node(id="b1", type="TRANSFORM", config={"op": "extract", "path": "$.x"}),
                Node(id="b2", type="DELAY", config={"seconds": 1})]
        loop = Node(id="lp", type="LOOP",
                    config={"items_path": "$.items", "body": ["b1", "b2"]})
        wf.nodes.extend(body)
        wf.nodes.append(loop)
        wf.edges.append(Edge("t1", "lp"))
        wf.edges.append(Edge("lp", "end"))
        self.assertEqual(wf.loop_body_ids(), {"b1", "b2"})
        self.assertEqual(sorted(n.id for n in wf.main_nodes()),
                         ["end", "lp", "t1", "trg"])


class TestPathUtils(unittest.TestCase):

    def test_resolve_nested(self):
        data = {"nodes": {"t1": {"data": {"items": [10, 20, {"z": 5}]}}},
                "vars": {"name": "sam"}}
        self.assertEqual(resolve_path(data, "$.vars.name"), "sam")
        self.assertEqual(resolve_path(data, "nodes.t1.data.items[1]"), 20)
        self.assertEqual(resolve_path(data, "$.nodes.t1.data.items[2].z"), 5)

    def test_resolve_never_raises(self):
        self.assertIsNone(resolve_path({}, "$.nope.deep"))
        self.assertIsNone(resolve_path(None, "$.a"))
        self.assertEqual(resolve_path({"a": 1}, "$", ), {"a": 1})
        self.assertEqual(resolve_path({"a": 1}, "a[bad]"), None)

    def test_set_path_creates_tree(self):
        d = {}
        set_path(d, "$.nodes.t1.message", "hi")
        set_path(d, "vars.x", 3)
        self.assertEqual(d["nodes"]["t1"]["message"], "hi")
        self.assertEqual(d["vars"]["x"], 3)


class TestNodeResult(unittest.TestCase):

    def test_success_output(self):
        r = NodeResult.from_tool_output("Found 5 results.", duration_ms=12)
        self.assertTrue(r.success)
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.execution_time_ms, 12)

    def test_failure_strings(self):
        for bad in ("Tool 'web_search' failed: boom",
                    "Action 'x' is not available.",
                    ""):
            r = NodeResult.from_tool_output(bad)
            self.assertFalse(r.success, bad)

    def test_timeout_and_refusal(self):
        t = NodeResult.from_tool_output("Tool 'x' timed out after 5s.",
                                        timed_out=True)
        self.assertEqual(t.status, "timeout")
        f = NodeResult.from_tool_output("I cannot run this — forbidden.",
                                        refused=True)
        self.assertEqual(f.status, "refused")

    def test_round_trip(self):
        r = NodeResult(success=True, message="m", data={"k": 1},
                       verification=VerificationState.VERIFIED.value)
        back = NodeResult.from_dict(r.to_dict())
        self.assertEqual(back.to_dict(), r.to_dict())


class TestEnums(unittest.TestCase):

    def test_status_vocab_complete(self):
        want = {"DRAFT", "VALIDATING", "READY", "WAITING_CONFIRMATION", "ACTIVE",
                "PAUSED", "RUNNING", "VERIFYING", "COMPLETED", "FAILED",
                "DISABLED", "ARCHIVED"}
        self.assertEqual({s.value for s in WorkflowStatus}, want)

    def test_node_types_complete(self):
        want = {"TRIGGER", "TOOL", "AI", "CONDITION", "TRANSFORM", "LOOP",
                "DELAY", "APPROVAL", "MEMORY", "NOTIFICATION", "VERIFICATION", "END"}
        self.assertEqual({t.value for t in NodeType}, want)

    def test_other_vocab(self):
        self.assertEqual({s.value for s in NodeStatus},
                         {"PENDING", "RUNNING", "WAITING_APPROVAL", "SUCCEEDED",
                          "FAILED", "SKIPPED", "VERIFIED", "CANCELLED", "TIMEOUT"})
        self.assertEqual({s.value for s in RunStatus},
                         {"RUNNING", "WAITING_APPROVAL", "PAUSED", "COMPLETED",
                          "FAILED", "CANCELLED", "DRY_RUN"})
        self.assertIn("manual", {t.value for t in __import__(
            "core.automation.schema", fromlist=["TriggerType"]).TriggerType})
        self.assertEqual(HARD_MAX_ITERATIONS, 1000)


class TestCapabilityCatalog(unittest.TestCase):

    def test_reads_action_registry(self):
        class FakePolicy:
            risk = "caution"
            timeout = 45
            requires_confirmation = True
            supports_undo = False
            verification = "verifier"
            permissions = ("desktop",)

        class Rec:
            valid = True
            parameters = {"type": "OBJECT", "properties": {"a": {}},
                          "required": ["a"]}

        class FakeActions:
            _all_records = {"browser_control": Rec()}

            def names(self):
                return ["browser_control"]

            def policy(self, name):
                return FakePolicy()

            def describe(self, name):
                return "control the browser"

        cat = CapabilityCatalog(action_registry=FakeActions())
        cap = cat.get("browser_control")
        self.assertEqual(cap.kind, "action")
        self.assertEqual(cap.risk, "caution")
        self.assertTrue(cap.requires_confirmation)
        self.assertEqual(cap.required_params, ("a",))
        self.assertEqual(len(cat), 1)

    def test_inline_meta_and_queries(self):
        cat = CapabilityCatalog(inline_meta={
            "save_memory": {"risk": "safe", "timeout": 15,
                            "requires_confirmation": False,
                            "supports_undo": False, "verification": "none",
                            "permissions": ["memory"]}})
        cap = cat.get("save_memory")
        self.assertEqual(cap.kind, "inline")
        self.assertIn("save_memory", cat.names())
        self.assertEqual(len(cat), 1)
        proj = cap.to_prompt_dict()
        self.assertEqual(proj["name"], "save_memory")
        self.assertEqual(proj["kind"], "inline")

    def test_missing_registries_yield_empty_catalog(self):
        self.assertEqual(len(CapabilityCatalog()), 0)


class TestRiskAnalysis(unittest.TestCase):

    def test_tool_nodes_inherit_policy(self):
        wf = make_workflow()
        risk = analyze(wf, make_catalog())
        node = wf.node("t1")
        self.assertEqual(node.risk, "safe")
        self.assertEqual(node.permissions, ("network",))
        self.assertEqual(risk.risk_level, "safe")
        self.assertEqual(risk.required_permissions, ("network",))

    def test_dangerous_tool_raises_workflow_risk(self):
        wf = make_workflow()
        wf.nodes.insert(1, _tool_node("t2", tool="send_message",
                                      args={"receiver": "a", "message_text": "hi",
                                            "platform": "whatsapp"}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "t2"), Edge("t2", "end")]
        risk = analyze(wf, make_catalog())
        self.assertEqual(wf.node("t2").risk, "dangerous")
        self.assertEqual(risk.risk_level, "dangerous")
        self.assertIn("t2", risk.high_risk_nodes)
        self.assertIn("messaging", risk.required_permissions)

    def test_plugin_overrides_gmail_send(self):
        wf = make_workflow()
        wf.nodes.insert(1, Node(id="gm", type="TOOL",
                                config={"tool": "gmail",
                                        "args": {"action": "send", "to": "x"}}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "gm"), Edge("gm", "end")]
        analyze(wf, make_catalog())
        self.assertEqual(wf.node("gm").risk, "dangerous")
        self.assertIn("messaging", wf.node("gm").permissions)

    def test_static_policies_for_non_tool_nodes(self):
        wf = make_workflow()
        wf.nodes.insert(1, Node(id="ai1", type="AI",
                                config={"operation": "summarize", "objective": "x"}))
        wf.nodes.insert(2, Node(id="nt1", type="NOTIFICATION",
                                config={"message": "done"}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "ai1"), Edge("ai1", "nt1"),
                    Edge("nt1", "end")]
        analyze(wf, make_catalog())
        self.assertEqual(wf.node("ai1").permissions, ("network",))
        self.assertEqual(wf.node("nt1").permissions, ("desktop",))
        self.assertEqual(wf.node("trg").risk, "safe")

    def test_unknown_tool_defaults_cautious_process(self):
        wf = make_workflow()
        wf.node("t1").config["tool"] = "ghost"
        analyze(wf, make_catalog())
        self.assertEqual(wf.node("t1").risk, "caution")


if __name__ == "__main__":
    unittest.main()
