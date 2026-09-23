"""Crop tool for image-sequence panels, with an aspect-ratio lock."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QPushButton, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from mdmovie.core import crop as C
from mdmovie.sources.image_sequence import IMAGE_CACHE, qimage_to_array

HANDLE_PX = 9
CURSORS = {"nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
           "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
           "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
           "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
           "move": Qt.CursorShape.SizeAllCursor}


class CropCanvas(QWidget):
    """Shows an image with a draggable crop rectangle (image pixel coordinates)."""
    rect_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 320)
        self.setMouseTracking(True)
        self.image: QImage | None = None
        self.rect = (0.0, 0.0, 1.0, 1.0)
        self.aspect: float | None = None
        self._drag = None  # (handle, press image point, rect at press)

    @property
    def bounds(self):
        return (self.image.width(), self.image.height()) if self.image else (1, 1)

    def set_image(self, img: QImage) -> None:
        self.image = img
        self.update()

    def set_rect(self, rect) -> None:
        self.rect = C.clamp_rect(rect, self.bounds)
        self.rect_changed.emit()
        self.update()

    # --- coordinate mapping --------------------------------------------------------------
    def _view(self) -> tuple[QRectF, float]:
        bw, bh = self.bounds
        s = min((self.width() - 20) / bw, (self.height() - 20) / bh)
        return QRectF((self.width() - bw * s) / 2, (self.height() - bh * s) / 2, bw * s, bh * s), s

    def to_img(self, p: QPointF):
        v, s = self._view()
        return (p.x() - v.x()) / s, (p.y() - v.y()) / s

    def to_widget(self, rect) -> QRectF:
        v, s = self._view()
        x, y, w, h = rect
        return QRectF(v.x() + x * s, v.y() + y * s, w * s, h * s)

    def _handles(self) -> dict[str, QPointF]:
        r = self.to_widget(self.rect)
        cx, cy = r.center().x(), r.center().y()
        return {"nw": r.topLeft(), "n": QPointF(cx, r.top()), "ne": r.topRight(), "e": QPointF(r.right(), cy),
                "se": r.bottomRight(), "s": QPointF(cx, r.bottom()), "sw": r.bottomLeft(),
                "w": QPointF(r.left(), cy)}

    def _hit(self, p: QPointF) -> str | None:
        for name, hp in self._handles().items():
            if abs(hp.x() - p.x()) <= HANDLE_PX and abs(hp.y() - p.y()) <= HANDLE_PX:
                return name
        return "move" if self.to_widget(self.rect).contains(p) else None

    # --- events ---------------------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        from mdmovie.ui.theme import C
        p.fillRect(self.rect_widget(), QColor(C.stage))
        if not self.image:
            return
        v, _ = self._view()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(v, self.image)
        r = self.to_widget(self.rect)
        shade = QPainterPath()
        shade.addRect(v)
        inner = QPainterPath()
        inner.addRect(r)
        p.fillPath(shade - inner, QColor(0, 0, 0, 140))
        p.setPen(QPen(QColor("#ffffff"), 1, Qt.PenStyle.DashLine))
        for i in (1, 2):  # rule-of-thirds guides
            p.drawLine(QPointF(r.left() + r.width() * i / 3, r.top()), QPointF(r.left() + r.width() * i / 3, r.bottom()))
            p.drawLine(QPointF(r.left(), r.top() + r.height() * i / 3), QPointF(r.right(), r.top() + r.height() * i / 3))
        p.setPen(QPen(QColor("#4c8bf5"), 2))
        p.drawRect(r)
        p.setBrush(QColor("#ffffff"))
        p.setPen(QPen(QColor("#4c8bf5"), 1.5))
        for hp in self._handles().values():
            p.drawRect(QRectF(hp.x() - 4, hp.y() - 4, 8, 8))

    def rect_widget(self):
        return QRectF(0, 0, self.width(), self.height())

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.image:
            return
        pos = e.position()
        h = self._hit(pos)
        ix, iy = self.to_img(pos)
        if h is None:  # start a new rectangle from this point
            self.rect = C.clamp_rect((ix, iy, C.MIN_SIZE, C.MIN_SIZE), self.bounds)
            h = "se"
        self._drag = (h, (ix, iy), self.rect)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if not self._drag:
            h = self._hit(pos)
            self.setCursor(QCursor(CURSORS.get(h, Qt.CursorShape.CrossCursor)))
            return
        h, (px, py), r0 = self._drag
        ix, iy = self.to_img(pos)
        if h == "move":
            self.rect = C.move_rect(r0, ix - px, iy - py, self.bounds)
        else:
            self.rect = C.resize_rect(r0, h, ix, iy, self.aspect, self.bounds)
        self.rect_changed.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None


class CropDialog(QDialog):
    def __init__(self, paths: list[str], crop, cell_aspect: float | None, parent=None, start_index: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Crop image sequence")
        self.resize(900, 680)
        self.paths = paths
        self.cell_aspect = cell_aspect
        self.canvas = CropCanvas()
        self.canvas.rect_changed.connect(self._update_readout)

        self.lock = QToolButton()
        self.lock.setText("Lock aspect")
        self.lock.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.lock.setCheckable(True)
        self.lock.toggled.connect(self._aspect_changed)
        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems([k for k in C.ASPECT_PRESETS if k != "Free"] + ["Match panel cell", "Custom"])
        if cell_aspect:
            self.aspect_combo.setCurrentText("Match panel cell")
        self.aspect_combo.currentTextChanged.connect(self._aspect_changed)
        self.cw, self.ch = QDoubleSpinBox(), QDoubleSpinBox()
        for s, v in ((self.cw, 16), (self.ch, 9)):
            s.setRange(0.01, 10000)
            s.setValue(v)
            s.valueChanged.connect(self._aspect_changed)

        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setRange(0, max(0, len(paths) - 1))
        self.frame_slider.valueChanged.connect(self._load_frame)
        self.frame_label = QLabel()

        self.tol = QSpinBox()
        self.tol.setRange(0, 255)
        self.tol.setValue(12)
        self.tol.setToolTip("Colour difference from the background that counts as content")
        from mdmovie.ui import icons
        trim = QPushButton(icons.icon("crop"), "Auto-trim")
        trim.setToolTip("Crop to the content (non-background pixels) of ~20 frames across the sequence")
        trim.clicked.connect(self._auto_trim)
        reset = QPushButton("Reset")
        reset.clicked.connect(lambda: self.canvas.set_rect((0, 0, *self.canvas.bounds)))
        self.readout = QLabel()

        row1 = QHBoxLayout()
        row1.addWidget(self.lock)
        row1.addWidget(self.aspect_combo)
        self.colon = QLabel(":")
        row1.addWidget(self.cw)
        row1.addWidget(self.colon)
        row1.addWidget(self.ch)
        row1.addStretch(1)
        row1.addWidget(QLabel("Tolerance"))
        row1.addWidget(self.tol)
        row1.addWidget(trim)
        row1.addWidget(reset)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Preview frame"))
        row2.addWidget(self.frame_slider, 1)
        row2.addWidget(self.frame_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(row1)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(row2)
        bottom = QHBoxLayout()
        bottom.addWidget(self.readout, 1)
        bottom.addWidget(buttons)
        lay.addLayout(bottom)

        self._load_frame(start_index)
        self.frame_slider.setValue(start_index)
        self.canvas.set_rect(C.denormalize(crop, *self.canvas.bounds))
        if crop and cell_aspect is None:
            self.aspect_combo.setCurrentText("Custom")
            x, y, w, h = self.canvas.rect
            self.cw.setValue(round(w))
            self.ch.setValue(round(h))
        self._aspect_changed()

    def aspect(self) -> float | None:
        if not self.lock.isChecked():
            return None
        name = self.aspect_combo.currentText()
        if name == "Match panel cell":
            return self.cell_aspect
        if name == "Custom":
            return self.cw.value() / self.ch.value()
        return C.ASPECT_PRESETS[name]

    def _aspect_changed(self, *_):
        from mdmovie.ui import icons
        self.lock.setIcon(icons.icon("lock", "accent") if self.lock.isChecked() else icons.icon("unlock", "muted"))
        custom = self.aspect_combo.currentText() == "Custom"
        for w in (self.cw, self.colon, self.ch):
            w.setVisible(custom)
        a = self.aspect()
        self.canvas.aspect = a
        if a:
            self.canvas.set_rect(C.fit_aspect(self.canvas.rect, a, self.canvas.bounds))
        self._update_readout()

    def _load_frame(self, i: int):
        if not self.paths:
            return
        img = IMAGE_CACHE.get(self.paths[i])
        if img is not None:
            same = self.canvas.image is not None and img.size() == self.canvas.image.size()
            self.canvas.set_image(img)
            if not same:
                self.canvas.set_rect(self.canvas.rect)
        self.frame_label.setText(f"{i + 1}/{len(self.paths)}")

    def _auto_trim(self):
        n = len(self.paths)
        idx = sorted({round(i * (n - 1) / 19) for i in range(20)}) if n > 1 else [0]
        frames = []
        for i in idx:
            img = IMAGE_CACHE.get(self.paths[i])
            if img is not None and img.size() == self.canvas.image.size():
                frames.append(qimage_to_array(img))
        r = C.auto_trim(frames, self.tol.value(), aspect=self.aspect())
        if r:
            self.canvas.set_rect(r)

    def _update_readout(self):
        x, y, w, h = C.to_int_rect(self.canvas.rect, *self.canvas.bounds)
        bw, bh = self.canvas.bounds
        self.readout.setText(f"Crop: x={x} y={y}  {w}×{h} px  (aspect {w / h:.3f})  of {bw}×{bh}")

    def result_crop(self):
        bw, bh = self.canvas.bounds
        x, y, w, h = self.canvas.rect
        if w >= bw - 0.5 and h >= bh - 0.5:
            return None
        return [round(v, 6) for v in C.normalize(self.canvas.rect, bw, bh)]
