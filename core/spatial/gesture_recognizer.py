"""
Gesture Recognizer — Identifies gestures from hand landmarks.

Part of AETHER-I Spatial Screen Interaction Engine V1.
"""

import logging
import math
import time
from collections import deque
from typing import Optional

from core.spatial.interaction_state import (
    GestureType,
    GestureEvent,
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
            return GestureEvent(
                gesture=GestureType.NONE,
                confidence=0.0,
                timestamp=timestamp,
            )

        primary = hands[0]
        secondary = hands[1] if len(hands) > 1 else None

        # Two-hand gestures
        if secondary:
            return self._recognize_two_hand(primary, secondary, timestamp)

        # Single-hand gestures
        return self._recognize_single_hand(primary, timestamp)

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

        # Pinch detection (thumb + index tips close together)
        pinch_dist = self._distance(hand.thumb_tip, hand.index_tip)
        is_pinching = pinch_dist < self._settings.pinch_threshold

        # Anti-false-positive: debounce pinch
        if is_pinching and not self._pinch_active:
            now = time.time() * 1000
            if now - self._last_pinch_time > self._settings.pinch_cooldown_ms:
                self._pinch_active = True
                self._last_pinch_time = now
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
            # If holding pinch, return drag gesture
        elif is_pinching and self._pinch_active:
            # Check for movement (drag)
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
                        intent="drag_move",
                        pointer_position=hand.index_tip,
                        drag_delta=delta,
                    )

        # Point gesture (only index finger extended)
        if index_extended and not middle_extended and not ring_extended and not pinky_extended:
            # Smooth pointer position
            smoothed = self._smooth_pointer(hand.index_tip)
            self._pointer_history.append(smoothed)

            return GestureEvent(
                gesture=GestureType.POINT,
                confidence=0.9,
                timestamp=timestamp,
                hand=hand,
                pointer_position=smoothed,
            )

        # Open palm (all fingers extended)
        if index_extended and middle_extended and ring_extended and pinky_extended:
            return GestureEvent(
                gesture=GestureType.OPEN_PALM,
                confidence=0.85,
                timestamp=timestamp,
                hand=hand,
            )

        # Fist (no fingers extended)
        if not index_extended and not middle_extended and not ring_extended and not pinky_extended:
            return GestureEvent(
                gesture=GestureType.FIST,
                confidence=0.8,
                timestamp=timestamp,
                hand=hand,
            )

        # Default: tracking
        smoothed = self._smooth_pointer(hand.palm_center)
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

    def _smooth_pointer(self, raw: tuple) -> tuple:
        """Apply smoothing filter to pointer position."""
        if not self._pointer_history:
            return raw

        last = self._pointer_history[-1]
        alpha = self._settings.pointer_smoothing
        smoothed = (
            last[0] + alpha * (raw[0] - last[0]),
            last[1] + alpha * (raw[1] - last[1]),
        )
        return smoothed
