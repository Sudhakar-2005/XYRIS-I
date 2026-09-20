"""
Camera Manager — Handles webcam capture for gesture detection.

Part of AETHER-I Spatial Screen Interaction Engine V1.
"""

import logging
import threading
import time
from typing import Optional, Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class CameraManager:
    """Manages webcam capture for hand tracking.

    Provides frames from the selected camera in a background thread.
    Thread-safe via callbacks and events.
    """

    def __init__(self, camera_index: int = 0):
        self._camera_index = camera_index
        self._cap: Optional[cv2.VideoCapture] = None
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._frame_timestamp: float = 0.0
        self._on_frame: Optional[Callable[[np.ndarray, float], None]] = None
        self._fps: float = 0.0
        self._frame_count: int = 0
        self._fps_start: float = 0.0
        self._width: int = 0
        self._height: int = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def resolution(self) -> tuple[int, int]:
        return (self._width, self._height)

    @property
    def latest_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def latest_timestamp(self) -> float:
        return self._frame_timestamp

    def set_frame_callback(self, callback: Callable[[np.ndarray, float], None]) -> None:
        """Set callback for each new frame: callback(frame, timestamp)."""
        self._on_frame = callback

    def start(self) -> bool:
        """Start camera capture. Returns True if successful."""
        if self._running:
            return True

        try:
            self._cap = cv2.VideoCapture(self._camera_index)
            if not self._cap.isOpened():
                logger.error("Failed to open camera %d", self._camera_index)
                return False

            self._width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self._height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            logger.info(
                "Camera %d opened: %dx%d",
                self._camera_index, self._width, self._height,
            )

            self._stop_event.clear()
            self._running = True
            self._frame_count = 0
            self._fps_start = time.time()
            self._thread = threading.Thread(
                target=self._capture_loop,
                daemon=True,
                name="camera-manager",
            )
            self._thread.start()
            return True

        except Exception as e:
            logger.error("Camera start error: %s", e)
            return False

    def stop(self) -> None:
        """Stop camera capture and release resources."""
        if not self._running:
            return

        self._stop_event.set()
        self._running = False

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        with self._lock:
            if self._cap:
                self._cap.release()
                self._cap = None
            self._frame = None

        logger.info("Camera stopped")

    def switch_camera(self, index: int) -> bool:
        """Switch to a different camera."""
        if index == self._camera_index and self._running:
            return True

        was_running = self._running
        if was_running:
            self.stop()

        self._camera_index = index

        if was_running:
            return self.start()
        return True

    def read_frame(self) -> Optional[np.ndarray]:
        """Read a single frame (synchronous)."""
        with self._lock:
            if self._cap and self._cap.isOpened():
                ret, frame = self._cap.read()
                if ret:
                    return frame
        return None

    def _capture_loop(self) -> None:
        """Background capture loop."""
        logger.debug("Capture loop started")

        while not self._stop_event.is_set():
            try:
                with self._lock:
                    if not self._cap or not self._cap.isOpened():
                        break
                    ret, frame = self._cap.read()

                if not ret or frame is None:
                    logger.warning("Failed to read frame")
                    time.sleep(0.1)
                    continue

                now = time.time()
                with self._lock:
                    self._frame = frame
                    self._frame_timestamp = now

                self._frame_count += 1
                elapsed = now - self._fps_start
                if elapsed >= 1.0:
                    self._fps = self._frame_count / elapsed
                    self._frame_count = 0
                    self._fps_start = now

                if self._on_frame:
                    try:
                        self._on_frame(frame, now)
                    except Exception as e:
                        logger.error("Frame callback error: %s", e)

                time.sleep(0.033)  # ~30fps cap

            except Exception as e:
                logger.error("Capture loop error: %s", e)
                time.sleep(0.1)

        logger.debug("Capture loop ended")

    def detect_cameras(self, max_scan: int = 5) -> list[int]:
        """Scan for available camera indices."""
        available = []
        for i in range(max_scan):
            try:
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    available.append(i)
                    cap.release()
            except Exception:
                pass
        return available
