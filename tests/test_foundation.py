"""
JARVIS Phase 1 — Foundation & Stability Automated Test Suite.

Verifies system architecture components, action loaders, config management,
confirmation gates, undo stack, memory retrieval, and system tools
without modifying operational codebase state.
"""
import unittest
import os
import sys
import json
from pathlib import Path

# Force UTF-8 encoding on Windows console for emoji prints
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.config_manager import (
    get_gemini_key, is_configured, get_assistant_name, get_voice,
    get_wake_word_enabled, get_push_to_talk_enabled, get_hud_style
)
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt, search_memory
)
from core.action_loader import discover_actions
from core.plugin_loader import discover_plugins
from core import confirm as confirm_gate
from core import undo as undo_stack
from actions.system_monitor import get_system_status
from actions.computer_settings import computer_settings
from actions.file_controller import file_controller
from actions.file_processor import file_processor


class TestFoundationArchitecture(unittest.TestCase):

    def test_01_config_manager(self):
        """Verify configuration manager returns valid values from config/api_keys.json."""
        self.assertTrue(is_configured(), "API key should be configured")
        key = get_gemini_key()
        self.assertIsNotNone(key)
        self.assertTrue(len(key) > 10)
        self.assertIsInstance(get_assistant_name(), str)
        self.assertIn(get_hud_style(), ("face", "core"))

    def test_02_action_loader(self):
        """Verify all 17 bundled actions are auto-discovered without errors."""
        actions_dir = PROJECT_ROOT / "actions"
        registry = discover_actions(
            actions_dir=actions_dir,
            reserved_names={"system_status", "shutdown_jarvis"},
            logger=lambda msg: None
        )
        actions = registry.names()
        expected_actions = {
            "browser_control", "code_helper", "computer_control", "computer_settings",
            "desktop_control", "dev_agent", "file_controller", "file_processor",
            "flight_finder", "game_updater", "memory_controller", "open_app",
            "reminder", "send_message", "weather_report", "web_search", "youtube_video"
        }
        for expected in expected_actions:
            self.assertIn(expected, actions, f"Action '{expected}' should be discovered")

    def test_03_plugin_loader(self):
        """Verify plugin discovery engine initializes cleanly."""
        plugins_dir = PROJECT_ROOT / "plugins"
        registry = discover_plugins(
            plugins_dir=plugins_dir,
            core_tool_names={"open_app", "computer_settings"},
            logger=lambda msg: None,
            notify=lambda msg: None
        )
        self.assertIsNotNone(registry)

    def test_04_confirmation_gate(self):
        """Verify irreversible action confirmation gate mechanics."""
        shown_title = []
        hidden_call = []

        def dummy_show(title, detail):
            shown_title.append(title)

        def dummy_hide():
            hidden_call.append(True)

        confirm_gate.bind(dummy_show, dummy_hide)

        # Test request
        executed = []
        res = confirm_gate.request("restart", "Restart Computer", "Restarting now", lambda: executed.append(True))
        self.assertIn("[CONFIRMATION_PENDING]", res)
        self.assertEqual(shown_title, ["Restart Computer"])

        # Test resolve cancel
        confirm_gate.resolve(accepted=False)
        self.assertEqual(executed, [], "Action should not run on cancel")
        self.assertTrue(hidden_call[0])

    def test_05_undo_stack(self):
        """Verify action undo stack push and execution."""
        undo_stack.clear()
        reverted = []
        undo_stack.push_undo("Test action", lambda: reverted.append(True) or "Reverted test")

        self.assertTrue(undo_stack.can_undo())
        res = undo_stack.undo_last()
        self.assertIn("Undone: Test action.", res)
        self.assertEqual(reverted, [True])
        self.assertFalse(undo_stack.can_undo())

    def test_06_memory_manager(self):
        """Verify long term memory storage, prompt formatting, and recall search."""
        mem = load_memory()
        self.assertIsInstance(mem, dict)

        # Test formatting
        formatted = format_memory_for_prompt(mem)
        self.assertIsInstance(formatted, str)

        # Test search
        search_res = search_memory("")
        self.assertTrue(isinstance(search_res, (list, str)))

    def test_07_system_monitor(self):
        """Verify hardware telemetry readout functions correctly."""
        metrics = get_system_status()
        self.assertIsInstance(metrics, dict)
        self.assertIn("cpu_percent", metrics)
        self.assertIn("ram_percent", metrics)
        self.assertIn("process_count", metrics)

    def test_08_file_controller_validation(self):
        """Verify file controller parameter validation without side effects."""
        res = file_controller(parameters={"action": "invalid_action"})
        self.assertIn("Unknown action", res)

    def test_09_computer_settings_validation(self):
        """Verify computer settings parameter validation."""
        res = computer_settings(parameters={"action": "unknown_action"})
        self.assertIn("could not match", res.lower())


if __name__ == "__main__":
    unittest.main()
