"""Phase 40 — permission metadata as the dispatcher enforcement layer (§3.17).

The V3 audit left one line open after M12: "extend permission metadata
(§3.9) as the enforcement layer". This file is that line, in four layers:

  1. Metadata      — `permissions` parses into ToolPolicy, the closed
                     vocabulary is enforced at load, unknown values fail
                     the whole policy (loud at load, fail-closed at dispatch).
  2. Inventory     — every action and every inline tool declares at least
                     its real capabilities, all inside the vocabulary, and
                     config/permissions.json is ignored + untracked like
                     every other per-machine file.
  3. Ledger        — the two grant tiers (part-17: session vs permanent):
                     missing config = everything granted (pre-Phase-40
                     behavior), corrupt config = everything revoked (fail
                     closed), unknown names never granted.
  4. Dispatcher    — the real _execute_tool refuses a tool whose permission
                     is revoked BEFORE any other gate sees it, and runs
                     normally when the permission is granted or absent.
"""
from __future__ import annotations

import asyncio
import inspect
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import main
from core import confirm as confirm_gate
from core.action_loader import (
    ActionRecord, ActionRegistry, ToolPolicy, policy_from_meta,
    discover_actions,
)
from core.permissions import PERMISSION_VOCAB, PermissionLedger

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_registry() -> ActionRegistry:
    return discover_actions(PROJECT_ROOT / "actions", reserved_names=set(),
                            logger=lambda m: None)


class _FakeUI:
    muted = True
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


def _make_live(registry, ledger=None) -> main.JarvisLive:
    obj = main.JarvisLive.__new__(main.JarvisLive)
    obj.ui = _FakeUI()
    obj.speak = lambda *a, **k: None
    obj.speak_errors = []
    obj.speak_error = lambda name, err: obj.speak_errors.append(
        f"{name}: {err}")
    obj._action_registry = registry
    obj._plugin_registry = _FakePluginReg()
    obj._permissions = (ledger if ledger is not None
                        else PermissionLedger(load=False))
    return obj


def _fc(name: str, args: dict | None = None):
    return SimpleNamespace(id="fc-test", name=name, args=args or {})


def _probe_registry(name: str, policy: ToolPolicy,
                    calls: list) -> ActionRegistry:
    rec = ActionRecord(
        name=name, description="permission probe",
        handler=lambda parameters: calls.append(name) or "probe ran",
        valid=True, policy=policy)
    return ActionRegistry({name: rec}, logger=lambda m: None)


class TestMetadataParsing(unittest.TestCase):
    """Layer 1 — the sixth key parses, dedupes, and fails loudly."""

    def test_permissions_parse_into_a_tuple(self):
        pol, err = policy_from_meta({"permissions": ["network", "capture"]})
        self.assertEqual(err, "")
        self.assertEqual(pol.permissions, ("network", "capture"))

    def test_permissions_dedupe_but_keep_order(self):
        pol, err = policy_from_meta(
            {"permissions": ["process", "network", "process"]})
        self.assertEqual(err, "")
        self.assertEqual(pol.permissions, ("process", "network"))

    def test_missing_permissions_defaults_to_empty(self):
        pol, err = policy_from_meta({"risk": "safe"})
        self.assertEqual(err, "")
        self.assertEqual(pol.permissions, ())

    def test_bad_permissions_reject_the_whole_policy(self):
        for meta in ({"permissions": "network"},
                     {"permissions": ["bogus"]},
                     {"permissions": [1]},
                     {"permissions": {"a": 1}},
                     {"permissions": ["network", "NOT_A_PERM"]}):
            pol, err = policy_from_meta(meta)
            self.assertIsNone(pol, f"accepted bad permissions {meta}")
            self.assertIn("permissions", err)


class TestInventory(unittest.TestCase):
    """Layer 2 — the metadata exists everywhere and is gitignore-clean."""

    @classmethod
    def setUpClass(cls):
        cls.registry = _load_registry()

    def test_every_action_declares_at_least_one_permission(self):
        policies = self.registry.policies()
        self.assertGreaterEqual(len(policies), 21)
        for name, pol in policies.items():
            self.assertTrue(
                pol.permissions,
                f"action '{name}' declares no permissions — the Phase 40 "
                f"inventory must be explicit, not defaulted")
            for p in pol.permissions:
                self.assertIn(p, PERMISSION_VOCAB, f"{name}: {p}")

    def test_every_inline_tool_declares_the_key(self):
        for name, meta in main.INLINE_TOOL_META.items():
            self.assertIn("permissions", meta, f"inline '{name}'")
            pol, err = policy_from_meta(meta)
            self.assertIsNotNone(pol, f"inline '{name}': {err}")
            for p in pol.permissions:
                self.assertIn(p, PERMISSION_VOCAB, f"{name}: {p}")

    def test_no_action_declares_an_unknown_permission(self):
        # Redundant with the loader (policy_from_meta would fail the whole
        # policy), asserted directly so the failure names the file.
        import importlib
        for path in sorted((PROJECT_ROOT / "actions").glob("*.py")):
            if path.name.startswith("_"):
                continue
            mod = importlib.import_module(f"actions.{path.stem}")
            tool = getattr(mod, "TOOL", None)
            if not isinstance(tool, dict):
                continue
            perms = tool.get("permissions")
            self.assertIsInstance(perms, list, f"{path.name}: {perms!r}")
            for p in perms:
                self.assertIn(p, PERMISSION_VOCAB, f"{path.name}: {p}")

    def test_permissions_config_is_ignored_and_untracked(self):
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("config/permissions.json", gitignore)
        ignored = subprocess.run(
            ["git", "check-ignore", "config/permissions.json"],
            cwd=PROJECT_ROOT, capture_output=True)
        self.assertEqual(ignored.returncode, 0,
                         "git does not ignore config/permissions.json")
        tracked = subprocess.run(
            ["git", "ls-files", "config/permissions.json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True)
        self.assertEqual(tracked.stdout.strip(), "",
                         "config/permissions.json is tracked")


class TestLedger(unittest.TestCase):
    """Layer 3 — grant tiers, defaults, and fail-closed parsing."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "permissions.json"

    def test_missing_file_grants_everything(self):
        led = PermissionLedger(config_path=self.path)
        for p in PERMISSION_VOCAB:
            self.assertTrue(led.is_granted(p), p)

    def test_load_false_ignores_a_revoked_file(self):
        self.path.write_text(json.dumps({"revoked": list(PERMISSION_VOCAB)}),
                             encoding="utf-8")
        led = PermissionLedger(config_path=self.path, load=False)
        for p in PERMISSION_VOCAB:
            self.assertTrue(led.is_granted(p), p)

    def test_session_revoke_does_not_touch_the_file(self):
        led = PermissionLedger(config_path=self.path)
        self.assertTrue(led.revoke("network"))
        self.assertFalse(led.is_granted("network"))
        self.assertTrue(led.is_granted("filesystem"))
        self.assertFalse(self.path.exists(),
                         "session-tier revoke must not write the config")

    def test_permanent_revoke_persists_across_instances(self):
        led = PermissionLedger(config_path=self.path)
        self.assertTrue(led.revoke("capture", permanent=True))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["revoked"], ["capture"])
        reloaded = PermissionLedger(config_path=self.path)
        self.assertFalse(reloaded.is_granted("capture"))
        self.assertTrue(reloaded.is_granted("network"))

    def test_permanent_grant_clears_the_file(self):
        led = PermissionLedger(config_path=self.path)
        led.revoke("network", permanent=True)
        self.assertTrue(led.grant("network", permanent=True))
        reloaded = PermissionLedger(config_path=self.path)
        self.assertTrue(reloaded.is_granted("network"))

    def test_session_grant_overrides_a_permanent_revoke(self):
        led = PermissionLedger(config_path=self.path)
        led.revoke("network", permanent=True)
        self.assertFalse(led.is_granted("network"))
        self.assertTrue(led.grant("network"))          # session-only
        self.assertTrue(led.is_granted("network"))
        reloaded = PermissionLedger(config_path=self.path)
        self.assertFalse(reloaded.is_granted("network"))  # permanent stands

    def test_corrupt_config_fails_closed(self):
        self.path.write_text("{ not json", encoding="utf-8")
        led = PermissionLedger(config_path=self.path)
        for p in PERMISSION_VOCAB:
            self.assertFalse(led.is_granted(p), p)

    def test_wrong_shape_config_fails_closed(self):
        self.path.write_text(json.dumps({"revoked": "network"}),
                             encoding="utf-8")
        led = PermissionLedger(config_path=self.path)
        for p in PERMISSION_VOCAB:
            self.assertFalse(led.is_granted(p), p)

    def test_unknown_names_are_never_granted_or_changeable(self):
        led = PermissionLedger(config_path=self.path)
        self.assertFalse(led.is_granted("bogus"))
        self.assertFalse(led.grant("bogus"))
        self.assertFalse(led.revoke("bogus"))
        self.assertEqual(led.revoked(), frozenset())

    def test_config_entries_outside_the_vocab_are_dropped(self):
        self.path.write_text(json.dumps(
            {"revoked": ["network", "skynet"]}), encoding="utf-8")
        led = PermissionLedger(config_path=self.path)
        self.assertFalse(led.is_granted("network"))
        self.assertNotIn("skynet", led.revoked())


class TestDispatcherEnforcement(unittest.TestCase):
    """Layer 4 — the real _execute_tool against stubs."""

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

    @staticmethod
    def _run(coro, timeout=8.0):
        return asyncio.run(asyncio.wait_for(coro, timeout))

    @staticmethod
    def _capture(coro):
        buf = io.StringIO()
        with redirect_stdout(buf):
            resp = asyncio.run(asyncio.wait_for(coro, 8.0))
        return resp, buf.getvalue()

    def test_revoked_permission_refuses_before_dispatch(self):
        calls: list = []
        led = PermissionLedger(load=False)
        led.revoke("network")
        live = _make_live(
            _probe_registry("probe_net",
                            ToolPolicy(permissions=("network",)), calls),
            ledger=led)
        resp, out = self._capture(live._execute_tool(_fc("probe_net")))
        self.assertEqual(calls, [], "handler ran despite a revoked permission")
        self.assertIn("not granted", resp.response["result"])
        self.assertIn("network", resp.response["result"])
        self.assertIn("Nothing was done", resp.response["result"])
        self.assertIn("config/permissions.json", resp.response["result"])
        self.assertIn("permission not granted", out)
        self.assertEqual(self.shown, [], "a banner appeared for a refusal")

    def test_granted_permission_dispatches_normally(self):
        calls: list = []
        live = _make_live(
            _probe_registry("probe_ok",
                            ToolPolicy(permissions=("network",)), calls))
        resp, _ = self._capture(live._execute_tool(_fc("probe_ok")))
        self.assertEqual(calls, ["probe_ok"])
        self.assertIn("probe ran", resp.response["result"])

    def test_permissionless_tool_runs_even_with_everything_revoked(self):
        calls: list = []
        led = PermissionLedger(load=False)
        for p in PERMISSION_VOCAB:
            led.revoke(p)
        live = _make_live(
            _probe_registry("probe_bare", ToolPolicy(), calls), ledger=led)
        resp, _ = self._capture(live._execute_tool(_fc("probe_bare")))
        self.assertEqual(calls, ["probe_bare"])

    def test_refusal_sets_the_state_when_unmuted(self):
        calls: list = []
        led = PermissionLedger(load=False)
        led.revoke("desktop")
        live = _make_live(
            _probe_registry("probe_ui",
                            ToolPolicy(permissions=("desktop",)), calls),
            ledger=led)
        live.ui.muted = False
        self._capture(live._execute_tool(_fc("probe_ui")))
        self.assertEqual(_FakeUI.states, ["LISTENING"])

    def test_permissions_gate_runs_before_the_confirmation_gate(self):
        # A dangerous+gated tool whose permission is revoked must be
        # refused outright — availability first, consent second — so no
        # banner may go up and the confirm gate is never asked.
        calls: list = []
        led = PermissionLedger(load=False)
        led.revoke("process")
        live = _make_live(
            _probe_registry("probe_gate",
                            ToolPolicy(risk="dangerous",
                                       requires_confirmation=True,
                                       permissions=("process",)),
                            calls),
            ledger=led)
        resp, out = self._capture(live._execute_tool(_fc("probe_gate")))
        self.assertEqual(calls, [])
        self.assertEqual(self.shown, [])
        self.assertIn("not granted", resp.response["result"])
        self.assertNotIn("[CONFIRMATION_PENDING]", resp.response["result"])
        self.assertIn("permission not granted", out)

    def test_forbidden_stays_the_first_refusal(self):
        # forbidden is the stronger classification: its message wins even
        # when a permission is also revoked.
        calls: list = []
        led = PermissionLedger(load=False)
        led.revoke("process")
        live = _make_live(
            _probe_registry("probe_forbidden",
                            ToolPolicy(risk="forbidden",
                                       permissions=("process",)),
                            calls),
            ledger=led)
        resp, _ = self._capture(live._execute_tool(_fc("probe_forbidden")))
        self.assertEqual(calls, [])
        self.assertIn("marked forbidden", resp.response["result"])

    def test_real_action_policies_pass_under_default_grants(self):
        # The production default (no config file) must not change a single
        # dispatch: every real action still runs its gate path normally.
        live = _make_live(self.registry)
        resp, _ = self._capture(
            live._execute_tool(_fc("file_controller",
                                   {"action": "invalid_action"})))
        self.assertIn("Unknown action", resp.response["result"])

    def test_shutdown_still_reaches_its_own_guard(self):
        # shutdown_jarvis declares ["process"]; with the permission granted
        # the Phase 40 gate is a no-op and the M2 shutdown guard still
        # refuses an unspoken session.
        live = _make_live(self.registry)

        async def scenario():
            resp = await live._execute_tool(_fc("shutdown_jarvis"))
            self.assertIn("Refused", resp.response["result"])

        self._run(scenario())

    def test_live_instance_loads_the_ledger_in_init(self):
        src = inspect.getsource(main.JarvisLive.__init__)
        self.assertIn("PermissionLedger()", src)


class TestSourceGuards(unittest.TestCase):
    """The seam must stay in the dispatcher."""

    @classmethod
    def setUpClass(cls):
        cls.src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    def test_execute_tool_runs_the_shared_gate(self):
        # Brain Phase 1: the gate moved into core/gates.py so the automation
        # path cannot drift from the chat path — the entry must call it, and
        # the model-visible denial print stays here.
        i = self.src.index("async def _execute_tool")
        j = self.src.index("def _tool_policy")
        entry = self.src[i:j]
        self.assertIn("evaluate_gates(", entry)
        self.assertIn("permission not granted", entry)

    def test_the_gate_precedes_the_confirmation_gate(self):
        gates_src = (PROJECT_ROOT / "core" / "gates.py").read_text(
            encoding="utf-8")
        body = gates_src[gates_src.index("def evaluate_gates"):]
        i_forbidden = body.index("forbidden")
        i_permissions = body.index("permissions")
        i_confirm = body.index("requires_confirmation")
        self.assertLess(i_forbidden, i_permissions,
                        "availability: forbidden refused first")
        self.assertLess(i_permissions, i_confirm,
                        "availability check must run before consent")
        self.assertIn("is_granted", body)

    def test_permissions_metadata_never_reaches_gemini(self):
        decls = main.__dict__["TOOL_DECLARATIONS"]
        for d in decls:
            self.assertNotIn("permissions", d)
        registry = _load_registry()
        allowed = {"name", "description", "parameters", "behavior"}
        for d in registry.get_tool_declarations():
            self.assertLessEqual(set(d), allowed)


if __name__ == "__main__":
    unittest.main()
