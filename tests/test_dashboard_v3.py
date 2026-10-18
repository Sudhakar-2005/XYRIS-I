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

    def test_center_has_three_pages(self):
        self.assertEqual(self.w._pages.count(), 3)
        self.assertIs(self.w._pages.widget(0), self.w._center_split)

    def test_nav_entries_map_to_real_destinations(self):
        keys = set(self.w._nav_btns)
        self.assertEqual(keys, {
            "command", "system", "memory", "automations",
            "conversations", "activity", "settings",
        })
        self.assertEqual(self.w._nav_btns["command"]._nav_target, 0)
        self.assertEqual(self.w._nav_btns["system"]._nav_target, 1)
        self.assertEqual(self.w._nav_btns["memory"]._nav_target, 2)
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


class TestV3MemoryPage(unittest.TestCase):
    """Phase 5 — page 2 shows real get_memory_stats() counts; the inspector
    is the existing MemoryOverlay (no second entry list)."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.w = MainWindow("face.png")
        cls.w.hide()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.w._metric_tmr.stop()
        except Exception:
            pass
        cls.w.deleteLater()
        cls.app.processEvents()

    def test_memory_is_page_two(self):
        self.assertEqual(self.w._pages.count(), 3)
        self.assertEqual(self.w._nav_btns["memory"]._nav_target, 2)
        self.w._on_nav(self.w._nav_btns["memory"])
        self.assertEqual(self.w._pages.currentIndex(), 2)
        self.assertTrue(self.w._nav_btns["memory"].isChecked())
        self.assertFalse(self.w._nav_btns["command"].isChecked())
        self.w._on_nav(self.w._nav_btns["command"])

    def test_stats_match_real_memory_manager(self):
        from memory.memory_manager import get_memory_stats
        self.w._refresh_mem_stats()
        real = get_memory_stats()
        self.assertIn(f"ENTRIES  {int(real['total'])}",
                      self.w._mem_lbls["total"].text())
        self.assertIn(f"SESSIONS  {int(real['sessions'])}",
                      self.w._mem_lbls["disk"].text())

    def test_empty_memory_is_explicit(self):
        # simulate the empty store honestly: total 0 → explicit line,
        # never a fake count
        real_total = None
        from memory.memory_manager import get_memory_stats
        real_total = int(get_memory_stats()["total"])
        if real_total == 0:
            self.assertIn("No memories stored yet",
                          self.w._mem_lbls["categories"].text())
        else:
            self.assertNotIn("No memories stored yet",
                             self.w._mem_lbls["categories"].text())

    def test_inspector_opens_existing_overlay(self):
        self.w._open_memory_panel()
        self.assertTrue(hasattr(self.w, "_memory_overlay"))
        self.w._memory_overlay.hide()
        self.w._memory_overlay.deleteLater()
        self.app.processEvents()


class TestV3ProviderCard(unittest.TestCase):
    """Phase 6 — the MODEL/PROVIDER card reflects real local state only:
    config presence, MODEL_MANAGER counters, LIVE_MODEL name. No network
    probe, no credential, unavailable != 0."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.w = MainWindow("face.png")
        cls.w.hide()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.w._metric_tmr.stop()
        except Exception:
            pass
        cls.w.deleteLater()
        cls.app.processEvents()

    def test_card_reflects_real_config_state(self):
        from memory.config_manager import is_configured
        self.w._update_provider_card()
        text = self.w._prov_lbls["gateway"].text()
        if is_configured():
            self.assertIn("configured", text.lower())
        else:
            self.assertIn("NOT CONFIGURED", text)

    def test_ladders_from_real_snapshot(self):
        from core.model_manager import MODEL_MANAGER
        self.w._update_provider_card()
        text = self.w._prov_lbls["ladders"].text()
        snap = MODEL_MANAGER.snapshot() if MODEL_MANAGER else {}
        if not snap:
            self.assertIn("no model calls recorded", text)
        else:
            short = str(next(iter(snap))).rsplit("/", 1)[-1][:20]
            self.assertIn(short, text)

    def test_live_model_is_real_constant(self):
        from core.model_manager import LIVE_MODEL
        self.w._update_provider_card()
        self.assertIn(LIVE_MODEL.rsplit("/", 1)[-1],
                      self.w._prov_lbls["live"].text())

    def test_no_credential_in_card(self):
        self.w._update_provider_card()
        for lbl in self.w._prov_lbls.values():
            low = lbl.text().lower()
            self.assertNotIn("api_key", low)
            self.assertNotIn("apikey", low)
            self.assertNotIn("aiza", low)   # Gemini key prefix


class TestV3AutomationCenter(unittest.TestCase):
    """Phase 7 — nav AUTOMATIONS opens the existing overlay; the header
    strip reflects a real engine.status_summary() when bound, and stays
    hidden (not zeros) when no engine is bound."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.w = MainWindow("face.png")
        cls.w.hide()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.w._metric_tmr.stop()
        except Exception:
            pass
        cls.w.deleteLater()
        cls.app.processEvents()

    def tearDown(self):
        ov = getattr(self.w, "_automation_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self.w._automation_overlay = None
        self.app.processEvents()

    def test_nav_opens_existing_overlay(self):
        self.w._on_nav(self.w._nav_btns["automations"])
        self.assertTrue(hasattr(self.w, "_automation_overlay"))
        self.assertIsNotNone(self.w._automation_overlay)

    def test_engine_strip_hidden_without_engine(self):
        self.w._automation_engine = None
        self.w._on_nav(self.w._nav_btns["automations"])
        ov = self.w._automation_overlay
        ov._update_engine_strip()
        self.assertTrue(ov._engine_lbl.isHidden())   # absent, not zeros
        self.assertEqual(ov._engine_lbl.text(), "")

    def test_engine_strip_shows_real_summary(self):
        class _Eng:
            def status_summary(self):
                return {"total": 7, "active": 2, "running": 1,
                        "by_status": {}}
        self.w._automation_engine = _Eng()
        self.w._on_nav(self.w._nav_btns["automations"])
        ov = self.w._automation_overlay
        ov._update_engine_strip()
        self.assertFalse(ov._engine_lbl.isHidden())
        txt = ov._engine_lbl.text()
        self.assertIn("7 saved", txt)
        self.assertIn("2 active", txt)
        self.assertIn("1 running", txt)

    def test_engine_strip_fails_soft(self):
        class _Boom:
            def status_summary(self):
                raise RuntimeError("engine gone")
        self.w._automation_engine = _Boom()
        self.w._on_nav(self.w._nav_btns["automations"])
        ov = self.w._automation_overlay
        ov._update_engine_strip()
        self.assertEqual(ov._engine_lbl.text(), "")

    def test_runner_still_binds_through_same_seam(self):
        self.w._automation_runner = lambda wf: None
        self.w._on_nav(self.w._nav_btns["automations"])
        self.assertIsNotNone(self.w._automation_overlay._runner)


class TestV3VoiceSurface(unittest.TestCase):
    """Phase 8 — §J: interrupt text bound to the real speaking flag, mic
    text bound to the real mute flag (state word always present — never
    colour alone), both driven from the single _apply_state sink."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.w = MainWindow("face.png")
        cls.w.hide()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.w._metric_tmr.stop()
        except Exception:
            pass
        cls.w.deleteLater()
        cls.app.processEvents()

    def test_interrupt_text_follows_speaking_flag(self):
        self.w._apply_state("SPEAKING")
        self.assertIn("SPEAKING", self.w._interrupt_btn.text())
        self.w._apply_state("LISTENING")
        self.assertIn("IDLE", self.w._interrupt_btn.text())
        self.w._apply_state("EXECUTING")
        self.assertIn("IDLE", self.w._interrupt_btn.text())

    def test_interrupt_click_still_live_when_idle(self):
        calls = []
        self.w.on_interrupt = lambda: calls.append(1)
        self.w._apply_state("IDLE")
        self.w._do_interrupt()
        self.assertEqual(len(calls), 1)   # flush stays valid when idle

    def test_mute_toggle_updates_text_and_state_word(self):
        self.w._toggle_mute()
        self.assertTrue(self.w._muted)
        self.assertIn("MUTED", self.w._mute_btn.text())
        self.assertIn("MUTED", self.w._state_chip.text())
        self.assertEqual(self.w.hud.state, "MUTED")
        self.w._toggle_mute()
        self.assertFalse(self.w._muted)
        self.assertIn("ACTIVE", self.w._mute_btn.text())
        self.assertIn("LISTENING", self.w._state_chip.text())

    def test_mute_property_setter_syncs_style(self):
        # JarvisLive's emergency-stop path goes through the JarvisUI
        # property setter (ui.muted = True) — test the real descriptor.
        from ui import JarvisUI
        j = JarvisUI.__new__(JarvisUI)
        j._win = self.w
        self.w._muted = False
        j.muted = True
        self.assertTrue(self.w._muted)
        self.assertIn("MUTED", self.w._mute_btn.text())
        self.assertIn("MUTED", self.w._state_chip.text())
        j.muted = False
        self.assertIn("ACTIVE", self.w._mute_btn.text())


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
