"""
Spatial Interaction Engine — Main orchestrator for gesture control.

Part of AETHER-I Spatial Screen Interaction Engine V1.
"""

import logging
import threading
import time
from typing import Optional, Callable

import numpy as np

from core.spatial.interaction_state import (
    InteractionState,
    InteractionStateMachine,
    GestureEvent,
    GestureType,
    GestureIntent,
    GestureSettings,
)
from core.spatial.camera_manager import CameraManager
from core.spatial.hand_tracker import HandTracker
from core.spatial.gesture_recognizer import GestureRecognizer
from core.spatial.gesture_interpreter import GestureInterpreter
from core.spatial.ui_controller import UIInteractionController
from core.spatial.safety_controller import SafetyController

logger = logging.getLogger(__name__)


class SpatialInteractionEngine:
    """Main engine orchestrating camera, tracking, recognition, and UI interaction.

    Usage:
        engine = SpatialInteractionEngine(settings)
        engine.on_gesture_event = my_callback
        engine.start()

        # ... later ...
        engine.stop()
    """

    def __init__(self, settings: Optional[GestureSettings] = None):
        self._settings = settings or GestureSettings()

        # Components
        self._camera = CameraManager(self._settings.camera_index)
        self._tracker = HandTracker(max_hands=2)
        self._recognizer = GestureRecognizer(self._settings)
        self._state_machine = InteractionStateMachine()
        self._interpreter = GestureInterpreter(self._state_machine, self._settings)
        self._ui_controller = UIInteractionController()
        self._safety = SafetyController()

        # Processing thread
        self._processing_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

        # Latest frame for visualization
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()

        # Callbacks
        self._on_gesture_event: Optional[Callable[[GestureEvent], None]] = None
        self._on_state_change: Optional[Callable[[InteractionState, InteractionState], None]] = None
        self._on_pointer_move: Optional[Callable[[tuple[float, float]], None]] = None

        # Debug
        self._debug_mode = False
        self._fps: float = 0.0
        self._frame_count: int = 0
        self._fps_start: float = 0.0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def state(self) -> InteractionState:
        return self._state_machine.state

    @property
    def camera(self) -> CameraManager:
        return self._camera

    @property
    def settings(self) -> GestureSettings:
        return self._settings

    @property
    def safety(self) -> SafetyController:
        return self._safety

    @property
    def ui_controller(self) -> UIInteractionController:
        return self._ui_controller

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def debug_mode(self) -> bool:
        return self._debug_mode

    @debug_mode.setter
    def debug_mode(self, value: bool) -> None:
        self._debug_mode = value

    # Callbacks
    @property
    def on_gesture_event(self):
        return self._on_gesture_event

    @on_gesture_event.setter
    def on_gesture_event(self, callback: Callable[[GestureEvent], None]):
        self._on_gesture_event = callback

    @property
    def on_state_change(self):
        return self._on_state_change

    @on_state_change.setter
    def on_state_change(self, callback: Callable[[InteractionState, InteractionState], None]):
        self._on_state_change = callback

    @property
    def on_pointer_move(self):
        return self._on_pointer_move

    @on_pointer_move.setter
    def on_pointer_move(self, callback: Callable[[tuple[float, float]], None]):
        self._on_pointer_move = callback

    def start(self) -> bool:
        """Start the gesture engine."""
        if self._running:
            return True

        logger.info("Starting Spatial Interaction Engine")

        # Start camera
        if not self._camera.start():
            self._state_machine.transition(InteractionState.CAMERA_ERROR)
            logger.error("Failed to start camera")
            return False

        self._state_machine.transition(InteractionState.ACTIVATING)

        # Set frame callback
        self._camera.set_frame_callback(self._on_camera_frame)

        # Start processing thread
        self._stop_event.clear()
        self._running = True
        self._fps_start = time.time()
        self._frame_count = 0

        self._processing_thread = threading.Thread(
            target=self._processing_loop,
            daemon=True,
            name="spatial-engine",
        )
        self._processing_thread.start()

        self._state_machine.transition(InteractionState.TRACKING)
        logger.info("Spatial Interaction Engine started")
        return True

    def stop(self) -> None:
        """Stop the gesture engine."""
        if not self._running:
            return

        logger.info("Stopping Spatial Interaction Engine")

        self._stop_event.set()
        self._running = False
        self._camera.stop()

        if self._processing_thread:
            self._processing_thread.join(timeout=2.0)
            self._processing_thread = None

        self._state_machine.reset()
        logger.info("Spatial Interaction Engine stopped")

    def emergency_stop(self) -> None:
        """Emergency stop all gesture interactions."""
        self._safety.trigger_emergency_stop()
        self.stop()

    def reset_emergency(self) -> None:
        """Reset emergency stop and restart."""
        self._safety.reset_emergency_stop()
        self.start()

    def register_ui_target(self, target) -> None:
        """Register a gesture-interactive UI element."""
        self._ui_controller.register_target(target)

    def unregister_ui_target(self, target_id: str) -> None:
        """Unregister a UI element."""
        self._ui_controller.unregister_target(target_id)

    def _on_camera_frame(self, frame: np.ndarray, timestamp: float) -> None:
        """Callback for each new camera frame."""
        with self._frame_lock:
            self._latest_frame = frame

    def _processing_loop(self) -> None:
        """Main processing loop — runs in background thread."""
        logger.debug("Processing loop started")

        while not self._stop_event.is_set():
            try:
                frame = None
                with self._frame_lock:
                    if self._latest_frame is not None:
                        frame = self._latest_frame.copy()

                if frame is None:
                    time.sleep(0.01)
                    continue

                # Detect hands
                hands = self._tracker.detect(frame)

                # Recognize gesture
                now = time.time()
                event = self._recognizer.hands_to_event(hands, now) if hands else GestureEvent(
                    gesture=GestureType.NONE,
                    confidence=0.0,
                    timestamp=now,
                )

                # Safety check
                is_safe, reason = self._safety.validate_event(event)
                if not is_safe:
                    logger.debug("Event blocked by safety: %s", reason)
                    continue

                # Interpret gesture
                interpreted = self._interpreter.interpret(event)

                # Process through UI controller
                target_id = self._ui_controller.process_event(interpreted)

                # Emit callbacks
                if self._on_gesture_event:
                    try:
                        self._on_gesture_event(interpreted)
                    except Exception as e:
                        logger.error("Gesture event callback error: %s", e)

                if self._on_pointer_move and interpreted.pointer_position != (0.0, 0.0):
                    try:
                        self._on_pointer_move(interpreted.pointer_position)
                    except Exception as e:
                        logger.error("Pointer move callback error: %s", e)

                # Update FPS
                self._frame_count += 1
                elapsed = now - self._fps_start
                if elapsed >= 1.0:
                    self._fps = self._frame_count / elapsed
                    self._frame_count = 0
                    self._fps_start = now

                time.sleep(0.033)  # ~30fps processing

            except Exception as e:
                logger.error("Processing loop error: %s", e)
                time.sleep(0.1)

        logger.debug("Processing loop ended")

    def get_debug_info(self) -> dict:
        """Get debug information."""
        return {
            "running": self._running,
            "state": self._state_machine.state.name,
            "fps": round(self._fps, 1),
            "camera_fps": round(self._camera.fps, 1),
            "processing_ms": round(self._tracker.processing_time_ms, 1),
            "hand_count": len(self._recognizer._last_gesture.hand) if self._recognizer._last_gesture.hand else 0,
            "emergency_stop": self._safety.emergency_stop_active,
            "hovered_target": self._ui_controller.hovered_target,
            "selected_target": self._ui_controller.selected_target,
            "is_dragging": self._ui_controller.is_dragging,
        }
