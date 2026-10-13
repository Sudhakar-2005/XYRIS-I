"""Neural core visual tests (Phases 4/8 of the CORE_VISUAL design).

Covers: state→motion completeness over the authoritative vocabulary,
bounded particles, deterministic projection/signal math, transition-pulse
dedup and decay, reduced-motion behaviour, widget render smoke, and
cleanup (timer stop + app_state listener removal).
"""
import time
import unittest

from core import reactor_visual as rviz
from core import app_state


class TestStateMotionMapping(unittest.TestCase):

    def test_every_valid_state_has_a_motion_profile(self):
        for s in app_state.VALID_STATES:
            m = rviz.motion_for(s)
            self.assertIsInstance(m, rviz.Motion, s)
            self.assertGreaterEqual(m.particle_budget, 0, s)
            self.assertLessEqual(m.particle_budget, rviz.MAX_PARTICLES, s)
            self.assertGreaterEqual(m.ring_rate, 0.0, s)

    def test_unknown_and_empty_states_fall_back_to_calm_default(self):
        for s in ("NOT_A_STATE", "", None, "  weird  "):
            self.assertEqual(rviz.motion_for(s), rviz._DEFAULT, repr(s))

    def test_legacy_processing_alias_maps_to_executing(self):
        self.assertEqual(rviz.motion_for("PROCESSING"),
                         rviz.motion_for("EXECUTING"))

    def test_status_lit_is_state_not_progress(self):
        self.assertEqual(rviz.status_lit("ERROR"), 1.0)
        self.assertEqual(rviz.status_lit(None), 0.12)          # unknown → rest
        self.assertLess(rviz.status_lit("IDLE"),
                        rviz.status_lit("EXECUTING"))
        # never a growing "progress" number: same state → same value always
        self.assertEqual(rviz.status_lit("EXECUTING"),
                         rviz.status_lit("EXECUTING"))

    def test_reduced_motion_profile_is_nearly_still(self):
        m = rviz.reduced_motion_profile()
        self.assertEqual(m.particle_budget, 0)
        self.assertLessEqual(m.ring_rate, 0.15)
        self.assertEqual(m.signal_speed, 0.0)


class TestNeuralGraphAndParticles(unittest.TestCase):

    def test_graph_is_deterministic_and_bounded(self):
        self.assertEqual(len(rviz.NODES), rviz.NODE_COUNT)
        self.assertEqual(rviz.project_nodes(0.5), rviz.project_nodes(0.5))
        for sx, sy, d, tier in rviz.project_nodes(1.1):
            self.assertTrue(-1.001 <= sx <= 1.001)
            self.assertTrue(-1.001 <= sy <= 1.001)
            self.assertTrue(0.0 <= d <= 1.0)
            self.assertIn(tier, (0, 1, 2))
        for i, j in rviz.EDGES:
            self.assertNotEqual(i, j)

    def test_particle_budget_hard_cap(self):
        self.assertEqual(len(rviz.spawn_particles(10_000)),
                         rviz.MAX_PARTICLES)
        self.assertEqual(len(rviz.spawn_particles(-3)), 0)
        parts = rviz.spawn_particles(10)
        before = len(parts)
        rviz.step_particles(parts, 0.016, 0.2)
        self.assertEqual(len(parts), before, "step must never change count")

    def test_particles_project_within_disc(self):
        p = rviz.spawn_particles(8)[0]
        sx, sy, d = rviz.project_particle(p, 0.7)
        self.assertTrue(-1.5 <= sx <= 1.5)
        self.assertTrue(0.0 <= d <= 1.0)

    def test_signal_positions_respect_speed_and_bounds(self):
        self.assertEqual(rviz.signal_positions(1.0, 0.0), ())
        sigs = rviz.signal_positions(2.5, 0.4)
        self.assertLessEqual(len(sigs), rviz.SIGNAL_COUNT)
        self.assertEqual(sigs, rviz.signal_positions(2.5, 0.4), "deterministic")
        for eidx, frac in sigs:
            self.assertTrue(0 <= eidx < len(rviz.EDGES))
            self.assertTrue(0.0 <= frac < 1.0)


class TestTransitionPulses(unittest.TestCase):

    def test_pulse_kinds_come_from_states_only(self):
        self.assertEqual(rviz.pulse_kind_for("VERIFYING"), "verify")
        self.assertEqual(rviz.pulse_kind_for("EXECUTING"), "tool")
        self.assertIsNone(rviz.pulse_kind_for("IDLE"))
        self.assertIsNone(rviz.pulse_kind_for("NONSENSE"))

    def test_pulse_alpha_decays_and_ignores_late_events(self):
        self.assertGreaterEqual(rviz.pulse_alpha(0.0), 0.99)
        self.assertLess(rviz.pulse_alpha(rviz.PULSE_TTL * 0.9), 0.1)
        self.assertEqual(rviz.pulse_alpha(rviz.PULSE_TTL + 5), 0.0)
        self.assertEqual(rviz.pulse_alpha(-1.0), 0.0, "late arrival is safe")


class TestHudCanvasWiring(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import ui
        self.ui_mod = ui
        self.hud = ui.HudCanvas("", "TESTCORE")
        self.addCleanup(self._close_hud)

    def _close_hud(self):
        try:
            self.hud.close()
        except Exception:
            pass

    def test_timer_running_and_listener_attached(self):
        self.assertTrue(self.hud._tmr.isActive())
        self.assertTrue(self.hud._state_listener_attached)
        self.assertIn(self.hud._on_state_event, app_state._listeners)

    def test_state_event_creates_and_dedups_pulses(self):
        self.hud._pulses.clear()
        self.hud._on_state_event("ERROR", "IDLE", "test")
        self.assertIn("error", self.hud._pulses)
        first = self.hud._pulses["error"]
        time.sleep(0.2)
        self.hud._on_state_event("ERROR", "ERROR", "test")  # duplicate burst
        self.assertGreater(self.hud._pulses["error"], first)
        # unknown state → no pulse, no crash
        self.hud._on_state_event("TOTALLY_UNKNOWN", "IDLE", "test")
        self.assertNotIn("TOTALLY_UNKNOWN", self.hud._pulses)

    def test_reduced_motion_zeroes_particles_and_slow_phase(self):
        self.hud._reduced_motion = True
        self.hud._particles = rviz.spawn_particles(20)
        self.hud.state = "EXECUTING"
        self.hud._step()
        self.assertEqual(len(self.hud._particles), 0,
                         "reduced motion must drain the particle budget")
        self.hud.close()

    def test_render_smoke_all_layers(self):
        self.hud.state = "VERIFYING"          # exercises scan + status ring
        self.hud._particles = rviz.spawn_particles(12)
        pix = self.hud.grab()
        self.assertFalse(pix.isNull())
        self.hud.state = "WAITING_CONFIRMATION"
        self.assertFalse(self.hud.grab().isNull())
        self.hud.state = "ERROR"
        self.assertFalse(self.hud.grab().isNull())

    def test_close_stops_timer_and_removes_listener(self):
        hud2 = self.ui_mod.HudCanvas("", "TEST2")
        self.assertIn(hud2._on_state_event, app_state._listeners)
        hud2.close()
        self.assertFalse(hud2._tmr.isActive())
        self.assertNotIn(hud2._on_state_event, app_state._listeners)
        self.assertFalse(hud2._state_listener_attached)

    def test_state_accent_semantics(self):
        hud = self.ui_mod.HudCanvas.__new__(self.ui_mod.HudCanvas)
        hud.state = "ERROR"
        self.assertIsNotNone(hud._state_accent())
        hud.state = "WAITING_CONFIRMATION"
        self.assertIsNotNone(hud._state_accent())
        hud.state = "LISTENING"
        self.assertIsNotNone(hud._state_accent())
        hud.state = "IDLE"
        self.assertIsNone(hud._state_accent(), "rest has no state accent")


if __name__ == "__main__":
    unittest.main()
