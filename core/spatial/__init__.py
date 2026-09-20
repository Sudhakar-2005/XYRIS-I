"""
Spatial Screen Interaction Engine — AETHER-I

Camera-based hand gesture control for the existing AETHER-I desktop UI.
"""

from core.spatial.camera_manager import CameraManager
from core.spatial.hand_tracker import HandTracker
from core.spatial.gesture_recognizer import GestureRecognizer
from core.spatial.gesture_interpreter import GestureInterpreter
from core.spatial.interaction_state import InteractionState, GestureEvent
from core.spatial.ui_controller import UIInteractionController
from core.spatial.safety_controller import SafetyController
from core.spatial.engine import SpatialInteractionEngine

__all__ = [
    "CameraManager",
    "HandTracker",
    "GestureRecognizer",
    "GestureInterpreter",
    "InteractionState",
    "InteractionEvent",
    "UIInteractionController",
    "SafetyController",
    "SpatialInteractionEngine",
]
