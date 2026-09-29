"""AETHER-I V3 — Phase 2 state machine tests.

Covers core/app_state.py (vocabulary, aliases, history, listeners,
thread-safety) plus source-level guards that the runtime emission points
actually exist in main.py / ui.py / core/avatar.py — the honest-state
contract, without needing a Qt event loop.
"""

from __future__ import annotations

import threading
import unittest
from pathlib import Path

from core.app_state import (
    AppState,
    V3_STATES,
    SYSTEM_STATES,
    VALID_STATES,
    ALIASES,
    WORKING_STATES,
    normalize,
    transition,
    current,
    history,
    add_listener,
    remove_listener,
    is_working,
    _reset_for_tests,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestVocabulary(unittest.TestCase):
    def setUp(self):
        _reset_for_tests()

    def test_all_13_v3_states_present(self):
        spec = {
            "IDLE", "LISTENING", "UNDERSTANDING", "THINKING", "PLANNING",
            "EXECUTING", "OBSERVING", "VERIFYING", "SPEAKING",
            "WAITING_CONFIRMATION", "ERROR", "RECOVERING", "SLEEPING",
        }
        self.assertEqual(set(V3_STATES), spec)

    def test_system_states_are_separate_from_v3(self):
        self.assertEqual(SYSTEM_STATES, {"MUTED", "INITIALISING"})
        self.assertFalse(SYSTEM_STATES & V3_STATES)
        self.assertEqual(VALID_STATES, V3_STATES | SYSTEM_STATES)

    def test_state_constants_match_their_names(self):
        for name in ("IDLE", "LISTENING", "UNDERSTANDING", "THINKING",
                     "PLANNING", "EXECUTING", "OBSERVING", "VERIFYING",
                     "SPEAKING", "WAITING_CONFIRMATION", "ERROR",
                     "RECOVERING", "SLEEPING", "MUTED", "INITIALISING"):
            self.assertEqual(getattr(AppState, name), name)


class TestNormalize(unittest.TestCase):
    def setUp(self):
        _reset_for_tests()

    def test_known_states_pass_through_canonical(self):
        for s in VALID_STATES:
            self.assertEqual(normalize(s), s)

    def test_lowercase_and_whitespace_normalise(self):
        self.assertEqual(normalize("  listening "), "LISTENING")
        self.assertEqual(normalize("waiting_confirmation"), "WAITING_CONFIRMATION")

    def test_legacy_processing_aliases_to_executing(self):
        self.assertEqual(normalize("PROCESSING"), "EXECUTING")
        self.assertEqual(ALIASES["PROCESSING"], AppState.EXECUTING)

    def test_unknown_returns_none(self):
        self.assertIsNone(normalize("BUSY"))
        self.assertIsNone(normalize(""))
        self.assertIsNone(normalize(None))


class TestTransition(unittest.TestCase):
    def setUp(self):
        _reset_for_tests()

    def test_updates_current_and_history(self):
        out = transition("THINKING", source="test")
        self.assertEqual(out, "THINKING")
        self.assertEqual(current(), "THINKING")
        entries = history()
        self.assertEqual(entries[-1][1], "THINKING")
        self.assertEqual(entries[-1][2], "test")

    def test_alias_is_applied_at_the_sink(self):
        transition("PROCESSING", source="tool")
        self.assertEqual(current(), "EXECUTING")

    def test_unknown_passthrough_does_not_crash(self):
        out = transition("WEIRD_STATE", source="x")
        self.assertEqual(out, "WEIRD_STATE")
        self.assertEqual(current(), "WEIRD_STATE")

    def test_listeners_fire_only_on_change(self):
        seen = []
        add_listener(lambda new, old, src: seen.append((new, old, src)))
        transition("THINKING")
        transition("THINKING")          # no-op: no duplicate notification
        transition("EXECUTING")
        self.assertEqual(seen, [("THINKING", AppState.INITIALISING, ""),
                                ("EXECUTING", "THINKING", "")])

    def test_listener_exception_cannot_break_transition(self):
        def boom(new, old, src):
            raise RuntimeError("bad listener")

        add_listener(boom)
        out = transition("ERROR")
        self.assertEqual(out, "ERROR")
        self.assertEqual(current(), "ERROR")

    def test_remove_listener(self):
        seen = []
        fn = lambda n, o, s: seen.append(n)
        add_listener(fn)
        remove_listener(fn)
        transition("LISTENING")
        self.assertEqual(seen, [])

    def test_history_is_capped(self):
        for i in range(300):
            transition("THINKING" if i % 2 else "LISTENING")
        self.assertLessEqual(len(history(limit=10_000)), 200)

    def test_thread_safety_smoke(self):
        errors = []

        def worker(tag):
            try:
                for i in range(300):
                    transition("EXECUTING" if i % 2 else "VERIFYING",
                               source=tag)
            except Exception as e:      # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"t{i}",))
                   for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertIn(current(), ("EXECUTING", "VERIFYING"))


class TestWorkingStates(unittest.TestCase):
    def test_working_membership(self):
        for s in ("UNDERSTANDING", "THINKING", "PLANNING", "EXECUTING",
                  "OBSERVING", "VERIFYING", "RECOVERING", "INITIALISING"):
            self.assertIn(s, WORKING_STATES, s)

    def test_idle_states_are_not_working(self):
        for s in ("IDLE", "LISTENING", "SPEAKING", "SLEEPING",
                  "WAITING_CONFIRMATION", "ERROR", "MUTED"):
            self.assertNotIn(s, WORKING_STATES, s)

    def test_is_working_defaults_to_current(self):
        _reset_for_tests()
        transition("VERIFYING")
        self.assertTrue(is_working())
        self.assertTrue(is_working("EXECUTING"))
        self.assertFalse(is_working("LISTENING"))


class TestRuntimeWiring(unittest.TestCase):
    """Source guards: the honest emission points must exist in the runtime.

    These read the source instead of driving Qt/asyncio — the point is to
    catch a regression that deletes an emission, not to re-test the seams.
    """

    @classmethod
    def setUpClass(cls):
        cls.main_src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        cls.ui_src = (PROJECT_ROOT / "ui.py").read_text(encoding="utf-8")
        cls.avatar_src = (PROJECT_ROOT / "core" / "avatar.py").read_text(
            encoding="utf-8")
        cls.gen_src = (PROJECT_ROOT / "actions" / "generate_image.py").read_text(
            encoding="utf-8")

    def test_main_emits_understanding_on_user_turn(self):
        self.assertIn('set_state("UNDERSTANDING")', self.main_src)

    def test_main_emits_executing_at_tool_dispatch(self):
        self.assertIn('set_state("EXECUTING")', self.main_src)
        # the old tool-time THINKING must be gone: execution is not thinking
        self.assertNotIn('set_state("THINKING")\n\n\n        if name == "save_memory"',
                         self.main_src)

    def test_main_emits_error_from_speak_error(self):
        i = self.main_src.index("def speak_error")
        body = self.main_src[i:i + 500]
        self.assertIn('set_state("ERROR")', body)

    def test_main_distinguishes_recovering_from_initialising(self):
        self.assertIn('set_state("RECOVERING")', self.main_src)
        self.assertIn('set_state("INITIALISING")', self.main_src)
        self.assertIn("_connect_n", self.main_src)

    def test_confirm_gate_sets_waiting_state(self):
        i = self.ui_src.index("def _show_confirm_banner")
        body = self.ui_src[i:i + 700]
        self.assertIn('"WAITING_CONFIRMATION"', body)
        self.assertIn("_pre_confirm_state", body)
        j = self.ui_src.index("def _hide_confirm_banner")
        hide = self.ui_src[j:j + 700]
        self.assertIn("_pre_confirm_state", hide)

    def test_apply_state_routes_through_state_machine(self):
        i = self.ui_src.index("def _apply_state")
        body = self.ui_src[i:i + 700]
        self.assertIn("_state_transition", body)

    def test_avatar_reacts_to_working_states_and_error(self):
        self.assertIn("WORKING_STATES", self.avatar_src)
        self.assertIn('"ERROR"', self.avatar_src)
        self.assertIn('"WAITING_CONFIRMATION"', self.avatar_src)

    def test_visual_engine_emits_verifying(self):
        self.assertEqual(self.gen_src.count('set_state("VERIFYING")'), 2)

    def test_hud_renders_every_v3_state(self):
        for s in ("UNDERSTANDING", "PLANNING", "EXECUTING", "OBSERVING",
                  "VERIFYING", "WAITING_CONFIRMATION", "ERROR", "RECOVERING",
                  "IDLE"):
            self.assertIn(f'"{s}"', self.ui_src, f"HUD has no branch for {s}")


if __name__ == "__main__":
    unittest.main()
