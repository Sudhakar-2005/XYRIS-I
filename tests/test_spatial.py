"""
Tests for core/spatial/ — Spatial Screen Interaction Engine.
"""

import pytest
import time
from unittest.mock import MagicMock, patch

from core.spatial.interaction_state import (
    InteractionState,
    InteractionStateMachine,
    GestureType,
    GestureIntent,
    GestureEvent,
    GestureSettings,
    HandLandmarks,
)
from core.spatial.gesture_recognizer import GestureRecognizer
from core.spatial.gesture_interpreter import GestureInterpreter
from core.spatial.ui_controller import UIInteractionController, UITarget
from core.spatial.safety_controller import SafetyController


# ── InteractionStateMachine Tests ────────────────────────────────────

class TestInteractionStateMachine:

    def test_initial_state(self):
        sm = InteractionStateMachine()
        assert sm.state == InteractionState.INACTIVE

    def test_valid_transition(self):
        sm = InteractionStateMachine()
        assert sm.transition(InteractionState.ACTIVATING)
        assert sm.state == InteractionState.ACTIVATING

    def test_invalid_transition(self):
        sm = InteractionStateMachine()
        assert not sm.transition(InteractionState.DRAGGING)
        assert sm.state == InteractionState.INACTIVE

    def test_full_lifecycle(self):
        sm = InteractionStateMachine()
        sm.transition(InteractionState.ACTIVATING)
        sm.transition(InteractionState.TRACKING)
        sm.transition(InteractionState.HOVERING)
        sm.transition(InteractionState.PINCH_DETECTED)
        sm.transition(InteractionState.SELECTING)
        sm.transition(InteractionState.DRAGGING)
        sm.transition(InteractionState.TRACKING)
        sm.transition(InteractionState.INACTIVE)

    def test_pause_resume(self):
        sm = InteractionStateMachine()
        sm.transition(InteractionState.ACTIVATING)
        sm.transition(InteractionState.TRACKING)
        sm.transition(InteractionState.PAUSED)
        sm.transition(InteractionState.TRACKING)

    def test_cancel(self):
        sm = InteractionStateMachine()
        sm.transition(InteractionState.ACTIVATING)
        sm.transition(InteractionState.TRACKING)
        sm.transition(InteractionState.CANCELLED)
        sm.transition(InteractionState.TRACKING)

    def test_reset(self):
        sm = InteractionStateMachine()
        sm.transition(InteractionState.ACTIVATING)
        sm.reset()
        assert sm.state == InteractionState.INACTIVE

    def test_is_active(self):
        sm = InteractionStateMachine()
        assert not sm.is_active
        sm.transition(InteractionState.ACTIVATING)
        assert sm.is_active

    def test_is_tracking(self):
        sm = InteractionStateMachine()
        sm.transition(InteractionState.ACTIVATING)
        sm.transition(InteractionState.TRACKING)
        assert sm.is_tracking

    def test_can_interact(self):
        sm = InteractionStateMachine()
        sm.transition(InteractionState.ACTIVATING)
        sm.transition(InteractionState.TRACKING)
        assert sm.can_interact
        sm.transition(InteractionState.PAUSED)
        assert not sm.can_interact

    def test_time_in_state(self):
        sm = InteractionStateMachine()
        time.sleep(0.05)
        assert sm.time_in_state >= 0.04


# ── GestureRecognizer Tests ─────────────────────────────────────────

class TestGestureRecognizer:

    def test_recognize_no_hands(self):
        rec = GestureRecognizer()
        event = rec.recognize([], time.time())
        assert event.gesture == GestureType.NONE

    def test_make_hand_landmarks(self):
        hand = HandLandmarks(
            landmarks=[(0.5, 0.5)] * 21,
            confidence=0.9,
            palm_center=(0.5, 0.5),
            index_tip=(0.4, 0.3),
            thumb_tip=(0.42, 0.32),
        )
        assert hand.confidence == 0.9

    def test_smooth_pointer(self):
        rec = GestureRecognizer()
        p1 = rec._smooth_pointer((0.5, 0.5))
        p2 = rec._smooth_pointer((0.6, 0.5))
        assert p1 == (0.5, 0.5)
        # Smoothed should be between raw and previous
        assert 0.5 <= p2[0] <= 0.6


# ── GestureInterpreter Tests ────────────────────────────────────────

class TestGestureInterpreter:

    def test_interpret_inactive(self):
        sm = InteractionStateMachine()
        interp = GestureInterpreter(sm)
        event = GestureEvent(gesture=GestureType.POINT, confidence=0.9)
        result = interp.interpret(event)
        assert result.intent == GestureIntent.NONE

    def test_interpret_point_transitions_to_hovering(self):
        sm = InteractionStateMachine()
        sm.transition(InteractionState.ACTIVATING)
        sm.transition(InteractionState.TRACKING)
        interp = GestureInterpreter(sm)

        event = GestureEvent(
            gesture=GestureType.POINT,
            confidence=0.9,
            pointer_position=(0.5, 0.5),
        )
        result = interp.interpret(event)
        assert result.intent == GestureIntent.POINTER_MOVE
        assert sm.state == InteractionState.HOVERING

    def test_interpret_palm_pauses(self):
        sm = InteractionStateMachine()
        sm.transition(InteractionState.ACTIVATING)
        sm.transition(InteractionState.TRACKING)
        interp = GestureInterpreter(sm)

        event = GestureEvent(gesture=GestureType.OPEN_PALM, confidence=0.9)
        result = interp.interpret(event)
        assert result.intent == GestureIntent.PAUSE
        assert sm.state == InteractionState.PAUSED

    def test_interpret_fist_cancels(self):
        sm = InteractionStateMachine()
        sm.transition(InteractionState.ACTIVATING)
        sm.transition(InteractionState.TRACKING)
        interp = GestureInterpreter(sm)

        event = GestureEvent(gesture=GestureType.FIST, confidence=0.9)
        result = interp.interpret(event)
        assert result.intent == GestureIntent.CANCEL
        assert sm.state == InteractionState.CANCELLED

    def test_low_confidence_blocked(self):
        sm = InteractionStateMachine()
        sm.transition(InteractionState.ACTIVATING)
        sm.transition(InteractionState.TRACKING)
        interp = GestureInterpreter(sm)

        event = GestureEvent(gesture=GestureType.POINT, confidence=0.3)
        result = interp.interpret(event)
        assert result.intent == GestureIntent.NONE
        assert sm.state == InteractionState.LOW_CONFIDENCE


# ── UIInteractionController Tests ───────────────────────────────────

class TestUIInteractionController:

    def test_register_target(self):
        ctrl = UIInteractionController()
        target = UITarget(id="btn1", gesture_interactive=True)
        ctrl.register_target(target)
        assert ctrl._targets["btn1"] is target

    def test_unregister_target(self):
        ctrl = UIInteractionController()
        target = UITarget(id="btn1")
        ctrl.register_target(target)
        ctrl.unregister_target("btn1")
        assert "btn1" not in ctrl._targets

    def test_select_callback(self):
        ctrl = UIInteractionController()
        called = []
        target = UITarget(
            id="btn1",
            bounds=(0, 0, 100, 100),
            gesture_interactive=True,
            gesture_capabilities=["select"],
            on_select=lambda: called.append("selected"),
        )
        ctrl.register_target(target)

        event = GestureEvent(
            intent=GestureIntent.SELECT,
            pointer_position=(0.5, 0.5),
        )
        ctrl.process_event(event)
        assert "selected" in called

    def test_pause_event(self):
        ctrl = UIInteractionController()
        event = GestureEvent(intent=GestureIntent.PAUSE)
        ctrl.process_event(event)


# ── SafetyController Tests ──────────────────────────────────────────

class TestSafetyController:

    def test_emergency_stop(self):
        safety = SafetyController()
        assert not safety.emergency_stop_active
        safety.trigger_emergency_stop()
        assert safety.emergency_stop_active

    def test_emergency_blocks_events(self):
        safety = SafetyController()
        safety.trigger_emergency_stop()
        event = GestureEvent(gesture=GestureType.PINCH, confidence=0.9)
        is_safe, reason = safety.validate_event(event)
        assert not is_safe
        assert "Emergency" in reason

    def test_normal_event_passes(self):
        safety = SafetyController()
        event = GestureEvent(gesture=GestureType.POINT, confidence=0.9)
        is_safe, reason = safety.validate_event(event)
        assert is_safe

    def test_requires_confirmation(self):
        safety = SafetyController()
        assert safety.requires_confirmation("send_email")
        assert safety.requires_confirmation("delete")
        assert not safety.requires_confirmation("open_app")

    def test_rate_limiting(self):
        safety = SafetyController()
        for _ in range(5):
            event = GestureEvent(gesture=GestureType.POINT, confidence=0.9)
            is_safe, _ = safety.validate_event(event)
            assert is_safe

        # 6th should be rate limited
        event = GestureEvent(gesture=GestureType.POINT, confidence=0.9)
        is_safe, reason = safety.validate_event(event)
        assert not is_safe
        assert "Rate" in reason

    def test_action_log(self):
        safety = SafetyController()
        event = GestureEvent(gesture=GestureType.POINT, confidence=0.9)
        safety.validate_event(event)
        log = safety.get_action_log()
        assert len(log) == 1
