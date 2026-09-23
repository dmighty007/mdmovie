"""Calibrate a pre-rendered plot image (e.g. a free-energy surface exported from matplotlib):
mark the rectangle of its plot area and type the axis values at the edges. The data is drawn
live on top, so a correct calibration is easy to see."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel,
                               QVBoxLayout)

from mdmovie.core import crop as C
from mdmovie.sources.image_sequence import IMAGE_CACHE
from mdmovie.ui.crop_dialog import CropCanvas


class CalibrationCanvas(CropCanvas):
    """Crop canvas that also draws the data points mapped through the current calibration."""

    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    def paintEvent(self, e):
        super().paintEvent(e)
        data = self.dialog.data
        if data is None or not self.image:
            return
        _, x, y, _, _ = data
        x0, x1, y0, y1 = self.dialog.ranges()
        rx, ry, rw, rh = self.rect
        px = rx + (x - x0) / ((x1 - x0) or 1) * rw
        py = ry + (1 - (y - y0) / ((y1 - y0) or 1)) * rh
        step = max(1, len(px) // 4000)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 59, 48, 170))
        v, s = self._view()
        for a, b in zip(px[::step], py[::step]):
            p.drawEllipse(QPointF(v.x() + a * s, v.y() + b * s), 2.2, 2.2)
        # axis-value labels at the edges of the box
        r = self.to_widget(self.rect)
        p.setPen(QPen(QColor("#ffffff")))
        for text, pos in ((f"x = {x0:g}", QPointF(r.left() + 4, r.bottom() - 6)),
                          (f"x = {x1:g}", QPointF(r.right() - 60, r.bottom() - 6)),
                          (f"y = {y1:g}", QPointF(r.left() + 4, r.top() + 16))):
            p.drawText(pos, text)


class CalibrateDialog(QDialog):
    def __init__(self, image_path: str, calib, ranges, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibrate data overlay")
        self.resize(920, 720)
        self.data = data
        img = IMAGE_CACHE.get(image_path)
        self.canvas = CalibrationCanvas(self)
        self.canvas.set_image(img)
        self.canvas.set_rect(C.denormalize(calib or [0, 0, 1, 1], img.width(), img.height()))
        self.canvas.rect_changed.connect(self.canvas.update)

        self.spins = []
        grid = QGridLayout()
        labels = ("X at left edge", "X at right edge", "Y at bottom edge", "Y at top edge")
        for i, (lab, val) in enumerate(zip(labels, ranges)):
            sp = QDoubleSpinBox()
            sp.setRange(-1e9, 1e9)
            sp.setDecimals(4)
            sp.setValue(float(val))
            sp.valueChanged.connect(self.canvas.update)
            self.spins.append(sp)
            grid.addWidget(QLabel(lab), i // 2, (i % 2) * 2)
            grid.addWidget(sp, i // 2, (i % 2) * 2 + 1)
        help_text = QLabel(
            "1. Drag a box exactly over the plot area of the picture (the inside of its axes).\n"
            "2. Type the axis values at the box edges (read them off the picture's tick labels).\n"
            + ("3. Check that the red dots (your data) land where the data should be."
               if data is not None else "Add the x/y series to see your data drawn here while you calibrate."))
        help_text.setObjectName("note")
        help_text.setWordWrap(True)
        fit = QDialogButtonBox()
        if data is not None:
            b = fit.addButton("Use data range", QDialogButtonBox.ButtonRole.ActionRole)
            b.setToolTip("Set the axis values to the data's min/max (for pictures made from the same data)")
            b.clicked.connect(self._data_range)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(help_text)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(grid)
        bottom = QHBoxLayout()
        bottom.addWidget(fit)
        bottom.addStretch(1)
        bottom.addWidget(bb)
        lay.addLayout(bottom)

    def ranges(self) -> tuple[float, float, float, float]:
        return tuple(sp.value() for sp in self.spins)

    def _data_range(self):
        _, x, y, _, _ = self.data
        for sp, v in zip(self.spins, (np.min(x), np.max(x), np.min(y), np.max(y))):
            sp.setValue(float(v))

    def result(self):
        bw, bh = self.canvas.bounds
        calib = [round(v, 6) for v in C.normalize(self.canvas.rect, bw, bh)]
        return calib, self.ranges()
