"""
Gesture Interpreter — Maps recognized gestures to semantic intents.

Part of AETHER-I Spatial Screen Interaction Engine V1.
"""

import logging
import time
from typing import Optional

from core.spatial.interaction_state import (
    GestureType,
    GestureIntent,
    GestureEvent,
    InteractionState,
    InteractionStateMachine,
    GestureSettings,
)

logger = logging.getLogger(__name__)


class GestureInterpreter:
    """Interprets recognized gestures into semantic intents.

    Applies state machine logic, anti-false-positive checks,
    and converts raw gestures into actionable UI intents.
    """

    def __init__(
        self,
        state_machine: InteractionStateMachine,
        settings: Optional[GestureSettings] = None,
    ):
        self._state = state_machine
        self._settings = settings or GestureSettings()
        self._last_intent_time = 0.0
        self._last_pointer_pos = (0.0, 0.0)
        self._hover_target: Optional[str] = None

    def interpret(self, event: GestureEvent) -> GestureEvent:
        """Interpret a gesture event into a semantic intent.

        Args:
            event: Raw gesture event from recognizer

        Returns:
            GestureEvent with intent set based on state machine
        """
        if not self._state.is_active:
            return GestureEvent(
                gesture=GestureType.NONE,
                intent=GestureIntent.NONE,
                confidence=0.0,
                timestamp=event.timestamp,
            )

        # Check confidence threshold
        if event.confidence < self._settings.confidence_threshold:
            if self._state.state != InteractionState.LOW_CONFIDENCE:
                self._state.transition(InteractionState.LOW_CONFIDENCE)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.NONE,
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
            )

        # Interpret based on current state
        current_state = self._state.state

        if current_state == InteractionState.TRACKING:
            return self._interpret_tracking(event)
        elif current_state == InteractionState.HOVERING:
            return self._interpret_hovering(event)
        elif current_state == InteractionState.PINCH_DETECTED:
            return self._interpret_pinch_detected(event)
        elif current_state == InteractionState.SELECTING:
            return self._interpret_selecting(event)
        elif current_state == InteractionState.DRAGGING:
            return self._interpret_dragging(event)
        elif current_state == InteractionState.PAUSED:
            return self._interpret_paused(event)

        return event

    def _interpret_tracking(self, event: GestureEvent) -> GestureEvent:
        """Interpret gesture while in TRACKING state."""
        if event.gesture == GestureType.POINT:
            self._state.transition(InteractionState.HOVERING)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.POINTER_MOVE,
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
                pointer_position=event.pointer_position,
            )

        elif event.gesture == GestureType.OPEN_PALM:
            self._state.transition(InteractionState.PAUSED)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.PAUSE,
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
            )

        elif event.gesture == GestureType.FIST:
            self._state.transition(InteractionState.CANCELLED)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.CANCEL,
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
            )

        elif event.gesture == GestureType.PINCH:
            self._state.transition(InteractionState.PINCH_DETECTED)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.NONE,  # Waiting for validation
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
                pointer_position=event.pointer_position,
            )

        return GestureEvent(
            gesture=GestureType.NONE,
            intent=GestureIntent.NONE,
            confidence=event.confidence,
            timestamp=event.timestamp,
        )

    def _interpret_hovering(self, event: GestureEvent) -> GestureEvent:
        """Interpret gesture while in HOVERING state."""
        if event.gesture == GestureType.POINT:
            # Continue pointer movement
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.POINTER_MOVE,
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
                pointer_position=event.pointer_position,
            )

        elif event.gesture == GestureType.PINCH:
            # Select element under pointer
            self._state.transition(InteractionState.PINCH_DETECTED)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.SELECT,
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
                pointer_position=event.pointer_position,
            )

        elif event.gesture == GestureType.OPEN_PALM:
            self._state.transition(InteractionState.PAUSED)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.PAUSE,
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
            )

        elif event.gesture == GestureType.FIST:
            self._state.transition(InteractionState.CANCELLED)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.CANCEL,
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
            )

        # Lost hand or no gesture -> return to tracking
        elif event.gesture == GestureType.NONE:
            self._state.transition(InteractionState.TRACKING)

        return GestureEvent(
            gesture=event.gesture,
            intent=GestureIntent.NONE,
            confidence=event.confidence,
            timestamp=event.timestamp,
        )

    def _interpret_pinch_detected(self, event: GestureEvent) -> GestureEvent:
        """Interpret gesture while in PINCH_DETECTED state (validating)."""
        if event.gesture == GestureType.PINCH:
            # Pinch held long enough -> confirm selection
            time_in_state = self._state.time_in_state * 1000
            if time_in_state >= self._settings.min_hold_duration_ms:
                self._state.transition(InteractionState.SELECTING)
                return GestureEvent(
                    gesture=event.gesture,
                    intent=GestureIntent.SELECT,
                    confidence=event.confidence,
                    timestamp=event.timestamp,
                    hand=event.hand,
                    pointer_position=event.pointer_position,
                )
        else:
            # Pinch released too quickly -> back to tracking
            self._state.transition(InteractionState.TRACKING)

        return GestureEvent(
            gesture=event.gesture,
            intent=GestureIntent.NONE,
            confidence=event.confidence,
            timestamp=event.timestamp,
        )

    def _interpret_selecting(self, event: GestureEvent) -> GestureEvent:
        """Interpret gesture while in SELECTING state."""
        if event.gesture == GestureType.PINCH:
            # Check for movement -> drag
            if event.hand:
                delta = (
                    event.pointer_position[0] - self._last_pointer_pos[0],
                    event.pointer_position[1] - self._last_pointer_pos[1],
                )
                if abs(delta[0]) > self._settings.movement_threshold or abs(delta[1]) > self._settings.movement_threshold:
                    self._state.transition(InteractionState.DRAGGING)
                    return GestureEvent(
                        gesture=event.gesture,
                        intent=GestureIntent.DRAG_START,
                        confidence=event.confidence,
                        timestamp=event.timestamp,
                        hand=event.hand,
                        pointer_position=event.pointer_position,
                        drag_delta=delta,
                    )

            self._last_pointer_pos = event.pointer_position
            return event

        else:
            # Pinch released -> back to hovering/tracking
            self._state.transition(InteractionState.HOVERING)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.NONE,
                confidence=event.confidence,
                timestamp=event.timestamp,
            )

    def _interpret_dragging(self, event: GestureEvent) -> GestureEvent:
        """Interpret gesture while in DRAGGING state."""
        if event.gesture == GestureType.PINCH and event.hand:
            delta = (
                event.pointer_position[0] - self._last_pointer_pos[0],
                event.pointer_position[1] - self._last_pointer_pos[1],
            )
            self._last_pointer_pos = event.pointer_position

            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.DRAG_MOVE,
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
                pointer_position=event.pointer_position,
                drag_delta=delta,
            )

        else:
            # Pinch released -> drop
            self._state.transition(InteractionState.HOVERING)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.DRAG_END,
                confidence=event.confidence,
                timestamp=event.timestamp,
                hand=event.hand,
                pointer_position=event.pointer_position,
            )

    def _interpret_paused(self, event: GestureEvent) -> GestureEvent:
        """Interpret gesture while in PAUSED state."""
        if event.gesture != GestureType.OPEN_PALM:
            # Any other gesture -> resume
            self._state.transition(InteractionState.TRACKING)

        return GestureEvent(
            gesture=event.gesture,
            intent=GestureIntent.RESUME if event.gesture != GestureType.OPEN_PALM else GestureIntent.NONE,
            confidence=event.confidence,
            timestamp=event.timestamp,
        )
