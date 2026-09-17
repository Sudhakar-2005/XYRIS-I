"""
Centralized Notification Manager for JARVIS.

Orchestrates all notification types with priority levels, delivery preferences,
history tracking, and OS-native desktop toast support.

Notification categories:
  - system:   Hardware alerts (CPU, RAM, temp, GPU)
  - monitor:  Background topic news alerts
  - reminder: Scheduled reminders
  - proactive: Proactive check-ins
  - gmail:    Email notifications
  - general:  Everything else

Priority levels:
  - critical: Voice + toast + beep (system failures)
  - high:     Voice + toast (reminders, important alerts)
  - medium:   Toast only (monitor alerts, email)
  - low:      History only (proactive, info)
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import threading
import time
import winsound
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Callable, Optional


class Priority(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Notification:
    id: str
    category: str
    title: str
    message: str
    priority: int
    timestamp: str
    delivered_voice: bool = False
    delivered_toast: bool = False
    read: bool = False


_OS = platform.system()
_HISTORY_MAX = 200
_COOLDOWN_DEFAULT = 60


class NotificationManager:
    """Singleton-style manager for all JARVIS notifications."""

    _instance: Optional["NotificationManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._history: deque[Notification] = deque(maxlen=_HISTORY_MAX)
        self._counter = 0
        self._last_toast: dict[str, float] = {}
        self._toast_cooldown = _COOLDOWN_DEFAULT

        self._voice_callback: Optional[Callable[[str], None]] = None
        self._toast_enabled = True
        self._voice_enabled = True
        self._beep_enabled = True

        self._category_config: dict[str, dict] = {
            "system":    {"default_priority": Priority.HIGH,    "voice": True, "toast": True,  "beep": False},
            "monitor":   {"default_priority": Priority.MEDIUM,  "voice": True, "toast": True,  "beep": False},
            "reminder":  {"default_priority": Priority.HIGH,    "voice": True, "toast": True,  "beep": True},
            "proactive": {"default_priority": Priority.LOW,     "voice": True, "toast": False, "beep": False},
            "gmail":     {"default_priority": Priority.MEDIUM,  "voice": True, "toast": True,  "beep": False},
            "general":   {"default_priority": Priority.MEDIUM,  "voice": True, "toast": True,  "beep": False},
        }

    # ── Configuration ────────────────────────────────────────────────────────

    def set_voice_callback(self, callback: Callable[[str], None]):
        self._voice_callback = callback

    def set_toast_enabled(self, enabled: bool):
        self._toast_enabled = enabled

    def set_voice_enabled(self, enabled: bool):
        self._voice_enabled = enabled

    def set_beep_enabled(self, enabled: bool):
        self._beep_enabled = enabled

    def configure_category(self, category: str, **kwargs):
        if category not in self._category_config:
            self._category_config[category] = {
                "default_priority": Priority.MEDIUM,
                "voice": True, "toast": True, "beep": False,
            }
        self._category_config[category].update(kwargs)

    def get_category_config(self, category: str) -> dict:
        return dict(self._category_config.get(category, self._category_config["general"]))

    # ── Core dispatch ────────────────────────────────────────────────────────

    def notify(
        self,
        category: str,
        title: str,
        message: str,
        priority: Optional[int] = None,
        force_toast: bool = False,
        force_voice: bool = False,
    ) -> Notification:
        """Send a notification through the appropriate channels."""
        self._counter += 1
        notif_id = f"n-{self._counter}-{int(time.time())}"

        cfg = self._category_config.get(category, self._category_config["general"])
        if priority is None:
            priority = cfg.get("default_priority", Priority.MEDIUM)

        notif = Notification(
            id=notif_id,
            category=category,
            title=title,
            message=message,
            priority=priority,
            timestamp=datetime.now().isoformat(),
        )

        self._history.append(notif)

        # Determine delivery channels
        should_voice = force_voice or (cfg.get("voice", True) and self._voice_enabled)
        should_toast = force_toast or (cfg.get("toast", True) and self._toast_enabled)
        should_beep = cfg.get("beep", False) and self._beep_enabled

        # Override: critical always gets toast + voice
        if priority >= Priority.CRITICAL:
            should_voice = should_voice or self._voice_enabled
            should_toast = should_toast or self._toast_enabled

        # Deliver
        if should_voice and self._voice_callback:
            try:
                self._voice_callback(f"[{category.upper()}_ALERT] {title}: {message}")
                notif.delivered_voice = True
            except Exception:
                pass

        if should_toast:
            if self._can_toast(category):
                self._send_toast(title, message, priority)
                notif.delivered_toast = True
                self._last_toast[category] = time.monotonic()

        if should_beep:
            self._play_beep()

        return notif

    # ── Toast delivery ───────────────────────────────────────────────────────

    def _can_toast(self, category: str) -> bool:
        last = self._last_toast.get(category, 0)
        return (time.monotonic() - last) >= self._toast_cooldown

    def _send_toast(self, title: str, message: str, priority: int):
        if _OS == "Windows":
            self._toast_windows(title, message)
        elif _OS == "Darwin":
            self._toast_macos(title, message)
        else:
            self._toast_linux(title, message)

    def _toast_windows(self, title: str, message: str):
        try:
            from win10toast import ToastNotifier
            toaster = ToastNotifier()
            toaster.show_toast(title, message, duration=10, threaded=True)
            return
        except Exception:
            pass

        try:
            import ctypes
            MB_OK = 0x00000040
            ctypes.windll.user32.MessageBoxW(0, message, title, MB_OK)
        except Exception:
            pass

    def _toast_macos(self, title: str, message: str):
        safe_title = title.replace('"', "")
        safe_msg = message.replace('"', "")
        script = f'display notification "{safe_msg}" with title "{safe_title}"'
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    def _toast_linux(self, title: str, message: str):
        try:
            subprocess.run(
                ["notify-send", "--urgency=normal", "--expire-time=10000",
                 title, message],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    def _play_beep(self):
        if _OS == "Windows":
            try:
                for freq in [800, 1000, 1200]:
                    winsound.Beep(freq, 180)
                    time.sleep(0.08)
            except Exception:
                pass

    # ── History & queries ────────────────────────────────────────────────────

    def get_history(
        self,
        category: Optional[str] = None,
        unread_only: bool = False,
        limit: int = 20,
    ) -> list[Notification]:
        items = list(self._history)
        if category:
            items = [n for n in items if n.category == category]
        if unread_only:
            items = [n for n in items if not n.read]
        return items[-limit:]

    def mark_read(self, notif_id: str) -> bool:
        for n in self._history:
            if n.id == notif_id:
                n.read = True
                return True
        return False

    def mark_all_read(self, category: Optional[str] = None):
        for n in self._history:
            if category and n.category != category:
                continue
            n.read = True

    def unread_count(self, category: Optional[str] = None) -> int:
        count = 0
        for n in self._history:
            if n.read:
                continue
            if category and n.category != category:
                continue
            count += 1
        return count

    def clear_history(self, category: Optional[str] = None):
        if category:
            self._history = deque(
                (n for n in self._history if n.category != category),
                maxlen=_HISTORY_MAX,
            )
        else:
            self._history.clear()

    def get_stats(self) -> dict:
        total = len(self._history)
        unread = sum(1 for n in self._history if not n.read)
        by_cat: dict[str, int] = {}
        by_pri: dict[str, int] = {}
        for n in self._history:
            by_cat[n.category] = by_cat.get(n.category, 0) + 1
            pri_name = Priority(n.priority).name
            by_pri[pri_name] = by_pri.get(pri_name, 0) + 1
        return {
            "total": total,
            "unread": unread,
            "by_category": by_cat,
            "by_priority": by_pri,
        }

    def to_dict_list(self, notifications: list[Notification]) -> list[dict]:
        return [asdict(n) for n in notifications]


def get_notification_manager() -> NotificationManager:
    return NotificationManager()
