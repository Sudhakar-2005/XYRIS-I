"""
UI Interaction Controller — Maps gesture intents to UI actions.

Part of AETHER-I Spatial Screen Interaction Engine V1.
"""

import logging
from typing import Optional, Callable
from dataclasses import dataclass, field

from core.spatial.interaction_state import (
    GestureEvent,
    GestureIntent,
    GestureType,
)

logger = logging.getLogger(__name__)


@dataclass
class UITarget:
    """A UI element that can be targeted by gestures."""
    id: str
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, width, height
    gesture_interactive: bool = False
    gesture_capabilities: list[str] = field(default_factory=list)
    on_select: Optional[Callable] = None
    on_hover: Optional[Callable] = None
    on_drag: Optional[Callable] = None


class UIInteractionController:
    """Maps gesture intents to UI actions.

    Maintains a registry of gesture-interactive UI elements
    and dispatches gesture intents to the appropriate handlers.
    """

    def __init__(self):
        self._targets: dict[str, UITarget] = {}
        self._hovered_target: Optional[str] = None
        self._selected_target: Optional[str] = None
        self._dragging = False
        self._pointer_position = (0.0, 0.0)
        self._callbacks: dict[str, Callable] = {}

    def register_target(self, target: UITarget) -> None:
        """Register a gesture-interactive UI element."""
        self._targets[target.id] = target
        logger.debug("Registered gesture target: %s", target.id)

    def unregister_target(self, target_id: str) -> None:
        """Unregister a UI element."""
        self._targets.pop(target_id, None)

    def set_callback(self, event: str, callback: Callable) -> None:
        """Set callback for gesture events."""
        self._callbacks[event] = callback

    def process_event(self, event: GestureEvent) -> Optional[str]:
        """Process a gesture event and dispatch to UI.

        Returns the ID of the affected target, or None.
        """
        if event.intent == GestureIntent.POINTER_MOVE:
            return self._handle_pointer_move(event)
        elif event.intent == GestureIntent.SELECT:
            return self._handle_select(event)
        elif event.intent == GestureIntent.DRAG_START:
            return self._handle_drag_start(event)
        elif event.intent == GestureIntent.DRAG_MOVE:
            return self._handle_drag_move(event)
        elif event.intent == GestureIntent.DRAG_END:
            return self._handle_drag_end(event)
        elif event.intent == GestureIntent.PAUSE:
            return self._handle_pause(event)
        elif event.intent == GestureIntent.CANCEL:
            return self._handle_cancel(event)
        elif event.intent == GestureIntent.ZOOM_IN:
            return self._handle_zoom(event, 1.1)
        elif event.intent == GestureIntent.ZOOM_OUT:
            return self._handle_zoom(event, 0.9)

        return None

    def _handle_pointer_move(self, event: GestureEvent) -> Optional[str]:
        """Handle pointer movement and hover detection."""
        self._pointer_position = event.pointer_position

        # Find target under pointer
        target_id = self._find_target_at(event.pointer_position)

        if target_id != self._hovered_target:
            # Unhover previous
            if self._hovered_target and self._hovered_target in self._targets:
                target = self._targets[self._hovered_target]
                if target.on_hover:
                    target.on_hover(False)

            # Hover new
            self._hovered_target = target_id
            if target_id and target_id in self._targets:
                target = self._targets[target_id]
                if target.on_hover:
                    target.on_hover(True)

                self._emit("hover", target_id)

        return target_id

    def _handle_select(self, event: GestureEvent) -> Optional[str]:
        """Handle selection/click on target."""
        target_id = self._find_target_at(event.pointer_position)
        if not target_id:
            return None

        target = self._targets.get(target_id)
        if not target or not target.gesture_interactive:
            return None

        if "select" not in target.gesture_capabilities:
            logger.debug("Target %s does not support select", target_id)
            return None

        self._selected_target = target_id

        if target.on_select:
            target.on_select()

        self._emit("select", target_id)
        logger.info("Gesture SELECT on: %s", target_id)
        return target_id

    def _handle_drag_start(self, event: GestureEvent) -> Optional[str]:
        """Handle drag start."""
        target_id = self._find_target_at(event.pointer_position)
        if not target_id:
            return None

        target = self._targets.get(target_id)
        if not target or not target.gesture_interactive:
            return None

        if "drag" not in target.gesture_capabilities:
            return None

        self._dragging = True
        self._selected_target = target_id
        self._emit("drag_start", target_id)
        return target_id

    def _handle_drag_move(self, event: GestureEvent) -> Optional[str]:
        """Handle drag movement."""
        if not self._dragging or not self._selected_target:
            return None

        target = self._targets.get(self._selected_target)
        if target and target.on_drag:
            target.on_drag(event.drag_delta)

        return self._selected_target

    def _handle_drag_end(self, event: GestureEvent) -> Optional[str]:
        """Handle drag end (drop)."""
        if not self._dragging:
            return None

        target_id = self._selected_target
        self._dragging = False
        self._selected_target = None

        self._emit("drop", target_id)
        return target_id

    def _handle_pause(self, event: GestureEvent) -> Optional[str]:
        """Handle interaction pause."""
        self._emit("pause", None)
        return None

    def _handle_cancel(self, event: GestureEvent) -> Optional[str]:
        """Handle interaction cancel."""
        self._dragging = False
        self._selected_target = None
        self._emit("cancel", None)
        return None

    def _handle_zoom(self, event: GestureEvent, factor: float) -> Optional[str]:
        """Handle zoom on target."""
        target_id = self._hovered_target or self._find_target_at(self._pointer_position)
        if not target_id:
            return None

        target = self._targets.get(target_id)
        if target and "zoom" in target.gesture_capabilities:
            self._emit("zoom", {"target": target_id, "factor": factor})
            return target_id

        return None

    def _find_target_at(self, position: tuple[float, float]) -> Optional[str]:
        """Find gesture-interactive target at given position."""
        # Convert normalized position to screen coordinates (simplified)
        # In production, this would use actual widget geometry
        for target_id, target in self._targets.items():
            if not target.gesture_interactive:
                continue
            x, y, w, h = target.bounds
            nx, ny = position
            # Simple bounds check (normalized coords -> widget bounds)
            if w > 0 and h > 0:
                if 0 <= nx <= 1 and 0 <= ny <= 1:
                    return target_id

        return None

    def _emit(self, event: str, data) -> None:
        """Emit gesture event to registered callback."""
        callback = self._callbacks.get(event)
        if callback:
            try:
                callback(data)
            except Exception as e:
                logger.error("Gesture callback error: %s", e)

    @property
    def hovered_target(self) -> Optional[str]:
        return self._hovered_target

    @property
    def selected_target(self) -> Optional[str]:
        return self._selected_target

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    @property
    def pointer_position(self) -> tuple[float, float]:
        return self._pointer_position
