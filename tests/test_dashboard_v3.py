"""V3 dashboard — shell (Phase 2), core integration (Phase 3), activity +
agent/task panels (Phase 4). DASHBOARD_V3_DESIGN phase map.

Every assertion reads real widgets or real core objects. The only test
doubles are a trivial fake automation store and dict-shaped feed rows —
the production panels read the same real objects via the existing bind
seams (`_activity_feed`, `_automation_store`). No layout geometry is
hardcoded beyond the documented nav-rail widths.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from core.activity import ActivityFeed
from ui import (
    APP_PROTOCOL,
    APP_VERSION,
    HudCanvas,
    MainWindow,
    _FeedPanel,
    _NAV_W,
    _NAV_W_SM,
    _TasksPanel,
    C,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class TestV3Shell(unittest.TestCase):
    """Phase 2 — the shell exists, branded, and navigates only to real
    destinations."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.w = MainWindow("face.png")
        cls.w.hide()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.w._metric_tmr.stop()
            cls.w._tmr.stop()
        except Exception:
            pass
        cls.w.deleteLater()
        cls.app.processEvents()

    def test_branding_constants(self):
        self.assertEqual(APP_VERSION, "AETHER-I")
        self.assertEqual(APP_PROTOCOL, "IV")
        self.assertIn("AETHER-I", self.w.windowTitle())

    def test_center_has_two_pages(self):
        self.assertEqual(self.w._pages.count(), 2)
        self.assertIs(self.w._pages.widget(0), self.w._center_split)

    def test_nav_entries_map_to_real_destinations(self):
        keys = set(self.w._nav_btns)
        self.assertEqual(keys, {
            "command", "system", "automations",
            "conversations", "activity", "settings",
        })
        self.assertEqual(self.w._nav_btns["command"]._nav_target, 0)
        self.assertEqual(self.w._nav_btns["system"]._nav_target, 1)
        self.assertTrue(callable(getattr(
            self.w, self.w._nav_btns["automations"]._nav_target, None)))
        self.assertTrue(callable(getattr(
            self.w, self.w._nav_btns["conversations"]._nav_target, None)))
        self.assertTrue(callable(getattr(
            self.w, self.w._nav_btns["activity"]._nav_target, None)))
        self.assertTrue(callable(getattr(
            self.w, self.w._nav_btns["settings"]._nav_target, None)))

    def test_nav_switches_page_and_selection(self):
        self.w._on_nav(self.w._nav_btns["system"])
        self.assertEqual(self.w._pages.currentIndex(), 1)
        self.assertTrue(self.w._nav_btns["system"].isChecked())
        self.assertFalse(self.w._nav_btns["command"].isChecked())
        # overlay launcher must not hijack the page selection
        before = self.w._pages.currentIndex()
        self.w._on_nav(self.w._nav_btns["activity"])
        self.assertEqual(self.w._pages.currentIndex(), before)
        self.w._on_nav(self.w._nav_btns["command"])
        self.assertEqual(self.w._pages.currentIndex(), 0)

    def test_collapse_roundtrip(self):
        self.w._set_nav_collapsed(True)
        self.assertEqual(self.w._left_panel.width(), _NAV_W_SM)
        self.w._set_nav_collapsed(False)
        self.assertEqual(self.w._left_panel.width(), _NAV_W)

    def test_state_chip_tracks_apply_state(self):
        # offscreen-hidden widgets: use isHidden() (setVisible(True) is
        # recorded, isVisible() stays False while ancestors are hidden)
        self.w._apply_state("EXECUTING")
        self.assertIn("EXECUTING", self.w._state_chip.text())
        self.assertEqual(self.w.hud.state, "EXECUTING")
        self.w._apply_state("WAITING_CONFIRMATION")
        self.assertIn("WAITING_CONFIRMATION", self.w._state_chip.text())
        self.assertFalse(self.w._notice_dot.isHidden())
        self.w._apply_state("LISTENING")
        self.assertIn("LISTENING", self.w._state_chip.text())
        self.assertTrue(self.w._notice_dot.isHidden())

    def test_legacy_alias_normalized_at_ui_seam(self):
        self.w._apply_state("PROCESSING")
        self.assertIn("EXECUTING", self.w._state_chip.text())
        self.assertEqual(self.w.hud.state, "EXECUTING")

    def test_sys_page_widgets_are_the_same_seam(self):
        self.w._pages.setCurrentIndex(1)
        for attr in ("_bar_cpu", "_bar_mem", "_bar_net", "_bar_gpu",
                     "_bar_tmp", "_uptime_lbl", "_proc_lbl", "_prov_lbls"):
            self.assertTrue(hasattr(self.w, attr), attr)
        self.assertEqual(set(self.w._prov_lbls), {"gateway", "ladders", "live"})

    def test_right_rail_has_feed_and_tasks_panels(self):
        self.assertEqual(self.w._tabs.count(), 2)
        self.assertIs(self.w._tabs.widget(0), self.w._feed_panel)
        self.assertIs(self.w._tabs.widget(1), self.w._log)
        self.assertIsInstance(self.w._tasks_panel, _TasksPanel)


class TestV3CoreIntegration(unittest.TestCase):
    """Phase 3 — the neural core is page 0, identity unchanged, and the V2
    automation band seam is intact."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.w = MainWindow("face.png")
        cls.w.hide()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.w._metric_tmr.stop()
            cls.w._tmr.stop()
        except Exception:
            pass
        cls.w.deleteLater()
        cls.app.processEvents()

    def test_hud_is_page_zero_centerpiece(self):
        page0 = self.w._pages.widget(0)
        self.assertIs(page0, self.w._center_split)
        self.assertIs(self.w._hud_cam_stack.widget(0), self.w.hud)
        self.assertIs(self.w._center_split.widget(0), self.w._hud_cam_stack)
        self.assertIsInstance(self.w.hud, HudCanvas)

    def test_hud_state_follows_apply_state(self):
        for state in ("EXECUTING", "VERIFYING", "LISTENING", "ERROR"):
            self.w._apply_state(state)
            self.assertEqual(self.w.hud.state, state)

    def test_auto_band_signal_connected(self):
        # V2 seam: _auto_sig must reach the HUD handler (queued cross-thread)
        self.assertTrue(hasattr(self.w, "_auto_sig"))
        self.assertTrue(hasattr(self.w.hud, "on_automation_event"))
        # emitting is thread-safe by design; handler must not raise
        self.w._auto_sig.emit("automation_run_started", {"name": "x"}, "r1")
        self.app.processEvents()

    def test_apply_state_is_single_sink_for_tasks_panel(self):
        self.w._apply_state("EXECUTING")
        self.assertIn("EXECUTING", self.w._tasks_panel._agent_lbl.text())


class _FakeStore:
    """Minimal AutomationStore stand-in: same all_runs() shape."""

    def __init__(self, runs=None):
        self._runs = runs or []

    def all_runs(self, limit=100):
        return self._runs[:max(1, int(limit))]


class TestV3FeedPanel(unittest.TestCase):
    """Phase 4 — the right-rail feed drains the real ring with its own
    cursor and never consumes the overlay's rows."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def setUp(self):
        self.feed = ActivityFeed()
        self.panel = _FeedPanel(win=None)
        self.panel._feed = self.feed

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()

    def test_pull_adds_labels_and_advances_cursor(self):
        self.feed.append({"event_type": "tool_result", "source": "open_app",
                          "data": {"ok": True, "result": "opened"},
                          "timestamp": 1.0, "priority": 20})
        self.panel._pull()
        self.assertEqual(self.panel._cursor, 1)
        self.assertEqual(self.panel._rows.count() - 1, 1)  # minus stretch
        self.assertTrue(self.panel._empty.isHidden())

    def test_cursor_is_per_consumer(self):
        self.feed.append({"event_type": "system_startup", "source": "jarvis",
                          "data": {}, "timestamp": 1.0, "priority": 20})
        self.panel._pull()          # panel takes the row…
        overlay_rows = self.feed.drain_since(0)   # …overlay still sees it
        self.assertEqual(len(overlay_rows), 1)
        # second pull yields nothing new
        self.panel._pull()
        self.assertEqual(self.panel._rows.count() - 1, 1)

    def test_error_rows_are_critical(self):
        self.feed.append({"event_type": "tool_error", "source": "shell",
                          "data": {"ok": False}, "timestamp": 1.0,
                          "priority": 5})
        self.panel._pull()
        w = self.panel._rows.itemAt(0).widget()
        self.assertIn(C.RED.lower(), w.styleSheet().lower())

    def test_empty_state_visible_when_no_rows(self):
        self.panel._pull()
        self.assertFalse(self.panel._empty.isHidden())
        self.assertIn("No activity yet", self.panel._empty.text())

    def test_rows_bounded(self):
        for i in range(_FeedPanel._MAX_ROWS + 15):
            self.feed.append({"event_type": "custom", "source": "s",
                              "data": {"kind": "note", "n": i},
                              "timestamp": float(i), "priority": 20})
        self.panel._pull()
        self.assertLessEqual(self.panel._rows.count() - 1, _FeedPanel._MAX_ROWS)

    def test_late_bind_via_window_attr(self):
        win = type("W", (), {})()
        win._activity_feed = self.feed
        panel = _FeedPanel(win=win)
        self.feed.append({"event_type": "system_startup", "source": "jarvis",
                          "data": {}, "timestamp": 1.0, "priority": 20})
        panel._pull()
        self.assertIs(panel._feed, self.feed)
        self.assertEqual(panel._cursor, 1)
        panel.deleteLater()


class TestV3TasksPanel(unittest.TestCase):
    """Phase 4 — agent row states the real runtime state; task rows are
    real automation runs only; empty store shows the empty state."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def setUp(self):
        self.win = type("W", (), {})()
        self.panel = _TasksPanel(win=self.win)

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()

    def _task_labels(self):
        return [self.panel._task_rows.itemAt(i).widget()
                for i in range(self.panel._task_rows.count())]

    def test_empty_store_shows_empty_state(self):
        self.win._automation_store = _FakeStore([])
        self.panel._refresh()
        self.assertFalse(self.panel._empty.isHidden())
        self.assertEqual(self.panel._task_rows.count(), 0)

    def test_runs_rendered_with_real_status(self):
        self.win._automation_store = _FakeStore([
            {"run_id": "wf1__0001", "workflow_id": "wf1", "status": "RUNNING",
             "trigger": "manual", "error": ""},
            {"run_id": "wf1__0002", "workflow_id": "wf1", "status": "COMPLETED",
             "trigger": "schedule", "error": ""},
            {"run_id": "wf2__0001", "workflow_id": "wf2", "status": "FAILED",
             "trigger": "voice", "error": "boom"},
        ])
        self.panel._refresh()
        self.assertTrue(self.panel._empty.isHidden())
        rows = self._task_labels()
        self.assertEqual(len(rows), 3)
        texts = [r.text() for r in rows]
        self.assertIn("RUNNING", texts[0])          # active first
        self.assertIn("COMPLETED", texts[1])
        self.assertIn("FAILED", texts[2])
        self.assertIn("boom", rows[2].toolTip())

    def test_active_before_recent(self):
        self.win._automation_store = _FakeStore([
            {"run_id": "a__1", "workflow_id": "a", "status": "COMPLETED",
             "trigger": "manual", "error": ""},
            {"run_id": "a__2", "workflow_id": "a", "status": "WAITING_APPROVAL",
             "trigger": "manual", "error": ""},
        ])
        self.panel._refresh()
        texts = [r.text() for r in self._task_labels()]
        self.assertIn("WAITING_APPROVAL", texts[0])

    def test_set_state_updates_agent_row(self):
        self.panel.set_state("EXECUTING")
        self.assertIn("EXECUTING", self.panel._agent_lbl.text())
        self.assertIn(C.ACC2, self.panel._agent_lbl.styleSheet())
        # honest fallback: state word always present
        self.panel.set_state("RECOVERING")
        self.assertIn("RECOVERING", self.panel._agent_lbl.text())

    def test_broken_store_fails_soft(self):
        class _Boom:
            def all_runs(self, limit=100):
                raise RuntimeError("disk gone")
        self.win._automation_store = _Boom()
        self.panel._refresh()
        self.assertFalse(self.panel._empty.isHidden())

    def test_no_store_is_empty_not_zero(self):
        self.panel._refresh()   # win has no _automation_store attr
        self.assertFalse(self.panel._empty.isHidden())


if __name__ == "__main__":
    unittest.main()
