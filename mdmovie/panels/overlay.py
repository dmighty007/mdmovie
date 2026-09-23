"""The moving point + trail drawn over a 2-D map (image overlay and CV map panels)."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

from mdmovie.panels.base import Prop

MARKER_SHAPES = ("circle", "square", "diamond", "triangle", "star", "cross")
TRAIL_STYLES = ("fading line", "line", "fading dots", "none")


def trail_props(section: str) -> list[Prop]:
    return [
        Prop("x_col", "int", 0, "X data: column #", maximum=999, section=section),
        Prop("y_col", "int", 1, "Y data: column #", maximum=999, section=section),
        Prop("marker", "choice", "circle", "Current point", MARKER_SHAPES, section=section),
        Prop("marker_size", "float", 16.0, "Point size (px)", minimum=1, maximum=200, section=section),
        Prop("marker_color", "color", "#ff3b30", "Point colour", section=section),
        Prop("marker_edge", "color", "#ffffff", "Point outline", section=section),
        Prop("trail", "choice", "fading line", "Trail", TRAIL_STYLES, section=section),
        Prop("trail_length", "int", 40, "Trail length (data points, 0 = all)", maximum=10**7, section=section),
        Prop("trail_color", "optcolor", "", "Trail colour (blank = point colour)", section=section),
        Prop("trail_width", "float", 2.5, "Trail width (px)", minimum=0.1, maximum=50, step=0.5, section=section),
        Prop("path", "bool", False, "Show whole path faintly", section=section),
        Prop("path_color", "color", "#ffffff", "Path colour", section=section),
        Prop("path_alpha", "float", 0.35, "Path opacity", minimum=0, maximum=1, step=0.05, section=section),
    ]


def trail_range(k: int, p: dict) -> range:
    n = int(p.get("trail_length", 40))
    return range(0 if n <= 0 else max(0, k - n), k + 1)


def _marker_path(shape: str, c: QPointF, r: float) -> QPainterPath:
    path = QPainterPath()
    if shape == "square":
        path.addRect(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r))
    elif shape == "diamond":
        path.addPolygon(QPolygonF([QPointF(c.x(), c.y() - r * 1.2), QPointF(c.x() + r, c.y()),
                                   QPointF(c.x(), c.y() + r * 1.2), QPointF(c.x() - r, c.y())]))
        path.closeSubpath()
    elif shape == "triangle":
        path.addPolygon(QPolygonF([QPointF(c.x(), c.y() - r * 1.2), QPointF(c.x() + r * 1.1, c.y() + r * 0.8),
                                   QPointF(c.x() - r * 1.1, c.y() + r * 0.8)]))
        path.closeSubpath()
    elif shape == "star":
        pts = []
        for i in range(10):
            a = -np.pi / 2 + i * np.pi / 5
            rr = r * 1.25 if i % 2 == 0 else r * 0.5
            pts.append(QPointF(c.x() + rr * np.cos(a), c.y() + rr * np.sin(a)))
        path.addPolygon(QPolygonF(pts))
        path.closeSubpath()
    elif shape == "cross":
        for dx, dy in ((1, 1), (1, -1)):
            path.moveTo(c.x() - r * dx, c.y() - r * dy)
            path.lineTo(c.x() + r * dx, c.y() + r * dy)
    else:
        path.addEllipse(c, r, r)
    return path


MAX_PATH_POINTS = 20000


def draw_point_and_trail(painter: QPainter, px: np.ndarray, py: np.ndarray, k: int, p: dict,
                         scale: float) -> None:
    """px, py: every data point in widget pixels; k: index of the current point."""
    if len(px) == 0 or k < 0:
        return
    pt = lambda i: QPointF(float(px[i]), float(py[i]))  # noqa: E731
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    marker_color = QColor(p.get("marker_color", "#ff3b30"))
    trail_color = QColor(p.get("trail_color") or p.get("marker_color", "#ff3b30"))
    width = max(0.5, p.get("trail_width", 2.5) * scale)

    if p.get("path"):
        pc = QColor(p.get("path_color", "#ffffff"))
        pc.setAlphaF(p.get("path_alpha", 0.35))
        step = max(1, len(px) // MAX_PATH_POINTS)
        poly = QPolygonF([QPointF(float(a), float(b)) for a, b in zip(px[::step], py[::step])])
        painter.setPen(QPen(pc, max(0.5, width * 0.5)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(poly)

    style = p.get("trail", "fading line")
    idx = list(trail_range(k, p))
    if style != "none" and len(idx) > 1:
        n = len(idx)
        if style in ("fading line", "line"):
            for j in range(1, n):
                c = QColor(trail_color)
                if style == "fading line":
                    c.setAlphaF(0.08 + 0.92 * j / (n - 1))
                pen = QPen(c, width)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(pt(idx[j - 1]), pt(idx[j]))
        else:  # fading dots
            painter.setPen(Qt.PenStyle.NoPen)
            for j in range(n - 1):
                c = QColor(trail_color)
                c.setAlphaF(0.1 + 0.8 * j / max(n - 2, 1))
                painter.setBrush(c)
                painter.drawEllipse(pt(idx[j]), width * 0.9, width * 0.9)

    r = max(1.0, p.get("marker_size", 16) * scale / 2)
    shape = p.get("marker", "circle")
    mp = _marker_path(shape, pt(k), r)
    if shape == "cross":
        painter.setPen(QPen(QColor(p.get("marker_edge", "#ffffff")), max(1.0, r * 0.55)))
        painter.drawPath(mp)
        painter.setPen(QPen(marker_color, max(1.0, r * 0.3)))
        painter.drawPath(mp)
    else:
        painter.setPen(QPen(QColor(p.get("marker_edge", "#ffffff")), max(1.0, 1.6 * scale)))
        painter.setBrush(marker_color)
        painter.drawPath(mp)
    painter.restore()
