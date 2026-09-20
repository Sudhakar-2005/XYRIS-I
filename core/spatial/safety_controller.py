"""
Safety Controller — Ensures gesture interactions are safe.

Part of AETHER-I Spatial Screen Interaction Engine V1.
"""

import logging
import time
from typing import Optional

from core.spatial.interaction_state import (
    GestureEvent,
    GestureIntent,
    InteractionState,
)

logger = logging.getLogger(__name__)


class SafetyController:
    """Enforces safety rules for gesture interactions.

    Ensures gesture input does not bypass:
    - Permission checks
    - Confirmation dialogs
    - Authentication
    - Plugin restrictions
    - Destructive action confirmations
    """

    # Actions that always require confirmation regardless of input method
    CONFIRMATION_REQUIRED = {
        "send_email",
        "delete",
        "remove",
        "uninstall",
        "format",
        "drop",
        "kill",
        "shutdown",
        "restart",
    }

    def __init__(self):
        self._emergency_stop = False
        self._action_log: list[tuple[float, str, str]] = []
        self._max_actions_per_second = 5
        self._action_timestamps: list[float] = []

    @property
    def emergency_stop_active(self) -> bool:
        return self._emergency_stop

    def trigger_emergency_stop(self) -> None:
        """Immediately stop all gesture interactions."""
        self._emergency_stop = True
        logger.warning("EMERGENCY STOP triggered")

    def reset_emergency_stop(self) -> None:
        """Reset emergency stop."""
        self._emergency_stop = False

    def validate_event(self, event: GestureEvent) -> tuple[bool, str]:
        """Validate a gesture event is safe to execute.

        Returns (is_safe, reason).
        """
        # Emergency stop blocks everything
        if self._emergency_stop:
            return False, "Emergency stop active"

        # Rate limiting
        now = time.time()
        self._action_timestamps = [
            t for t in self._action_timestamps if now - t < 1.0
        ]
        if len(self._action_timestamps) >= self._max_actions_per_second:
            return False, "Rate limit exceeded"

        # Log the action
        self._action_timestamps.append(now)
        self._action_log.append((now, event.gesture.value, event.intent.value))

        # Keep log manageable
        if len(self._action_log) > 1000:
            self._action_log = self._action_log[-500:]

        return True, "OK"

    def requires_confirmation(self, action: str) -> bool:
        """Check if an action requires user confirmation."""
        return action.lower() in self.CONFIRMATION_REQUIRED

    def get_action_log(self, limit: int = 50) -> list[tuple[float, str, str]]:
        """Get recent action log."""
        return self._action_log[-limit:]

    def clear_log(self) -> None:
        """Clear action log."""
        self._action_log.clear()
