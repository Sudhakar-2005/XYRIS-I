"""Living Neural Core V2 — Phase 2/9 tests (CORE_VISUAL_V2_DESIGN).

Covers the ring-set motion profiles (distinctness invariant), technical
markings constants, reactor render smoke across the real state vocabulary,
reduced-motion behaviour of the new layers, and the fail-soft paint
boundary with its sticky simple-orb fallback (spec §14).
"""
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
