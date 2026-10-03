"""Phase 6 — natural language → validated workflow (spec §11).

Pipeline: text → GoalModel → graph draft → validate() → (one repair round).

The model only ever *drafts*. Every result passes the same validate() gate
the runtime uses, so a hallucinated tool, a missing schedule time or an
unreachable node comes back as an honest error / clarification question —
never as a workflow that pretends to work.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .capability import CapabilityCatalog
from .schema import (
    Edge, Node, NodeType, Trigger, TriggerType, Workflow, WorkflowStatus,
    MAX_NODES, MAX_NODE_TIMEOUT_S, MIN_NODE_TIMEOUT_S,
)
from .validate import (
    INVALID, REQUIRES_CLARIFICATION, REQUIRES_PERMISSION, VALID,
    ValidationReport, normalize_ids, validate,
)

AiJson = Callable[..., Any]

MAX_REQUEST_CHARS = 2000


class GenerationError(Exception):
    """Raised internally when the model produced nothing usable."""


# --------------------------------------------------------------- goal model

@dataclass
class GoalModel:
    objective: str = ""
    trigger_type: str = TriggerType.MANUAL.value
    trigger_config: dict = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    variables: dict = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, d: Any, fallback_text: str = "") -> "GoalModel":
        if not isinstance(d, dict):
            raise GenerationError("goal model is not an object")
        trig = d.get("trigger") or {}
        if not isinstance(trig, dict):
            trig = {}
        ttype = str(trig.get("type") or cls.trigger_type).strip()
        if ttype not in {t.value for t in TriggerType}:
            ttype = TriggerType.MANUAL.value
        tcfg = trig.get("config") if isinstance(trig.get("config"), dict) else {}
        objective = str(d.get("objective") or fallback_text or "").strip()
        if not objective:
            raise GenerationError("goal model has no objective")

        def _strlist(v: Any) -> list[str]:
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()][:20]
            return []

        return cls(
            objective=objective[:MAX_REQUEST_CHARS],
            trigger_type=ttype,
            trigger_config=dict(tcfg),
            steps=_strlist(d.get("steps")),
            required_tools=_strlist(d.get("required_tools")),
            variables=dict(d.get("variables") or {}) if isinstance(
                d.get("variables"), dict) else {},
            constraints=_strlist(d.get("constraints")),
            ambiguities=_strlist(d.get("ambiguities")),
            notes=str(d.get("notes") or "")[:500],
        )

    def to_dict(self) -> dict:
        return {
            "objective": self.objective,
            "trigger": {"type": self.trigger_type,
                        "config": dict(self.trigger_config)},
            "steps": list(self.steps),
            "required_tools": list(self.required_tools),
            "variables": dict(self.variables),
            "constraints": list(self.constraints),
            "ambiguities": list(self.ambiguities),
            "notes": self.notes,
        }


# ------------------------------------------------------------------ results

@dataclass
class GenerationResult:
    status: str = INVALID
    workflow: Workflow | None = None
    clarification: str = ""
    report: ValidationReport | None = None
    goal: GoalModel | None = None
    capability_report: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == VALID and self.workflow is not None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "workflow_id": self.workflow.id if self.workflow else None,
            "clarification": self.clarification,
            "validation": self.report.to_dict() if self.report else None,
            "goal": self.goal.to_dict() if self.goal else None,
            "capability_report": dict(self.capability_report),
            "errors": list(self.errors),
        }


# ------------------------------------------------------------------ prompts

_INTENT_PROMPT = """You turn ONE user request into a goal model. Output JSON only.

{
  "objective": "<one sentence: what the user wants to achieve>",
  "trigger": {"type": "manual|schedule|file_watch", "config": {}},
  "steps": ["<plain-language step>", ...],
  "required_tools": ["<capability name if clearly needed>", ...],
  "variables": {"<name>": "<value or empty when the user must fill it in>"},
  "constraints": ["<explicit limits the user stated>", ...],
  "ambiguities": ["<specific question to ask when critical info is missing>", ...],
  "notes": ""
}

Rules:
- Ask an ambiguity ONLY for critical info you must not guess (when to run a
  schedule, which file/folder, who receives a message). Details with safe
  defaults are NOT ambiguities.
- For a schedule trigger put the time in trigger.config as "HH:MM" or
  "every_minutes": N; if the user did not say when, that IS an ambiguity.
- No prose outside the JSON. JSON must parse.

User request:
"""


def _draft_prompt(text: str, goal: GoalModel,
                  catalog: CapabilityCatalog) -> str:
    caps = json.dumps(catalog.prompt_list(limit=200), ensure_ascii=False)
    return f"""You design ONE workflow graph as JSON. Output JSON only, no prose.

USER REQUEST:
{text[:MAX_REQUEST_CHARS]}

GOAL MODEL:
{json.dumps(goal.to_dict(), ensure_ascii=False)}

AVAILABLE CAPABILITIES (use ONLY these names, params must satisfy "required"):
{caps}

NODE TYPES you may use (config keys marked * are required):
- TRIGGER: {{}}                    exactly one, the entry point
- TOOL: {{"tool": "<name from capabilities>", "args": {{...}}}}
- AI: {{"operation": "summarize|classify|extract|transform|compare|generate|decide|analyze", "objective": "...", "input_path": "$.nodes.<id>"}}
- CONDITION: {{"left": "$.nodes.<id>.<field>", "op": "eq|ne|contains|exists|bool|gt|gte|lt|lte|in", "right": <value>}}
- TRANSFORM: {{"op": "..."}}
- LOOP: {{"items_path": "$.vars.<name>", "body": ["<node ids>"], "max_iterations": N}}
- DELAY: {{"seconds": N}}
- APPROVAL: {{"title": "..."}}
- MEMORY: {{"action": "..."}}
- NOTIFICATION: {{"message": "..."}}   placeholders like {{$.nodes.<id>.message}}
- VERIFICATION: {{"assert": "..."}}
- END: {{}}                         at least one, no outgoing edges

GRAPH RULES:
- Exactly one TRIGGER (outgoing edge has no "when") and at least one END.
- A CONDITION needs exactly one edge with when="true" and at most one when="false".
- Loop body nodes are listed in the LOOP config only — no main-graph edges to them.
- Main graph must be acyclic and every non-body node reachable from TRIGGER.
- Every node needs a unique id (n1, n2, ...); edges reference those ids.
- Only you pick tool args the user actually gave; if a required param value is
  unknown, add a question to "clarifications" instead of guessing.

OUTPUT SCHEMA:
{{
  "name": "<short workflow name>",
  "description": "<one line>",
  "trigger": {{"type": "manual|schedule|file_watch", "config": {{}}}},
  "variables": {{}},
  "nodes": [{{"id": "n1", "type": "TRIGGER", "name": "...", "config": {{}}, "timeout_s": 60}}, ...],
  "edges": [{{"source": "n1", "target": "n2", "when": ""}}, ...],
  "clarifications": ["<question for the user, empty list when nothing is missing>"]
}}
Limits: at most {MAX_NODES} nodes.
"""


def _repair_prompt(prompt: str, errors: list[str]) -> str:
    listed = "\n".join(f"- {e}" for e in errors[:20])
    return (prompt +
            "\nYOUR PREVIOUS DRAFT FAILED VALIDATION:\n" + listed +
            "\nReturn a corrected JSON draft that fixes every error.")


# ----------------------------------------------------------------- ai calls

def _ai_json(ai_json: AiJson, prompt: str, timeout_s: float,
             errors: list[str]) -> Any:
    try:
        try:
            raw = ai_json(prompt, timeout_s=timeout_s)
        except TypeError:
            raw = ai_json(prompt)
    except Exception as exc:
        errors.append(f"model call failed: {exc}")
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            errors.append("model returned non-JSON text")
            return None
    if not isinstance(raw, (dict, list)):
        errors.append(f"model returned {type(raw).__name__}, expected an object")
        return None
    return raw


def parse_intent(text: str, ai_json: AiJson, *,
                 timeout_s: float = 60.0) -> GoalModel:
    """One bounded model call → GoalModel. Raises GenerationError on junk."""
    errors: list[str] = []
    raw = _ai_json(ai_json, _INTENT_PROMPT + text[:MAX_REQUEST_CHARS],
                   timeout_s, errors)
    if raw is None:
        raise GenerationError("; ".join(errors) or "no intent result")
    return GoalModel.from_dict(raw, fallback_text=text)


# ----------------------------------------------------------- draft → graph

def _clamp_timeout(v: Any) -> int:
    try:
        t = int(v)
    except (TypeError, ValueError):
        t = 60
    return max(MIN_NODE_TIMEOUT_S, min(MAX_NODE_TIMEOUT_S, t))


def _draft_to_workflow(draft: Any, goal: GoalModel,
                       text: str) -> tuple[Workflow | None, list[str], list[str]]:
    """Returns (workflow, errors, clarifications). Never raises."""
    clarifications: list[str] = []
    if isinstance(draft, dict) and isinstance(draft.get("clarifications"), list):
        clarifications = [str(q).strip() for q in draft["clarifications"]
                          if str(q).strip()][:20]
    if not isinstance(draft, dict):
        return None, ["draft is not an object"], clarifications
    if clarifications:
        return None, [], clarifications

    errors: list[str] = []
    raw_nodes = draft.get("nodes")
    raw_edges = draft.get("edges")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return None, ["draft has no nodes"], clarifications
    if not isinstance(raw_edges, list):
        raw_edges = []

    nodes: list[Node] = []
    for i, rn in enumerate(raw_nodes):
        if not isinstance(rn, dict):
            errors.append(f"node #{i} is not an object")
            continue
        ntype = str(rn.get("type") or "").strip().upper()
        if not ntype:
            errors.append(f"node #{i} has no type")
            continue
        cfg = rn.get("config") if isinstance(rn.get("config"), dict) else {}
        nodes.append(Node(
            id=str(rn.get("id") or "").strip(),
            type=ntype,
            name=str(rn.get("name") or "")[:80],
            config=cfg,
            timeout_s=_clamp_timeout(rn.get("timeout_s", 60)),
        ))

    edges: list[Edge] = []
    for i, re_ in enumerate(raw_edges):
        if not isinstance(re_, dict):
            errors.append(f"edge #{i} is not an object")
            continue
        when = str(re_.get("when") or "").strip().lower()
        if when not in ("", "true", "false"):
            errors.append(f"edge #{i} has invalid when={when!r}")
            when = ""
        edges.append(Edge(source=str(re_.get("source") or "").strip(),
                          target=str(re_.get("target") or "").strip(),
                          when=when))

    trig = draft.get("trigger")
    if not isinstance(trig, dict):
        trig = {"type": goal.trigger_type, "config": dict(goal.trigger_config)}
    ttype = str(trig.get("type") or "").strip().lower()
    if ttype not in {t.value for t in TriggerType}:
        errors.append(f"draft has unknown trigger type {ttype!r}")
        ttype = TriggerType.MANUAL.value
    tcfg = trig.get("config") if isinstance(trig.get("config"), dict) else {}

    name = str(draft.get("name") or "").strip() or text.strip()[:60]
    variables = draft.get("variables")
    if not isinstance(variables, dict):
        variables = dict(goal.variables)

    wf = Workflow(
        id="wf_generated",
        name=name,
        description=str(draft.get("description") or "")[:400],
        status=WorkflowStatus.DRAFT.value,
        trigger=Trigger(type=ttype, config=dict(tcfg)),
        nodes=nodes,
        edges=edges,
        variables=variables,
        metadata={"source": "generator", "objective": goal.objective[:200]},
    )
    if errors:
        return None, errors, clarifications
    return normalize_ids(wf), [], clarifications


# ----------------------------------------------------------------- pipeline

def _question(ambiguities: list[str]) -> str:
    head = [f"{i + 1}. {q}" for i, q in enumerate(ambiguities[:5])]
    more = f"\n(+{len(ambiguities) - 5} more)" if len(ambiguities) > 5 else ""
    return "I need a few details before designing this:\n" + "\n".join(head) + more


def _capability_report(report: ValidationReport | None) -> dict:
    if report is None:
        return {}
    return {
        "missing_tools": list(report.missing_tools),
        "unavailable_tools": list(report.unavailable_tools),
        "required_permissions": list(report.required_permissions),
        "denied_permissions": list(report.denied_permissions),
        "risk_level": report.risk_level,
    }


def generate(text: str, catalog: CapabilityCatalog, ai_json: AiJson, *,
             ledger: Any = None, timeout_s: float = 60.0) -> GenerationResult:
    """Full pipeline: intent → goal → draft → validate → at most 1 repair."""
    if not str(text or "").strip():
        return GenerationResult(status=INVALID,
                                errors=["request text is empty"])

    errors: list[str] = []
    try:
        goal = parse_intent(text, ai_json, timeout_s=timeout_s)
    except Exception as exc:
        errors.append(f"intent parse failed: {exc}")
        goal = GoalModel(objective=text[:200])
        if not goal.ambiguities:
            goal.ambiguities = []
    if goal.ambiguities:
        return GenerationResult(status=REQUIRES_CLARIFICATION,
                                clarification=_question(goal.ambiguities),
                                goal=goal, errors=list(errors))

    prompt = _draft_prompt(text, goal, catalog)
    raw = _ai_json(ai_json, prompt, timeout_s, errors)

    wf = report = None
    if raw is not None:
        wf, derr, clar = _draft_to_workflow(raw, goal, text)
        errors.extend(derr)
        if clar:
            return GenerationResult(status=REQUIRES_CLARIFICATION,
                                    clarification=_question(clar),
                                    goal=goal, errors=list(errors))
        if wf is not None:
            report = validate(wf, catalog, ledger)
            if report.valid:
                wf.status = WorkflowStatus.READY.value
                return GenerationResult(status=VALID, workflow=wf,
                                        report=report, goal=goal,
                                        capability_report=_capability_report(
                                            report))
            if report.status == REQUIRES_PERMISSION:
                # not a drafting problem — tell the caller who must grant what
                return GenerationResult(
                    status=INVALID, goal=goal, report=report,
                    capability_report=_capability_report(report),
                    errors=["missing permissions: " +
                            ", ".join(report.denied_permissions) +
                            " — ask the user to grant them in "
                            "config/permissions.json"])
            errors = []          # repairable — retry once with the errors

    # ---- one repair round -------------------------------------------------
    repair_errors = list(report.errors) if report is not None else list(errors)
    if not repair_errors:
        repair_errors = ["draft could not be built from the model output"]
    raw2 = _ai_json(ai_json, _repair_prompt(prompt, repair_errors),
                    timeout_s, errors)
    if raw2 is None:
        return GenerationResult(status=INVALID, goal=goal,
                                report=report,
                                capability_report=_capability_report(report),
                                errors=errors + repair_errors)

    wf2, derr2, clar2 = _draft_to_workflow(raw2, goal, text)
    if clar2:
        return GenerationResult(status=REQUIRES_CLARIFICATION,
                                clarification=_question(clar2),
                                goal=goal, errors=list(errors))
    if wf2 is None:
        return GenerationResult(status=INVALID, goal=goal,
                                errors=errors + repair_errors + derr2)

    report2 = validate(wf2, catalog, ledger)
    if report2.valid:
        wf2.status = WorkflowStatus.READY.value
        return GenerationResult(status=VALID, workflow=wf2, report=report2,
                                goal=goal,
                                capability_report=_capability_report(report2))
    if report2.status == REQUIRES_PERMISSION:
        return GenerationResult(
            status=INVALID, goal=goal, report=report2,
            capability_report=_capability_report(report2),
            errors=["missing permissions: " +
                    ", ".join(report2.denied_permissions) +
                    " — ask the user to grant them in "
                    "config/permissions.json"])
    return GenerationResult(status=INVALID, goal=goal, report=report2,
                            capability_report=_capability_report(report2),
                            errors=list(report2.errors))
