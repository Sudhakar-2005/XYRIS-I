"""Phase 6 — starter workflows built ONLY from capabilities that exist.

Each builder checks the live catalog first: if a template needs a tool the
current install does not expose (e.g. gmail not connected), the template is
omitted instead of shipping a graph that cannot run. Every template is
validated against the same catalog before it is offered.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from .capability import CapabilityCatalog
from .schema import Edge, Node, Trigger, Workflow, WorkflowStatus, new_id
from .validate import ValidationReport, validate


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    description: str
    workflow: Workflow
    report: ValidationReport


# ------------------------------------------------------------------ helpers

def _tool(nid: str, tool: str, args: dict, name: str = "") -> Node:
    return Node(id=nid, type="TOOL", name=name or tool,
                config={"tool": tool, "args": dict(args)})


def _ai(nid: str, objective: str, input_path: str) -> Node:
    return Node(id=nid, type="AI", name="summarize",
                config={"operation": "summarize", "objective": objective,
                        "input_path": input_path})


def _notify(nid: str, message: str) -> Node:
    return Node(id=nid, type="NOTIFICATION", name="notify",
                config={"message": message})


def _chain(nodes: list[Node]) -> list[Edge]:
    return [Edge(a.id, b.id) for a, b in zip(nodes, nodes[1:])]


def _finish(nodes: list[Node], message: str) -> list[Node]:
    nodes.append(_notify("notice", message))
    nodes.append(Node(id="end", type="END", name="done"))
    return nodes


def _wf(key: str, name: str, description: str, trigger: Trigger,
        nodes: list[Node], edges: list[Edge],
        variables: dict | None = None) -> Workflow:
    return Workflow(id=f"tmpl_{key}", name=name, description=description,
                    status=WorkflowStatus.DRAFT.value, trigger=trigger,
                    nodes=nodes, edges=edges,
                    variables=dict(variables or {}),
                    metadata={"template": key})


# --------------------------------------------------------------- builders

def _daily_briefing(c: CapabilityCatalog) -> Workflow | None:
    if not (c.has("weather_report") and c.has("web_search")):
        return None
    nodes = [
        Node(id="trg", type="TRIGGER", name="8am",
             config={}, ),
        _tool("weather", "weather_report", {"city": "{$.vars.city}"}, "weather"),
        _tool("news", "web_search", {"query": "top news today"}, "top news"),
        _ai("brief", "Combine the weather and news into a short morning "
                     "briefing: 5 bullets maximum.",
            "$.nodes"),
    ]
    nodes = _finish(nodes, "Morning briefing:\n{$.nodes.brief.message}")
    return _wf("daily_briefing", "Daily briefing",
               "Weather + top news at 08:00 — set the 'city' variable first.",
               Trigger(type="schedule", config={"time": "08:00"}),
               nodes, _chain(nodes), {"city": ""})


def _email_summarizer(c: CapabilityCatalog) -> Workflow | None:
    if not c.has("gmail"):
        return None
    nodes = [
        Node(id="trg", type="TRIGGER", name="manual"),
        _tool("mail", "gmail", {"action": "list"}, "recent emails"),
        _ai("digest", "Summarize these recent emails: sender, subject, and "
                      "one line of what each one wants.",
            "$.nodes.mail"),
    ]
    nodes = _finish(nodes, "Email digest:\n{$.nodes.digest.message}")
    return _wf("email_summarizer", "Email digest",
               "Summarize the inbox on demand.",
               Trigger(), nodes, _chain(nodes))


def _internship_tracker(c: CapabilityCatalog) -> Workflow | None:
    if not c.has("gmail"):
        return None
    nodes = [
        Node(id="trg", type="TRIGGER", name="9am"),
        _tool("mail", "gmail",
              {"action": "search", "query": "internship OR intern"}, "search"),
        _ai("hits", "Summarize internship-related emails: company, role, "
                    "deadline if mentioned.",
            "$.nodes.mail"),
    ]
    nodes = _finish(nodes, "Internship updates:\n{$.nodes.hits.message}")
    return _wf("internship_tracker", "Internship tracker",
               "Search the inbox for internship mail at 09:00 daily.",
               Trigger(type="schedule", config={"time": "09:00"}),
               nodes, _chain(nodes))


def _file_organizer(c: CapabilityCatalog) -> Workflow | None:
    if not c.has("file_controller"):
        return None
    nodes = [
        Node(id="trg", type="TRIGGER", name="new file",
             config={}),
        _tool("files", "file_controller",
              {"action": "list", "path": "downloads"}, "list downloads"),
    ]
    nodes = _finish(nodes, "Download folder changed:\n{$.nodes.files}")
    wf = _wf("file_organizer", "Downloads watcher",
             "Notify and list Downloads when a new file appears.",
             Trigger(type="file_watch",
                     config={"path": "downloads", "event": "created"}),
             nodes, _chain(nodes))
    return wf


def _reminder_workflow(c: CapabilityCatalog) -> Workflow | None:
    nodes = [
        Node(id="trg", type="TRIGGER", name="9am"),
    ]
    nodes = _finish(nodes, "{$.vars.reminder}")
    return _wf("reminder_workflow", "Reminder",
               "Show a reminder message at 09:00 daily.",
               Trigger(type="schedule", config={"time": "09:00"}),
               nodes, _chain(nodes),
               {"reminder": "Time to stretch and drink water"})


def _system_monitor(c: CapabilityCatalog) -> Workflow | None:
    if not c.has("system_status"):
        return None
    nodes = [
        Node(id="trg", type="TRIGGER", name="every 15m"),
        _tool("status", "system_status", {}, "system status"),
    ]
    nodes = _finish(nodes, "System status:\n{$.nodes.status}")
    return _wf("system_monitor", "System monitor",
               "Report CPU/memory/disk every 15 minutes.",
               Trigger(type="schedule", config={"every_minutes": 15}),
               nodes, _chain(nodes))


def _job_alerts(c: CapabilityCatalog) -> Workflow | None:
    if not c.has("web_search"):
        return None
    nodes = [
        Node(id="trg", type="TRIGGER", name="10am"),
        _tool("search", "web_search",
              {"query": "remote internships and entry level jobs hiring "
                        "this week"}, "job search"),
        _ai("picks", "Extract the most relevant roles: title, company, "
                     "apply link. Max 8.", "$.nodes.search"),
    ]
    nodes = _finish(nodes, "Job alerts:\n{$.nodes.picks.message}")
    return _wf("job_alerts", "Job alerts",
               "Web search for fresh roles at 10:00, summarized.",
               Trigger(type="schedule", config={"time": "10:00"}),
               nodes, _chain(nodes))


_BUILDERS = [
    ("daily_briefing", _daily_briefing),
    ("email_summarizer", _email_summarizer),
    ("internship_tracker", _internship_tracker),
    ("file_organizer", _file_organizer),
    ("reminder_workflow", _reminder_workflow),
    ("system_monitor", _system_monitor),
    ("job_alerts", _job_alerts),
]


# ------------------------------------------------------------------- API

def build_templates(catalog: CapabilityCatalog) -> list[Template]:
    """Validated templates for everything the current catalog supports."""
    out: list[Template] = []
    for key, fn in _BUILDERS:
        try:
            wf = fn(catalog)
        except Exception:
            continue
        if wf is None:
            continue
        report = validate(wf, catalog)
        if not report.valid:
            continue          # never offer a template that fails the gate
        out.append(Template(key=key, name=wf.name,
                            description=wf.description,
                            workflow=wf, report=report))
    return out


def get_template(key: str, catalog: CapabilityCatalog) -> Template | None:
    for t in build_templates(catalog):
        if t.key == key:
            return t
    return None


def instantiate(template: Template, catalog: CapabilityCatalog) -> Workflow:
    """Fresh DRAFT copy with a new id — templates are never stored as-is."""
    wf = Workflow.from_dict(copy.deepcopy(template.workflow.to_dict()))
    wf.id = new_id("wf")
    wf.status = WorkflowStatus.DRAFT.value
    prefix = f"{wf.id}_"
    for n in wf.nodes:
        n.id = prefix + n.id
        body = n.config.get("body") if isinstance(n.config, dict) else None
        if isinstance(body, list):
            n.config["body"] = [prefix + str(b) for b in body]
    node_ids = {n.id for n in wf.nodes}
    wf.edges = [Edge(e.source if e.source in node_ids else prefix + e.source,
                     e.target if e.target in node_ids else prefix + e.target,
                     e.when)
                for e in wf.edges]
    return wf
