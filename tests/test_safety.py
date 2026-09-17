"""
JARVIS Safety Intelligence Test Suite.

Tests the SafetyManager (core/safety_manager.py) and
safety_action tool (actions/safety_action.py).
"""
import unittest
from unittest.mock import patch
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.safety_manager import (
    Risk, classify, should_confirm, is_forbidden,
    check_rate_limit, rate_limit_remaining,
    log_action, get_audit_log, get_audit_stats, clear_audit_log,
    reset_rate_limits, _risk_summary, AuditEntry,
)
from actions.safety_action import safety_action


class TestRiskEnum(unittest.TestCase):
    """Test Risk enum ordering."""

    def test_ordering(self):
        self.assertLess(Risk.SAFE, Risk.CAUTION)
        self.assertLess(Risk.CAUTION, Risk.DANGEROUS)
        self.assertLess(Risk.DANGEROUS, Risk.FORBIDDEN)

    def test_values(self):
        self.assertEqual(Risk.SAFE, 0)
        self.assertEqual(Risk.FORBIDDEN, 3)


class TestClassify(unittest.TestCase):
    """Test action risk classification."""

    def test_shutdown_is_dangerous(self):
        self.assertEqual(classify("computer_settings", "shutdown"), Risk.DANGEROUS)

    def test_restart_is_dangerous(self):
        self.assertEqual(classify("computer_settings", "restart"), Risk.DANGEROUS)

    def test_toggle_wifi_is_dangerous(self):
        self.assertEqual(classify("computer_settings", "toggle_wifi"), Risk.DANGEROUS)

    def test_delete_file_is_dangerous(self):
        self.assertEqual(classify("file_controller", "delete"), Risk.DANGEROUS)

    def test_organize_desktop_is_dangerous(self):
        self.assertEqual(classify("file_controller", "organize_desktop"), Risk.DANGEROUS)

    def test_volume_set_is_caution(self):
        self.assertEqual(classify("computer_settings", "volume_set"), Risk.CAUTION)

    def test_move_file_is_caution(self):
        self.assertEqual(classify("file_controller", "move"), Risk.CAUTION)

    def test_click_is_safe(self):
        self.assertEqual(classify("computer_control", "click"), Risk.SAFE)

    def test_screenshot_is_safe(self):
        self.assertEqual(classify("computer_control", "screenshot"), Risk.SAFE)

    def test_unknown_action_is_safe(self):
        self.assertEqual(classify("some_tool", "some_action"), Risk.SAFE)

    def test_wildcard_shutdown(self):
        self.assertEqual(classify("any_tool", "shutdown"), Risk.DANGEROUS)

    def test_wildcard_delete(self):
        self.assertEqual(classify("any_tool", "delete"), Risk.DANGEROUS)

    def test_wildcard_format_is_forbidden(self):
        self.assertEqual(classify("any_tool", "format"), Risk.FORBIDDEN)

    def test_wildcard_rm_rf_is_forbidden(self):
        self.assertEqual(classify("any_tool", "rm -rf"), Risk.FORBIDDEN)

    def test_dark_mode_is_caution(self):
        self.assertEqual(classify("computer_settings", "dark_mode"), Risk.CAUTION)

    def test_close_app_is_caution(self):
        self.assertEqual(classify("computer_settings", "close_app"), Risk.CAUTION)

    def test_lock_screen_is_caution(self):
        self.assertEqual(classify("computer_settings", "lock_screen"), Risk.CAUTION)

    def test_gmail_send_is_caution(self):
        self.assertEqual(classify("gmail", "send"), Risk.CAUTION)

    def test_gmail_delete_is_dangerous(self):
        self.assertEqual(classify("gmail", "delete"), Risk.DANGEROUS)

    def test_memory_clear_is_dangerous(self):
        self.assertEqual(classify("memory_controller", "clear"), Risk.DANGEROUS)


class TestShouldConfirm(unittest.TestCase):
    """Test confirmation gate decision."""

    def test_dangerous_needs_confirm(self):
        self.assertTrue(should_confirm("computer_settings", "shutdown"))

    def test_caution_no_confirm(self):
        self.assertFalse(should_confirm("computer_settings", "volume_set"))

    def test_safe_no_confirm(self):
        self.assertFalse(should_confirm("computer_control", "click"))

    def test_forbidden_needs_confirm(self):
        self.assertTrue(should_confirm("any_tool", "format"))


class TestIsForbidden(unittest.TestCase):
    """Test forbidden action detection."""

    def test_format_is_forbidden(self):
        self.assertTrue(is_forbidden("any_tool", "format"))

    def test_rm_rf_is_forbidden(self):
        self.assertTrue(is_forbidden("any_tool", "rm -rf"))

    def test_shutdown_not_forbidden(self):
        self.assertFalse(is_forbidden("computer_settings", "shutdown"))

    def test_click_not_forbidden(self):
        self.assertFalse(is_forbidden("computer_control", "click"))


class TestRateLimit(unittest.TestCase):
    """Test rate limiting on dangerous actions."""

    def setUp(self):
        reset_rate_limits()

    def test_first_call_allowed(self):
        self.assertTrue(check_rate_limit("shutdown"))

    def test_allows_up_to_limit(self):
        for _ in range(5):
            self.assertTrue(check_rate_limit("shutdown"))
        self.assertFalse(check_rate_limit("shutdown"))

    def test_remaining_decreases(self):
        self.assertEqual(rate_limit_remaining("shutdown"), 5)
        check_rate_limit("shutdown")
        self.assertEqual(rate_limit_remaining("shutdown"), 4)

    def test_different_actions_independent(self):
        for _ in range(5):
            check_rate_limit("shutdown")
        self.assertFalse(check_rate_limit("shutdown"))
        self.assertTrue(check_rate_limit("restart"))

    def test_remaining_unknown_action(self):
        self.assertEqual(rate_limit_remaining("unknown"), 5)


class TestAuditLog(unittest.TestCase):
    """Test audit trail logging and querying."""

    def setUp(self):
        clear_audit_log()

    def test_log_and_retrieve(self):
        log_action("shutdown", "computer_settings", Risk.DANGEROUS.value, "test")
        items = get_audit_log()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].action, "shutdown")

    def test_log_by_tool_filter(self):
        log_action("shutdown", "computer_settings", Risk.DANGEROUS.value)
        log_action("click", "computer_control", Risk.SAFE.value)
        items = get_audit_log(tool="computer_settings")
        self.assertEqual(len(items), 1)

    def test_log_by_risk_filter(self):
        log_action("shutdown", "computer_settings", Risk.DANGEROUS.value)
        log_action("click", "computer_control", Risk.SAFE.value)
        items = get_audit_log(risk="DANGEROUS")
        self.assertEqual(len(items), 1)

    def test_log_limit(self):
        for i in range(10):
            log_action(f"action_{i}", "tool", Risk.SAFE.value)
        items = get_audit_log(limit=3)
        self.assertEqual(len(items), 3)

    def test_log_confirmed(self):
        log_action("shutdown", "computer_settings", Risk.DANGEROUS.value, confirmed=True)
        items = get_audit_log()
        self.assertTrue(items[0].confirmed)

    def test_clear_audit(self):
        log_action("test", "tool", Risk.SAFE.value)
        clear_audit_log()
        items = get_audit_log()
        self.assertEqual(len(items), 0)

    def test_audit_stats(self):
        log_action("shutdown", "computer_settings", Risk.DANGEROUS.value, confirmed=True)
        log_action("click", "computer_control", Risk.SAFE.value)
        stats = get_audit_stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["confirmed"], 1)
        self.assertIn("DANGEROUS", stats["by_risk"])

    def test_audit_entry_has_timestamp(self):
        entry = log_action("test", "tool", Risk.SAFE.value)
        self.assertIsInstance(entry.timestamp, str)
        self.assertTrue(len(entry.timestamp) > 0)


class TestAuditEntry(unittest.TestCase):
    """Test AuditEntry dataclass."""

    def test_creation(self):
        e = AuditEntry(
            timestamp="2025-01-01T00:00:00",
            action="test",
            tool="tool",
            risk="SAFE",
            detail="",
            confirmed=False,
        )
        self.assertEqual(e.action, "test")
        self.assertFalse(e.confirmed)


class TestRiskSummary(unittest.TestCase):
    """Test risk summary output."""

    def test_returns_string(self):
        result = _risk_summary()
        self.assertIsInstance(result, str)
        self.assertIn("shutdown", result)
        self.assertIn("DANGEROUS", result)


class TestSafetyAction(unittest.TestCase):
    """Test the safety_action Gemini tool."""

    def setUp(self):
        clear_audit_log()
        reset_rate_limits()

    def test_classify(self):
        result = safety_action({
            "action": "classify",
            "tool": "computer_settings",
            "action_name": "shutdown",
        })
        self.assertIn("DANGEROUS", result)

    def test_classify_missing_params(self):
        result = safety_action({
            "action": "classify",
            "tool": "",
            "action_name": "",
        })
        self.assertIn("provide both", result.lower())

    def test_check_action(self):
        result = safety_action({
            "action": "check_action",
            "tool": "computer_settings",
            "action_name": "shutdown",
        })
        self.assertIn("Needs confirmation: Yes", result)
        self.assertIn("DANGEROUS", result)

    def test_check_action_safe(self):
        result = safety_action({
            "action": "check_action",
            "tool": "computer_control",
            "action_name": "click",
        })
        self.assertIn("Needs confirmation: No", result)
        self.assertIn("SAFE", result)

    def test_audit_log_empty(self):
        result = safety_action({"action": "audit_log"})
        self.assertIn("empty", result.lower())

    def test_audit_log_with_entries(self):
        log_action("test", "tool", Risk.SAFE.value)
        result = safety_action({"action": "audit_log"})
        self.assertIn("test", result)

    def test_audit_stats(self):
        result = safety_action({"action": "audit_stats"})
        self.assertIn("Total logged", result)

    def test_clear_audit(self):
        result = safety_action({"action": "clear_audit"})
        self.assertIn("cleared", result.lower())

    def test_rate_check(self):
        result = safety_action({
            "action": "rate_check",
            "action_name": "shutdown",
        })
        self.assertIn("Rate limit", result)

    def test_rate_check_no_action(self):
        result = safety_action({
            "action": "rate_check",
            "action_name": "",
        })
        self.assertIn("provide an", result.lower())

    def test_risk_summary(self):
        result = safety_action({"action": "risk_summary"})
        self.assertIn("shutdown", result)

    def test_unknown_action(self):
        result = safety_action({"action": "bogus"})
        self.assertIn("unknown safety action", result.lower())


class TestSafetyActionToolDeclaration(unittest.TestCase):
    """Verify the tool declaration meets the loader contract."""

    def test_tool_dict_exists(self):
        from actions.safety_action import TOOL
        self.assertIsInstance(TOOL, dict)

    def test_tool_name(self):
        from actions.safety_action import TOOL
        self.assertEqual(TOOL["name"], "safety_action")

    def test_tool_has_handler(self):
        from actions.safety_action import TOOL
        self.assertTrue(callable(TOOL["handler"]))

    def test_tool_parameters_are_object(self):
        from actions.safety_action import TOOL
        self.assertEqual(TOOL["parameters"]["type"], "OBJECT")

    def test_tool_description_nonempty(self):
        from actions.safety_action import TOOL
        self.assertTrue(len(TOOL["description"]) > 20)


if __name__ == "__main__":
    unittest.main()
