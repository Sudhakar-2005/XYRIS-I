"""
Stage 4 — ON-state probe tests for the recognizer hardening gates
(GESTURE_INTEGRATION_ROADMAP.md Stage 4, GESTURE_TEST_PLAN.md L1 rows G1/G2/G5/G6).

Every test here constructs GestureSettings with a flag ON; the Stage 3 golden
battery (tests/test_gesture_probe.py) covers the flag-default (legacy) state
and must pass unchanged — that pair is the "no silent flip" guarantee.

Flagless Stage 4e/4f repairs are asserted in the golden file (deliberately
flipped tests, see its module docstring), not here.
"""
import math
import os
import random
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.spatial.gesture_recognizer import GestureRecognizer
from core.spatial.interaction_state import (
    GestureIntent,
    GestureSettings,
    GestureType,
    InteractionState,
)
from gesture_fixtures import pose_fist, pose_open, pose_pinch_ratio, pose_point
from test_gesture_probe import Probe


def _pair(d1: float, d2: float, angle2: float = 0.0):
    """Two open hands at palm distance d1 (frame 1) and d2/angle2 (frame 2)."""
    a1 = make_at(0.5 - d1 / 2, 0.56)
    b1 = make_at(0.5 + d1 / 2, 0.56)
    a2 = make_at(0.5 - (d2 / 2) * math.cos(angle2),
                 0.56 - (d2 / 2) * math.sin(angle2))
    b2 = make_at(0.5 + (d2 / 2) * math.cos(angle2),
                 0.56 + (d2 / 2) * math.sin(angle2))
    return (a1, b1), (a2, b2)


def make_at(x: float, y: float):
    from gesture_fixtures import make_hand
    return make_hand(palm_center=(x, y))


# ---------------------------------------------------------------------------
class TestG1NormalizedPinch(unittest.TestCase):
    """G1 — holo.html:368/370/376 + :519-525 (IN .30 / OUT .42 / EARN 2)."""

    def _probe(self):
        return Probe(settings=GestureSettings(pinch_normalized=True))

    def test_in_gate_earns_on_second_consecutive_frame(self):
        p = self._probe()
        try:
            ev1, _ = p.feed(pose_pinch_ratio(0.25))
            self.assertEqual(ev1.gesture, GestureType.NONE)   # N-1 frame ⇒ nothing
            ev2, _ = p.feed(pose_pinch_ratio(0.25))
            self.assertEqual(ev2.gesture, GestureType.PINCH)
            self.assertGreaterEqual(ev2.confidence, 0.7)
        finally:
            p.close()

    def test_single_below_in_frame_resets_and_earns_nothing(self):
        p = self._probe()
        try:
            seq = [0.25, 0.35, 0.25, 0.25]   # break above IN at frame 2
            evs = [p.feed(pose_pinch_ratio(r))[0] for r in seq]
            self.assertEqual(evs[0].gesture, GestureType.NONE)   # earn 1
            self.assertEqual(evs[1].gesture, GestureType.NONE)   # reset (0.35 ≥ IN)
            self.assertEqual(evs[2].gesture, GestureType.NONE)   # earn restarted 1
            self.assertEqual(evs[3].gesture, GestureType.PINCH)  # earn 2
        finally:
            p.close()

    def test_out_gate_releases_and_re_earns_from_scratch(self):
        p = self._probe()
        try:
            p.feed(pose_pinch_ratio(0.25))
            ev, _ = p.feed(pose_pinch_ratio(0.25))
            self.assertEqual(ev.gesture, GestureType.PINCH)      # armed
            ev, _ = p.feed(pose_pinch_ratio(0.45))               # > PINCH_OUT
            self.assertEqual(ev.gesture, GestureType.FIST)       # latch off → pose
            ev, _ = p.feed(pose_pinch_ratio(0.25))
            self.assertEqual(ev.gesture, GestureType.NONE)       # earn restarted
            ev, _ = p.feed(pose_pinch_ratio(0.25))
            self.assertEqual(ev.gesture, GestureType.PINCH)      # re-earned
        finally:
            p.close()

    def test_span_invariance_scale_05_vs_15(self):
        # Same ratio sequence must decide identically at span 0.5 and 1.5
        # (the motivation for normalizing: absolute-0.05 cannot hold this).
        seq = [0.25, 0.25, 0.45, 0.25, 0.25]
        results = []
        for scale in (0.5, 1.5):
            p = self._probe()
            try:
                evs = [p.feed(pose_pinch_ratio(r, scale=scale))[0] for r in seq]
                results.append([e.gesture for e in evs])
            finally:
                p.close()
        expected = [
            GestureType.NONE, GestureType.PINCH, GestureType.FIST,
            GestureType.NONE, GestureType.PINCH,
        ]
        self.assertEqual(results[0], expected)
        self.assertEqual(results[1], expected)


# ---------------------------------------------------------------------------
class TestG2PoseEarn(unittest.TestCase):
    """G2 — pose earn-frames; holo.html:491-496 (st.point === 4)."""

    def _probe(self):
        return Probe(settings=GestureSettings(pose_earn_frames=4))

    def test_point_flicker_three_frames_never_fires(self):
        p = self._probe()
        try:
            for _ in range(3):
                ev, _ = p.feed(pose_point())
                self.assertEqual(ev.gesture, GestureType.NONE)
            # a different pose breaks the streak — still no POINT afterwards
            p.feed(pose_fist())
            ev, _ = p.feed(pose_point())
            self.assertEqual(ev.gesture, GestureType.NONE)
        finally:
            p.close()

    def test_point_fires_on_fourth_frame_and_recovers_state(self):
        p = self._probe()
        try:
            for _ in range(3):
                ev, _ = p.feed(pose_point())
                self.assertEqual(ev.gesture, GestureType.NONE)
                self.assertEqual(p.sm.state, InteractionState.LOW_CONFIDENCE)
            ev, out = p.feed(pose_point())
            self.assertEqual(ev.gesture, GestureType.POINT)
            self.assertAlmostEqual(ev.confidence, 0.9)
            # Stage 4e recovery lets the earned frame act immediately
            self.assertEqual(out.intent, GestureIntent.POINTER_MOVE)
            self.assertEqual(p.sm.state, InteractionState.HOVERING)
        finally:
            p.close()

    def test_open_palm_three_vs_four_frame_bounds(self):
        p = self._probe()
        try:
            for _ in range(3):
                ev, _ = p.feed(pose_open())
                self.assertEqual(ev.gesture, GestureType.NONE)
            ev, out = p.feed(pose_open())
            self.assertEqual(ev.gesture, GestureType.OPEN_PALM)
            self.assertEqual(out.intent, GestureIntent.PAUSE)
            self.assertEqual(p.sm.state, InteractionState.PAUSED)
        finally:
            p.close()

    def test_fist_three_vs_four_frame_bounds(self):
        p = self._probe()
        try:
            for _ in range(3):
                ev, _ = p.feed(pose_fist())
                self.assertEqual(ev.gesture, GestureType.NONE)
            ev, out = p.feed(pose_fist())
            self.assertEqual(ev.gesture, GestureType.FIST)
            self.assertEqual(out.intent, GestureIntent.CANCEL)
            self.assertEqual(p.sm.state, InteractionState.CANCELLED)
        finally:
            p.close()

    def test_earn_off_single_frame_is_enough(self):
        p = Probe(settings=GestureSettings(pose_earn_frames=0))  # legacy default
        try:
            ev, _ = p.feed(pose_point())
            self.assertEqual(ev.gesture, GestureType.POINT)
        finally:
            p.close()


# ---------------------------------------------------------------------------
class TestG5TwoHandGate(unittest.TestCase):
    """G5 — separation gate (holo.html:631, 140px ≈ 0.10 normalized) and
    just-appeared second-hand grace (holo.html:483, st.seen >= 3)."""

    def test_close_palms_cannot_zoom(self):
        p = Probe(settings=GestureSettings(two_hand_min_separation=0.10))
        try:
            (a1, b1), (a2, b2) = _pair(0.050, 0.065)   # velocity would fire
            ev1, _ = p.feed(a1, b1)
            self.assertEqual(ev1.gesture, GestureType.NONE)
            ev2, _ = p.feed(a2, b2)                    # dv 0.015 > 0.01
            self.assertEqual(ev2.gesture, GestureType.NONE)  # gated, no SPREAD
        finally:
            p.close()

    def test_close_palms_cannot_rotate(self):
        p = Probe(settings=GestureSettings(two_hand_min_separation=0.10))
        try:
            (a1, b1), _ = _pair(0.05, 0.05)
            p.feed(a1, b1)
            _, (a2, b2) = _pair(0.05, 0.05, angle2=0.3)  # same dist, big angle
            ev, _ = p.feed(a2, b2)
            self.assertEqual(ev.gesture, GestureType.NONE)   # gated, no ROTATE
        finally:
            p.close()

    def test_separated_hands_still_zoom(self):
        p = Probe(settings=GestureSettings(two_hand_min_separation=0.10))
        try:
            (a1, b1), (a2, b2) = _pair(0.30, 0.45)
            p.feed(a1, b1)
            ev, _ = p.feed(a2, b2)
            self.assertEqual(ev.gesture, GestureType.SPREAD)
        finally:
            p.close()

    def test_just_appeared_second_hand_grace(self):
        p = Probe(settings=GestureSettings(two_hand_grace_frames=3))
        try:
            (a1, b1), _ = _pair(0.30, 0.30)
            ev, _ = p.feed(a1, b1)
            self.assertEqual(ev.gesture, GestureType.NONE)
            # frame 2 has real zoom velocity but the pair is only 2 frames old
            _, (a2, b2) = _pair(0.30, 0.45)
            ev, _ = p.feed(a2, b2)
            self.assertEqual(ev.gesture, GestureType.NONE)
            _, (a3, b3) = _pair(0.45, 0.60)
            ev, _ = p.feed(a3, b3)
            self.assertEqual(ev.gesture, GestureType.SPREAD)   # seen >= 3
        finally:
            p.close()

    def test_grace_and_separation_default_off(self):
        s = GestureSettings()
        self.assertEqual(s.two_hand_min_separation, 0.0)
        self.assertEqual(s.two_hand_grace_frames, 1)


# ---------------------------------------------------------------------------
class TestG6OneEuro(unittest.TestCase):
    """G6 — One Euro filter, holo.html:418-429 (dt clamp :420, d_cutoff :423,
    cutoff = 1.3 + .009*speed :426). Runs in the virtual 1280px frame."""

    _OE = GestureSettings(smoothing_filter="one_euro")

    @staticmethod
    def _run_static(settings, jitter, frames=80, seed_frames=0):
        """Feed a still hand with seeded jitter; return output x samples."""
        p = Probe(settings=settings)
        try:
            xs = []
            for i in range(frames):
                dx, dy = jitter[i]
                h = pose_point()
                h.index_tip = (0.44 + dx, 0.38 + dy)
                h.landmarks[8] = h.index_tip
                _, out = p.feed(h)
                xs.append(out.pointer_position[0])
                p.advance(1.0 / 60.0)
            return xs
        finally:
            p.close()

    @staticmethod
    def _std(vals):
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        return math.sqrt(var)

    def test_still_hand_jitter_lower_than_ema(self):
        rng = random.Random(20261019)
        jitter = [(rng.uniform(-0.004, 0.004), rng.uniform(-0.004, 0.004))
                  for _ in range(80)]
        ema = self._run_static(GestureSettings(), jitter)
        oe = self._run_static(self._OE, jitter)
        # compare settled halves: One Euro must hold still tighter than EMA
        self.assertLess(self._std(oe[40:]), self._std(ema[40:]))

    @staticmethod
    def _run_step(settings, target=0.64, start=0.44):
        p = Probe(settings=settings)
        try:
            def feed_at(x):
                h = pose_point()
                h.index_tip = (x, 0.38)
                h.landmarks[8] = h.index_tip
                _, out = p.feed(h)
                p.advance(1.0 / 60.0)
                return out.pointer_position[0]

            feed_at(start)
            for _ in range(10):                      # settle at start
                feed_at(start)
            for n in range(1, 41):                   # step response
                x = feed_at(target)
                if x >= start + 0.9 * (target - start):
                    return n
            return 41                                # never caught 90%
        finally:
            p.close()

    def test_fast_move_lag_not_worse_than_ema(self):
        ema = self._run_step(GestureSettings())
        oe = self._run_step(self._OE)
        self.assertLessEqual(oe, ema)

    def test_dt_clamp_paths_never_produce_nan(self):
        p = Probe(settings=self._OE)
        try:
            weird_deltas = [0.0, 1e-12, 5.0, -1.0, 0.04]
            p.feed(pose_point())                     # initialize filter
            for d in weird_deltas:
                p.t += d
                h = pose_point()
                h.index_tip = (0.50, 0.42)
                h.landmarks[8] = h.index_tip
                _, out = p.feed(h)
                self.assertTrue(
                    all(math.isfinite(v) for v in out.pointer_position),
                    f"non-finite output for dt={d}: {out.pointer_position}",
                )
        finally:
            p.close()


# ---------------------------------------------------------------------------
class TestStage4Plumbing(unittest.TestCase):
    """Config defaults stay legacy and main.py maps every new key."""

    def test_config_and_settings_defaults_are_legacy(self):
        from memory.config_manager import DEFAULT_GESTURE_CFG

        self.assertIs(DEFAULT_GESTURE_CFG["gesture_pinch_normalized"], False)
        self.assertEqual(DEFAULT_GESTURE_CFG["gesture_pose_earn_frames"], 0)
        self.assertEqual(DEFAULT_GESTURE_CFG["gesture_two_hand_min_separation"], 0.0)
        self.assertEqual(DEFAULT_GESTURE_CFG["gesture_two_hand_grace_frames"], 1)
        self.assertEqual(DEFAULT_GESTURE_CFG["gesture_smoothing_filter"], "ema")

        s = GestureSettings()
        self.assertIs(s.pinch_normalized, False)
        self.assertEqual(s.pose_earn_frames, 0)
        self.assertEqual(s.two_hand_min_separation, 0.0)
        self.assertEqual(s.two_hand_grace_frames, 1)
        self.assertEqual(s.smoothing_filter, "ema")

    def test_main_maps_every_stage4_key(self):
        src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        for key in (
            "gesture_pinch_normalized",
            "gesture_pose_earn_frames",
            "gesture_two_hand_min_separation",
            "gesture_two_hand_grace_frames",
            "gesture_smoothing_filter",
        ):
            self.assertIn(key, src, f"main.py does not map {key}")


if __name__ == "__main__":
    unittest.main()
