"""
JARVIS Notification Intelligence Test Suite.

Tests the NotificationManager (core/notification_manager.py) and
notification_action tool (actions/notification_action.py) with
mocked OS-native toast and voice delivery.
"""
import unittest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.notification_manager import (
    NotificationManager, Notification, Priority, get_notification_manager,
)
from actions.notification_action import notification_action


def _fresh_manager():
    """Create a fresh NotificationManager bypassing the singleton."""
    mgr = object.__new__(NotificationManager)
    mgr._initialized = True
    mgr._history = __import__("collections").deque(maxlen=200)
    mgr._counter = 0
    mgr._last_toast = {}
    mgr._toast_cooldown = 0
    mgr._voice_callback = None
    mgr._toast_enabled = True
    mgr._voice_enabled = True
    mgr._beep_enabled = True
    mgr._category_config = {
        "system":    {"default_priority": Priority.HIGH,    "voice": True, "toast": True,  "beep": False},
        "monitor":   {"default_priority": Priority.MEDIUM,  "voice": True, "toast": True,  "beep": False},
        "reminder":  {"default_priority": Priority.HIGH,    "voice": True, "toast": True,  "beep": True},
        "proactive": {"default_priority": Priority.LOW,     "voice": True, "toast": False, "beep": False},
        "gmail":     {"default_priority": Priority.MEDIUM,  "voice": True, "toast": True,  "beep": False},
        "general":   {"default_priority": Priority.MEDIUM,  "voice": True, "toast": True,  "beep": False},
    }
    return mgr


class TestNotificationDataclass(unittest.TestCase):
    """Test the Notification dataclass."""

    def test_notification_creation(self):
        n = Notification(
            id="n-1", category="test", title="T", message="M",
            priority=Priority.MEDIUM, timestamp="2025-01-01T00:00:00",
        )
        self.assertEqual(n.id, "n-1")
        self.assertFalse(n.read)
        self.assertFalse(n.delivered_voice)
        self.assertFalse(n.delivered_toast)

    def test_notification_asdict(self):
        from dataclasses import asdict
        n = Notification(
            id="n-1", category="test", title="T", message="M",
            priority=Priority.MEDIUM, timestamp="2025-01-01T00:00:00",
        )
        d = asdict(n)
        self.assertIsInstance(d, dict)
        self.assertEqual(d["id"], "n-1")


class TestPriority(unittest.TestCase):
    """Test Priority enum."""

    def test_ordering(self):
        self.assertLess(Priority.LOW, Priority.MEDIUM)
        self.assertLess(Priority.MEDIUM, Priority.HIGH)
        self.assertLess(Priority.HIGH, Priority.CRITICAL)

    def test_values(self):
        self.assertEqual(Priority.LOW, 0)
        self.assertEqual(Priority.MEDIUM, 1)
        self.assertEqual(Priority.HIGH, 2)
        self.assertEqual(Priority.CRITICAL, 3)


class TestNotificationManagerInit(unittest.TestCase):
    """Test manager initialization and configuration."""

    def test_singleton_same_instance(self):
        mgr1 = NotificationManager()
        mgr2 = NotificationManager()
        self.assertIs(mgr1, mgr2)

    def test_fresh_manager_has_categories(self):
        mgr = _fresh_manager()
        self.assertIn("system", mgr._category_config)
        self.assertIn("reminder", mgr._category_config)

    def test_fresh_manager_history_empty(self):
        mgr = _fresh_manager()
        self.assertEqual(len(mgr._history), 0)

    def test_set_voice_callback(self):
        mgr = _fresh_manager()
        cb = lambda msg: None
        mgr.set_voice_callback(cb)
        self.assertIs(mgr._voice_callback, cb)

    def test_set_toast_enabled(self):
        mgr = _fresh_manager()
        mgr.set_toast_enabled(False)
        self.assertFalse(mgr._toast_enabled)

    def test_set_voice_enabled(self):
        mgr = _fresh_manager()
        mgr.set_voice_enabled(False)
        self.assertFalse(mgr._voice_enabled)

    def test_set_beep_enabled(self):
        mgr = _fresh_manager()
        mgr.set_beep_enabled(False)
        self.assertFalse(mgr._beep_enabled)


class TestNotificationManagerNotify(unittest.TestCase):
    """Test the notify() dispatch method."""

    def test_notify_creates_notification(self):
        mgr = _fresh_manager()
        mgr.set_toast_enabled(False)
        notif = mgr.notify("general", "Test", "Hello", priority=Priority.LOW)
        self.assertIsInstance(notif, Notification)
        self.assertEqual(notif.category, "general")
        self.assertEqual(notif.title, "Test")

    def test_notify_records_in_history(self):
        mgr = _fresh_manager()
        mgr.set_toast_enabled(False)
        mgr.notify("general", "T", "M")
        self.assertEqual(len(mgr._history), 1)

    def test_notify_voice_delivered(self):
        mgr = _fresh_manager()
        cb = MagicMock()
        mgr.set_voice_callback(cb)
        mgr.set_toast_enabled(False)
        notif = mgr.notify("general", "T", "M", priority=Priority.LOW)
        self.assertTrue(notif.delivered_voice)
        cb.assert_called_once()

    def test_notify_voice_not_delivered_when_disabled(self):
        mgr = _fresh_manager()
        mgr.set_voice_enabled(False)
        mgr.set_toast_enabled(False)
        cb = MagicMock()
        mgr.set_voice_callback(cb)
        notif = mgr.notify("general", "T", "M")
        self.assertFalse(notif.delivered_voice)
        cb.assert_not_called()

    def test_notify_no_voice_without_callback(self):
        mgr = _fresh_manager()
        mgr.set_toast_enabled(False)
        notif = mgr.notify("general", "T", "M")
        self.assertFalse(notif.delivered_voice)

    @patch("core.notification_manager.NotificationManager._send_toast")
    def test_notify_toast_delivered(self, mock_toast):
        mgr = _fresh_manager()
        notif = mgr.notify("general", "T", "M", priority=Priority.LOW)
        self.assertTrue(notif.delivered_toast)
        mock_toast.assert_called_once()

    @patch("core.notification_manager.NotificationManager._send_toast")
    def test_notify_toast_not_delivered_when_disabled(self, mock_toast):
        mgr = _fresh_manager()
        mgr.set_toast_enabled(False)
        notif = mgr.notify("general", "T", "M")
        self.assertFalse(notif.delivered_toast)
        mock_toast.assert_not_called()

    @patch("core.notification_manager.NotificationManager._send_toast")
    def test_notify_critical_gets_toast_even_if_category_disabled(self, mock_toast):
        mgr = _fresh_manager()
        mgr.configure_category("test_cat", toast=False)
        notif = mgr.notify("test_cat", "T", "M", priority=Priority.CRITICAL)
        self.assertTrue(notif.delivered_toast)

    def test_notify_increments_counter(self):
        mgr = _fresh_manager()
        mgr.set_toast_enabled(False)
        n1 = mgr.notify("general", "A", "B")
        n2 = mgr.notify("general", "C", "D")
        self.assertNotEqual(n1.id, n2.id)

    @patch("core.notification_manager.NotificationManager._play_beep")
    def test_notify_beep_for_reminder(self, mock_beep):
        mgr = _fresh_manager()
        mgr.notify("reminder", "T", "M")
        mock_beep.assert_called_once()

    @patch("core.notification_manager.NotificationManager._play_beep")
    def test_notify_no_beep_for_system(self, mock_beep):
        mgr = _fresh_manager()
        mgr.notify("system", "T", "M")
        mock_beep.assert_not_called()


class TestNotificationManagerHistory(unittest.TestCase):
    """Test history queries and management."""

    def _populate(self, mgr, count=5):
        for i in range(count):
            mgr.notify("general", f"Title {i}", f"Msg {i}")

    def test_get_history_empty(self):
        mgr = _fresh_manager()
        self.assertEqual(mgr.get_history(), [])

    def test_get_history_returns_recent(self):
        mgr = _fresh_manager()
        self._populate(mgr, 3)
        history = mgr.get_history()
        self.assertEqual(len(history), 3)

    def test_get_history_limit(self):
        mgr = _fresh_manager()
        self._populate(mgr, 10)
        history = mgr.get_history(limit=3)
        self.assertEqual(len(history), 3)

    def test_get_history_by_category(self):
        mgr = _fresh_manager()
        mgr.notify("general", "A", "B")
        mgr.notify("system", "C", "D")
        mgr.notify("general", "E", "F")
        history = mgr.get_history(category="general")
        self.assertEqual(len(history), 2)

    def test_get_history_unread_only(self):
        mgr = _fresh_manager()
        n1 = mgr.notify("general", "A", "B")
        mgr.notify("general", "C", "D")
        mgr.mark_read(n1.id)
        unread = mgr.get_history(unread_only=True)
        self.assertEqual(len(unread), 1)

    def test_mark_read(self):
        mgr = _fresh_manager()
        n = mgr.notify("general", "T", "M")
        self.assertFalse(n.read)
        result = mgr.mark_read(n.id)
        self.assertTrue(result)
        self.assertTrue(n.read)

    def test_mark_read_nonexistent(self):
        mgr = _fresh_manager()
        self.assertFalse(mgr.mark_read("nonexistent"))

    def test_mark_all_read(self):
        mgr = _fresh_manager()
        self._populate(mgr, 5)
        mgr.mark_all_read()
        self.assertEqual(mgr.unread_count(), 0)

    def test_mark_all_read_by_category(self):
        mgr = _fresh_manager()
        mgr.notify("general", "A", "B")
        mgr.notify("system", "C", "D")
        mgr.mark_all_read(category="general")
        self.assertEqual(mgr.unread_count("general"), 0)
        self.assertEqual(mgr.unread_count("system"), 1)

    def test_unread_count(self):
        mgr = _fresh_manager()
        self._populate(mgr, 3)
        self.assertEqual(mgr.unread_count(), 3)

    def test_unread_count_by_category(self):
        mgr = _fresh_manager()
        mgr.notify("general", "A", "B")
        mgr.notify("system", "C", "D")
        self.assertEqual(mgr.unread_count("general"), 1)
        self.assertEqual(mgr.unread_count("system"), 1)

    def test_clear_history_all(self):
        mgr = _fresh_manager()
        self._populate(mgr)
        mgr.clear_history()
        self.assertEqual(len(mgr._history), 0)

    def test_clear_history_by_category(self):
        mgr = _fresh_manager()
        mgr.notify("general", "A", "B")
        mgr.notify("system", "C", "D")
        mgr.clear_history(category="general")
        self.assertEqual(len(mgr._history), 1)


class TestNotificationManagerStats(unittest.TestCase):
    """Test statistics reporting."""

    def test_stats_empty(self):
        mgr = _fresh_manager()
        stats = mgr.get_stats()
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["unread"], 0)

    def test_stats_with_notifications(self):
        mgr = _fresh_manager()
        mgr.notify("general", "A", "B")
        mgr.notify("system", "C", "D")
        stats = mgr.get_stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["unread"], 2)
        self.assertIn("general", stats["by_category"])
        self.assertIn("system", stats["by_category"])


class TestNotificationManagerConfigure(unittest.TestCase):
    """Test category configuration."""

    def test_configure_category_voice(self):
        mgr = _fresh_manager()
        mgr.configure_category("test_cat", voice=False)
        cfg = mgr.get_category_config("test_cat")
        self.assertFalse(cfg["voice"])

    def test_configure_category_toast(self):
        mgr = _fresh_manager()
        mgr.configure_category("test_cat", toast=False)
        cfg = mgr.get_category_config("test_cat")
        self.assertFalse(cfg["toast"])

    def test_configure_category_priority(self):
        mgr = _fresh_manager()
        mgr.configure_category("test_cat", default_priority=Priority.LOW)
        cfg = mgr.get_category_config("test_cat")
        self.assertEqual(cfg["default_priority"], Priority.LOW)

    def test_configure_creates_new_category(self):
        mgr = _fresh_manager()
        mgr.configure_category("brand_new", voice=True)
        cfg = mgr.get_category_config("brand_new")
        self.assertTrue(cfg["voice"])

    def test_get_category_config_unknown(self):
        mgr = _fresh_manager()
        cfg = mgr.get_category_config("unknown")
        self.assertIn("voice", cfg)


class TestNotificationManagerToDictList(unittest.TestCase):
    """Test serialization."""

    def test_to_dict_list(self):
        mgr = _fresh_manager()
        n1 = mgr.notify("general", "A", "B")
        n2 = mgr.notify("system", "C", "D")
        result = mgr.to_dict_list([n1, n2])
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], dict)
        self.assertEqual(result[0]["id"], n1.id)


class TestNotificationAction(unittest.TestCase):
    """Test the notification_action Gemini tool."""

    def _mgr(self):
        return _fresh_manager()

    @patch("actions.notification_action.get_notification_manager")
    def test_send_success(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({
            "action": "send", "category": "general",
            "title": "Test", "message": "Hello",
        })
        self.assertIn("Notification sent", result)

    @patch("actions.notification_action.get_notification_manager")
    def test_send_no_title(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({
            "action": "send", "category": "general",
            "title": "", "message": "Hello",
        })
        self.assertIn("provide a title", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_send_no_message(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({
            "action": "send", "category": "general",
            "title": "Test", "message": "",
        })
        self.assertIn("provide a message", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_send_with_priority(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({
            "action": "send", "category": "general",
            "title": "T", "message": "M", "priority": "critical",
        })
        self.assertIn("CRITICAL", result)

    @patch("actions.notification_action.get_notification_manager")
    def test_send_unknown_priority(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({
            "action": "send", "category": "general",
            "title": "T", "message": "M", "priority": "bogus",
        })
        self.assertIn("unknown priority", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_history_empty(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({"action": "history"})
        self.assertIn("no notifications", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_history_with_items(self, mock_get):
        mgr = self._mgr()
        mgr.set_toast_enabled(False)
        mgr.notify("general", "A", "B")
        mock_get.return_value = mgr
        result = notification_action({"action": "history"})
        self.assertIn("A: B", result)

    @patch("actions.notification_action.get_notification_manager")
    def test_unread_count(self, mock_get):
        mgr = self._mgr()
        mgr.notify("general", "A", "B")
        mock_get.return_value = mgr
        result = notification_action({"action": "unread_count"})
        self.assertIn("1 unread", result)

    @patch("actions.notification_action.get_notification_manager")
    def test_mark_read(self, mock_get):
        mgr = self._mgr()
        n = mgr.notify("general", "A", "B")
        mock_get.return_value = mgr
        result = notification_action({"action": "mark_read", "notif_id": n.id})
        self.assertIn("marked as read", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_mark_read_no_id(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({"action": "mark_read", "notif_id": ""})
        self.assertIn("provide a notif_id", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_mark_all_read(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({"action": "mark_all_read"})
        self.assertIn("marked as read", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_clear(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({"action": "clear"})
        self.assertIn("cleared", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_stats(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({"action": "stats"})
        self.assertIn("Total:", result)

    @patch("actions.notification_action.get_notification_manager")
    def test_configure_show(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({"action": "configure", "category": "system"})
        self.assertIn("system", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_configure_update(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({
            "action": "configure", "category": "test",
            "voice": "false",
        })
        self.assertIn("updated", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_configure_no_category(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({"action": "configure"})
        self.assertIn("provide a category", result.lower())

    @patch("actions.notification_action.get_notification_manager")
    def test_unknown_action(self, mock_get):
        mock_get.return_value = self._mgr()
        result = notification_action({"action": "bogus"})
        self.assertIn("unknown notification action", result.lower())


class TestNotificationActionToolDeclaration(unittest.TestCase):
    """Verify the tool declaration meets the loader contract."""

    def test_tool_dict_exists(self):
        from actions.notification_action import TOOL
        self.assertIsInstance(TOOL, dict)

    def test_tool_name(self):
        from actions.notification_action import TOOL
        self.assertEqual(TOOL["name"], "notification_action")

    def test_tool_has_handler(self):
        from actions.notification_action import TOOL
        self.assertTrue(callable(TOOL["handler"]))

    def test_tool_parameters_are_object(self):
        from actions.notification_action import TOOL
        self.assertEqual(TOOL["parameters"]["type"], "OBJECT")

    def test_tool_description_nonempty(self):
        from actions.notification_action import TOOL
        self.assertTrue(len(TOOL["description"]) > 20)


if __name__ == "__main__":
    unittest.main()
