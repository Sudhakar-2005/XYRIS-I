"""
actions/memory_controller.py — Skill for managing user memory.

Auto-discovered tool providing verbal memory commands:
  - Remember facts ("Remember that my project demo is on Friday")
  - Search / recall memories ("What do you remember about my project?")
  - Forget facts ("Forget that", "Forget my project demo")
  - Memory statistics ("How much do you remember?")
  - Export / import memories (backup and restore)
  - Clear memories (by category or all)
  - Memory age ("When did I tell you about my project?")
  - Open the interactive view ("Open my memory universe" — the read-only
    board in the system browser, memory_holo on loopback)
"""
from memory.memory_manager import (
    remember, forget, forget_matching, search_memory, is_sensitive_key_value,
    get_memory_stats, export_memory, import_memory, clear_category,
    clear_all_memory, get_memory_age,
)
from core import confirm


def memory_controller(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action   = str(params.get("action", "")).lower().strip()
    key      = str(params.get("key", "")).strip()
    value    = str(params.get("value", "")).strip()
    category = str(params.get("category", "notes")).strip()
    query    = str(params.get("query", "")).strip()

    if action in ("remember", "save"):
        if not key or not value:
            return "Please provide both key and value to remember."
        if is_sensitive_key_value(key, value):
            return "Refused to silently store sensitive credentials (passwords/PINs)."
        return remember(key, value, category)

    elif action in ("recall", "search", "list"):
        return search_memory(query)

    elif action in ("forget", "delete", "remove"):
        if key:
            return forget(key, category)
        elif query:
            return forget_matching(query)
        else:
            return "Please specify what fact or query to forget."

    elif action == "stats":
        stats = get_memory_stats()
        lines = [
            f"Total entries: {stats['total']}",
            f"Sessions stored: {stats['sessions']}",
            f"File size: {stats['size_bytes']:,} bytes",
        ]
        if stats["oldest"]:
            lines.append(f"Oldest entry: {stats['oldest']}")
        if stats["newest"]:
            lines.append(f"Newest entry: {stats['newest']}")
        by_cat = stats.get("by_category", {})
        if by_cat:
            lines.append("By category:")
            for cat, count in sorted(by_cat.items()):
                if count > 0:
                    lines.append(f"  {cat}: {count}")
        return "\n".join(lines)

    elif action == "export":
        data = export_memory()
        return f"Memory exported ({len(data)} bytes). Data:\n{data}"

    elif action == "import":
        import_data = str(params.get("data", "")).strip()
        if not import_data:
            return "Please provide import data (JSON string) in the 'data' parameter."
        return import_memory(import_data, merge=True)

    elif action == "clear":
        target = str(params.get("target", "")).strip()
        if not target:
            return "Please specify a target: a category name or 'all'."
        # M12: safety classifies 'clear' DANGEROUS, and the old guard was the
        # model writing confirm_key itself — the forgeable gate core/confirm.py
        # was written to replace. The human's banner is the confirmation now;
        # the key below is fixed by the code, never supplied by the model.
        def _run_clear():
            if target == "all":
                return clear_all_memory(confirm_key="YES_DELETE_ALL")
            return clear_category(target)
        return confirm.request(
            key="memory_controller.clear",
            title="Clear stored memories",
            detail=(f"Permanently delete ALL remembered memories."
                    if target == "all"
                    else f"Permanently delete every memory in '{target}'."),
            run=_run_clear)

    elif action == "age":
        if not key:
            return "Please provide a key to check the age of."
        return get_memory_age(key, category)

    elif action == "view":
        # Read-only board over this same store, in the system browser.
        # Fail-soft: the port may be busy or the module missing — say so.
        from memory_holo import server as mem_view
        try:
            url = mem_view.open_in_browser()
        except Exception:
            url = None
        if not url:
            return "The memory universe view could not start (its local port may be busy)."
        return f"Opened the read-only memory universe view: {url}"

    else:
        return (
            f"Unknown memory action: '{action}'. "
            "Supported actions: remember | recall | forget | stats | export | import | clear | age | view"
        )


TOOL = {
    "name": "memory_controller",
    "risk": "caution",
    "permissions": ["memory"],
    "timeout": 30,
    "requires_confirmation": False,
    "supports_undo": False,
    "verification": "none",
    "description": (
        "Manages short-term and long-term memory with full CRUD operations. "
        "Use action='remember' to store a fact. "
        "Use action='recall' to search stored facts. "
        "Use action='forget' to delete a fact. "
        "Use action='stats' to see memory statistics (total entries, categories, size). "
        "Use action='export' to backup all memories as JSON. "
        "Use action='import' to restore memories from a JSON backup. "
        "Use action='clear' to delete entries by category or all. "
        "Use action='age' to check when a fact was last updated. "
        "Use action='view' to open the interactive read-only memory universe in the browser."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "remember | recall | forget | stats | export | import | clear | age | view"
            },
            "key": {
                "type": "STRING",
                "description": "Short snake_case key for the memory entry (e.g. project_demo)"
            },
            "value": {
                "type": "STRING",
                "description": "The fact to remember (e.g. 'Project demo is on Friday')"
            },
            "category": {
                "type": "STRING",
                "description": "identity | preferences | projects | relationships | wishes | notes"
            },
            "query": {
                "type": "STRING",
                "description": "Keyword or topic to search or forget"
            },
            "data": {
                "type": "STRING",
                "description": "JSON string for import action"
            },
            "target": {
                "type": "STRING",
                "description": "Category name or 'all' for the clear action"
            },
            "confirm_key": {
                "type": "STRING",
                "description": "Must be 'YES_DELETE_ALL' to confirm clearing all memories"
            }
        },
        "required": ["action"]
    },
    "handler": memory_controller,
}
