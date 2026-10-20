"""
Stage 2 — status honesty (GESTURE_INTEGRATION_ROADMAP.md Stage 2).

CameraManager gains `last_error` in the only vocabulary cv2 provides
("not_opened" / "exception: …"), the toggle's failure line tells that truth
(SECURITY_AND_PRIVACY.md R4), and real state-machine transitions now reach
the UI through the previously-uninvoked on_state_change seam (Phase 2 §1.7).
The INACTIVE->CAMERA_ERROR edge is added because engine.start() itself
attempts it (engine.py) and the table used to reject it.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.spatial.camera_manager import CameraManager
from core.spatial.engine import SpatialInteractionEngine
from core.spatial.interaction_state import (
    InteractionState,
    InteractionStateMachine,
)


class FakeCapClosed:
    """cv2.VideoCapture double that never opens."""

    def __init__(self, *a, **k):
        pass

    def isOpened(self):
        return False

    def release(self):
        pass


class FakeCapOpen:
    """Opens, but yields no frames - the capture loop idles until stop()."""

    def __init__(self, *a, **k):
        self._released = False

    def isOpened(self):
        return not self._released

    def get(self, prop):
        return 640

    def read(self):
        return False, None

    def release(self):
        self._released = True


class TestCameraLastError(unittest.TestCase):
    def test_initial_error_is_none(self):
        self.assertIsNone(CameraManager().last_error)

    def test_not_opened_recorded(self):
        cm = CameraManager(3)
        with mock.patch(
            "core.spatial.camera_manager.cv2.VideoCapture",
            return_value=FakeCapClosed(),
        ):
            self.assertFalse(cm.start())
        self.assertEqual(cm.last_error, "not_opened")
        self.assertFalse(cm.is_running)

    def test_exception_recorded_verbatim(self):
        cm = CameraManager(0)
        with mock.patch(
            "core.spatial.camera_manager.cv2.VideoCapture",
            side_effect=RuntimeError("boom"),
        ):
            self.assertFalse(cm.start())
        self.assertEqual(cm.last_error, "exception: boom")

    def test_successful_start_clears_error(self):
        cm = CameraManager(0)
        with mock.patch(
            "core.spatial.camera_manager.cv2.VideoCapture",
            return_value=FakeCapClosed(),
        ):
            self.assertFalse(cm.start())
        self.assertEqual(cm.last_error, "not_opened")
        with mock.patch(
            "core.spatial.camera_manager.cv2.VideoCapture",
            return_value=FakeCapOpen(),
        ):
            self.assertTrue(cm.start())
            self.assertIsNone(cm.last_error)
            self.assertTrue(cm.is_running)
            cm.stop()
        self.assertFalse(cm.is_running)


class TestStateTransitionCallback(unittest.TestCase):
    def test_valid_transition_fires_callback(self):
        sm = InteractionStateMachine()
        seen = []
        sm.on_transition = lambda old, new: seen.append((old, new))
        self.assertTrue(sm.transition(InteractionState.ACTIVATING))
        self.assertEqual(
            seen, [(InteractionState.INACTIVE, InteractionState.ACTIVATING)]
        )

    def test_invalid_transition_does_not_fire_callback(self):
        sm = InteractionStateMachine()
        seen = []
        sm.on_transition = lambda old, new: seen.append((old, new))
        self.assertFalse(sm.transition(InteractionState.DRAGGING))
        self.assertEqual(seen, [])
        self.assertEqual(sm.state, InteractionState.INACTIVE)

    def test_camera_error_valid_from_inactive(self):
        # engine.start() attempts this exact edge on camera failure.
        sm = InteractionStateMachine()
        self.assertTrue(sm.transition(InteractionState.CAMERA_ERROR))
        self.assertEqual(sm.state, InteractionState.CAMERA_ERROR)

    def test_same_state_returns_true_without_callback(self):
        sm = InteractionStateMachine()
        seen = []
        sm.on_transition = lambda old, new: seen.append((old, new))
        self.assertTrue(sm.transition(InteractionState.INACTIVE))
        self.assertEqual(seen, [])

    def test_reset_fires_callback_only_when_state_changes(self):
        sm = InteractionStateMachine()
        seen = []
        sm.on_transition = lambda old, new: seen.append((old, new))
        sm.reset()
        self.assertEqual(seen, [])  # already INACTIVE
        sm.transition(InteractionState.ACTIVATING)
        seen.clear()
        sm.reset()
        self.assertEqual(
            seen, [(InteractionState.ACTIVATING, InteractionState.INACTIVE)]
        )

    def test_callback_exception_is_contained(self):
        sm = InteractionStateMachine()

        def boom(old, new):
            raise RuntimeError("listener failed")

        sm.on_transition = boom
        self.assertTrue(sm.transition(InteractionState.ACTIVATING))
        self.assertEqual(sm.state, InteractionState.ACTIVATING)


class TestEngineStateForwarding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = SpatialInteractionEngine()

    def setUp(self):
        # the engine instance is shared; always start each test from INACTIVE
        self.engine.on_state_change = None
        self.engine._state_machine.reset()

    def tearDown(self):
        self.engine.on_state_change = None

    def test_engine_wires_state_machine_at_init(self):
        self.assertIsNotNone(self.engine._state_machine.on_transition)

    def test_assigned_state_callback_receives_transitions(self):
        seen = []
        self.engine.on_state_change = lambda old, new: seen.append((old, new))
        try:
            self.assertTrue(
                self.engine._state_machine.transition(InteractionState.ACTIVATING)
            )
            self.assertEqual(
                seen, [(InteractionState.INACTIVE, InteractionState.ACTIVATING)]
            )
        finally:
            self.engine.on_state_change = None

    def test_callback_exception_is_contained(self):
        def boom(old, new):
            raise RuntimeError("listener failed")

        self.engine.on_state_change = boom
        try:
            self.engine._state_machine.transition(InteractionState.ACTIVATING)
            # engine still consistent
            self.assertEqual(self.engine.state, InteractionState.ACTIVATING)
        finally:
            self.engine.on_state_change = None
            self.engine._state_machine.reset()

    def test_no_callback_is_safe(self):
        self.engine.on_state_change = None
        self.engine._state_machine.transition(InteractionState.CAMERA_ERROR)
        self.engine._state_machine.reset()


class _FakeCamera:
    def __init__(self, last_error):
        self.last_error = last_error


class _FakeEngine:
    def __init__(self, last_error):
        self.is_running = False
        self.camera = _FakeCamera(last_error)

    def start(self):
        return False

    def stop(self):
        self.is_running = False


class TestToggleCameraWording(unittest.TestCase):
    """The toggle failure line must tell only what cv2 actually reported."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        import ui
        cls.win = ui.MainWindow("face.png")
        cls._recorded = []
        cls._orig_append = cls.win._log.append_log
        cls.win._log.append_log = cls._recorded.append
        cls._orig_engine = cls.win._gesture_engine

    @classmethod
    def tearDownClass(cls):
        cls.win._log.append_log = cls._orig_append
        cls.win._gesture_engine = cls._orig_engine
        for t in ("_metric_tmr", "_tmr"):
            try:
                getattr(cls.win, t).stop()
            except Exception:
                pass
        cls.win.close()

    def setUp(self):
        self._recorded.clear()

    def tearDown(self):
        self.win._gesture_engine = self._orig_engine

    def test_not_opened_wording_is_cautious(self):
        self.win._gesture_engine = _FakeEngine("not_opened")
        self.win.toggle_gesture_control()
        self.assertEqual(len(self._recorded), 1)
        line = self._recorded[0]
        self.assertIn("Gesture control unavailable", line)
        self.assertIn("missing or in use", line)
        # never invents a cause cv2 did not give
        self.assertNotIn("blocked", line.lower())
        self.assertNotIn("permission", line.lower())

    def test_exception_text_surfaces_verbatim(self):
        self.win._gesture_engine = _FakeEngine("exception: device gone")
        self.win.toggle_gesture_control()
        self.assertEqual(len(self._recorded), 1)
        self.assertIn("device gone", self._recorded[0])

    def test_no_error_detail_when_unknown(self):
        self.win._gesture_engine = _FakeEngine(None)
        self.win.toggle_gesture_control()
        self.assertEqual(
            self._recorded, ["Gesture control unavailable — camera error"]
        )


if __name__ == "__main__":
    unittest.main()
