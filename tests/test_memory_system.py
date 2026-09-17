"""
JARVIS Phase 2 — Memory System Enhanced Test Suite.

Verifies:
  - Long-term memory persistence & recall
  - Natural query deletion ("forget_matching")
  - Privacy guard sensitive credential protection
  - Short-term turn buffer operations
  - Memory controller action tool execution
  - NEW: Memory statistics
  - NEW: Memory export/import
  - NEW: Clear by category and clear all
  - NEW: Memory age tracking
"""
import unittest
import json
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
    clear_short_term_turns, get_memory_stats, export_memory, import_memory,
    clear_category, clear_all_memory, get_memory_age,
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
        """Test verbal deletion by natural language keyword query."""
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


class TestMemoryStats(unittest.TestCase):
    """Tests for the new get_memory_stats() function."""

    def test_stats_returns_dict(self):
        stats = get_memory_stats()
        self.assertIsInstance(stats, dict)

    def test_stats_has_required_keys(self):
        stats = get_memory_stats()
        self.assertIn("total", stats)
        self.assertIn("by_category", stats)
        self.assertIn("oldest", stats)
        self.assertIn("newest", stats)
        self.assertIn("size_bytes", stats)
        self.assertIn("sessions", stats)

    def test_stats_total_is_nonnegative(self):
        stats = get_memory_stats()
        self.assertGreaterEqual(stats["total"], 0)

    def test_stats_by_category_is_dict(self):
        stats = get_memory_stats()
        self.assertIsInstance(stats["by_category"], dict)

    def test_stats_after_storing_entry(self):
        remember("stats_test_key", "Stats test value", category="notes")
        stats = get_memory_stats()
        self.assertGreaterEqual(stats["total"], 1)
        self.assertIn("notes", stats["by_category"])
        # Clean up
        forget("stats_test_key", category="notes")


class TestMemoryExportImport(unittest.TestCase):
    """Tests for export and import functionality."""

    def test_export_returns_json_string(self):
        data = export_memory()
        self.assertIsInstance(data, str)
        parsed = json.loads(data)
        self.assertIsInstance(parsed, dict)

    def test_export_excludes_sessions(self):
        data = export_memory()
        parsed = json.loads(data)
        self.assertNotIn("sessions", parsed)

    def test_import_valid_json(self):
        test_data = json.dumps({
            "notes": {
                "import_test": {"value": "Imported value", "updated": "2025-01-01"}
            }
        })
        result = import_memory(test_data, merge=True)
        self.assertIn("Imported", result)
        # Verify it was stored
        search_res = search_memory("import_test")
        self.assertIn("imported value", search_res.lower())
        # Clean up
        forget("import_test", category="notes")

    def test_import_invalid_json(self):
        result = import_memory("not valid json {{{", merge=True)
        self.assertIn("Invalid JSON", result)

    def test_import_empty_string(self):
        result = import_memory("", merge=True)
        self.assertIn("No data", result)

    def test_import_rejects_sensitive_data(self):
        test_data = json.dumps({
            "notes": {
                "wifi_password": {"value": "SecretPass123", "updated": "2025-01-01"}
            }
        })
        result = import_memory(test_data, merge=True)
        self.assertIn("skipped", result)

    def test_import_merge_preserves_existing(self):
        remember("merge_test", "Original value", category="notes")
        test_data = json.dumps({
            "notes": {
                "merge_test_2": {"value": "New value", "updated": "2025-01-01"}
            }
        })
        import_memory(test_data, merge=True)
        # Both should exist
        search1 = search_memory("merge_test")
        search2 = search_memory("merge_test_2")
        self.assertIn("merge_test", search1)
        self.assertIn("merge_test_2", search2)
        # Clean up
        forget("merge_test", category="notes")
        forget("merge_test_2", category="notes")

    def test_import_replace_clears_existing(self):
        remember("replace_test", "Old value", category="notes")
        test_data = json.dumps({
            "notes": {
                "replace_new": {"value": "New value", "updated": "2025-01-01"}
            }
        })
        import_memory(test_data, merge=False)
        search_old = search_memory("replace_test")
        search_new = search_memory("replace_new")
        self.assertIn("Nothing stored", search_old)
        self.assertIn("replace_new", search_new)
        # Clean up
        forget("replace_new", category="notes")


class TestMemoryClear(unittest.TestCase):
    """Tests for clear_category and clear_all_memory."""

    def test_clear_category(self):
        remember("clear_cat_test1", "Value 1", category="notes")
        remember("clear_cat_test2", "Value 2", category="notes")
        result = clear_category("notes")
        self.assertIn("Cleared", result)
        # Verify entries are gone
        search = search_memory("clear_cat_test")
        self.assertIn("Nothing stored", search)

    def test_clear_category_invalid(self):
        result = clear_category("invalid_category")
        self.assertIn("Unknown category", result)

    def test_clear_category_empty(self):
        # Clear twice — second time should say empty
        clear_category("wishes")
        result = clear_category("wishes")
        self.assertIn("already empty", result)

    def test_clear_all_needs_confirmation(self):
        result = clear_all_memory()
        self.assertIn("YES_DELETE_ALL", result)

    def test_clear_all_with_confirmation(self):
        remember("clear_all_test", "To be cleared", category="notes")
        result = clear_all_memory(confirm_key="YES_DELETE_ALL")
        self.assertIn("cleared", result.lower())
        search = search_memory("clear_all_test")
        self.assertIn("Nothing stored", search)


class TestMemoryAge(unittest.TestCase):
    """Tests for memory age tracking."""

    def test_age_of_existing_entry(self):
        remember("age_test", "Age test value", category="notes")
        age = get_memory_age("age_test", "notes")
        self.assertEqual(age, "today")
        # Clean up
        forget("age_test", category="notes")

    def test_age_of_nonexistent_entry(self):
        age = get_memory_age("nonexistent_key", "notes")
        self.assertEqual(age, "not found")

    def test_age_of_entry_with_old_date(self):
        # Manually create an entry with an old date
        from memory.memory_manager import save_memory, load_memory
        memory = load_memory()
        memory.setdefault("notes", {})["old_entry"] = {
            "value": "Old value",
            "updated": "2020-01-01"
        }
        save_memory(memory)
        age = get_memory_age("old_entry", "notes")
        self.assertIn("ago", age)
        # Clean up
        forget("old_entry", category="notes")


class TestMemoryControllerNewActions(unittest.TestCase):
    """Tests for the new memory controller actions."""

    def test_stats_action(self):
        result = memory_controller(parameters={"action": "stats"})
        self.assertIn("Total entries", result)

    def test_export_action(self):
        result = memory_controller(parameters={"action": "export"})
        self.assertIn("Memory exported", result)

    def test_import_action(self):
        test_data = json.dumps({
            "notes": {"ctrl_import_test": {"value": "Test", "updated": "2025-01-01"}}
        })
        result = memory_controller(parameters={"action": "import", "data": test_data})
        self.assertIn("Imported", result)
        # Clean up
        forget("ctrl_import_test", category="notes")

    def test_import_action_no_data(self):
        result = memory_controller(parameters={"action": "import"})
        self.assertIn("provide import data", result)

    def test_clear_action_category(self):
        remember("ctrl_clear_test", "To clear", category="notes")
        result = memory_controller(parameters={"action": "clear", "target": "notes"})
        self.assertIn("Cleared", result)

    def test_clear_action_all_needs_confirm(self):
        result = memory_controller(parameters={"action": "clear", "target": "all"})
        self.assertIn("YES_DELETE_ALL", result)

    def test_age_action(self):
        remember("ctrl_age_test", "Age test", category="notes")
        result = memory_controller(parameters={"action": "age", "key": "ctrl_age_test", "category": "notes"})
        self.assertIn("today", result)
        # Clean up
        forget("ctrl_age_test", category="notes")

    def test_age_action_no_key(self):
        result = memory_controller(parameters={"action": "age"})
        self.assertIn("provide a key", result)

    def test_unknown_action(self):
        result = memory_controller(parameters={"action": "unknown"})
        self.assertIn("Unknown memory action", result)


if __name__ == "__main__":
    unittest.main()
