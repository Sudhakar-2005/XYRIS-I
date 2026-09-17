"""
Tests for the action loader and plugin loader — the tool dispatch system.

Verifies discovery, validation, collision detection, handler invocation,
and error isolation.
"""
import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.action_loader import (
    discover_actions, ActionRegistry, ActionRecord,
    _validate, _call_handler, _NAME_RE,
)
from core.plugin_loader import (
    discover_plugins, PluginRegistry, PluginRecord,
    _validate as _validate_plugin,
)


class TestActionLoaderDiscovery(unittest.TestCase):

    def setUp(self):
        self.registry = discover_actions(
            actions_dir=PROJECT_ROOT / "actions",
            reserved_names={"system_status", "shutdown_jarvis"},
            logger=lambda msg: None,
        )

    def test_discover_returns_registry(self):
        self.assertIsInstance(self.registry, ActionRegistry)

    def test_discover_finds_all_valid_actions(self):
        names = self.registry.names()
        self.assertGreaterEqual(len(names), 17)

    def test_all_discovered_names_are_valid_identifiers(self):
        import re
        pattern = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
        for name in self.registry.names():
            self.assertRegex(name, pattern, f"'{name}' is not a valid identifier")

    def test_no_name_collisions(self):
        names = self.registry.names()
        self.assertEqual(len(names), len(set(names)), "Duplicate action names found")

    def test_get_tool_declarations_returns_dicts(self):
        decls = self.registry.get_tool_declarations()
        self.assertIsInstance(decls, list)
        self.assertGreater(len(decls), 0)
        for d in decls:
            self.assertIn("name", d)
            self.assertIn("description", d)
            self.assertIn("parameters", d)

    def test_all_declarations_have_required_fields(self):
        for d in self.registry.get_tool_declarations():
            self.assertIsInstance(d["name"], str)
            self.assertTrue(len(d["name"]) > 0)
            self.assertIsInstance(d["description"], str)
            self.assertTrue(len(d["description"]) > 0)
            self.assertIsInstance(d["parameters"], dict)
            self.assertEqual(d["parameters"].get("type"), "OBJECT")


class TestActionLoaderValidation(unittest.TestCase):

    def test_valid_tool_dict(self):
        module = MagicMock()
        module.TOOL = {
            "name": "test_tool",
            "description": "A test tool",
            "parameters": {"type": "OBJECT", "properties": {}},
            "handler": lambda parameters: "ok",
        }
        rec = _validate(module, "test.py")
        self.assertTrue(rec.valid)
        self.assertEqual(rec.name, "test_tool")

    def test_missing_tool_dict(self):
        module = MagicMock(spec=[])  # no TOOL attribute
        rec = _validate(module, "helper.py")
        self.assertFalse(rec.valid)
        self.assertIn("No module-level TOOL dict", rec.error)

    def test_invalid_name(self):
        module = MagicMock()
        module.TOOL = {
            "name": "123-invalid!",
            "description": "Test",
            "parameters": {"type": "OBJECT", "properties": {}},
            "handler": lambda parameters: "ok",
        }
        rec = _validate(module, "bad.py")
        self.assertFalse(rec.valid)
        self.assertIn("not a valid identifier", rec.error)

    def test_missing_description(self):
        module = MagicMock()
        module.TOOL = {
            "name": "test_tool",
            "description": "",
            "parameters": {"type": "OBJECT", "properties": {}},
            "handler": lambda parameters: "ok",
        }
        rec = _validate(module, "bad.py")
        self.assertFalse(rec.valid)
        self.assertIn("description", rec.error)

    def test_missing_handler(self):
        module = MagicMock()
        module.TOOL = {
            "name": "test_tool",
            "description": "Test",
            "parameters": {"type": "OBJECT", "properties": {}},
        }
        rec = _validate(module, "bad.py")
        self.assertFalse(rec.valid)
        self.assertIn("handler", rec.error)

    def test_invalid_parameters_type(self):
        module = MagicMock()
        module.TOOL = {
            "name": "test_tool",
            "description": "Test",
            "parameters": {"type": "STRING"},
            "handler": lambda parameters: "ok",
        }
        rec = _validate(module, "bad.py")
        self.assertFalse(rec.valid)
        self.assertIn("OBJECT", rec.error)


class TestActionRegistryDispatch(unittest.TestCase):

    def test_run_existing_action(self):
        registry = discover_actions(
            actions_dir=PROJECT_ROOT / "actions",
            reserved_names=set(),
            logger=lambda msg: None,
        )
        result = registry.run("file_controller", {"action": "invalid_action"})
        self.assertIsInstance(result, str)
        self.assertIn("Unknown action", result)

    def test_run_nonexistent_action(self):
        registry = discover_actions(
            actions_dir=PROJECT_ROOT / "actions",
            reserved_names=set(),
            logger=lambda msg: None,
        )
        result = registry.run("nonexistent_tool", {})
        self.assertIn("not available", result)

    def test_has_method(self):
        registry = discover_actions(
            actions_dir=PROJECT_ROOT / "actions",
            reserved_names=set(),
            logger=lambda msg: None,
        )
        self.assertTrue(registry.has("open_app"))
        self.assertFalse(registry.has("nonexistent_tool"))

    def test_scheduling_returns_none_for_default(self):
        registry = discover_actions(
            actions_dir=PROJECT_ROOT / "actions",
            reserved_names=set(),
            logger=lambda msg: None,
        )
        sched = registry.scheduling("open_app")
        self.assertIsNone(sched)


class TestCallHandler(unittest.TestCase):

    def test_handler_receives_parameters(self):
        received = {}
        def handler(parameters):
            received.update(parameters)
            return "ok"
        _call_handler(handler, {"action": "test"}, {})
        self.assertEqual(received.get("action"), "test")

    def test_handler_with_context_kwargs(self):
        received = {}
        def handler(parameters, player=None, session_memory=None):
            received["player"] = player
            received["session_memory"] = session_memory
            return "ok"
        ctx = {"player": "speaker", "session_memory": "mem"}
        _call_handler(handler, {}, ctx)
        self.assertEqual(received["player"], "speaker")
        self.assertEqual(received["session_memory"], "mem")

    def test_handler_with_kwargs_catches_all_context(self):
        received = {}
        def handler(parameters, **kwargs):
            received.update(kwargs)
            return "ok"
        ctx = {"player": "spk", "response": "resp", "session_memory": "mem"}
        _call_handler(handler, {}, ctx)
        self.assertIn("player", received)
        self.assertIn("response", received)
        self.assertIn("session_memory", received)


class TestPluginLoaderDiscovery(unittest.TestCase):

    def setUp(self):
        self.registry = discover_plugins(
            plugins_dir=PROJECT_ROOT / "plugins",
            core_tool_names={"open_app", "computer_settings"},
            logger=lambda msg: None,
            notify=lambda msg: None,
        )

    def test_discover_returns_registry(self):
        self.assertIsInstance(self.registry, PluginRegistry)

    def test_template_plugin_is_skipped(self):
        ui_list = self.registry.list_for_ui()
        names = [p["name"] for p in ui_list]
        self.assertNotIn("_template", names)

    def test_list_for_ui_returns_list(self):
        ui_list = self.registry.list_for_ui()
        self.assertIsInstance(ui_list, list)


class TestPluginLoaderValidation(unittest.TestCase):

    def test_valid_plugin(self):
        module = MagicMock()
        module.PLUGIN = {
            "name": "test_plugin",
            "description": "A test plugin",
            "parameters": {"type": "OBJECT", "properties": {}},
        }
        module.run = lambda parameters: "ok"
        rec = _validate_plugin(module, "test.py")
        self.assertTrue(rec.valid)
        self.assertEqual(rec.name, "test_plugin")

    def test_missing_plugin_dict(self):
        module = MagicMock(spec=[])
        rec = _validate_plugin(module, "helper.py")
        self.assertFalse(rec.valid)

    def test_missing_run_function(self):
        module = MagicMock(spec=["PLUGIN"])
        module.PLUGIN = {
            "name": "test_plugin",
            "description": "Test",
            "parameters": {"type": "OBJECT", "properties": {}},
        }
        rec = _validate_plugin(module, "bad.py")
        self.assertFalse(rec.valid)
        self.assertIn("run", rec.error)


if __name__ == "__main__":
    unittest.main()
