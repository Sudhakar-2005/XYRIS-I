"""M11 — event bus wired (§3.15) + dashboard events/health/emergency-stop
(§3.16).

Two contracts are under test:

* The dormant ``core/event_bus`` now rides alongside the existing print()
  logging: a fire-and-forget ``emit_event`` (never raises, never blocks,
  drops cleanly when the bus isn't running), one bridge subscriber feeding
  both dashboard surfaces, and real emissions from tool dispatch.
* Every observability/control route is auth-gated. An unauthenticated
  client must not read status, stream events, or reach emergency-stop —
  §3.16's "never unauthenticated OS control" is the assertion, not a comment.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from core.action_loader import ToolPolicy  # noqa: E402
from core.event_bus import EventBus, EventType, Priority  # noqa: E402
from dashboard.server import _DEPS_OK, DashboardServer  # noqa: E402

if _DEPS_OK:
    from fastapi.testclient import TestClient  # noqa: E402

requires_dashboard = unittest.skipUnless(
    _DEPS_OK, "fastapi/uvicorn not installed — dashboard disabled")

AUTH = {"Authorization": "Bearer tok"}


def _server() -> DashboardServer:
    srv = DashboardServer()
    srv._tokens.add("tok")
    return srv


class _FakeActions:
    def __init__(self, result="Done.", boom=None):
        self._result = result
        self._boom = boom

    def has(self, name):
        return True

    def policy(self, name):
        return None

    def scheduling(self, name):
        return None

    def run(self, name, args, ctx):
        if self._boom:
            raise self._boom
        return self._result


class _FakeUI:
    muted = True
    state = "LISTENING"
    current_file = None

    def set_state(self, s):
        pass

    def show_content(self, title, text):
        pass

    def write_log(self, s):
        pass


@requires_dashboard
class TestDashboardRoutes(unittest.TestCase):
    """§3.16: status / events / emergency-stop — all auth-gated."""

    def test_status_requires_auth(self):
        with TestClient(_server().app) as c:
            self.assertEqual(c.get("/api/status").status_code, 401)

    def test_status_reports_server_and_runtime(self):
        srv = _server()
        srv.set_status_callback(
            lambda: {"state": "LISTENING", "session": True})
        with TestClient(srv.app) as c:
            r = c.get("/api/status", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertIn("uptime_s", body["server"])
        self.assertEqual(body["runtime"]["state"], "LISTENING")

    def test_status_survives_a_broken_callback(self):
        srv = _server()

        def _boom():
            raise RuntimeError("runtime wedge")

        srv.set_status_callback(_boom)
        with TestClient(srv.app) as c:
            r = c.get("/api/status", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertIn("runtime wedge", r.json()["runtime"]["status_error"])

    def test_status_without_runtime_is_still_server_health(self):
        with TestClient(_server().app) as c:
            body = c.get("/api/status", headers=AUTH).json()
        self.assertEqual(body["runtime"], {})
        self.assertIn("clients", body["server"])

    def test_events_requires_auth(self):
        with TestClient(_server().app) as c:
            self.assertEqual(c.get("/api/events").status_code, 401)

    def test_events_ring_is_bounded_and_ordered(self):
        srv = _server()
        for i in range(250):
            srv.push_event({"n": i})
        with TestClient(srv.app) as c:
            events = c.get("/api/events", headers=AUTH).json()["events"]
        self.assertEqual(len(events), 200)          # deque(maxlen=200)
        self.assertEqual(events[0]["n"], 50)         # oldest-survivor first
        self.assertEqual(events[-1]["n"], 249)

    def test_emergency_stop_requires_auth(self):
        srv = _server()
        calls = []
        srv.set_emergency_callback(lambda: calls.append(1))
        with TestClient(srv.app) as c:
            r = c.post("/api/emergency-stop")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(calls, [])                  # never even invoked

    def test_emergency_stop_without_runtime_is_503(self):
        with TestClient(_server().app) as c:
            r = c.post("/api/emergency-stop", headers=AUTH)
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.json()["ok"])

    def test_emergency_stop_reaches_the_runtime(self):
        srv = _server()
        calls = []
        srv.set_emergency_callback(lambda: calls.append("stop"))
        with TestClient(srv.app) as c:
            r = c.post("/api/emergency-stop", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(calls, ["stop"])

    def test_emergency_stop_surfaces_runtime_failure(self):
        srv = _server()

        def _boom():
            raise RuntimeError("stop failed")

        srv.set_emergency_callback(_boom)
        with TestClient(srv.app) as c:
            r = c.post("/api/emergency-stop", headers=AUTH)
        self.assertEqual(r.status_code, 500)
        self.assertFalse(r.json()["ok"])

    def test_broadcast_can_skip_the_history_buffer(self):
        srv = _server()
        asyncio.run(srv.broadcast({"type": "log", "text": "keep me"}))
        asyncio.run(srv.broadcast({"type": "event"}, history=False))
        self.assertEqual(len(srv._history), 1)
        self.assertEqual(srv._history[0]["text"], "keep me")


class TestEmitEvent(unittest.TestCase):
    """§3.15: fire-and-forget — observability never breaks the observer."""

    def test_missing_bus_attribute_drops_cleanly(self):
        live = main.JarvisLive.__new__(main.JarvisLive)   # the test-fake shape
        self.assertFalse(live.emit_event(EventType.TOOL_RESULT))

    def test_stopped_bus_drops_cleanly(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live._event_bus = EventBus()                      # never started
        self.assertFalse(live.emit_event(EventType.TOOL_RESULT))

    def test_running_loop_bus_publishes(self):
        async def scenario():
            live = main.JarvisLive.__new__(main.JarvisLive)
            live._event_bus = EventBus()
            await live._event_bus.start()
            got, landed = [], asyncio.Event()

            async def capture(e):
                got.append(e)
                landed.set()

            live._event_bus.subscribe_all(capture)
            ok = live.emit_event(EventType.TOOL_RESULT, source="t",
                                 data={"n": 1}, priority=Priority.HIGH)
            await asyncio.wait_for(landed.wait(), 2)
            await live._event_bus.stop()
            return ok, got

        ok, got = asyncio.run(scenario())
        self.assertTrue(ok)
        self.assertEqual(got[0].source, "t")
        self.assertEqual(got[0].data, {"n": 1})
        self.assertEqual(got[0].priority, Priority.HIGH)


class TestRuntimeSurface(unittest.TestCase):
    """The status shape and emergency-stop, off the real class."""

    def _live(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live.ui = _FakeUI()
        live.session = object()
        live._phone_active = False
        live.audio_in_queue = None
        live._event_bus = EventBus()
        return live

    def test_dashboard_status_shape(self):
        live = self._live()
        live.ui.muted = False
        status = live._dashboard_status()
        self.assertEqual(status["state"], "LISTENING")
        self.assertTrue(status["session"])
        self.assertFalse(status["muted"])
        self.assertFalse(status["phone_active"])
        self.assertIn("running", status["event_bus"])

    def test_emergency_stop_mutes_and_flushes_input(self):
        async def scenario():
            live = self._live()
            live.ui.muted = False
            live.audio_in_queue = asyncio.Queue()
            for i in range(3):
                live.audio_in_queue.put_nowait(f"chunk{i}")
            live.emergency_stop()                     # must not raise (HTTP)
            first = live.ui.muted, live.audio_in_queue.qsize()
            live.emergency_stop()                     # idempotent
            return first

        muted, remaining = asyncio.run(scenario())
        self.assertTrue(muted)
        self.assertEqual(remaining, 0)

    def test_emergency_stop_emits_a_critical_interrupt(self):
        async def scenario():
            live = self._live()
            live.ui.muted = False
            await live._event_bus.start()
            got, landed = [], asyncio.Event()

            async def capture(e):
                got.append(e)
                landed.set()

            live._event_bus.subscribe_all(capture)
            live.emergency_stop()
            await asyncio.wait_for(landed.wait(), 2)
            await live._event_bus.stop()
            return got

        got = asyncio.run(scenario())
        self.assertEqual(got[0].event_type, EventType.USER_INTERRUPT)
        self.assertEqual(got[0].priority, Priority.CRITICAL)
        self.assertEqual(got[0].data["kind"], "emergency_stop")


class TestBusDispatchAndBridge(unittest.TestCase):
    """Real dispatch emissions + the bus→dashboard bridge."""

    def _live(self, actions=None, dashboard=None):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live.ui = _FakeUI()
        live.speak = lambda *a, **k: None
        live.speak_error = lambda *a, **k: None
        live._action_registry = actions or _FakeActions()
        live._plugin_registry = SimpleNamespace(has=lambda n: False,
                                                scheduling=lambda n: None)
        live._dashboard = dashboard
        live._event_bus = EventBus()
        return live

    def _captured(self, live):
        """Run dispatch with a capture subscriber; return the events."""
        async def scenario():
            await live._event_bus.start()
            got, done = [], asyncio.Event()
            seen = {"n": 0}

            async def capture(e):
                got.append(e)
                seen["n"] += 1
                if seen["n"] >= 1:
                    done.set()

            live._event_bus.subscribe_all(capture)
            resp = await live._dispatch_tool(
                SimpleNamespace(id="fc-1", name="code_helper", args={}),
                ToolPolicy(verification="none", timeout=10))
            await asyncio.wait_for(done.wait(), 2)
            await live._event_bus.stop()
            return resp, got

        return asyncio.run(scenario())

    def test_successful_dispatch_emits_tool_result(self):
        live = self._live(_FakeActions(result="Output text"))
        resp, got = self._captured(live)
        kinds = [(e.event_type, e.source) for e in got]
        self.assertIn((EventType.TOOL_RESULT, "code_helper"), kinds)
        self.assertEqual(resp.response["result"], "Output text")

    def test_failed_dispatch_emits_tool_error(self):
        live = self._live(_FakeActions(boom=RuntimeError("engine down")))
        resp, got = self._captured(live)
        errs = [e for e in got if e.event_type == EventType.TOOL_ERROR]
        self.assertTrue(errs)
        self.assertEqual(errs[0].source, "code_helper")
        self.assertFalse(errs[0].data["ok"])
        self.assertIn("engine down", resp.response["result"])

    def test_bridge_feeds_ring_and_live_stream(self):
        class _Dash:
            def __init__(self):
                self.ring = []
                self.sent = []

            def push_event(self, payload):
                self.ring.append(payload)

            async def broadcast(self, msg, *, history=True):
                self.sent.append((msg, history))

        async def scenario():
            live = self._live()
            dash = _Dash()
            live._dashboard = dash
            await live._event_bus.start()
            live._event_bus.subscribe_all(live._bridge_event)
            live.emit_event(EventType.SYSTEM_STARTUP, source="main",
                            data={"boot": 1})
            for _ in range(40):                       # bridge is async
                if dash.ring:
                    break
                await asyncio.sleep(0.05)
            await live._event_bus.stop()
            return dash

        dash = asyncio.run(scenario())
        self.assertEqual(dash.ring[0]["event_type"], "system_startup")
        msg, history = dash.sent[0]
        self.assertEqual(msg["type"], "event")
        self.assertEqual(msg["event"]["source"], "main")
        self.assertFalse(history)                     # never evicts chat replay

    def test_bridge_without_dashboard_is_silent(self):
        async def scenario():
            live = self._live(dashboard=None)
            await live._event_bus.start()
            live._event_bus.subscribe_all(live._bridge_event)
            live.emit_event(EventType.HEALTH_CHECK, source="t")
            await asyncio.sleep(0.2)                  # must not raise
            await live._event_bus.stop()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
