"""Playback controls: play/pause, step, scrub slider and a timecode readout."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget

from mdmovie.ui import icons


def timecode(frame: int, fps: float) -> str:
    """mm:ss.ff (ff = frame within the second)."""
    fps_i = max(1, round(fps))
    total_s, ff = divmod(frame, fps_i)
    m, s = divmod(total_s, 60)
    return f"{m:02d}:{s:02d}.{ff:02d}"


class TransportBar(QWidget):
    seek = Signal(int)
    play_toggled = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setObjectName("transport")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 14, 8)
        lay.setSpacing(4)

        def btn(name, tip, cb):
            b = QToolButton()
            b.setIcon(icons.icon(name))
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(cb)
            lay.addWidget(b)
            return b

        self._buttons = [btn("first", "First frame (Home)", lambda: self.seek.emit(0)),
                         btn("prev", "Previous frame (←)", lambda: self.seek.emit(self.frame.value() - 1))]
        self.play = QToolButton()
        self.play.setObjectName("playButton")
        self.play.setCheckable(True)
        self.play.setFixedSize(QSize(34, 34))
        self.play.setIconSize(QSize(16, 16))
        self.play.setToolTip("Play / pause (Space)")
        self.play.clicked.connect(lambda: self.play_toggled.emit(self.play.isChecked()))
        lay.addSpacing(2)
        lay.addWidget(self.play)
        lay.addSpacing(2)
        self._buttons += [btn("next", "Next frame (→)", lambda: self.seek.emit(self.frame.value() + 1)),
                          btn("last", "Last frame (End)", lambda: self.seek.emit(self.slider.maximum()))]

        readout = QVBoxLayout()
        readout.setSpacing(0)
        readout.setContentsMargins(14, 0, 14, 0)
        self.tc = QLabel("00:00.00")
        self.tc.setObjectName("timecode")
        self.info = QLabel()
        self.info.setObjectName("frameInfo")
        readout.addWidget(self.tc)
        readout.addWidget(self.info)
        lay.addLayout(readout)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self.seek)
        lay.addWidget(self.slider, 1)
        self.frame = QSpinBox()
        self.frame.setKeyboardTracking(False)
        self.frame.setToolTip("Current movie frame")
        self.frame.valueChanged.connect(self.seek)
        self.frame.setMinimumWidth(72)
        lay.addSpacing(8)
        lay.addWidget(self.frame)
        self.loop = QToolButton()
        self.loop.setCheckable(True)
        self.loop.setChecked(True)
        self.loop.setIconSize(QSize(18, 18))
        self.loop.setToolTip("Loop playback")
        self.loop.toggled.connect(self._loop_icon)
        lay.addWidget(self.loop)
        self.perf = QLabel()
        self.perf.setObjectName("perf")
        self.perf.setMinimumWidth(64)
        self.perf.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.perf)
        self.refresh_icons()

    def refresh_icons(self):
        """Re-tint icons after a theme change."""
        names = ("first", "prev", "next", "last")
        for b, n in zip(self._buttons, names):
            b.setIcon(icons.icon(n))
        self.set_playing(self.play.isChecked())
        self._loop_icon(self.loop.isChecked())

    def _loop_icon(self, on: bool):
        self.loop.setIcon(icons.icon("loop", "accent" if on else "muted"))

    def set_state(self, frame: int, n_frames: int, fps: float) -> None:
        for w in (self.slider, self.frame):
            w.blockSignals(True)
            w.setRange(0, max(0, n_frames - 1))
            w.setValue(frame)
            w.blockSignals(False)
        self.tc.setText(timecode(frame, fps))
        self.info.setText(f"frame {frame} / {max(0, n_frames - 1)} · {timecode(n_frames, fps)}")

    def set_playing(self, on: bool) -> None:
        self.play.setChecked(on)
        self.play.setIcon(icons.icon("pause" if on else "play", "on_accent"))
