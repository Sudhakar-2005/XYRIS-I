"""automation — the model-facing Automation Intelligence tool (Phase 7).

Discovered automatically by core/action_loader (module-level TOOL dict).
The handler runs on an executor thread; everything blocking (LLM design
calls) is fine there, and runs are scheduled onto the engine's asyncio loop
via start_run(), so this tool always answers inside its policy timeout
instead of holding the conversation hostage for a long workflow.

Nothing here executes work by itself: creating validates before storing,
activating re-validates with the permission ledger, and running goes
through the runtime's per-node risk/idempotency/approval gates.
"""
from __future__ import annotations

from core.automation import (
    Workflow, WorkflowStatus, build_templates, generate, get_template,
    instantiate, new_id,
)
from core.automation.validate import REQUIRES_CLARIFICATION, REQUIRES_PERMISSION
from core.automation.engine import get_engine
from core import confirm as confirm_gate

_VERBS = ("list", "explain", "create", "generate", "templates", "activate",
          "run", "test", "status", "pause", "resume", "stop", "delete",
          "history")


def _engine_or_none():
    try:
        return get_engine()
    except Exception:
        return None


def _fmt_runs(runs: list[dict]) -> str:
    if not runs:
        return "  (no runs recorded yet)"
    lines = []
    for r in runs[:5]:
        dur = r.get("duration_ms")
        dur = f"{int(dur) // 1000}s" if isinstance(dur, (int, float)) else "-"
        err = r.get("error") or {}
        why = f" — {err.get('why', '')[:80]}" if isinstance(err, dict) and err.get("why") else ""
        lines.append(f"  {r.get('run_id')} [{r.get('status')}] "
                     f"{r.get('trigger', '')} {dur}{why}")
    return "\n".join(lines)


def automation_action(parameters: dict, player=None) -> str:
    eng = _engine_or_none()
    if eng is None:
        return ("Automation engine is not available in this session — "
                "nothing was done.")

    action = str(parameters.get("action") or "").strip().lower()
    wf_id = str(parameters.get("id") or "").strip()
    if action not in _VERBS:
        return (f"Unknown automation action {action!r}. Use one of: "
                f"{', '.join(_VERBS)}.")

    def _require_id() -> str:
        if not wf_id:
            raise ValueError(f"action {action!r} needs an automation id — "
                             f"call list first")
        return wf_id

    try:
        # ---------------------------------------------------------- reads
        if action == "list":
            items = eng.list(status=str(parameters.get("status") or "") or None)
            if not items:
                return ("No automations yet. Say 'create an automation from "
                        "template …' or describe one with 'generate'.")
            lines = [f"{len(items)} automation(s):"]
            for it in items[:15]:
                lines.append(f"  {it.get('id')} | {it.get('name')} | "
                             f"{it.get('status')} | trigger={it.get('trigger')}")
            if len(items) > 15:
                lines.append(f"  … {len(items) - 15} more")
            return "\n".join(lines)

        if action == "templates":
            tpls = build_templates(eng.catalog)
            if not tpls:
                return ("No starter templates are available with the "
                        "current tools.")
            lines = [f"{len(tpls)} starter template(s):"]
            for t in tpls[:10]:
                lines.append(f"  {t.key} — {t.name}: {t.description}")
            return "\n".join(lines)

        if action == "explain":
            return eng.explain(_require_id())

        if action == "status":
            if not wf_id:
                s = eng.status_summary()
                return (f"automations: {s['total']} total, {s['active']} "
                        f"active, {s['running']} running now")
            wf = eng.get(_require_id())
            if wf is None:
                raise KeyError(_short_id_error(eng, wf_id))
            active = eng.runtime.active_run_for(wf.id)
            stats = wf.statistics or {}
            return (f"{wf.name} ({wf.id}) — {wf.status}\n"
                    f"trigger: {wf.trigger.type} {wf.trigger.config or ''}\n"
                    f"runs: {stats.get('runs', 0)} · completed "
                    f"{stats.get('completed', 0)} · failed "
                    f"{stats.get('failed', 0)}"
                    + (f"\nrunning now: {active}" if active else "")
                    + "\nrecent:\n" + _fmt_runs(eng.runs(wf.id, limit=5)))

        if action == "history":
            return _fmt_runs(eng.runs(_require_id(),
                                      limit=int(parameters.get("limit") or 10)))

        # ------------------------------------------------------- mutations
        if action == "create":
            tpl_key = str(parameters.get("template") or "").strip()
            spec = parameters.get("spec")
            if tpl_key:
                t = get_template(tpl_key, eng.catalog)
                if t is None:
                    available = [x.key for x in build_templates(eng.catalog)]
                    raise ValueError(
                        f"template {tpl_key!r} is not available with the "
                        f"current tools; available: {', '.join(available)}")
                wf = instantiate(t, eng.catalog)
            elif isinstance(spec, dict) and spec:
                spec = dict(spec)
                spec.setdefault("id", new_id("auto"))
                wf = Workflow.from_dict(spec)
            else:
                raise ValueError("create needs either template=<key> or "
                                 "spec=<workflow object>")
            report = eng.validate(wf)
            if report.status == REQUIRES_PERMISSION:
                return ("Cannot save: permissions not granted: "
                        + ", ".join(report.denied_permissions))
            if not report.valid:
                return ("Cannot save an invalid workflow: "
                        + "; ".join(report.errors[:5]))
            saved = eng.save(wf)
            return (f"Created '{saved.name}' ({saved.id}) — validated, "
                    f"status {saved.status}. Use activate, then run/test.")

        if action == "generate":
            text = str(parameters.get("text") or "").strip()
            if not text:
                raise ValueError("generate needs text=<your request>")
            # Designing the graph is real planning — the planner model runs
            # inside generate() until a valid workflow comes out. Muted HUDs
            # stay untouched; main's dispatch end resets to LISTENING anyway.
            _muted = getattr(player, "muted", True)
            if player is not None and not _muted:
                player.set_state("PLANNING")
            try:
                res = generate(text, eng.catalog, _ai_json, ledger=eng.ledger)
            finally:
                if player is not None and not _muted:
                    player.set_state("EXECUTING")
            if res.status == REQUIRES_CLARIFICATION:
                return ("NEEDS CLARIFICATION — ask the user, do not "
                        "guess:\n" + res.clarification)
            if not res.ok:
                errs = "; ".join((res.errors or [])[:4]) or "unknown error"
                return f"Could not design a valid workflow: {errs}"
            saved = eng.save(res.workflow)
            trig = saved.trigger.type
            return (f"Designed and validated '{saved.name}' ({saved.id}), "
                    f"trigger={trig}, risk={res.report.risk_level}. "
                    f"Say 'test it' (dry run) or 'activate' + 'run it'.")

        if action == "activate":
            wf = eng.get(_require_id())
            if wf is None:
                raise KeyError(_short_id_error(eng, wf_id))
            saved = eng.set_status(wf.id, WorkflowStatus.ACTIVE.value)
            return f"'{saved.name}' is now ACTIVE (validated with permissions)."

        if action in ("run", "test"):
            mode = "dry" if action == "test" else \
                ("dry" if str(parameters.get("mode") or "").lower() == "dry"
                 else "live")
            run_id = eng.start_run(_require_id(), mode=mode, trigger="manual")
            verb = "Test (dry run)" if mode == "dry" else "Run"
            return (f"{verb} started: {run_id}. Nothing is simulated as "
                    f"success — follow it with action=status or "
                    f"action=history.")

        if action in ("pause", "resume", "stop"):
            fn = {"pause": eng.pause, "resume": eng.resume,
                  "stop": eng.stop}[action]
            ok, msg = fn(_require_id())
            return msg if ok else f"Could not {action}: {msg}"

        if action == "delete":
            wf = eng.get(_require_id())
            if wf is None:
                raise KeyError(_short_id_error(eng, wf_id))
            if confirm_gate.pending_title():
                return ("There is already a confirmation waiting on screen — "
                        "the user must answer that one first.")
            deleted = {"done": False}

            def _do_delete() -> str:
                deleted["done"] = True
                eng.delete(wf.id)
                return f"deleted {wf.id}"

            sentence = confirm_gate.request(
                key=f"auto-delete:{wf.id}",
                title=f"Delete automation '{wf.name}'",
                detail=(f"{wf.name} ({wf.id}) — {len(wf.nodes)} steps, "
                        f"versions kept in config/automations/. Confirm to "
                        f"delete."),
                run=_do_delete)
            if not confirm_gate.pending_title() and not deleted["done"]:
                return ("Cannot show a confirmation right now, so nothing "
                        "was deleted.")
            return sentence

    except KeyError as exc:
        return f"No such automation: {exc}"
    except (ValueError, PermissionError, RuntimeError) as exc:
        return f"Refused: {exc}"
    except Exception as exc:                                     # noqa: BLE001
        return f"Automation action failed: {exc}"
    return f"Unknown automation action {action!r}."


def _short_id_error(eng, wf_id: str) -> str:
    ids = [str(i.get("id")) for i in eng.list()[:8]]
    return (f"{wf_id!r} does not exist. Known ids: "
            f"{', '.join(ids) if ids else 'none yet'}")


def _ai_json(prompt: str, timeout_s: float = 60.0):
    """Blocking gemini JSON call — safe here: handlers run on an executor
    thread, never on the session loop."""
    from core import gemini
    return gemini.as_json(prompt, gemini.SMART,
                          timeout_ms=max(10_000, int(timeout_s) * 1000))


TOOL = {
    "name": "automation",
    "risk": "caution",
    "timeout": 120,
    "requires_confirmation": False,
    "supports_undo": False,
    "verification": "none",
    "permissions": ["memory"],   # automations + run history are persisted
                                 # records; inner steps enforce their own
                                 # permissions at dispatch time
    "description": (
        "Automation intelligence: design, list, explain, test, run, pause, "
        "resume, stop, activate and delete multi-step automations. Use "
        "action='generate' with text=<user request> to design one from "
        "natural language (it asks back instead of guessing), "
        "action='templates' for starters, action='create' with template=<key> "
        "to instantiate one, action='list'/'status'/'history' to inspect, "
        "action='test' for a dry run that never touches real tools, and "
        "action='run' to execute. Dangerous steps still ask for real human "
        "confirmation on screen."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("list | explain | create | generate | "
                                "templates | activate | run | test | status | "
                                "pause | resume | stop | delete | history"),
            },
            "id": {"type": "STRING",
                   "description": "Automation id (needed by most actions)."},
            "text": {"type": "STRING",
                     "description": "Natural-language request for generate."},
            "template": {"type": "STRING",
                         "description": "Template key for create."},
            "spec": {"type": "OBJECT",
                     "description": "Workflow object for create (alt to "
                                    "template)."},
            "mode": {"type": "STRING",
                     "description": "run mode: live (default) or dry."},
            "status": {"type": "STRING",
                       "description": "Optional status filter for list."},
            "limit": {"type": "INTEGER",
                      "description": "Max runs for history (default 10)."},
        },
        "required": ["action"],
    },
    "handler": automation_action,
}
