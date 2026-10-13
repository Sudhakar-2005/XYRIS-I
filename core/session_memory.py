"""Session-scoped tool handoff memory — the ``session_memory`` slot, made real.

The context key existed since the action loader was written but was always
passed as ``None``: weather_report's guarded ``set_last_search`` call could
never run, which is exactly the promise-without-a-code-path class of defect
BRAIN Phase 2 retired. This module gives the slot one honest object per live
conversation — injected by ``main`` into both dispatch contexts, written by
tools that record something for the *current session only*.

Deliberately small. Durable facts belong to ``memory.memory_manager``; this
is the sticky note on the inside of the session window (last search, later
lookups), dropped when the process exits. Write-only consumers are fine —
every reader that appears later reads the same single object main injects.
"""

from __future__ import annotations

import threading
from typing import Any


class SessionMemory:
    """In-process, thread-safe, session-lifetime handoff slot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_search: dict[str, Any] | None = None

    def set_last_search(self, query: str = "", response: str = "") -> None:
        """Record the most recent search-style tool call (weather, …)."""
        with self._lock:
            self._last_search = {
                "query": str(query),
                "response": str(response),
            }

    @property
    def last_search(self) -> dict[str, Any] | None:
        """Copy of the last recorded search, or None when nothing ran yet."""
        with self._lock:
            return dict(self._last_search) if self._last_search else None
