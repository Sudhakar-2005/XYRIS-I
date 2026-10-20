"""
Interaction State Machine — Manages gesture interaction states.

Part of XYRIS I Spatial Screen Interaction Engine V1.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

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
    # Stage 5 vocabulary (XYRIS_GESTURE_ARCHITECTURE.md §6, holo.html:381).
    # Detection is pose-only and always safe; the RESET *action* is gated by
    # GestureSettings.peace_reset (§4.1/§4.2).
    PEACE = "peace"           # Index + middle extended, ring + pinky curled


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
    # Stage 5 new vocabulary (additive; §6 data contracts). Both are
    # reversible UI-state-only actions (capability matrix §5) and, like every
    # gesture intent, are disjoint from SafetyController.CONFIRMATION_REQUIRED.
    DISMISS = "dismiss"       # flick on pinch release while dragging
    RESET = "reset"           # peace held 8 frames (holo.html:528-531)


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
    # ── Stage 4 hardening flags (GESTURE_INTEGRATION_ROADMAP.md Stage 4) ─────
    # Every default reproduces legacy behavior byte-for-byte; only config or
    # explicit construction can turn a gate on (Phase 3 P2, no silent flips).
    pinch_normalized: bool = False        # 4a: holo.html:368/370/376 + :519-525
    pose_earn_frames: int = 0             # 4b: holo.html:491-496 (ON value 4)
    two_hand_min_separation: float = 0.0  # 4c: holo.html:631 (140px ≈ 0.10 norm)
    two_hand_grace_frames: int = 1        # 4c: holo.html:483 (ON value 3; 1 = legacy)
    smoothing_filter: str = "ema"         # 4d: "ema" (legacy) | "one_euro"
    one_euro_min_cutoff: float = 1.3      # holo.html:426
    one_euro_beta: float = 0.009          # holo.html:426
    one_euro_d_cutoff: float = 2.5        # holo.html:423
    one_euro_px_scale: float = 1280.0     # virtual px frame (see recognizer._one_euro)
    # ── Stage 5 vocabulary flags (defaults = legacy, roadmap Stage 5) ────────
    # tap_action: SELECT commits on quick release (holo.html:576-583 field-
    # tuned gate: release <450 ms AND palm drift <60 px) instead of on the
    # 100 ms hold. With the flag ON the hold/hover SELECT is deferred to the
    # release decision (holo philosophy: release = the action), so one pinch
    # yields at most one SELECT. OFF: identical legacy SELECT/DRAG flow.
    tap_action: bool = False
    tap_max_ms: float = 450.0             # holo.html:578
    tap_max_drift: float = 0.046875       # 60 px / 1280 virtual px (holo.html:579)
    # flick_detect: observe pinch-release peak velocity (holo.html:559-567,
    # gate sp > 4 px per 16 ms) and attach metadata["flick"]; the interpreter
    # turns it into DISMISS only from a DRAGGING release. OFF: no metadata,
    # DRAG_END exactly as today.
    flick_detect: bool = False
    flick_threshold: float = 4.0          # holo.html:574 (sp px/16ms)
    # peace_reset: RESET intent when PEACE is held for exactly 8 consecutive
    # interpreted frames with a >2500 ms cooldown (holo.html:528-531 uses
    # strict equality + strict >, so a continuous hold fires once).
    peace_reset: bool = False
    peace_cooldown_ms: float = 2500.0     # holo.html:530


class InteractionStateMachine:
    """Manages gesture interaction state transitions.

    Enforces valid state transitions and handles anti-false-positive logic.
    """

    VALID_TRANSITIONS = {
        InteractionState.INACTIVE: {
            InteractionState.ACTIVATING,
            # The engine attempts CAMERA_ERROR straight from INACTIVE when the
            # camera fails to open (engine.start); without this edge the
            # state machine rejected its own engine's error transition.
            InteractionState.CAMERA_ERROR,
        },
        InteractionState.ACTIVATING: {
            InteractionState.TRACKING,
            InteractionState.CAMERA_ERROR,
            InteractionState.INACTIVE,
        },
        InteractionState.TRACKING: {
            InteractionState.HOVERING,
            # Flagless repair (Stage 4f): gesture_interpreter's TRACKING+PINCH
            # branch transitions here, but the edge was missing, so every pinch
            # from neutral logged "Invalid transition" and stayed TRACKING
            # (probe-verified, tests/test_gesture_probe.py Stage 3 baseline).
            InteractionState.PINCH_DETECTED,
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
        # Stage 2: real transitions surface to the UI (was an uninvoked seam,
        # see XYRIS_ARCHITECTURE_COMPATIBILITY.md §1.7). Exceptions are contained.
        self.on_transition: Optional[Callable[[InteractionState, InteractionState], None]] = None

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
        if self.on_transition:
            try:
                self.on_transition(old, new_state)
            except Exception as e:
                logger.error("on_transition callback error: %s", e)
        return True

    def reset(self) -> None:
        """Reset to INACTIVE state."""
        old = self._state
        self._previous_state = old
        self._state = InteractionState.INACTIVE
        self._state_entered_at = time.time()
        if old != InteractionState.INACTIVE and self.on_transition:
            try:
                self.on_transition(old, InteractionState.INACTIVE)
            except Exception as e:
                logger.error("on_transition callback error: %s", e)

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
