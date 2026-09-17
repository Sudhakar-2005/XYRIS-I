"""
Notification Intelligence action tool for Gemini.

Lets the assistant query notification history, send notifications,
manage preferences, and check unread counts.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.notification_manager import get_notification_manager, Priority


def notification_action(parameters: dict, player=None, session_memory=None) -> str:
    action = parameters.get("action", "").strip().lower()
    manager = get_notification_manager()

    try:
        if action == "send":
            return _handle_send(manager, parameters)
        elif action == "history":
            return _handle_history(manager, parameters)
        elif action == "unread_count":
            return _handle_unread_count(manager, parameters)
        elif action == "mark_read":
            return _handle_mark_read(manager, parameters)
        elif action == "mark_all_read":
            return _handle_mark_all_read(manager, parameters)
        elif action == "clear":
            return _handle_clear(manager, parameters)
        elif action == "stats":
            return _handle_stats(manager)
        elif action == "configure":
            return _handle_configure(manager, parameters)
        else:
            return (
                f"Unknown notification action '{action}'. "
                "Use: send, history, unread_count, mark_read, mark_all_read, clear, stats, configure."
            )
    except Exception as e:
        return f"Notification action failed: {e}"


def _handle_send(manager, params: dict) -> str:
    category = params.get("category", "general").strip().lower()
    title = params.get("title", "").strip()
    message = params.get("message", "").strip()

    if not title:
        return "Please provide a title for the notification."
    if not message:
        return "Please provide a message for the notification."

    priority_str = params.get("priority", "").strip().lower()
    priority = None
    if priority_str:
        try:
            priority = Priority[priority_str.upper()].value
        except KeyError:
            return f"Unknown priority '{priority_str}'. Use: low, medium, high, critical."

    notif = manager.notify(category, title, message, priority=priority)
    return f"Notification sent (id: {notif.id}, category: {category}, priority: {Priority(notif.priority).name})."


def _handle_history(manager, params: dict) -> str:
    category = params.get("category", "").strip().lower() or None
    unread_only = params.get("unread_only", "").strip().lower() in ("true", "1", "yes")
    try:
        limit = max(1, min(50, int(params.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10

    items = manager.get_history(category=category, unread_only=unread_only, limit=limit)
    if not items:
        return "No notifications found."

    lines = []
    for n in items:
        pri = Priority(n.priority).name
        read_marker = "" if n.read else " [UNREAD]"
        lines.append(
            f"  [{n.category}/{pri}] {n.title}: {n.message[:80]}"
            f"{read_marker} (id: {n.id})"
        )

    header = f"Notifications ({len(items)} shown):"
    return header + "\n" + "\n".join(lines)


def _handle_unread_count(manager, params: dict) -> str:
    category = params.get("category", "").strip().lower() or None
    count = manager.unread_count(category=category)
    if category:
        return f"You have {count} unread notification(s) in '{category}'."
    return f"You have {count} unread notification(s) total."


def _handle_mark_read(manager, params: dict) -> str:
    notif_id = params.get("notif_id", "").strip()
    if not notif_id:
        return "Please provide a notif_id to mark as read."
    if manager.mark_read(notif_id):
        return f"Notification {notif_id} marked as read."
    return f"Notification {notif_id} not found."


def _handle_mark_all_read(manager, params: dict) -> str:
    category = params.get("category", "").strip().lower() or None
    manager.mark_all_read(category=category)
    if category:
        return f"All notifications in '{category}' marked as read."
    return "All notifications marked as read."


def _handle_clear(manager, params: dict) -> str:
    category = params.get("category", "").strip().lower() or None
    manager.clear_history(category=category)
    if category:
        return f"Notification history cleared for '{category}'."
    return "Notification history cleared."


def _handle_stats(manager) -> str:
    stats = manager.get_stats()
    lines = [
        f"Total: {stats['total']}",
        f"Unread: {stats['unread']}",
        "By category: " + ", ".join(f"{k}={v}" for k, v in stats["by_category"].items()) or "none",
        "By priority: " + ", ".join(f"{k}={v}" for k, v in stats["by_priority"].items()) or "none",
    ]
    return "\n".join(lines)


def _handle_configure(manager, params: dict) -> str:
    category = params.get("category", "").strip().lower()
    if not category:
        return "Please provide a category to configure."

    updates = {}
    voice = params.get("voice", "").strip().lower()
    if voice in ("true", "false"):
        updates["voice"] = voice == "true"

    toast = params.get("toast", "").strip().lower()
    if toast in ("true", "false"):
        updates["toast"] = toast == "true"

    beep = params.get("beep", "").strip().lower()
    if beep in ("true", "false"):
        updates["beep"] = beep == "true"

    priority = params.get("priority", "").strip().lower()
    if priority:
        try:
            updates["default_priority"] = Priority[priority.upper()].value
        except KeyError:
            return f"Unknown priority '{priority}'. Use: low, medium, high, critical."

    if not updates:
        cfg = manager.get_category_config(category)
        return (
            f"Config for '{category}': "
            f"voice={cfg.get('voice')}, toast={cfg.get('toast')}, "
            f"beep={cfg.get('beep')}, priority={Priority(cfg.get('default_priority', 1)).name}"
        )

    manager.configure_category(category, **updates)
    return f"Category '{category}' updated: {', '.join(f'{k}={v}' for k, v in updates.items())}."


TOOL = {
    "name": "notification_action",
    "description": (
        "Notification intelligence. Use this to send notifications, check notification "
        "history, manage unread counts, mark notifications as read, clear history, "
        "view stats, or configure notification delivery preferences. "
        "Trigger phrases: 'send a notification', 'check notifications', "
        "'how many notifications', 'notification history', 'clear notifications', "
        "'configure notifications'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The notification action: "
                    "'send' (send a new notification), "
                    "'history' (view notification history), "
                    "'unread_count' (count unread notifications), "
                    "'mark_read' (mark a notification as read by notif_id), "
                    "'mark_all_read' (mark all as read), "
                    "'clear' (clear notification history), "
                    "'stats' (view notification statistics), "
                    "'configure' (configure delivery preferences for a category)."
                ),
            },
            "category": {
                "type": "STRING",
                "description": "Notification category: system, monitor, reminder, proactive, gmail, general.",
            },
            "title": {
                "type": "STRING",
                "description": "Notification title (for 'send' action).",
            },
            "message": {
                "type": "STRING",
                "description": "Notification message text (for 'send' action).",
            },
            "priority": {
                "type": "STRING",
                "description": "Priority level: low, medium, high, critical. Used by 'send' and 'configure'.",
            },
            "notif_id": {
                "type": "STRING",
                "description": "Notification ID (for 'mark_read' action).",
            },
            "unread_only": {
                "type": "STRING",
                "description": "Filter to unread only: 'true' or 'false' (for 'history').",
            },
            "limit": {
                "type": "STRING",
                "description": "Max results to return (for 'history', default 10).",
            },
            "voice": {
                "type": "STRING",
                "description": "Enable/disable voice delivery: 'true' or 'false' (for 'configure').",
            },
            "toast": {
                "type": "STRING",
                "description": "Enable/disable toast delivery: 'true' or 'false' (for 'configure').",
            },
            "beep": {
                "type": "STRING",
                "description": "Enable/disable beep sound: 'true' or 'false' (for 'configure').",
            },
        },
        "required": ["action"],
    },
    "handler": notification_action,
}
