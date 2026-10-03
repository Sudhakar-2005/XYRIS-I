"""Workflow validation (Phase 4 / spec §16).

Pure functions over Workflow + CapabilityCatalog — no side effects, no LLM.
Produces one of: VALID · INVALID · REQUIRES_CLARIFICATION · REQUIRES_PERMISSION.
The runtime refuses to activate anything that did not come back VALID.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .capability import CapabilityCatalog
from .risk import analyze, RISK_ORDER
from .schema import (
    AI_OPERATIONS, CONDITION_OPS, DEFAULT_MAX_ITERATIONS, Edge, HARD_MAX_ITERATIONS,
    MAX_DELAY_S, MAX_EDGES, MAX_NODES, MAX_NODE_TIMEOUT_S, MEMORY_ACTIONS,
    MIN_NODE_TIMEOUT_S, NodeType, REQUIRED_CONFIG, TriggerType, TRANSFORM_OPS,
    VERIFY_ASSERTS, Workflow, WorkflowStatus, new_id,
)

VALID = "VALID"
INVALID = "INVALID"
REQUIRES_CLARIFICATION = "REQUIRES_CLARIFICATION"
REQUIRES_PERMISSION = "REQUIRES_PERMISSION"

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


@dataclass
class ValidationReport:
    status: str = INVALID
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_tools: list[str] = field(default_factory=list)
    unavailable_tools: list[str] = field(default_factory=list)
    required_permissions: tuple[str, ...] = ()
    denied_permissions: tuple[str, ...] = ()
    risk_level: str = "safe"

    @property
    def valid(self) -> bool:
        return self.status == VALID

    def to_dict(self) -> dict:
        return {
            "status": self.status, "errors": list(self.errors),
            "warnings": list(self.warnings),
            "missing_tools": list(self.missing_tools),
            "unavailable_tools": list(self.unavailable_tools),
            "required_permissions": list(self.required_permissions),
            "denied_permissions": list(self.denied_permissions),
            "risk_level": self.risk_level,
        }


def validate(workflow: Workflow, catalog: CapabilityCatalog | None = None,
             ledger: Any = None) -> ValidationReport:
    report = ValidationReport()
    if workflow is None:
        report.errors.append("workflow is missing")
        return report

    _check_structure(workflow, report)
    _check_nodes(workflow, catalog, report)
    _check_edges(workflow, report)
    _check_trigger(workflow, report)

    risk = analyze(workflow, catalog)
    report.risk_level = risk.risk_level
    report.required_permissions = risk.required_permissions
    if risk.risk_level == "forbidden":
        report.errors.append("workflow contains a forbidden operation — refused")

    if ledger is not None:
        denied = [p for p in risk.required_permissions
                  if not _ledger_granted(ledger, p)]
        if denied:
            report.denied_permissions = tuple(denied)
            report.status = REQUIRES_PERMISSION

    if not report.errors:
        if report.status != REQUIRES_PERMISSION:
            report.status = VALID
    else:
        report.status = INVALID
    return report


def _ledger_granted(ledger: Any, perm: str) -> bool:
    try:
        return bool(ledger.is_granted(perm))
    except Exception:
        return True


# ------------------------------------------------------------------ checks

def _check_structure(wf: Workflow, r: ValidationReport) -> None:
    if not wf.nodes:
        r.errors.append("workflow has no nodes")
        return
    if len(wf.nodes) > MAX_NODES:
        r.errors.append(f"too many nodes ({len(wf.nodes)} > {MAX_NODES})")
    if len(wf.edges) > MAX_EDGES:
        r.errors.append(f"too many edges ({len(wf.edges)} > {MAX_EDGES})")

    seen: set[str] = set()
    for n in wf.nodes:
        if not n.id or not _ID_RE.match(n.id):
            r.errors.append(f"invalid node id {n.id!r}")
        elif n.id in seen:
            r.errors.append(f"duplicate node id {n.id!r}")
        seen.add(n.id)
        try:
            ntype = n.ntype
        except ValueError:
            r.errors.append(f"node {n.id!r} has unknown type {n.type!r}")
            continue
        for key in REQUIRED_CONFIG[ntype]:
            if key not in n.config:
                r.errors.append(f"node {n.id!r} ({ntype.value}) missing config.{key}")
        if not MIN_NODE_TIMEOUT_S <= int(n.timeout_s) <= MAX_NODE_TIMEOUT_S:
            r.errors.append(f"node {n.id!r} timeout out of range")

    triggers = [n for n in wf.nodes if n.type == NodeType.TRIGGER.value]
    if len(triggers) != 1:
        r.errors.append(f"workflow must have exactly one TRIGGER node (found {len(triggers)})")
    ends = [n for n in wf.nodes if n.type == NodeType.END.value]
    if not ends:
        r.errors.append("workflow must have at least one END node")
    if not wf.name or not str(wf.name).strip():
        r.errors.append("workflow has no name")


def _check_nodes(wf: Workflow, catalog: CapabilityCatalog | None,
                 r: ValidationReport) -> None:
    body_ids = wf.loop_body_ids()
    node_ids = {n.id for n in wf.nodes}

    for node in wf.nodes:
        ntype = node.ntype if node.type in NodeType._value2member_map_ else None
        if ntype is None:
            continue

        if ntype is NodeType.TOOL:
            tool = str(node.config.get("tool", ""))
            if not tool:
                r.errors.append(f"node {node.id!r} has no tool")
                continue
            if catalog is None:
                continue
            cap = catalog.get(tool)
            if cap is None:
                if tool not in r.missing_tools:
                    r.missing_tools.append(tool)
                r.errors.append(f"node {node.id!r} needs unknown tool {tool!r}")
            elif not cap.available:
                if tool not in r.unavailable_tools:
                    r.unavailable_tools.append(tool)
                r.errors.append(f"tool {tool!r} is not available right now")
            else:
                args = node.config.get("args")
                args = args if isinstance(args, dict) else {}
                missing = [p for p in cap.required_params if not args.get(p)]
                if missing:
                    r.errors.append(
                        f"node {node.id!r} missing required params for "
                        f"{tool!r}: {', '.join(missing)}")
                unknown = [k for k in args if k not in cap.properties and cap.properties]
                if unknown:
                    r.warnings.append(
                        f"node {node.id!r} passes unknown params to {tool!r}: "
                        f"{', '.join(sorted(unknown))}")

        elif ntype is NodeType.AI:
            op = str(node.config.get("operation", ""))
            if op not in AI_OPERATIONS:
                r.errors.append(f"node {node.id!r} has invalid AI operation {op!r}")
            _bounded(r, node.id, "max_output_chars", node.config, 4000)

        elif ntype is NodeType.CONDITION:
            op = str(node.config.get("op", ""))
            if op not in CONDITION_OPS:
                r.errors.append(f"node {node.id!r} has invalid condition op {op!r}")
            if op != "exists" and op != "bool" and "right" not in node.config:
                r.warnings.append(f"node {node.id!r} condition has no right operand")

        elif ntype is NodeType.TRANSFORM:
            op = str(node.config.get("op", ""))
            if op not in TRANSFORM_OPS:
                r.errors.append(f"node {node.id!r} has invalid transform op {op!r}")
            if op in ("extract", "rename", "pick", "format", "filter") and \
                    "path" not in node.config and op != "format":
                if op != "rename":
                    r.errors.append(f"node {node.id!r} transform.{op} needs config.path")

        elif ntype is NodeType.LOOP:
            body = node.config.get("body")
            if not isinstance(body, list) or not body:
                r.errors.append(f"node {node.id!r} loop has an empty body")
                continue
            try:
                cap_iter = int(node.config.get("max_iterations", DEFAULT_MAX_ITERATIONS))
            except (TypeError, ValueError):
                cap_iter = -1
            if not 1 <= cap_iter <= HARD_MAX_ITERATIONS:
                r.errors.append(
                    f"node {node.id!r} max_iterations must be 1..{HARD_MAX_ITERATIONS}")
            body_set: set[str] = set()
            for bid in body:
                bid = str(bid)
                if bid in body_set:
                    r.errors.append(f"node {node.id!r} body lists {bid!r} twice")
                body_set.add(bid)
                if bid not in node_ids:
                    r.errors.append(f"node {node.id!r} body references missing node {bid!r}")

        elif ntype is NodeType.DELAY:
            try:
                secs = int(node.config.get("seconds", -1))
            except (TypeError, ValueError):
                secs = -1
            if not 0 < secs <= MAX_DELAY_S:
                r.errors.append(f"node {node.id!r} delay seconds must be 1..{MAX_DELAY_S}")

        elif ntype is NodeType.MEMORY:
            if str(node.config.get("action", "")) not in MEMORY_ACTIONS:
                r.errors.append(f"node {node.id!r} invalid memory action")

        elif ntype is NodeType.VERIFICATION:
            if str(node.config.get("assert", "")) not in VERIFY_ASSERTS:
                r.errors.append(f"node {node.id!r} invalid verification assert")

        elif ntype is NodeType.NOTIFICATION:
            msg = str(node.config.get("message", "")).strip()
            if not msg:
                r.errors.append(f"node {node.id!r} notification has no message")


def _bounded(r: ValidationReport, node_id: str, key: str, config: dict,
             hard_max: int) -> None:
    if key not in config:
        return
    try:
        v = int(config[key])
    except (TypeError, ValueError):
        r.errors.append(f"node {node_id!r} config.{key} must be an integer")
        return
    if v < 1 or v > hard_max:
        r.errors.append(f"node {node_id!r} config.{key} must be 1..{hard_max}")


def _check_edges(wf: Workflow, r: ValidationReport) -> None:
    node_ids = {n.id for n in wf.nodes}
    body_ids = wf.loop_body_ids()
    by_id = {n.id: n for n in wf.nodes}

    for i, e in enumerate(wf.edges):
        if e.source not in node_ids:
            r.errors.append(f"edge #{i} source {e.source!r} does not exist")
        if e.target not in node_ids:
            r.errors.append(f"edge #{i} target {e.target!r} does not exist")
        if e.when and e.when not in ("true", "false"):
            r.errors.append(f"edge #{i} has invalid when={e.when!r}")

    src_counts: dict[str, list[Edge]] = {}
    for e in wf.edges:
        src_counts.setdefault(e.source, []).append(e)

    for node in wf.nodes:
        try:
            ntype = node.ntype
        except ValueError:
            continue
        outs = src_counts.get(node.id, [])
        if ntype is NodeType.END and outs:
            r.errors.append(f"END node {node.id!r} must not have outgoing edges")
        elif ntype is NodeType.CONDITION:
            trues = [e for e in outs if e.when == "true"]
            falses = [e for e in outs if e.when == "false"]
            plain = [e for e in outs if not e.when]
            if len(trues) != 1:
                r.errors.append(f"condition {node.id!r} needs exactly one true-branch")
            if len(falses) > 1:
                r.errors.append(f"condition {node.id!r} has multiple false-branches")
            if plain:
                r.errors.append(f"condition {node.id!r} branch edges need when=true/false")
            if not falses:
                r.warnings.append(f"condition {node.id!r} has no false-branch (else skips to end)")
        elif ntype is NodeType.TRIGGER:
            if len(outs) != 1:
                r.errors.append(f"TRIGGER node {node.id!r} needs exactly one outgoing edge")
            elif outs[0].when:
                r.errors.append(f"TRIGGER node {node.id!r} branch edge must be plain")
        elif ntype is NodeType.LOOP:
            plain = [e for e in outs if not e.when]
            if len(plain) != 1:
                r.errors.append(f"loop {node.id!r} needs exactly one 'next' edge")
        else:
            if len(outs) > 1:
                r.errors.append(f"node {node.id!r} has {len(outs)} outgoing edges (max 1)")
            if any(e.when for e in outs):
                r.errors.append(f"node {node.id!r} is not a condition but has branch edges")

    for e in wf.edges:
        if e.source in body_ids or e.target in body_ids:
            if e.source in body_ids and e.target in body_ids:
                continue
            r.errors.append(
                f"edge {e.source!r}→{e.target!r} crosses between loop body and main graph")

    # body nodes must not be main-reachable; main nodes must be reachable
    main = wf.main_nodes()
    main_ids = {n.id for n in main}
    triggers = [n for n in wf.nodes if n.type == NodeType.TRIGGER.value]
    reachable: set[str] = set()
    if triggers:
        stack = [triggers[0].id]
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            for e in wf.edges_from(cur):
                if e.target not in reachable:
                    stack.append(e.target)
    for n in main:
        if n.id not in reachable:
            r.errors.append(f"node {n.id!r} is unreachable from TRIGGER")

    for n in wf.nodes:
        if n.id in body_ids:
            continue
        if n.id not in main_ids:
            r.errors.append(f"node {n.id!r} is neither main-graph nor in a loop body")

    cycle = _find_cycle([(e.source, e.target) for e in wf.edges
                         if e.source not in body_ids and e.target not in body_ids])
    if cycle:
        r.errors.append("main graph contains a cycle: " + " → ".join(cycle))

    # body chains: sequential, no duplicate membership across loops
    owned: dict[str, str] = {}
    for loop in (n for n in wf.nodes if n.type == NodeType.LOOP.value):
        body = loop.config.get("body") or []
        for bid in body:
            bid = str(bid)
            if bid in owned:
                r.errors.append(
                    f"node {bid!r} is in the body of both {owned[bid]!r} and {loop.id!r}")
            owned[bid] = loop.id
            if bid in main_ids:
                r.errors.append(f"node {bid!r} is both a loop body and a main-graph node")


def _find_cycle(edges: list[tuple[str, str]]) -> list[str]:
    graph: dict[str, list[str]] = {}
    for a, b in edges:
        graph.setdefault(a, []).append(b)
    state: dict[str, int] = {}

    def dfs(n: str, path: list[str]) -> list[str]:
        state[n] = 1
        for m in graph.get(n, ()):
            if state.get(m) == 1:
                return path + [n, m]
            if state.get(m) is None:
                got = dfs(m, path + [n])
                if got:
                    return got
        state[n] = 2
        return []

    for node in list(graph):
        if state.get(node) is None:
            got = dfs(node, [])
            if got:
                return got
    return []


def _check_trigger(wf: Workflow, r: ValidationReport) -> None:
    trig = wf.trigger
    ttype = trig.type
    if ttype == TriggerType.MANUAL.value:
        return
    if ttype == TriggerType.SCHEDULE.value:
        cfg = trig.config
        if "time" in cfg:
            if not _TIME_RE.match(str(cfg.get("time", ""))):
                r.errors.append(f"schedule trigger time must be HH:MM (got {cfg.get('time')!r})")
        elif "every_minutes" in cfg:
            try:
                m = int(cfg["every_minutes"])
                if m < 1 or m > 10080:
                    raise ValueError
            except (TypeError, ValueError):
                r.errors.append("every_minutes must be 1..10080")
        else:
            r.errors.append("schedule trigger needs time=HH:MM or every_minutes")
    elif ttype == TriggerType.FILE_WATCH.value:
        cfg = trig.config
        if not str(cfg.get("path", "")).strip():
            r.errors.append("file_watch trigger needs a path")
        ev = str(cfg.get("event", "created"))
        if ev not in ("created", "changed", "any"):
            r.errors.append("file_watch event must be created|changed|any")
    else:
        r.errors.append(f"unknown trigger type {ttype!r}")


def normalize_ids(workflow: Workflow) -> Workflow:
    """Assign stable ids to nodes lacking one (generator convenience)."""
    for i, n in enumerate(workflow.nodes):
        if not n.id:
            n.id = new_id(f"n{i + 1}")
    return workflow
