"""Draws a whole movie frame. Both the live preview and the exporter use this, so the preview
is exactly what gets exported (the preview just uses a smaller `scale`)."""
from __future__ import annotations

import traceback

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from mdmovie.core import layout as L
from mdmovie.panels.base import RenderContext, draw_message


def output_size(project, scale: float = 1.0) -> tuple[int, int]:
    w, h = project.size
    if scale == 1.0:
        return w, h
    return max(2, round(w * scale)), max(2, round(h * scale))


def cell_rects(project, w: int, h: int, scale: float):
    """[(leaf path, panel id or None, QRectF in output pixels)] after applying the gutter."""
    g = project.gutter * scale
    inner = (g / 2, g / 2, w - g, h - g)
    out = []
    for path, leaf, (x, y, rw, rh) in L.iter_leaves(project.layout):
        r = QRectF(inner[0] + x * inner[2] + g / 2, inner[1] + y * inner[3] + g / 2,
                   rw * inner[2] - g, rh * inner[3] - g)
        out.append((path, leaf.panel, r))
    return out


def overlay_rect(ov, w: int, h: int) -> QRectF:
    return QRectF(ov.x * w, ov.y * h, ov.w * w, ov.h * h)


def cell_aspect(project, panel_id: str) -> float | None:
    """Width/height of the drawable area of the cell (or overlay) holding a panel."""
    w, h = project.size
    panel = project.panels.get(panel_id)
    pad = 2 * (panel.props.get("padding", 0) if panel else 0)
    for _, pid, r in cell_rects(project, w, h, 1.0):
        if pid == panel_id and r.height() - pad > 0:
            return (r.width() - pad) / (r.height() - pad)
    for ov in project.overlays:
        if ov.panel == panel_id:
            r = overlay_rect(ov, w, h)
            return (r.width() - pad) / max(r.height() - pad, 1)
    return None


def draw_panel(painter: QPainter, project, panel, rect: QRectF, gframe: int, scale: float) -> None:
    painter.save()
    painter.setClipRect(rect)
    p = panel.props
    if p.get("background"):
        painter.fillRect(rect, QColor(p["background"]))
    t = project.group_time(panel.group, gframe)
    hidden = t is None
    pad = p.get("padding", 0) * scale
    inner = rect.adjusted(pad, pad, -pad, -pad)
    if not hidden and inner.width() > 2 and inner.height() > 2:
        try:
            panel.render(painter, inner, RenderContext(project, gframe, t, scale))
        except Exception as e:  # a broken panel must not break the movie
            traceback.print_exc()
            draw_message(painter, inner, f"{panel.name}: {type(e).__name__}: {e}", scale, "#c33")
    bw = p.get("border_width", 0) * scale
    if bw > 0:
        painter.setPen(QPen(QColor(p.get("border_color", "#333")), bw))
        painter.drawRect(rect.adjusted(bw / 2, bw / 2, -bw / 2, -bw / 2))
    painter.restore()


def render_frame(project, gframe: int, scale: float = 1.0) -> QImage:
    w, h = output_size(project, scale)
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(project.background))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    try:
        for _, pid, rect in cell_rects(project, w, h, scale):
            panel = project.panels.get(pid) if pid else None
            if panel is not None and rect.width() > 0 and rect.height() > 0:
                draw_panel(painter, project, panel, rect, gframe, scale)
        for ov in project.overlays:
            panel = project.panels.get(ov.panel)
            if panel is not None:
                draw_panel(painter, project, panel, overlay_rect(ov, w, h), gframe, scale)
    finally:
        painter.end()
    return img
