"""
JARVIS Tray Manager — System tray icon and background operation.

Provides:
- System tray icon with context menu (Restore, Quit)
- Minimize-to-tray on window close
- Background operation while window is hidden
- Global hotkey (Ctrl+Alt+J) to restore from anywhere
- Graceful quit that saves session state
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtCore import pyqtSignal, QObject, QMetaObject, Qt
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

from core.hotkey import GlobalHotkey

if TYPE_CHECKING:
    from ui import MainWindow


class TrayManager(QObject):
    """
    Manages the system tray icon for JARVIS.

    Usage:
        tray = TrayManager(main_window, on_quit=my_quit_handler)
        tray.install()
    """

    quit_requested = pyqtSignal()
    restore_requested = pyqtSignal()
    _hotkey_restore_sig = pyqtSignal()

    def __init__(
        self,
        window: "MainWindow",
        on_quit: Optional[Callable[[], None]] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent or window)
        self._window = window
        self._on_quit = on_quit
        self._tray: Optional[QSystemTrayIcon] = None
        self._hotkey: Optional[GlobalHotkey] = None
        self._hotkey_restore_sig.connect(self._restore, Qt.ConnectionType.QueuedConnection)

    def install(self) -> None:
        """Create and show the tray icon and global hotkey."""
        if self._tray is not None:
            return

        self._tray = QSystemTrayIcon(self)

        icon = self._window.windowIcon()
        if icon.isNull():
            icon = QIcon()
        self._tray.setIcon(icon)

        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background-color: #1a1a2e; color: #e0e0e0; border: 1px solid #0ff; }"
            "QMenu::item:selected { background-color: #16213e; }"
        )

        restore_action = QAction("Restore  (Ctrl+Alt+J)", menu)
        restore_action.triggered.connect(self._restore)
        menu.addAction(restore_action)

        menu.addSeparator()

        quit_action = QAction("Quit JARVIS", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.setToolTip("JARVIS — Mark LIV  (Ctrl+Alt+J to open)")
        self._tray.show()

        # Start the global open hotkey — callback emits a signal so the
        # actual Qt work happens on the main thread.
        self._hotkey = GlobalHotkey(self._hotkey_restore_sig.emit)
        self._hotkey.start()

    def uninstall(self) -> None:
        """Remove the tray icon and global hotkey."""
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None
        if self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()
            self._tray = None

    def is_installed(self) -> bool:
        return self._tray is not None and self._tray.isVisible()

    def update_tooltip(self, text: str) -> None:
        if self._tray is not None:
            self._tray.setToolTip(text)

    def show_notification(self, title: str, message: str, duration_ms: int = 3000) -> None:
        if self._tray is not None:
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, duration_ms)

    def hotkey_label(self) -> str:
        """Return the human-readable hotkey label for display."""
        if self._hotkey is not None:
            return self._hotkey.chord_label
        return "Ctrl+Alt+J"

    def _on_activated(self, reason: int) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            pass

    def _restore(self) -> None:
        self._window.showNormal()
        self._window.activateWindow()
        self._window.raise_()
        self.restore_requested.emit()

    def _quit(self) -> None:
        self.quit_requested.emit()
        if self._on_quit is not None:
            self._on_quit()
        else:
            app = QApplication.instance()
            if app is not None:
                app.quit()
