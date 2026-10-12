"""M8 — wiring core/verifier.py into the dispatcher (audit §3.13).

The audit's ask: "wire Verifier for important actions (process exists?
window visible? URL loaded?) before claiming success. Test: per-tool verify
tests with fake process/window probes." Covered here, all offline:

* action_loader: the new ``verification="verifier"`` mode, the mode/probe
  agreement rules (both mismatch directions rejected at load), and the fact
  that ``verify`` never reaches the API's function declarations.
* registration: actions declaring a probe bind into the Verifier at startup.
* ``verification_note`` — each status worded as the claim it supports
  (VERIFIED may be reported; FAILED must never be).
* ``_dispatch_tool`` — the real wiring: verdict appended to the result,
  bounded by VERIFY_TIMEOUT_S, skipped when the dispatch itself failed.
* per-tool probes with fake process/window/browser probes: open_app proves a
  process is running, computer_control proves the file/window exists,
  browser_control reads the URL back — and everything unobservable says so.
"""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from core.action_loader import (  # noqa: E402
    ActionRegistry, _validate, discover_actions, policy_from_meta,
)
from core.verifier import (  # noqa: E402
    VerificationResult, VerificationRule, VerificationStatus,
    VerificationStrategy, Verifier,
)
from actions import browser_control, computer_control, open_app  # noqa: E402


def _v(status, message="m", confidence=0.5):
    return VerificationResult(status=status, confidence=confidence,
                              message=message)


def _load_registry() -> ActionRegistry:
    return discover_actions(ROOT / "actions", reserved_names=set(),
                            logger=lambda m: None)


class _FakeUI:
    muted = True
    current_file = None

    def set_state(self, s):
        pass

    def write_log(self, s):
        pass

    def show_content(self, *a):
        pass


class _FakePluginReg:
    def has(self, name):
        return False

    def scheduling(self, name):
        return None


class _FakeActions:
    """Minimal action registry for dispatch tests; `run` is per-test."""

    def __init__(self, result="Done.", raises=None):
        self._result, self._raises = result, raises

    def has(self, name):
        return True

    def policy(self, name):
        return None

    def scheduling(self, name):
        return None

    def run(self, name, args, ctx):
        if self._raises is not None:
            raise self._raises
        return self._result


def _make_live(actions=None) -> main.JarvisLive:
    live = main.JarvisLive.__new__(main.JarvisLive)
    live.ui = _FakeUI()
    live.speak = lambda *a, **k: None
    live.speak_error = lambda *a, **k: None
    live._action_registry = actions or _FakeActions()
    live._plugin_registry = _FakePluginReg()
    return live


def _fc(name, args=None):
    return SimpleNamespace(id="fc-v", name=name, args=args or {})


class TestVerifierMode(unittest.TestCase):
    """The new metadata mode and its load-time agreement rules."""

    def test_mode_accepted_by_policy(self):
        pol, err = policy_from_meta({"verification": "verifier"})
        self.assertEqual(err, "")
        self.assertEqual(pol.verification, "verifier")

    @staticmethod
    def _tool(**meta):
        return SimpleNamespace(TOOL={
            "name": "t_probe", "description": "d",
            "parameters": {"type": "OBJECT", "properties": {}},
            "handler": lambda parameters=None: "ok",
            **meta,
        })

    def test_verifier_mode_with_callable_is_valid(self):
        rec = _validate(self._tool(verification="verifier",
                                   verify=lambda a, o: None), "t.py")
        self.assertTrue(rec.valid, rec.error)
        self.assertTrue(callable(rec.verify_fn))
        self.assertEqual(rec.policy.verification, "verifier")

    def test_verifier_mode_without_probe_rejected(self):
        rec = _validate(self._tool(verification="verifier"), "t.py")
        self.assertFalse(rec.valid)
        self.assertIn("verifier", rec.error)

    def test_probe_without_verifier_mode_rejected(self):
        rec = _validate(self._tool(verification="none",
                                   verify=lambda a, o: None), "t.py")
        self.assertFalse(rec.valid)
        self.assertIn("verifier", rec.error)

    def test_non_callable_probe_rejected(self):
        rec = _validate(self._tool(verification="verifier",
                                   verify="not callable"), "t.py")
        self.assertFalse(rec.valid)
        self.assertIn("callable", rec.error)

    def test_registry_exposes_probe(self):
        rec = _validate(self._tool(verification="verifier",
                                   verify=lambda a, o: None), "t.py")
        reg = ActionRegistry({"t_probe": rec}, logger=lambda m: None)
        self.assertTrue(callable(reg.verify_fn("t_probe")))
        self.assertIsNone(reg.verify_fn("missing"))


class TestRegistration(unittest.TestCase):
    """Startup binds the declared probes; declarations stay API-clean."""

    @classmethod
    def setUpClass(cls):
        cls.registry = _load_registry()

    def test_real_tools_declare_the_mode_and_probe(self):
        for name in ("open_app", "computer_control", "browser_control"):
            pol = self.registry.policy(name)
            self.assertEqual(pol.verification, "verifier", name)
            self.assertTrue(callable(self.registry.verify_fn(name)), name)

    def test_registration_binds_rules(self):
        fresh = Verifier()
        count = main._register_verifier_rules(self.registry, verifier=fresh)
        self.assertGreaterEqual(count, 3)
        for name in ("open_app", "computer_control", "browser_control"):
            rule = fresh.get_rule(name)
            self.assertIsNotNone(rule, name)
            self.assertEqual(rule.strategy, VerificationStrategy.CUSTOM)

    def test_probes_never_reach_the_api_declaration(self):
        for decl in self.registry.get_tool_declarations():
            self.assertLessEqual(
                set(decl), {"name", "description", "parameters", "behavior"},
                f"{decl.get('name')} leaked metadata to the API")

    def test_tools_without_probe_register_nothing(self):
        fresh = Verifier()
        main._register_verifier_rules(self.registry, verifier=fresh)
        self.assertIsNone(fresh.get_rule("file_processor"))
        self.assertIsNone(fresh.get_rule("web_search"))


class TestVerificationNote(unittest.TestCase):
    """Each status is worded as the claim the model may make from it."""

    def test_verified_allows_reporting(self):
        note = main.verification_note(_v(VerificationStatus.VERIFIED, "ok"))
        self.assertEqual(note, "\n[VERIFIED] ok")

    def test_failed_forbids_claiming(self):
        note = main.verification_note(
            _v(VerificationStatus.FAILED, "no process"))
        self.assertIn("[VERIFICATION FAILED] no process", note)
        self.assertIn("Do NOT claim this action succeeded", note)

    def test_partial_and_unverifiable_stay_uncertain(self):
        self.assertIn("[PARTIALLY VERIFIED]",
                      main.verification_note(
                          _v(VerificationStatus.PARTIAL, "handed over")))
        self.assertIn("[NOT VERIFIED]",
                      main.verification_note(
                          _v(VerificationStatus.UNVERIFIABLE, "no check")))


class TestDispatchWiring(unittest.TestCase):
    """The real _dispatch_tool, with a fake probe behind the Verifier."""

    def setUp(self):
        from core.action_loader import ToolPolicy
        self.policy = ToolPolicy(verification="verifier", timeout=10)
        self.policy_none = ToolPolicy(verification="none", timeout=10)

    def _dispatch(self, check_fn=None, policy=None, actions=None,
                  verify_timeout=None):
        live = _make_live(actions or _FakeActions(result="Opened Notepad."))
        fresh = Verifier()
        if check_fn is not None:
            fresh.register_rule(VerificationRule(
                tool_name="open_app",
                strategy=VerificationStrategy.CUSTOM,
                check_fn=check_fn))

        def _run():
            return asyncio.run(
                live._dispatch_tool(_fc("open_app", {"app_name": "Notepad"}),
                                    policy or self.policy))

        with mock.patch.object(main, "VERIFIER", fresh):
            if verify_timeout is None:
                return _run()
            with mock.patch.object(main, "VERIFY_TIMEOUT_S", verify_timeout):
                return _run()

    def test_verified_appends_the_verdict(self):
        resp = self._dispatch(
            lambda a, o: _v(VerificationStatus.VERIFIED,
                            "running process: notepad.exe", 0.85))
        out = resp.response["result"]
        self.assertIn("Opened Notepad.", out)
        self.assertIn("[VERIFIED] running process: notepad.exe", out)

    def test_failed_appends_the_forbidding_verdict(self):
        resp = self._dispatch(
            lambda a, o: _v(VerificationStatus.FAILED, "no matching process"))
        out = resp.response["result"]
        self.assertIn("[VERIFICATION FAILED] no matching process", out)
        self.assertIn("Do NOT claim", out)

    def test_slow_probe_reports_timeout_not_a_guess(self):
        def slow(a, o):
            time.sleep(1.0)
            return _v(VerificationStatus.VERIFIED, "too late")
        resp = self._dispatch(slow, verify_timeout=0.2)
        out = resp.response["result"]
        self.assertIn("[NOT VERIFIED]", out)
        self.assertIn("timed out", out)
        self.assertNotIn("too late", out)

    def test_crashing_probe_fails_closed(self):
        # Verifier._execute_verification catches probe exceptions itself and
        # reports FAILED — a check that blew up must never read as a pass.
        def boom(a, o):
            raise RuntimeError("probe exploded")
        resp = self._dispatch(boom)
        out = resp.response["result"]
        self.assertIn("[VERIFICATION FAILED]", out)
        self.assertIn("probe exploded", out)
        self.assertIn("Do NOT claim", out)

    def test_verifier_engine_crash_is_unverifiable(self):
        # The backstop in _run_verification: if verify() itself (not the
        # probe) raises, the note says the check did not finish.
        broken = mock.Mock()
        broken.verify.side_effect = RuntimeError("engine broke")
        live = _make_live(_FakeActions(result="Opened Notepad."))
        with mock.patch.object(main, "VERIFIER", broken):
            resp = asyncio.run(
                live._dispatch_tool(_fc("open_app", {"app_name": "Notepad"}),
                                    self.policy))
        out = resp.response["result"]
        self.assertIn("[NOT VERIFIED]", out)
        self.assertIn("verification crashed: engine broke", out)

    def test_verifier_mode_without_rule_is_honest(self):
        resp = self._dispatch(check_fn=None)
        out = resp.response["result"]
        self.assertIn("[NOT VERIFIED]", out)
        self.assertIn("No verification rule", out)

    def test_verification_none_appends_nothing(self):
        resp = self._dispatch(
            lambda a, o: _v(VerificationStatus.VERIFIED, "x"),
            policy=self.policy_none)
        out = resp.response["result"]
        self.assertEqual(out, "Opened Notepad.")

    def test_failed_dispatch_is_not_verified(self):
        actions = _FakeActions(raises=RuntimeError("handler died"))
        resp = self._dispatch(
            lambda a, o: _v(VerificationStatus.VERIFIED, "should not run"),
            actions=actions)
        out = resp.response["result"]
        self.assertIn("handler died", out)
        self.assertNotIn("[VERIFIED]", out)
        self.assertNotIn("[NOT VERIFIED]", out)

    # -- structured verdict (brain Phase 1): the automation adapter reads
    #    response["verification"] instead of sniffing failure-shaped text.

    def test_verified_carries_a_structured_verdict(self):
        resp = self._dispatch(
            lambda a, o: _v(VerificationStatus.VERIFIED, "running", 0.85))
        ver = resp.response.get("verification")
        self.assertIsInstance(ver, dict)
        self.assertTrue(ver["passed"])
        self.assertEqual(ver["status"], "verified")
        self.assertEqual(ver["tool"], "open_app")
        self.assertAlmostEqual(ver["confidence"], 0.85)
        self.assertEqual(ver["detail"], "running")

    def test_failed_carries_passed_false(self):
        resp = self._dispatch(
            lambda a, o: _v(VerificationStatus.FAILED, "no matching process"))
        ver = resp.response["verification"]
        self.assertFalse(ver["passed"])
        self.assertEqual(ver["status"], "failed")

    def test_unverifiable_is_uncertainty_not_failure(self):
        resp = self._dispatch(check_fn=None)
        ver = resp.response["verification"]
        self.assertTrue(ver["passed"])
        self.assertEqual(ver["status"], "unverifiable")

    def test_verification_none_has_no_verdict_key(self):
        resp = self._dispatch(
            lambda a, o: _v(VerificationStatus.VERIFIED, "x"),
            policy=self.policy_none)
        self.assertNotIn("verification", resp.response)

    def test_failed_dispatch_carries_no_verdict(self):
        actions = _FakeActions(raises=RuntimeError("handler died"))
        resp = self._dispatch(
            lambda a, o: _v(VerificationStatus.VERIFIED, "should not run"),
            actions=actions)
        self.assertNotIn("verification", resp.response)


class TestOpenAppProbe(unittest.TestCase):
    """Fake process probe: verified only when a process really runs."""

    def _verify(self, out, match):
        with mock.patch.object(open_app, "_running_match",
                               lambda app: match):
            return open_app.verify_opened({"app_name": "Notepad"}, out)

    def test_running_process_verifies(self):
        res = self._verify("Opened Notepad.", ["notepad.exe"])
        self.assertEqual(res.status, VerificationStatus.VERIFIED)
        self.assertIn("notepad.exe", res.message)

    def test_no_matching_process_fails(self):
        res = self._verify("Opened Notepad.", [])
        self.assertEqual(res.status, VerificationStatus.FAILED)

    def test_no_probe_available_is_unverifiable(self):
        res = self._verify("Opened Notepad.", None)
        self.assertEqual(res.status, VerificationStatus.UNVERIFIABLE)
        self.assertIn("no process probe", res.message)

    def test_handler_failure_has_nothing_to_verify(self):
        res = self._verify("Could not confirm that Notepad launched.",
                           ["notepad.exe"])
        self.assertEqual(res.status, VerificationStatus.UNVERIFIABLE)

    def test_probe_exception_is_unverifiable(self):
        with mock.patch.object(open_app, "_running_match",
                               side_effect=RuntimeError("psutil died")):
            res = open_app.verify_opened({"app_name": "x"}, "Opened x.")
        self.assertEqual(res.status, VerificationStatus.UNVERIFIABLE)
        self.assertIn("process probe failed", res.message)

    def test_tokens_cover_alias_processes(self):
        # "ms-settings:" launches SystemSettings.exe — the token must still
        # reach it.
        tokens = open_app._match_tokens("ms-settings")
        self.assertTrue(any("settings" in t for t in tokens), tokens)
        self.assertIn("notepad", open_app._match_tokens("Notepad"))


class TestComputerControlProbe(unittest.TestCase):
    """Fake file/window probes: check what exists, admit what does not."""

    def test_existing_screenshot_file_verifies(self):
        with mock.patch.object(
                computer_control, "_screenshot_path_from",
                lambda out: str(ROOT / "main.py")):
            res = computer_control.verify_control(
                {"action": "screenshot"},
                f"Screenshot saved: {ROOT / 'main.py'}")
        self.assertEqual(res.status, VerificationStatus.VERIFIED)

    def test_missing_screenshot_file_fails(self):
        with mock.patch.object(
                computer_control, "_screenshot_path_from",
                lambda out: str(ROOT / "does_not_exist.png")):
            res = computer_control.verify_control(
                {"action": "screenshot"}, "Screenshot saved: x.png")
        self.assertEqual(res.status, VerificationStatus.FAILED)

    def test_screenshot_without_path_is_unverifiable(self):
        res = computer_control.verify_control({"action": "screenshot"}, "ok")
        self.assertEqual(res.status, VerificationStatus.UNVERIFIABLE)

    def test_click_says_it_cannot_be_checked(self):
        res = computer_control.verify_control({"action": "click"}, "Clicked.")
        self.assertEqual(res.status, VerificationStatus.UNVERIFIABLE)
        self.assertIn("no observable post-state", res.message)

    def test_focused_window_verifies_via_fake_probe(self):
        with mock.patch.object(computer_control, "_window_probe",
                               lambda t: (True, True)):
            res = computer_control.verify_control(
                {"action": "focus_window", "title": "Notepad"}, "done")
        self.assertEqual(res.status, VerificationStatus.VERIFIED)
        self.assertIn("frontmost", res.message)

    def test_missing_window_fails_via_fake_probe(self):
        with mock.patch.object(computer_control, "_window_probe",
                               lambda t: (False, False)):
            res = computer_control.verify_control(
                {"action": "focus_window", "title": "Nope"}, "done")
        self.assertEqual(res.status, VerificationStatus.FAILED)

    def test_handler_failure_has_nothing_to_verify(self):
        res = computer_control.verify_control(
            {"action": "click"}, "computer_control 'click' failed: boom")
        self.assertEqual(res.status, VerificationStatus.UNVERIFIABLE)


class _FakeSession:
    def __init__(self, url="", error=None):
        self._url, self._error = url, error

    def get_url(self):
        return "get_url-coro"

    def run(self, coro, timeout=60):
        if self._error is not None:
            raise self._error
        return self._url


class _FakeRegistry:
    def __init__(self, has_session=False, session=None, native=""):
        self._has, self._sess = has_session, session
        self._last_native_url = native

    def has(self, browser=None):
        return self._has

    def get(self, browser=None):
        return self._sess


class TestBrowserControlProbe(unittest.TestCase):
    """Fake sessions: the URL is read back, natives admit they cannot be."""

    def _verify(self, args, output, registry):
        with mock.patch.object(browser_control, "_registry", registry):
            return browser_control.verify_browser(args, output)

    def test_expected_url_verifies(self):
        reg = _FakeRegistry(True, _FakeSession("https://example.com/x"))
        res = self._verify({"action": "go_to", "url": "https://example.com/x"},
                           "Opened.", reg)
        self.assertEqual(res.status, VerificationStatus.VERIFIED)
        self.assertEqual(res.confidence, 0.95)

    def test_any_live_page_verifies_with_its_real_url(self):
        reg = _FakeRegistry(True, _FakeSession("https://other.test/"))
        res = self._verify({"action": "search", "query": "q"}, "Opened.", reg)
        self.assertEqual(res.status, VerificationStatus.VERIFIED)
        self.assertIn("https://other.test/", res.message)

    def test_blank_page_fails(self):
        reg = _FakeRegistry(True, _FakeSession("about:blank"))
        res = self._verify({"action": "go_to", "url": "https://x"},
                           "Opened.", reg)
        self.assertEqual(res.status, VerificationStatus.FAILED)

    def test_unreadable_url_is_unverifiable(self):
        reg = _FakeRegistry(True, _FakeSession(error=RuntimeError("gone")))
        res = self._verify({"action": "go_to", "url": "https://x"},
                           "Opened.", reg)
        self.assertEqual(res.status, VerificationStatus.UNVERIFIABLE)

    def test_native_open_is_only_partial(self):
        reg = _FakeRegistry(False, native="https://native.test/")
        res = self._verify({"action": "go_to", "url": "https://native.test/"},
                           "Opened https://native.test/.", reg)
        self.assertEqual(res.status, VerificationStatus.PARTIAL)
        self.assertIn("cannot be confirmed", res.message)

    def test_native_claim_without_record_is_partial(self):
        # The handler said "Opened ..." but the URL note is gone (a later
        # interactive call may have consumed it): still PARTIAL — handed
        # over, not confirmed — never a blind pass.
        res = self._verify({"action": "search", "query": "q"},
                           "Opened.", _FakeRegistry(False))
        self.assertEqual(res.status, VerificationStatus.PARTIAL)

    def test_failed_native_open_is_unverifiable(self):
        res = self._verify({"action": "go_to", "url": "https://x"},
                           "Could not open that address.",
                           _FakeRegistry(False))
        self.assertEqual(res.status, VerificationStatus.UNVERIFIABLE)

    def test_interactive_action_admits_no_dom_check(self):
        res = self._verify({"action": "click", "text": "OK"}, "Clicked.",
                           _FakeRegistry(True, _FakeSession("https://x")))
        self.assertEqual(res.status, VerificationStatus.UNVERIFIABLE)
        self.assertIn("DOM checks are not implemented", res.message)

    def test_handler_error_has_nothing_to_verify(self):
        res = self._verify({"action": "go_to", "url": "https://x"},
                           "Browser error (go_to): boom",
                           _FakeRegistry(False))
        self.assertEqual(res.status, VerificationStatus.UNVERIFIABLE)


if __name__ == "__main__":
    unittest.main()
