"""Automation Intelligence V0.1 — agent-first workflow layer.

UNDERSTAND → DESIGN → VALIDATE → APPROVE → EXECUTE → OBSERVE → VERIFY →
RECOVER → REMEMBER.

Everything reuses the existing XYRIS I systems (tool registry, permission
ledger, confirm gate, verifier, event bus, memory) — see
docs/automation/phase0-architecture-audit.md.
"""
from .schema import (  # noqa: F401
    Edge, Node, NodeResult, NodeStatus, NodeType, RetryPolicy, RunStatus,
    Trigger, TriggerType, VerificationState, Workflow, WorkflowStatus,
    new_id, now, resolve_path, set_path,
)
from .capability import Capability, CapabilityCatalog  # noqa: F401
from .validate import ValidationReport, validate  # noqa: F401
from .risk import WorkflowRisk, analyze  # noqa: F401
from .generator import (  # noqa: F401
    GenerationResult, GoalModel, generate, parse_intent,
)
from .templates import Template, build_templates, get_template, instantiate  # noqa: F401
