"""Phase 32 — activity center: §3.15's second consumer, tested.

The audit's V3 line was "expose stream to dashboard + activity center
(Phase 32)": M11 did the dashboard half, this file covers the other one.

  1. ActivityFeed   — bounded ring, monotonic cursor, thread-safe, and it
                      never raises no matter what the bus hands it.
  2. Bridge         — one event reaches BOTH consumers: the feed (always,
                      dashboard or not) and the dashboard (when attached).
  3. ActivityOverlay — the Qt view: backlog on open, live drain on the GUI
                      timer, clear, timer lifecycle, feed-less safety.
  4. Source guards  — the wiring stays where it is: construction in
                      __init__, feed-first bridge, bind in run(), button
                      on the drawer, JarvisUI passthrough.
"""
from __future__ import annotations

import asyncio
import os
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main
from core.activity import ActivityFeed, summarize
from core.event_bus import EventBus, Event, EventType, Priority

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _event(etype=EventType.TOOL_RESULT, source="open_app", data=None,
           priority=Priority.HIGH, ts=None) -> Event:
    return Event(priority=priority, event_type=etype, source=source,
                 data=data if data is not None else {"ok": True,
                                                     "result": "Opened."},
                 timestamp=ts if ts is not None else 1700000000.0)


class TestActivityFeed(unittest.TestCase):
    """Layer 1 — the model ring."""

    def test_append_event_builds_a_row(self):
        feed = ActivityFeed()
        row = feed.append(_event())
        self.assertIsNotNone(row)
        self.assertEqual(row["seq"], 1)
        self.assertEqual(row["type"], "tool_result")
        self.assertEqual(row["source"], "open_app")
        self.assertTrue(row["text"].startswith("open_app ok"))
        self.assertFalse(row["critical"])
        self.assertEqual(len(feed), 1)

    def test_append_accepts_a_plain_dict(self):
        feed = ActivityFeed()
        row = feed.append({"event_type": "system_startup", "source": "main",
                           "data": {}, "timestamp": 1700000000.0,
                           "priority": 20})
        self.assertEqual(row["type"], "system_startup")
        self.assertEqual(row["text"], "Session started")

    def test_append_never_raises_on_junk(self):
        feed = ActivityFeed()
        for junk in (None, 42, "x", object(), {}, {"data": None},
                     {"priority": "??", "timestamp": "??"}):
            # None/object have no to_dict → must be swallowed, not raised
            result = feed.append(junk)
            if isinstance(junk, dict):
                self.assertIsNotNone(result, junk)
        # unusable non-dicts return None instead of exploding
        self.assertIsNone(feed.append(42))
        self.assertIsNone(feed.append(object()))

    def test_ring_is_bounded_and_cursor_stays_monotonic(self):
        feed = ActivityFeed(maxlen=10)
        for i in range(25):
            feed.append(_event(data={"ok": True, "result": str(i)}))
        self.assertEqual(len(feed), 10)
        entries = feed.entries()
        self.assertEqual([r["seq"] for r in entries], list(range(16, 26)))
        # older rows are gone for a cursor parked at 5…
        self.assertEqual(len(feed.drain_since(5)), 10)
        # …and a cursor at 25 sees nothing new
        self.assertEqual(feed.drain_since(25), [])

    def test_clear_keeps_seq_so_ui_cursors_stay_valid(self):
        feed = ActivityFeed()
        feed.append(_event())
        feed.append(_event())
        cursor = feed.entries()[-1]["seq"]
        feed.clear()
        self.assertEqual(len(feed), 0)
        row = feed.append(_event(data={"ok": True, "result": "post-clear"}))
        self.assertGreater(row["seq"], cursor)
        fresh = feed.drain_since(cursor)
        self.assertEqual(len(fresh), 1)
        self.assertIn("post-clear", fresh[0]["text"])

    def test_concurrent_appends_are_safe(self):
        feed = ActivityFeed(maxlen=500)

        def worker(n):
            for i in range(60):
                feed.append(_event(source=f"t{n}",
                                   data={"ok": True, "result": str(i)}))

        threads = [threading.Thread(target=worker, args=(n,))
                   for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(feed), 240)
        seqs = [r["seq"] for r in feed.entries()]
        self.assertEqual(seqs, list(range(1, 241)))


class TestSummaries(unittest.TestCase):
    """Layer 1 — the one-line vocabulary."""

    def test_tool_result_and_error_lines(self):
        ok = summarize("tool_result", "open_app",
                       {"ok": True, "result": "Opened Notepad."})
        self.assertIn("open_app ok", ok)
        self.assertIn("Opened Notepad.", ok)
        bad = summarize("tool_error", "web_search",
                        {"ok": False, "result": "quota exceeded"})
        self.assertIn("web_search FAILED", bad)

    def test_known_kinds_are_humanized(self):
        self.assertIn("Emergency stop",
                      summarize("user_interrupt", "dashboard",
                                {"kind": "emergency_stop"}))
        self.assertIn("Degraded mode engaged",
                      summarize("custom", "degraded",
                                {"kind": "degraded_start"}))
        self.assertIn("Degraded mode ended",
                      summarize("custom", "degraded",
                                {"kind": "degraded_end"}))

    def test_unknown_kind_and_bare_type_fall_back(self):
        self.assertEqual(summarize("custom", "x", {"kind": "new_thing"}),
                         "New thing")
        self.assertEqual(summarize("system_startup", "main", {}),
                         "Session started")
        self.assertTrue(summarize("health_check", "", {}))

    def test_long_results_are_truncated(self):
        line = summarize("tool_result", "dev_agent",
                         {"ok": True, "result": "x" * 500})
        self.assertLessEqual(len(line), 110)

    def test_junk_shapes_do_not_raise(self):
        for etype, src, data in (("tool_result", "t", None),
                                 ("tool_result", None, {}),
                                 ("", "", "not-a-dict")):
            self.assertIsInstance(summarize(etype, src, data), str)


class _FakeDash:
    def __init__(self):
        self.pushed = []
        self.broadcasted = []

    def push_event(self, payload):
        self.pushed.append(payload)

    async def broadcast(self, msg, *, history=True):
        self.broadcasted.append((msg, history))


def _live(dash=None) -> main.JarvisLive:
    live = main.JarvisLive.__new__(main.JarvisLive)
    live._event_bus = EventBus()
    live._activity = ActivityFeed()
    live._dashboard = dash
    return live


class TestBridgeFeedsBothSurfaces(unittest.TestCase):
    """Layer 2 — one subscriber, two consumers (§3.15 + Phase 32)."""

    @staticmethod
    async def _publish_and_wait(live, etype=EventType.TOOL_RESULT, **kw):
        await live._event_bus.start()
        live._event_bus.subscribe_all(live._bridge_event)
        live.emit_event(etype, **kw)
        for _ in range(60):
            if len(live._activity):
                break
            await asyncio.sleep(0.05)
        await live._event_bus.stop()

    def test_feed_fills_with_no_dashboard_attached(self):
        async def scenario():
            live = _live(dash=None)          # the normal test state
            await self._publish_and_wait(
                live, source="open_app",
                data={"ok": True, "result": "Opened."})
            return live._activity.entries()

        rows = asyncio.run(scenario())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "open_app")

    def test_one_event_reaches_feed_and_dashboard(self):
        async def scenario():
            dash = _FakeDash()
            live = _live(dash=dash)
            await self._publish_and_wait(
                live, source="open_app",
                data={"ok": True, "result": "Opened."})
            return dash, live._activity.entries()

        dash, rows = asyncio.run(scenario())
        self.assertEqual(len(rows), 1)               # Phase 32 half
        self.assertEqual(len(dash.pushed), 1)        # M11 half unchanged
        self.assertEqual(len(dash.broadcasted), 1)
        self.assertFalse(dash.broadcasted[0][1])      # history=False kept

    def test_bridge_survives_a_broken_dashboard(self):
        async def scenario():
            class Broken:
                def push_event(self, payload):
                    raise RuntimeError("dash down")

                async def broadcast(self, msg, *, history=True):
                    raise RuntimeError("ws down")

            live = _live(dash=Broken())
            await self._publish_and_wait(live)
            return live._activity.entries()

        rows = asyncio.run(scenario())
        self.assertEqual(len(rows), 1, "feed must fill even when the "
                                       "dashboard blows up mid-bridge")


class TestActivityOverlay(unittest.TestCase):
    """Layer 3 — the Qt view, offscreen."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _row_count(ov) -> int:
        n = 0
        for i in range(ov._rows.count()):
            item = ov._rows.itemAt(i)
            if item is not None and item.widget() is not None:
                n += 1
        return n

    @staticmethod
    def _feed_with(n) -> ActivityFeed:
        feed = ActivityFeed()
        for i in range(n):
            feed.append(_event(data={"ok": True, "result": f"step {i}"}))
        return feed

    def test_backlog_appears_on_open(self):
        from ui import ActivityOverlay
        ov = ActivityOverlay(feed=self._feed_with(3))
        self.assertEqual(self._row_count(ov), 3)
        self.assertFalse(ov._empty.isVisible())   # hidden: no-parent widget
        ov.deleteLater()

    def test_new_rows_drain_while_open(self):
        from ui import ActivityOverlay
        feed = self._feed_with(2)
        ov = ActivityOverlay(feed=feed)
        feed.append(_event(source="later", data={"ok": True,
                                                 "result": "fresh"}))
        ov._pull()
        self.assertEqual(self._row_count(ov), 3)
        # already-seen rows are not drained twice
        ov._pull()
        self.assertEqual(self._row_count(ov), 3)
        ov.deleteLater()

    def test_clear_empties_view_and_feed_then_new_rows_appear(self):
        from ui import ActivityOverlay
        feed = self._feed_with(4)
        ov = ActivityOverlay(feed=feed)
        ov._on_clear()
        self.assertEqual(self._row_count(ov), 0)
        self.assertEqual(len(feed), 0)
        feed.append(_event(data={"ok": True, "result": "after clear"}))
        ov._pull()
        self.assertEqual(self._row_count(ov), 1)
        ov.deleteLater()

    def test_timer_runs_only_while_shown(self):
        from ui import ActivityOverlay
        ov = ActivityOverlay(feed=self._feed_with(1))
        self.assertFalse(ov._timer.isActive())
        ov.show()
        self.assertTrue(ov._timer.isActive())
        ov.hide()
        self.assertFalse(ov._timer.isActive())
        ov.deleteLater()

    def test_feedless_overlay_is_safe(self):
        from ui import ActivityOverlay
        ov = ActivityOverlay(feed=None)
        ov._pull()                 # no feed → no-op, no crash
        ov._on_clear()
        self.assertEqual(self._row_count(ov), 0)
        ov.deleteLater()

    def test_critical_rows_paint_red_and_results_green(self):
        from ui import ActivityOverlay
        feed = ActivityFeed()
        feed.append(_event(etype=EventType.USER_INTERRUPT,
                           source="dashboard",
                           data={"kind": "emergency_stop"},
                           priority=Priority.CRITICAL))
        feed.append(_event())
        ov = ActivityOverlay(feed=feed)
        texts = [ov._rows.itemAt(i).widget().text()
                 for i in range(ov._rows.count())
                 if ov._rows.itemAt(i).widget() is not None]
        self.assertTrue(any("Emergency stop" in t for t in texts))
        self.assertTrue(any("open_app ok" in t for t in texts))
        ov.deleteLater()


class TestSourceGuards(unittest.TestCase):
    """Layer 4 — the wiring must stay where it is."""

    @classmethod
    def setUpClass(cls):
        cls.main_src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        cls.ui_src = (PROJECT_ROOT / "ui.py").read_text(encoding="utf-8")

    def test_init_constructs_the_feed(self):
        i = self.main_src.index("class JarvisLive")
        j = self.main_src.index("def run(self)")
        init = self.main_src[i:j]
        self.assertIn("ActivityFeed()", init)

    def test_bridge_appends_to_the_feed_before_the_dashboard_guard(self):
        i = self.main_src.index("async def _bridge_event")
        j = self.main_src.index("def _dashboard_status")
        bridge = self.main_src[i:j]
        self.assertLess(bridge.index("feed.append"),
                        bridge.index("if not dash"),
                        "the feed must fill even when no dashboard exists")

    def test_run_binds_the_feed_to_the_ui(self):
        i = self.main_src.index("async def run(self)")
        run = self.main_src[i:i + 8000]
        self.assertIn("bind_activity_feed", run)

    def test_drawer_button_opens_the_panel(self):
        self.assertIn("act_btn.clicked.connect(self._open_activity_panel)",
                      self.ui_src)
        self.assertIn("def _open_activity_panel", self.ui_src)
        self.assertIn("def bind_activity_feed", self.ui_src)

    def test_overlay_drains_through_the_feed_seam(self):
        self.assertIn("drain_since", self.ui_src)
        i = self.ui_src.index("class ActivityOverlay")
        j = self.ui_src.index("class ClipboardPanel")
        overlay = self.ui_src[i:j]
        self.assertIn("QTimer", overlay)
        self.assertIn("self._timer.start()", overlay)
        self.assertIn("self._timer.stop()", overlay)


if __name__ == "__main__":
    unittest.main()
