"""
Stage 3 — gesture probe battery: golden baseline of CURRENT behavior
(GESTURE_INTEGRATION_ROADMAP.md Stage 3, GESTURE_TEST_PLAN.md L1).

Method ported from holo-gestures' ?probe=1 (synthetic hands through the real
engine, GESTURE_SOURCE_AUDIT.md §8). Every expectation below was verified by
executing the pipeline (not guessed) and locks semantics so later stages
cannot drift them silently.

Stage 3 originally locked three defects as current behavior; Stage 4 repaired
them (flagless bug fixes, kept separate from the flagged G1/G2/G5/G6 gates)
and these tests were flipped deliberately and loudly:
  * TRACKING -> PINCH_DETECTED was missing from VALID_TRANSITIONS
    (→ test_pinch_from_tracking_enters_validation, Stage 4f).
  * a stationary held pinch fell through to FIST/cancel from frame 2
    (→ test_held_pinch_from_tracking_validates_and_selects, Stage 4f).
  * LOW_CONFIDENCE was a trap with no exit branch
    (→ test_low_confidence_recovers_on_confident_gesture, Stage 4e; likewise
     test_two_hand_events_recover_but_produce_no_intent).
Stage 5 added PEACE detection (holo.html:381, tracker-derived confidence,
unflagged pose-only per XYRIS_GESTURE_ARCHITECTURE.md §4.1) and flipped one
more golden loudly — the documented Stage 5 gap this file locked:
  * peace pose is now recognized (→ test_peace_pose_recognized_stage5); the
    two confidence-gate tests that used peace as their low-confidence
    generator switched to the two-hand first frame (NONE @0.4), which is
    unchanged. No other golden expectation changed — everything else here
    still asserts the original Stage 3 locked behavior.
"""
import math
import os
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.spatial.gesture_interpreter import GestureInterpreter
from core.spatial.gesture_recognizer import GestureRecognizer
from core.spatial.interaction_state import (
    GestureIntent,
    GestureSettings,
    GestureType,
    InteractionState,
    InteractionStateMachine,
)
from gesture_fixtures import (
    make_hand,
    pose_fist,
    pose_open,
    pose_peace,
    pose_pinch,
    pose_point,
)


class Probe:
    """Fresh recognizer+interpreter under an injectable fake clock."""

    def __init__(self, active: bool = True, t0: float = 1000.0, settings=None):
        self.t = t0
        self._patch = mock.patch("time.time", new=lambda: self.t)
        self._patch.start()
        self.sm = InteractionStateMachine()
        if active:
            self.sm.transition(InteractionState.ACTIVATING)
            self.sm.transition(InteractionState.TRACKING)
        s = settings or GestureSettings()
        self.rec = GestureRecognizer(s)
        self.itp = GestureInterpreter(self.sm, s)

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def feed(self, *hands):
        ev = self.rec.recognize(list(hands), self.t)
        out = self.itp.interpret(ev)
        return ev, out

    def close(self) -> None:
        self._patch.stop()


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.p = Probe()

    def tearDown(self):
        self.p.close()


# ---------------------------------------------------------------------------
class TestProbePoseGates(ProbeCase):
    """handMetrics-equivalent pose rules as implemented by XYRIS (legacy)."""

    def test_no_hands_is_none_zero(self):
        ev, out = self.p.feed()
        self.assertEqual(ev.gesture, GestureType.NONE)
        self.assertEqual(ev.confidence, 0.0)
        self.assertEqual(out.intent, GestureIntent.NONE)

    def test_open_palm(self):
        ev, out = self.p.feed(pose_open())
        self.assertEqual(ev.gesture, GestureType.OPEN_PALM)
        self.assertAlmostEqual(ev.confidence, 0.85)
        self.assertEqual(out.intent, GestureIntent.PAUSE)
        self.assertEqual(self.p.sm.state, InteractionState.PAUSED)

    def test_fist(self):
        ev, out = self.p.feed(pose_fist())
        self.assertEqual(ev.gesture, GestureType.FIST)
        self.assertAlmostEqual(ev.confidence, 0.8)
        self.assertEqual(out.intent, GestureIntent.CANCEL)
        self.assertEqual(self.p.sm.state, InteractionState.CANCELLED)

    def test_point(self):
        ev, out = self.p.feed(pose_point())
        self.assertEqual(ev.gesture, GestureType.POINT)
        self.assertAlmostEqual(ev.confidence, 0.9)
        self.assertEqual(out.intent, GestureIntent.POINTER_MOVE)
        self.assertEqual(self.p.sm.state, InteractionState.HOVERING)

    def test_peace_pose_recognized_stage5(self):
        # Stage 5 flip (was: test_peace_pose_is_unrecognized_today — the
        # documented "GestureType has no PEACE yet" gap). Detection is
        # pose-only and unflagged (arch §4.1); confidence is the tracker's
        # own hand confidence (fixtures: 0.9) — no invented number. The
        # RESET *action* stays gated by peace_reset (tests in
        # test_gesture_vocabulary.py); with the flag off this event has
        # intent NONE and changes no state.
        ev, out = self.p.feed(pose_peace())
        self.assertEqual(ev.gesture, GestureType.PEACE)
        self.assertAlmostEqual(ev.confidence, 0.9)  # hand.confidence
        self.assertEqual(out.intent, GestureIntent.NONE)
        self.assertEqual(self.p.sm.state, InteractionState.TRACKING)

    def test_pinch_under_threshold_first_frame(self):
        ev, _ = self.p.feed(pose_pinch(0.04))
        self.assertEqual(ev.gesture, GestureType.PINCH)
        # confidence formula gesture_recognizer.py:94 => 0.8 + (1-.04/.05)*.2
        self.assertAlmostEqual(ev.confidence, 0.84)
        self.assertEqual(ev.intent, GestureIntent.NONE)

    def test_pinch_above_threshold_not_pinching(self):
        # legacy gate is strict "<" with no OUT hysteresis (0.052 > 0.05;
        # the exact-boundary case is float-noise dependent, see released test)
        ev, _ = self.p.feed(pose_pinch(0.052))
        self.assertNotEqual(ev.gesture, GestureType.PINCH)
        self.assertEqual(ev.gesture, GestureType.FIST)

    def test_default_intent_is_enum_none(self):
        _, out = self.p.feed(pose_peace())
        self.assertIsInstance(out.intent, GestureIntent)


# ---------------------------------------------------------------------------
class TestProbePinchLifecycle(ProbeCase):
    def test_pinch_from_tracking_enters_validation(self):
        # Stage 4f repair: TRACKING -> PINCH_DETECTED edge added; the pinch
        # from neutral now enters the hold-validation state (was: invalid
        # transition + stuck TRACKING, locked by the Stage 3 baseline).
        ev, out = self.p.feed(pose_pinch(0.04))
        self.assertEqual(ev.gesture, GestureType.PINCH)
        self.assertEqual(out.intent, GestureIntent.NONE)  # "waiting for validation"
        self.assertEqual(self.p.sm.state, InteractionState.PINCH_DETECTED)

    def test_held_pinch_from_tracking_validates_and_selects(self):
        # Stage 4f repair chain: held pinch keeps emitting PINCH (was: fell
        # through to FIST and cancelled from frame 2), so the 100ms hold path
        # is reachable from a neutral start.
        self.p.feed(pose_pinch(0.04))               # PINCH -> PINCH_DETECTED
        self.p.advance(0.02)
        ev, out = self.p.feed(pose_pinch(0.04))     # still held, under 100ms
        self.assertEqual(ev.gesture, GestureType.PINCH)
        self.assertEqual(out.intent, GestureIntent.NONE)
        self.assertEqual(self.p.sm.state, InteractionState.PINCH_DETECTED)
        self.p.advance(0.12)
        _, out = self.p.feed(pose_pinch(0.04))      # hold >= 100ms
        self.assertEqual(out.intent, GestureIntent.SELECT)
        self.assertEqual(self.p.sm.state, InteractionState.SELECTING)

    def test_pinch_from_hover_selects_immediately(self):
        # HOVERING -> PINCH_DETECTED is a valid edge; current semantics fire
        # SELECT on the very first pinch frame (tap-act-from-hover works).
        self.p.feed(pose_point())
        ev, out = self.p.feed(pose_pinch(0.04))
        self.assertEqual(ev.gesture, GestureType.PINCH)
        self.assertEqual(out.intent, GestureIntent.SELECT)
        self.assertEqual(self.p.sm.state, InteractionState.PINCH_DETECTED)

    def test_pinch_held_under_100ms_then_released_back_to_tracking(self):
        self.p.feed(pose_point())
        self.p.feed(pose_pinch(0.04))               # SELECT, PINCH_DETECTED @t
        self.p.advance(0.04)
        ev, out = self.p.feed(pose_open())          # released before 100ms
        self.assertEqual(ev.gesture, GestureType.OPEN_PALM)
        self.assertEqual(out.intent, GestureIntent.NONE)
        self.assertEqual(self.p.sm.state, InteractionState.TRACKING)

    def test_pinch_held_120ms_selects_again_and_enters_selecting(self):
        self.p.feed(pose_point())
        self.p.feed(pose_pinch(0.04))               # SELECT, PINCH_DETECTED @t
        self.p.advance(0.12)
        _, out = self.p.feed(pose_pinch(0.04))      # hold >= min_hold_duration
        self.assertEqual(out.intent, GestureIntent.SELECT)
        self.assertEqual(self.p.sm.state, InteractionState.SELECTING)

    def test_selecting_pinch_movement_starts_drag_then_drag_end(self):
        self.p.feed(pose_point())
        self.p.feed(pose_pinch(0.04))
        self.p.advance(0.12)
        self.p.feed(pose_pinch(0.04))               # -> SELECTING
        moved = pose_pinch(0.04)
        moved.index_tip = (0.52, 0.54)
        moved.landmarks[8] = (0.52, 0.54)
        _, out = self.p.feed(moved)                 # delta > movement_threshold
        self.assertEqual(out.intent, GestureIntent.DRAG_START)
        self.assertEqual(self.p.sm.state, InteractionState.DRAGGING)
        _, out = self.p.feed(moved)
        self.assertEqual(out.intent, GestureIntent.DRAG_MOVE)
        _, out = self.p.feed(pose_open())           # release
        self.assertEqual(out.intent, GestureIntent.DRAG_END)
        self.assertEqual(self.p.sm.state, InteractionState.HOVERING)

    def test_pinch_cooldown_blocks_rearm_within_300ms(self):
        # recognizer-internal latch/cooldown, independent of interpreter state
        ev, _ = self.p.feed(pose_pinch(0.04))
        self.assertEqual(ev.gesture, GestureType.PINCH)      # armed at t0
        ev, _ = self.p.feed(pose_fist())
        self.assertEqual(ev.gesture, GestureType.FIST)       # release
        self.p.advance(0.10)
        ev, _ = self.p.feed(pose_pinch(0.04))                # 100ms < cooldown
        self.assertNotEqual(ev.gesture, GestureType.PINCH)   # blocked -> FIST
        self.p.advance(0.25)
        ev, _ = self.p.feed(pose_pinch(0.04))                # 350ms > cooldown
        self.assertEqual(ev.gesture, GestureType.PINCH)

    def test_pinch_releases_only_at_or_above_threshold_no_out_hysteresis(self):
        # hover first so pointer history exists (drag branch path)
        self.p.feed(pose_point())
        ev, _ = self.p.feed(pose_pinch(0.04))
        self.assertEqual(ev.gesture, GestureType.PINCH)
        ev, _ = self.p.feed(pose_pinch(0.049))       # still below 0.05
        self.assertEqual(ev.gesture, GestureType.PINCH)
        ev, _ = self.p.feed(pose_pinch(0.051))       # crosses 0.05 -> released
        self.assertNotEqual(ev.gesture, GestureType.PINCH)


# ---------------------------------------------------------------------------
class TestProbeConfidenceGate(ProbeCase):
    @staticmethod
    def _low_conf_event_pair():
        """First two-hand frame: NONE @0.4 (< the 0.7 gate) — the unchanged
        low-confidence generator (Stage 5 switched these tests off peace,
        which is now recognized with tracker confidence)."""
        a = make_hand(palm_center=(0.35, 0.56))
        b = make_hand(palm_center=(0.65, 0.56))
        return a, b

    def test_low_confidence_event_transitions_and_blocks_intent(self):
        ev, out = self.p.feed(*self._low_conf_event_pair())   # conf .4 < .7
        self.assertEqual(self.p.sm.state, InteractionState.LOW_CONFIDENCE)
        self.assertEqual(out.intent, GestureIntent.NONE)
        self.assertEqual(out.confidence, ev.confidence)

    def test_low_confidence_recovers_on_confident_gesture(self):
        # Stage 4e repair: LOW_CONFIDENCE is no longer a trap — a confident
        # real gesture restores TRACKING and is re-interpreted immediately
        # (was: every later gesture passed through raw forever; locked by the
        # Stage 3 baseline).
        self.p.feed(*self._low_conf_event_pair())
        self.assertEqual(self.p.sm.state, InteractionState.LOW_CONFIDENCE)
        ev, out = self.p.feed(pose_point())
        self.assertEqual(ev.gesture, GestureType.POINT)
        self.assertEqual(ev.confidence, 0.9)
        self.assertEqual(out.intent, GestureIntent.POINTER_MOVE)
        self.assertEqual(self.p.sm.state, InteractionState.HOVERING)
        _, out = self.p.feed(pose_point())
        self.assertEqual(out.intent, GestureIntent.POINTER_MOVE)
        self.assertEqual(self.p.sm.state, InteractionState.HOVERING)


# ---------------------------------------------------------------------------
class TestProbeTwoHand(ProbeCase):
    @staticmethod
    def _pair(d1: float, d2: float, angle2: float = 0.0):
        a1 = make_hand(palm_center=(0.5 - d1 / 2, 0.56))
        b1 = make_hand(palm_center=(0.5 + d1 / 2, 0.56))
        a2 = make_hand(
            palm_center=(0.5 - (d2 / 2) * math.cos(angle2),
                         0.56 - (d2 / 2) * math.sin(angle2))
        )
        b2 = make_hand(
            palm_center=(0.5 + (d2 / 2) * math.cos(angle2),
                         0.56 + (d2 / 2) * math.sin(angle2))
        )
        return (a1, b1), (a2, b2)

    def test_first_two_hand_frame_is_none_low_confidence(self):
        (a, b), _ = self._pair(0.30, 0.30)
        ev, out = self.p.feed(a, b)
        self.assertEqual(ev.gesture, GestureType.NONE)
        self.assertAlmostEqual(ev.confidence, 0.4)
        self.assertEqual(self.p.sm.state, InteractionState.LOW_CONFIDENCE)

    def test_spread_and_contract_recognized_after_first_frame(self):
        (a1, b1), (a2, b2) = self._pair(0.30, 0.40)
        self.p.feed(a1, b1)                          # history baseline
        ev, _ = self.p.feed(a2, b2)                  # hands moving apart
        self.assertEqual(ev.gesture, GestureType.SPREAD)
        self.assertAlmostEqual(ev.confidence, 0.75)
        (a3, b3), _ = self._pair(0.30, 0.40)        # back inward to 0.30
        ev, _ = self.p.feed(a3, b3)
        self.assertEqual(ev.gesture, GestureType.CONTRACT)

    def test_rotate_isolated_when_distance_constant(self):
        (a1, b1), _ = self._pair(0.30, 0.30)
        self.p.feed(a1, b1)
        _, (a2, b2) = self._pair(0.30, 0.30, angle2=0.2)   # same dist, angle+
        ev, _ = self.p.feed(a2, b2)
        self.assertEqual(ev.gesture, GestureType.ROTATE)
        self.assertAlmostEqual(ev.confidence, 0.7)
        self.assertIn(ev.metadata.get("intent"), ("rotate_cw", "rotate_ccw"))

    def test_two_hand_events_recover_but_produce_no_intent(self):
        # Stage 4e repairs the recovery (state returns to TRACKING); the
        # interpreter still has no zoom/rotate intent branches and the first
        # frame still trips the conf gate (0.4 < 0.7) — so two-hand gestures
        # remain intent-less, honestly.
        (a1, b1), (a2, b2) = self._pair(0.30, 0.40)
        self.p.feed(a1, b1)
        ev, out = self.p.feed(a2, b2)
        self.assertEqual(ev.gesture, GestureType.SPREAD)
        self.assertEqual(out.intent, GestureIntent.NONE)
        self.assertEqual(out.gesture, GestureType.NONE)  # raw re-interpretation
        self.assertEqual(self.p.sm.state, InteractionState.TRACKING)


# ---------------------------------------------------------------------------
class TestProbePauseCancel(ProbeCase):
    def test_point_then_palm_pause_and_resume(self):
        self.p.feed(pose_point())
        _, out = self.p.feed(pose_open())
        self.assertEqual(out.intent, GestureIntent.PAUSE)
        self.assertEqual(self.p.sm.state, InteractionState.PAUSED)
        _, out = self.p.feed(pose_point())
        self.assertEqual(out.intent, GestureIntent.RESUME)
        self.assertEqual(self.p.sm.state, InteractionState.TRACKING)

    def test_cancelled_state_passes_events_through_raw(self):
        self.p.feed(pose_fist())
        self.assertEqual(self.p.sm.state, InteractionState.CANCELLED)
        ev, out = self.p.feed(pose_open())
        # no CANCELLED branch in the interpreter -> raw event, no intent
        self.assertEqual(ev.gesture, GestureType.OPEN_PALM)
        self.assertEqual(out.intent, GestureIntent.NONE)
        self.assertEqual(self.p.sm.state, InteractionState.CANCELLED)


# ---------------------------------------------------------------------------
class TestProbePointer(ProbeCase):
    def test_first_point_pointer_equals_index_tip_raw(self):
        h = pose_point()
        _, out = self.p.feed(h)
        self.assertEqual(out.pointer_position, h.index_tip)  # empty history

    def test_second_point_is_smoothed_not_raw(self):
        self.p.feed(pose_point())                   # history seeded (0.44, 0.38)
        h = pose_point()
        h.index_tip = (0.54, 0.38)                  # raw moved +0.10 in x
        h.landmarks[8] = (0.54, 0.38)
        _, out = self.p.feed(h)
        self.assertNotEqual(out.pointer_position, h.index_tip)
        # EMA alpha = pointer_smoothing (0.3): last + 0.3 * (raw - last)
        self.assertAlmostEqual(out.pointer_position[0], 0.44 + 0.3 * 0.10, places=9)

    def test_inactive_state_short_circuits(self):
        p2 = Probe(active=False)
        try:
            _, out = p2.feed(pose_point())
            self.assertEqual(out.intent, GestureIntent.NONE)
            self.assertEqual(out.confidence, 0.0)
            self.assertEqual(p2.sm.state, InteractionState.INACTIVE)
        finally:
            p2.close()


# ---------------------------------------------------------------------------
class TestProbePropertyJitter(ProbeCase):
    def test_seeded_jitter_never_raises_and_keeps_enum_types(self):
        rng = random.Random(20261019)
        poses = [pose_open, pose_fist, pose_point, pose_peace,
                 lambda: pose_pinch(0.04)]
        for i in range(120):
            base = poses[i % len(poses)]()
            jittered = [
                (x + rng.uniform(-0.01, 0.01), y + rng.uniform(-0.01, 0.01))
                for (x, y) in base.landmarks
            ]
            base.landmarks = jittered
            base.index_tip = jittered[8]
            base.thumb_tip = jittered[4]
            ev, out = self.p.feed(base)
            self.assertIsInstance(ev.gesture, GestureType)
            self.assertIsInstance(out.intent, GestureIntent)
            self.assertGreaterEqual(ev.confidence, 0.0)
            self.assertLessEqual(ev.confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
