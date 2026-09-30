"""AETHER-I V3 — Phase 4 tool metadata + dispatcher enforcement tests.

Three layers, matching the audit (§3.9, §3.14):
  1. Schema validation — every TOOL dict (actions + inline) declares the five
     metadata keys with valid values, and the loader agrees.
  2. Coverage invariants — dangerous ⇒ gated, supports_undo ⇒ the file really
     pushes undo, gated tools warn the model in their description, and the
     confirmation can only be resolved by the UI, never by model-reachable
     code.
  3. Dispatcher enforcement — the real JarvisLive._execute_tool, driven with
     a stub UI and a scripted registry: forbidden is refused, gated parks
     behind core/confirm and runs only after resolve(True), invalid metadata
     fails closed, and the timeout bound returns the model's turn.
"""

from __future__ import annotations

import asyncio
import importlib
import time
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import main
from core import confirm as confirm_gate
from core.action_loader import (
    ActionRecord, ActionRegistry, ToolPolicy, policy_from_meta,
    RISK_LEVELS, VERIFICATION_MODES, DEFAULT_TIMEOUT_S,
    discover_actions,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACTION_FILES = sorted(p for p in (PROJECT_ROOT / "actions").glob("*.py")
                      if not p.name.startswith("_"))
INLINE_KEYS = ("risk", "timeout", "requires_confirmation",
               "supports_undo", "verification")


def _raw_tools() -> dict[str, tuple[str, dict]]:
    """filename → (tool name, raw TOOL dict) for every action file that
    declares one, imported straight from source — no loader defaults in
    between, so a missing key is visible."""
    out = {}
    for path in ACTION_FILES:
        mod = importlib.import_module(f"actions.{path.stem}")
        tool = getattr(mod, "TOOL", None)
        if isinstance(tool, dict):
            out[path.name] = (str(tool.get("name", "")), tool)
    return out


def _load_registry() -> ActionRegistry:
    return discover_actions(PROJECT_ROOT / "actions", reserved_names=set(),
                            logger=lambda m: None)


class _FakeUI:
    muted = True
    current_file = None
    states: list = []

    def set_state(self, s):
        _FakeUI.states.append(s)

    def write_log(self, s):
        pass

    def show_content(self, *a):
        pass


class _FakePluginReg:
    def has(self, name):
        return False

    def scheduling(self, name):
        return None


def _make_live(registry) -> main.JarvisLive:
    """A JarvisLive with just the dispatch surface — __init__ needs Qt and a
    live session; _execute_tool needs only these attributes."""
    obj = main.JarvisLive.__new__(main.JarvisLive)
    obj.ui = _FakeUI()
    obj.speak = lambda *a, **k: None
    obj.speak_errors = []
    obj.speak_error = lambda name, err: obj.speak_errors.append(
        f"{name}: {err}")
    obj._action_registry = registry
    obj._plugin_registry = _FakePluginReg()
    return obj


def _fc(name: str, args: dict | None = None):
    return SimpleNamespace(id="fc-test", name=name, args=args or {})


class TestSchemaValidation(unittest.TestCase):
    """Layer 1 — the five keys exist and are well-formed everywhere."""

    @classmethod
    def setUpClass(cls):
        cls.tools = _raw_tools()
        cls.registry = _load_registry()

    def test_every_action_tool_declares_all_five_keys(self):
        self.assertGreaterEqual(len(self.tools), 21)
        for fname, (name, tool) in self.tools.items():
            for key in INLINE_KEYS:
                self.assertIn(key, tool,
                              f"{fname} ({name}) missing metadata key '{key}'")

    def test_every_action_tool_metadata_parses(self):
        for fname, (name, tool) in self.tools.items():
            pol, err = policy_from_meta(tool)
            self.assertIsNotNone(pol, f"{fname} ({name}): {err}")

    def test_discovery_rejects_nothing_for_metadata(self):
        for rec in self.registry._all_records:
            if Path(rec.file).stem.startswith("_"):
                continue
            if getattr(rec, "error", ""):
                self.assertNotIn("metadata invalid", rec.error,
                                 f"{rec.file}: {rec.error}")

    def test_inline_table_matches_declarations_exactly(self):
        declared = {d["name"] for d in main.TOOL_DECLARATIONS}
        self.assertEqual(set(main.INLINE_TOOL_META), declared)

    def test_inline_entries_declare_all_five_keys_and_parse(self):
        for name, meta in main.INLINE_TOOL_META.items():
            for key in INLINE_KEYS:
                self.assertIn(key, meta, f"inline '{name}' missing '{key}'")
            pol, err = policy_from_meta(meta)
            self.assertIsNotNone(pol, f"inline '{name}': {err}")

    def test_policy_from_meta_defaults(self):
        for blank in (None, {}):
            pol, err = policy_from_meta(blank)
            self.assertEqual(err, "")
            self.assertEqual(pol.risk, "safe")
            self.assertEqual(pol.timeout, DEFAULT_TIMEOUT_S)
            self.assertFalse(pol.requires_confirmation)
            self.assertFalse(pol.supports_undo)
            self.assertEqual(pol.verification, "none")

    def test_policy_from_meta_rejects_bad_values(self):
        cases = [
            {"risk": "high"},
            {"risk": None},
            {"timeout": 0},
            {"timeout": 3601},
            {"timeout": True},          # bool is not an int here
            {"timeout": "30"},
            {"requires_confirmation": "yes"},
            {"supports_undo": 1},
            {"verification": "maybe"},
        ]
        for meta in cases:
            pol, err = policy_from_meta(meta)
            self.assertIsNone(pol, f"accepted bad metadata {meta}")
            self.assertTrue(err, f"no error for {meta}")

    def test_policy_from_meta_is_case_tolerant_on_risk(self):
        pol, err = policy_from_meta({"risk": "DANGEROUS"})
        self.assertEqual(err, "")
        self.assertEqual(pol.risk, "dangerous")

    def test_policy_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            ToolPolicy().risk = "safe"

    def test_registry_exposes_policies_and_describe(self):
        pol = self.registry.policy("send_message")
        self.assertEqual(pol.risk, "dangerous")
        self.assertTrue(pol.requires_confirmation)
        self.assertIsNone(self.registry.policy("no_such_tool"))
        self.assertIn("send_message", self.registry.policies())
        self.assertTrue(self.registry.describe("send_message"))


class TestDeclarationsStayGeminiSafe(unittest.TestCase):
    """Layer 1 — metadata must never leak into function_declarations."""

    @classmethod
    def setUpClass(cls):
        cls.decls = _load_registry().get_tool_declarations()

    def test_declaration_keys_are_exactly_the_api_shape(self):
        allowed = {"name", "description", "parameters", "behavior"}
        for d in self.decls:
            self.assertLessEqual(set(d), allowed, f"metadata leaked: {set(d)}")

    def test_no_metadata_key_names_in_declarations(self):
        for d in self.decls:
            for key in INLINE_KEYS:
                self.assertNotIn(key, d)


class TestCoverageInvariants(unittest.TestCase):
    """Layer 2 — §3.14 coverage audit."""

    @classmethod
    def setUpClass(cls):
        cls.tools = _raw_tools()
        cls.registry = _load_registry()

    def test_dangerous_tools_are_gated(self):
        for fname, (name, tool) in self.tools.items():
            if str(tool.get("risk", "")).lower() == "dangerous":
                self.assertTrue(
                    tool.get("requires_confirmation") is True,
                    f"{fname} ({name}) is dangerous but not gated")
        for name, meta in main.INLINE_TOOL_META.items():
            if meta["risk"] == "dangerous":
                self.assertTrue(meta["requires_confirmation"],
                                f"inline '{name}' is dangerous but not gated")

    def test_gated_tools_warn_the_model_in_their_description(self):
        # The model must know a banner is coming, or it will claim the act
        # is done (the computer_settings lesson).
        gated = [(n, t.get("description", ""))
                 for _, (n, t) in self.tools.items()
                 if t.get("requires_confirmation")]
        gated += [(n, next(d["description"] for d in main.TOOL_DECLARATIONS
                           if d["name"] == n))
                  for n, m in main.INLINE_TOOL_META.items()
                  if m["requires_confirmation"]]
        self.assertGreaterEqual(len(gated), 2)   # send_message + shutdown_jarvis
        for name, desc in gated:
            self.assertIn("confirmation", desc.lower(),
                          f"gated tool '{name}' does not warn the model")

    def test_undo_metadata_matches_the_source(self):
        for fname, (name, tool) in self.tools.items():
            if tool.get("supports_undo"):
                src = (PROJECT_ROOT / "actions" / fname).read_text(
                    encoding="utf-8")
                self.assertIn("push_undo", src,
                              f"{name} claims undo but never pushes it")
        # and nothing else claims it spuriously
        for fname, (name, tool) in self.tools.items():
            if not tool.get("supports_undo"):
                self.assertNotIn("push_undo(", self.registry.describe(name) or "")

    def test_self_check_verification_matches_the_source(self):
        for fname, (name, tool) in self.tools.items():
            if tool.get("verification") == "self_check":
                src = (PROJECT_ROOT / "actions" / fname).read_text(
                    encoding="utf-8").lower()
                self.assertIn("verify", src,
                              f"{name} claims self_check but never verifies")

    def test_confirmation_can_only_be_resolved_by_the_ui(self):
        # The model writes tool calls; core/confirm.resolve() runs the parked
        # action. If model-reachable code could call resolve, the gate would
        # be a convention again (the problem confirm.py was written against).
        import re
        resolve_re = re.compile(
            r"confirm(_gate)?\.resolve|from core\.confirm import resolve")
        hits = []
        for rel in ["main.py", *[f"actions/{p.name}" for p in ACTION_FILES],
                    *map(lambda p: f"plugins/{p.name}",
                         (PROJECT_ROOT / "plugins").glob("*.py"))]:
            src = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            if resolve_re.search(src):
                hits.append(rel)
        self.assertEqual(hits, [], f"resolve() called from model-reachable {hits}")
        ui_src = (PROJECT_ROOT / "ui.py").read_text(encoding="utf-8")
        self.assertRegex(ui_src, r"from core\.confirm import resolve")


class TestDispatcherEnforcement(unittest.TestCase):
    """Layer 3 — the real _execute_tool against stubs."""

    @classmethod
    def setUpClass(cls):
        cls.registry = _load_registry()

    def setUp(self):
        _FakeUI.states = []
        self.shown: list[tuple[str, str]] = []
        self.logged: list[str] = []
        confirm_gate.bind(lambda t, d: self.shown.append((t, d)),
                          lambda: None,
                          lambda m: self.logged.append(m))
        confirm_gate._pending = None
        self.addCleanup(self._reset_confirm)

    @staticmethod
    def _reset_confirm():
        confirm_gate._pending = None
        confirm_gate.bind(None, None, None)

    def _run(self, coro, timeout=8.0):
        return asyncio.run(asyncio.wait_for(coro, timeout))

    def test_plain_tool_dispatches_with_its_policy(self):
        live = _make_live(self.registry)
        resp = self._run(live._execute_tool(_fc("file_controller",
                                                {"action": "invalid_action"})))
        self.assertIn("Unknown action", resp.response["result"])
        self.assertEqual(self.shown, [])           # caution tool: no gate

    def test_forbidden_tool_is_refused_without_running(self):
        live = _make_live(self.registry)
        meta = {"risk": "forbidden", "timeout": 30,
                "requires_confirmation": False, "supports_undo": False,
                "verification": "none"}
        with mock.patch.object(main, "INLINE_TOOL_META",
                               {"doomsday": meta}):
            # even though its own meta does NOT ask for confirmation
            resp = self._run(live._execute_tool(_fc("doomsday")))
        self.assertIn("forbidden", resp.response["result"])
        self.assertEqual(self.shown, [])
        self.assertNotIn("doomsday", _FakeUI.states)

    def test_gated_tool_parks_and_runs_only_after_confirm(self):
        live = _make_live(self.registry)
        meta = {"risk": "caution", "timeout": 30,
                "requires_confirmation": True, "supports_undo": False,
                "verification": "none"}
        with mock.patch.object(main, "INLINE_TOOL_META",
                               {"gated_probe": meta}):

            async def scenario():
                resp = await live._execute_tool(_fc("gated_probe"))
                self.assertIn("[CONFIRMATION_PENDING]",
                              resp.response["result"])
                self.assertEqual(len(self.shown), 1)
                self.assertEqual(self.shown[0][0], "Gated probe")
                self.assertEqual(confirm_gate.pending_title(), "Gated probe")
                # parked, not run: only confirm's "awaiting" line exists yet
                self.assertFalse(any("Confirmed" in m for m in self.logged),
                                 self.logged)
                # a HUMAN answers now
                main.confirm_gate.resolve(accepted=True)
                await asyncio.sleep(0.4)      # worker + session loop round trip
                self.assertEqual(confirm_gate.pending_title(), "")

            self._run(scenario())
        # the parked dispatch really executed (unknown probe → honest result)
        self.assertTrue(any("Unknown tool: gated_probe" in m
                            for m in self.logged), self.logged)

    def test_cancelled_gate_never_dispatches(self):
        live = _make_live(self.registry)
        meta = {"risk": "caution", "timeout": 30,
                "requires_confirmation": True, "supports_undo": False,
                "verification": "none"}
        with mock.patch.object(main, "INLINE_TOOL_META",
                               {"gated_probe": meta}):

            async def scenario():
                await live._execute_tool(_fc("gated_probe"))
                main.confirm_gate.resolve(accepted=False)
                await asyncio.sleep(0.3)

            self._run(scenario())
        self.assertFalse(any("gated_probe" in m for m in self.logged),
                         self.logged)

    def test_shutdown_refused_before_any_user_input(self):
        # The model called shutdown_jarvis unprompted right after the
        # briefing — no banner must appear, nothing may park, and the
        # sentence must tell the model why it was refused.
        live = _make_live(self.registry)
        self.assertFalse(getattr(live, "_heard_user_text", False))

        async def scenario():
            resp = await live._execute_tool(_fc("shutdown_jarvis"))
            self.assertIn("Refused", resp.response["result"])
            self.assertIn("briefing does not count",
                          resp.response["result"])
            self.assertEqual(self.shown, [])
            self.assertEqual(confirm_gate.pending_title(), "")

        self._run(scenario())

    def test_shutdown_parks_once_the_user_has_spoken(self):
        # With real user input recorded, the gate behaves normally: the
        # banner goes up and nothing dispatches until a human answers.
        # CANCEL keeps the shutdown handler from ever executing here.
        live = _make_live(self.registry)
        live._heard_user_text = True

        async def scenario():
            resp = await live._execute_tool(_fc("shutdown_jarvis"))
            self.assertIn("[CONFIRMATION_PENDING]", resp.response["result"])
            self.assertEqual(len(self.shown), 1)
            self.assertEqual(confirm_gate.pending_title(), "Shutdown jarvis")
            main.confirm_gate.resolve(accepted=False)
            await asyncio.sleep(0.3)
            self.assertEqual(confirm_gate.pending_title(), "")

        self._run(scenario())
        self.assertFalse(any("Confirmed" in m for m in self.logged),
                         self.logged)

    def test_second_gate_waits_for_the_first(self):
        live = _make_live(self.registry)
        meta = {"risk": "caution", "timeout": 30,
                "requires_confirmation": True, "supports_undo": False,
                "verification": "none"}
        with mock.patch.object(main, "INLINE_TOOL_META",
                               {"gated_probe": meta}):

            async def scenario():
                first = await live._execute_tool(_fc("gated_probe"))
                self.assertIn("[CONFIRMATION_PENDING]", first.response["result"])
                second = await live._execute_tool(_fc("gated_probe"))
                self.assertIn("already a confirmation",
                              second.response["result"])
                self.assertEqual(len(self.shown), 1)   # no second banner

            self._run(scenario())

    def test_invalid_metadata_fails_closed(self):
        live = _make_live(self.registry)
        bad = {"risk": "banana", "timeout": 30,
               "requires_confirmation": False, "supports_undo": False,
               "verification": "none"}
        with mock.patch.object(main, "INLINE_TOOL_META", {"junk": bad}):
            pol = live._tool_policy("junk")
        self.assertEqual(pol.risk, "dangerous")
        self.assertTrue(pol.requires_confirmation)

    def test_unknown_tool_gets_safe_defaults_and_runs_to_unknown(self):
        live = _make_live(self.registry)
        pol = live._tool_policy("no_such_tool_anywhere")
        self.assertEqual(pol.risk, "safe")
        resp = self._run(live._execute_tool(_fc("no_such_tool_anywhere")))
        self.assertIn("Unknown tool", resp.response["result"])

    def test_registry_timeout_returns_the_turn(self):
        slow = ActionRecord(
            name="slow_probe", description="sleeps",
            handler=lambda parameters: time.sleep(1.6) or "too late",
            valid=True, policy=ToolPolicy(timeout=1))
        registry = ActionRegistry({"slow_probe": slow}, logger=lambda m: None)
        live = _make_live(registry)

        async def scenario():
            # measure INSIDE the loop: asyncio.run's teardown joins the
            # abandoned worker thread, which is not what the model sees
            t0 = time.monotonic()
            resp = await live._execute_tool(_fc("slow_probe"))
            took = time.monotonic() - t0
            self.assertIn("timed out after 1s", resp.response["result"])
            self.assertLess(took, 1.5)

        self._run(scenario())
        self.assertTrue(any("timed out" in e for e in live.speak_errors))

    def test_policy_lookup_order_registry_before_inline(self):
        live = _make_live(self.registry)
        # open_app is a real action; a colliding inline entry must lose
        with mock.patch.object(main, "INLINE_TOOL_META",
                               {"open_app": {"risk": "forbidden",
                                             "timeout": 5,
                                             "requires_confirmation": False,
                                             "supports_undo": False,
                                             "verification": "none"}}):
            pol = live._tool_policy("open_app")
        self.assertEqual(pol.risk, "safe")        # the registry's value


class TestSourceGuards(unittest.TestCase):
    """The enforcement seams must stay in the dispatcher."""

    @classmethod
    def setUpClass(cls):
        cls.src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    def test_execute_tool_is_the_policy_entry(self):
        i = self.src.index("async def _execute_tool")
        j = self.src.index("async def _dispatch_tool")
        entry = self.src[i:j]
        self.assertIn("policy.risk == \"forbidden\"", entry)
        self.assertIn("confirm_gate.request", entry)
        self.assertIn("run_coroutine_threadsafe", entry)
        self.assertIn("_dispatch_tool(fc, policy)", entry)

    def test_dispatch_enforces_the_timeout(self):
        i = self.src.index("async def _dispatch_tool")
        j = self.src.index("async def _send_realtime")
        body = self.src[i:j]
        # Two dispatch timeouts (the action run and the plugin run); the
        # third asyncio.wait_for in this slice is M8's verification-probe
        # bound (VERIFY_TIMEOUT_S), which must stay bounded too.
        self.assertEqual(body.count("timeout=policy.timeout"), 2)
        self.assertEqual(body.count("asyncio.wait_for"), 3)
        self.assertIn("except asyncio.TimeoutError", body)

    def test_inline_meta_is_never_in_tool_declarations(self):
        i = self.src.index("TOOL_DECLARATIONS = [")
        j = self.src.index("INLINE_TOOL_META")
        decls = self.src[i:j]
        for key in INLINE_KEYS:
            self.assertNotIn(f'"{key}":', decls)

    def test_every_vocabulary_value_is_lowercase(self):
        self.assertEqual([v for v in RISK_LEVELS if v != v.lower()], [])
        self.assertEqual([v for v in VERIFICATION_MODES if v != v.lower()], [])


if __name__ == "__main__":
    unittest.main()
