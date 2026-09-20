"""
Interaction State Machine — Manages gesture interaction states.

Part of AETHER-I Spatial Screen Interaction Engine V1.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger(__name__)


class InteractionState(Enum):
    """States of the gesture interaction system."""
    INACTIVE = auto()       # Gesture control OFF
    ACTIVATING = auto()     # Camera starting, hand tracking initializing
    TRACKING = auto()       # Hand detected, no specific gesture
    HOVERING = auto()       # Pointer moving over UI element
    PINCH_DETECTED = auto() # Pinch detected, validating
    SELECTING = auto()      # Pinch confirmed, executing select
    DRAGGING = auto()       # Pinch + move, dragging element
    PAUSED = auto()         # Open palm, interaction suspended
    CANCELLED = auto()      # Fist, current interaction cancelled
    LOST_HAND = auto()      # Hand temporarily lost from frame
    LOW_CONFIDENCE = auto() # Gesture confidence below threshold
    CAMERA_ERROR = auto()   # Camera unavailable or failed


class GestureType(Enum):
    """Recognized gesture types."""
    NONE = "none"
    POINT = "point"           # Index finger extended
    PINCH = "pinch"           # Index + thumb together
    OPEN_PALM = "open_palm"   # Open hand
    FIST = "fist"             # Closed fist
    SPREAD = "spread"         # Two hands moving apart
    CONTRACT = "contract"     # Two hands moving together
    ROTATE = "rotate"         # Two hands rotating


class GestureIntent(Enum):
    """Semantic intents from gesture recognition."""
    NONE = "none"
    POINTER_MOVE = "pointer_move"
    SELECT = "select"
    DRAG_START = "drag_start"
    DRAG_MOVE = "drag_move"
    DRAG_END = "drag_end"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    ROTATE_CW = "rotate_cw"
    ROTATE_CCW = "rotate_ccw"


@dataclass
class HandLandmarks:
    """Detected hand landmarks."""
    landmarks: list[tuple[float, float]] = field(default_factory=list)
    handedness: str = "Right"  # "Left" or "Right"
    confidence: float = 0.0
    palm_center: tuple[float, float] = (0.0, 0.0)
    index_tip: tuple[float, float] = (0.0, 0.0)
    thumb_tip: tuple[float, float] = (0.0, 0.0)
    index_mcp: tuple[float, float] = (0.0, 0.0)
    wrist: tuple[float, float] = (0.0, 0.0)
    hand_width: float = 0.0


@dataclass
class GestureEvent:
    """A detected gesture event."""
    gesture: GestureType = GestureType.NONE
    intent: GestureIntent = GestureIntent.NONE
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)
    hand: Optional[HandLandmarks] = None
    second_hand: Optional[HandLandmarks] = None
    pointer_position: tuple[float, float] = (0.0, 0.0)
    drag_delta: tuple[float, float] = (0.0, 0.0)
    zoom_delta: float = 0.0
    rotation_angle: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class GestureSettings:
    """Configurable gesture settings."""
    confidence_threshold: float = 0.7
    pinch_threshold: float = 0.05
    pinch_cooldown_ms: float = 300
    pointer_smoothing: float = 0.3
    min_hold_duration_ms: float = 100
    movement_threshold: float = 0.01
    debounce_ms: float = 50
    mirror_mode: bool = True
    camera_index: int = 0
    enabled: bool = False


class InteractionStateMachine:
    """Manages gesture interaction state transitions.

    Enforces valid state transitions and handles anti-false-positive logic.
    """

    VALID_TRANSITIONS = {
        InteractionState.INACTIVE: {
            InteractionState.ACTIVATING,
        },
        InteractionState.ACTIVATING: {
            InteractionState.TRACKING,
            InteractionState.CAMERA_ERROR,
            InteractionState.INACTIVE,
        },
        InteractionState.TRACKING: {
            InteractionState.HOVERING,
            InteractionState.PAUSED,
            InteractionState.CANCELLED,
            InteractionState.LOST_HAND,
            InteractionState.LOW_CONFIDENCE,
            InteractionState.INACTIVE,
        },
        InteractionState.HOVERING: {
            InteractionState.TRACKING,
            InteractionState.PINCH_DETECTED,
            InteractionState.PAUSED,
            InteractionState.CANCELLED,
            InteractionState.LOST_HAND,
            InteractionState.LOW_CONFIDENCE,
            InteractionState.INACTIVE,
        },
        InteractionState.PINCH_DETECTED: {
            InteractionState.SELECTING,
            InteractionState.TRACKING,
            InteractionState.LOST_HAND,
            InteractionState.INACTIVE,
        },
        InteractionState.SELECTING: {
            InteractionState.DRAGGING,
            InteractionState.TRACKING,
            InteractionState.HOVERING,
            InteractionState.LOST_HAND,
            InteractionState.INACTIVE,
        },
        InteractionState.DRAGGING: {
            InteractionState.TRACKING,
            InteractionState.HOVERING,
            InteractionState.LOST_HAND,
            InteractionState.INACTIVE,
        },
        InteractionState.PAUSED: {
            InteractionState.TRACKING,
            InteractionState.INACTIVE,
        },
        InteractionState.CANCELLED: {
            InteractionState.TRACKING,
            InteractionState.INACTIVE,
        },
        InteractionState.LOST_HAND: {
            InteractionState.TRACKING,
            InteractionState.INACTIVE,
        },
        InteractionState.LOW_CONFIDENCE: {
            InteractionState.TRACKING,
            InteractionState.INACTIVE,
        },
        InteractionState.CAMERA_ERROR: {
            InteractionState.INACTIVE,
        },
    }

    def __init__(self):
        self._state = InteractionState.INACTIVE
        self._previous_state = InteractionState.INACTIVE
        self._state_entered_at = time.time()
        self._transition_log: list[tuple[InteractionState, InteractionState, float]] = []

    @property
    def state(self) -> InteractionState:
        return self._state

    @property
    def previous_state(self) -> InteractionState:
        return self._previous_state

    @property
    def time_in_state(self) -> float:
        return time.time() - self._state_entered_at

    def transition(self, new_state: InteractionState) -> bool:
        """Attempt a state transition.

        Returns True if transition was valid and executed.
        """
        if new_state == self._state:
            return True

        valid = self.VALID_TRANSITIONS.get(self._state, set())
        if new_state not in valid:
            logger.warning(
                "Invalid transition: %s -> %s (valid: %s)",
                self._state.name, new_state.name,
                [s.name for s in valid],
            )
            return False

        old = self._state
        self._previous_state = old
        self._state = new_state
        self._state_entered_at = time.time()
        self._transition_log.append((old, new_state, self._state_entered_at))

        logger.debug("State: %s -> %s", old.name, new_state.name)
        return True

    def reset(self) -> None:
        """Reset to INACTIVE state."""
        self._previous_state = self._state
        self._state = InteractionState.INACTIVE
        self._state_entered_at = time.time()

    @property
    def is_active(self) -> bool:
        return self._state not in (
            InteractionState.INACTIVE,
            InteractionState.CAMERA_ERROR,
        )

    @property
    def is_tracking(self) -> bool:
        return self._state in (
            InteractionState.TRACKING,
            InteractionState.HOVERING,
            InteractionState.PINCH_DETECTED,
            InteractionState.SELECTING,
            InteractionState.DRAGGING,
        )

    @property
    def can_interact(self) -> bool:
        return self._state in (
            InteractionState.TRACKING,
            InteractionState.HOVERING,
        )
