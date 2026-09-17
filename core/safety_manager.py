"""
Centralized Safety Manager for JARVIS.

Provides action risk classification, audit logging, rate limiting on
destructive actions, and integration with the existing confirm.py gate.

Risk levels:
  SAFE      — no gate needed (volume up, list files, search)
  CAUTION   — undo-backed, no confirmation needed (move file, rename)
  DANGEROUS — confirmation gate + audit log (shutdown, delete all, format)
  FORBIDDEN — always refused (no human should ever trigger this via voice)

This module does NOT replace confirm.py or undo.py — it layers on top:
  - classify() tells callers what risk level an action has
  - log_action() records what happened for the audit trail
  - check_rate_limit() prevents a model in a loop from hammering dangerous ops
  - should_confirm() decides if an action needs the HUD confirmation gate
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Optional


class Risk(IntEnum):
    SAFE = 0
    CAUTION = 1
    DANGEROUS = 2
    FORBIDDEN = 3


@dataclass
class AuditEntry:
    timestamp: str
    action: str
    tool: str
    risk: str
    detail: str
    confirmed: bool


# ── Action risk registry ─────────────────────────────────────────────────────
# Maps (tool_name, action_name) → Risk level.
# Wildcard tool ("*") applies to any tool.

_RISK_MAP: dict[tuple[str, str], Risk] = {
    # --- computer_settings ---
    ("computer_settings", "shutdown"):         Risk.DANGEROUS,
    ("computer_settings", "restart"):          Risk.DANGEROUS,
    ("computer_settings", "toggle_wifi"):      Risk.DANGEROUS,
    ("computer_settings", "lock_screen"):      Risk.CAUTION,
    ("computer_settings", "close_app"):        Risk.CAUTION,
    ("computer_settings", "close_window"):     Risk.CAUTION,
    ("computer_settings", "dark_mode"):        Risk.CAUTION,
    ("computer_settings", "volume_set"):       Risk.CAUTION,
    ("computer_settings", "brightness_up"):    Risk.CAUTION,
    ("computer_settings", "brightness_down"):  Risk.CAUTION,
    # --- computer_control ---
    ("computer_control", "hotkey"):            Risk.CAUTION,
    ("computer_control", "type"):              Risk.CAUTION,
    ("computer_control", "smart_type"):        Risk.CAUTION,
    ("computer_control", "click"):             Risk.SAFE,
    ("computer_control", "screenshot"):        Risk.SAFE,
    # --- file_controller ---
    ("file_controller", "delete"):             Risk.DANGEROUS,
    ("file_controller", "move"):               Risk.CAUTION,
    ("file_controller", "copy"):               Risk.CAUTION,
    ("file_controller", "rename"):             Risk.CAUTION,
    ("file_controller", "write"):              Risk.CAUTION,
    ("file_controller", "create_file"):        Risk.CAUTION,
    ("file_controller", "create_folder"):      Risk.CAUTION,
    ("file_controller", "organize_desktop"):   Risk.DANGEROUS,
    # --- desktop ---
    ("desktop_control", "clean"):              Risk.DANGEROUS,
    ("desktop_control", "organize"):           Risk.DANGEROUS,
    ("desktop_control", "wallpaper"):          Risk.CAUTION,
    # --- memory ---
    ("memory_controller", "clear"):            Risk.DANGEROUS,
    # --- gmail ---
    ("gmail", "send"):                         Risk.CAUTION,
    ("gmail", "delete"):                       Risk.DANGEROUS,
    # --- notification ---
    ("notification_action", "clear"):          Risk.CAUTION,
    # --- Wildcards ---
    ("*", "shutdown"):                         Risk.DANGEROUS,
    ("*", "restart"):                          Risk.DANGEROUS,
    ("*", "delete"):                           Risk.DANGEROUS,
    ("*", "format"):                           Risk.FORBIDDEN,
    ("*", "rm -rf"):                           Risk.FORBIDDEN,
}

# ── Rate limiting ────────────────────────────────────────────────────────────
# Maximum number of times a DANGEROUS action can fire per session window.
_DANGER_RATE_LIMIT = 5
_DANGER_WINDOW = 300  # seconds

_rate_history: dict[str, list[float]] = {}
_rate_lock = threading.Lock()

# ── Audit log ────────────────────────────────────────────────────────────────
_AUDIT_MAX = 500
_audit_log: deque[AuditEntry] = deque(maxlen=_AUDIT_MAX)
_audit_lock = threading.Lock()


def classify(tool: str, action: str) -> Risk:
    """Return the risk level for a given tool+action combination."""
    key = (tool, action)
    if key in _RISK_MAP:
        return _RISK_MAP[key]

    key_wild = ("*", action)
    if key_wild in _RISK_MAP:
        return _RISK_MAP[key_wild]

    key_tool = (tool, "*")
    if key_tool in _RISK_MAP:
        return _RISK_MAP[key_tool]

    return Risk.SAFE


def should_confirm(tool: str, action: str) -> bool:
    """Should this action go through the HUD confirmation gate?"""
    return classify(tool, action) >= Risk.DANGEROUS


def is_forbidden(tool: str, action: str) -> bool:
    """Should this action be refused outright?"""
    return classify(tool, action) >= Risk.FORBIDDEN


def check_rate_limit(action: str) -> bool:
    """Returns True if the action is allowed, False if rate-limited."""
    now = time.monotonic()
    key = action.lower()

    with _rate_lock:
        history = _rate_history.setdefault(key, [])
        # Prune entries outside the window
        history[:] = [t for t in history if now - t < _DANGER_WINDOW]
        if len(history) >= _DANGER_RATE_LIMIT:
            return False
        history.append(now)
        return True


def rate_limit_remaining(action: str) -> int:
    """How many more times this action can fire in the current window."""
    now = time.monotonic()
    key = action.lower()

    with _rate_lock:
        history = _rate_history.get(key, [])
        active = [t for t in history if now - t < _DANGER_WINDOW]
        return max(0, _DANGER_RATE_LIMIT - len(active))


def log_action(
    action: str,
    tool: str,
    risk: Optional[int] = None,
    detail: str = "",
    confirmed: bool = False,
) -> AuditEntry:
    """Record an action in the audit trail."""
    if risk is None:
        risk = classify(tool, action).value

    entry = AuditEntry(
        timestamp=datetime.now().isoformat(),
        action=action,
        tool=tool,
        risk=Risk(risk).name,
        detail=detail[:200],
        confirmed=confirmed,
    )

    with _audit_lock:
        _audit_log.append(entry)

    return entry


def get_audit_log(
    tool: Optional[str] = None,
    risk: Optional[str] = None,
    limit: int = 20,
) -> list[AuditEntry]:
    """Query the audit trail."""
    with _audit_lock:
        items = list(_audit_log)

    if tool:
        items = [e for e in items if e.tool == tool]
    if risk:
        items = [e for e in items if e.risk.upper() == risk.upper()]

    return items[-limit:]


def get_audit_stats() -> dict:
    """Summary statistics for the audit trail."""
    with _audit_lock:
        items = list(_audit_log)

    total = len(items)
    by_risk: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    confirmed_count = 0

    for e in items:
        by_risk[e.risk] = by_risk.get(e.risk, 0) + 1
        by_tool[e.tool] = by_tool.get(e.tool, 0) + 1
        if e.confirmed:
            confirmed_count += 1

    return {
        "total": total,
        "by_risk": by_risk,
        "by_tool": by_tool,
        "confirmed": confirmed_count,
    }


def clear_audit_log():
    """Clear the audit trail."""
    with _audit_lock:
        _audit_log.clear()


def reset_rate_limits():
    """Reset all rate limit counters."""
    with _rate_lock:
        _rate_history.clear()


def _risk_summary() -> str:
    """Human-readable summary of all classified actions."""
    lines = ["Action risk classification:"]
    by_tool: dict[str, list[str]] = {}
    for (tool, action), risk in sorted(_RISK_MAP.items()):
        if tool == "*":
            tool = "(any)"
        by_tool.setdefault(tool, []).append(f"  {action}: {risk.name}")
    for tool, entries in sorted(by_tool.items()):
        lines.append(f"\n{tool}:")
        lines.extend(entries)
    return "\n".join(lines)
