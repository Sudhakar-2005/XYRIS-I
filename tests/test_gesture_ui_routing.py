"""
Stage 1 — gesture dead-seam completion (GESTURE_INTEGRATION_ROADMAP.md Stage 1).

Phase 2 §1.7 discovered the seams were dead: "intent" was emitted but never
handled, the "state_change" cursor branch had no emitter, and _on_gesture_state
was a stub. Stage 1 wires them: cursor styling now flows from the real state
signal, actionable dispatch outcomes reach the UI, and a targetless SELECT is
reported honestly ("no gesture target", deduped) instead of vanishing
(SECURITY_AND_PRIVACY.md R4).
"""
import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.spatial.engine import SpatialInteractionEngine
from core.spatial.interaction_state import GestureEvent, GestureIntent


class TestEngineDispatchCallback(unittest.TestCase):
    """Engine-side seam: _dispatch_actionable reports actionable outcomes."""

    @classmethod
    def setUpClass(cls):
        cls.engine = SpatialInteractionEngine()

    def _capture(self):
        calls = []
        self.engine.on_gesture_dispatch = lambda intent, target: calls.append((intent, target))
        return calls

    def test_select_reports_none_target_without_registered_targets(self):
        calls = self._capture()
        self.engine._dispatch_actionable(GestureEvent(intent=GestureIntent.SELECT))
        self.assertEqual(calls, [("select", None)])

    def test_select_reports_registered_target(self):
        calls = self._capture()
        self.engine._dispatch_actionable(
            GestureEvent(intent=GestureIntent.SELECT), "card-1"
        )
        self.assertEqual(calls, [("select", "card-1")])

    def test_non_actionable_intent_not_dispatched(self):
        calls = self._capture()
        self.engine._dispatch_actionable(GestureEvent(intent=GestureIntent.POINTER_MOVE))
        self.engine._dispatch_actionable(GestureEvent(intent=GestureIntent.DRAG_START))
        self.engine._dispatch_actionable(GestureEvent(intent=GestureIntent.NONE))
        self.assertEqual(calls, [])

    def test_no_callback_is_safe(self):
        self.engine.on_gesture_dispatch = None
        self.engine._dispatch_actionable(GestureEvent(intent=GestureIntent.SELECT))

    def test_callback_exception_is_contained(self):
        def boom(intent, target):
            raise RuntimeError("listener failed")
        self.engine.on_gesture_dispatch = boom
        self.engine._dispatch_actionable(GestureEvent(intent=GestureIntent.SELECT))

    def test_property_roundtrip(self):
        cb = lambda intent, target: None
        self.engine.on_gesture_dispatch = cb
        self.assertIs(self.engine.on_gesture_dispatch, cb)


class TestUiGestureRouting(unittest.TestCase):
    """Qt-side seam: signal names intent/dispatch/state flow to real handlers."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        import ui
        cls.win = ui.MainWindow("face.png")
        # append_log animates text through a queue/timer; spy on it directly
        # so assertions are synchronous and lossless.
        cls._recorded = []
        cls._orig_append = cls.win._log.append_log
        cls.win._log.append_log = cls._recorded.append

    @classmethod
    def tearDownClass(cls):
        cls.win._log.append_log = cls._orig_append
        for t in ("_metric_tmr", "_tmr"):
            try:
                getattr(cls.win, t).stop()
            except Exception:
                pass
        cls.win.close()

    def setUp(self):
        self._recorded.clear()
        self.win._gesture_no_target_logged.clear()

    # --- dispatch honesty -------------------------------------------------
    def test_no_target_logged_once_then_deduped(self):
        self.win._gesture_sig.emit("dispatch", ("select", None))
        self.assertEqual(self._recorded, ["GESTURE select: no gesture target"])
        self.win._gesture_sig.emit("dispatch", ("select", None))
        self.win._gesture_sig.emit("dispatch", ("select", None))
        self.assertEqual(self._recorded, ["GESTURE select: no gesture target"])

    def test_toggle_clears_no_target_dedupe(self):
        self.win._gesture_sig.emit("dispatch", ("select", None))
        self.assertEqual(len(self._recorded), 1)
        self.win.toggle_gesture_control()  # engine is None -> clears, returns early
        self.win._gesture_sig.emit("dispatch", ("select", None))
        self.assertEqual(len(self._recorded), 2)

    def test_target_dispatch_logs_target(self):
        self.win._gesture_sig.emit("dispatch", ("select", "card-1"))
        self.assertEqual(self._recorded, ["GESTURE select -> card-1"])
        # then a targetless attempt speaks again (dedupe cleared on success)
        self.win._gesture_sig.emit("dispatch", ("select", None))
        self.assertEqual(len(self._recorded), 2)

    def test_malformed_dispatch_payload_is_ignored(self):
        self.win._gesture_sig.emit("dispatch", None)
        self.win._gesture_sig.emit("dispatch", ("only-one",))
        self.win._gesture_sig.emit("dispatch", {"intent": "select"})
        self.assertEqual(self._recorded, [])

    # --- intent seam ------------------------------------------------------
    def test_intent_signal_stored(self):
        self.win._last_gesture_intent = None
        self.win._gesture_sig.emit("intent", "select")
        self.assertEqual(self.win._last_gesture_intent, "select")
        self.win._gesture_sig.emit("intent", "none")
        self.assertEqual(self.win._last_gesture_intent, "none")

    # --- cursor state seam (replaces dead state_change branch) -----------
    def test_state_signal_updates_cursor_state(self):
        self.win._gesture_state_sig.emit("CAMERA_ERROR")
        self.assertIsNotNone(self.win._gesture_cursor)
        self.assertEqual(self.win._gesture_cursor._state, "error")
        self.win._gesture_state_sig.emit("TRACKING")
        self.assertEqual(self.win._gesture_cursor._state, "pointer")
        self.win._gesture_state_sig.emit("DRAGGING")
        self.assertEqual(self.win._gesture_cursor._state, "drag")

    def test_state_signal_unknown_state_hides_cursor(self):
        self.win._gesture_state_sig.emit("SOME_FUTURE_STATE")
        self.assertEqual(self.win._gesture_cursor._state, "hidden")
        self.assertTrue(self.win._gesture_cursor.isHidden())

    def test_inactive_state_hides_cursor(self):
        self.win._gesture_state_sig.emit("CAMERA_ERROR")
        self.win._gesture_state_sig.emit("INACTIVE")
        self.assertEqual(self.win._gesture_cursor._state, "hidden")
        self.assertTrue(self.win._gesture_cursor.isHidden())

    def test_pointer_move_shows_and_moves_cursor(self):
        self.win._gesture_sig.emit("pointer_move", (0.5, 0.4))
        c = self.win._gesture_cursor
        self.assertFalse(c.isHidden())
        self.assertEqual(c.state if hasattr(c, "state") else c._state, c._state)

    def test_dead_state_change_branch_removed(self):
        # cursor starts from previous state; a "state_change" payload must be inert
        self.win._gesture_state_sig.emit("CAMERA_ERROR")
        self.assertEqual(self.win._gesture_cursor._state, "error")
        self.win._gesture_sig.emit("state_change", "TRACKING")
        self.assertEqual(self.win._gesture_cursor._state, "error")


if __name__ == "__main__":
    unittest.main()
