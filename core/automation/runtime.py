"""Automation runtime (Phase 5 / spec §17-21).

asyncio executor over a validated Workflow: sequence, branching, bounded
loops, retries, timeouts, cancellation, pause/resume, approval waits,
execute→observe→verify, structured results and idempotency.

HARD RULES (spec §0):
* never simulate success — a LIVE tool node only succeeds if the injected
  tool executor says so AND (when declared) the verifier agrees;
* DRY RUN never touches real tools/LLM/memory/notifications — pure nodes
  (transform/condition) still execute so the preview is honest, and every
  simulated step is flagged metadata.simulated;
* dangerous nodes are never auto-retried;
* the runtime has no registry of its own — every side effect flows through
  the callables the engine injects (which main.py wires to the EXISTING
  dispatcher gate, verifier, confirm banner and event bus).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .schema import (
    Node, NodeResult, NodeStatus, NodeType, RunStatus, VerificationState,
    Workflow, resolve_path, set_path,
)

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
PURE_NODE_TYPES = frozenset({NodeType.TRIGGER.value, NodeType.CONDITION.value,
                             NodeType.TRANSFORM.value, NodeType.END.value,
                             NodeType.VERIFICATION.value})
MAX_MESSAGE_CHARS = 4000


@dataclass
class StepRecord:
    node_id: str
    node_type: str
    name: str = ""
    status: str = NodeStatus.PENDING.value
    attempt: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_ms: float = 0.0
    result: NodeResult = field(default_factory=NodeResult)

    def to_dict(self) -> dict:
        return {"node_id": self.node_id, "node_type": self.node_type,
                "name": self.name, "status": self.status, "attempt": self.attempt,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "duration_ms": self.duration_ms, "result": self.result.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "StepRecord":
        return cls(node_id=str(d.get("node_id", "")),
                   node_type=str(d.get("node_type", "")),
                   name=str(d.get("name", "")),
                   status=str(d.get("status", "PENDING")),
                   attempt=int(d.get("attempt", 0) or 0),
                   started_at=float(d.get("started_at", 0) or 0),
                   finished_at=float(d.get("finished_at", 0) or 0),
                   duration_ms=float(d.get("duration_ms", 0) or 0),
                   result=NodeResult.from_dict(d.get("result") or {}))


@dataclass
class RunRecord:
    run_id: str
    workflow_id: str
    version: int = 1
    status: str = RunStatus.RUNNING.value
    mode: str = "live"              # live | dry
    trigger: str = "manual"
    started_at: float = 0.0
    finished_at: float = 0.0
    steps: list[StepRecord] = field(default_factory=list)
    error: dict | None = None
    stats: dict = field(default_factory=dict)
    variables: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, (end - self.started_at) * 1000.0)

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "workflow_id": self.workflow_id,
                "version": self.version, "status": self.status, "mode": self.mode,
                "trigger": self.trigger, "started_at": self.started_at,
                "finished_at": self.finished_at,
                "steps": [s.to_dict() for s in self.steps],
                "error": self.error, "stats": self.stats,
                "variables": self.variables, "notes": list(self.notes)}

    @classmethod
    def from_dict(cls, d: dict) -> "RunRecord":
        return cls(run_id=str(d.get("run_id", "")),
                   workflow_id=str(d.get("workflow_id", "")),
                   version=int(d.get("version", 1) or 1),
                   status=str(d.get("status", RunStatus.RUNNING.value)),
                   mode=str(d.get("mode", "live")),
                   trigger=str(d.get("trigger", "manual")),
                   started_at=float(d.get("started_at", 0) or 0),
                   finished_at=float(d.get("finished_at", 0) or 0),
                   steps=[StepRecord.from_dict(s) for s in (d.get("steps") or [])],
                   error=d.get("error"),
                   stats=dict(d.get("stats") or {}),
                   variables=dict(d.get("variables") or {}),
                   notes=list(d.get("notes") or []))


class _RunCancelled(Exception):
    pass


@dataclass
class _Control:
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    pause: asyncio.Event = field(default_factory=asyncio.Event)
    record: RunRecord | None = None
    on_step: Callable | None = None


class AutomationRuntime:
    """Executes workflows. All side effects arrive via injected callables."""

    def __init__(self, *, tool_call: Callable | None = None,
                 confirm: Callable | None = None,
                 ai_call: Callable | None = None,
                 memory_save: Callable | None = None,
                 memory_recall: Callable | None = None,
                 notify: Callable | None = None,
                 emit: Callable | None = None,
                 store: Any = None,
                 sleep: Callable | None = None) -> None:
        self._tool_call = tool_call
        self._confirm = confirm
        self._ai_call = ai_call
        self._memory_save = memory_save
        self._memory_recall = memory_recall
        self._notify = notify
        self._emit = emit
        self._store = store
        self._sleep = sleep or asyncio.sleep
        self._runs: dict[str, _Control] = {}

    # ------------------------------------------------------------ controls

    def active_run_ids(self) -> list[str]:
        return list(self._runs)

    def active_run_for(self, workflow_id: str) -> str | None:
        for rid, ctl in self._runs.items():
            if ctl.record and ctl.record.workflow_id == workflow_id:
                return rid
        return None

    def cancel(self, run_id: str) -> bool:
        ctl = self._runs.get(run_id)
        if not ctl:
            return False
        ctl.cancel.set()
        ctl.pause.set()
        return True

    def pause(self, run_id: str) -> bool:
        ctl = self._runs.get(run_id)
        if not ctl:
            return False
        ctl.pause.clear()
        if ctl.record and ctl.record.status == RunStatus.RUNNING.value:
            ctl.record.status = RunStatus.PAUSED.value
            self._emit_event("run_paused", ctl.record.workflow_id, run_id,
                             {"status": RunStatus.PAUSED.value})
        return True

    def resume(self, run_id: str) -> bool:
        ctl = self._runs.get(run_id)
        if not ctl:
            return False
        if ctl.record and ctl.record.status == RunStatus.PAUSED.value:
            ctl.record.status = RunStatus.RUNNING.value
            self._emit_event("run_resumed", ctl.record.workflow_id, run_id,
                             {"status": RunStatus.RUNNING.value})
        ctl.pause.set()
        return True

    # ------------------------------------------------------------- execute

    async def execute(self, workflow: Workflow, *, run_id: str | None = None,
                      mode: str = "live", trigger: str = "manual",
                      variables: dict | None = None,
                      on_step: Callable | None = None) -> RunRecord:
        run = RunRecord(
            run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
            workflow_id=workflow.id, version=int(workflow.version or 1),
            status=RunStatus.RUNNING.value,
            mode="dry" if mode == "dry" else "live",
            trigger=trigger, started_at=time.time(),
            variables=dict(variables or {}),
        )
        ctl = _Control(record=run, on_step=on_step)
        ctl.pause.set()
        self._runs[run.run_id] = ctl
        self._emit_event("run_started", workflow.id, run.run_id,
                         {"mode": run.mode, "trigger": trigger,
                          "name": workflow.name})
        if self._store is not None:
            try:
                self._store.record_run(run)
            except Exception:
                pass

        context: dict[str, Any] = {"vars": dict(workflow.variables or {}),
                                   "nodes": {}}
        if variables:
            context["vars"].update(variables)

        try:
            trigger_node = next(n for n in workflow.trigger_nodes())
            await self._walk(workflow, trigger_node, context, run, ctl)
            if run.status == RunStatus.RUNNING.value:
                run.status = RunStatus.COMPLETED.value
        except _RunCancelled:
            run.status = RunStatus.CANCELLED.value
            run.notes.append("cancelled by user")
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELLED.value
            raise
        except _NodeFailed as nf:
            err = nf.to_dict()
            if nf.code == "approval_denied":
                run.status = RunStatus.CANCELLED.value
                run.notes.append("stopped at approval — user said no")
            else:
                run.status = RunStatus.FAILED.value
            executed = {s.node_id for s in run.steps}
            remaining = [n.name or n.id for n in workflow.nodes
                         if n.id not in executed and
                         n.id not in workflow.loop_body_ids()]
            err["completed"] = _completed_summary(run)
            err["not_executed"] = ", ".join(remaining)
            err["what"] = err.get("what") or (
                f"Step '{nf.node_id}' stopped the workflow: {nf.message}")
            err["why"] = err.get("why") or nf.message
            err["next"] = err.get("next") or (
                "Fix the reported cause, then retry — completed steps are "
                "listed above and were not repeated.")
            run.error = err
        except Exception as exc:                                   # noqa: BLE001
            run.status = RunStatus.FAILED.value
            run.error = {"message": f"runtime error: {exc}",
                         "node_id": "", "code": "runtime_error",
                         "what": "The workflow runtime hit an unexpected error.",
                         "why": str(exc), "completed": _completed_summary(run),
                         "not_executed": "", "next": "Check the run history and retry."}
        finally:
            run.finished_at = time.time()
            run.stats.update({
                "duration_ms": run.duration_ms,
                "nodes_executed": len(run.steps),
                "retries": sum(max(0, s.attempt - 1) for s in run.steps),
                "failures": sum(1 for s in run.steps
                                if s.status in (NodeStatus.FAILED.value,
                                                NodeStatus.TIMEOUT.value)),
                "verified": sum(1 for s in run.steps
                                if s.result.verification == VerificationState.VERIFIED.value),
            })
            self._runs.pop(run.run_id, None)
            if self._store is not None:
                try:
                    self._store.record_run(run)
                    self._store.update_statistics(workflow.id, run)
                except Exception:
                    pass
            self._emit_event("run_" + run.status.lower(), workflow.id, run.run_id,
                             {"status": run.status, "mode": run.mode,
                              "error": (run.error or {}).get("message", "")})
        return run

    # ------------------------------------------------------------ traversal

    async def _walk(self, wf: Workflow, node: Node | None,
                    context: dict, run: RunRecord, ctl: _Control) -> None:
        current = node
        guard = 0
        while current is not None:
            guard += 1
            if guard > len(wf.nodes) + 8:
                raise _NodeFailed(node_id=current.id if current else "",
                                  message="traversal exceeded node budget",
                                  code="loop_guard")
            await self._check_control(ctl)

            if current.type == NodeType.CONDITION.value:
                branch = await self._exec_node(wf, current, context, run, ctl)
                if not branch.success:
                    raise _failure(current, branch)
                want = "true" if branch.data.get("condition") else "false"
                cond_id = current.id
                nxt = _branch_target(wf, cond_id, want)
                if nxt is None:
                    run.notes.append(
                        f"condition {cond_id} has no '{want}' branch — run ended")
                    return
                current = wf.node(nxt)
                continue

            if current.type == NodeType.LOOP.value:
                await self._run_loop(wf, current, context, run, ctl)
                nxt = _plain_target(wf, current.id)
                current = wf.node(nxt) if nxt else None
                continue

            result = await self._exec_node(wf, current, context, run, ctl)
            if not result.success:
                raise _failure(current, result)
            if current.type == NodeType.END.value:
                run.notes.append(result.message or "end")
                return
            nxt = _plain_target(wf, current.id)
            current = wf.node(nxt) if nxt else None

    async def _run_loop(self, wf: Workflow, loop_node: Node,
                        context: dict, run: RunRecord, ctl: _Control) -> None:
        items = resolve_path(context, str(loop_node.config.get("items_path", "")),
                             default=[])
        if items is None:
            items = []
        if not isinstance(items, list):
            items = [items]
        try:
            cap = int(loop_node.config.get("max_iterations", 50))
        except (TypeError, ValueError):
            cap = 50
        cap = max(1, min(cap, 1000))
        items = items[:cap]
        item_var = str(loop_node.config.get("item_var", "item"))
        on_error = str(loop_node.config.get("on_error", "stop"))
        body_ids = [str(b) for b in (loop_node.config.get("body") or [])]

        loop_step = StepRecord(node_id=loop_node.id, node_type=loop_node.type,
                               name=loop_node.name, status=NodeStatus.RUNNING.value,
                               started_at=time.time(), attempt=1)
        run.steps.append(loop_step)
        self._emit_event("node_started", wf.id, run.run_id,
                         {"node": loop_node.id, "type": loop_node.type})
        executed = 0
        for index, item in enumerate(items):
            await self._check_control(ctl)
            context["vars"][item_var] = item
            for bid in body_ids:
                await self._check_control(ctl)
                body_node = wf.node(bid)
                if body_node is None:
                    continue
                result = await self._exec_node(wf, body_node, context, run, ctl)
                if not result.success:
                    executed += 1
                    if on_error == "continue":
                        run.notes.append(
                            f"loop {loop_node.id} item {index}: {bid} failed, continuing")
                        continue
                    loop_step.finished_at = time.time()
                    loop_step.duration_ms = (loop_step.finished_at - loop_step.started_at) * 1000
                    loop_step.status = NodeStatus.FAILED.value
                    loop_step.result = NodeResult(
                        success=False, status="error",
                        message=f"body node {bid} failed on item {index}",
                        error_code="loop_body_failed",
                        execution_time_ms=loop_step.duration_ms)
                    raise _failure(loop_node, loop_step.result)
            executed += 1
        loop_step.finished_at = time.time()
        loop_step.duration_ms = (loop_step.finished_at - loop_step.started_at) * 1000
        loop_step.status = NodeStatus.SUCCEEDED.value
        loop_step.result = NodeResult(
            success=True, status="ok",
            message=f"iterated {executed}/{len(items)} item(s)",
            data={"iterations": executed, "capped": len(items) >= cap},
            execution_time_ms=loop_step.duration_ms)
        context["nodes"][loop_node.id] = _context_payload(loop_step.result)
        await self._after_step(loop_step, ctl)

    # ------------------------------------------------------------ one node

    async def _exec_node(self, wf: Workflow, node: Node, context: dict,
                         run: RunRecord, ctl: _Control) -> NodeResult:
        await self._check_control(ctl)
        if run.mode == "dry" and node.type not in PURE_NODE_TYPES:
            result = self._simulate(node)
            step = self._record(run, ctl, node, result, attempt=1, started=time.time())
            return result

        if node.type in (NodeType.TRIGGER.value, NodeType.END.value):
            result = NodeResult(success=True, status="ok",
                                message=node.name or node.type.lower())
            if node.type == NodeType.END.value:
                result.data = dict(resolve_path(context, "$.vars") or {})
            self._record(run, ctl, node, result, attempt=1, started=time.time())
            return result

        attempts = max(1, int(node.retry.max_attempts))
        if node.risk in ("dangerous", "forbidden") or node.type == NodeType.APPROVAL.value:
            attempts = 1
        started = time.time()
        last: NodeResult | None = None
        for attempt in range(1, attempts + 1):
            await self._check_control(ctl)
            last_started = time.time()
            try:
                last = await self._run_by_type(wf, node, context, run, ctl)
            except asyncio.TimeoutError:
                last = NodeResult(success=False, status="timeout",
                                  message=f"node {node.id} timed out after {node.timeout_s}s",
                                  error_code="timeout")
            except _RunCancelled:
                raise
            except _NodeFailed:
                raise
            except Exception as exc:                               # noqa: BLE001
                last = NodeResult(success=False, status="error",
                                  message=str(exc), error_code="node_error")
            last.execution_time_ms = (time.time() - last_started) * 1000.0
            if last.success:
                break
            if attempt < attempts:
                self._emit_event("node_retry", wf.id, run.run_id,
                                 {"node": node.id, "attempt": attempt})
                if node.retry.backoff_s > 0:
                    await self._sleep(node.retry.backoff_s)
        step = self._record(run, ctl, node, last, attempt=attempt,
                            started=started)
        context["nodes"][node.id] = _context_payload(last)
        return last

    async def _run_by_type(self, wf: Workflow, node: Node, context: dict,
                           run: RunRecord, ctl: _Control) -> NodeResult:
        ntype = node.ntype
        if ntype is NodeType.TOOL:
            return await self._node_tool(node, context, run, ctl)
        if ntype is NodeType.AI:
            return await self._node_ai(node, context, run, ctl)
        if ntype is NodeType.CONDITION:
            return _node_condition(node, context)
        if ntype is NodeType.TRANSFORM:
            return _node_transform(node, context)
        if ntype is NodeType.DELAY:
            secs = int(node.config.get("seconds", 1))
            await self._sleep(secs)
            return NodeResult(success=True, status="ok",
                              message=f"waited {secs}s")
        if ntype is NodeType.APPROVAL:
            return await self._node_approval(node, context, run, ctl)
        if ntype is NodeType.MEMORY:
            return await self._node_memory(node, context)
        if ntype is NodeType.NOTIFICATION:
            return await self._node_notification(node, context, run)
        if ntype is NodeType.VERIFICATION:
            return _node_verification(node, context, run)
        return NodeResult(success=False, status="error",
                          message=f"unsupported node type {node.type}",
                          error_code="unsupported")

    # -- tool node: the ONLY path to real side effects --------------------

    async def _node_tool(self, node: Node, context: dict, run: RunRecord,
                         ctl: _Control) -> NodeResult:
        tool = str(node.config.get("tool", ""))
        args = dict(node.config.get("args") or {})
        args = _render_args(args, context)
        if run.mode == "dry":
            return self._simulate(node)

        key = _idempotency_key(node, args)
        if key and node.risk in ("dangerous", "forbidden") and self._store is not None:
            try:
                if self._store.idempotency_done(run.workflow_id, key):
                    return NodeResult(
                        success=True, status="ok", message=(
                            "skipped — this exact external action already ran "
                            "in a previous execution (idempotency guard)"),
                        data={"simulated": False, "idempotent_skip": True},
                        metadata={"idempotency_key": key})
            except Exception:
                pass

        if self._tool_call is None:
            return NodeResult(success=False, status="refused",
                              message="automation runtime has no tool executor",
                              error_code="no_executor")

        outcome = await _maybe_await(
            self._tool_call(tool, args, int(node.timeout_s)),
            float(node.timeout_s) + 1.0)
        if isinstance(outcome, NodeResult):
            result = outcome
        else:
            o = outcome or {}
            result = NodeResult(
                success=bool(o.get("ok", False)),
                status=str(o.get("status", "ok" if o.get("ok") else "error")),
                message=str(o.get("text", ""))[:MAX_MESSAGE_CHARS],
                verification=str(o.get("verification", "none")),
                reversible=bool(o.get("reversible", False)),
                error_code=str(o.get("error", "")),
                data=dict(o.get("data") or {}))
            if o.get("timed_out"):
                result.status, result.success, result.error_code = "timeout", False, "timeout"
            if o.get("refused"):
                result.status, result.success, result.error_code = "refused", False, "refused"
        result.data.setdefault("output", result.message[:MAX_MESSAGE_CHARS])
        if key and result.success and node.risk in ("dangerous", "forbidden") \
                and self._store is not None:
            try:
                self._store.mark_idempotency(run.workflow_id, key, run.run_id)
            except Exception:
                pass
        return result

    # -- other side-effecting nodes ---------------------------------------

    async def _node_ai(self, node: Node, context: dict, run: RunRecord,
                       ctl: _Control) -> NodeResult:
        if run.mode == "dry":
            return self._simulate(node)
        if self._ai_call is None:
            return NodeResult(success=False, status="error",
                              message="AI engine unavailable",
                              error_code="no_ai")
        cfg = node.config
        source = cfg.get("input_path")
        payload = resolve_path(context, source) if source else context
        text_in = _as_text(payload, limit=6000)
        try:
            max_chars = int(cfg.get("max_output_chars", 1200))
        except (TypeError, ValueError):
            max_chars = 1200
        out = await _maybe_await(
            self._ai_call(operation=str(cfg.get("operation", "analyze")),
                          objective=str(cfg.get("objective", "")),
                          input_text=text_in, max_chars=max_chars,
                          timeout_s=int(node.timeout_s)),
            float(node.timeout_s) + 5.0)
        if isinstance(out, NodeResult):
            return out
        out_str = str(out or "").strip()
        if not out_str:
            return NodeResult(success=False, status="error",
                              message="AI returned nothing", error_code="empty_ai")
        data: dict[str, Any] = {"output": out_str[:MAX_MESSAGE_CHARS]}
        if isinstance(out, dict):
            data.update(out)
        result = NodeResult(success=True, status="ok",
                            message=out_str[:MAX_MESSAGE_CHARS], data=data)
        out_field = cfg.get("out")
        if out_field:
            set_path(context, str(out_field), data["output"])
        return result

    async def _node_approval(self, node: Node, context: dict,
                             run: RunRecord, ctl: _Control) -> NodeResult:
        title = _render(str(node.config.get("title", "Approval required")), context)
        detail = _render(str(node.config.get("detail", "")), context)
        run.status = RunStatus.WAITING_APPROVAL.value
        if self._store is not None:
            try:
                self._store.record_run(run)
            except Exception:
                pass
        self._emit_event("approval_waiting", run.workflow_id, run.run_id,
                         {"node": node.id, "title": title, "detail": detail})
        if run.mode == "dry":
            run.status = RunStatus.RUNNING.value
            return self._simulate(node, message="approval would be requested here")
        if self._confirm is None:
            run.status = RunStatus.RUNNING.value
            return NodeResult(success=False, status="refused",
                              message="no confirmation gate bound",
                              error_code="no_confirm")
        try:
            accepted = await _maybe_await(self._confirm(title, detail),
                                          float(node.timeout_s))
        except asyncio.TimeoutError:
            accepted = False
        run.status = RunStatus.RUNNING.value
        if accepted:
            return NodeResult(success=True, status="ok",
                              message="approved", data={"approved": True})
        return NodeResult(success=False, status="refused",
                          message="approval denied — workflow stopped here, "
                                  "nothing after this point was executed",
                          error_code="approval_denied",
                          data={"approved": False})

    async def _node_memory(self, node: Node, context: dict) -> NodeResult:
        action = str(node.config.get("action", "save"))
        if action == "save":
            if self._memory_save is None:
                return NodeResult(success=False, status="error",
                                  message="memory unavailable",
                                  error_code="no_memory")
            key = _render(str(node.config.get("key", "")), context) or "automation"
            source = node.config.get("source") or node.config.get("path")
            value = resolve_path(context, str(source)) if source else None
            msg = await _maybe_await(
                self._memory_save(key, _as_text(value, limit=380)), 15.0)
            return NodeResult(success=True, status="ok",
                              message=str(msg or "saved"),
                              data={"key": key})
        if self._memory_recall is None:
            return NodeResult(success=False, status="error",
                              message="memory unavailable", error_code="no_memory")
        query = _render(str(node.config.get("query", "")), context)
        out = await _maybe_await(self._memory_recall(query), 15.0)
        text = str(out or "")
        return NodeResult(success=True, status="ok",
                          message=text[:MAX_MESSAGE_CHARS],
                          data={"output": text[:MAX_MESSAGE_CHARS]})

    async def _node_notification(self, node: Node, context: dict,
                                 run: RunRecord) -> NodeResult:
        if run.mode == "dry":
            return self._simulate(node)
        message = _render(str(node.config.get("message", "")), context)
        if self._notify is None:
            return NodeResult(success=True, status="ok",
                              message=f"[log only] {message}",
                              data={"logged": True})
        await _maybe_await(self._notify(message), 15.0)
        return NodeResult(success=True, status="ok", message=message,
                          data={"notified": True})

    # ---------------------------------------------------------- bookkeeping

    def _record(self, run: RunRecord, ctl: _Control, node: Node,
                result: NodeResult, attempt: int, started: float) -> StepRecord:
        finished = time.time()
        status = NodeStatus.SUCCEEDED.value if result.success else (
            NodeStatus.TIMEOUT.value if result.status == "timeout"
            else NodeStatus.FAILED.value)
        if result.metadata.get("simulated"):
            status = NodeStatus.SUCCEEDED.value if result.success else status
        step = StepRecord(
            node_id=node.id, node_type=node.type, name=node.name,
            status=status, attempt=attempt, started_at=started,
            finished_at=finished,
            duration_ms=(finished - started) * 1000.0, result=result)
        run.steps.append(step)
        self._emit_event("node_finished", run.workflow_id, run.run_id,
                         {"node": node.id, "type": node.type,
                          "ok": result.success, "status": status,
                          "verification": result.verification,
                          "message": result.message[:160]})
        if ctl.on_step is not None:
            try:
                ctl.on_step(step)
            except Exception:
                pass
        return step

    async def _after_step(self, step: StepRecord, ctl: _Control) -> None:
        if ctl.on_step is not None:
            try:
                ctl.on_step(step)
            except Exception:
                pass

    def _simulate(self, node: Node, message: str = "") -> NodeResult:
        if not message:
            if node.type == NodeType.TOOL.value:
                tool = node.config.get("tool", "")
                message = f"[DRY RUN] would call {tool} with the configured arguments"
            else:
                message = f"[DRY RUN] would run {node.type.lower()} node {node.id}"
        return NodeResult(
            success=True, status="ok", message=message,
            data={"simulated": True},
            metadata={"simulated": True, "dry": True})

    async def _check_control(self, ctl: _Control) -> None:
        if ctl.cancel.is_set():
            raise _RunCancelled()
        if not ctl.pause.is_set():
            while not ctl.pause.is_set():
                if ctl.cancel.is_set():
                    raise _RunCancelled()
                await asyncio.sleep(0.05)

    def _emit_event(self, kind: str, wf_id: str, run_id: str,
                    data: dict) -> None:
        if self._emit is None:
            return
        payload = dict(data)
        payload.update({"kind": kind, "workflow_id": wf_id, "run_id": run_id})
        try:
            self._emit(kind, payload, run_id)
        except Exception:
            pass


class _NodeFailed(Exception):
    def __init__(self, node_id: str, message: str, code: str,
                 node_type: str = "", what: str = "", why: str = "",
                 completed: str = "", not_executed: str = "",
                 next_step: str = "") -> None:
        super().__init__(message)
        self.node_id = node_id
        self.message = message
        self.code = code
        self.node_type = node_type
        self.what = what
        self.why = why
        self.completed = completed
        self.not_executed = not_executed
        self.next = next_step

    def to_dict(self) -> dict:
        return {"message": self.message, "node_id": self.node_id,
                "code": self.code, "node_type": self.node_type,
                "what": self.what, "why": self.why,
                "completed": self.completed, "not_executed": self.not_executed,
                "next": self.next}


def _failure(node: Node, result: NodeResult) -> _NodeFailed:
    if result.status == "approval_denied" or result.error_code == "approval_denied":
        code = "approval_denied"
    elif result.status == "refused":
        code = "refused"
    else:
        code = result.error_code or "node_failed"
    what = (f"Step '{node.name or node.id}' failed because "
            f"{result.message or 'the step reported no detail'}")
    return _NodeFailed(
        node_id=node.id, message=result.message or "step failed", code=code,
        node_type=node.type, what=what,
        why=result.message or "", completed="", not_executed="",
        next_step="Review the run history; fix the reported cause and retry.")


def _completed_summary(run: RunRecord) -> str:
    done = [s.node_id for s in run.steps
            if s.status in (NodeStatus.SUCCEEDED.value, NodeStatus.VERIFIED.value)]
    return ", ".join(done) if done else "nothing"


def _branch_target(wf: Workflow, node_id: str, when: str) -> str | None:
    for e in wf.edges_from(node_id):
        if e.when == when:
            return e.target
    return None


def _plain_target(wf: Workflow, node_id: str) -> str | None:
    for e in wf.edges_from(node_id):
        if not e.when:
            return e.target
    return None


def _context_payload(result: NodeResult) -> dict:
    return {"message": result.message, "success": result.success,
            "data": result.data, "verification": result.verification}


# ------------------------------------------------------------ pure nodes

def _node_condition(node: Node, context: dict) -> NodeResult:
    cfg = node.config
    raw = str(cfg.get("left", ""))
    if "left_value" in cfg:
        left = cfg.get("left_value")
    elif raw.startswith(("$", "vars.", "nodes.")) or "." in raw:
        left = resolve_path(context, raw)
    else:
        left = raw
    op = str(cfg.get("op", "bool"))
    right = cfg.get("right")
    ok = False
    try:
        if op == "exists":
            ok = left is not None and left is not False and left != ""
        elif op == "bool":
            ok = bool(left)
        elif op == "eq":
            ok = left == right or str(left) == str(right)
        elif op == "ne":
            ok = not (left == right or str(left) == str(right))
        elif op == "contains":
            ok = (str(right) in str(left)) if left is not None else False
        elif op == "gt":
            ok = float(left) > float(right)
        elif op == "gte":
            ok = float(left) >= float(right)
        elif op == "lt":
            ok = float(left) < float(right)
        elif op == "lte":
            ok = float(left) <= float(right)
        elif op == "in":
            ok = left in (right if isinstance(right, (list, tuple)) else [])
    except (TypeError, ValueError):
        ok = False
    return NodeResult(success=True, status="ok",
                      message=f"condition → {ok}", data={"condition": ok,
                                                         "left": _clip(left),
                                                         "right": _clip(right)})


def _node_transform(node: Node, context: dict) -> NodeResult:
    cfg = node.config
    op = str(cfg.get("op", ""))
    out_field = cfg.get("out")
    value: Any = None
    try:
        if op == "extract":
            value = resolve_path(context, str(cfg.get("path", "")))
        elif op == "rename":
            value = resolve_path(context, str(cfg.get("path", "")))
            out_field = cfg.get("out") or cfg.get("to") or out_field
        elif op == "pick":
            src = resolve_path(context, str(cfg.get("path", "")), default={}) or {}
            fields = cfg.get("fields") or []
            if isinstance(src, dict):
                value = {k: src.get(k) for k in fields if k in src}
            else:
                value = src
        elif op == "set":
            value = cfg.get("value")
        elif op == "format":
            value = _render(str(cfg.get("template", "")), context)
        elif op == "filter":
            src = resolve_path(context, str(cfg.get("path", "")), default=[]) or []
            where = cfg.get("where") or {}
            field_name = str(where.get("field", ""))
            wop = str(where.get("op", "eq"))
            expect = where.get("value")
            items = src if isinstance(src, list) else [src]
            kept = []
            for it in items:
                v = it.get(field_name) if isinstance(it, dict) else it
                try:
                    if wop == "eq" and (v == expect or str(v) == str(expect)):
                        kept.append(it)
                    elif wop == "ne" and not (v == expect or str(v) == str(expect)):
                        kept.append(it)
                    elif wop == "contains" and str(expect) in str(v):
                        kept.append(it)
                    elif wop == "gt" and float(v) > float(expect):
                        kept.append(it)
                    elif wop == "lt" and float(v) < float(expect):
                        kept.append(it)
                except (TypeError, ValueError):
                    continue
            value = kept
        elif op == "merge":
            a = resolve_path(context, str(cfg.get("path", "")), default={}) or {}
            b = resolve_path(context, str(cfg.get("path2", "")), default={}) or {}
            value = {**(a if isinstance(a, dict) else {}),
                     **(b if isinstance(b, dict) else {})}
        else:
            return NodeResult(success=False, status="error",
                              message=f"unknown transform op {op!r}",
                              error_code="bad_transform")
    except Exception as exc:                                       # noqa: BLE001
        return NodeResult(success=False, status="error",
                          message=f"transform failed: {exc}",
                          error_code="transform_error")
    if out_field:
        set_path(context, str(out_field), value)
    return NodeResult(success=True, status="ok",
                      message=f"{op} → {_clip(value)}",
                      data={"value": _clip(value)})


def _node_verification(node: Node, context: dict, run: RunRecord) -> NodeResult:
    cfg = node.config
    assertion = str(cfg.get("assert", "not_empty"))
    path = str(cfg.get("path", ""))
    value = resolve_path(context, path)
    ok = False
    detail = ""
    try:
        if assertion == "exists":
            ok = value is not None
            detail = f"{path} {'exists' if ok else 'is missing'}"
        elif assertion == "not_empty":
            ok = value is not None and value != "" and value != [] and value != {}
            detail = f"{path} {'has content' if ok else 'is empty'}"
        elif assertion == "equals":
            ok = value == cfg.get("value") or str(value) == str(cfg.get("value"))
            detail = f"{path} {'==' if ok else '!='} expected value"
        elif assertion == "matches":
            ok = bool(re.search(str(cfg.get("pattern", "")), str(value or "")))
            detail = f"{path} {'matches' if ok else 'does not match'} pattern"
        elif assertion == "count_gt":
            n = len(value) if isinstance(value, (list, dict, str)) else 0
            ok = n > float(cfg.get("n", 0))
            detail = f"count {n} > {cfg.get('n')} → {ok}"
        elif assertion == "all_success":
            if isinstance(value, list):
                ok = bool(value) and all(bool(x) for x in value)
            elif isinstance(value, dict):
                ok = value.get("success") is True
            else:
                ok = value is True
            detail = f"all_success → {ok}"
        else:
            return NodeResult(success=False, status="error",
                              message=f"unknown assert {assertion!r}",
                              error_code="bad_assert")
    except Exception as exc:                                       # noqa: BLE001
        return NodeResult(success=False, status="error",
                          message=str(exc), error_code="assert_error")
    return NodeResult(success=ok,
                      status="ok" if ok else "error",
                      message=detail,
                      verification=VerificationState.VERIFIED.value if ok
                      else VerificationState.FAILED.value,
                      error_code="" if ok else "verification_failed",
                      data={"assert": assertion, "passed": ok})


# ------------------------------------------------------------- helpers

def _render(template: str, context: dict) -> str:
    """Substitute {$.path} placeholders from workflow context (never raises)."""
    if not template or "{" not in template:
        return template

    def sub(m: re.Match) -> str:
        token = m.group(1).strip()
        if token.startswith(("$", "vars.", "nodes.")):
            val = resolve_path(context, token, default="")
            return _as_text(val, limit=500)
        return m.group(0)

    return _PLACEHOLDER.sub(sub, template)


def _render_args(args: dict, context: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            out[k] = _render(v, context)
        elif isinstance(v, dict):
            out[k] = _render_args(v, context)
        elif isinstance(v, list):
            out[k] = [_render(i, context) if isinstance(i, str) else i
                      for i in v]
        else:
            out[k] = v
    return out


def _as_text(value: Any, limit: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)[:limit]
        except Exception:
            return str(value)[:limit]
    return str(value)[:limit]


def _clip(value: Any, limit: int = 300) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    if isinstance(value, list) and len(value) > 50:
        return {"__list_len__": len(value), "head": value[:50]}
    if isinstance(value, dict) and len(json.dumps(value, default=str)) > 2000:
        return {"__clipped__": True, "keys": list(value)[:50]}
    return value


def _idempotency_key(node: Node, args: dict) -> str:
    explicit = node.config.get("idempotency_key")
    if explicit:
        return str(explicit)
    try:
        blob = json.dumps({"tool": node.config.get("tool", ""), "args": args},
                          sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
    except Exception:
        return ""


async def _await_bounded(coro, timeout: float):
    return await asyncio.wait_for(coro, timeout=timeout)


async def _maybe_await(value, timeout: float):
    """Accept async OR sync injected callables; bound async ones by timeout."""
    if hasattr(value, "__await__"):
        return await asyncio.wait_for(value, timeout=timeout)
    return value
