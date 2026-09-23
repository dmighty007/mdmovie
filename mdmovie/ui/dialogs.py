"""Dialogs: trajectory loading, analysis series (preset + parameters), trajectory timing match."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
                               QSpinBox, QVBoxLayout)

from mdmovie.analysis.registry import presets_for
from mdmovie.panels import Series
from mdmovie.ui.widgets import ColorButton, CompactDoubleSpinBox, PathEdit, last_dir, make_editor, remember_dir

NO_TRAJ = "(none: preset reads its own file)"

TOPOLOGY_FILTER = ("Topology (*.pdb *.gro *.psf *.prmtop *.parm7 *.top *.tpr *.mol2 *.pqr *.data *.xyz *.crd);;"
                   "All files (*)")
TRAJ_FILTER = "Trajectories (*.xtc *.trr *.dcd *.nc *.netcdf *.ncdf *.mdcrd *.crd *.lammpstrj *.xyz *.pdb *.gro);;All files (*)"


class TrajectoryDialog(QDialog):
    def __init__(self, parent=None, topology="", trajectories=(), name=""):
        super().__init__(parent)
        self.setWindowTitle("Trajectory")
        self.resize(620, 360)
        self.name = QLineEdit(name)
        self.top = PathEdit(topology, "file", "Choose topology", TOPOLOGY_FILTER)
        self.trajs = QListWidget()
        self.trajs.addItems(list(trajectories))
        add = QPushButton("Add…")
        add.clicked.connect(self._add)
        rem = QPushButton("Remove")
        rem.clicked.connect(lambda: [self.trajs.takeItem(self.trajs.row(i)) for i in self.trajs.selectedItems()])
        btns = QHBoxLayout()
        btns.addWidget(add)
        btns.addWidget(rem)
        btns.addStretch(1)
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Topology", self.top)
        form.addRow("Trajectory files\n(concatenated in order)", self.trajs)
        form.addRow("", btns)
        self.info = QLabel("")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._check)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.info)
        lay.addWidget(bb)
        self.meta = None

    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add trajectory files", last_dir(), TRAJ_FILTER)
        if files:
            remember_dir(files[0])
            self.trajs.addItems(files)

    def values(self):
        return (self.top.edit.text().strip(), [self.trajs.item(i).text() for i in range(self.trajs.count())],
                self.name.text().strip())

    def _check(self):
        from mdmovie.sources.trajectory import TrajectorySource
        top, trajs, _ = self.values()
        if not top:
            QMessageBox.warning(self, "Trajectory", "Choose a topology file.")
            return
        self.info.setText("Loading…")
        self.repaint()
        try:
            self.meta = TrajectorySource("tmp", "", top, trajs).meta(lambda p: p)
        except Exception as e:
            self.info.setText("")
            QMessageBox.critical(self, "Cannot load trajectory", f"{type(e).__name__}: {e}")
            return
        if not self.name.text().strip():
            self.name.setText(os.path.splitext(os.path.basename(top))[0])
        self.accept()


class SeriesDialog(QDialog):
    """Pick a trajectory, an analysis preset and its parameters."""

    def __init__(self, win, kinds, series: Series | None = None, parent=None, plot_style: bool = False):
        super().__init__(parent or win)
        self.win = win
        self.kinds = kinds
        self.setWindowTitle("Analysis series")
        self.resize(560, 560)
        s = series or Series("", "")
        self.params: dict = dict(s.params)

        self.traj = QComboBox()
        load = QPushButton("Load trajectory…")
        load.clicked.connect(self._load_traj)
        tr = QHBoxLayout()
        tr.addWidget(self.traj, 1)
        tr.addWidget(load)
        self._fill_trajs(s.traj)

        self.preset = QComboBox()
        self.presets = presets_for(kinds)
        for p in self.presets:
            self.preset.addItem(f"{p.category} › {p.name}", p.name)
        if s.preset:
            i = self.preset.findData(s.preset)
            if i >= 0:
                self.preset.setCurrentIndex(i)
        self.preset.currentIndexChanged.connect(self._build_params)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet("color: gray")

        self.param_box = QGroupBox("Parameters")
        self.param_form = QFormLayout(self.param_box)

        self.start, self.stop, self.step = QSpinBox(), QSpinBox(), QSpinBox()
        for w, v, lo in ((self.start, s.start or 0, 0), (self.stop, s.stop or 0, 0), (self.step, s.step or 1, 1)):
            w.setRange(lo, 10**9)
            w.setValue(v)
        self.stop.setSpecialValueText("end")
        frames = QHBoxLayout()
        for lbl, w in (("start", self.start), ("stop", self.stop), ("step", self.step)):
            frames.addWidget(QLabel(lbl))
            frames.addWidget(w)

        self.label = QLineEdit(s.label)
        self.label.setPlaceholderText("automatic")
        self.color = ColorButton(s.color, optional=True)
        self.axis = QComboBox()
        self.axis.addItems(["left", "right"])
        self.axis.setCurrentText(s.axis)
        self.column = QSpinBox()
        self.column.setRange(-1, 999)
        self.column.setSpecialValueText("all")
        self.column.setValue(-1 if s.column is None else s.column)

        from mdmovie.panels.plot_panel import LINESTYLES, MARKERS
        self.linestyle = QComboBox()
        self.linestyle.addItems(list(LINESTYLES))
        self.linestyle.setCurrentText(s.linestyle)
        self.marker = QComboBox()
        self.marker.addItems(list(MARKERS))
        self.marker.setCurrentText(s.marker)
        self.linewidth = CompactDoubleSpinBox()
        self.linewidth.setRange(0, 20)
        self.linewidth.setSingleStep(0.25)
        self.linewidth.setSpecialValueText("panel default")
        self.linewidth.setValue(s.linewidth)
        self.alpha = CompactDoubleSpinBox()
        self.alpha.setRange(0.05, 1)
        self.alpha.setSingleStep(0.05)
        self.alpha.setValue(s.alpha)
        from PySide6.QtWidgets import QCheckBox
        self.fill = QCheckBox("Shade the area under the curve")
        self.fill.setChecked(s.fill)

        form = QFormLayout()
        form.addRow("Trajectory", tr)
        form.addRow("Preset", self.preset)
        form.addRow("", self.desc)
        style = QFormLayout()
        style.addRow("Frames", frames)
        style.addRow("Label", self.label)
        if "timeseries" in kinds:
            style.addRow("Column", self.column)
        if plot_style:
            style.addRow("Colour", self.color)
            style.addRow("Y axis", self.axis)
            style.addRow("Line style", self.linestyle)
            style.addRow("Markers", self.marker)
            style.addRow("Line width", self.linewidth)
            style.addRow("Opacity", self.alpha)
            style.addRow("", self.fill)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.param_box)
        lay.addLayout(style)
        lay.addStretch(1)
        lay.addWidget(bb)
        self._build_params()

    def _fill_trajs(self, current=""):
        self.traj.clear()
        for t in self.win.project.trajectories.values():
            self.traj.addItem(t.name, t.id)
        self.traj.addItem(NO_TRAJ, "")
        i = self.traj.findData(current) if current is not None else -1
        self.traj.setCurrentIndex(i if i >= 0 else 0)

    def _load_traj(self):
        t = self.win.load_trajectory()
        if t:
            self._fill_trajs(t)

    def current_preset(self):
        name = self.preset.currentData()
        return next((p for p in self.presets if p.name == name), None)

    def _build_params(self, *_):
        while self.param_form.rowCount():
            self.param_form.removeRow(0)
        p = self.current_preset()
        if p is None:
            return
        self.desc.setText(p.description + (f"\n(from {p.origin})" if p.origin != "built-in" else ""))
        if not p.needs_universe:
            self.traj.setCurrentIndex(self.traj.findData(""))
        elif not self.traj.currentData() and self.traj.count() > 1:
            self.traj.setCurrentIndex(0)
        for prm in p.params:
            value = self.params.get(prm.name, prm.default)
            self.params[prm.name] = value
            ed = make_editor(prm.kind, value, lambda v, n=prm.name: self.params.__setitem__(n, v),
                             options=prm.options, minimum=prm.minimum, maximum=prm.maximum, tooltip=prm.help)
            if prm.kind in ("str", "sel"):  # commit on every keystroke so OK always sees the text
                ed.textChanged.connect(lambda v, n=prm.name: self.params.__setitem__(n, v))
            self.param_form.addRow(prm.text, ed)

    def _ok(self):
        p = self.current_preset()
        if p is None:
            return
        if p.needs_universe and not self.traj.currentData():
            QMessageBox.warning(self, "Analysis series",
                                "This preset reads a trajectory: load one with “Load trajectory…”.")
            return
        self.accept()

    def series(self) -> Series:
        p = self.current_preset()
        params = {k: v for k, v in self.params.items() if k in p.defaults()}
        return Series(self.traj.currentData() or "", p.name, params,
                      self.start.value() or None, self.stop.value() or None,
                      None if self.step.value() == 1 else self.step.value(),
                      self.label.text().strip(), self.color.color, self.axis.currentText(),
                      None if self.column.value() < 0 else self.column.value(),
                      self.linestyle.currentText(), self.marker.currentText(), self.linewidth.value(),
                      self.alpha.value(), self.fill.isChecked())


class MatchTimingDialog(QDialog):
    """Set an image sequence's timing from a trajectory: image k ↔ trajectory frame first + k*stride."""

    def __init__(self, project, parent=None, index_from: str = "order"):
        super().__init__(parent)
        self.setWindowTitle("Match trajectory timing")
        self.index_from = index_from
        self.project = project
        self.traj = QComboBox()
        for t in project.trajectories.values():
            self.traj.addItem(t.name, t.id)
        self.first = QSpinBox()
        self.first.setRange(0, 10**9)
        self.stride = QSpinBox()
        self.stride.setRange(1, 10**6)
        self.info = QLabel()
        self.info.setWordWrap(True)
        form = QFormLayout(self)
        form.addRow("Trajectory", self.traj)
        if index_from == "filename":
            hint = QLabel("Images are indexed by the number in their file name. If that number is the "
                          "trajectory frame (frame.00042.png = frame 42), keep 0 and 1.")
            form.addRow("Trajectory frame of file number 0", self.first)
            form.addRow("Trajectory frames per file number", self.stride)
        else:
            hint = QLabel("Images are indexed by their position in the folder: enter the trajectory frame "
                          "of the first image and the stride you rendered with.")
            form.addRow("Trajectory frame of the first image", self.first)
            form.addRow("Trajectory frames between images (stride)", self.stride)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        form.insertRow(1, hint)
        form.addRow(self.info)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        for w in (self.first, self.stride):
            w.valueChanged.connect(self._info)
        self.traj.currentIndexChanged.connect(self._info)
        self._info()

    def timing(self):
        t = self.project.trajectories.get(self.traj.currentData())
        if t is None:
            return None
        m = t.meta(self.project.resolve_path)
        return m.t0 + self.first.value() * m.dt, m.dt * self.stride.value()

    def _info(self, *_):
        try:
            tm = self.timing()
        except Exception as e:
            self.info.setText(str(e))
            return
        self.info.setText("Load a trajectory first." if tm is None else
                          f"Image k is shown at t = {tm[0]:g} + k × {tm[1]:g} ps, where k is the image's "
                          + ("file number." if self.index_from == "filename" else "position (0, 1, 2, …)."))
        self.info.setAlignment(Qt.AlignmentFlag.AlignLeft)
