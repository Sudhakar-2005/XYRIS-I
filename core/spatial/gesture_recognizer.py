"""
Gesture Recognizer — Identifies gestures from hand landmarks.

Part of XYRIS I Spatial Screen Interaction Engine V1.
"""

import logging
import math
import time
from collections import deque
from typing import Optional

from core.spatial.interaction_state import (
    GestureType,
    GestureEvent,
    GestureIntent,
    HandLandmarks,
    GestureSettings,
)

logger = logging.getLogger(__name__)


class GestureRecognizer:
    """Recognizes gestures from hand landmark data.

    Uses geometric analysis of finger positions to identify
    point, pinch, open palm, fist, and two-hand gestures.
    """

    def __init__(self, settings: Optional[GestureSettings] = None):
        self._settings = settings or GestureSettings()
        self._last_gesture = GestureType.NONE
        self._last_pinch_time = 0.0
        self._pinch_active = False
        self._pointer_history: deque = deque(maxlen=5)
        self._gesture_stability: deque = deque(maxlen=5)
        # Stage 4 hardening state (flags default off ⇒ unused in legacy runs)
        self._norm_pinch_n = 0              # 4a earn counter (holo pinchN)
        self._last_earned_pose = GestureType.NONE   # 4b pose streak bookkeeping
        self._pose_streak = 0
        self._two_hand_seen = 0             # 4c consecutive two-hand frames
        self._oe: Optional[dict] = None     # 4d One Euro filter state
        # Stage 5 flick observation (flag off ⇒ unused; holo.html:559-567)
        self._flick_trail: deque = deque(maxlen=12)   # (x, y, ts) while pinching
        self._pending_flick: Optional[float] = None   # speed awaiting release event

    def recognize(
        self,
        hands: list[HandLandmarks],
        timestamp: float,
    ) -> GestureEvent:
        """Recognize gesture from detected hands.

        Args:
            hands: List of detected hand landmarks
            timestamp: Current timestamp

        Returns:
            GestureEvent with recognized gesture and intent
        """
        if not hands:
            self._pinch_active = False
            self._pose_streak = 0
            self._last_earned_pose = GestureType.NONE
            self._two_hand_seen = 0
            self._oe = None
            self._flick_trail.clear()
            self._pending_flick = None
            return GestureEvent(
                gesture=GestureType.NONE,
                confidence=0.0,
                timestamp=timestamp,
            )

        primary = hands[0]
        secondary = hands[1] if len(hands) > 1 else None

        # Two-hand gestures
        if secondary:
            self._pose_streak = 0            # a two-hand frame breaks pose earn
            self._two_hand_seen += 1
            return self._recognize_two_hand(primary, secondary, timestamp)

        # Single-hand gestures
        self._two_hand_seen = 0
        event = self._recognize_single_hand(primary, timestamp)
        if self._pending_flick is not None:
            # Stage 5c: the release frame carries the flick observation (no
            # action by itself — §4.1); the interpreter converts it to DISMISS
            # only from a DRAGGING release with flick_detect on.
            event.metadata["flick"] = self._pending_flick
            self._pending_flick = None
        return event

    def _recognize_single_hand(
        self, hand: HandLandmarks, timestamp: float
    ) -> GestureEvent:
        """Recognize single-hand gestures."""
        fingers = self._count_fingers(hand)
        index_extended = fingers["index"]
        middle_extended = fingers["middle"]
        ring_extended = fingers["ring"]
        pinky_extended = fingers["pinky"]
        thumb_extended = fingers["thumb"]

        if self._settings.pinch_normalized:
            # Stage 4a: while the flag is on, the span-normalized lane owns the
            # pinch decision entirely (legacy absolute gate is skipped).
            norm_event = self._recognize_pinch_normalized(hand, timestamp)
            if norm_event is not None:
                return norm_event
        else:
            # Pinch detection (thumb + index tips close together)
            pinch_dist = self._distance(hand.thumb_tip, hand.index_tip)
            is_pinching = pinch_dist < self._settings.pinch_threshold

            # Anti-false-positive: debounce pinch
            if is_pinching and not self._pinch_active:
                now = time.time() * 1000
                if now - self._last_pinch_time > self._settings.pinch_cooldown_ms:
                    self._pinch_active = True
                    self._last_pinch_time = now
                    self._note_pinch_arm(hand, timestamp)
                    return GestureEvent(
                        gesture=GestureType.PINCH,
                        confidence=min(1.0, 0.8 + (1.0 - pinch_dist / self._settings.pinch_threshold) * 0.2),
                        timestamp=timestamp,
                        hand=hand,
                        pointer_position=hand.index_tip,
                    )
            elif not is_pinching:
                if self._pinch_active:
                    self._pinch_active = False
                    self._capture_flick()
                # If holding pinch, return drag gesture
            elif is_pinching and self._pinch_active:
                # Held pinch: drag on movement, plain continuation otherwise
                # (the plain continuation is the Stage 4f repair — before it,
                # a stationary held pinch fell through to the FIST check below
                # and cancelled from frame 2; probe-verified Stage 3)
                return self._held_pinch_event(hand, timestamp)

        # Point gesture (only index finger extended)
        if index_extended and not middle_extended and not ring_extended and not pinky_extended:
            # Smooth pointer position
            smoothed = self._smooth_pointer(hand.index_tip, timestamp)
            self._pointer_history.append(smoothed)

            return self._earn_pose(
                GestureEvent(
                    gesture=GestureType.POINT,
                    confidence=0.9,
                    timestamp=timestamp,
                    hand=hand,
                    pointer_position=smoothed,
                ),
                timestamp,
            )

        # Open palm (all fingers extended)
        if index_extended and middle_extended and ring_extended and pinky_extended:
            return self._earn_pose(
                GestureEvent(
                    gesture=GestureType.OPEN_PALM,
                    confidence=0.85,
                    timestamp=timestamp,
                    hand=hand,
                ),
                timestamp,
            )

        # Fist (no fingers extended)
        if not index_extended and not middle_extended and not ring_extended and not pinky_extended:
            return self._earn_pose(
                GestureEvent(
                    gesture=GestureType.FIST,
                    confidence=0.8,
                    timestamp=timestamp,
                    hand=hand,
                ),
                timestamp,
            )

        # Stage 5a — PEACE pose (holo.html:381):
        #   tips[0] > 1.45 && tips[1] > 1.45 && tips[2] < 1.15 && tips[3] < 1.15
        # where tips[i] = dist(finger_tip, wrist) / span and
        # span = max(1e-4, dist(lm[0], lm[9])) (holo.html:370-372). Confidence
        # is the tracker-derived hand confidence — no invented number (§4.1).
        # Pose-only, always safe: the RESET *action* is interpreter-gated.
        # Not routed through _earn_pose: its earn-frames live in the
        # interpreter as the 8-frame streak (holo.html:528-531).
        lm = hand.landmarks
        if len(lm) >= 21:
            span = max(self._distance(lm[0], lm[9]), 1e-4)
            tip_index = self._distance(lm[8], lm[0]) / span
            tip_middle = self._distance(lm[12], lm[0]) / span
            tip_ring = self._distance(lm[16], lm[0]) / span
            tip_pinky = self._distance(lm[20], lm[0]) / span
            if (tip_index > 1.45 and tip_middle > 1.45
                    and tip_ring < 1.15 and tip_pinky < 1.15):
                return GestureEvent(
                    gesture=GestureType.PEACE,
                    confidence=hand.confidence,
                    timestamp=timestamp,
                    hand=hand,
                )

        # Default: tracking
        smoothed = self._smooth_pointer(hand.palm_center, timestamp)
        self._pointer_history.append(smoothed)

        return GestureEvent(
            gesture=GestureType.NONE,
            confidence=0.5,
            timestamp=timestamp,
            hand=hand,
            pointer_position=smoothed,
        )

    def _recognize_two_hand(
        self, hand1: HandLandmarks, hand2: HandLandmarks, timestamp: float
    ) -> GestureEvent:
        """Recognize two-hand gestures (zoom, rotate)."""
        # Calculate distance between hands
        dist = self._distance(hand1.palm_center, hand2.palm_center)

        # Store distance history for velocity calculation
        if not hasattr(self, '_two_hand_dists'):
            self._two_hand_dists: deque = deque(maxlen=10)

        prev_dist = self._two_hand_dists[-1] if self._two_hand_dists else dist
        self._two_hand_dists.append(dist)
        dist_velocity = dist - prev_dist

        # Stage 4c — separation gate: below the minimum palm distance no zoom
        # or rotate may fire (holo.html:631 refuses world gestures under 140px
        # of real separation; 140px on a ~1366px viewport ≈ 0.10 normalized).
        # Default 0.0 disables the gate entirely (legacy byte-identical).
        if (
            self._settings.two_hand_min_separation > 0.0
            and dist < self._settings.two_hand_min_separation
        ):
            return GestureEvent(
                gesture=GestureType.NONE,
                confidence=0.4,
                timestamp=timestamp,
                hand=hand1,
                second_hand=hand2,
            )

        # Stage 4c — just-appeared second-hand grace: fewer than N consecutive
        # two-hand frames ⇒ no event (holo.html:483 gates presence on
        # st.seen >= 3). Default 1 = legacy (no additional grace).
        if self._two_hand_seen < self._settings.two_hand_grace_frames:
            return GestureEvent(
                gesture=GestureType.NONE,
                confidence=0.4,
                timestamp=timestamp,
                hand=hand1,
                second_hand=hand2,
            )

        # Zoom detection
        if abs(dist_velocity) > self._settings.movement_threshold:
            if dist_velocity > 0:
                return GestureEvent(
                    gesture=GestureType.SPREAD,
                    confidence=0.75,
                    timestamp=timestamp,
                    hand=hand1,
                    second_hand=hand2,
                    zoom_delta=dist_velocity,
                )
            else:
                return GestureEvent(
                    gesture=GestureType.CONTRACT,
                    confidence=0.75,
                    timestamp=timestamp,
                    hand=hand1,
                    second_hand=hand2,
                    zoom_delta=dist_velocity,
                )

        # Rotation detection (angular velocity between hands)
        angle1 = math.atan2(
            hand1.palm_center[1] - hand2.palm_center[1],
            hand1.palm_center[0] - hand2.palm_center[0],
        )

        if not hasattr(self, '_two_hand_angles'):
            self._two_hand_angles: deque = deque(maxlen=5)

        if self._two_hand_angles:
            prev_angle = self._two_hand_angles[-1]
            angle_delta = angle1 - prev_angle
            # Normalize angle delta
            if angle_delta > math.pi:
                angle_delta -= 2 * math.pi
            elif angle_delta < -math.pi:
                angle_delta += 2 * math.pi

            self._two_hand_angles.append(angle1)

            if abs(angle_delta) > 0.05:  # ~3 degrees
                gesture = GestureType.ROTATE
                intent = "rotate_cw" if angle_delta > 0 else "rotate_ccw"
                return GestureEvent(
                    gesture=gesture,
                    confidence=0.7,
                    timestamp=timestamp,
                    hand=hand1,
                    second_hand=hand2,
                    rotation_angle=angle_delta,
                    metadata={"intent": intent},
                )

        self._two_hand_angles.append(angle1)

        return GestureEvent(
            gesture=GestureType.NONE,
            confidence=0.4,
            timestamp=timestamp,
            hand=hand1,
            second_hand=hand2,
        )

    # ── Stage 4 hardening (flagged unless noted otherwise) ──────────────────

    def _recognize_pinch_normalized(
        self, hand: HandLandmarks, timestamp: float
    ) -> Optional[GestureEvent]:
        """4a — span-normalized pinch with holo's IN/OUT ladder and 2-frame earn.

        Citations (GESTURE_SOURCE_AUDIT.md §4): holo.html:368 defines
        ``PINCH_IN = .30, PINCH_OUT = .42, PINCH_EARN = 2``; holo.html:370/376
        define ``span = dist(wrist, middle_mcp)`` and
        ``pinch = dist(thumb_tip, index_tip) / span``; holo.html:519-525 is the
        per-frame ladder (count below IN, reset at/above IN, release above OUT).

        Returns ``None`` when the hand is clearly not pinching so the caller
        runs the normal pose checks; returns an event (PINCH or pending NONE)
        whenever the pinch lane owns the frame, so a partially-formed pinch can
        never fall through to FIST/cancel.
        """
        lm = hand.landmarks
        if len(lm) < 21:
            return None
        span = max(self._distance(lm[0], lm[9]), 1e-4)
        ratio = self._distance(hand.thumb_tip, hand.index_tip) / span

        if self._pinch_active:
            if ratio > 0.42:                     # holo.html:524 release
                self._pinch_active = False
                self._norm_pinch_n = 0
                self._capture_flick()
                return None                      # released → pose checks
            return self._held_pinch_event(hand, timestamp)

        if ratio < 0.30:                         # holo.html:519 arm ladder
            self._norm_pinch_n += 1
            if self._norm_pinch_n >= 2:          # holo.html:521 PINCH_EARN = 2
                self._pinch_active = True
                self._last_pinch_time = timestamp * 1000
                self._note_pinch_arm(hand, timestamp)
                return GestureEvent(
                    gesture=GestureType.PINCH,
                    confidence=min(1.0, 0.8 + (1.0 - ratio / 0.30) * 0.2),
                    timestamp=timestamp,
                    hand=hand,
                    pointer_position=hand.index_tip,
                )
            # earn pending: N-1 frame ⇒ nothing (G1 test plan row)
            return GestureEvent(
                gesture=GestureType.NONE, confidence=0.5, timestamp=timestamp
            )

        self._norm_pinch_n = 0                   # holo.html:523 reset
        if ratio <= 0.42:
            # ambiguous zone between the gates: suppress pose fallback so a
            # slowly-opening pinch never classifies as FIST (=> spurious cancel)
            return GestureEvent(
                gesture=GestureType.NONE, confidence=0.5, timestamp=timestamp
            )
        return None                              # clearly open → pose checks

    def _held_pinch_event(self, hand: HandLandmarks, timestamp: float) -> GestureEvent:
        """Held-pinch frame: DRAG_MOVE on finger movement, plain PINCH otherwise.

        The plain continuation is the flagless Stage 4f repair: before it, a
        stationary held pinch (empty pointer history or sub-threshold delta)
        fell through to the FIST check — all fingers curl inside a pinch pose —
        and cancelled the interaction from the very second frame
        (probe-verified: tests/test_gesture_probe.py). holo keeps ``movePinch``
        running for every held frame instead (holo.html:525).
        """
        if self._settings.flick_detect:
            # Stage 5c: held frames are the flick trail (holo's st.trail);
            # the release frame is deliberately NOT appended — holo computes
            # peakVelocity on the last *pinch* frame, never on the release
            # pose, so an opening hand cannot fake a throw.
            self._flick_trail.append(
                (hand.index_tip[0], hand.index_tip[1], timestamp)
            )
        if self._pointer_history:
            last_pos = self._pointer_history[-1]
            delta = (
                hand.index_tip[0] - last_pos[0],
                hand.index_tip[1] - last_pos[1],
            )
            if abs(delta[0]) > self._settings.movement_threshold or abs(delta[1]) > self._settings.movement_threshold:
                return GestureEvent(
                    gesture=GestureType.PINCH,
                    confidence=0.85,
                    timestamp=timestamp,
                    hand=hand,
                    intent=GestureIntent.DRAG_MOVE,
                    pointer_position=hand.index_tip,
                    drag_delta=delta,
                )
        return GestureEvent(
            gesture=GestureType.PINCH,
            confidence=0.85,
            timestamp=timestamp,
            hand=hand,
            pointer_position=hand.index_tip,
        )

    # ── Stage 5 flick observation (flagged; holo.html:559-567) ──────────────

    def _note_pinch_arm(self, hand: HandLandmarks, timestamp: float) -> None:
        """Fresh pinch: reset the flick trail and seed it with the arm frame."""
        if not self._settings.flick_detect:
            return
        self._flick_trail.clear()
        self._flick_trail.append(
            (hand.index_tip[0], hand.index_tip[1], timestamp)
        )

    def _capture_flick(self) -> None:
        """On pinch release: stash peak speed if it exceeds the flick gate."""
        if not self._settings.flick_detect:
            return
        speed = self._peak_flick_speed()
        self._pending_flick = (
            speed if speed > self._settings.flick_threshold else None
        )

    def _peak_flick_speed(self) -> float:
        """holo.html:559-567 peakVelocity ported: dt clamped to >= 8 ms,
        velocities normalized to px per 16 ms inside the virtual
        ``one_euro_px_scale``-wide frame (same synthetic-px convention as
        ``_one_euro``; Stage 7 measures real behavior, no camera claim)."""
        tr = self._flick_trail
        if len(tr) < 2:
            return 0.0
        scale = self._settings.one_euro_px_scale or 1280.0
        best = 0.0
        for i in range(1, len(tr)):
            dt_ms = max(8.0, (tr[i][2] - tr[i - 1][2]) * 1000.0)
            vx = (tr[i][0] - tr[i - 1][0]) * scale / dt_ms * 16.0
            vy = (tr[i][1] - tr[i - 1][1]) * scale / dt_ms * 16.0
            sp = math.hypot(vx, vy)
            if sp > best:
                best = sp
        return best

    def _earn_pose(self, event: GestureEvent, timestamp: float) -> GestureEvent:
        """4b — pose earn-frames (``pose_earn_frames`` N; 0/1 = legacy instant).

        holo.html:491-496 holds POINT for 4 consecutive frames
        (``st.point === 4``) before any stroke is drawn, so a one-frame pose
        flicker never acts. With N set, XYRIS applies the same streak rule to
        POINT / OPEN_PALM / FIST: pending frames emit NONE @0.5 (honest
        "not yet"), which the confidence gate reports as LOW_CONFIDENCE and the
        Stage 4e recovery lets the earned frame act on the very next confident
        event. PINCH and NONE are exempt (their own latches gate them).
        """
        n = self._settings.pose_earn_frames
        if n <= 1:
            return event
        if event.gesture == self._last_earned_pose:
            self._pose_streak += 1
        else:
            self._last_earned_pose = event.gesture
            self._pose_streak = 1
        if self._pose_streak < n:
            return GestureEvent(
                gesture=GestureType.NONE, confidence=0.5, timestamp=timestamp
            )
        return event

    def _one_euro(self, raw: tuple, now: float) -> tuple:
        """4d — One Euro filter (smoothing_filter == "one_euro").

        Ported from holo.html:418-429 (Casiez et al.): derivative cutoff 2.5
        (``:423``), ``cutoff = 1.3 + 0.009 * speed`` (``:426``), dt clamped to
        the 1/60 s fallback below 1/240 s and capped at 0.2 s (``:420``).

        Unit note: holo filters screen pixels, XYRIS landmarks are normalized
        to [0,1], and 0.009 is tuned for px/s — so the filter runs inside a
        virtual ``one_euro_px_scale``-wide frame (1280, a synthetic constant;
        Stage 7 measures real behavior, no camera claim) and converts back.
        """
        scale = self._settings.one_euro_px_scale
        if not scale or scale <= 0:
            scale = 1280.0
        rx, ry = raw[0] * scale, raw[1] * scale
        st = self._oe
        if st is None:
            self._oe = {"t": now, "x": rx, "y": ry, "dx": 0.0, "dy": 0.0}
            return raw
        dt = now - st["t"]
        if not (dt > 1.0 / 240.0):
            dt = 1.0 / 60.0
        if dt > 0.2:
            dt = 0.2
        st["t"] = now

        def alpha(cut: float) -> float:
            r = 2.0 * math.pi * cut * dt
            return r / (r + 1.0)

        ad = alpha(self._settings.one_euro_d_cutoff)
        st["dx"] += ((rx - st["x"]) / dt - st["dx"]) * ad
        st["dy"] += ((ry - st["y"]) / dt - st["dy"]) * ad
        a = alpha(
            self._settings.one_euro_min_cutoff
            + self._settings.one_euro_beta * math.hypot(st["dx"], st["dy"])
        )
        st["x"] += (rx - st["x"]) * a
        st["y"] += (ry - st["y"]) * a
        return (st["x"] / scale, st["y"] / scale)

    def _count_fingers(self, hand: HandLandmarks) -> dict:
        """Count which fingers are extended based on landmark positions."""
        landmarks = hand.landmarks
        if len(landmarks) < 21:
            return {"thumb": False, "index": False, "middle": False, "ring": False, "pinky": False}

        # Index finger: tip above PIP (y decreases upward in normalized coords)
        index_extended = landmarks[8][1] < landmarks[6][1]

        # Middle finger: tip above PIP
        middle_extended = landmarks[12][1] < landmarks[10][1]

        # Ring finger: tip above PIP
        ring_extended = landmarks[16][1] < landmarks[14][1]

        # Pinky finger: tip above PIP
        pinky_extended = landmarks[20][1] < landmarks[18][1]

        # Thumb: tip to the right of IP joint (for right hand) or left (for left hand)
        thumb_tip_x = landmarks[4][0]
        thumb_ip_x = landmarks[3][0]
        if hand.handedness == "Right":
            thumb_extended = thumb_tip_x < thumb_ip_x
        else:
            thumb_extended = thumb_tip_x > thumb_ip_x

        return {
            "thumb": thumb_extended,
            "index": index_extended,
            "middle": middle_extended,
            "ring": ring_extended,
            "pinky": pinky_extended,
        }

    def _distance(self, p1: tuple, p2: tuple) -> float:
        """Euclidean distance between two points."""
        return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)

    def _smooth_pointer(self, raw: tuple, timestamp: Optional[float] = None) -> tuple:
        """Apply the configured smoothing filter (Stage 4d).

        Default "ema" reproduces the legacy alpha blend byte-for-byte;
        smoothing_filter == "one_euro" swaps in holo's speed-adaptive filter.
        """
        if self._settings.smoothing_filter == "one_euro":
            return self._one_euro(raw, timestamp if timestamp is not None else time.time())

        if not self._pointer_history:
            return raw

        last = self._pointer_history[-1]
        alpha = self._settings.pointer_smoothing
        smoothed = (
            last[0] + alpha * (raw[0] - last[0]),
            last[1] + alpha * (raw[1] - last[1]),
        )
        return smoothed
