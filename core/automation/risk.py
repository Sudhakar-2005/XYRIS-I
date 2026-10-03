"""Risk + permission analysis for workflows (Phase 12).

Rules, in priority order:
1. TOOL nodes INHERIT risk/permissions from the live ToolPolicy of the
   underlying capability — automation never reclassifies a real tool.
2. Plugins declare no policy (audit gap) — explicit overrides below fill
   them in, and anything unknown defaults to safe + no permissions, which
   the runtime then re-checks through the real dispatcher gate anyway.
3. Non-tool node types get a static, conservative policy.
4. Workflow risk = max(node risks); the runtime refuses forbidden, pauses
   on dangerous, exactly like the live dispatcher.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .capability import CapabilityCatalog
from .schema import NodeType, Workflow

RISK_ORDER = ("safe", "caution", "dangerous", "forbidden")

# (risk, permissions, requires_confirmation) keyed tool → action-keyword
# matched against node config["args"]["action"] when present.
PLUGIN_RULES: dict[str, dict[str, tuple[str, tuple[str, ...], bool]]] = {
    "gmail": {
        "send": ("dangerous", ("messaging", "network"), True),
        "delete": ("dangerous", ("messaging", "network"), True),
        "draft": ("caution", ("messaging", "network"), False),
        "*": ("caution", ("network",), False),
    },
    "calendar": {
        "create": ("caution", ("network",), False),
        "*": ("safe", ("network",), False),
    },
    "quiz": {"*": ("safe", (), False)},
}

STATIC_NODE_POLICY: dict[NodeType, tuple[str, tuple[str, ...]]] = {
    NodeType.TRIGGER: ("safe", ()),
    NodeType.AI: ("caution", ("network",)),
    NodeType.CONDITION: ("safe", ()),
    NodeType.TRANSFORM: ("safe", ()),
    NodeType.LOOP: ("safe", ()),
    NodeType.DELAY: ("safe", ()),
    NodeType.APPROVAL: ("safe", ()),
    NodeType.MEMORY: ("caution", ("memory",)),
    NodeType.NOTIFICATION: ("caution", ("desktop",)),
    NodeType.VERIFICATION: ("safe", ()),
    NodeType.END: ("safe", ()),
}


def max_risk(a: str, b: str) -> str:
    ia = RISK_ORDER.index(a) if a in RISK_ORDER else 0
    ib = RISK_ORDER.index(b) if b in RISK_ORDER else 0
    return RISK_ORDER[max(ia, ib)]


def plugin_policy(name: str, config: dict) -> tuple[str, tuple[str, ...], bool]:
    """Resolve risk/permissions/confirmation for a plugin node."""
    rules = PLUGIN_RULES.get(name)
    if not rules:
        return "caution", ("network",), False
    args = config.get("args") if isinstance(config.get("args"), dict) else {}
    action = str(args.get("action", "")).lower()
    if action and action in rules:
        return rules[action]
    return rules.get("*", ("caution", ("network",), False))


@dataclass
class WorkflowRisk:
    risk_level: str = "safe"
    required_permissions: tuple[str, ...] = ()
    high_risk_nodes: list[str] = field(default_factory=list)
    confirmation_nodes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"risk_level": self.risk_level,
                "required_permissions": list(self.required_permissions),
                "high_risk_nodes": list(self.high_risk_nodes),
                "confirmation_nodes": list(self.confirmation_nodes)}


def analyze(workflow: Workflow, catalog: CapabilityCatalog | None) -> WorkflowRisk:
    """Compute and stamp risk/permissions onto every node; return the summary."""
    result = WorkflowRisk()
    perms: list[str] = list(workflow.permissions)

    for node in workflow.nodes:
        ntype = node.ntype
        if ntype is NodeType.TOOL:
            tool = str(node.config.get("tool", ""))
            cap = catalog.get(tool) if catalog else None
            if cap is None:
                risk, node_perms, confirm = "caution", ("process",), True
            elif cap.kind == "plugin":
                risk, node_perms, confirm = plugin_policy(tool, node.config)
            else:
                risk, confirm = cap.risk, cap.requires_confirmation
                node_perms = tuple(cap.permissions)
        else:
            risk, node_perms = STATIC_NODE_POLICY.get(ntype, ("caution", ("process",),))
            confirm = ntype is NodeType.APPROVAL

        node.risk = risk
        node.permissions = tuple(node_perms)
        if confirm or ntype is NodeType.APPROVAL:
            result.confirmation_nodes.append(node.id)
        if risk in ("dangerous", "forbidden"):
            result.high_risk_nodes.append(node.id)
        result.risk_level = max_risk(result.risk_level, risk)
        for p in node_perms:
            if p not in perms:
                perms.append(p)

    result.required_permissions = tuple(perms)
    return result
