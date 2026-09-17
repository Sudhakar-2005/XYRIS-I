"""
JARVIS Phase 2 — Memory System Automated Test Suite.

Verifies:
  - Long-term memory persistence & recall
  - Natural query deletion ("forget_matching")
  - Privacy guard sensitive credential protection
  - Short-term turn buffer operations
  - Memory controller action tool execution
"""
import unittest
import sys
from pathlib import Path

# Force UTF-8 encoding on Windows console for emoji prints
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.memory_manager import (
    remember, forget, forget_matching, search_memory, load_memory,
    is_sensitive_key_value, add_short_term_turn, get_short_term_turns,
    clear_short_term_turns
)
from actions.memory_controller import memory_controller


class TestMemorySystem(unittest.TestCase):

    def setUp(self):
        clear_short_term_turns()

    def test_01_remember_and_search(self):
        """Test storing a fact and recalling it by keyword."""
        res = remember("project_demo", "Project demo is on Friday", category="projects")
        self.assertIn("Remembered", res)

        search_res = search_memory("demo")
        self.assertIn("project demo", search_res)

    def test_02_forget_by_key(self):
        """Test forgetting a specific memory key."""
        remember("test_temp_key", "Temporary memory value", category="notes")
        res = forget("test_temp_key", category="notes")
        self.assertIn("Forgotten", res)

        search_res = search_memory("test_temp_key")
        self.assertIn("Nothing stored", search_res)

    def test_03_forget_matching_natural_query(self):
        """Test verbal deletion by natural language keyword query ("Forget that / project")."""
        remember("demo_meeting", "Meeting regarding project demo at 3 PM", category="projects")
        res = forget_matching("project demo")
        self.assertIn("Forgotten", res)

        search_res = search_memory("demo_meeting")
        self.assertIn("Nothing stored", search_res)

    def test_04_privacy_guard(self):
        """Verify privacy guard blocks silent persistence of passwords and secrets."""
        self.assertTrue(is_sensitive_key_value("wifi_password", "SecretPass123"))
        self.assertTrue(is_sensitive_key_value("account_pin", "9876"))

        res = remember("my_password", "SecretPass123", category="notes")
        self.assertIn("Refused", res)

    def test_05_short_term_turn_buffer(self):
        """Verify short-term conversation turn buffer operations."""
        add_short_term_turn("user", "Remember my project demo is on Friday.")
        add_short_term_turn("assistant", "I will remember that your project demo is on Friday.")

        turns = get_short_term_turns()
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["role"], "user")
        self.assertEqual(turns[1]["role"], "assistant")

    def test_06_memory_controller_tool(self):
        """Verify memory_controller action tool execution."""
        res_remember = memory_controller(parameters={"action": "remember", "key": "fav_fruit", "value": "Mango", "category": "preferences"})
        self.assertIn("Remembered", res_remember)

        res_recall = memory_controller(parameters={"action": "recall", "query": "Mango"})
        self.assertIn("fav fruit", res_recall)

        res_forget = memory_controller(parameters={"action": "forget", "query": "Mango"})
        self.assertIn("Forgotten", res_forget)


if __name__ == "__main__":
    unittest.main()
