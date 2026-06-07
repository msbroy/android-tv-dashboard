"""Small custom widgets (zero extra deps beyond PySide6)."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class Sparkline(QWidget):
    """A lightweight rolling line chart drawn with QPainter."""

    def __init__(self, color="#4ea1ff", maxlen=90, fixed_max=None, parent=None):
        super().__init__(parent)
        self.values = deque(maxlen=maxlen)
        self.color = QColor(color)
        self.fixed_max = fixed_max
        self.setMinimumHeight(54)

    def push(self, v: float):
        self.values.append(float(v))
        self.update()

    def clear(self):
        self.values.clear()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0e131a"))
        w, h = self.width(), self.height()
        if len(self.values) < 2:
            return
        mx = self.fixed_max or (max(self.values) or 1.0)
        mx = max(mx, 1e-6)
        n = len(self.values)
        maxlen = self.values.maxlen or n
        step = w / max(1, (maxlen - 1))
        start = w - (n - 1) * step
        pts = []
        for i, v in enumerate(self.values):
            x = start + i * step
            y = h - (min(v, mx) / mx) * (h - 6) - 3
            pts.append(QPointF(x, y))

        path = QPainterPath()
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)

        fill = QPainterPath(path)
        fill.lineTo(pts[-1].x(), h)
        fill.lineTo(pts[0].x(), h)
        fill.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        c1 = QColor(self.color)
        c1.setAlpha(90)
        c2 = QColor(self.color)
        c2.setAlpha(8)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        p.fillPath(fill, grad)

        pen = QPen(self.color)
        pen.setWidth(2)
        p.setPen(pen)
        p.drawPath(path)
