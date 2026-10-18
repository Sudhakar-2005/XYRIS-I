"""XYRIS I V3 — central application state machine.

One source of truth for "what is the assistant doing right now". Every UI
state string flows through :func:`transition`, which normalises legacy
aliases, records history, and notifies listeners (the hook the event bus
will use in M11).

Design rules (Phase 2 / Phase 5 of the V3 spec):

* **Honest states only.** A state may be set only from a real runtime event —
  a transcript arriving, a tool running, a verification starting, an error
  being reported. Never a timer, never decoration. ``PLANNING`` lights while
  a real plan producer works (dev_agent's planner model, the automation
  generator); ``OBSERVING`` while a captured screen/camera frame waits for
  the model to see it (``screen_process`` → ``_flush_pending_vision``).
* **The vocabulary is fixed.** 13 V3 states + 2 system states
  (``MUTED``, ``INITIALISING``). Anything else arriving is passed through
  unchanged so no existing caller can break, but it is flagged for tests.
* **Thread-safe.** Emitted from the asyncio thread, tool executor threads and
  the Qt thread alike; all mutation happens under one lock.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger("aether.state")


class AppState:
    """The V3 state vocabulary (spec Phase 2). Plain strings, not an Enum —
    these cross signal boundaries and JSON payloads where ``AppState.LISTENING``
    formatting would only add noise."""

    # ── V3 core states ────────────────────────────────────────────────────
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    UNDERSTANDING = "UNDERSTANDING"
    THINKING = "THINKING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"
    SPEAKING = "SPEAKING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    ERROR = "ERROR"
    RECOVERING = "RECOVERING"
    SLEEPING = "SLEEPING"

    # ── system states predating V3 that still carry real meaning ──────────
    MUTED = "MUTED"
    INITIALISING = "INITIALISING"


V3_STATES = frozenset({
    AppState.IDLE, AppState.LISTENING, AppState.UNDERSTANDING,
    AppState.THINKING, AppState.PLANNING, AppState.EXECUTING,
    AppState.OBSERVING, AppState.VERIFYING, AppState.SPEAKING,
    AppState.WAITING_CONFIRMATION, AppState.ERROR, AppState.RECOVERING,
    AppState.SLEEPING,
})

SYSTEM_STATES = frozenset({AppState.MUTED, AppState.INITIALISING})

VALID_STATES = V3_STATES | SYSTEM_STATES

# Legacy strings that map onto V3 states. Tools across 25 action modules emit
# "PROCESSING" for "a tool is running" — that is EXECUTING in the V3
# vocabulary. Normalised at the single sink so no tool needs editing.
ALIASES = {
    "PROCESSING": AppState.EXECUTING,
}

# States in which the HUD/animation must actually be *doing* something because
# real work is in flight. Reused by the HUD (ring speed, paint rate) and the
# avatar (gaze aversion) so all three layers agree on what "working" means.
WORKING_STATES = frozenset({
    AppState.UNDERSTANDING, AppState.THINKING, AppState.PLANNING,
    AppState.EXECUTING, AppState.OBSERVING, AppState.VERIFYING,
    AppState.RECOVERING, AppState.INITIALISING,
})

Listener = Callable[[str, str, str], None]   # (new_state, old_state, source)

_HISTORY_LIMIT = 200

_lock = threading.Lock()
_current: str = AppState.INITIALISING
_history: list[tuple[float, str, str]] = []     # (monotonic ts, state, source)
_listeners: list[Listener] = []
_unknown_seen: set[str] = set()


def normalize(state: str) -> str | None:
    """Canonical form of *state*, or None when it is not in the vocabulary.

    Applies :data:`ALIASES` first, so ``"PROCESSING"`` normalises to
    ``"EXECUTING"``.
    """
    s = str(state or "").strip().upper()
    if s in VALID_STATES:
        return s
    if s in ALIASES:
        return ALIASES[s]
    return None


def transition(state: str, source: str = "") -> str:
    """Record *state* as current and return what should actually be displayed.

    Known states are normalised (aliases applied). Unknown strings pass
    through unchanged — the HUD's generic fallback still renders them and no
    legacy caller can break — but are logged once for tests to catch.
    """
    global _current
    canonical = normalize(state)
    display = canonical if canonical is not None else str(state or "").strip()

    with _lock:
        old = _current
        _current = display
        _history.append((time.monotonic(), display, source))
        if len(_history) > _HISTORY_LIMIT:
            del _history[: len(_history) - _HISTORY_LIMIT]
        if canonical is None and display and display not in _unknown_seen:
            _unknown_seen.add(display)
            logger.debug("state outside V3 vocabulary passed through: %r", display)
        listeners = list(_listeners)

    if display != old:
        for fn in listeners:
            try:
                fn(display, old, source)
            except Exception:      # a listener must never break the state path
                logger.exception("state listener failed")
    return display


def current() -> str:
    """The current (normalised) state."""
    with _lock:
        return _current


def history(limit: int = 20) -> list[tuple[float, str, str]]:
    """Most recent transitions, newest last: [(ts, state, source), ...]."""
    with _lock:
        return list(_history[-limit:])


def add_listener(fn: Listener) -> None:
    """Subscribe to state changes (hook for the M11 event bus)."""
    with _lock:
        if fn not in _listeners:
            _listeners.append(fn)


def remove_listener(fn: Listener) -> None:
    with _lock:
        if fn in _listeners:
            _listeners.remove(fn)


def is_working(state: str | None = None) -> bool:
    """True when *state* (default: current) means real work is in flight."""
    s = normalize(state) if state else current()
    return s in WORKING_STATES


def _reset_for_tests() -> None:
    """Restore pristine state — test helper only."""
    global _current
    with _lock:
        _current = AppState.INITIALISING
        _history.clear()
        _listeners.clear()
        _unknown_seen.clear()
