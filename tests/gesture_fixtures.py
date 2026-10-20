"""
Synthetic 21-landmark hand fixtures for gesture probe tests (Stage 3).

Method ported from holo-gestures' probe battery (synthetic hands through the
real engine, holo.html:1288+ / GESTURE_SOURCE_AUDIT.md §8), re-expressed as
XYRIS `HandLandmarks` factories. Geometry follows MediaPipe conventions:
21 points, y grows downward, finger extended <=> tip.y < pip.y
(gesture_recognizer.py:251-261).
"""
from __future__ import annotations

import math

from core.spatial.interaction_state import HandLandmarks

# MediaPipe hand landmark indices
WRIST, THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 0, 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

# knuckle x positions (hand upright, centered on 0.5)
_KNUCKLE_X = {INDEX_MCP: 0.44, MIDDLE_MCP: 0.47, RING_MCP: 0.50, PINKY_MCP: 0.53}
_PIP_OF = {INDEX_MCP: INDEX_PIP, MIDDLE_MCP: MIDDLE_PIP,
           RING_MCP: RING_PIP, PINKY_MCP: PINKY_PIP}
_DIP_OF = {INDEX_MCP: INDEX_DIP, MIDDLE_MCP: MIDDLE_DIP,
           RING_MCP: RING_DIP, PINKY_MCP: PINKY_DIP}
_TIP_OF = {INDEX_MCP: INDEX_TIP, MIDDLE_MCP: MIDDLE_TIP,
           RING_MCP: RING_TIP, PINKY_MCP: PINKY_TIP}


def make_hand(
    extensions: tuple[bool, bool, bool, bool] = (True, True, True, True),
    thumb_tip: tuple[float, float] | None = None,
    index_tip_override: tuple[float, float] | None = None,
    palm_center: tuple[float, float] | None = None,
    handedness: str = "Right",
    confidence: float = 0.9,
) -> HandLandmarks:
    """Build a 21-landmark hand.

    extensions = (index, middle, ring, pinky) extended flags.
    thumb_tip defaults to a neutral side position (~0.063 from a default
    extended index tip, i.e. NOT pinching under the legacy 0.05 gate).
    """
    lm: list[tuple[float, float]] = [(0.0, 0.0)] * 21
    lm[WRIST] = (0.5, 0.60)
    # thumb chain
    lm[THUMB_CMC] = (0.47, 0.56)
    lm[THUMB_MCP] = (0.45, 0.53)
    lm[THUMB_IP] = (0.43, 0.50)
    lm[THUMB_TIP] = thumb_tip if thumb_tip is not None else (0.46, 0.44)
    # fingers
    for mcp, extended in zip(
        (INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP), extensions
    ):
        bx = _KNUCKLE_X[mcp]
        lm[mcp] = (bx, 0.52)
        if extended:  # tip above pip (smaller y)
            lm[_PIP_OF[mcp]] = (bx, 0.46)
            lm[_DIP_OF[mcp]] = (bx, 0.42)
            lm[_TIP_OF[mcp]] = (bx, 0.38)
        else:         # tip below pip
            lm[_PIP_OF[mcp]] = (bx, 0.48)
            lm[_DIP_OF[mcp]] = (bx, 0.51)
            lm[_TIP_OF[mcp]] = (bx, 0.54)
    if index_tip_override is not None:
        lm[INDEX_TIP] = index_tip_override

    if palm_center is None:
        palm_center = (
            (lm[WRIST][0] + lm[MIDDLE_MCP][0]) / 2,
            (lm[WRIST][1] + lm[MIDDLE_MCP][1]) / 2,
        )

    return HandLandmarks(
        landmarks=lm,
        handedness=handedness,
        confidence=confidence,
        palm_center=palm_center,
        index_tip=lm[INDEX_TIP],
        thumb_tip=lm[THUMB_TIP],
        index_mcp=lm[INDEX_MCP],
        wrist=lm[WRIST],
        hand_width=0.08,
    )


def pose_open(**kw) -> HandLandmarks:
    return make_hand(extensions=(True, True, True, True), **kw)


def pose_fist(**kw) -> HandLandmarks:
    return make_hand(extensions=(False, False, False, False), **kw)


def pose_point(**kw) -> HandLandmarks:
    return make_hand(extensions=(True, False, False, False), **kw)


def pose_peace(**kw) -> HandLandmarks:
    return make_hand(extensions=(True, True, False, False), **kw)


def pose_pinch(distance: float = 0.04, **kw) -> HandLandmarks:
    """Thumb tip placed exactly `distance` from the curled index tip (0.44, 0.54)."""
    return make_hand(
        extensions=(False, False, False, False),
        thumb_tip=(0.44 + distance, 0.54),
        **kw,
    )


def hand_with_palm(palm: tuple[float, float], **kw) -> HandLandmarks:
    return make_hand(palm_center=palm, **kw)


def scale_hand(hand: HandLandmarks, s: float) -> HandLandmarks:
    """Scale every coordinate by s about the origin (homogeneous: all ratios
    — including the span-normalized pinch of holo.html:376 — are preserved).
    Used by the G1 span-invariance probe (span 0.5 vs 1.5)."""
    lm = [(x * s, y * s) for (x, y) in hand.landmarks]
    return HandLandmarks(
        landmarks=lm,
        handedness=hand.handedness,
        confidence=hand.confidence,
        palm_center=(hand.palm_center[0] * s, hand.palm_center[1] * s),
        index_tip=(hand.index_tip[0] * s, hand.index_tip[1] * s),
        thumb_tip=(hand.thumb_tip[0] * s, hand.thumb_tip[1] * s),
        index_mcp=(hand.index_mcp[0] * s, hand.index_mcp[1] * s),
        wrist=(hand.wrist[0] * s, hand.wrist[1] * s),
        hand_width=hand.hand_width * s,
    )


def pose_pinch_ratio(ratio: float, scale: float = 1.0) -> HandLandmarks:
    """Curled hand whose thumb-index distance equals `ratio * span`, where
    span = dist(landmark[0], landmark[9]) per holo.html:370/376.

    ratio < 0.30 → below holo's PINCH_IN gate; > 0.42 → above PINCH_OUT.
    """
    h = pose_fist()
    span = math.hypot(
        h.landmarks[MIDDLE_MCP][0] - h.landmarks[WRIST][0],
        h.landmarks[MIDDLE_MCP][1] - h.landmarks[WRIST][1],
    )
    tip = h.landmarks[INDEX_TIP]
    h.landmarks[THUMB_TIP] = (tip[0] + ratio * span, tip[1])
    h.thumb_tip = h.landmarks[THUMB_TIP]
    if scale != 1.0:
        h = scale_hand(h, scale)
    return h
