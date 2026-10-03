"""Timeline: one bar per sync group showing when it plays in the movie. Drag a bar to shift
its start; click or drag on the ruler to scrub."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

LABEL_W = 150
RULER_H = 24
ROW_H = 30


def nice_step(span: float, target: int = 10) -> int:
    raw = max(span / target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return int(m * mag)
    return int(10 * mag)


class TimelineWidget(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setMouseTracking(True)
        self._drag = None

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(600, RULER_H + ROW_H * max(1, len(self.win.project.groups)) + 8)

    def refresh(self):
        n = len(self.win.project.groups)
        self.setMinimumHeight(RULER_H + ROW_H * max(1, n) + 8)
        self.setMaximumHeight(RULER_H + ROW_H * max(3, n + 1) + 8)  # room for one more group, no dead band
        self.update()

    # --- geometry -------------------------------------------------------------------------
    def _span(self) -> int:
        pr = self.win.project
        ends = [pr.group_map(g).end for g in pr.groups]
        return max([pr.n_frames, *ends, 1]) + 1

    def x_of(self, frame: float) -> float:
        return LABEL_W + frame * (self.width() - LABEL_W - 10) / self._span()

    def frame_at(self, x: float) -> int:
        return round((x - LABEL_W) * self._span() / max(self.width() - LABEL_W - 10, 1))

    def _rows(self):
        pr = self.win.project
        for i, g in enumerate(pr.groups.values()):
            yield i, g, QRectF(0, RULER_H + i * ROW_H, self.width(), ROW_H)

    def _bar(self, g, row: QRectF) -> QRectF:
        m = self.win.project.group_map(g.id)
        return QRectF(self.x_of(m.start), row.y() + 4, max(self.x_of(m.end) - self.x_of(m.start), 3), row.height() - 8)

    # --- painting -------------------------------------------------------------------------
    def paintEvent(self, _):
        from mdmovie.ui.theme import C
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pr = self.win.project
        p.fillRect(self.rect(), QColor(C.panel))
        p.fillRect(QRectF(0, 0, LABEL_W, self.height()), QColor(C.window))
        p.setPen(QPen(QColor(C.border)))
        p.drawLine(LABEL_W, 0, LABEL_W, self.height())
        p.drawLine(0, RULER_H, self.width(), RULER_H)
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() * 0.85)
        p.setFont(f)

        # ruler: major ticks with labels, minor ticks between
        span = self._span()
        step = nice_step(span)
        minor = max(1, step // 5)
        for fr in range(0, span + 1, minor):
            x = self.x_of(fr)
            major = fr % step == 0
            p.setPen(QPen(QColor(C.border if not major else C.faint)))
            p.drawLine(QPointF(x, RULER_H - (7 if major else 3)), QPointF(x, RULER_H))
            if major:
                p.setPen(QPen(QColor(C.border)))
                p.drawLine(QPointF(x, RULER_H), QPointF(x, self.height()))
                p.setPen(QPen(QColor(C.muted)))
                p.drawText(QRectF(x + 3, 0, 60, RULER_H - 4), int(Qt.AlignmentFlag.AlignVCenter), f"{fr}")
        p.setPen(QPen(QColor(C.muted)))
        p.drawText(QRectF(12, 0, LABEL_W - 16, RULER_H), int(Qt.AlignmentFlag.AlignVCenter), "SYNC GROUPS")
        p.setPen(QPen(QColor(C.faint)))
        p.drawText(QRectF(12, 0, LABEL_W - 20, RULER_H),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), "frame")

        n = pr.n_frames
        end_x = self.x_of(n)
        shade = QColor(C.stage)
        shade.setAlpha(160)
        p.fillRect(QRectF(end_x, RULER_H + 1, self.width() - end_x, self.height()), shade)

        sel = self.win.selection
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        for i, g, row in self._rows():
            members = [pp.name for pp in pr.group_members(g.id)]
            m = pr.group_map(g.id)
            col = QColor(g.color)
            if sel == ("group", g.id):
                hl = QColor(C.accent_soft)
                p.fillRect(QRectF(0, row.y(), self.width(), row.height()), hl)
            # label: colour dot + name
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(QRectF(12, row.center().y() - 4, 8, 8))
            p.setPen(QPen(QColor(C.text)))
            p.setFont(bold)
            p.drawText(QRectF(28, row.y(), LABEL_W - 34, row.height()),
                       int(Qt.AlignmentFlag.AlignVCenter), p.fontMetrics().elidedText(
                           g.name, Qt.TextElideMode.ElideRight, LABEL_W - 34))
            p.setFont(f)
            bar = self._bar(g, row)
            # held / looped extensions before and after the active range
            ext = QColor(col)
            ext.setAlpha(70)
            style = {"loop": Qt.BrushStyle.BDiagPattern, "hold": Qt.BrushStyle.SolidPattern}
            if g.before != "hide" and m.start > 0:
                p.fillRect(QRectF(self.x_of(0), bar.center().y() - 2, bar.x() - self.x_of(0), 4),
                           QBrush(ext, style.get(g.before, Qt.BrushStyle.SolidPattern)))
            if g.after != "hide":
                p.fillRect(QRectF(bar.right(), bar.center().y() - 2, self.x_of(span) - bar.right(), 4),
                           QBrush(ext, style.get(g.after, Qt.BrushStyle.SolidPattern)))
            p.setPen(QPen(col.lighter(125), 1))
            p.setBrush(col)
            p.drawRoundedRect(bar, 5, 5)
            p.setPen(QPen(QColor("#ffffff")))
            txt = f"{', '.join(members) or '(no panels)'}   ·   {m.lo:g}–{m.hi:g} ps"
            p.drawText(bar.adjusted(8, 0, -6, 0), int(Qt.AlignmentFlag.AlignVCenter),
                       p.fontMetrics().elidedText(txt, Qt.TextElideMode.ElideRight, int(bar.width() - 14)))

        # playhead: line + a pill on the ruler that shows the current frame (it would hide a tick label anyway)
        x = self.x_of(self.win.frame)
        p.setPen(QPen(QColor(C.accent), 1.5))
        p.drawLine(QPointF(x, RULER_H - 2), QPointF(x, self.height()))
        p.setFont(bold)
        text = str(self.win.frame)
        w = max(22, p.fontMetrics().horizontalAdvance(text) + 12)
        pill = QRectF(min(max(x - w / 2, LABEL_W + 1), self.width() - w - 1), 3, w, RULER_H - 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C.accent))
        p.drawRoundedRect(pill, 4, 4)
        p.drawPolygon(QPolygonF([QPointF(x - 4, pill.bottom() - 1), QPointF(x + 4, pill.bottom() - 1),
                                 QPointF(x, RULER_H - 1)]))
        p.setPen(QPen(QColor(C.accent_text)))
        p.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), text)

    # --- mouse ----------------------------------------------------------------------------
    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        for _, g, row in self._rows():
            if row.contains(pos):
                if pos.x() < LABEL_W:
                    self.win.select(("group", g.id))
                    return
                if self._bar(g, row).contains(pos):
                    self.win.select(("group", g.id))
                    self._drag = ("group", g.id, self.win.project.to_dict(), pos.x(), g.start)
                    return
        self._drag = ("seek",)
        self.win.set_frame(self.frame_at(pos.x()))

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is None:
            over_bar = any(self._bar(g, row).contains(pos) for _, g, row in self._rows())
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor if over_bar else Qt.CursorShape.ArrowCursor))
            return
        if self._drag[0] == "seek":
            self.win.set_frame(self.frame_at(pos.x()))
            return
        _, gid, _, x0, start0 = self._drag
        g = self.win.project.groups.get(gid)
        if g is None:
            return
        g.start = max(-10**6, start0 + self.frame_at(pos.x()) - self.frame_at(x0))
        self.win.on_project_changed(structural=False)

    def mouseReleaseEvent(self, e):
        if self._drag and self._drag[0] == "group":
            before = self._drag[2]
            after = self.win.project.to_dict()
            if before != after:
                self.win.push_snapshot("Shift sync group", before, after)
                self.win.inspector.rebuild()
        self._drag = None
