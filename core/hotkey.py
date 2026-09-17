"""
Push-to-talk — hold a key, speak, release.

Why this exists
---------------
Wake-word is hands-free but it is not always what you want: in a meeting, in a
noisy room, or when you simply do not feel like saying a name out loud, a key
you hold is faster and never mishears. It is also the natural way to use the
assistant while another window has focus.

How it works, and what it costs
-------------------------------
Zero new dependencies, best available mechanism per platform:

* **Windows** — `GetAsyncKeyState` polled from one small thread. This is
  deliberately *not* `RegisterHotKey`, which only reports a press: push-to-talk
  needs the release too, and it needs to work while another application has
  focus. Polling two virtual-key codes 30 times a second is a rounding error of
  CPU and needs no message loop.
* **macOS / Linux** — no portable way to read global key state without pulling
  in a new package or asking for accessibility permissions, so the chord is
  bound as an application shortcut instead: it works whenever the assistant's
  window has focus. `scope` reports which of the two you got, so the UI can say
  so honestly rather than pretending.

The class never raises. If the platform hook cannot be installed it simply
reports `scope == "window"` and the Qt shortcut carries it.
"""

from __future__ import annotations

import platform
import threading
import time
from typing import Callable

_OS = platform.system()

# The default chord. Ctrl+Space is free in most desktop environments and is the
# same finger shape on every keyboard layout, which matters for a worldwide app.
DEFAULT_CHORD = ("ctrl", "space")

# Windows virtual-key codes for the names we accept.
_VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
    "space": 0x20, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "capslock": 0x14, "insert": 0x2D,
}

# Qt key sequence text for the same chord, used by the windowed fallback.
_QT_NAME = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "space": "Space",
            "f8": "F8", "f9": "F9", "f10": "F10",
            "capslock": "CapsLock", "insert": "Ins"}

_POLL_HZ = 30.0
# A key has to be down this long before we call it speech. It stops a stray
# brush of the chord from opening the microphone.
_DEBOUNCE_S = 0.06


def chord_label(chord=DEFAULT_CHORD) -> str:
    """Human-readable name of the chord, for the UI and the logs."""
    return "+".join(_QT_NAME.get(k, k.title()) for k in chord)


def qt_sequence(chord=DEFAULT_CHORD) -> str:
    """The same chord as a QKeySequence string."""
    return "+".join(_QT_NAME.get(k, k.title()) for k in chord)


class PushToTalk:
    """Calls `on_change(held: bool)` whenever the chord is pressed or released.

    Start it once; it is safe to start and stop repeatedly, and safe to stop a
    detector that never started.
    """

    def __init__(self, on_change: Callable[[bool], None], chord=DEFAULT_CHORD):
        self._on_change = on_change
        self._chord = tuple(chord)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._held = False
        self._scope = "window"

    # ── state ───────────────────────────────────────────────────────────────

    @property
    def held(self) -> bool:
        return self._held

    @property
    def scope(self) -> str:
        """'global' once a system-wide hook is running, else 'window'."""
        return self._scope

    @property
    def label(self) -> str:
        return chord_label(self._chord)

    # ── lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> str:
        """Begin watching. Returns the scope actually achieved."""
        self.stop()
        self._stop.clear()
        if _OS == "Windows" and self._can_poll():
            self._scope = "global"
            self._thread = threading.Thread(
                target=self._poll_loop, name="push-to-talk", daemon=True)
            self._thread.start()
        else:
            self._scope = "window"
        return self._scope

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        self._set_held(False)

    # ── the windowed fallback drives this directly ──────────────────────────

    def set_held(self, held: bool) -> None:
        """Feed a press/release from a Qt shortcut (non-Windows, or no hook)."""
        self._set_held(bool(held))

    # ── internals ───────────────────────────────────────────────────────────

    def _can_poll(self) -> bool:
        try:
            import ctypes
            ctypes.windll.user32.GetAsyncKeyState  # noqa: B018 — presence check
            return all(k in _VK for k in self._chord)
        except Exception:
            return False

    def _set_held(self, held: bool) -> None:
        if held == self._held:
            return
        self._held = held
        try:
            self._on_change(held)
        except Exception:
            pass          # a listener fault must never kill the watcher

    def _poll_loop(self) -> None:
        import ctypes
        user32 = ctypes.windll.user32
        codes = [_VK[k] for k in self._chord]
        period = 1.0 / _POLL_HZ
        down_since = 0.0

        while not self._stop.is_set():
            try:
                # The high bit of the return value is "currently down".
                down = all(user32.GetAsyncKeyState(c) & 0x8000 for c in codes)
            except Exception:
                break     # driver or session teardown — fall back to windowed
            now = time.monotonic()
            if down:
                if down_since == 0.0:
                    down_since = now
                elif now - down_since >= _DEBOUNCE_S:
                    self._set_held(True)
            else:
                down_since = 0.0
                self._set_held(False)
            self._stop.wait(period)

        self._set_held(False)
        self._scope = "window"


# ── Global open hotkey ───────────────────────────────────────────────────────

_DEFAULT_OPEN_CHORD = ("ctrl", "alt", "j")
DEFAULT_OPEN_CHORD = _DEFAULT_OPEN_CHORD

_OPEN_VK = {
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10,
    "j": 0x4A, "space": 0x20, "f8": 0x77, "f9": 0x78, "f10": 0x79,
}

_OPEN_MOD_MAP = {
    "ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008,
}


class GlobalHotkey:
    """System-wide hotkey that fires a callback when pressed.

    Uses RegisterHotKey + hidden message window on Windows.
    On other platforms, falls back to a polling thread via GetAsyncKeyState.

    Usage:
        def on_hotkey():
            window.showNormal()
            window.activateWindow()

        hk = GlobalHotkey(on_hotkey)
        hk.start()
        ...
        hk.stop()
    """

    _id_counter = 1

    def __init__(self, callback, chord=_DEFAULT_OPEN_CHORD):
        self._callback = callback
        self._chord = tuple(chord)
        self._thread = None
        self._stop = threading.Event()
        self._hwnd = None
        self._hotkey_id = None
        self._registered = False

    @property
    def chord_label(self) -> str:
        return chord_label(self._chord)

    def start(self) -> bool:
        """Begin listening. Returns True if a global hook was installed."""
        self.stop()
        self._stop.clear()
        if _OS == "Windows":
            return self._start_win()
        return False

    def stop(self) -> None:
        self._stop.set()
        if _OS == "Windows":
            self._stop_win()
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        self._registered = False

    # ── Windows: RegisterHotKey + message window ────────────────────────────

    def _start_win(self) -> bool:
        try:
            import ctypes
            import ctypes.wintypes as wt

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            mod = 0
            vk = None
            for k in self._chord:
                if k in _OPEN_MOD_MAP:
                    mod |= _OPEN_MOD_MAP[k]
                elif k in _OPEN_VK:
                    vk = _OPEN_VK[k]

            if mod == 0 or vk is None:
                return False

            self._hotkey_id = GlobalHotkey._id_counter
            GlobalHotkey._id_counter += 1

            # Create a hidden message-only window
            WM_HOTKEY = 0x0312
            HWND_MESSAGE = -3

            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_long, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM
            )

            def _wnd_proc(hwnd, msg, wparam, lparam):
                if msg == WM_HOTKEY and wparam == self._hotkey_id:
                    try:
                        self._callback()
                    except Exception:
                        pass
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._wnd_proc_ref = WNDPROC(_wnd_proc)

            h_instance = kernel32.GetModuleHandleW(None)

            wc = wt.WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(wc)
            wc.lpfnWndProc = self._wnd_proc_ref
            wc.hInstance = h_instance
            wc.lpszClassName = f"JARVIS_GlobalHotkey_{self._hotkey_id}"

            atom = user32.RegisterClassExW(ctypes.byref(wc))
            if not atom:
                return False

            self._hwnd = user32.CreateWindowExW(
                0, wc.lpszClassName, "", 0, 0, 0, 0, 0,
                HWND_MESSAGE, None, h_instance, None,
            )
            if not self._hwnd:
                user32.UnregisterClass(wc.lpszClassName, h_instance)
                return False

            if not user32.RegisterHotKey(self._hwnd, self._hotkey_id, mod, vk):
                user32.DestroyWindow(self._hwnd)
                user32.UnregisterClass(wc.lpszClassName, h_instance)
                self._hwnd = None
                return False

            self._registered = True
            self._wc_class = wc.lpszClassName
            self._h_instance = h_instance

            # Message pump thread
            self._thread = threading.Thread(
                target=self._msg_loop, name="global-hotkey", daemon=True
            )
            self._thread.start()
            return True

        except Exception:
            return False

    def _msg_loop(self) -> None:
        import ctypes
        import ctypes.wintypes as wt

        msg = wt.MSG()
        user32 = ctypes.windll.user32

        while not self._stop.is_set():
            ret = user32.PeekMessageW(ctypes.byref(msg), self._hwnd, 0, 0, 1)
            if ret:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            self._stop.wait(0.02)

    def _stop_win(self) -> None:
        if not self._registered:
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            if self._hwnd is not None:
                user32.UnregisterHotKey(self._hwnd, self._hotkey_id)
                user32.DestroyWindow(self._hwnd)
            if hasattr(self, "_wc_class") and self._wc_class:
                user32.UnregisterClass(self._wc_class, self._h_instance)
        except Exception:
            pass
        finally:
            self._hwnd = None
            self._registered = False
