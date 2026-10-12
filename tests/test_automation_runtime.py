"""Automation runtime + store + triggers + engine contracts (Phases 5, 8, 9)."""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from core.automation import Edge, Node, Trigger, Workflow
from core.automation.engine import AutomationEngine, get_engine, set_engine
from core.automation.runtime import AutomationRuntime, RunRecord, RunStatus
from core.automation.schema import NodeStatus, WorkflowStatus
from core.automation.store import AutomationStore
from core.automation.triggers import TriggerEngine
from tests.test_automation_schema import make_catalog, make_workflow


def run_async(coro):
    return asyncio.run(coro)


class FakeDeps:
    def __init__(self, replies=None, fail_times=None):
        self.calls: list[tuple] = []
        self.replies = replies or {}
        self.fail_times = dict(fail_times or {})
        self.approvals: list = []
        self.approve_with = True
        self.notified: list[str] = []
        self.memory: dict = {}
        self.ai_replies: list[str] = ["AI says: fine"]
        self.events: list[tuple] = []
        self.cancel_on: str | None = None
        self.cancelled_ids: set = set()

    async def tool_call(self, name, args, timeout):
        self.calls.append((name, dict(args), timeout))
        n = len([c for c in self.calls if c[0] == name])
        if n <= self.fail_times.get(name, 0):
            return {"ok": False, "text": f"Tool '{name}' failed: transient",
                    "error": "transient"}
        reply = self.replies.get(name, f"{name} ok")
        if callable(reply):
            reply = reply(args)
        return {"ok": True, "text": reply, "verification": "none"}

    async def confirm(self, title, detail):
        self.approvals.append((title, detail))
        return self.approve_with

    async def ai_call(self, *, operation, objective, input_text, max_chars,
                      timeout_s):
        return self.ai_replies[0]

    def memory_save(self, key, value):
        self.memory[key] = value
        return f"saved {key}"

    def memory_recall(self, query):
        return json.dumps(self.memory)

    def notify(self, message):
        self.notified.append(message)

    def emit(self, kind, data, run_id):
        self.events.append((kind, dict(data), run_id))
        if self.cancel_on and kind == "node_finished" and \
                data.get("node") == self.cancel_on and run_id:
            if run_id not in self.cancelled_ids:
                self.cancelled_ids.add(run_id)
                self.runtime.cancel(run_id)


def _runtime(deps: FakeDeps, store=None) -> AutomationRuntime:
    rt = AutomationRuntime(
        tool_call=deps.tool_call, confirm=deps.confirm, ai_call=deps.ai_call,
        memory_save=deps.memory_save, memory_recall=deps.memory_recall,
        notify=deps.notify, emit=deps.emit, store=store)
    deps.runtime = rt
    return rt


class TestSequentialExecution(unittest.TestCase):

    def test_happy_path(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        wf = make_workflow()
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        ids = [s.node_id for s in rec.steps]
        self.assertEqual(ids, ["trg", "t1", "end"])
        self.assertTrue(all(s.result.success for s in rec.steps))
        self.assertEqual(deps.calls[0][0], "web_search")
        kinds = [e[0] for e in deps.events]
        self.assertIn("run_started", kinds)
        self.assertIn("node_finished", kinds)
        self.assertIn("run_completed", kinds)

    def test_tool_failure_stops_with_ux_error(self):
        deps = FakeDeps(fail_times={"web_search": 99})
        rt = _runtime(deps)
        rec = run_async(rt.execute(make_workflow()))
        self.assertEqual(rec.status, RunStatus.FAILED.value)
        err = rec.error
        self.assertEqual(err["node_id"], "t1")
        self.assertTrue(err["what"])
        self.assertTrue(err["why"])
        self.assertIn("trg", err["completed"])
        self.assertIn("done", err["not_executed"])   # END node, name "done"
        self.assertTrue(err["next"])

    def test_retry_then_success(self):
        deps = FakeDeps(fail_times={"web_search": 2})
        rt = _runtime(deps)
        wf = make_workflow()
        wf.node("t1").retry.max_attempts = 3
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        step = next(s for s in rec.steps if s.node_id == "t1")
        self.assertEqual(step.attempt, 3)
        self.assertEqual(len(deps.calls), 3)

    def test_dangerous_node_never_autoretried(self):
        deps = FakeDeps(fail_times={"web_search": 5})
        rt = _runtime(deps)
        wf = make_workflow()
        wf.node("t1").risk = "dangerous"
        wf.node("t1").retry.max_attempts = 5
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.FAILED.value)
        self.assertEqual(len(deps.calls), 1)

    def test_timeout_marks_node(self):
        deps = FakeDeps()

        async def slow(name, args, timeout):
            await asyncio.sleep(5.0)
            return {"ok": True, "text": "late"}

        deps.tool_call = slow
        rt = _runtime(deps)
        wf = make_workflow()
        wf.node("t1").timeout_s = 1
        start = time.time()
        rec = run_async(rt.execute(wf))
        self.assertLess(time.time() - start, 3.5)
        self.assertEqual(rec.status, RunStatus.FAILED.value)
        step = next(s for s in rec.steps if s.node_id == "t1")
        self.assertEqual(step.status, NodeStatus.TIMEOUT.value)

    def test_timeout_outcome_from_dispatcher(self):
        deps = FakeDeps()

        async def timed_out(name, args, timeout):
            return {"ok": False, "timed_out": True,
                    "text": f"Tool '{name}' timed out after {timeout}s."}

        deps.tool_call = timed_out
        rt = _runtime(deps)
        rec = run_async(rt.execute(make_workflow()))
        step = next(s for s in rec.steps if s.node_id == "t1")
        self.assertEqual(step.status, NodeStatus.TIMEOUT.value)
        self.assertFalse(step.result.success)

    def test_cancellation(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        deps.cancel_on = "t1"
        rec = run_async(rt.execute(make_workflow()))
        self.assertEqual(rec.status, RunStatus.CANCELLED.value)
        self.assertNotIn("end", [s.node_id for s in rec.steps])

    def test_pause_and_resume(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        wf = make_workflow()
        wf.nodes.insert(2, Node(id="d1", type="DELAY", config={"seconds": 1}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "d1"), Edge("d1", "end")]

        async def scenario():
            task = asyncio.ensure_future(rt.execute(wf))
            await asyncio.sleep(0.15)
            rid = rt.active_run_ids()[0]
            self.assertTrue(rt.pause(rid))
            await asyncio.sleep(0.4)
            self.assertFalse(task.done())
            self.assertTrue(rt.resume(rid))
            return await task

        rec = run_async(scenario())
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        self.assertEqual(len(rec.steps), 4)

    def test_stop_missing_run(self):
        rt = _runtime(FakeDeps())
        self.assertFalse(rt.cancel("nope"))
        self.assertFalse(rt.pause("nope"))
        self.assertFalse(rt.resume("nope"))


class TestBranchesAndPureNodes(unittest.TestCase):

    def test_condition_true_branch(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        wf = make_workflow()
        wf.nodes.insert(1, Node(id="c1", type="CONDITION",
                                config={"left": "$.vars.flag", "op": "bool"}))
        wf.nodes.insert(2, Node(id="t2", type="TOOL",
                                config={"tool": "web_search",
                                        "args": {"query": "else"}}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "c1"),
                    Edge("c1", "t2", when="true"),
                    Edge("c1", "end", when="false")]
        wf.variables = {"flag": True}
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        self.assertIn("t2", [s.node_id for s in rec.steps])
        self.assertNotIn("end", [s.node_id for s in rec.steps])

    def test_condition_false_branch(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        wf = make_workflow()
        wf.nodes.insert(1, Node(id="c1", type="CONDITION",
                                config={"left": "$.vars.flag", "op": "bool"}))
        wf.nodes.insert(2, Node(id="t2", type="TOOL",
                                config={"tool": "web_search",
                                        "args": {"query": "else"}}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "c1"),
                    Edge("c1", "t2", when="true"),
                    Edge("c1", "end", when="false")]
        wf.variables = {"flag": False}
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        self.assertNotIn("t2", [s.node_id for s in rec.steps])
        self.assertIn("end", [s.node_id for s in rec.steps])

    def test_transform_extract_and_format(self):
        deps = FakeDeps(replies={"web_search": "hello world"})
        rt = _runtime(deps)
        wf = make_workflow()
        wf.nodes.insert(2, Node(id="x1", type="TRANSFORM",
                                config={"op": "extract",
                                        "path": "$.nodes.t1.message",
                                        "out": "$.vars.snippet"}))
        wf.nodes.insert(3, Node(id="x2", type="TRANSFORM",
                                config={"op": "format", "out": "$.vars.line",
                                        "template": "got {$.vars.snippet}!"}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "x1"), Edge("x1", "x2"),
                    Edge("x2", "end")]
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)

    def test_verification_node_fails_run_when_assertion_false(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        wf = make_workflow()
        wf.nodes.insert(2, Node(id="v1", type="VERIFICATION",
                                config={"assert": "equals",
                                        "path": "$.vars.expect",
                                        "value": "yes"}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "v1"), Edge("v1", "end")]
        wf.variables = {"expect": "no"}
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.FAILED.value)
        self.assertEqual(rec.error["node_id"], "v1")

    def test_notification_and_memory_nodes(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        wf = make_workflow()
        wf.nodes[1:1] = [
            Node(id="m1", type="MEMORY",
                 config={"action": "save", "key": "snippet",
                         "source": "$.nodes.t1.message"}),
            Node(id="n1", type="NOTIFICATION",
                 config={"message": "done: {$.nodes.t1.message}"}),
        ]
        wf.edges = [Edge("trg", "t1"), Edge("t1", "m1"), Edge("m1", "n1"),
                    Edge("n1", "end")]
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        self.assertIn("snippet", deps.memory)
        self.assertEqual(len(deps.notified), 1)
        self.assertIn("done: web_search ok", deps.notified[0])

    def test_ai_node_uses_injected_engine(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        wf = make_workflow()
        wf.nodes.insert(2, Node(id="a1", type="AI",
                                config={"operation": "summarize",
                                        "objective": "summarize",
                                        "input_path": "$.nodes.t1.message"}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "a1"), Edge("a1", "end")]
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        step = next(s for s in rec.steps if s.node_id == "a1")
        self.assertIn("AI says", step.result.message)


class TestApproval(unittest.TestCase):

    def _wf(self):
        wf = make_workflow()
        wf.nodes.insert(2, Node(id="ap", type="APPROVAL",
                                config={"title": "Send it?",
                                        "detail": "about {$.vars.name}"}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "ap"), Edge("ap", "end")]
        wf.variables = {"name": "Arun"}
        return wf

    def test_approval_accepted(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        rec = run_async(rt.execute(self._wf()))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        self.assertEqual(deps.approvals[0][0], "Send it?")
        self.assertIn("Arun", deps.approvals[0][1])

    def test_approval_denied_cancels_and_explains(self):
        deps = FakeDeps()
        deps.approve_with = False
        rt = _runtime(deps)
        rec = run_async(rt.execute(self._wf()))
        self.assertEqual(rec.status, RunStatus.CANCELLED.value)
        self.assertEqual(rec.error["code"], "approval_denied")
        self.assertIn("done", rec.error["not_executed"])


class TestLoops(unittest.TestCase):

    def test_iterates_all_items(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        wf = make_workflow()
        wf.variables = {"numbers": [1, 2, 3]}
        wf.nodes.append(Node(id="b1", type="TRANSFORM",
                             config={"op": "extract", "path": "$.vars.item",
                                     "out": "$.vars.seen"}))
        wf.nodes.append(Node(id="lp", type="LOOP",
                             config={"items_path": "$.vars.numbers",
                                     "body": ["b1"], "max_iterations": 10}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "lp"), Edge("lp", "end")]
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        body_steps = [s for s in rec.steps if s.node_id == "b1"]
        self.assertEqual(len(body_steps), 3)
        loop_step = next(s for s in rec.steps if s.node_id == "lp")
        self.assertEqual(loop_step.result.data["iterations"], 3)

    def test_iteration_cap(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        wf = make_workflow()
        wf.variables = {"numbers": list(range(10))}
        wf.nodes.append(Node(id="b1", type="TRANSFORM",
                             config={"op": "set", "value": 1,
                                     "out": "$.vars.seen"}))
        wf.nodes.append(Node(id="lp", type="LOOP",
                             config={"items_path": "$.vars.numbers",
                                     "body": ["b1"], "max_iterations": 3}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "lp"), Edge("lp", "end")]
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        loop_step = next(s for s in rec.steps if s.node_id == "lp")
        self.assertEqual(loop_step.result.data["iterations"], 3)
        self.assertTrue(loop_step.result.data["capped"])

    def test_body_failure_stops_by_default(self):
        deps = FakeDeps(fail_times={"generic": 1})
        rt = _runtime(deps)
        wf = make_workflow()
        wf.variables = {"numbers": [1, 2]}
        wf.nodes.append(Node(id="b1", type="TOOL",
                             config={"tool": "generic", "args": {}}))
        wf.nodes.append(Node(id="lp", type="LOOP",
                             config={"items_path": "$.vars.numbers",
                                     "body": ["b1"]}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "lp"), Edge("lp", "end")]
        rec = run_async(rt.execute(wf))
        self.assertEqual(rec.status, RunStatus.FAILED.value)
        self.assertEqual(rec.error["node_id"], "lp")


class TestDryRun(unittest.TestCase):

    def test_dry_run_never_touches_real_tools(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        rec = run_async(rt.execute(make_workflow(), mode="dry"))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        self.assertEqual(deps.calls, [])
        self.assertEqual(rec.mode, "dry")
        tool_step = next(s for s in rec.steps if s.node_id == "t1")
        self.assertTrue(tool_step.result.metadata.get("simulated"))
        self.assertIn("DRY RUN", tool_step.result.message)

    def test_dry_run_still_evaluates_pure_nodes(self):
        deps = FakeDeps()
        rt = _runtime(deps)
        wf = make_workflow()
        wf.nodes.insert(1, Node(id="c1", type="CONDITION",
                                config={"left": "$.vars.flag", "op": "bool"}))
        wf.nodes.insert(2, Node(id="t2", type="TOOL",
                                config={"tool": "web_search",
                                        "args": {"query": "y"}}))
        wf.edges = [Edge("trg", "t1"), Edge("t1", "c1"),
                    Edge("c1", "t2", when="true"),
                    Edge("c1", "end", when="false")]
        wf.variables = {"flag": False}
        rec = run_async(rt.execute(wf, mode="dry"))
        self.assertEqual(rec.status, RunStatus.COMPLETED.value)
        self.assertNotIn("t2", [s.node_id for s in rec.steps])


class TestIdempotency(unittest.TestCase):

    def test_dangerous_tool_runs_once_across_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            deps = FakeDeps()
            rt = _runtime(deps, store=store)
            wf = make_workflow()
            wf.node("t1").risk = "dangerous"
            wf.node("t1").config["idempotency_key"] = "send-once"
            store.save(wf, bump_version=True)
            r1 = run_async(rt.execute(wf))
            self.assertEqual(r1.status, RunStatus.COMPLETED.value)
            r2 = run_async(rt.execute(wf))
            self.assertEqual(r2.status, RunStatus.COMPLETED.value)
            self.assertEqual(len(deps.calls), 1)
            step = next(s for s in r2.steps if s.node_id == "t1")
            self.assertTrue(step.result.data.get("idempotent_skip"))


class TestStore(unittest.TestCase):

    def test_versions_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            wf = make_workflow()
            store.save(wf, bump_version=True)
            wf.description = "v2 desc"
            store.save(wf, bump_version=True)
            self.assertEqual(wf.version, 2)
            versions = store.versions(wf.id)
            self.assertEqual([v["version"] for v in versions], [1, 2])
            self.assertTrue(versions[-1]["current"])
            store.restore(wf.id, 1)
            back = store.load(wf.id)
            self.assertEqual(back.version, 3)
            self.assertEqual(back.description, "")
            self.assertEqual(back.metadata["restored_from_version"], 1)

    def test_save_is_idempotent_when_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            wf = make_workflow()
            store.save(wf, bump_version=True)
            v = wf.version
            store.save(wf, bump_version=True)
            self.assertEqual(store.load(wf.id).version, v)

    def test_list_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            wf = make_workflow()
            store.save(wf, bump_version=True)
            items = store.list()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["id"], wf.id)
            self.assertEqual(items[0]["name"], "Test WF")
            self.assertTrue(store.delete(wf.id))
            self.assertEqual(store.list(), [])
            self.assertIsNone(store.load(wf.id))

    def test_corrupt_file_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(store.load("bad"))

    def test_run_round_trip_and_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            wf = make_workflow()
            store.save(wf, bump_version=True)
            rec = RunRecord(run_id="run_1", workflow_id=wf.id,
                            status="COMPLETED", started_at=time.time(),
                            finished_at=time.time())
            store.record_run(rec)
            runs = store.list_runs(wf.id)
            self.assertEqual(runs[0]["run_id"], "run_1")
            stats = store.update_statistics(wf.id, rec)
            self.assertEqual(stats["runs"], 1)
            self.assertEqual(stats["completed"], 1)
            reloaded = store.load(wf.id)
            self.assertEqual(reloaded.statistics["runs"], 1)
            self.assertEqual(reloaded.last_run["run_id"], "run_1")

    def test_idempotency_marks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            wf = make_workflow()
            store.save(wf, bump_version=True)
            self.assertFalse(store.idempotency_done(wf.id, "k"))
            store.mark_idempotency(wf.id, "k", "run_1")
            self.assertTrue(store.idempotency_done(wf.id, "k"))
            self.assertFalse(store.idempotency_done(wf.id, "other"))

    def test_bad_id_is_sanitised_to_store_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            p = store._path("../evil")
            self.assertEqual(p.parent, Path(tmp))
            self.assertNotIn("..", p.name)
            self.assertEqual(store._path("ok-id_1").name, "ok-id_1.json")
            with self.assertRaises(ValueError):
                store._path("...")


class TestTriggers(unittest.TestCase):

    def _wf(self, ttype, config, status="ACTIVE"):
        wf = make_workflow()
        wf.status = status
        wf.trigger = Trigger(type=ttype, config=config)
        return wf

    def test_daily_schedule_fires_once_per_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            fixed = time.mktime((2026, 10, 3, 9, 1, 0, 0, 0, -1))
            te = TriggerEngine(store, now=lambda: fixed)
            wf = self._wf("schedule", {"time": "09:00"})
            due = te.due([wf])
            self.assertEqual(len(due), 1)
            te.mark_fired(wf, due[0][1])
            self.assertEqual(te.due([wf]), [])

    def test_daily_schedule_not_due_before_time(self):
        te = TriggerEngine(None, now=lambda: time.mktime(
            (2026, 10, 3, 8, 59, 0, 0, 0, -1)))
        wf = self._wf("schedule", {"time": "09:00"})
        self.assertEqual(te.due([wf]), [])

    def test_interval_schedule_needs_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            t_holder = {"now": 1_000_000.0}
            te = TriggerEngine(store, now=lambda: t_holder["now"])
            wf = self._wf("schedule", {"every_minutes": 10})
            te.baseline(wf)
            self.assertEqual(te.due([wf]), [])
            t_holder["now"] += 11 * 60
            due = te.due([wf])
            self.assertEqual(len(due), 1)
            te.mark_fired(wf, due[0][1])
            self.assertEqual(te.due([wf]), [])

    def test_file_watch_baseline_then_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AutomationStore(tmp)
            te = TriggerEngine(store)
            wf = self._wf("file_watch", {"path": tmp, "pattern": "*.pdf",
                                         "event": "created"})
            te.baseline(wf)
            self.assertEqual(te.due([wf]), [])
            (Path(tmp) / "new.pdf").write_bytes(b"x")
            due = te.due([wf])
            self.assertEqual(len(due), 1)
            self.assertIn("new.pdf", due[0][1])

    def test_inactive_and_manual_never_due(self):
        te = TriggerEngine(None)
        wf = self._wf("schedule", {"time": "00:00"}, status="PAUSED")
        self.assertEqual(te.due([wf]), [])
        manual = self._wf("manual", {})
        self.assertEqual(te.due([manual]), [])


class TestEngine(unittest.TestCase):

    def _engine(self, tmp, deps):
        cat = make_catalog()
        store = AutomationStore(tmp)
        return AutomationEngine(store=store, catalog=cat,
                                tool_call=deps.tool_call,
                                confirm=deps.confirm, ai_call=deps.ai_call,
                                memory_save=deps.memory_save,
                                memory_recall=deps.memory_recall,
                                notify=deps.notify, emit=deps.emit,
                                log=lambda m: None)

    def test_create_validate_activate_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            deps = FakeDeps()
            eng = self._engine(tmp, deps)
            spec = make_workflow().to_dict()
            wf = eng.create(spec, activate=True)
            self.assertEqual(wf.status, WorkflowStatus.ACTIVE.value)
            rec = run_async(eng.run(wf.id))
            self.assertEqual(rec.status, RunStatus.COMPLETED.value)
            summary = eng.status_summary()
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["active"], 1)

    def test_create_invalid_refuses_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            deps = FakeDeps()
            eng = self._engine(tmp, deps)
            spec = make_workflow().to_dict()
            spec["nodes"].pop()          # drop END
            with self.assertRaises(ValueError):
                eng.create(spec, activate=True)

    def test_run_missing_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp, FakeDeps())
            with self.assertRaises(KeyError):
                run_async(eng.run("nope"))

    def test_tick_fires_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            deps = FakeDeps()
            eng = self._engine(tmp, deps)
            spec = make_workflow().to_dict()
            spec["trigger"] = {"type": "schedule", "config": {"time": "00:00"}}
            wf = eng.create(spec, activate=True)
            fired = run_async(eng.tick())
            self.assertEqual(len(fired), 1)
            self.assertEqual(fired[0].status, RunStatus.COMPLETED.value)
            fired2 = run_async(eng.tick())
            self.assertEqual(fired2, [])

    def test_pause_resume_stop_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            deps = FakeDeps()
            eng = self._engine(tmp, deps)
            spec = make_workflow().to_dict()
            spec["nodes"].insert(2, {"id": "d1", "type": "DELAY",
                                     "config": {"seconds": 2}})
            spec["edges"] = [{"source": "trg", "target": "t1"},
                             {"source": "t1", "target": "d1"},
                             {"source": "d1", "target": "end"}]
            wf = eng.create(spec, activate=True)

            async def scenario():
                task = asyncio.ensure_future(eng.run(wf.id))
                await asyncio.sleep(0.15)
                ok, msg = eng.pause(wf.id)
                self.assertTrue(ok, msg)
                await asyncio.sleep(0.3)
                self.assertFalse(task.done())
                ok, msg = eng.resume(wf.id)
                self.assertTrue(ok, msg)
                rec = await task
                ok, msg = eng.stop(wf.id)
                self.assertFalse(ok)
                return rec

            rec = run_async(scenario())
            self.assertEqual(rec.status, RunStatus.COMPLETED.value)

    def test_explain_lists_steps_and_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp, FakeDeps())
            spec = make_workflow().to_dict()
            wf = eng.create(spec)
            text = eng.explain(wf.id)
            self.assertIn("Test WF", text)
            self.assertIn("[TOOL]", text)
            self.assertIn("trigger: manual", text)

    def test_delete_cancels_active_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            deps = FakeDeps()
            eng = self._engine(tmp, deps)
            spec = make_workflow().to_dict()
            spec["nodes"].insert(2, {"id": "d1", "type": "DELAY",
                                     "config": {"seconds": 3}})
            spec["edges"] = [{"source": "trg", "target": "t1"},
                             {"source": "t1", "target": "d1"},
                             {"source": "d1", "target": "end"}]
            wf = eng.create(spec, activate=True)

            async def scenario():
                task = asyncio.ensure_future(eng.run(wf.id))
                await asyncio.sleep(0.1)
                self.assertTrue(eng.delete(wf.id))
                return await task

            rec = run_async(scenario())
            self.assertEqual(rec.status, RunStatus.CANCELLED.value)

    def test_engine_singleton_accessor(self):
        set_engine(None)
        self.assertIsNone(get_engine())
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp, FakeDeps())
            set_engine(eng)
            self.assertIs(get_engine(), eng)
        set_engine(None)

    def test_runs_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp, FakeDeps())
            spec = make_workflow().to_dict()
            wf = eng.create(spec, activate=True)
            run_async(eng.run(wf.id))
            self.assertEqual(len(eng.runs(wf.id)), 1)
            self.assertEqual(len(eng.runs()), 1)

    def test_second_manual_run_refused_while_running(self):
        """Phase 4: the RUNNING guard in _require_runnable is real."""
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp, FakeDeps())
            spec = make_workflow().to_dict()
            spec["nodes"].insert(2, {"id": "d1", "type": "DELAY",
                                     "config": {"seconds": 1}})
            spec["edges"] = [{"source": "trg", "target": "t1"},
                             {"source": "t1", "target": "d1"},
                             {"source": "d1", "target": "end"}]
            wf = eng.create(spec, activate=True)

            async def scenario():
                task = asyncio.ensure_future(eng.run(wf.id))
                await asyncio.sleep(0.15)
                with self.assertRaises(RuntimeError):
                    await eng.run(wf.id)
                return await task

            rec = run_async(scenario())
            self.assertEqual(rec.status, RunStatus.COMPLETED.value)


if __name__ == "__main__":
    unittest.main()
