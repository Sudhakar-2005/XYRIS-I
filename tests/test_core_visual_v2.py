"""Living Neural Core V2 — Phase 2/9 tests (CORE_VISUAL_V2_DESIGN).

Covers the ring-set motion profiles (distinctness invariant), technical
markings constants, reactor render smoke across the real state vocabulary,
reduced-motion behaviour of the new layers, and the fail-soft paint
boundary with its sticky simple-orb fallback (spec §14).
"""
import math
import time
import unittest

from core import reactor_visual as rviz
from core import app_state


class TestRingProfiles(unittest.TestCase):

    def test_profiles_have_distinct_motion(self):
        profiles = rviz.RING_PROFILES
        self.assertGreaterEqual(len(profiles), 6, "Layer C ring set")
        names = [p.name for p in profiles]
        self.assertEqual(len(names), len(set(names)), "unique ring names")
        for p in profiles:
            self.assertGreater(p.rate, 0.0, p.name)
            self.assertIn(p.direction, (+1, -1), p.name)
            self.assertGreater(p.span, 0.0, p.name)
            self.assertLessEqual(p.span, 360.0, p.name)
            self.assertGreaterEqual(p.radius, 0.5, p.name)
            self.assertLessEqual(p.radius, 1.2, p.name)
            self.assertGreater(p.alpha, 0.0, p.name)
            self.assertLessEqual(p.alpha, 1.0, p.name)
            self.assertGreater(p.width, 0.0, p.name)
            self.assertIn(p.colour, ("main", "acc"), p.name)
        # The tested invariant: no two rings share rate AND direction.
        keys = [(p.rate, p.direction) for p in profiles]
        self.assertEqual(len(keys), len(set(keys)),
                         "two rings share the same motion signature")

    def test_ring_bases_are_deterministic_and_reduced_freezes(self):
        bases = rviz.ring_bases(1.7, 2.0)
        self.assertEqual(len(bases), len(rviz.RING_PROFILES))
        self.assertEqual(bases, rviz.ring_bases(1.7, 2.0), "deterministic")
        phases = tuple(p.phase % 360.0 for p in rviz.RING_PROFILES)
        self.assertEqual(rviz.ring_bases(9.0, 3.0, reduced=True), phases,
                         "reduced motion must freeze rotation at the phase")
        moving = rviz.ring_bases(9.0, 3.0)
        self.assertNotEqual(moving, phases, "normal motion must rotate")

    def test_technical_markings_and_tick_constants_are_sane(self):
        tm = rviz.TECH_MARKINGS
        self.assertGreaterEqual(tm["count"], 36)
        self.assertGreaterEqual(tm["major_every"], 2)
        self.assertLess(tm["radius_base"], tm["radius_fine"])
        self.assertLessEqual(tm["radius_fine"], tm["radius_major"])
        self.assertLess(tm["radius_major"], 1.0)
        self.assertGreaterEqual(tm["rate"], 0.0)
        ct = rviz.CALIBRATION_TICKS
        self.assertGreaterEqual(ct["count"], 8)
        self.assertLess(ct["radius_in"], ct["radius_out"])
        self.assertGreater(ct["alpha"], 0.0)


class TestSphereDepthV2(unittest.TestCase):
    """V2 Layer B: perspective, deterministic node drift, bounds."""

    def test_perspective_scale_is_monotonic_and_clamped(self):
        self.assertAlmostEqual(rviz.perspective_scale(0.0), 0.82)
        self.assertAlmostEqual(rviz.perspective_scale(1.0), 1.22)
        prev = -1.0
        for i in range(11):
            v = rviz.perspective_scale(i / 10)
            self.assertGreaterEqual(v, prev, "monotonic in depth")
            prev = v
        self.assertAlmostEqual(rviz.perspective_scale(-3.0), 0.82,
                               msg="far side clamps")
        self.assertAlmostEqual(rviz.perspective_scale(9.0), 1.22,
                               msg="near side clamps")

    def test_drift_is_deterministic_bounded_and_indexed(self):
        self.assertEqual(len(rviz.DRIFT), rviz.NODE_COUNT)
        for freq, ph, amp in rviz.DRIFT:
            self.assertGreater(freq, 0.0)
            self.assertTrue(0.0 <= ph < math.tau)
            self.assertLessEqual(amp, 0.03, "drift must stay micro")

    def test_node_positions_deterministic_and_within_bounds(self):
        a = rviz.node_positions(0.9, 3.3)
        b = rviz.node_positions(0.9, 3.3)
        self.assertEqual(a, b, "deterministic in (ang, t)")
        for sx, sy, d, tier in a:
            self.assertLessEqual(abs(sx), 1.35)
            self.assertLessEqual(abs(sy), 1.35)
            self.assertTrue(0.0 <= d <= 1.0)
            self.assertIn(tier, (0, 1, 2))
        c = rviz.node_positions(0.9, 7.7)
        self.assertNotEqual(a, c, "micro-drift animates over time")
        for sx, sy, d, tier in c:
            self.assertLessEqual(abs(sx), 1.35)
            self.assertLessEqual(abs(sy), 1.35)

    def test_reduced_motion_freezes_drift(self):
        still1 = rviz.node_positions(1.2, 10.0, reduced=True)
        still2 = rviz.node_positions(1.2, 10.5, reduced=True)
        self.assertEqual(still1, still2, "no drift under reduced motion")
        # rotation still honoured through the ang argument
        self.assertNotEqual(rviz.node_positions(0.0, 10.0, reduced=True),
                            rviz.node_positions(1.5, 10.0, reduced=True))


class TestPointerParallax(unittest.TestCase):
    """V2 Layer E: bounded pointer parallax — target from real mouse events,
    eased in _step, zeroed on leave and under reduced motion."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import ui
        self.hud = ui.HudCanvas("", "PAR")
        self.hud.resize(400, 300)
        self.addCleanup(lambda: self.hud.close())

    def _move(self, x, y):
        self._move2(self.hud, x, y)

    def test_tracking_on_and_target_normalised(self):
        self.assertTrue(self.hud.hasMouseTracking())
        self._move(0.0, 0.0)                       # top-left → (-1, -1)
        self.assertAlmostEqual(self.hud._par_tgt.x(), -1.0)
        self.assertAlmostEqual(self.hud._par_tgt.y(), -1.0)
        self.assertTrue(self.hud._par_on)
        self._move(400.0, 300.0)                   # bottom-right → (1, 1)
        self.assertAlmostEqual(self.hud._par_tgt.x(), 1.0)
        self.assertAlmostEqual(self.hud._par_tgt.y(), 1.0)

    def test_step_eases_toward_target(self):
        self._move(400.0, 0.0)
        for _ in range(80):
            self.hud._step()
        self.assertAlmostEqual(self.hud._par.x(), 1.0, places=2)
        self.assertAlmostEqual(self.hud._par.y(), -1.0, places=2)

    def test_leave_returns_to_centre_and_reduced_blocks(self):
        from PyQt6.QtCore import QEvent
        import ui
        self._move(400.0, 0.0)
        self.hud.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertEqual((self.hud._par_tgt.x(), self.hud._par_tgt.y()),
                         (0.0, 0.0), "leave must re-centre the target")
        for _ in range(80):
            self.hud._step()
        self.assertAlmostEqual(self.hud._par.x(), 0.0, places=3)

        hud2 = ui.HudCanvas("", "PAR2")
        try:
            hud2._reduced_motion = True
            self._move2(hud2, 400.0, 0.0)
            self.assertFalse(hud2._par_on, "reduced motion ignores the pointer")
            self.assertEqual(hud2._par_tgt.x(), 0.0)
        finally:
            hud2.close()

    def _move2(self, hud, x, y):
        from PyQt6.QtCore import QPointF, QEvent, Qt
        try:
            from PyQt6.QtGui import QMouseEvent
            e = QMouseEvent(QEvent.Type.MouseMove, QPointF(x, y),
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier)
        except Exception:                       # pragma: no cover
            class _E:
                def position(self):
                    return QPointF(x, y)
            e = _E()
        hud.mouseMoveEvent(e)


class TestAutomationBand(unittest.TestCase):
    """V2 Layer D: honest automation→band mapping (unknown/stale/expiry
    rules), the canvas record method, and paint smoke with the band live."""

    def test_unknown_kinds_never_paint(self):
        self.assertIsNone(rviz.auto_band("invented_kind", 100.0, 100.0, 100.0))
        self.assertIsNone(rviz.auto_band("", 100.0, 100.0, 100.0))
        self.assertIsNone(rviz.auto_band(None, 100.0, 100.0, 100.0))

    def test_backend_inventory_is_fully_covered(self):
        backend = {"run_started", "run_paused", "run_resumed",
                   "run_completed", "run_failed", "run_cancelled",
                   "run_waiting_approval", "run_dry_run",
                   "approval_waiting", "node_started", "node_retry",
                   "node_finished"}
        self.assertLessEqual(backend, set(rviz.AUTO_KINDS),
                             "every backend kind must be paintable")
        for k in rviz.AUTO_KINDS:
            self.assertTrue(rviz.AUTO_LABELS.get(k), f"label missing: {k}")

    def test_active_and_hold_persist_until_stale(self):
        cls, a, label, frac = rviz.auto_band("run_started", 100.0, 100.0, 100.0)
        self.assertEqual((cls, label), ("active", "RUN"))
        self.assertEqual(frac, 0.0)
        self.assertGreater(a, 0.0)
        cls2, _, label2, _ = rviz.auto_band("run_paused", 100.0, 100.0, 100.0)
        self.assertEqual((cls2, label2), ("hold", "PAUSED"))
        late = 100.0 + rviz.AUTO_STALE_S + 1.0
        self.assertIsNone(rviz.auto_band("run_started", late, 100.0, 100.0),
                          "stale run band must disappear (honest default)")

    def test_terminal_outcomes_flash_then_expire(self):
        now, at = 50.0, 50.0
        cls, a0, label, _ = rviz.auto_band("run_completed", now, now, at)
        self.assertEqual((cls, label), ("done", "DONE"))
        mid = rviz.auto_band("run_completed", now + 0.8, now, at)
        self.assertLess(mid[1], a0, "completion flash decays")
        ttl = rviz.AUTO_OUTCOME_TTL["run_completed"]
        self.assertIsNone(rviz.auto_band("run_completed", now + ttl + 0.01,
                                         now, at), "expired = no band")
        s0 = rviz.auto_band("run_failed", now, now, at)
        self.assertEqual(s0[0], "stop")
        s1 = rviz.auto_band("run_failed", now + 3.0, now, at)
        self.assertLess(s1[1], s0[1], "failure banner fades but lingers")
        self.assertEqual(rviz.auto_band("run_dry_run", now, now, at)[0],
                         "clear", "dry run is neutral, never success")
        self.assertEqual(rviz.auto_band("run_cancelled", now, now, at)[0],
                         "clear", "cancel is neutral, never success")

    def test_no_progress_fields_exist_in_band_recipe(self):
        # The band must never carry a percentage — assert the recipe shape
        # has only (cls, alpha, label, age_frac) and alpha stays in [0, 1].
        res = rviz.auto_band("node_finished", 10.0, 10.0, 10.0)
        self.assertEqual(len(res), 4)
        self.assertGreaterEqual(res[1], 0.0)
        self.assertLessEqual(res[1], 1.0)


class TestAutomationBandHud(unittest.TestCase):
    """Canvas-side record method: dedup, close-on-terminal, paint smoke."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import ui
        self.hud = ui.HudCanvas("", "AUTO")
        self.hud.hud_style = "core"
        self.addCleanup(lambda: self.hud.close())

    def test_unknown_ignored_and_known_recorded_deduped(self):
        self.hud.on_automation_event("nonsense", {}, "r1")
        self.assertEqual(self.hud._auto, {}, "unknown kind must be ignored")
        self.hud.on_automation_event("run_started", {"x": 1}, "r1")
        self.assertEqual(self.hud._auto["kind"], "run_started")
        at0 = self.hud._auto["at"]
        self.hud.on_automation_event("run_started", {}, "r1")   # <150 ms dup
        self.assertEqual(self.hud._auto["at"], at0, "duplicate must dedupe")
        self.hud.on_automation_event("node_finished", {"node": "n"}, "r1")
        self.assertEqual(self.hud._auto["kind"], "node_finished")
        self.assertGreater(self.hud._auto_pulse, 0.0,
                           "node events pulse the band")

    def test_terminal_wins_over_late_events_and_new_run_resets(self):
        self.hud.on_automation_event("run_started", {}, "r9")
        self.hud.on_automation_event("run_completed", {}, "r9")
        self.assertTrue(self.hud._auto["close"])
        self.hud.on_automation_event("node_finished", {}, "r9")   # out of order
        self.assertEqual(self.hud._auto["kind"], "run_completed",
                         "late node events must not resurrect a closed run")
        self.hud.on_automation_event("run_started", {}, "r10")    # new run
        self.assertEqual(self.hud._auto["kind"], "run_started")
        self.assertFalse(self.hud._auto["close"], "new run starts open")

    def test_band_paint_smoke_including_reduced_motion(self):
        self.hud.on_automation_event("run_started", {}, "r1")
        self.assertFalse(self.hud.grab().isNull())
        self.hud.on_automation_event("run_failed", {}, "r1")
        self.assertFalse(self.hud.grab().isNull())
        self.hud._reduced_motion = True
        self.assertFalse(self.hud.grab().isNull(),
                         "reduced motion: static band window still paints")
        self.hud.on_automation_event("run_completed", {}, "r2")
        self.assertFalse(self.hud.grab().isNull())


class TestAutomationSeam(unittest.TestCase):
    """Phase 5/6 wiring: main forwards ALL kinds to the UI seam, the bus
    keeps its run-only filter, JarvisUI rides the queued signal."""

    def test_main_forwards_all_kinds_and_filters_bus(self):
        import main as main_mod

        class FakeUI:
            def __init__(self):
                self.calls = []

            def emit_automation(self, kind, payload, run_id):
                self.calls.append((kind, payload, run_id))

        j = main_mod.JarvisLive.__new__(main_mod.JarvisLive)
        j.ui = FakeUI()
        bus = []
        j.emit_event = lambda *a, **k: bus.append((a, k))

        j._automation_emit("node_finished", {"node": "n"}, "run_1")
        self.assertEqual(len(j.ui.calls), 1, "node kinds reach the HUD seam")
        self.assertEqual(bus, [], "node kinds must NOT reach the bus/feed")

        j._automation_emit("run_completed", {"status": "ok"}, "run_1")
        self.assertEqual(len(j.ui.calls), 2)
        self.assertEqual(len(bus), 1, "run kinds still reach the bus")

    def test_jarvisui_emit_automation_rides_signal_with_dict_payload(self):
        import ui

        class FakeSig:
            def __init__(self):
                self.emitted = []

            def emit(self, *a):
                self.emitted.append(a)

        class FakeWin:
            pass

        j = ui.JarvisUI.__new__(ui.JarvisUI)
        w = FakeWin()
        w._auto_sig = FakeSig()
        j._win = w
        j.emit_automation("run_paused", {"x": 1}, "r2")
        self.assertEqual(w._auto_sig.emitted[0][0], "run_paused")
        self.assertEqual(w._auto_sig.emitted[0][2], "r2")
        j.emit_automation("weird", "not-a-dict", "r2")
        self.assertEqual(w._auto_sig.emitted[1][1], {},
                         "non-dict payload is coerced, never raised")

    def test_mainwindow_signal_reaches_canvas_slot(self):
        import ui
        self.assertTrue(hasattr(ui.MainWindow, "_auto_sig"))
        self.assertTrue(callable(ui.HudCanvas.on_automation_event))


class TestV2ReactorPaint(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import ui
        self.ui_mod = ui
        self.hud = ui.HudCanvas("", "V2CORE")
        self.hud.hud_style = "core"        # exercise the reactor path
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.hud.close()
        except Exception:
            pass

    def test_reactor_renders_across_real_states(self):
        for st in ("IDLE", "LISTENING", "THINKING", "PLANNING", "EXECUTING",
                   "VERIFYING", "SPEAKING", "WAITING_CONFIRMATION", "ERROR",
                   "RECOVERING", "SLEEPING", "MUTED"):
            self.hud.state = st
            self.assertFalse(self.hud.grab().isNull(), st)
        self.assertFalse(self.hud._core_paint_failed,
                         "no state may fail the V2 paint path")

    def test_new_layers_survive_reduced_motion_and_live_pulse(self):
        self.hud._reduced_motion = True
        self.hud.state = "VERIFYING"
        self.hud._pulses["verify"] = time.monotonic()   # live transition
        self.assertFalse(self.hud.grab().isNull())
        self.hud._reduced_motion = False
        self.assertFalse(self.hud.grab().isNull(), "pulse ring when motion on")

    def test_paint_failure_falls_back_and_sticks(self):
        hud = self.hud
        calls = {"detail": 0, "fallback": 0}

        def boom(*_a, **_k):
            calls["detail"] += 1
            raise RuntimeError("synthetic paint failure")

        real_fb = hud._paint_core_fallback

        def spy_fb(*a, **k):
            calls["fallback"] += 1
            return real_fb(*a, **k)

        hud._paint_core = boom
        hud._paint_core_fallback = spy_fb
        with self.assertLogs("aether.hud", level="ERROR"):
            pix = hud.grab()
        self.assertFalse(pix.isNull(), "fallback must still produce a frame")
        self.assertTrue(hud._core_paint_failed)
        self.assertGreaterEqual(calls["fallback"], 1)
        self.assertGreaterEqual(calls["detail"], 1)

        # sticky: even if the detail helper would work again, the canvas
        # stays on the fallback (no per-frame retry of a broken renderer)
        hud._paint_core = lambda *_a, **_k: None
        hud.grab()
        self.assertEqual(calls["detail"], 1, "detail must not be retried")
        self.assertGreaterEqual(calls["fallback"], 2)

    def test_fallback_orb_paints_standalone(self):
        from PyQt6.QtGui import QImage, QPainter
        img = QImage(240, 240, QImage.Format.Format_RGB32)
        img.fill(0)
        p = QPainter(img)
        try:
            self.hud._paint_core_fallback(p, 120.0, 120.0, 90.0)
        finally:
            p.end()
        self.assertFalse(img.isNull())
        # something non-background reached the surface
        self.assertNotEqual(img.pixel(120, 120), img.pixel(2, 2))

    def test_app_state_vocabulary_covers_paint_smoke(self):
        for s in app_state.VALID_STATES:
            self.hud.state = s
            self.assertFalse(self.hud.grab().isNull(), s)


if __name__ == "__main__":
    unittest.main()
