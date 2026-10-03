"""Reusable editor widgets generated from Prop / Param schemas."""
from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy,
                               QSpinBox, QToolButton, QVBoxLayout, QWidget)


def settings() -> QSettings:
    return QSettings("mdmovie", "MD Movie Maker")


def last_dir(key: str = "last_dir") -> str:
    return str(settings().value(key, ""))


def remember_dir(path: str, key: str = "last_dir") -> None:
    import os
    settings().setValue(key, path if os.path.isdir(path) else os.path.dirname(path))


LABEL_WIDTH = 138   # every inspector form shares one label column, so fields line up across sections

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.tga *.tif *.tiff *.bmp *.webp);;All files (*)"


def note(text: str) -> QLabel:
    """Secondary, wrapped explanatory text."""
    lbl = QLabel(text)
    lbl.setObjectName("note")
    lbl.setWordWrap(True)
    return lbl


class CompactDoubleSpinBox(QDoubleSpinBox):
    """Shows 30 instead of 30.000 and 0.25 instead of 0.250, while keeping full precision."""

    def textFromValue(self, value: float) -> str:
        if self.specialValueText() and value == self.minimum():
            return self.specialValueText()
        text = f"{value:.{self.decimals()}f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"


class Section(QFrame):
    """Collapsible inspector section: an uppercase header that folds a form away."""
    _collapsed: dict[str, bool] = {}   # remembered per title for the session

    def __init__(self, title: str, collapsed: bool | None = None, parent=None):
        super().__init__(parent)
        from mdmovie.ui import icons
        self.setObjectName("section")
        self._title = title
        if collapsed is not None and title not in Section._collapsed:
            Section._collapsed[title] = collapsed
        self.header = QToolButton()
        self.header.setObjectName("sectionHeader")
        self.header.setText(title.upper())
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setIconSize(QSize(14, 14))
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.clicked.connect(self.toggle)
        self._icons = (icons.icon("chevron-right", "muted", 14), icons.icon("chevron-down", "muted", 14))
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.form.setContentsMargins(2, 0, 2, 12)
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(8)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(self.body)
        self._apply(Section._collapsed.get(title, False))

    def title(self) -> str:
        return self._title

    def add_row(self, label: str, widget: QWidget) -> None:
        lbl = QLabel(label)
        lbl.setObjectName("formLabel")
        lbl.setWordWrap(True)      # long labels wrap instead of pushing the fields out of view
        lbl.setFixedWidth(LABEL_WIDTH)
        lbl.ensurePolished()
        lbl.setMinimumHeight(lbl.heightForWidth(LABEL_WIDTH))
        self.form.addRow(lbl, widget)

    def add_widget(self, widget: QWidget) -> None:
        self.form.addRow(widget)

    def toggle(self):
        self._apply(not Section._collapsed.get(self._title, False))

    def _apply(self, collapsed: bool):
        Section._collapsed[self._title] = collapsed
        self.body.setVisible(not collapsed)
        self.header.setIcon(self._icons[0 if collapsed else 1])


class HeaderCard(QFrame):
    """Top of the inspector: icon, title and a one-line description of the selection."""

    def __init__(self, icon_name: str, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        from mdmovie.ui import icons
        self.setObjectName("headerCard")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        badge = QLabel()
        badge.setPixmap(icons.pixmap(icon_name, icons._colors["accent"], 22))
        badge.setFixedSize(QSize(36, 36))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"background: {_accent_soft()}; border-radius: 9px;")
        text = QVBoxLayout()
        text.setSpacing(1)
        t = QLabel(title)
        t.setObjectName("headerTitle")
        t.setWordWrap(True)
        text.addWidget(t)
        if subtitle:
            st = QLabel(subtitle)
            st.setObjectName("headerSub")
            st.setWordWrap(True)
            text.addWidget(st)
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(text, 1)


def _accent_soft() -> str:
    from mdmovie.ui import theme
    return theme.C.accent_soft


def button_row(*buttons: QPushButton) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for b in buttons:
        b.setMinimumWidth(10)
        lay.addWidget(b)
    return w


class ColorButton(QWidget):
    """Colour swatch that opens a colour dialog; optional colours can be cleared ("none")."""
    changed = Signal(str)

    def __init__(self, color: str, optional: bool = False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.btn = QPushButton()
        self.btn.setObjectName("colorButton")
        self.btn.setIconSize(QSize(30, 16))
        self.btn.clicked.connect(self._pick)
        lay.addWidget(self.btn, 1)
        if optional:
            clear = QToolButton()
            from mdmovie.ui import icons
            clear.setIcon(icons.icon("trash", "muted", 16))
            clear.setToolTip("No colour (transparent)")
            clear.clicked.connect(lambda: self.set_color("", emit=True))
            lay.addWidget(clear)
        self.set_color(color)

    def set_color(self, color: str, emit: bool = False) -> None:
        self.color = color or ""
        pm = QPixmap(60, 32)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, 29, 15, 4, 4)
        if self.color:
            p.fillPath(path, QColor(self.color))
        p.setPen(QColor(128, 128, 128, 160))
        p.drawPath(path)
        if not self.color:  # diagonal = "none"
            p.drawLine(4, 13, 26, 3)
        p.end()
        self.btn.setIcon(QIcon(pm))
        self.btn.setText("  " + (self.color or "none"))
        if emit:
            self.changed.emit(self.color)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color or "#ffffff"), self, "Choose colour",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self.set_color(c.name(QColor.NameFormat.HexArgb if c.alpha() < 255 else QColor.NameFormat.HexRgb),
                           emit=True)


class PathEdit(QWidget):
    changed = Signal(str)

    def __init__(self, value: str, mode: str = "dir", caption: str = "Choose", filt: str = "", parent=None):
        super().__init__(parent)
        self.mode, self.caption, self.filt = mode, caption, filt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(value or "")
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.edit.text().strip()))
        from mdmovie.ui import icons
        lay.addWidget(self.edit, 1)
        # mode: "dir", "file", or "dir|file" (two buttons: a folder of frames or a single picture)
        for m in (["dir", "file"] if mode == "dir|file" else [mode]):
            btn = QToolButton()
            btn.setIcon(icons.icon("image" if m == "file" and filt == IMAGE_FILTER else "open", "muted"))
            btn.setToolTip("Choose a folder" if (m == "dir" and mode == "dir|file") else
                           "Choose a single image file" if mode == "dir|file" else caption)
            btn.clicked.connect(lambda _=False, mm=m: self._browse(mm))
            lay.addWidget(btn)

    def _browse(self, mode: str | None = None):
        start = self.edit.text() or last_dir()
        if (mode or self.mode) == "dir":
            p = QFileDialog.getExistingDirectory(self, self.caption, start)
        else:
            p, _ = QFileDialog.getOpenFileName(self, self.caption, start, self.filt)
        if p:
            remember_dir(p)
            self.edit.setText(p)
            self.changed.emit(p)


class OptFloatEdit(QLineEdit):
    """Float field that may be left blank (meaning automatic)."""
    changed = Signal(object)

    def __init__(self, value, placeholder="auto", parent=None):
        super().__init__("" if value is None else f"{value:g}", parent)
        self.setPlaceholderText(placeholder)
        self.editingFinished.connect(self._emit)

    def _emit(self):
        t = self.text().strip()
        if not t:
            self.changed.emit(None)
            return
        try:
            self.changed.emit(float(t))
        except ValueError:
            self.setStyleSheet("color: #c33")
            return
        self.setStyleSheet("")


class DebouncedText(QPlainTextEdit):
    changed = Signal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(64)
        self._t = QTimer(self, singleShot=True, interval=350)
        self._t.timeout.connect(lambda: self.changed.emit(self.toPlainText()))
        self.textChanged.connect(self._t.start)


def make_editor(kind: str, value, on_change, *, options=(), minimum=-1e9, maximum=1e9, step=1.0,
                tooltip: str = "") -> QWidget:
    """Editor for one value. `on_change(new_value)` is called when the user commits a change."""
    if kind in ("str", "sel"):
        w = QLineEdit(str(value if value is not None else ""))
        if kind == "sel":
            w.setPlaceholderText("MDAnalysis selection, e.g. protein and name CA")
        w.editingFinished.connect(lambda: on_change(w.text()))
    elif kind == "text":
        w = DebouncedText(str(value or ""))
        w.changed.connect(on_change)
    elif kind == "int":
        w = QSpinBox()
        w.setRange(int(max(minimum, -2**31 + 1)), int(min(maximum, 2**31 - 1)))
        w.setValue(int(value))
        w.setKeyboardTracking(False)
        w.valueChanged.connect(on_change)
    elif kind == "float":
        w = CompactDoubleSpinBox()
        w.setDecimals(6 if step < 0.01 or 0 < abs(float(value)) < 0.01 else 3)
        w.setStepType(QDoubleSpinBox.StepType.AdaptiveDecimalStepType)
        w.setRange(minimum, maximum)
        w.setSingleStep(step)
        w.setValue(float(value))
        w.setKeyboardTracking(False)
        w.valueChanged.connect(on_change)
    elif kind == "optfloat":
        w = OptFloatEdit(value)
        w.changed.connect(on_change)
    elif kind == "bool":
        w = QCheckBox()
        w.setChecked(bool(value))
        w.toggled.connect(on_change)
    elif kind == "choice":
        w = QComboBox()
        w.addItems([str(o) for o in options])
        if value in options:
            w.setCurrentIndex(list(options).index(value))
        w.currentIndexChanged.connect(lambda i: on_change(options[i]))
    elif kind in ("color", "optcolor"):
        w = ColorButton(value, optional=kind == "optcolor")
        w.changed.connect(on_change)
    elif kind == "dir":
        w = PathEdit(value, "dir|file", "Choose image folder", IMAGE_FILTER)
        w.changed.connect(on_change)
    elif kind in ("file", "image"):
        w = PathEdit(value, "file", "Choose a file", IMAGE_FILTER if kind == "image" else "All files (*)")
        w.changed.connect(on_change)
    else:
        raise ValueError(f"no editor for {kind}")
    if tooltip:
        w.setToolTip(tooltip)
    return w
