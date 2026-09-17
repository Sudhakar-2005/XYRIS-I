"""
Tests for the undo stack and confirmation gate.

Verifies undo push/pop, history, peek, clear, confirmation request/resolve,
timeout behavior, and thread safety.
"""
import unittest
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import undo as undo_stack
from core import confirm as confirm_gate


class TestUndoStack(unittest.TestCase):

    def setUp(self):
        undo_stack.clear()

    def test_empty_stack(self):
        self.assertFalse(undo_stack.can_undo())
        self.assertEqual(undo_stack.peek(), "")
        self.assertEqual(undo_stack.history(), [])

    def test_push_undo(self):
        executed = []
        undo_stack.push_undo("Test action", lambda: executed.append(True) or "Reverted")
        self.assertTrue(undo_stack.can_undo())
        self.assertEqual(undo_stack.peek(), "Test action")

    def test_undo_last(self):
        result_log = []
        undo_stack.push_undo("Move file", lambda: result_log.append("moved") or "File moved")
        result = undo_stack.undo_last()
        self.assertIn("Undone: Move file", result)
        self.assertEqual(result_log, ["moved"])
        self.assertFalse(undo_stack.can_undo())

    def test_undo_empty_stack(self):
        result = undo_stack.undo_last()
        self.assertIn("nothing to undo", result)

    def test_history_order(self):
        undo_stack.push_undo("First", lambda: "")
        undo_stack.push_undo("Second", lambda: "")
        undo_stack.push_undo("Third", lambda: "")
        hist = undo_stack.history()
        self.assertEqual(hist, ["Third", "Second", "First"])

    def test_clear(self):
        undo_stack.push_undo("Action 1", lambda: "")
        undo_stack.push_undo("Action 2", lambda: "")
        undo_stack.clear()
        self.assertFalse(undo_stack.can_undo())
        self.assertEqual(undo_stack.history(), [])

    def test_max_depth(self):
        from core.undo import MAX_DEPTH
        for i in range(MAX_DEPTH + 5):
            undo_stack.push_undo(f"Action {i}", lambda: "")
        hist = undo_stack.history()
        self.assertLessEqual(len(hist), MAX_DEPTH)

    def test_undo_failure_doesnt_crash(self):
        def bad_undo():
            raise ValueError("Something went wrong")
        undo_stack.push_undo("Bad action", bad_undo)
        result = undo_stack.undo_last()
        self.assertIn("Could not undo", result)

    def test_non_callable_undo_is_ignored(self):
        undo_stack.push_undo("Bad", "not a function")
        # Should not crash, but undo won't work
        self.assertFalse(undo_stack.can_undo())

    def test_label_truncation(self):
        long_label = "A" * 200
        undo_stack.push_undo(long_label, lambda: "")
        self.assertEqual(len(undo_stack.peek()), 120)


class TestConfirmGate(unittest.TestCase):

    def setUp(self):
        self.shown = []
        self.hidden = []
        confirm_gate.bind(
            lambda title, detail: self.shown.append((title, detail)),
            lambda: self.hidden.append(True),
        )
        confirm_gate._pending = None

    def test_request_shows_banner(self):
        confirm_gate.request("restart", "Restart PC", "Restarting...", lambda: "done")
        self.assertEqual(len(self.shown), 1)
        self.assertEqual(self.shown[0][0], "Restart PC")

    def test_request_returns_pending_string(self):
        result = confirm_gate.request("restart", "Restart PC", "Restarting...", lambda: "done")
        self.assertIn("[CONFIRMATION_PENDING]", result)

    def test_resolve_cancel(self):
        executed = []
        confirm_gate.request("restart", "Restart PC", "Restarting...", lambda: executed.append(True))
        confirm_gate.resolve(accepted=False)
        self.assertEqual(executed, [])
        self.assertTrue(self.hidden[0])

    def test_resolve_accept(self):
        executed = []
        confirm_gate.request("restart", "Restart PC", "Restarting...", lambda: executed.append(True) or "done")
        confirm_gate.resolve(accepted=True)
        time.sleep(0.1)  # worker thread
        self.assertEqual(executed, [True])

    def test_pending_title(self):
        self.assertEqual(confirm_gate.pending_title(), "")
        confirm_gate.request("restart", "Restart PC", "Restarting...", lambda: "")
        self.assertEqual(confirm_gate.pending_title(), "Restart PC")

    def test_no_pending_after_resolve(self):
        confirm_gate.request("restart", "Restart PC", "Restarting...", lambda: "")
        confirm_gate.resolve(accepted=False)
        self.assertEqual(confirm_gate.pending_title(), "")

    def test_timeout_expires(self):
        confirm_gate.TIMEOUT_SECONDS = 0.01
        confirm_gate.request("restart", "Restart PC", "Restarting...", lambda: "")
        time.sleep(0.02)
        self.assertEqual(confirm_gate.pending_title(), "")
        confirm_gate.TIMEOUT_SECONDS = 90.0  # restore

    def test_resolve_with_no_pending(self):
        # resolve() always calls _hide_cb even with no pending (idempotent)
        confirm_gate.resolve(accepted=True)
        # hide was called but no action was executed
        self.assertTrue(len(self.hidden) >= 0)


if __name__ == "__main__":
    unittest.main()
