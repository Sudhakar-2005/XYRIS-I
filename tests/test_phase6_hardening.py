"""Brain Phase 6 — automation hardening.

1. desktop_control's generated-code path (the only exec() of model-written
   Python in the repo) must park behind the on-screen confirmation banner —
   tool-level requires_confirmation stays False because clean/organize are
   reversible (undo pushes, §3.14 doctrine), so the gate lives in the handler.
2. The setup overlay must MERGE into config/api_keys.json, not clobber it.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import main  # noqa: F401  (offscreen Qt; also validates ui imports cleanly)
import ui
from core import confirm


class TestGeneratedCodeBehindBanner(unittest.TestCase):

    def setUp(self):
        self.shown: list[tuple[str, str]] = []
        confirm.bind(lambda t, d: self.shown.append((t, d)), lambda: None)
        confirm._pending = None
        self.addCleanup(self._reset)

    @staticmethod
    def _reset():
        confirm._pending = None
        confirm.bind(None, None)

    def test_task_path_parks_behind_the_banner(self):
        import actions.desktop as d
        with patch.object(d, "_ask_gemini_for_desktop_action",
                          return_value="print('hi')") as ask:
            out = d.desktop_control(
                parameters={"action": "task", "task": "say hi"})
        self.assertIn("[CONFIRMATION_PENDING]", out)
        self.assertTrue(self.shown, "no banner was raised")
        self.assertIn("say hi", self.shown[0][1])
        self.assertIn("print('hi')", self.shown[0][1])
        ask.assert_called_once()

        # Nothing runs until the human accepts.
        confirm.resolve(accepted=False)
        self.assertEqual(len(self.shown), 1)

    def test_accepting_the_banner_runs_the_code(self):
        import actions.desktop as d
        with patch.object(d, "_ask_gemini_for_desktop_action",
                          return_value="print('banner-ok')"):
            out = d.desktop_control(
                parameters={"action": "task", "task": "say hi"})
        self.assertIn("[CONFIRMATION_PENDING]", out)
        confirm.resolve(accepted=True)
        # The pending run callback executed inside confirm.resolve; asking
        # again must show nothing pending.
        self.assertIsNone(confirm._pending)

    def test_unsafe_or_empty_generation_never_reaches_the_banner(self):
        import actions.desktop as d
        for bad in ("", None, "UNSAFE"):
            with patch.object(d, "_ask_gemini_for_desktop_action",
                              return_value=bad):
                out = d.desktop_control(
                    parameters={"action": "task", "task": "evil"})
            self.assertIn("cannot be performed safely", out)
            self.assertFalse(self.shown)

    def test_pending_banner_blocks_a_second_request(self):
        import actions.desktop as d
        with patch.object(d, "_ask_gemini_for_desktop_action",
                          return_value="print('a')"):
            d.desktop_control(parameters={"action": "task", "task": "one"})
            out2 = d.desktop_control(
                parameters={"action": "task", "task": "two"})
        self.assertIn("already a confirmation", out2)

    def test_tool_level_flag_stays_false_reversible_ops_stay_frictionless(self):
        from actions.desktop import TOOL
        self.assertFalse(TOOL["requires_confirmation"])
        self.assertTrue(TOOL["supports_undo"])
        self.assertIn("CONFIRM", TOOL["description"])


class TestSetupWritesMerge(unittest.TestCase):

    def test_setup_done_preserves_other_keys(self):
        tmp = Path(tempfile.mkdtemp()) / "api_keys.json"
        tmp.write_text(json.dumps(
            {"gemini_api_key": "old", "assistant_name": "FRIDAY"}),
            encoding="utf-8")
        old = ui.API_FILE
        ui.API_FILE = tmp
        try:
            cls = next(c for c in vars(ui).values()
                       if isinstance(c, type)
                       and "_on_setup_done" in vars(c))
            obj = MagicMock()
            obj._overlay = None
            cls._on_setup_done(obj, "NEWKEY", "windows")
        finally:
            ui.API_FILE = old
        data = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(data["gemini_api_key"], "NEWKEY")
        self.assertEqual(data["os_system"], "windows")
        self.assertEqual(data["assistant_name"], "FRIDAY",
                         "unrelated keys must survive the setup write")

    def test_setup_done_recovers_from_corrupt_file(self):
        tmp = Path(tempfile.mkdtemp()) / "api_keys.json"
        tmp.write_text("{not json", encoding="utf-8")
        old = ui.API_FILE
        ui.API_FILE = tmp
        try:
            cls = next(c for c in vars(ui).values()
                       if isinstance(c, type)
                       and "_on_setup_done" in vars(c))
            obj = MagicMock()
            obj._overlay = None
            cls._on_setup_done(obj, "NEWKEY", "linux")
        finally:
            ui.API_FILE = old
        data = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(data, {"gemini_api_key": "NEWKEY",
                                "os_system": "linux"})


if __name__ == "__main__":
    unittest.main()
