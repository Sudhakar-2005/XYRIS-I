"""
Hand Tracker — MediaPipe-based hand landmark detection.

Part of AETHER-I Spatial Screen Interaction Engine V1.
"""

import logging
import time
from typing import Optional

import cv2
import numpy as np

try:
    import mediapipe as mp
    HAS_MEDIAPIPE = True
except ImportError:
    HAS_MEDIAPIPE = False
    mp = None

from core.spatial.interaction_state import HandLandmarks

logger = logging.getLogger(__name__)


class HandTracker:
    """Detects and tracks hand landmarks using MediaPipe Hands.

    Provides real-time hand landmark data from camera frames.
    All processing is local — no data leaves the device.
    """

    # MediaPipe hand landmark indices
    WRIST = 0
    THUMB_TIP = 4
    INDEX_TIP = 8
    MIDDLE_TIP = 12
    RING_TIP = 16
    PINKY_TIP = 20
    INDEX_MCP = 5
    MIDDLE_MCP = 9
    RING_MCP = 13
    PINKY_MCP = 17
    THUMB_IP = 3
    INDEX_PIP = 6
    MIDDLE_PIP = 10
    RING_PIP = 14
    PINKY_PIP = 18

    def __init__(
        self,
        max_hands: int = 2,
        min_detection_confidence: float = 0.7,
        min_tracking_confidence: float = 0.6,
    ):
        if not HAS_MEDIAPIPE:
            raise ImportError(
                "MediaPipe not installed. Run: pip install mediapipe"
            )

        self._max_hands = max_hands
        self._min_detection_confidence = min_detection_confidence
        self._min_tracking_confidence = min_tracking_confidence

        self._hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=max_hands,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._draw = mp.solutions.drawing_utils
        self._hand_connections = mp.solutions.hands.HAND_CONNECTIONS

        self._last_landmarks: list[HandLandmarks] = []
        self._processing_time_ms: float = 0.0
        self._frame_count: int = 0

    @property
    def processing_time_ms(self) -> float:
        return self._processing_time_ms

    @property
    def last_landmarks(self) -> list[HandLandmarks]:
        return list(self._last_landmarks)

    def detect(self, frame: np.ndarray) -> list[HandLandmarks]:
        """Detect hands in a camera frame.

        Args:
            frame: BGR camera frame from OpenCV

        Returns:
            List of HandLandmarks for each detected hand
        """
        start = time.time()

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False

        results = self._hands.process(rgb)

        landmarks_list = []

        if results.multi_hand_landmarks:
            for idx, hand_landmarks in enumerate(results.multi_hand_landmarks):
                handedness = "Right"
                if results.multi_handedness and idx < len(results.multi_handedness):
                    label = results.multi_handedness[idx].classification[0].label
                    handedness = label

                hand = self._extract_landmarks(hand_landmarks, handedness, frame.shape)
                if hand:
                    landmarks_list.append(hand)

        self._last_landmarks = landmarks_list
        self._processing_time_ms = (time.time() - start) * 1000
        self._frame_count += 1

        return landmarks_list

    def _extract_landmarks(
        self, hand_landmarks, handedness: str, frame_shape: tuple
    ) -> Optional[HandLandmarks]:
        """Extract structured landmark data from MediaPipe results."""
        try:
            h, w = frame_shape[:2]
            lm = hand_landmarks.landmark

            # Convert normalized coordinates to pixel coordinates
            def to_px(landmark):
                return (landmark.x * w, landmark.y * h)

            def to_norm(landmark):
                return (landmark.x, landmark.y)

            landmarks_px = [to_px(l) for l in lm]
            landmarks_norm = [to_norm(l) for l in lm]

            # Calculate palm center (average of wrist, middle MCP, index MCP, pinky MCP)
            palm_points = [lm[self.WRIST], lm[self.MIDDLE_MCP], lm[self.INDEX_MCP], lm[self.PINKY_MCP]]
            palm_center = (
                sum(p.x for p in palm_points) / len(palm_points),
                sum(p.y for p in palm_points) / len(palm_points),
            )

            # Calculate hand width for scale reference
            hand_width = abs(landmarks_px[self.INDEX_MCP][0] - landmarks_px[self.PINKY_MCP][0])

            # Confidence from wrist landmark visibility
            confidence = lm[self.WRIST].visibility if hasattr(lm[self.WRIST], 'visibility') else 0.8

            return HandLandmarks(
                landmarks=landmarks_norm,
                handedness=handedness,
                confidence=confidence,
                palm_center=palm_center,
                index_tip=to_norm(lm[self.INDEX_TIP]),
                thumb_tip=to_norm(lm[self.THUMB_TIP]),
                index_mcp=to_norm(lm[self.INDEX_MCP]),
                wrist=to_norm(lm[self.WRIST]),
                hand_width=hand_width / w,  # Normalized
            )

        except Exception as e:
            logger.error("Landmark extraction error: %s", e)
            return None

    def draw_landmarks(self, frame: np.ndarray, landmarks_list: list[HandLandmarks]) -> np.ndarray:
        """Draw hand landmarks on frame for debug visualization."""
        if not HAS_MEDIAPIPE:
            return frame

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._hands.process(rgb)

        annotated = frame.copy()
        if results.multi_hand_landmarks:
            for hand_lm in results.multi_hand_landmarks:
                self._draw.draw_landmarks(
                    annotated, hand_lm, self._hand_connections
                )

        return annotated

    def close(self) -> None:
        """Release MediaPipe resources."""
        if self._hands:
            self._hands.close()

    def __del__(self):
        self.close()
