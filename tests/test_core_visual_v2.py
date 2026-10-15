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
