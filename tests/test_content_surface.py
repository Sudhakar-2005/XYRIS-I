"""M9 — content surface unification (§3.2, §3.10) + mic-mute verification.

The milestone row reads: "Content surface unification + video mic-mute
verification". Concretely, in this codebase that is:

* ONE route from a tool result to the ContentPanel — ``_surface_content`` —
  instead of the old web_search-only special case: declared content actions
  surface reference-shaped results, plugins surface on the same length rule,
  failure-shaped results never appear as content, and what the user reads is
  the tool's answer (the M8 verdict is appended after the surface call).
* The mute path made testable at the audio choke: ``_pc_mic_blocked`` is the
  named decision every mic frame passes through — muted (the user silenced
  the mic while, e.g., a video plays) or phone-owned silences the PC mic
  before a byte is queued, and the reason is assertable, not implied.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from core.action_loader import ToolPolicy  # noqa: E402
from core.verifier import (  # noqa: E402
    VerificationResult, VerificationRule, VerificationStatus,
    VerificationStrategy, Verifier,
)

MAIN_PY = (ROOT / "main.py").read_text(encoding="utf-8")

LONG = ("Detailed report. " * 20).strip()      # ≈ 340 chars — reference-shaped
SHORT = "Opened Notepad."


class _RecordingUI:
    muted = True
    current_file = None

    def __init__(self, raise_on_show=False):
        self.shown: list[tuple[str, str]] = []
        self._raise = raise_on_show

    def set_state(self, s):
        pass

    def write_log(self, s):
        pass

    def show_content(self, title, text):
        if self._raise:
            raise RuntimeError("panel exploded")
        self.shown.append((title, text))


class _FakeActions:
    def __init__(self, result="Done.", names=None):
        self._result = result
        # names=None → every name is an action (the usual dispatch test);
        # pass a set to push a name into the plugin branch instead.
        self._names = names

    def has(self, name):
        return name in self._names if self._names is not None else True

    def policy(self, name):
        return None

    def scheduling(self, name):
        return None

    def run(self, name, args, ctx):
        return self._result


class _FakePlugins:
    def __init__(self, names=(), result=LONG):
        self._names = set(names)
        self._result = result

    def has(self, name):
        return name in self._names

    def scheduling(self, name):
        return None

    def run(self, name, args, **kw):
        return self._result


def _make_live(ui=None, plugins=None, actions=None) -> main.JarvisLive:
    live = main.JarvisLive.__new__(main.JarvisLive)
    live.ui = ui or _RecordingUI()
    live.speak = lambda *a, **k: None
    live.speak_error = lambda *a, **k: None
    live._action_registry = actions or _FakeActions()
    live._plugin_registry = plugins if plugins is not None else _FakePlugins()
    live._phone_active = False
    return live


def _fc(name, args=None):
    return SimpleNamespace(id="fc-c", name=name, args=args or {})


class TestPcMicGate(unittest.TestCase):
    """The mute path, verified at the choke (M9)."""

    def test_muted_blocks_with_the_reason(self):
        live = _make_live()
        live.ui.muted = True
        live._phone_active = False
        self.assertEqual(live._pc_mic_blocked(), "muted")

    def test_phone_owns_the_mic(self):
        live = _make_live()
        live.ui.muted = False
        live._phone_active = True
        self.assertEqual(live._pc_mic_blocked(), "phone")

    def test_muted_wins_over_phone_in_the_reason(self):
        live = _make_live()
        live.ui.muted = True
        live._phone_active = True
        self.assertEqual(live._pc_mic_blocked(), "muted")

    def test_clear_mic_is_not_blocked(self):
        live = _make_live()
        live.ui.muted = False
        live._phone_active = False
        self.assertEqual(live._pc_mic_blocked(), "")

    def test_listening_loop_uses_the_named_gate(self):
        self.assertIn("if not self._pc_mic_blocked():", MAIN_PY)
        # The old inline condition must be gone — one gate, not two spellings.
        self.assertNotIn("not self.ui.muted and not self._phone_active",
                         MAIN_PY)

    def test_phone_relay_keeps_its_own_muted_check(self):
        # The phone path must NOT consult _phone_active (it would block
        # itself); it gates on muted only, still inline and still guarded.
        self.assertIn("if not speaking and not self.ui.muted:",
                      MAIN_PY)


class TestSurfaceContent(unittest.TestCase):
    """The single route to the panel, tested directly on the helper."""

    def _surface(self, name, args, result, ui=None, plugins=None):
        live = _make_live(ui=ui, plugins=plugins)
        live._surface_content(name, args, result)
        return live.ui.shown

    def test_web_search_keeps_its_mode_query_label(self):
        shown = self._surface("web_search",
                              {"mode": "search", "query": "my query"},
                              "Results body")
        self.assertEqual(shown, [("SEARCH — my query", "Results body")])

    def test_web_search_failure_is_not_surfaced(self):
        shown = self._surface("web_search", {"query": "q"},
                              "No results for q")
        self.assertEqual(shown, [])

    def test_declared_action_long_result_surfaces(self):
        shown = self._surface("youtube_video", {"query": "q"}, LONG)
        self.assertEqual(shown, [("YOUTUBE", LONG)])

    def test_declared_action_short_result_stays_in_the_voice(self):
        shown = self._surface("youtube_video", {"query": "q"}, SHORT)
        self.assertEqual(shown, [])

    def test_plugin_results_surface_on_the_length_rule(self):
        shown = self._surface("my_plugin", {}, LONG,
                              plugins=_FakePlugins({"my_plugin"}))
        self.assertEqual(shown, [("MY PLUGIN", LONG)])
        short = self._surface("my_plugin", {}, SHORT,
                              plugins=_FakePlugins({"my_plugin"}))
        self.assertEqual(short, [])

    def test_undeclared_action_never_surfaces(self):
        shown = self._surface("open_app", {"app_name": "x"}, LONG)
        self.assertEqual(shown, [])

    def test_none_result_is_not_surfaced(self):
        self.assertEqual(self._surface("web_search", {}, None), [])

    def test_failure_prefixes_are_never_content(self):
        # Padded past the floor so only the prefix rule can be skipping them.
        for pre in ("Tool 'x' failed: ", "Unknown action: ", "Could not do it ",
                    "Browser error (go_to): "):
            shown = self._surface("youtube_video", {}, pre + LONG)
            self.assertEqual(shown, [], pre)

    def test_panel_crash_never_breaks_dispatch(self):
        ui = _RecordingUI(raise_on_show=True)
        # must not raise
        self._surface("youtube_video", {}, LONG, ui=ui)
        self.assertEqual(ui.shown, [])


class TestDispatchSurfaces(unittest.TestCase):
    """Through the real _dispatch_tool."""

    def _dispatch(self, name, args, actions=None, plugins=None,
                  verifier=None):
        ui = _RecordingUI()
        live = _make_live(ui=ui, plugins=plugins, actions=actions)
        policy = ToolPolicy(verification="verifier" if verifier else "none",
                            timeout=10)
        with mock.patch.object(main, "VERIFIER", verifier or Verifier()):
            resp = asyncio.run(live._dispatch_tool(_fc(name, args), policy))
        return resp, ui.shown

    def test_declared_action_result_reaches_the_panel(self):
        resp, shown = self._dispatch(
            "code_helper", {}, actions=_FakeActions(result=LONG))
        self.assertEqual(shown, [("CODE", LONG)])
        self.assertEqual(resp.response["result"], LONG)

    def test_plugin_result_reaches_the_panel(self):
        resp, shown = self._dispatch(
            "calendar", {}, actions=_FakeActions(names=set()),
            plugins=_FakePlugins({"calendar"}))
        self.assertEqual(shown, [("CALENDAR", LONG)])
        self.assertEqual(resp.response["result"], LONG)

    def test_web_search_label_survives_the_unification(self):
        resp, shown = self._dispatch(
            "web_search", {"mode": "search", "query": "news today"},
            actions=_FakeActions(result="Some result list"))
        self.assertEqual(shown, [("SEARCH — news today", "Some result list")])

    def test_user_sees_the_answer_not_the_verdict(self):
        fresh = Verifier()
        fresh.register_rule(VerificationRule(
            tool_name="code_helper",
            strategy=VerificationStrategy.CUSTOM,
            check_fn=lambda a, o: VerificationResult(
                status=VerificationStatus.FAILED,
                message="probe said no")))
        resp, shown = self._dispatch("code_helper", {},
                                     actions=_FakeActions(result=LONG),
                                     verifier=fresh)
        # panel: the tool's own answer; response: answer + the model's verdict
        self.assertEqual(shown, [("CODE", LONG)])
        self.assertNotIn("[VERIFICATION FAILED]", shown[0][1])
        self.assertIn("[VERIFICATION FAILED]", resp.response["result"])


if __name__ == "__main__":
    unittest.main()
