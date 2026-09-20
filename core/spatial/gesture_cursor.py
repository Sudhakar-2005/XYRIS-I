"""
Gesture Cursor Overlay — Futuristic pointer for gesture interaction.

Part of AETHER-I Spatial Screen Interaction Engine V1.
"""

from PyQt6.QtCore import Qt, QPointF, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath
from PyQt6.QtWidgets import QWidget


class GestureCursorOverlay(QWidget):
    """Transparent overlay showing the gesture pointer and state.

    Draws a futuristic cursor that indicates:
    - POINTER: thin ring following index finger
    - HOVER: filled ring when over interactive element
    - SELECT: pulse animation on pinch
    - DRAG: trail effect during drag
    """

    gesture_clicked = pyqtSignal()

    STATES = {
        "hidden": QColor(0, 0, 0, 0),
        "pointer": QColor(0, 212, 255, 180),     # Cyan
        "hover": QColor(0, 212, 255, 240),        # Bright cyan
        "select": QColor(255, 255, 255, 255),     # White flash
        "drag": QColor(0, 180, 220, 200),         # Teal
        "paused": QColor(128, 128, 128, 120),     # Gray
        "error": QColor(255, 60, 60, 180),        # Red
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._position = QPointF(0, 0)
        self._target_position = QPointF(0, 0)
        self._state = "hidden"
        self._size = 24
        self._inner_size = 6
        self._opacity = 0.0
        self._trail: list[QPointF] = []
        self._max_trail = 8
        self._pulse = 0.0
        self._visible = False

        # Smoothing animation
        self._anim = QPropertyAnimation(self, b"smoothPos")
        self._anim.setDuration(80)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_state(self, state: str) -> None:
        """Set cursor state: hidden, pointer, hover, select, drag, paused, error."""
        if state == self._state:
            return
        old = self._state
        self._state = state

        if state == "select":
            self._pulse = 1.0
        elif state == "hidden":
            self._opacity = 0.0
            self._trail.clear()
        elif old == "hidden":
            self._opacity = 1.0

        self.update()

    def move_to(self, x: float, y: float) -> None:
        """Move cursor to normalized coordinates (0-1)."""
        if not self.parent():
            return

        pw = self.parent().width()
        ph = self.parent().height()
        self._target_position = QPointF(x * pw, y * ph)

        # Add to trail
        if self._state == "drag":
            self._trail.append(self._position)
            if len(self._trail) > self._max_trail:
                self._trail.pop(0)

        # Smooth interpolation
        dx = self._target_position.x() - self._position.x()
        dy = self._target_position.y() - self._position.y()
        self._position = self._position + QPointF(dx * 0.35, dy * 0.35)

        self._visible = True
        self.update()

    def paintEvent(self, event):
        if not self._visible or self._state == "hidden":
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Fade opacity
        if self._state in ("pointer", "hover", "drag"):
            self._opacity = min(1.0, self._opacity + 0.1)
        elif self._state == "paused":
            self._opacity = max(0.3, self._opacity - 0.02)

        color = self.STATES.get(self._state, self.STATES["pointer"])
        color.setAlphaF(min(color.alphaF(), self._opacity))

        cx = self._position.x()
        cy = self._position.y()

        # Draw trail for drag
        if self._state == "drag" and len(self._trail) > 1:
            trail_pen = QPen(QColor(0, 212, 255, 60), 2)
            p.setPen(trail_pen)
            for i in range(1, len(self._trail)):
                alpha = int(60 * (i / len(self._trail)))
                trail_color = QColor(0, 212, 255, alpha)
                p.setPen(QPen(trail_color, 2))
                p.drawLine(self._trail[i - 1], self._trail[i])

        # Pulse animation
        if self._pulse > 0:
            pulse_size = self._size + (1.0 - self._pulse) * 20
            pulse_color = QColor(255, 255, 255, int(self._pulse * 100))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(pulse_color))
            p.drawEllipse(QPointF(cx, cy), pulse_size, pulse_size)
            self._pulse = max(0, self._pulse - 0.05)

        # Outer ring
        pen = QPen(color, 2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        ring_size = self._size if self._state != "hover" else self._size + 4
        p.drawEllipse(QPointF(cx, cy), ring_size, ring_size)

        # Inner dot
        inner_color = QColor(color)
        inner_color.setAlphaF(min(1.0, self._opacity))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(inner_color))
        p.drawEllipse(QPointF(cx, cy), self._inner_size, self._inner_size)

        # Crosshair lines for pointer state
        if self._state == "pointer":
            line_color = QColor(0, 212, 255, int(self._opacity * 100))
            p.setPen(QPen(line_color, 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(cx - ring_size - 6, cy), QPointF(cx - self._inner_size - 2, cy))
            p.drawLine(QPointF(cx + self._inner_size + 2, cy), QPointF(cx + ring_size + 6, cy))
            p.drawLine(QPointF(cx, cy - ring_size - 6), QPointF(cx, cy - self._inner_size - 2))
            p.drawLine(QPointF(cx, cy + self._inner_size + 2), QPointF(cx, cy + ring_size + 6))

        # "PAUSED" text
        if self._state == "paused":
            p.setPen(QPen(QColor(180, 180, 180, int(self._opacity * 180)), 1))
            p.drawText(QPointF(cx + ring_size + 8, cy + 4), "PAUSED")

        p.end()
