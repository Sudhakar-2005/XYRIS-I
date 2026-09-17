"""
actions/memory_controller.py — Skill for managing user memory.

Auto-discovered tool providing verbal memory commands:
  - Remember facts ("Remember that my project demo is on Friday")
  - Search / recall memories ("What do you remember about my project?")
  - Forget facts ("Forget that", "Forget my project demo")
"""
from memory.memory_manager import (
    remember, forget, forget_matching, search_memory, is_sensitive_key_value
)


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

    else:
        return (
            f"Unknown memory action: '{action}'. "
            "Supported actions: remember | recall | forget"
        )


TOOL = {
    "name": "memory_controller",
    "description": (
        "Manages short-term and long-term memory. "
        "Use action='remember' to store a fact ('Remember that my project demo is on Friday'). "
        "Use action='recall' to search stored facts ('What do you remember about my project?'). "
        "Use action='forget' to delete a fact ('Forget that', 'Forget my project demo')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "remember | recall | forget"
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
            }
        },
        "required": ["action"]
    },
    "handler": memory_controller,
}
