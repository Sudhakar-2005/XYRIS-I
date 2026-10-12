"""Automation Intelligence V0.1 — workflow data model (Phase 1/2).

The single source of truth for an Automation. Everything else (validator,
runtime, generator, store, UI) reads and writes these dataclasses through
to_dict/from_dict — no side formats.

Design constraints (see docs/automation/phase0-architecture-audit.md):
* adapted to this repo's conventions: plain dataclasses, JSON round-trip,
  epoch-float timestamps, str-valued enums.
* a graph is TRIGGER → … → END over `nodes` + `edges`; iteration is a LOOP
  node holding an ordered body list (body nodes are NOT part of the main
  graph, which keeps the main graph a DAG and validation simple).
* runtime execution state never lives here — it belongs to runtime.RunRecord.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


# ---------------------------------------------------------------- vocabulary

class WorkflowStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    READY = "READY"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DISABLED = "DISABLED"
    ARCHIVED = "ARCHIVED"


DURABLE_STATUSES = frozenset({
    WorkflowStatus.DRAFT, WorkflowStatus.READY, WorkflowStatus.ACTIVE,
    WorkflowStatus.PAUSED, WorkflowStatus.DISABLED, WorkflowStatus.ARCHIVED,
    WorkflowStatus.FAILED, WorkflowStatus.COMPLETED,
})


class NodeType(str, Enum):
    TRIGGER = "TRIGGER"
    TOOL = "TOOL"
    AI = "AI"
    CONDITION = "CONDITION"
    TRANSFORM = "TRANSFORM"
    LOOP = "LOOP"
    DELAY = "DELAY"
    APPROVAL = "APPROVAL"
    MEMORY = "MEMORY"
    NOTIFICATION = "NOTIFICATION"
    VERIFICATION = "VERIFICATION"
    END = "END"


class NodeStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    VERIFIED = "VERIFIED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class VerificationState(str, Enum):
    NONE = "none"
    VERIFIED = "verified"
    FAILED = "failed"
    UNVERIFIABLE = "unverifiable"
    PARTIAL = "partial"


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DRY_RUN = "DRY_RUN"


class TriggerType(str, Enum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    FILE_WATCH = "file_watch"


# ------------------------------------------------------------- limits (V0.1)

DEFAULT_NODE_TIMEOUT_S = 60
MIN_NODE_TIMEOUT_S = 1
MAX_NODE_TIMEOUT_S = 3600
MAX_RETRY_ATTEMPTS = 5
DEFAULT_MAX_ITERATIONS = 50
HARD_MAX_ITERATIONS = 1000
MAX_DELAY_S = 3600
MAX_NODES = 200
MAX_EDGES = 500

# config keys each node type must carry (validated by validate.py)
REQUIRED_CONFIG: dict[NodeType, tuple[str, ...]] = {
    NodeType.TRIGGER: (),
    NodeType.TOOL: ("tool",),
    NodeType.AI: ("operation", "objective"),
    NodeType.CONDITION: ("left", "op"),
    NodeType.TRANSFORM: ("op",),
    NodeType.LOOP: ("items_path", "body"),
    NodeType.DELAY: ("seconds",),
    NodeType.APPROVAL: ("title",),
    NodeType.MEMORY: ("action",),
    NodeType.NOTIFICATION: ("message",),
    NodeType.VERIFICATION: ("assert",),
    NodeType.END: (),
}

CONDITION_OPS = ("eq", "ne", "contains", "exists", "bool",
                 "gt", "gte", "lt", "lte", "in")

TRANSFORM_OPS = ("extract", "rename", "pick", "set", "format",
                 "filter", "merge")

AI_OPERATIONS = ("summarize", "classify", "extract", "transform", "compare",
                 "generate", "decide", "analyze")

MEMORY_ACTIONS = ("save", "recall")

VERIFY_ASSERTS = ("exists", "not_empty", "equals", "matches", "count_gt",
                  "all_success")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now() -> float:
    return time.time()


# ------------------------------------------------------------------- results

@dataclass
class NodeResult:
    """Structured node result (spec §17), adapted around the repo's str tools."""
    success: bool = False
    status: str = "ok"                # ok | error | refused | timeout | skipped
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    verification: str = VerificationState.NONE.value
    reversible: bool = False
    error_code: str = ""
    execution_time_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success, "status": self.status,
            "message": self.message, "data": self.data,
            "verification": self.verification, "reversible": self.reversible,
            "error_code": self.error_code,
            "execution_time_ms": self.execution_time_ms,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NodeResult":
        return cls(
            success=bool(d.get("success", False)),
            status=str(d.get("status", "error")),
            message=str(d.get("message", "")),
            data=dict(d.get("data") or {}),
            verification=str(d.get("verification", "none")),
            reversible=bool(d.get("reversible", False)),
            error_code=str(d.get("error_code", "")),
            execution_time_ms=float(d.get("execution_time_ms", 0.0) or 0.0),
            metadata=dict(d.get("metadata") or {}),
        )


# -------------------------------------------------------------------- nodes

@dataclass
class RetryPolicy:
    max_attempts: int = 1
    backoff_s: float = 0.0

    def to_dict(self) -> dict:
        return {"max_attempts": self.max_attempts, "backoff_s": self.backoff_s}

    @classmethod
    def from_dict(cls, d: dict | None) -> "RetryPolicy":
        d = d or {}
        try:
            attempts = int(d.get("max_attempts", 1))
        except (TypeError, ValueError):
            attempts = 1
        try:
            backoff = float(d.get("backoff_s", 0.0))
        except (TypeError, ValueError):
            backoff = 0.0
        attempts = max(1, min(MAX_RETRY_ATTEMPTS, attempts))
        backoff = max(0.0, min(60.0, backoff))
        return cls(max_attempts=attempts, backoff_s=backoff)


@dataclass
class Node:
    id: str
    type: str
    name: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    timeout_s: int = DEFAULT_NODE_TIMEOUT_S
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    risk: str = ""
    permissions: tuple[str, ...] = ()

    @property
    def ntype(self) -> NodeType:
        return NodeType(self.type)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "name": self.name,
            "config": self.config, "timeout_s": self.timeout_s,
            "retry": self.retry.to_dict(), "risk": self.risk,
            "permissions": list(self.permissions),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        timeout = d.get("timeout_s", DEFAULT_NODE_TIMEOUT_S)
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = DEFAULT_NODE_TIMEOUT_S
        timeout = max(MIN_NODE_TIMEOUT_S, min(MAX_NODE_TIMEOUT_S, timeout))
        perms = d.get("permissions") or ()
        return cls(
            id=str(d.get("id", "")),
            type=str(d.get("type", "")),
            name=str(d.get("name", "")),
            config=dict(d.get("config") or {}),
            timeout_s=timeout,
            retry=RetryPolicy.from_dict(d.get("retry")),
            risk=str(d.get("risk", "")),
            permissions=tuple(str(p) for p in perms),
        )


@dataclass
class Edge:
    source: str
    target: str
    when: str = ""          # "" = always; "true"/"false" = condition branch

    def to_dict(self) -> dict:
        d = {"source": self.source, "target": self.target}
        if self.when:
            d["when"] = self.when
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        return cls(source=str(d.get("source", "")),
                   target=str(d.get("target", "")),
                   when=str(d.get("when", "")))


# ------------------------------------------------------------------ trigger

@dataclass
class Trigger:
    type: str = TriggerType.MANUAL.value
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> TriggerType:
        return TriggerType(self.type)

    def to_dict(self) -> dict:
        return {"type": self.type, "config": self.config}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Trigger":
        d = d or {}
        ttype = str(d.get("type", TriggerType.MANUAL.value))
        if ttype not in {t.value for t in TriggerType}:
            ttype = TriggerType.MANUAL.value
        return cls(type=ttype, config=dict(d.get("config") or {}))


# ---------------------------------------------------------------- workflow

@dataclass
class Workflow:
    id: str
    name: str
    description: str = ""
    version: int = 1
    status: str = WorkflowStatus.DRAFT.value
    trigger: Trigger = field(default_factory=Trigger)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)
    last_run: dict | None = None
    statistics: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------ lookups

    def node(self, node_id: str) -> Node | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def trigger_nodes(self) -> list[Node]:
        return [n for n in self.nodes if n.type == NodeType.TRIGGER.value]

    def edges_from(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node_id]

    def edges_to(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.target == node_id]

    def loop_body_ids(self) -> set[str]:
        ids: set[str] = set()
        for n in self.nodes:
            if n.type == NodeType.LOOP.value:
                body = n.config.get("body")
                if isinstance(body, list):
                    ids.update(str(b) for b in body)
        return ids

    def main_nodes(self) -> list[Node]:
        body = self.loop_body_ids()
        return [n for n in self.nodes if n.id not in body]

    def successors(self, node_id: str, when: str | None = None) -> list[str]:
        out = []
        for e in self.edges_from(node_id):
            if when is None or e.when == when:
                out.append(e.target)
        return out

    def required_permissions(self) -> tuple[str, ...]:
        perms: list[str] = list(self.permissions)
        for n in self.nodes:
            for p in n.permissions:
                if p not in perms:
                    perms.append(p)
        return tuple(perms)

    def highest_risk(self) -> str:
        order = ("safe", "caution", "dangerous", "forbidden")
        best = "safe"
        for n in self.nodes:
            if n.risk in order and order.index(n.risk) > order.index(best):
                best = n.risk
        return best

    # -------------------------------------------------------- (de)serialise

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "version": self.version, "status": self.status,
            "trigger": self.trigger.to_dict(),
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "variables": self.variables,
            "permissions": list(self.permissions),
            "metadata": self.metadata, "created_at": self.created_at,
            "updated_at": self.updated_at, "last_run": self.last_run,
            "statistics": self.statistics,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Workflow":
        return cls(
            id=str(d.get("id", "") or new_id("auto")),
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
            version=int(d.get("version", 1) or 1),
            status=str(d.get("status", WorkflowStatus.DRAFT.value)),
            trigger=Trigger.from_dict(d.get("trigger")),
            nodes=[Node.from_dict(n) for n in (d.get("nodes") or [])],
            edges=[Edge.from_dict(e) for e in (d.get("edges") or [])],
            variables=dict(d.get("variables") or {}),
            permissions=tuple(str(p) for p in (d.get("permissions") or ())),
            metadata=dict(d.get("metadata") or {}),
            created_at=float(d.get("created_at", now()) or now()),
            updated_at=float(d.get("updated_at", now()) or now()),
            last_run=d.get("last_run"),
            statistics=dict(d.get("statistics") or {}),
        )


# --------------------------------------------------------------- path utils

def resolve_path(data: Any, path: str, default: Any = None) -> Any:
    """Resolve ``$.a.b[0].c`` / ``a.b`` against dicts/lists. Never raises."""
    if path is None:
        return default
    p = str(path).strip()
    if p.startswith("$."):
        p = p[2:]
    elif p == "$":
        return data
    elif p.startswith("$"):
        p = p[1:]
    if not p:
        return data
    cur = data
    for part in p.split("."):
        if not part:
            continue
        head, sep, rest = part.partition("[")
        if head:
            if isinstance(cur, dict):
                cur = cur.get(head, default)
            elif isinstance(cur, list) and head.isdigit():
                i = int(head)
                cur = cur[i] if 0 <= i < len(cur) else default
            else:
                return default
        while sep:
            idx, _, nxt = rest.partition("]")
            try:
                i = int(idx.strip())
            except ValueError:
                return default
            if not isinstance(cur, list) or not 0 <= i < len(cur):
                return default
            cur = cur[i]
            rest = nxt
            sep = "[" if rest.startswith("[") else ""
            if sep:
                rest = rest[1:]
        if cur is default and not isinstance(cur, (dict, list)):
            return default
    return cur


def set_path(data: dict, path: str, value: Any) -> dict:
    """Set ``$.a.b`` inside a dict tree (creates intermediate dicts). Never raises."""
    p = str(path).strip()
    if p.startswith("$."):
        p = p[2:]
    elif p.startswith("$"):
        p = p[1:]
    if not p:
        return data
    cur: Any = data
    parts = [x for x in p.split(".") if x]
    for i, part in enumerate(parts[:-1]):
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return data
