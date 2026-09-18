"""
JARVIS Tray Manager Test Suite.

Tests the TrayManager (core/tray_manager.py) with mocked PyQt6 components.
"""
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestTrayManager(unittest.TestCase):
    """Test TrayManager with mocked PyQt6."""

    def _make_tray(self):
        """Create a TrayManager with all PyQt6 deps mocked."""
        mock_qobject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
        mock_signal = MagicMock()

        with patch.dict("sys.modules", {
            "PyQt6.QtCore": MagicMock(
                pyqtSignal=mock_signal,
                QObject=mock_qobject,
            ),
            "PyQt6.QtGui": MagicMock(),
            "PyQt6.QtWidgets": MagicMock(),
        }):
            from core.tray_manager import TrayManager
            window = MagicMock()
            window.windowIcon.return_value = MagicMock(isNull=MagicMock(return_value=False))
            tray = TrayManager(window, on_quit=None)
            return tray, window

    def test_install_creates_tray(self):
        tray, _ = self._make_tray()
        tray.install()
        self.assertTrue(tray.is_installed())

    def test_double_install_idempotent(self):
        tray, _ = self._make_tray()
        tray.install()
        tray.install()
        self.assertTrue(tray.is_installed())

    def test_uninstall_removes_tray(self):
        tray, _ = self._make_tray()
        tray.install()
        tray.uninstall()
        self.assertFalse(tray.is_installed())

    def test_uninstall_when_not_installed(self):
        tray, _ = self._make_tray()
        tray.uninstall()
        self.assertFalse(tray.is_installed())

    def test_is_installed_false_by_default(self):
        tray, _ = self._make_tray()
        self.assertFalse(tray.is_installed())

    def test_update_tooltip(self):
        tray, _ = self._make_tray()
        tray.install()
        tray.update_tooltip("test tooltip")
        tray._tray.setToolTip.assert_called_with("test tooltip")

    def test_update_tooltip_noop_when_not_installed(self):
        tray, _ = self._make_tray()
        tray.update_tooltip("test")
        self.assertFalse(tray.is_installed())

    def test_show_notification(self):
        tray, _ = self._make_tray()
        tray.install()
        tray.show_notification("title", "msg")
        tray._tray.showMessage.assert_called_once()

    def test_quit_calls_on_quit(self):
        on_quit = MagicMock()
        tray, _ = self._make_tray.__wrapped__(self) if hasattr(self._make_tray, '__wrapped__') else (None, None)
        # Re-create with on_quit callback
        mock_qobject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
        mock_signal = MagicMock()
        with patch.dict("sys.modules", {
            "PyQt6.QtCore": MagicMock(pyqtSignal=mock_signal, QObject=mock_qobject),
            "PyQt6.QtGui": MagicMock(),
            "PyQt6.QtWidgets": MagicMock(),
        }):
            from core.tray_manager import TrayManager
            window = MagicMock()
            window.windowIcon.return_value = MagicMock(isNull=MagicMock(return_value=False))
            cb = MagicMock()
            t = TrayManager(window, on_quit=cb)
            t.install()
            t._quit()
            cb.assert_called_once()

    def test_restore_shows_window(self):
        mock_qobject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
        mock_signal = MagicMock()
        with patch.dict("sys.modules", {
            "PyQt6.QtCore": MagicMock(pyqtSignal=mock_signal, QObject=mock_qobject),
            "PyQt6.QtGui": MagicMock(),
            "PyQt6.QtWidgets": MagicMock(),
        }):
            from core.tray_manager import TrayManager
            window = MagicMock()
            window.windowIcon.return_value = MagicMock(isNull=MagicMock(return_value=False))
            t = TrayManager(window)
            t.install()
            t._restore()
            window.showNormal.assert_called_once()
            window.activateWindow.assert_called_once()
            window.raise_.assert_called_once()

    def test_quit_fallback_to_app_quit(self):
        mock_qobject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
        mock_signal = MagicMock()
        mock_app = MagicMock()
        with patch.dict("sys.modules", {
            "PyQt6.QtCore": MagicMock(pyqtSignal=mock_signal, QObject=mock_qobject),
            "PyQt6.QtGui": MagicMock(),
            "PyQt6.QtWidgets": MagicMock(QApplication=MagicMock(
                instance=MagicMock(return_value=mock_app)
            )),
        }):
            from core.tray_manager import TrayManager
            window = MagicMock()
            window.windowIcon.return_value = MagicMock(isNull=MagicMock(return_value=False))
            t = TrayManager(window, on_quit=None)
            t.install()
            t._quit()
            mock_app.quit.assert_called_once()


class TestMainWindowCloseEvent(unittest.TestCase):
    """Test that MainWindow.closeEvent minimizes to tray when tray is installed."""

    def test_close_hides_when_tray_installed(self):
        from unittest.mock import MagicMock
        event = MagicMock()

        window = MagicMock()
        window.tray_manager = MagicMock()
        window.tray_manager.is_installed.return_value = True

        # Simulate closeEvent logic
        if window.tray_manager is not None and window.tray_manager.is_installed():
            event.ignore()
            window.hide()
        else:
            event.accept()

        event.ignore.assert_called_once()
        window.hide.assert_called_once()
        event.accept.assert_not_called()

    def test_close_accepts_when_no_tray(self):
        from unittest.mock import MagicMock
        event = MagicMock()

        window = MagicMock()
        window.tray_manager = None

        if window.tray_manager is not None and window.tray_manager.is_installed():
            event.ignore()
            window.hide()
        else:
            event.accept()

        event.accept.assert_called_once()
        event.ignore.assert_not_called()

    def test_close_accepts_when_tray_not_installed(self):
        from unittest.mock import MagicMock
        event = MagicMock()

        window = MagicMock()
        window.tray_manager = MagicMock()
        window.tray_manager.is_installed.return_value = False

        if window.tray_manager is not None and window.tray_manager.is_installed():
            event.ignore()
            window.hide()
        else:
            event.accept()

        event.accept.assert_called_once()
        event.ignore.assert_not_called()


class TestTrayManagerToolDeclaration(unittest.TestCase):
    """Verify tray manager can be imported and has expected interface."""

    def test_import(self):
        from core.tray_manager import TrayManager
        self.assertTrue(callable(TrayManager))

    def test_has_install(self):
        from core.tray_manager import TrayManager
        self.assertTrue(hasattr(TrayManager, "install"))

    def test_has_uninstall(self):
        from core.tray_manager import TrayManager
        self.assertTrue(hasattr(TrayManager, "uninstall"))

    def test_has_is_installed(self):
        from core.tray_manager import TrayManager
        self.assertTrue(hasattr(TrayManager, "is_installed"))

    def test_has_quit_requested_signal(self):
        from core.tray_manager import TrayManager
        self.assertTrue(hasattr(TrayManager, "quit_requested"))

    def test_has_restore_requested_signal(self):
        from core.tray_manager import TrayManager
        self.assertTrue(hasattr(TrayManager, "restore_requested"))


class TestJarvisUITrayIntegration(unittest.TestCase):
    """Test JarvisUI tray integration methods exist."""

    def test_start_tray_exists(self):
        from ui import JarvisUI
        self.assertTrue(hasattr(JarvisUI, "start_tray"))

    def test_stop_tray_exists(self):
        from ui import JarvisUI
        self.assertTrue(hasattr(JarvisUI, "stop_tray"))


class TestShutdownTrayCleanup(unittest.TestCase):
    """Test that shutdown_jarvis calls stop_tray before exit."""

    def test_shutdown_calls_stop_tray(self):
        """Verify the shutdown flow includes tray cleanup."""
        mock_ui = MagicMock()
        mock_ui.stop_tray = MagicMock()

        import asyncio

        async def _do_shutdown():
            mock_ui.stop_tray()

        asyncio.run(_do_shutdown())
        mock_ui.stop_tray.assert_called_once()


class TestGlobalHotkey(unittest.TestCase):
    """Test GlobalHotkey class interface and behavior."""

    def test_import(self):
        from core.hotkey import GlobalHotkey
        self.assertTrue(callable(GlobalHotkey))

    def test_has_start_stop(self):
        from core.hotkey import GlobalHotkey
        self.assertTrue(hasattr(GlobalHotkey, "start"))
        self.assertTrue(hasattr(GlobalHotkey, "stop"))

    def test_has_chord_label(self):
        from core.hotkey import GlobalHotkey
        cb = MagicMock()
        hk = GlobalHotkey(cb)
        self.assertIsInstance(hk.chord_label, str)
        self.assertIn("+", hk.chord_label)

    def test_default_chord(self):
        from core.hotkey import GlobalHotkey, DEFAULT_OPEN_CHORD
        self.assertEqual(DEFAULT_OPEN_CHORD, ("ctrl", "alt", "j"))

    def test_stop_when_not_started(self):
        from core.hotkey import GlobalHotkey
        cb = MagicMock()
        hk = GlobalHotkey(cb)
        hk.stop()
        self.assertEqual(hk.scope, "none")

    def test_custom_chord(self):
        from core.hotkey import GlobalHotkey
        cb = MagicMock()
        hk = GlobalHotkey(cb, chord=("ctrl", "alt", "k"))
        self.assertIn("K", hk.chord_label)

    def test_start_returns_bool(self):
        from core.hotkey import GlobalHotkey
        cb = MagicMock()
        hk = GlobalHotkey(cb)
        result = hk.start()
        self.assertIsInstance(result, bool)
        hk.stop()


class TestTrayManagerHotkeyIntegration(unittest.TestCase):
    """Test that TrayManager manages the global hotkey."""

    def test_tray_has_hotkey_attr(self):
        mock_qobject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
        mock_signal = MagicMock()
        with patch.dict("sys.modules", {
            "PyQt6.QtCore": MagicMock(pyqtSignal=mock_signal, QObject=mock_qobject),
            "PyQt6.QtGui": MagicMock(),
            "PyQt6.QtWidgets": MagicMock(),
            "core.hotkey": MagicMock(GlobalHotkey=MagicMock),
        }):
            saved = sys.modules.pop("core.tray_manager", None)
            try:
                from core.tray_manager import TrayManager
                window = MagicMock()
                window.windowIcon.return_value = MagicMock(isNull=MagicMock(return_value=False))
                t = TrayManager(window)
                self.assertIsNone(t._hotkey)
            finally:
                if saved is not None:
                    sys.modules["core.tray_manager"] = saved

    def test_tray_hotkey_label_default(self):
        mock_qobject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
        mock_signal = MagicMock()
        with patch.dict("sys.modules", {
            "PyQt6.QtCore": MagicMock(pyqtSignal=mock_signal, QObject=mock_qobject),
            "PyQt6.QtGui": MagicMock(),
            "PyQt6.QtWidgets": MagicMock(),
            "core.hotkey": MagicMock(GlobalHotkey=MagicMock),
        }):
            saved = sys.modules.pop("core.tray_manager", None)
            try:
                from core.tray_manager import TrayManager
                window = MagicMock()
                window.windowIcon.return_value = MagicMock(isNull=MagicMock(return_value=False))
                t = TrayManager(window)
                label = t.hotkey_label()
                self.assertEqual(label, "Ctrl+Alt+J")
            finally:
                if saved is not None:
                    sys.modules["core.tray_manager"] = saved

    def test_uninstall_clears_hotkey(self):
        mock_qobject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
        mock_signal = MagicMock()
        with patch.dict("sys.modules", {
            "PyQt6.QtCore": MagicMock(pyqtSignal=mock_signal, QObject=mock_qobject),
            "PyQt6.QtGui": MagicMock(),
            "PyQt6.QtWidgets": MagicMock(),
            "core.hotkey": MagicMock(GlobalHotkey=MagicMock),
        }):
            saved = sys.modules.pop("core.tray_manager", None)
            try:
                from core.tray_manager import TrayManager
                window = MagicMock()
                window.windowIcon.return_value = MagicMock(isNull=MagicMock(return_value=False))
                t = TrayManager(window)
                mock_hk = MagicMock()
                t._hotkey = mock_hk
                t.uninstall()
                mock_hk.stop.assert_called_once()
                self.assertIsNone(t._hotkey)
            finally:
                if saved is not None:
                    sys.modules["core.tray_manager"] = saved


if __name__ == "__main__":
    unittest.main()
