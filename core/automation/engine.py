"""Automation engine facade (Phases 5-9 integration).

Owns store · catalog · validator · runtime · trigger engine and exposes the
whole UNDERSTAND→…→REMEMBER lifecycle as one injectable object. main.py
constructs exactly one instance with callables wired to the EXISTING
systems (dispatcher gate, confirm banner, verifier, event bus, gemini,
memory, notifications) — this module never imports main or ui, so tests
build it with fakes.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .capability import CapabilityCatalog
from .risk import analyze
from .runtime import AutomationRuntime, RunRecord
from .schema import (Workflow, WorkflowStatus, new_id, now)
from .store import AutomationStore
from .triggers import TriggerEngine
from .validate import ValidationReport, validate

_ENGINE: "AutomationEngine | None" = None


def set_engine(engine: "AutomationEngine | None") -> None:
    global _ENGINE
    _ENGINE = engine


def get_engine() -> "AutomationEngine | None":
    return _ENGINE


class AutomationEngine:
    def __init__(self, *, store: AutomationStore | None = None,
                 catalog: CapabilityCatalog | None = None,
                 ledger: Any = None,
                 tool_call: Callable | None = None,
                 confirm: Callable | None = None,
                 ai_call: Callable | None = None,
                 memory_save: Callable | None = None,
                 memory_recall: Callable | None = None,
                 notify: Callable | None = None,
                 emit: Callable | None = None,
                 log: Callable | None = None,
                 tick_interval_s: float = 5.0) -> None:
        self.store = store if store is not None else AutomationStore()
        self.catalog = catalog if catalog is not None else CapabilityCatalog()
        self.ledger = ledger
        self._log = log or (lambda msg: None)
        self.runtime = AutomationRuntime(
            tool_call=tool_call, confirm=confirm, ai_call=ai_call,
            memory_save=memory_save, memory_recall=memory_recall,
            notify=notify, emit=emit, store=self.store)
        self.triggers = TriggerEngine(self.store)
        self.tick_interval_s = tick_interval_s
        self._started_at = time.time()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._ticker: asyncio.Task | None = None

    # -------------------------------------------------------- capability

    def refresh_catalog(self, action_registry: Any = None,
                        plugin_registry: Any = None,
                        inline_meta: dict | None = None) -> None:
        if action_registry is not None:
            self.catalog._actions = action_registry
        if plugin_registry is not None:
            self.catalog._plugins = plugin_registry
        if inline_meta is not None:
            self.catalog._inline = dict(inline_meta)
        self.catalog.refresh()

    # ----------------------------------------------------------- validate

    def validate(self, workflow: Workflow) -> ValidationReport:
        return validate(workflow, self.catalog, self.ledger)

    # ------------------------------------------------------- CRUD / state

    def save(self, workflow: Workflow, *, activate: bool = False) -> Workflow:
        analyze(workflow, self.catalog)
        if activate:
            report = self.validate(workflow)
            if not report.valid:
                raise ValueError("workflow is not valid: " +
                                 "; ".join(report.errors[:5]))
            if report.status == "REQUIRES_PERMISSION":
                raise PermissionError(
                    "missing permissions: " +
                    ", ".join(report.denied_permissions))
            workflow.status = WorkflowStatus.ACTIVE.value
            self.triggers.baseline(workflow)
        return self.store.save(workflow, bump_version=True)

    def create(self, spec: dict, *, activate: bool = False) -> Workflow:
        spec = dict(spec or {})
        spec.setdefault("id", new_id("auto"))
        wf = Workflow.from_dict(spec)
        if not wf.name:
            raise ValueError("workflow needs a name")
        return self.save(wf, activate=activate)

    def list(self, status: str | None = None) -> list[dict]:
        return self.store.list(status=status)

    def get(self, wf_id: str) -> Workflow | None:
        return self.store.load(wf_id)

    def delete(self, wf_id: str) -> bool:
        active = self.runtime.active_run_for(wf_id)
        if active:
            self.runtime.cancel(active)
        return self.store.delete(wf_id)

    def set_status(self, wf_id: str, status: str) -> Workflow | None:
        wf = self.store.load(wf_id)
        if wf is None:
            return None
        if status == WorkflowStatus.ACTIVE.value:
            return self.save(wf, activate=True)
        wf.status = status
        return self.store.save(wf, bump_version=True)

    def pause(self, wf_id: str) -> tuple[bool, str]:
        run_id = self.runtime.active_run_for(wf_id)
        if run_id:
            return self.runtime.pause(run_id), f"run {run_id} paused"
        wf = self.set_status(wf_id, WorkflowStatus.PAUSED.value)
        if wf is None:
            return False, "automation not found"
        return True, f"'{wf.name}' paused"

    def resume(self, wf_id: str) -> tuple[bool, str]:
        run_id = self.runtime.active_run_for(wf_id)
        if run_id:
            return self.runtime.resume(run_id), f"run {run_id} resumed"
        wf = self.set_status(wf_id, WorkflowStatus.ACTIVE.value)
        if wf is None:
            return False, "automation not found"
        return True, f"'{wf.name}' active again"

    def stop(self, wf_id: str) -> tuple[bool, str]:
        run_id = self.runtime.active_run_for(wf_id)
        if not run_id:
            return False, "nothing is running"
        return self.runtime.cancel(run_id), f"run {run_id} stopping"

    # -------------------------------------------------------------- runs

    def _require_runnable(self, wf: Workflow) -> None:
        """Validation + permission gate shared by run() and start_run()."""
        report = self.validate(wf)
        if not report.valid:
            raise ValueError("workflow is not valid: " +
                             "; ".join(report.errors[:5]))
        if report.status == "REQUIRES_PERMISSION":
            raise PermissionError("missing permissions: " +
                                  ", ".join(report.denied_permissions))
        if wf.status == WorkflowStatus.RUNNING.value:
            raise RuntimeError("workflow is already running")
        # The status field alone never catches a live run: execution sets
        # the RUN record to RUNNING, not wf.status, so the check above was
        # effectively dead. Consult the runtime for a run actually in
        # flight — the guard that check was meant to be (Phase 4).
        if self.runtime.active_run_for(wf.id):
            raise RuntimeError("workflow is already running")

    async def run(self, wf_id: str, *, mode: str = "live",
                  trigger: str = "manual",
                  variables: dict | None = None,
                  run_id: str | None = None) -> RunRecord:
        wf = self.store.load(wf_id)
        if wf is None:
            raise KeyError(f"automation {wf_id!r} not found")
        self._require_runnable(wf)
        return await self.runtime.execute(
            wf, run_id=run_id, mode=mode, trigger=trigger,
            variables=variables)

    def start_run(self, wf_id: str, *, mode: str = "live",
                  trigger: str = "manual",
                  variables: dict | None = None) -> str:
        """Schedule a run on the engine loop from any thread; returns the
        run_id immediately after fast-failing on not-found/invalid/denied."""
        wf = self.store.load(wf_id)
        if wf is None:
            raise KeyError(f"automation {wf_id!r} not found")
        self._require_runnable(wf)
        loop = self.loop
        if loop is None or loop.is_closed():
            raise RuntimeError("automation engine loop is not running yet")
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        asyncio.run_coroutine_threadsafe(
            self.run(wf_id, mode=mode, trigger=trigger,
                     variables=variables, run_id=run_id), loop)
        return run_id

    async def test(self, wf_id: str) -> RunRecord:
        return await self.run(wf_id, mode="dry", trigger="test")

    # ---------------------------------------------------------- scheduler

    async def tick(self) -> list[RunRecord]:
        """Fire due triggered workflows; returns runs started this tick."""
        started: list[RunRecord] = []
        try:
            summaries = self.store.list()
        except Exception:
            return started
        due_input: list[Workflow] = []
        by_id: dict[str, Workflow] = {}
        for s in summaries:
            if s.get("status") != WorkflowStatus.ACTIVE.value:
                continue
            if s.get("trigger") == "manual":
                continue
            wf = self.store.load(s.get("id"))
            if wf is None:
                continue
            due_input.append(wf)
            by_id[wf.id] = wf
        for wf, reason in self.triggers.due(due_input):
            if self.runtime.active_run_for(wf.id):
                continue
            self.triggers.mark_fired(wf, reason)
            self._log(f"[Automation] firing '{wf.name}' ({reason})")
            try:
                rec = await self.run(wf.id, mode="live", trigger=reason)
                started.append(rec)
            except Exception as exc:                                # noqa: BLE001
                self._log(f"[Automation] trigger failed for '{wf.name}': {exc}")
        return started

    def start_ticker(self, emit_log: Callable | None = None) -> None:
        """Spawn the background tick loop (call from the asyncio thread)."""
        loop = asyncio.get_running_loop()
        self.loop = loop

        async def _loop() -> None:
            while True:
                try:
                    await asyncio.sleep(self.tick_interval_s)
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:                            # noqa: BLE001
                    self._log(f"[Automation] tick error: {exc}")
        self._ticker = loop.create_task(_loop())

    # ------------------------------------------------------ introspection

    def explain(self, wf_id: str) -> str:
        wf = self.store.load(wf_id)
        if wf is None:
            return "No such automation."
        risk = analyze(wf, self.catalog)
        lines = [f"{wf.name} (v{wf.version}) — {wf.status}",
                 wf.description or "(no description)",
                 f"trigger: {wf.trigger.type} {wf.trigger.config or ''}".rstrip(),
                 f"risk: {risk.risk_level}; permissions: "
                 f"{', '.join(risk.required_permissions) or 'none'}",
                 "steps:"]
        body = wf.loop_body_ids()
        for i, n in enumerate([x for x in wf.nodes if x.id not in body], 1):
            detail = n.config.get("tool") or n.config.get("operation") or \
                n.config.get("op") or n.config.get("title") or ""
            lines.append(f"  {i}. [{n.type}] {n.name or n.id} "
                         f"{detail} (risk={n.risk})".rstrip())
        stats = wf.statistics or {}
        lines.append(f"runs: {stats.get('runs', 0)} "
                     f"· completed {stats.get('completed', 0)} "
                     f"· failed {stats.get('failed', 0)}")
        return "\n".join(lines)

    def status_summary(self) -> dict:
        items = self.store.list()
        return {
            "total": len(items),
            "active": sum(1 for i in items if i.get("status") == "ACTIVE"),
            "running": len(self.runtime.active_run_ids()),
            "by_status": _count_by(items, "status"),
        }

    def runs(self, wf_id: str | None = None, limit: int = 30) -> list[dict]:
        if wf_id:
            return self.store.list_runs(wf_id, limit=limit)
        return self.store.all_runs(limit=limit)


def _count_by(items: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for i in items:
        k = str(i.get(key, ""))
        out[k] = out.get(k, 0) + 1
    return out
