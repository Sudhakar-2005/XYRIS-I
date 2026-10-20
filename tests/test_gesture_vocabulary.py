"""
Stage 5 — new gesture vocabulary behind flags (GESTURE_INTEGRATION_ROADMAP.md
Stage 5; GESTURE_TEST_PLAN.md G3/G9). Every flag defaults OFF ⇒ legacy flow
byte-for-byte; only explicit construction turns one on (P2).

Sources (audit-before-implementation, hololens holo.html):
  * PEACE pose rule   — holo.html:370-372/381 (span-normalized tips)
  * peace 8-frame     — holo.html:528-531 (streak === 8 exact, cooldown >2500)
  * tap gate          — holo.html:576-583 (release <450 ms AND palm drift <60 px)
  * flick peak        — holo.html:559-567/574 (dt>=8ms, px/16ms, sp > 4)

Safety (SECURITY_AND_PRIVACY.md R1): DISMISS and RESET are reversible
UI-state-only intents, disjoint from SafetyController.CONFIRMATION_REQUIRED;
engine._DISPATCH_INTENTS still contains only SELECT, so nothing new is
dispatchable until Stage 6 registers explicit targets.
"""
import math
import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.spatial.interaction_state import (
    GestureIntent,
    GestureSettings,
    GestureType,
    InteractionState,
)
from core.spatial.safety_controller import SafetyController
from gesture_fixtures import (
    make_hand,
    pose_open,
    pose_peace,
    pose_pinch,
    pose_point,
    scale_hand,
)
from test_gesture_probe import Probe

SCALE = 1280.0  # virtual px frame (one_euro_px_scale convention)


def _px(px: float) -> float:
    """Virtual-pixel offset as a normalized coordinate delta."""
    return px / SCALE


def _pinch_at(x: float, y: float = 0.54, dist: float = 0.04):
    """Pinch pose whose index tip (and landmark 8) sits at (x, y).

    Thumb stays put; as the index moves the pinch distance changes but stays
    well under the 0.05 gate for the small deltas used here.
    """
    h = pose_pinch(dist)
    h.index_tip = (x, y)
    h.landmarks[8] = (x, y)
    return h


class VocabularyCase(unittest.TestCase):
    def setUp(self):
        self.p = None

    def tearDown(self):
        if self.p is not None:
            self.p.close()

    def probe(self, **settings):
        self.p = Probe(settings=GestureSettings(**settings))
        return self.p


# ---------------------------------------------------------------------------
class TestPeaceDetection(VocabularyCase):
    """Stage 5a — PEACE detection is pose-only, unflagged, tracker-confident."""

    def test_peace_recognized_with_hand_confidence(self):
        p = self.probe()
        ev, out = p.feed(pose_peace())
        self.assertEqual(ev.gesture, GestureType.PEACE)
        self.assertAlmostEqual(ev.confidence, 0.9)  # hand.confidence (tracker)
        self.assertEqual(out.intent, GestureIntent.NONE)  # no flag ⇒ no action
        self.assertEqual(p.sm.state, InteractionState.TRACKING)

    def test_peace_rule_is_span_normalized(self):
        # holo.html:370-381 is homogeneous in the span: scaled hands keep
        # identical tip ratios, so the pose must classify at any size.
        # pinch_normalized=True because the LEGACY absolute pinch gate is
        # deliberately scale-variant (that is what Stage 4a fixed); here we
        # are testing the peace rule's span math, not the legacy lane.
        p = self.probe(pinch_normalized=True)
        for s in (0.5, 1.5):
            ev, _ = p.feed(scale_hand(pose_peace(), s))
            self.assertEqual(ev.gesture, GestureType.PEACE, f"scale={s}")

    def test_other_poses_are_not_peace(self):
        p = self.probe()
        for pose, expected in (
            (pose_open(), GestureType.OPEN_PALM),
            (pose_point(), GestureType.POINT),
            (pose_pinch(0.04), GestureType.PINCH),
        ):
            ev, _ = p.feed(pose)
            self.assertEqual(ev.gesture, expected)
            self.assertNotEqual(ev.gesture, GestureType.PEACE)


# ---------------------------------------------------------------------------
class TestTapAction(VocabularyCase):
    """G3 — tap SELECT on quick release (flag on); legacy flow (flag off)."""

    def test_release_449ms_59px_is_tap_select(self):
        p = self.probe(tap_action=True)
        p.feed(pose_pinch(0.04))                      # PINCH_DETECTED @t0
        p.advance(0.449)                              # 449 ms < 450 ms
        h = pose_open()
        h.palm_center = (h.palm_center[0] + _px(59), h.palm_center[1])
        _, out = p.feed(h)
        self.assertEqual(out.intent, GestureIntent.SELECT)
        self.assertIs(out.metadata.get("tap"), True)
        self.assertEqual(p.sm.state, InteractionState.TRACKING)

    def test_release_451ms_is_not_tap(self):
        p = self.probe(tap_action=True)
        p.feed(pose_pinch(0.04))
        p.advance(0.451)                              # past the 450 ms window
        _, out = p.feed(pose_open())
        self.assertEqual(out.intent, GestureIntent.NONE)
        self.assertEqual(p.sm.state, InteractionState.TRACKING)

    def test_drift_61px_is_not_tap(self):
        p = self.probe(tap_action=True)
        p.feed(pose_pinch(0.04))
        p.advance(0.10)
        h = pose_open()
        h.palm_center = (h.palm_center[0] + _px(61), h.palm_center[1])
        _, out = p.feed(h)
        self.assertEqual(out.intent, GestureIntent.NONE)

    def test_flag_off_quick_release_is_none(self):
        p = self.probe()                              # tap_action=False
        p.feed(pose_pinch(0.04))
        p.advance(0.049)
        _, out = p.feed(pose_open())
        self.assertEqual(out.intent, GestureIntent.NONE)
        self.assertEqual(p.sm.state, InteractionState.TRACKING)

    def test_hold_select_deferred_yields_single_select_at_release(self):
        # tap ON replaces the philosophy: hold-SELECT is deferred, so the
        # whole pinch produces exactly one SELECT (at release), while the
        # state machine still walks PINCH_DETECTED -> SELECTING (drag ready).
        p = self.probe(tap_action=True)
        _, out = p.feed(pose_pinch(0.04))             # entry: deferred
        self.assertEqual(out.intent, GestureIntent.NONE)
        p.advance(0.12)
        _, out = p.feed(pose_pinch(0.04))             # hold >= 100 ms
        self.assertEqual(out.intent, GestureIntent.NONE)  # deferred
        self.assertEqual(p.sm.state, InteractionState.SELECTING)
        p.advance(0.30)                               # release at 420 ms
        _, out = p.feed(pose_open())
        self.assertEqual(out.intent, GestureIntent.SELECT)
        self.assertIs(out.metadata.get("tap"), True)
        self.assertEqual(p.sm.state, InteractionState.HOVERING)

    def test_hover_entry_select_deferred_under_tap(self):
        p = self.probe(tap_action=True)
        p.feed(pose_point())                          # HOVERING
        _, out = p.feed(pose_pinch(0.04))             # was immediate SELECT
        self.assertEqual(out.intent, GestureIntent.NONE)  # deferred
        self.assertEqual(p.sm.state, InteractionState.PINCH_DETECTED)
        p.advance(0.05)
        _, out = p.feed(pose_open())
        self.assertEqual(out.intent, GestureIntent.SELECT)
        self.assertIs(out.metadata.get("tap"), True)

    def test_drag_path_still_works_with_tap_on(self):
        p = self.probe(tap_action=True)
        p.feed(_pinch_at(0.44))
        p.advance(0.12)
        _, out = p.feed(_pinch_at(0.46))              # SELECTING (deferred)
        self.assertEqual(out.intent, GestureIntent.NONE)
        p.advance(0.05)
        _, out = p.feed(_pinch_at(0.50))              # first SELECTING frame
        self.assertEqual(out.intent, GestureIntent.DRAG_START)
        self.assertEqual(p.sm.state, InteractionState.DRAGGING)
        p.advance(0.05)
        _, out = p.feed(pose_open())                  # slow release
        self.assertEqual(out.intent, GestureIntent.DRAG_END)


# ---------------------------------------------------------------------------
class TestFlickDismiss(VocabularyCase):
    """G3 — flick observation on pinch release; DISMISS only from DRAGGING."""

    def _drag_then(self, p, third_pos: float):
        """Arm, hold into SELECTING, then one SELECTING frame at third_pos
        which DRAG_STARTs (delta vs the (0,0) pointer origin). Trail pairs:
        sp1 = .02*1280/120*16 = 3.41, sp2 = |third-.46|*1280*16/50."""
        p.feed(_pinch_at(0.44))                       # arm @ t0
        p.advance(0.12)
        _, out = p.feed(_pinch_at(0.46))              # hold >=100 -> SELECT
        self.assertEqual(out.intent, GestureIntent.SELECT)
        p.advance(0.05)
        _, out = p.feed(_pinch_at(third_pos))         # DRAG_START
        self.assertEqual(out.intent, GestureIntent.DRAG_START)
        p.advance(0.05)

    def test_drag_release_with_flick_emits_dismiss(self):
        p = self.probe(flick_detect=True)
        # frame2 sp = .02*1280/120*16 = 3.41; frame3 sp = .04*1280/50*16
        # = 16.38 > 4 ⇒ flick
        self._drag_then(p, 0.50)
        ev, out = p.feed(pose_open())
        self.assertGreater(out.metadata.get("flick", 0.0), 4.0)
        self.assertEqual(out.metadata.get("speed"), out.metadata["flick"])
        self.assertEqual(out.intent, GestureIntent.DISMISS)
        self.assertEqual(p.sm.state, InteractionState.HOVERING)

    def test_flick_boundary_plus_epsilon_dismisses(self):
        p = self.probe(flick_detect=True)
        # pair2: dt=50 ms, sp = delta*1280*16/50
        delta = 4.01 * 50 / (16 * SCALE)
        self._drag_then(p, 0.46 + delta)
        _, out = p.feed(pose_open())
        self.assertAlmostEqual(out.metadata.get("flick", 0.0), 4.01, places=6)
        self.assertEqual(out.intent, GestureIntent.DISMISS)

    def test_flick_boundary_minus_epsilon_is_drag_end(self):
        p = self.probe(flick_detect=True)
        delta = 3.99 * 50 / (16 * SCALE)
        self._drag_then(p, 0.46 + delta)
        _, out = p.feed(pose_open())
        # peak 3.99 < 4 ⇒ no flick observed ⇒ no metadata, legacy DRAG_END
        self.assertNotIn("flick", out.metadata)
        self.assertEqual(out.intent, GestureIntent.DRAG_END)

    def test_slow_drag_release_with_flag_on_is_drag_end(self):
        p = self.probe(flick_detect=True)
        p.feed(_pinch_at(0.44))
        p.advance(0.12)
        _, out = p.feed(_pinch_at(0.46))              # SELECT
        self.assertEqual(out.intent, GestureIntent.SELECT)
        p.advance(0.05)
        _, out = p.feed(_pinch_at(0.465))             # DRAG_START, sp 2.05
        self.assertEqual(out.intent, GestureIntent.DRAG_START)
        p.advance(0.05)
        _, out = p.feed(pose_open())                  # peak 3.41 < 4
        self.assertEqual(out.intent, GestureIntent.DRAG_END)
        self.assertNotIn("flick", out.metadata)

    def test_flag_off_drag_release_is_drag_end(self):
        p = self.probe()                              # flick_detect=False
        self._drag_then(p, 0.50)                      # same fast motion
        ev, out = p.feed(pose_open())
        self.assertEqual(out.intent, GestureIntent.DRAG_END)
        self.assertNotIn("flick", ev.metadata)

    def test_fast_release_outside_drag_is_metadata_only(self):
        # Recognizer-level observation (§4.1): the release event carries
        # metadata["flick"] but no action — G3 "DISMISS metadata only".
        p = self.probe(flick_detect=True)
        p.feed(_pinch_at(0.44))                       # arm
        p.advance(0.02)
        p.feed(_pinch_at(0.49))                       # held frame, trail .05
        p.advance(0.02)
        ev, out = p.feed(pose_open())                 # release
        self.assertAlmostEqual(ev.metadata.get("flick", 0.0), 51.2, places=6)
        self.assertEqual(out.intent, GestureIntent.NONE)  # not dragging
        self.assertEqual(p.sm.state, InteractionState.TRACKING)


# ---------------------------------------------------------------------------
class TestPeaceReset(VocabularyCase):
    """G9 — RESET on 8 consecutive frames, 2500 ms cooldown, flag-gated."""

    def _hold(self, p, n, start_advance=0.02):
        outs = []
        for i in range(n):
            if i:
                p.advance(start_advance)
            _, out = p.feed(pose_peace())
            outs.append(out)
        return outs

    def test_seven_frames_never_reset(self):
        p = self.probe(peace_reset=True)
        for out in self._hold(p, 7):
            self.assertEqual(out.intent, GestureIntent.NONE)
        self.assertEqual(p.sm.state, InteractionState.TRACKING)

    def test_eighth_frame_fires_reset_exactly(self):
        p = self.probe(peace_reset=True)
        self._hold(p, 7)
        p.advance(0.02)
        _, out = p.feed(pose_peace())                 # 8th frame
        self.assertEqual(out.intent, GestureIntent.RESET)
        self.assertEqual(out.metadata.get("peace_streak"), 8)
        self.assertEqual(p.sm.state, InteractionState.TRACKING)  # untouched

    def test_continuous_hold_past_8_does_not_refire(self):
        # holo.html:530 is strict equality (peaceN === 8): a hold that keeps
        # going fires once, then needs a break + re-earn.
        p = self.probe(peace_reset=True)
        self._hold(p, 10)
        p.advance(0.02)
        _, out = p.feed(pose_peace())                 # 11th frame
        self.assertNotEqual(out.intent, GestureIntent.RESET)

    def test_refire_blocked_at_2499ms(self):
        p = self.probe(peace_reset=True)
        self._hold(p, 8)                              # fires at t0+0.14
        fire_t = p.t
        p.feed(pose_point())                          # break the streak
        p.advance(2.499 - 0.14)                       # 8th re-earn @ fire+2499
        outs = self._hold(p, 8)
        self.assertNotEqual(outs[-1].intent, GestureIntent.RESET)

    def test_refire_allowed_at_2501ms(self):
        p = self.probe(peace_reset=True)
        self._hold(p, 8)
        p.feed(pose_point())                          # break the streak
        p.advance(2.501 - 0.14)                       # 8th re-earn @ fire+2501
        outs = self._hold(p, 8)
        self.assertEqual(outs[-1].intent, GestureIntent.RESET)

    def test_flag_off_never_resets(self):
        p = self.probe()                              # peace_reset=False
        for out in self._hold(p, 20):
            self.assertEqual(out.intent, GestureIntent.NONE)

    def test_non_peace_frame_breaks_streak(self):
        p = self.probe(peace_reset=True)
        self._hold(p, 7)
        p.feed(pose_point())                          # breaks
        for out in self._hold(p, 7):
            self.assertEqual(out.intent, GestureIntent.NONE)

    def test_low_confidence_frame_also_breaks_streak(self):
        # Streak reset happens BEFORE the confidence gate (holo else-law):
        # an uncertain frame between peace poses must reset the count.
        p = self.probe(peace_reset=True)
        self._hold(p, 7)
        p.feed(make_hand(palm_center=(0.35, 0.56)),
               make_hand(palm_center=(0.65, 0.56)))   # NONE @0.4
        for out in self._hold(p, 7):
            self.assertEqual(out.intent, GestureIntent.NONE)


# ---------------------------------------------------------------------------
class TestVocabularySafety(unittest.TestCase):
    """R1 — gesture ≠ authorization: new vocabulary stays reversible and
    disjoint from CONFIRMATION_REQUIRED; nothing new is dispatchable."""

    def test_new_enum_members_exist(self):
        self.assertEqual(GestureType.PEACE.value, "peace")
        self.assertEqual(GestureIntent.DISMISS.value, "dismiss")
        self.assertEqual(GestureIntent.RESET.value, "reset")

    def test_no_gesture_intent_is_a_restricted_action(self):
        safety = SafetyController()
        restricted = SafetyController.CONFIRMATION_REQUIRED
        for intent in GestureIntent:
            self.assertNotIn(
                intent.value, restricted,
                f"gesture intent {intent.value!r} collides with a "
                "confirmation-required action name",
            )
            self.assertFalse(safety.requires_confirmation(intent.value))

    def test_dismiss_and_reset_pass_safety_validation(self):
        from core.spatial.interaction_state import GestureEvent

        safety = SafetyController()
        for intent in (GestureIntent.DISMISS, GestureIntent.RESET):
            event = GestureEvent(
                gesture=GestureType.PEACE, intent=intent, confidence=0.9
            )
            is_safe, reason = safety.validate_event(event)
            self.assertTrue(is_safe, reason)

    def test_engine_dispatch_whitelist_unchanged(self):
        # Stage 6 registers targets deliberately; Stage 5 must not widen
        # what the engine will act on.
        from core.spatial.engine import SpatialInteractionEngine

        self.assertEqual(
            SpatialInteractionEngine._DISPATCH_INTENTS, {GestureIntent.SELECT}
        )

    def test_config_defaults_are_legacy_and_main_maps_keys(self):
        from memory.config_manager import DEFAULT_GESTURE_CFG

        self.assertIs(DEFAULT_GESTURE_CFG["gesture_tap_action"], False)
        self.assertIs(DEFAULT_GESTURE_CFG["gesture_flick_detect"], False)
        self.assertIs(DEFAULT_GESTURE_CFG["gesture_peace_reset"], False)

        s = GestureSettings()
        self.assertIs(s.tap_action, False)
        self.assertIs(s.flick_detect, False)
        self.assertIs(s.peace_reset, False)
        self.assertEqual(s.tap_max_ms, 450.0)
        self.assertEqual(s.tap_max_drift, 0.046875)
        self.assertEqual(s.flick_threshold, 4.0)
        self.assertEqual(s.peace_cooldown_ms, 2500.0)

        src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        for key in (
            "gesture_tap_action",
            "gesture_flick_detect",
            "gesture_peace_reset",
        ):
            self.assertIn(key, src, f"main.py does not map {key}")


if __name__ == "__main__":
    unittest.main()
