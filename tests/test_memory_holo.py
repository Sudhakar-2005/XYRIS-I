"""
Living Memory Brain — Stage 1 tests: adapter mapping + loopback server.

Isolation first: both stores (long_term.json and memory/sessions/) are swapped
to per-test temp dirs, so a green run can never touch the real memory on disk.

Contracts under test (docs/living-memory-brain/STAGE_A_AUDIT.md §G):
  * every field shown comes from the store — nothing invented;
  * empty categories render as honest "0 entries" orbs;
  * caps are stated in labels ("1 of 2 entries"), never a silent slice;
  * huge transcripts state their overflow ("+N more turns") and stay in bounds;
  * POST /api/state records board events to the feed and NEVER mutates memory;
  * the server is loopback, fail-soft, and serves the derived page no-store.
"""
import json
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest import mock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory import memory_manager as mm          # noqa: E402
from memory_holo import adapter, server          # noqa: E402


class _MemoryIsolated(unittest.TestCase):
    """Swap both memory stores to a throwaway dir for each test."""

    def setUp(self):
        self._orig_path = mm.MEMORY_PATH
        self._orig_sess = mm._SESSIONS_DIR
        self._tmp = Path(tempfile.mkdtemp(prefix="xyris_memory_holo_"))
        mm.MEMORY_PATH = self._tmp / "long_term.json"
        mm._SESSIONS_DIR = self._tmp / "sessions"
        mm._SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        mm.MEMORY_PATH = self._orig_path
        mm._SESSIONS_DIR = self._orig_sess
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_session(self, name, **overrides):
        entry = {"date": "2026-10-01", "time": "10:00",
                 "summary": "A session.", "language": "en",
                 "turn_count": 2, "turns": ["hi", "hello"]}
        entry.update(overrides)
        path = mm._SESSIONS_DIR / name
        path.write_text(json.dumps(entry), encoding="utf-8")
        return path


class TestAdapterTree(_MemoryIsolated):

    def test_empty_store_seven_honest_orbs(self):
        tree = adapter.build_tree()
        self.assertEqual([f["name"] for f in tree],
                         ["IDENTITY", "PREFERENCES", "PROJECTS",
                          "RELATIONSHIPS", "WISHES", "NOTES", "SESSIONS"])
        for folder in tree:
            self.assertEqual(folder["kind"], "folder")
            self.assertEqual(set(folder), {"kind", "name", "label", "files"})
        for folder in tree[:-1]:
            self.assertEqual(folder["files"], [])
            self.assertEqual(folder["label"], "0 entries")
        self.assertEqual(tree[-1]["files"], [])
        self.assertEqual(tree[-1]["label"], "0 sessions")

    def test_real_entry_roundtrip_no_invention(self):
        mm.remember("project_demo", "Project demo is on Friday",
                    category="projects")
        tree = adapter.build_tree()
        projects = next(f for f in tree if f["name"] == "PROJECTS")
        self.assertEqual(projects["label"], "1 entry")
        card = projects["files"][0]
        self.assertEqual(set(card), {"name", "title", "body", "full",
                                     "updated"})
        self.assertEqual(card["name"], "project_demo")
        self.assertEqual(card["title"], "project demo")
        self.assertEqual(card["body"], "Project demo is on Friday")
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(card["updated"], today)          # store granularity
        self.assertIn("Project demo is on Friday", card["full"])
        self.assertIn(f"projects · updated {today}", card["full"])
        notes = next(f for f in tree if f["name"] == "NOTES")
        self.assertEqual(notes["files"], [])              # honest emptiness

    def test_file_cap_is_stated_not_silent(self):
        mm.remember("note_a", "first", category="notes")
        mm.remember("note_b", "second", category="notes")
        capped = adapter.build_tree(max_files=1)
        notes = next(f for f in capped if f["name"] == "NOTES")
        self.assertEqual(len(notes["files"]), 1)
        self.assertEqual(notes["label"], "1 of 2 entries")
        full = adapter.build_tree()
        notes = next(f for f in full if f["name"] == "NOTES")
        self.assertEqual(len(notes["files"]), 2)
        self.assertEqual(notes["label"], "2 entries")

    def test_sessions_orb_real_fields(self):
        self._write_session("2026-10-01_10-00-00_0001.json",
                            summary="Talked about the demo.")
        self._write_session("2026-10-02_11-00-00_0002.json",
                            date="2026-10-02", time="11:00",
                            summary="", turn_count=3,
                            turns=["alpha", "beta", "gamma"])
        tree = adapter.build_tree()
        sess = tree[-1]
        self.assertEqual(sess["name"], "SESSIONS")
        self.assertEqual(sess["label"], "2 sessions")
        self.assertEqual(len(sess["files"]), 2)
        newest = sess["files"][0]                        # newest first
        self.assertEqual(newest["updated"], "2026-10-02")
        self.assertEqual(newest["title"], "2026-10-02 11:00")
        self.assertEqual(newest["body"],
                         "(no summary saved for this session)")
        self.assertIn("3 turns", newest["full"])
        self.assertIn("alpha\nbeta\ngamma", newest["full"])
        older = sess["files"][1]
        self.assertEqual(older["body"], "Talked about the demo.")
        self.assertIn("Talked about the demo.", older["full"])

    def test_sessions_overflow_is_stated_and_bounded(self):
        turns = [f"turn number {i} with some words" for i in range(400)]
        self._write_session("2026-10-03_09-00-00_0003.json",
                            summary="Long one.", turn_count=400, turns=turns)
        card = adapter.build_tree()[-1]["files"][0]
        self.assertLessEqual(len(card["full"]), adapter.FULL_MAX)
        self.assertRegex(card["full"], r"… \(\+\d+ more turns\)$")

    def test_sessions_cap_stated(self):
        self._write_session("2026-10-01_10-00-00_0001.json")
        self._write_session("2026-10-02_11-00-00_0002.json")
        sess = adapter.build_tree(max_sessions=1)[-1]
        self.assertEqual(sess["label"], "1 of 2 on disk")
        self.assertEqual(len(sess["files"]), 1)

    def test_tree_is_deterministic_and_read_only(self):
        mm.remember("city", "Istanbul", category="identity")
        first = adapter.build_tree()
        second = adapter.build_tree()
        self.assertEqual(first, second)
        on_disk = json.loads(mm.MEMORY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["identity"]["city"]["value"], "Istanbul")


class TestAdapterStats(_MemoryIsolated):

    def test_stats_real_counts(self):
        st = adapter.build_stats()
        self.assertEqual(st["total"], 0)
        self.assertEqual(st["transcripts"], 0)
        self.assertIn("by_category", st)
        mm.remember("city", "Istanbul", category="identity")
        self._write_session("2026-10-01_10-00-00_0001.json")
        st = adapter.build_stats()
        self.assertEqual(st["total"], 1)
        self.assertEqual(st["by_category"]["identity"], 1)
        self.assertEqual(st["transcripts"], 1)


class TestServer(_MemoryIsolated):

    @classmethod
    def setUpClass(cls):
        cls.srv = server.create_server(port=0)           # OS-assigned port
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever,
                                      daemon=True)
        cls.thread.start()
        # no system-proxy interference for loopback requests
        cls.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        server.set_activity_feed(None)
        server.stop(cls.srv)
        cls.thread.join(timeout=5)

    def setUp(self):
        super().setUp()
        server._CACHE.clear()          # per-test store = per-test cache
        self._hands0, self._hands_at0 = server._hands, server._hands_at
        server._hands = {"hands": [], "vw": 0, "vh": 0}
        server._hands_at = 0.0

    def tearDown(self):
        server._hands, server._hands_at = self._hands0, self._hands_at0
        super().tearDown()

    def _req(self, path, data=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(
            url, data=data,
            method="POST" if data is not None else "GET")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=5) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_page_served_no_store_with_attribution(self):
        code, body, headers = self._req("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        text = body.decode("utf-8")
        self.assertIn("MEMORY VIEW", text)
        self.assertIn("MIT License", text)
        self.assertIn("READ-ONLY VIEW", text)

    def test_keep_alive_is_on(self):
        # HTTP/1.1: the page's tree+stats fetches share one connection.
        url = f"http://127.0.0.1:{self.port}/api/stats"
        with self.opener.open(url, timeout=5) as r:
            self.assertEqual(r.version, 11)

    def test_tree_endpoint_matches_adapter(self):
        code, body, _ = self._req("/api/tree")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), adapter.build_tree())

    def test_stats_endpoint(self):
        code, body, _ = self._req("/api/stats")
        self.assertEqual(code, 200)
        st = json.loads(body)
        self.assertEqual(st["total"], adapter.build_stats()["total"])
        self.assertIn("transcripts", st)

    def test_hands_endpoint_serves_engine_frame(self):
        server.publish_hands({"hands": [[{"x": 0.1, "y": 0.2}] * 21],
                              "vw": 640, "vh": 480})
        code, body, _ = self._req("/api/hands")
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(len(d["hands"]), 1)
        self.assertEqual(d["hands"][0][0], {"x": 0.1, "y": 0.2})
        self.assertEqual((d["vw"], d["vh"]), (640, 480))
        self.assertTrue(0 <= d["age_ms"] < 1000)

    def test_hands_endpoint_when_engine_is_off(self):
        code, body, _ = self._req("/api/hands")
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(d["hands"], [])
        self.assertEqual(d["age_ms"], -1)           # honestly gone, not a fake frame

    def test_hands_never_rebuilds_the_tree(self):
        # the 30Hz poll must not touch the fingerprint cache path at all
        with mock.patch.object(adapter, "build_tree",
                               side_effect=AssertionError("hands rebuilt tree")), \
             mock.patch.object(adapter, "build_stats",
                               side_effect=AssertionError("hands rebuilt stats")):
            server.publish_hands({"hands": [], "vw": 0, "vh": 0})
            code, _, _ = self._req("/api/hands")
            self.assertEqual(code, 200)

    def test_unknown_and_traversal_paths_404(self):
        for path in ("/nope", "/../../etc/passwd", "/api/../secret",
                     "/memory_view.html.bak"):
            code, _, _ = self._req(path)
            self.assertEqual(code, 404, path)

    def test_state_post_records_to_feed(self):
        class _Feed:
            def __init__(self):
                self.rows = []

            def append(self, event):
                self.rows.append(event)

        feed = _Feed()
        server.set_activity_feed(feed)
        try:
            before = server.state_event_count()
            payload = json.dumps({"event": "open_folder",
                                  "card": "preferences"}).encode()
            code, body, _ = self._req("/api/state", payload)
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body), {"ok": True})
            self.assertEqual(server.state_event_count(), before + 1)
            self.assertEqual(len(feed.rows), 1)
            row = feed.rows[0]
            self.assertEqual(row["event_type"], "custom")
            self.assertEqual(row["source"], "memory_view")
            self.assertEqual(row["data"]["kind"],
                             "open_folder — preferences")
            self.assertIn("timestamp", row)
        finally:
            server.set_activity_feed(None)

    def test_state_filters_per_gesture_noise(self):
        class _Feed:
            def __init__(self):
                self.rows = []

            def append(self, event):
                self.rows.append(event)

        feed = _Feed()
        server.set_activity_feed(feed)
        try:
            before = server.state_event_count()
            payload = json.dumps({"event": "grab", "card": "city"}).encode()
            code, body, _ = self._req("/api/state", payload)
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body), {"ok": True})   # page ignores
            self.assertEqual(server.state_event_count(), before)
            self.assertEqual(feed.rows, [])
        finally:
            server.set_activity_feed(None)

    def test_state_bad_json_is_tolerated(self):
        code, body, _ = self._req("/api/state", b"not json at all")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"ok": True})

    def test_state_clear_event_never_touches_memory(self):
        mm.remember("keep_me", "still here", category="notes")
        payload = json.dumps({"event": "clear", "card": "all"}).encode()
        code, _, _ = self._req("/api/state", payload)
        self.assertEqual(code, 200)
        store = mm.load_memory()
        self.assertEqual(store["notes"]["keep_me"]["value"], "still here")

    # ── make-it-fast: store-fingerprint response cache ──────────────────────

    def test_warm_tree_hit_never_rebuilds(self):
        calls = []
        orig = adapter.build_tree

        def counted(*a, **k):
            calls.append(1)
            return orig(*a, **k)

        with mock.patch.object(adapter, "build_tree", counted):
            code1, body1, _ = self._req("/api/tree")
            code2, body2, _ = self._req("/api/tree")
        self.assertEqual((code1, code2), (200, 200))
        self.assertEqual(body1, body2)          # identical payload
        self.assertEqual(len(calls), 1)         # second was a cache hit

    def test_warm_stats_hit_never_rebuilds(self):
        calls = []
        orig = adapter.build_stats

        def counted(*a, **k):
            calls.append(1)
            return orig(*a, **k)

        with mock.patch.object(adapter, "build_stats", counted):
            self._req("/api/stats")
            self._req("/api/stats")
        self.assertEqual(len(calls), 1)

    def test_cache_sees_new_writes_immediately(self):
        _, body1, _ = self._req("/api/tree")
        self.assertNotIn("cache_canary", body1.decode())
        mm.remember("cache_canary", "canary", category="notes")
        _, body2, _ = self._req("/api/tree")
        self.assertIn("cache_canary", body2.decode())   # fingerprint changed

    def test_cache_sees_new_sessions_immediately(self):
        _, body1, _ = self._req("/api/tree")
        self.assertNotIn("2099-01-01", body1.decode())
        self._write_session("2099-01-01_00-00-00_0001.json",
                            date="2099-01-01")
        _, body2, _ = self._req("/api/tree")
        self.assertIn("2099-01-01", body2.decode())     # dir mtime changed


class TestFailSoftStart(unittest.TestCase):

    def test_start_returns_none_when_bind_fails(self):
        original = server._Server

        def _boom(*_a, **_k):
            raise OSError("address already in use")

        server._Server = _boom
        try:
            self.assertIsNone(server.start(port=0))       # never raises
        finally:
            server._Server = original

    def test_stop_tolerates_none(self):
        server.stop(None)                                # no-op, no raise


class TestStoreFingerprint(_MemoryIsolated):

    def test_changes_with_the_store(self):
        fp1 = adapter.store_fingerprint()
        mm.remember("city", "Istanbul", category="identity")
        fp2 = adapter.store_fingerprint()
        self.assertNotEqual(fp1, fp2)

    def test_changes_with_new_sessions(self):
        fp1 = adapter.store_fingerprint()
        self._write_session("2099-01-01_00-00-00_0001.json")
        fp2 = adapter.store_fingerprint()
        self.assertNotEqual(fp1, fp2)

    def test_stable_when_the_store_is_quiet(self):
        self.assertEqual(adapter.store_fingerprint(),
                         adapter.store_fingerprint())


# ── Phase 2: entry points ────────────────────────────────────────────────────

class TestOpenInBrowser(unittest.TestCase):
    """One port/URL decision shared by the button and the voice action."""

    def setUp(self):
        self._orig_running = server._running
        server._running = None

    def tearDown(self):
        server._running = self._orig_running

    def test_starts_on_demand_and_opens_once(self):
        fake = types.SimpleNamespace(server_address=("127.0.0.1", 4891))
        with mock.patch.object(server, "start", return_value=fake) as st, \
                mock.patch("webbrowser.open") as wb:
            url = server.open_in_browser()
            again = server.get_url()                 # second call reuses it
        st.assert_called_once_with()                 # one server, not two
        wb.assert_called_once_with("http://127.0.0.1:4891/")
        self.assertEqual(url, "http://127.0.0.1:4891/")
        self.assertEqual(again, url)

    def test_disabled_server_returns_none_without_opening(self):
        with mock.patch.object(server, "start", return_value=None), \
                mock.patch("webbrowser.open") as wb:
            self.assertIsNone(server.open_in_browser())
        wb.assert_not_called()


class TestViewAction(unittest.TestCase):
    """memory_controller action='view' — the voice/CLI entry point."""

    def setUp(self):
        from actions.memory_controller import memory_controller
        self.mc = memory_controller
        self._orig_running = server._running
        server._running = None

    def tearDown(self):
        server._running = self._orig_running

    def test_view_reports_the_url_it_opened(self):
        fake = types.SimpleNamespace(server_address=("127.0.0.1", 4891))
        with mock.patch.object(server, "start", return_value=fake), \
                mock.patch("webbrowser.open") as wb:
            out = self.mc(parameters={"action": "view"})
        wb.assert_called_once_with("http://127.0.0.1:4891/")
        self.assertIn("read-only", out)
        self.assertIn("http://127.0.0.1:4891/", out)

    def test_view_fails_soft_when_server_cannot_start(self):
        with mock.patch.object(server, "start", return_value=None):
            out = self.mc(parameters={"action": "view"})
        self.assertIn("could not start", out)

    def test_view_is_advertised_in_the_tool(self):
        from actions.memory_controller import TOOL
        self.assertIn("age | view", TOOL["parameters"]["properties"]["action"]
                                              ["description"])
        self.assertIn("action='view'", TOOL["description"])


# ── Phase 3: MEMORY_STORED / MEMORY_RETRIEVED ────────────────────────────────

class TestMemoryEventHook(_MemoryIsolated):

    def tearDown(self):
        mm.set_memory_event_hook(None)
        super().tearDown()

    def test_remember_fires_stored_with_metadata_only(self):
        seen = []
        mm.set_memory_event_hook(lambda kind, data: seen.append((kind, data)))
        mm.remember("city", "Istanbul", category="identity")
        self.assertEqual(seen, [("memory_stored",
                                 {"key": "city", "category": "identity"})])
        # the value itself never reaches the observer
        self.assertNotIn("Istanbul", json.dumps(seen))

    def test_search_fires_retrieved_with_hits(self):
        mm.remember("city", "Istanbul", category="identity")
        seen = []
        mm.set_memory_event_hook(lambda kind, data: seen.append((kind, data)))
        mm.search_memory("Istanbul")
        self.assertEqual(seen, [("memory_retrieved", {"hits": 1})])

    def test_search_miss_reports_zero_hits(self):
        seen = []
        mm.set_memory_event_hook(lambda kind, data: seen.append((kind, data)))
        mm.search_memory("a there")
        self.assertEqual(seen, [("memory_retrieved", {"hits": 0})])

    def test_raising_observer_never_breaks_the_store(self):
        def boom(kind, data):
            raise RuntimeError("observer exploded")
        mm.set_memory_event_hook(boom)
        out = mm.remember("safe", "still works", category="notes")
        self.assertIn("Remembered", out)
        store = mm.load_memory()
        self.assertEqual(store["notes"]["safe"]["value"], "still works")

    def test_unbound_hook_is_a_silent_noop(self):
        mm.set_memory_event_hook(None)
        self.assertIn("Remembered",
                      mm.remember("quiet", "no observer", category="notes"))


# ── Wiring: entry points + main bindings (source net, repo style) ────────────

class TestPhase23Wiring(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ui_src = (PROJECT_ROOT / "ui.py").read_text(encoding="utf-8")
        cls.main_src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    def test_memory_page_has_the_universe_button(self):
        self.assertIn("OPEN MEMORY UNIVERSE", self.ui_src)
        self.assertIn("def _open_memory_universe", self.ui_src)
        self.assertIn("open_in_browser()", self.ui_src)

    def test_main_starts_the_view_and_binds_the_feed(self):
        self.assertIn("from memory_holo import server", self.main_src)
        self.assertIn("set_activity_feed(self._activity)", self.main_src)

    def test_main_publishes_memory_events_to_the_bus(self):
        self.assertIn("set_memory_event_hook", self.main_src)
        self.assertIn("EventType.MEMORY_STORED", self.main_src)
        self.assertIn("EventType.MEMORY_RETRIEVED", self.main_src)
        self.assertIn("run_coroutine_threadsafe", self.main_src)


class TestHandsFeed(unittest.TestCase):
    """Phase 4 push side: engine thread → publish_hands → snapshot."""

    def setUp(self):
        self._h, self._t = server._hands, server._hands_at
        server._hands = {"hands": [], "vw": 0, "vh": 0}
        server._hands_at = 0.0

    def tearDown(self):
        server._hands, server._hands_at = self._h, self._t

    @staticmethod
    def _frame(n=2, w=1280, h=720):
        return {"hands": [[{"x": i / 21, "y": 0.5} for i in range(21)]
                          for _ in range(n)],
                "vw": w, "vh": h}

    def test_never_published_is_honest_empty(self):
        snap = server._hands_snapshot()
        self.assertEqual(snap["hands"], [])
        self.assertEqual(snap["age_ms"], -1)

    def test_fresh_frame_round_trips(self):
        server.publish_hands(self._frame())
        snap = server._hands_snapshot()
        self.assertEqual(len(snap["hands"]), 2)
        self.assertEqual(len(snap["hands"][0]), 21)
        self.assertEqual((snap["vw"], snap["vh"]), (1280, 720))
        self.assertTrue(0 <= snap["age_ms"] < 1000)

    def test_stale_frame_serves_empty_keeps_dims(self):
        server.publish_hands(self._frame())
        server._hands_at = time.monotonic() - (server.HANDS_STALE_S + 0.05)
        snap = server._hands_snapshot()
        self.assertEqual(snap["hands"], [])         # stalled engine drops cleanly
        self.assertEqual((snap["vw"], snap["vh"]), (1280, 720))
        self.assertGreater(snap["age_ms"], 1000)

    def test_bounds_are_enforced_at_publish(self):
        over = self._frame(n=3)
        over["hands"][0] = [{"x": "bad", "y": 0}] * 21   # non-numeric → dropped
        over["hands"][2] = [{"x": 0, "y": 0}] * 22       # 3rd hand + 22 pts → dropped
        server.publish_hands(over)
        snap = server._hands_snapshot()
        self.assertEqual(len(snap["hands"]), 1)          # only the valid 2nd survives
        self.assertEqual(len(snap["hands"][0]), 21)

    def test_garbage_resets_to_a_fresh_empty_frame(self):
        server.publish_hands(self._frame())
        server.publish_hands(None)
        snap = server._hands_snapshot()
        self.assertEqual(snap["hands"], [])
        self.assertTrue(0 <= snap["age_ms"] < 1000)  # alive-but-empty ≠ engine-gone

    def test_float_coercion_and_dim_parsing(self):
        server.publish_hands({"hands": [[{"x": 1, "y": 0}] * 21],   # ints → floats
                              "vw": "640", "vh": 480.9})
        snap = server._hands_snapshot()
        self.assertEqual(snap["hands"][0][0], {"x": 1.0, "y": 0.0})
        self.assertEqual((snap["vw"], snap["vh"]), (640, 480))


class TestEngineHandsEmit(unittest.TestCase):
    """core/spatial stays ignorant of memory_holo: landmarks fan out, nothing more."""

    @staticmethod
    def _bare():
        from core.spatial.engine import SpatialInteractionEngine
        # __new__ skips __init__ (camera/mediapipe) — this is about the
        # observer plumbing only.
        eng = SpatialInteractionEngine.__new__(SpatialInteractionEngine)
        eng._on_hands_update = None
        return eng

    def test_no_observer_is_a_silent_noop(self):
        self._bare()._emit_hands([], (480, 640, 3))

    def test_observer_gets_the_same_list_and_frame_shape(self):
        eng = self._bare()
        got = {}
        eng.on_hands_update = lambda hands, shape: got.update(h=hands, s=shape)
        frame = object()
        eng._emit_hands(frame, (480, 640, 3))
        self.assertIs(got["h"], frame)
        self.assertEqual(got["s"], (480, 640, 3))

    def test_raising_observer_cannot_stall_tracking(self):
        eng = self._bare()

        def boom(hands, shape):
            raise RuntimeError("observer died")

        eng.on_hands_update = boom
        eng._emit_hands([], (480, 640, 3))         # must not raise


class TestEngineHandsBridge(unittest.TestCase):
    """main._on_engine_hands: engine tick → /api/hands payload, fail-soft."""

    @staticmethod
    def _live():
        from main import JarvisLive
        return JarvisLive.__new__(JarvisLive)

    @staticmethod
    def _hand(x=0.25, y=0.75, n=21):
        return types.SimpleNamespace(landmarks=[(x, y)] * n)

    def test_landmarks_become_page_shape_with_frame_dims(self):
        live = self._live()
        with mock.patch.object(server, "publish_hands") as pub:
            live._on_engine_hands([self._hand(), self._hand()], (480, 640, 3))
        payload = pub.call_args[0][0]
        self.assertEqual(len(payload["hands"]), 2)
        self.assertEqual(payload["hands"][0][0], {"x": 0.25, "y": 0.75})
        self.assertEqual(len(payload["hands"][0]), 21)
        self.assertEqual((payload["vw"], payload["vh"]), (640, 480))  # shape=(h, w, …)

    def test_short_hand_is_skipped_dims_stay_honest(self):
        live = self._live()
        with mock.patch.object(server, "publish_hands") as pub:
            live._on_engine_hands([self._hand(n=20), self._hand()], (480, 640, 3))
        payload = pub.call_args[0][0]
        self.assertEqual(len(payload["hands"]), 1)       # 20-point hand dropped
        self.assertEqual((payload["vw"], payload["vh"]), (640, 480))

    def test_no_hands_still_publishes_an_empty_live_frame(self):
        live = self._live()
        with mock.patch.object(server, "publish_hands") as pub:
            live._on_engine_hands([], (480, 640, 3))
        payload = pub.call_args[0][0]
        self.assertEqual(payload["hands"], [])
        self.assertEqual((payload["vw"], payload["vh"]), (640, 480))

    def test_broken_frame_never_raises(self):
        live = self._live()
        with mock.patch.object(server, "publish_hands") as pub:
            live._on_engine_hands(None, None)            # must not raise
        pub.assert_not_called()

    def test_landmarkless_hand_publishes_empty_not_garbage(self):
        live = self._live()
        with mock.patch.object(server, "publish_hands") as pub:
            live._on_engine_hands([types.SimpleNamespace()], (480, 640, 3))
        payload = pub.call_args[0][0]
        self.assertEqual(payload["hands"], [])
        self.assertEqual((payload["vw"], payload["vh"]), (640, 480))


class TestPhase4Wiring(unittest.TestCase):
    """Source-level: the bridge is wired end to end and the privacy floor holds."""

    @classmethod
    def setUpClass(cls):
        cls.page = (PROJECT_ROOT / "memory_holo" / "memory_view.html").read_text(encoding="utf-8")
        cls.engine = (PROJECT_ROOT / "core" / "spatial" / "engine.py").read_text(encoding="utf-8")
        cls.main = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    def test_page_polls_hands_and_maps_frame_once(self):
        self.assertIn("/api/hands", self.page)
        self.assertIn("async function pollHands", self.page)
        self.assertIn("updateMap(d.vw, d.vh)", self.page)

    def test_page_camera_floor_still_holds(self):
        self.assertNotIn("getUserMedia", self.page)
        self.assertNotIn("navigator.mediaDevices", self.page)

    def test_engine_emits_after_detect(self):
        self.assertIn("self._emit_hands(hands, frame.shape)", self.engine)
        self.assertIn("def _emit_hands", self.engine)

    def test_main_binds_the_bridge(self):
        self.assertIn("on_hands_update = self._on_engine_hands", self.main)
        self.assertIn("def _on_engine_hands", self.main)
        self.assertIn("publish_hands", self.main)


if __name__ == "__main__":
    unittest.main()
