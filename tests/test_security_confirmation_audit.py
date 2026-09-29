"""M12 — security + confirmation coverage audit (§3.14, §3.17).

Two audits encoded as tests so they cannot silently rot:

§3.14 — every action core/safety_manager classifies DANGEROUS is protected
    by a real mechanism (the human gate, or an undo that puts the files
    back), and the pre-V3 forgeable gate — the model writing `confirmed=` /
    `confirm_key=` itself — is gone from every tool.

§3.17 — `config/api_keys.json` stays ignored and untracked, no
    secret-shaped literals live in the source tree, and every dashboard
    route is either auth-gated or one of the documented authentication
    schemes (login/pairing pages, static assets, query-token sockets).
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.safety_manager import Risk, _RISK_MAP  # noqa: E402

SERVER_PY = ROOT / "dashboard" / "server.py"


def _function_body(src: str, func: str) -> str:
    """Source of `def func` until the next top-level def/class."""
    m = re.search(rf"^def {re.escape(func)}\(", src, re.M)
    if not m:
        return ""
    rest = src[m.end():]
    nxt = re.search(r"^(?:def |class |@)", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


class TestDangerousActionsAreProtected(unittest.TestCase):
    """§3.14: safety's DANGEROUS map ⇄ an actual protection, tool by tool."""

    def setUp(self):
        self.dangerous = {k for k, v in _RISK_MAP.items()
                          if v is Risk.DANGEROUS and k[0] != "*"}

    def test_inventory_is_the_expected_nine(self):
        # If safety learns a new dangerous action, this test forces the
        # question "how is it protected?" instead of passing silently.
        self.assertEqual(self.dangerous, {
            ("computer_settings", "shutdown"),
            ("computer_settings", "restart"),
            ("computer_settings", "toggle_wifi"),
            ("file_controller", "delete"),
            ("file_controller", "organize_desktop"),
            ("desktop_control", "clean"),
            ("desktop_control", "organize"),
            ("memory_controller", "clear"),
            ("gmail", "delete"),
        })

    def test_computer_settings_irreversible_behind_the_banner(self):
        import actions.computer_settings as cs
        wanted = {a for t, a in self.dangerous if t == "computer_settings"}
        self.assertTrue(wanted <= set(cs._IRREVERSIBLE),
                        f"not gated: {wanted - set(cs._IRREVERSIBLE)}")
        src = (ROOT / "actions" / "computer_settings.py").read_text("utf-8")
        self.assertIn("confirm.request", src)

    def test_file_controller_dangerous_paths_push_undo(self):
        src = (ROOT / "actions" / "file_controller.py").read_text("utf-8")
        for func in ("delete_file", "organize_desktop"):
            self.assertIn("push_undo", _function_body(src, func),
                          f"{func} has no undo")

    def test_desktop_control_batches_push_undo(self):
        src = (ROOT / "actions" / "desktop.py").read_text("utf-8")
        for func in ("organize_desktop", "clean_desktop"):
            self.assertIn("push_undo", _function_body(src, func),
                          f"{func} has no undo")
        from actions.desktop import TOOL
        self.assertTrue(TOOL["supports_undo"],
                        "desktop_control must declare its undo")

    def test_memory_clear_is_behind_the_human_gate(self):
        src = (ROOT / "actions" / "memory_controller.py").read_text("utf-8")
        self.assertIn("confirm.request", src,
                      "clear must park behind the banner")
        self.assertNotIn('params.get("confirm_key"', src,
                         "the model must not supply the confirmation key")

    def test_gmail_delete_lands_in_the_trash(self):
        # Accepted, by doctrine: recoverable-by-design needs no banner
        # (confirm.py: anything reversible is pushed, not asked).
        src = (ROOT / "plugins" / "gmail.py").read_text("utf-8")
        body = _function_body(src, "_handle_delete")
        self.assertIn(".trash(", body)
        self.assertIn("moved to trash", body)


class TestForgeableGateIsGone(unittest.TestCase):
    """§3.14: the model can never answer the gate itself."""

    def test_no_tool_reads_a_model_written_confirmation_param(self):
        # The pre-V3 pattern confirm.py documents verbatim:
        #   confirmed = str(params.get("confirmed", "")).lower()
        for folder in ("actions", "plugins"):
            for py in (ROOT / folder).rglob("*.py"):
                src = py.read_text(encoding="utf-8")
                for pat in ('params.get("confirmed"', "params.get('confirmed'",
                            'params.get("confirm_key"', "params.get('confirm_key'"):
                    self.assertNotIn(pat, src,
                                     f"{py.name} still self-authorizes")

    def test_main_does_not_read_confirmation_params(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn('params.get("confirmed"', src)


class TestSecretsHygiene(unittest.TestCase):
    """§3.17: key file ignored + untracked, no secret literals in source."""

    def test_api_keys_is_in_gitignore(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("config/api_keys.json", gi)

    def test_api_keys_is_untracked_if_it_exists(self):
        keys = ROOT / "config" / "api_keys.json"
        if not keys.exists():
            self.skipTest("no api_keys.json on this machine")
        try:
            r = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "config/api_keys.json"],
                cwd=ROOT, capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("git unavailable")
        self.assertNotEqual(r.returncode, 0,
                            "config/api_keys.json is TRACKED — leak it and "
                            "the whole key set goes with the repo")

    def test_no_secret_shaped_literals_in_source(self):
        patterns = [
            re.compile(r"AIza[0-9A-Za-z_\-]{35}"),           # Google API key
            re.compile(r"['\"]sk-[A-Za-z0-9_\-]{16,}['\"]"),  # OpenAI-style
            re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),        # GitHub token
            re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),     # Slack token
        ]
        roots = [ROOT / d for d in ("actions", "plugins", "core",
                                    "dashboard", "memory")]
        files = [ROOT / "main.py", ROOT / "ui.py"]
        for d in roots:
            files.extend(d.rglob("*.py"))
        for py in files:
            src = py.read_text(encoding="utf-8", errors="replace")
            for pat in patterns:
                m = pat.search(src)
                if m:
                    self.fail(
                        f"secret-shaped literal {m.group(0)[:12]!r}… "
                        f"in {py.relative_to(ROOT)}")


class TestDashboardRoutesAuthGated(unittest.TestCase):
    """§3.16/§3.17: every route is auth'd or one of the documented schemes."""

    # These ARE the authentication surface: entry pages, static assets, and
    # the pairing endpoints that mint bearer tokens (they validate a one-time
    # PIN / device token inside their own bodies — the marker is what proves
    # it; GET /login is just the form page and needs no marker).
    AUTH_SCHEME = {
        "/", "/login", "/auto-login", "/api/device-login",
        "/static/crypto.js", "/manifest.webmanifest", "/sw.js",
    }
    SCHEME_MARKER = {
        ("post", "/login"): "_pending_keys",
        ("get", "/auto-login"): "_pending_keys",
        ("post", "/api/device-login"): "_device_sessions",
    }
    # Browsers can't set headers on downloads/websockets — these authenticate
    # via `?token=` checked against `self._tokens` in the handler body.
    QUERY_TOKEN = {"/ws", "/ws/phone-audio", "/uploads/{filename}"}

    def _routes(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        hits = list(re.finditer(
            r'@app\.(get|post|websocket)\("([^"]+)"', src))
        for i, m in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(src)
            yield m.group(1), m.group(2), src[m.start():end]

    def test_every_route_is_classified_and_guarded(self):
        rest = set()
        for method, path, body in self._routes():
            if path in self.AUTH_SCHEME:
                marker = self.SCHEME_MARKER.get((method, path))
                if marker:
                    self.assertIn(marker, body, f"{path} validates nothing")
                continue
            if path in self.QUERY_TOKEN:
                self.assertIn("self._tokens", body,
                              f"{path} does not check ?token=")
                continue
            if '"File uploads require:' in body:
                # The no-capability stub: returns 503 and does nothing —
                # there is no operation left to protect.
                continue
            self.assertIn("_auth(req)", body,
                          f"{method.upper()} {path} is reachable without auth")
            rest.add(path)
        # The audit surface itself must stay classified, not vanish.
        self.assertTrue({"/api/status", "/api/events", "/api/emergency-stop"}
                        <= rest)

    def test_unauthenticated_status_is_401(self):
        from fastapi.testclient import TestClient
        from dashboard.server import DashboardServer, _DEPS_OK
        if not _DEPS_OK:
            self.skipTest("dashboard deps missing")
        srv = DashboardServer()
        with TestClient(srv.app) as c:
            for path, method in (("/api/status", "get"),
                                 ("/api/events", "get"),
                                 ("/api/emergency-stop", "post")):
                r = getattr(c, method)(path)
                self.assertEqual(r.status_code, 401, path)


if __name__ == "__main__":
    unittest.main()
