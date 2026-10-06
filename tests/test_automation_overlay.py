"""Phases 10/11/12 — automations panel, prompt verbs, activity labels."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from core.activity import summarize                          # noqa: E402
from core.automation import Edge, Node, Trigger, Workflow    # noqa: E402


def make_wf(wid="wf_demo", name="Demo"):
    return Workflow(
        id=wid, name=name, description="demo workflow",
        nodes=[Node(id="trg", type="TRIGGER", name="start"),
               Node(id="t1", type="TOOL", name="search",
                    config={"tool": "web_search", "args": {"query": "x"}}),
               Node(id="end", type="END", name="done")],
        edges=[Edge("trg", "t1"), Edge("t1", "end")],
        trigger=Trigger())


def make_loop_wf():
    wf = Workflow(
        id="wf_loop", name="Looper",
        nodes=[Node(id="trg", type="TRIGGER", name="start"),
               Node(id="lp", type="LOOP", name="repeat",
                    config={"items_path": "$.vars.x", "body": ["b1"]}),
               Node(id="b1", type="TOOL", name="inner",
                    config={"tool": "web_search", "args": {"query": "y"}}),
               Node(id="end", type="END", name="done")],
        edges=[Edge("trg", "lp"), Edge("lp", "end")],
        trigger=Trigger())
    return wf


class FakeStore:
    def __init__(self, workflows=None, runs=None):
        self.workflows = {w.id: w for w in (workflows or [])}
        self.runs = {w.id: (runs or {}).get(w.id, []) for w in
                     (workflows or [])}

    def list(self, status=None):
        return [{"id": w.id, "name": w.name, "status": w.status,
                 "trigger": w.trigger.type} for w in self.workflows.values()]

    def load(self, wf_id):
        return self.workflows.get(wf_id)

    def list_runs(self, wf_id, limit=10):
        return self.runs.get(wf_id, [])[:limit]

    def all_runs(self, limit=30):
        out = []
        for rs in self.runs.values():
            out.extend(rs)
        return out[:limit]


class BrokenStore:
    def list(self):
        raise RuntimeError("disk on fire")

    def load(self, wf_id):
        raise RuntimeError("disk on fire")

    def list_runs(self, wf_id, limit=10):
        raise RuntimeError("disk on fire")


def _run(run_id, status="COMPLETED"):
    return {"run_id": run_id, "status": status, "trigger": "manual",
            "duration_ms": 1500, "finished_at": 1.0}


class TestAutomationOverlay(unittest.TestCase):
    """Layer 3 — the Qt view, offscreen."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _rows(self, layout):
        return [layout.itemAt(i).widget() for i in range(layout.count())
                if layout.itemAt(i).widget() is not None]

    def test_populated_store_lists_and_selects(self):
        from ui import AutomationOverlay
        store = FakeStore([make_wf()],
                          {"wf_demo": [_run("run_1"), _run("run_2", "FAILED")]})
        ov = AutomationOverlay(store=store)
        labels = [b.text() for b in self._rows(ov._list_rows)
                  if hasattr(b, "text")]
        self.assertTrue(any("Demo" in t for t in labels), labels)
        self.assertEqual(ov._selected, "wf_demo")
        self.assertIsNotNone(ov._wf)
        self.assertEqual(ov._canvas._wf.id, "wf_demo")
        self.assertIn("Demo", ov._info.text())
        run_labels = [w.text() for w in self._rows(ov._run_rows)
                      if hasattr(w, "text")]
        self.assertTrue(any("run_2" in t for t in run_labels), run_labels)
        ov.deleteLater()

    def test_empty_store_is_honest(self):
        from ui import AutomationOverlay
        ov = AutomationOverlay(store=FakeStore())
        texts = [w.text() for w in self._rows(ov._list_rows)
                 if hasattr(w, "text")]
        self.assertTrue(any("No automations yet" in t for t in texts), texts)
        self.assertIsNone(ov._canvas._wf)
        ov.deleteLater()

    def test_no_store_never_crashes(self):
        from ui import AutomationOverlay
        ov = AutomationOverlay(store=None)
        self.assertEqual(len(self._rows(ov._list_rows)), 1)  # empty label
        self.assertIn("No automation selected", ov._info.text())
        ov.deleteLater()

    def test_broken_store_degrades_to_empty(self):
        from ui import AutomationOverlay
        ov = AutomationOverlay(store=BrokenStore())
        texts = [w.text() for w in self._rows(ov._list_rows)
                 if hasattr(w, "text")]
        self.assertTrue(any("No automations yet" in t for t in texts), texts)
        ov.deleteLater()

    def test_pull_picks_up_new_workflow_and_drops_deleted(self):
        from ui import AutomationOverlay
        store = FakeStore([make_wf()])
        ov = AutomationOverlay(store=store)
        self.assertEqual(ov._selected, "wf_demo")
        store.workflows.clear()
        ov._pull()
        self.assertIsNone(ov._selected)
        store.workflows["wf_new"] = make_wf("wf_new", "Fresh")
        ov._pull()
        self.assertEqual(ov._selected, "wf_new")
        ov.deleteLater()

    def test_timer_lifecycle(self):
        from ui import AutomationOverlay
        ov = AutomationOverlay(store=FakeStore([make_wf()]))
        self.assertFalse(ov._timer.isActive())
        ov.show()
        self.assertTrue(ov._timer.isActive())
        ov.hide()
        self.assertFalse(ov._timer.isActive())
        ov.deleteLater()

    def test_canvas_paints_with_and_without_workflow(self):
        from ui import AutomationCanvas
        cv = AutomationCanvas()
        cv.resize(400, 300)
        pm = cv.grab()
        self.assertFalse(pm.isNull())
        cv.set_workflow(make_wf())
        pm = cv.grab()
        self.assertFalse(pm.isNull())
        cv.set_workflow(make_loop_wf())
        pm = cv.grab()
        self.assertFalse(pm.isNull())
        cv.deleteLater()

    def test_canvas_layout_layers_body_nodes_out(self):
        from ui import AutomationCanvas
        cv = AutomationCanvas()
        cv.set_workflow(make_loop_wf())
        pos, by_id, chips = cv._layout()
        self.assertIn("trg", pos)
        self.assertIn("lp", pos)
        self.assertNotIn("b1", pos)                 # loop body stays out
        self.assertEqual(chips, [("lp", ["b1"])])
        cv.set_workflow(make_wf())
        pos2, _, _ = cv._layout()
        self.assertEqual(set(pos2), {"trg", "t1", "end"})
        cv.deleteLater()


class TestCanvasAnimation(unittest.TestCase):

    def test_new_workflow_starts_entrance(self):
        from ui import AutomationCanvas
        cv = AutomationCanvas()
        cv.resize(400, 300)
        cv.set_workflow(make_wf())
        self.assertEqual(cv._t, 0.0)
        self.assertTrue(cv._anim.isActive())
        self.assertFalse(cv._flow_t.isActive())
        cv.deleteLater()

    def test_same_id_does_not_restart_entrance(self):
        from ui import AutomationCanvas
        cv = AutomationCanvas()
        cv.set_workflow(make_wf())
        cv._t = 0.6                       # simulate mid-reveal (poll reload)
        cv.set_workflow(make_wf())        # same id — must not restart
        self.assertEqual(cv._t, 0.6)
        self.assertTrue(cv._anim.isActive())
        cv.deleteLater()

    def test_clear_workflow_stops_all_timers(self):
        from ui import AutomationCanvas
        cv = AutomationCanvas()
        cv.set_workflow(make_wf())
        cv.set_workflow(None)
        self.assertFalse(cv._anim.isActive())
        self.assertFalse(cv._flow_t.isActive())
        self.assertEqual(cv._t, 1.0)
        self.assertEqual(cv._flow, 0.0)
        cv.deleteLater()

    def test_entrance_completes_to_flow_only_when_visible(self):
        from ui import AutomationCanvas
        cv = AutomationCanvas()
        cv.resize(400, 300)
        cv.set_workflow(make_wf())
        cv._t = 1.0                       # pretend the reveal just finished
        cv._anim_tick()
        self.assertFalse(cv._anim.isActive())
        self.assertFalse(cv._flow_t.isActive())   # hidden — no pulse
        cv.show()
        self.assertTrue(cv._flow_t.isActive())    # showEvent starts it
        before = cv._flow
        cv._flow_tick()
        self.assertGreater(cv._flow, before)
        cv.hide()
        self.assertFalse(cv._flow_t.isActive())   # hideEvent stops it
        cv.deleteLater()

    def test_grab_mid_animation_no_crash(self):
        from ui import AutomationCanvas
        cv = AutomationCanvas()
        cv.resize(400, 300)
        cv.set_workflow(make_wf())        # _t == 0.0 — first frame
        pm = cv.grab()
        self.assertFalse(pm.isNull())
        cv._t = 0.5
        pm = cv.grab()
        self.assertFalse(pm.isNull())
        cv.deleteLater()


class TestFullscreenToggle(unittest.TestCase):

    def test_maximise_fills_parent_and_restores(self):
        from PyQt6.QtWidgets import QWidget
        from ui import AutomationOverlay
        pw = QWidget()
        pw.resize(1000, 720)
        ov = AutomationOverlay(store=None, parent=pw)
        ov.setGeometry(140, 125, 720, 470)
        ov._toggle_maximise()
        self.assertTrue(ov._maximised)
        self.assertEqual(ov.geometry().size(), pw.size())
        self.assertEqual(ov._max_btn.text(), "RESTORE")
        ov._toggle_maximise()
        self.assertFalse(ov._maximised)
        self.assertEqual((ov.width(), ov.height()), (ov._OW, ov._OH))
        self.assertEqual(ov._max_btn.text(), "FULLSCREEN")
        self.assertEqual((ov.x(), ov.y()), (140, 125))   # re-centred
        ov.deleteLater()
        pw.deleteLater()

    def test_escape_restores_but_never_maximises(self):
        from PyQt6.QtWidgets import QWidget
        from ui import AutomationOverlay
        pw = QWidget()
        pw.resize(900, 640)
        ov = AutomationOverlay(store=None, parent=pw)
        ov._esc()                          # not maximised — must do nothing
        self.assertFalse(ov._maximised)
        ov._toggle_maximise()
        self.assertTrue(ov._maximised)
        ov._esc()                          # Esc exits fullscreen
        self.assertFalse(ov._maximised)
        ov.deleteLater()
        pw.deleteLater()

    def test_toggle_without_parent_does_not_crash(self):
        from ui import AutomationOverlay
        ov = AutomationOverlay(store=None)
        ov._toggle_maximise()
        self.assertTrue(ov._maximised)
        ov._toggle_maximise()
        self.assertFalse(ov._maximised)
        self.assertEqual((ov.width(), ov.height()), (ov._OW, ov._OH))
        ov.deleteLater()


class TestFacadeAndDrawer(unittest.TestCase):

    def test_bind_automation_store_sets_the_window_slot(self):
        from ui import JarvisUI
        ju = JarvisUI.__new__(JarvisUI)
        ju._win = SimpleNamespace(_automation_store=None)
        ju.bind_automation_store("STORE")
        self.assertEqual(ju._win._automation_store, "STORE")

    def test_mainwindow_has_the_handler(self):
        from ui import MainWindow
        self.assertTrue(hasattr(MainWindow, "_open_automation_panel"))


class TestSourceWiring(unittest.TestCase):
    """The seams that only run inside the live app are guarded in source."""

    @classmethod
    def setUpClass(cls):
        cls.ui_src = (PROJECT_ROOT / "ui.py").read_text(encoding="utf-8")
        cls.main_src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    def test_drawer_button_opens_the_panel(self):
        self.assertIn("auto_btn.clicked.connect(self._open_automation_panel)",
                      self.ui_src)
        self.assertIn("def _open_automation_panel", self.ui_src)
        self.assertIn("def bind_automation_store", self.ui_src)

    def test_overlay_polls_the_store_on_a_gui_timer(self):
        i = self.ui_src.index("class AutomationOverlay")
        j = self.ui_src.index("class ClipboardPanel")
        body = self.ui_src[i:j]
        self.assertIn("QTimer", body)
        self.assertIn("self._timer.start()", body)
        self.assertIn("self._timer.stop()", body)
        self.assertIn("class AutomationCanvas", self.ui_src)

    def test_run_binds_the_store_to_the_ui(self):
        i = self.main_src.index("async def run(self)")
        run = self.main_src[i:i + 9000]
        self.assertIn("bind_automation_store", run)

    def test_emit_filter_keeps_only_run_level_events(self):
        i = self.main_src.index("def _automation_emit")
        body = self.main_src[i:i + 700]
        self.assertIn('kind.startswith("run_")', body)
        self.assertIn('"approval_waiting"', body)
        self.assertIn("correlation_id", body)


class TestActivityLabels(unittest.TestCase):
    """Phase 12 — automation run events read as human lines."""

    def test_run_failed_label(self):
        self.assertEqual(
            summarize("custom", "automation", {"kind": "run_failed"}),
            "Automation run failed")

    def test_run_started_appends_workflow_name(self):
        self.assertEqual(
            summarize("custom", "automation",
                      {"kind": "run_started", "name": "Morning briefing"}),
            "Automation run started — Morning briefing")

    def test_approval_waiting_label(self):
        self.assertEqual(
            summarize("custom", "automation",
                      {"kind": "approval_waiting"}),
            "Automation needs confirmation on screen")

    def test_node_kind_still_humanized(self):
        self.assertEqual(summarize("custom", "automation",
                                   {"kind": "node_finished"}),
                         "Node finished")

    def test_existing_labels_unchanged(self):
        self.assertEqual(summarize("custom", "x",
                                   {"kind": "emergency_stop"}),
                         "Emergency stop — mic muted from the dashboard")
        self.assertEqual(summarize("system_startup", "jarvis", {}),
                         "Session started")


class TestMainEmitFilter(unittest.TestCase):

    def _live(self):
        import main as main_mod
        live = main_mod.JarvisLive.__new__(main_mod.JarvisLive)
        live.calls = []
        live.emit_event = (
            lambda et, **kw: live.calls.append((et, kw)) or True)
        return live

    def test_run_events_are_published_with_correlation(self):
        live = self._live()
        live._automation_emit("run_failed",
                              {"kind": "run_failed", "run_id": "run_9"},
                              "run_9")
        self.assertEqual(len(live.calls), 1)
        _et, kw = live.calls[0]
        self.assertEqual(kw["correlation_id"], "run_9")
        self.assertEqual(kw["source"], "automation")
        self.assertEqual(kw["data"]["kind"], "run_failed")

    def test_node_events_are_not_published(self):
        live = self._live()
        live._automation_emit("node_finished",
                              {"kind": "node_finished"}, "run_9")
        live._automation_emit("node_started",
                              {"kind": "node_started"}, "run_9")
        self.assertEqual(live.calls, [])


class TestPromptVerbs(unittest.TestCase):
    """Phase 11 — the model is told how to drive automations honestly."""

    @classmethod
    def setUpClass(cls):
        cls.txt = (PROJECT_ROOT / "core" / "prompt.txt").read_text(
            encoding="utf-8")

    def test_automation_section_present(self):
        self.assertIn("[AUTOMATION]", self.txt)
        self.assertIn("action='generate'", self.txt)
        self.assertIn("NEEDS CLARIFICATION", self.txt)
        self.assertIn("action='status' or\n  'history'", self.txt)

    def test_honest_reporting_rules_present(self):
        self.assertIn("never from assuming", self.txt)
        self.assertIn("[CONFIRMATION_PENDING]", self.txt)

    def test_existing_sections_intact(self):
        for marker in ("[SELF]", "[EXECUTION]", "[WHAT YOU CANNOT DO]",
                       "{capabilities}"):
            self.assertIn(marker, self.txt)


if __name__ == "__main__":
    unittest.main()
