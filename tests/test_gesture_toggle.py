"""Gesture toggle logs through the log panel — MainWindow has no write_log.

A live crash (app died on boot after enabling gestures): the handler called
self.write_log, which only exists on JarvisUI, so both the happy path and
its own except handler raised AttributeError and PyQt6 aborted the slot."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestGestureToggleLogging(unittest.TestCase):

    def test_toggle_logs_via_log_panel_and_never_calls_missing_method(self):
        import memory.config_manager as cm
        from ui import MainWindow

        old_get, old_set = cm.is_gesture_enabled, cm.set_gesture_enabled
        cm.is_gesture_enabled = lambda: False
        cm.set_gesture_enabled = lambda v: None
        try:
            logged = []
            fake = SimpleNamespace(
                _gesture_engine=None,
                on_gesture_engine_needed=None,
                _refresh_gesture_btn=lambda: None,
                _log=SimpleNamespace(append_log=logged.append),
            )
            MainWindow._toggle_gesture_control(fake)   # must not raise
        finally:
            cm.is_gesture_enabled, cm.set_gesture_enabled = old_get, old_set

        self.assertTrue(logged)                        # engine-unavailable line
        self.assertTrue(all(isinstance(x, str) and x for x in logged))

    def test_source_never_uses_write_log_on_mainwindow_handler(self):
        src = (PROJECT_ROOT / "ui.py").read_text(encoding="utf-8")
        i = src.index("def _toggle_gesture_control")
        j = src.index("def _toggle_hud_style")
        body = src[i:j]
        self.assertNotIn("self.write_log(", body)
        self.assertIn("self._log.append_log(", body)


if __name__ == "__main__":
    unittest.main()
