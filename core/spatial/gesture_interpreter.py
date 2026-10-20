"""
Gesture Interpreter — Maps recognized gestures to semantic intents.

Part of XYRIS I Spatial Screen Interaction Engine V1.
"""

import logging
import math
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
        # Stage 5b tap state (tap_action flag; holo.html:576-583)
        self._pinch_started_at = 0.0
        self._tap_anchor: Optional[tuple[float, float]] = None
        # Stage 5d peace-RESET state (peace_reset flag; holo.html:528-531)
        self._peace_streak = 0
        self._last_reset_time = 0.0

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

        # Stage 5d: any non-PEACE frame breaks the streak BEFORE the
        # confidence gate (holo.html:531 ``else st.peaceN = 0``), so uncertain
        # frames between peace poses reset the count too.
        if event.gesture != GestureType.PEACE:
            self._peace_streak = 0

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

        # Flagless repair (Stage 4e): LOW_CONFIDENCE was a trap — no branch
        # below ever handled it, so one uncertain frame (an unrecognized peace
        # pose, a two-hand NONE) passed every later gesture through raw forever
        # (probe-verified Stage 3: test_low_confidence_trap_current_behavior).
        # A confident real gesture now restores TRACKING and is re-interpreted.
        if current_state == InteractionState.LOW_CONFIDENCE:
            if (
                event.gesture != GestureType.NONE
                and event.confidence >= self._settings.confidence_threshold
            ):
                self._state.transition(InteractionState.TRACKING)
                current_state = InteractionState.TRACKING
            else:
                return event

        # Stage 5d — peace RESET (holo.html:528-531): streak === 8 exact and
        # cooldown > 2500 ms strict, so a continuous hold fires exactly once;
        # re-firing requires breaking the streak and re-earning 8 frames.
        # Only gate-passing ("interpreted") PEACE frames count. The RESET
        # event leaves the state machine untouched — it is a gesture-layer
        # intent for Stage 6 targets (capability matrix §5).
        if event.gesture == GestureType.PEACE:
            self._peace_streak += 1
            if (
                self._settings.peace_reset
                and self._peace_streak == 8
                and (event.timestamp - self._last_reset_time) * 1000.0
                > self._settings.peace_cooldown_ms
            ):
                self._last_reset_time = event.timestamp
                return GestureEvent(
                    gesture=event.gesture,
                    intent=GestureIntent.RESET,
                    confidence=event.confidence,
                    timestamp=event.timestamp,
                    hand=event.hand,
                    metadata={"peace_streak": self._peace_streak},
                )

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
            self._begin_pinch(event)
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
            # Select element under pointer. Stage 5b (tap_action ON): the
            # selection is DEFERRED to the release-time tap decision — the
            # holo philosophy (release = the action, holo.html:578) — so one
            # pinch yields at most one SELECT. OFF: immediate SELECT as today.
            self._state.transition(InteractionState.PINCH_DETECTED)
            self._begin_pinch(event)
            return GestureEvent(
                gesture=event.gesture,
                intent=GestureIntent.NONE if self._settings.tap_action
                else GestureIntent.SELECT,
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
                # Stage 5b: with tap_action ON the hold-SELECT is deferred to
                # the release-time tap check (state still becomes SELECTING
                # so the drag path stays reachable).
                return GestureEvent(
                    gesture=event.gesture,
                    intent=GestureIntent.NONE if self._settings.tap_action
                    else GestureIntent.SELECT,
                    confidence=event.confidence,
                    timestamp=event.timestamp,
                    hand=event.hand,
                    pointer_position=event.pointer_position,
                )
        else:
            # Pinch released too quickly -> back to tracking
            # Stage 5b: a tap-eligible release emits SELECT instead of NONE.
            tap = self._maybe_tap(event)
            self._state.transition(InteractionState.TRACKING)
            if tap is not None:
                return tap

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
            # Stage 5b: tap-eligible release (release <450 ms, drift <60 px)
            # selects; the hold-SELECT above was deferred under tap_action.
            tap = self._maybe_tap(event)
            self._state.transition(InteractionState.HOVERING)
            if tap is not None:
                return tap
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
            # Pinch released -> drop. Stage 5c: a flicking release (peak
            # velocity over the pinch trail > flick_threshold, observed by
            # the recognizer as metadata) becomes DISMISS instead of
            # DRAG_END — the throw-away gesture (capability matrix §5).
            # Both are reversible UI-state-only intents on the same
            # HOVERING transition; flag off ⇒ legacy DRAG_END exactly.
            self._state.transition(InteractionState.HOVERING)
            flick = (
                event.metadata.get("flick")
                if self._settings.flick_detect else None
            )
            if flick is not None:
                return GestureEvent(
                    gesture=event.gesture,
                    intent=GestureIntent.DISMISS,
                    confidence=event.confidence,
                    timestamp=event.timestamp,
                    hand=event.hand,
                    pointer_position=event.pointer_position,
                    metadata={"flick": flick, "speed": flick},
                )
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

    # ── Stage 5b tap action (flagged; holo.html:576-583) ────────────────────

    def _begin_pinch(self, event: GestureEvent) -> None:
        """Record pinch start time and palm anchor for the tap drift check."""
        self._pinch_started_at = event.timestamp
        self._tap_anchor = (
            event.hand.palm_center if event.hand is not None else None
        )

    def _maybe_tap(self, event: GestureEvent) -> Optional[GestureEvent]:
        """Tap gate: release < tap_max_ms after pinch start AND palm drift
        from the start palm < tap_max_drift (holo.html:576-583 field-tuned:
        real taps ≈ 400 ms / 50 px). Returns a SELECT event tagged
        metadata["tap"]=True, or None for a legacy release.

        Boundaries are strict-inclusive of failure: 450 ms exactly or 60 px
        exactly is NOT a tap (matches ``< 450 && < 60`` in holo).
        """
        if not self._settings.tap_action:
            return None
        if event.hand is None or self._tap_anchor is None:
            return None
        elapsed_ms = (event.timestamp - self._pinch_started_at) * 1000.0
        if elapsed_ms >= self._settings.tap_max_ms:
            return None
        palm = event.hand.palm_center
        drift = math.hypot(
            palm[0] - self._tap_anchor[0], palm[1] - self._tap_anchor[1]
        )
        if drift >= self._settings.tap_max_drift:
            return None
        return GestureEvent(
            gesture=GestureType.PINCH,
            intent=GestureIntent.SELECT,
            confidence=event.confidence,
            timestamp=event.timestamp,
            hand=event.hand,
            pointer_position=event.hand.index_tip,
            metadata={"tap": True},
        )
