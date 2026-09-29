"""
Safety Intelligence action tool for Gemini.

Lets the assistant query the safety classification of actions,
view the audit trail, check rate limits, and understand what
requires confirmation.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.safety_manager import (
    classify, should_confirm, is_forbidden, Risk,
    get_audit_log, get_audit_stats, clear_audit_log,
    rate_limit_remaining, _risk_summary,
)


def safety_action(parameters: dict, player=None, session_memory=None) -> str:
    action = parameters.get("action", "").strip().lower()

    try:
        if action == "classify":
            return _handle_classify(parameters)
        elif action == "audit_log":
            return _handle_audit_log(parameters)
        elif action == "audit_stats":
            return _handle_audit_stats()
        elif action == "clear_audit":
            return _handle_clear_audit()
        elif action == "rate_check":
            return _handle_rate_check(parameters)
        elif action == "risk_summary":
            return _handle_risk_summary()
        elif action == "check_action":
            return _handle_check_action(parameters)
        else:
            return (
                f"Unknown safety action '{action}'. "
                "Use: classify, audit_log, audit_stats, clear_audit, "
                "rate_check, risk_summary, check_action."
            )
    except Exception as e:
        return f"Safety action failed: {e}"


def _handle_classify(params: dict) -> str:
    tool = params.get("tool", "").strip()
    action_name = params.get("action_name", "").strip()
    if not tool or not action_name:
        return "Please provide both 'tool' and 'action_name' to classify."
    risk = classify(tool, action_name)
    return f"'{tool}.{action_name}' is classified as {risk.name} (level {risk.value})."


def _handle_check_action(params: dict) -> str:
    tool = params.get("tool", "").strip()
    action_name = params.get("action_name", "").strip()
    if not tool or not action_name:
        return "Please provide both 'tool' and 'action_name'."

    risk = classify(tool, action_name)
    needs_confirm = should_confirm(tool, action_name)
    forbidden = is_forbidden(tool, action_name)
    remaining = rate_limit_remaining(action_name)

    lines = [f"Action: {tool}.{action_name}"]
    lines.append(f"Risk: {risk.name} (level {risk.value})")
    lines.append(f"Needs confirmation: {'Yes' if needs_confirm else 'No'}")
    lines.append(f"Forbidden: {'Yes' if forbidden else 'No'}")
    lines.append(f"Rate limit remaining: {remaining}")
    return "\n".join(lines)


def _handle_audit_log(params: dict) -> str:
    tool = params.get("tool", "").strip() or None
    risk = params.get("risk", "").strip() or None
    try:
        limit = max(1, min(50, int(params.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20

    items = get_audit_log(tool=tool, risk=risk, limit=limit)
    if not items:
        return "Audit log is empty."

    lines = [f"Audit trail ({len(items)} entries):"]
    for e in items:
        conf = " [CONFIRMED]" if e.confirmed else ""
        lines.append(f"  [{e.timestamp}] {e.risk} {e.tool}/{e.action}{conf}")
        if e.detail:
            lines.append(f"    {e.detail}")
    return "\n".join(lines)


def _handle_audit_stats() -> str:
    stats = get_audit_stats()
    lines = [
        f"Total logged actions: {stats['total']}",
        f"Confirmed actions: {stats['confirmed']}",
        "By risk level: " + ", ".join(f"{k}={v}" for k, v in stats["by_risk"].items()) or "none",
        "By tool: " + ", ".join(f"{k}={v}" for k, v in stats["by_tool"].items()) or "none",
    ]
    return "\n".join(lines)


def _handle_clear_audit() -> str:
    clear_audit_log()
    return "Audit log cleared."


def _handle_rate_check(params: dict) -> str:
    action_name = params.get("action_name", "").strip()
    if not action_name:
        return "Please provide an 'action_name' to check."
    remaining = rate_limit_remaining(action_name)
    return f"Rate limit for '{action_name}': {remaining} remaining (max 5 per 5 minutes)."


def _handle_risk_summary() -> str:
    return _risk_summary()


TOOL = {
    "name": "safety_action",
    "risk": "safe",
    "timeout": 30,
    "requires_confirmation": False,
    "supports_undo": False,
    "verification": "none",
    "description": (
        "Safety intelligence. Use this to check if an action is safe, "
        "view the audit trail of safety-critical actions, check rate "
        "limits, or see the risk classification of all actions. "
        "Trigger phrases: 'is this safe', 'audit log', 'safety status', "
        "'what actions need confirmation', 'rate limit'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The safety action: "
                    "'classify' (classify a tool.action), "
                    "'check_action' (full safety check for tool.action), "
                    "'audit_log' (view audit trail), "
                    "'audit_stats' (view audit statistics), "
                    "'clear_audit' (clear audit log), "
                    "'rate_check' (check rate limit for an action), "
                    "'risk_summary' (show all risk classifications)."
                ),
            },
            "tool": {
                "type": "STRING",
                "description": "Tool name to classify or check (e.g. 'computer_settings', 'file_controller').",
            },
            "action_name": {
                "type": "STRING",
                "description": "Action name to classify or check (e.g. 'shutdown', 'delete').",
            },
            "risk": {
                "type": "STRING",
                "description": "Filter audit log by risk level: SAFE, CAUTION, DANGEROUS, FORBIDDEN.",
            },
            "limit": {
                "type": "STRING",
                "description": "Max audit log entries to show (default 20).",
            },
        },
        "required": ["action"],
    },
    "handler": safety_action,
}
