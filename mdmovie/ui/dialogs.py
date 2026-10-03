"""Dialogs: trajectory loading, analysis series (preset + parameters), trajectory timing match."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPushButton, QSpinBox, QVBoxLayout)

from mdmovie.analysis.registry import presets_for
from mdmovie.panels import Series
from mdmovie.sources.image_sequence import folder_patterns, scan_folder
from mdmovie.ui.widgets import ColorButton, CompactDoubleSpinBox, PathEdit, last_dir, make_editor, remember_dir

NO_TRAJ = "(none: preset reads its own file)"

TOPOLOGY_FILTER = ("Topology (*.pdb *.gro *.psf *.prmtop *.parm7 *.top *.tpr *.mol2 *.pqr *.data *.xyz *.crd);;"
                   "All files (*)")
TRAJ_FILTER = "Trajectories (*.xtc *.trr *.dcd *.nc *.netcdf *.ncdf *.mdcrd *.crd *.lammpstrj *.xyz *.pdb *.gro);;All files (*)"


def choose_pattern(parent, folder: str, current: str) -> str | None:
    """Ask which files of an image folder form the sequence. Offers one glob per image type found
    (plus "*" when there are several) and accepts a typed pattern; None if cancelled or no images."""
    found = folder_patterns(folder)
    if not found:
        return None
    items = [p for p, _ in found] + (["*"] if len(found) > 1 else [])
    if current and current not in items and scan_folder(folder, current):  # keep a custom pattern that works
        items.insert(0, current)
    summary = ", ".join(f"{n} × {p[1:]}" for p, n in found)
    text, ok = QInputDialog.getItem(
        parent, "File pattern",
        f"Found {summary} in\n{folder}\n\nWhich files make up the sequence? "
        "(glob; separate several with commas)",
        items, items.index(current) if current in items else 0, True)
    return text.strip() if ok and text.strip() else None


class TrajectoryDialog(QDialog):
    def __init__(self, parent=None, topology="", trajectories=(), name="", dt=0.0, whole=True):
        super().__init__(parent)
        self.setWindowTitle("Trajectory")
        self.resize(620, 360)
        self.name = QLineEdit(name)
        self.top = PathEdit(topology, "file", "Choose topology", TOPOLOGY_FILTER)
        self.trajs = QListWidget()
        self.trajs.addItems(list(trajectories))
        self.top.edit.textChanged.connect(self._suggest_trajectory)
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
        self.dt = CompactDoubleSpinBox()
        self.dt.setDecimals(6)
        self.dt.setRange(0, 1e9)
        self.dt.setSpecialValueText("from the file")
        self.dt.setValue(dt)
        self.dt.setToolTip("Simulation time between saved frames. Leave at 'from the file' unless the file "
                           "stores no times (or wrong ones).")
        form.addRow("Time between frames (ps)", self.dt)
        self.whole = QCheckBox("Make molecules whole")
        self.whole.setChecked(whole)
        self.whole.setToolTip("Join molecules that are split across the periodic box and keep them together, "
                              "as 'gmx trjconv -pbc mol -center' does, for drawing and for analysis "
                              "(RMSD, Rg…). Water and ions are left as stored. Untick for trajectories that "
                              "were already processed, to save a little time.")
        form.addRow("Periodic boundaries", self.whole)
        self._warned = False
        self.info = QLabel("")
        self.info.setWordWrap(True)
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

    def _suggest_trajectory(self, top: str):
        """md.tpr → md.xtc (or .trr, .dcd…) from the same folder, while no trajectory has been chosen."""
        from mdmovie.core.quickstart import TRAJECTORY_EXT
        stem, _ = os.path.splitext(top.strip())
        if self.trajs.count() or not stem:
            return
        found = next((stem + e for e in TRAJECTORY_EXT if os.path.isfile(stem + e)), None)
        if found:
            self.trajs.addItem(found)

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
            self.meta = TrajectorySource("tmp", "", top, trajs, self.dt.value(), self.whole.isChecked()).meta(
                lambda p: p)
        except Exception as e:
            self.info.setText("")
            QMessageBox.critical(self, "Cannot load trajectory", f"{type(e).__name__}: {e}")
            return
        if not self.name.text().strip():
            self.name.setText(os.path.splitext(os.path.basename(top))[0])
        if not self.meta.timed and not self._warned:     # say so once, instead of silently inventing times
            self._warned = True
            self.info.setText(f"⚠ {self.meta.n_frames:,} frames, but the file stores no frame times. Enter the "
                              "time between frames above so time axes and labels are right, or press OK again "
                              "to count each frame as 1 ps.")
            self.info.setMinimumHeight(self.info.heightForWidth(max(self.info.width(), 300)))
            self.resize(self.width(), max(self.height(), self.sizeHint().height()))   # room for the message
            self.dt.setFocus()
            return
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


class RepDialog(QDialog):
    """One representation of a molecule panel: which atoms, how they are drawn and coloured."""

    EXAMPLES = ("protein", "protein and not name H*", "nucleic", "resname LIG", "resid 10:25",
                "around 5 resname LIG", "not (protein or resname HOH SOL)")

    def __init__(self, win, scene, rep: dict | None = None, parent=None):
        super().__init__(parent or win)
        from mdmovie.mol.structure import COLOR_SCHEMES, STYLES
        self.scene = scene
        self.setWindowTitle("Representation")
        self.resize(480, 300)
        r = rep or {"sel": "protein", "style": "cartoon", "color": "secondary structure", "custom": "#4c8bf5",
                    "scale": 1.0}
        self.sel = QComboBox()
        self.sel.setEditable(True)
        self.sel.addItems(self.EXAMPLES)
        self.sel.setCurrentText(r.get("sel", "protein"))
        self.sel.setToolTip("MDAnalysis selection language")
        self.count = QLabel()
        self.count.setWordWrap(True)
        self.count.setStyleSheet("color: gray")
        self.style = QComboBox()
        self.style.addItems(STYLES)
        self.style.setCurrentText(r.get("style", "cartoon"))
        self.scheme = QComboBox()
        self.scheme.addItems(COLOR_SCHEMES)
        self.scheme.setCurrentText(r.get("color", "element"))
        self.custom = ColorButton(r.get("custom") or "#4c8bf5")
        self.scale = CompactDoubleSpinBox()
        self.scale.setRange(0.1, 10)
        self.scale.setSingleStep(0.1)
        self.scale.setValue(float(r.get("scale", 1.0) or 1.0))
        self.scale.setToolTip("Thickness of sticks, tubes and ribbons; radius of spheres")
        form = QFormLayout(self)
        form.addRow("Atoms", self.sel)
        form.addRow("", self.count)
        form.addRow("Drawn as", self.style)
        form.addRow("Coloured by", self.scheme)
        form.addRow("Single colour", self.custom)
        form.addRow("Size", self.scale)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        form.addRow(self.bb)
        self.sel.currentTextChanged.connect(self._check)
        self.style.currentTextChanged.connect(self._check)
        self.scheme.currentTextChanged.connect(self._check)
        self._check()

    def _check(self, *_):
        """Count the selected atoms as the user types, so a wrong selection is caught here."""
        self.custom.setEnabled(self.scheme.currentText() == "single colour")
        ok, text = True, ""
        if self.scene is not None:
            try:
                atoms = self.scene.u.select_atoms(self.sel.currentText().strip() or "all")
                text = f"{len(atoms):,} atoms in {atoms.residues.n_residues:,} residues"
                if self.style.currentText() in ("cartoon", "tube"):
                    n = len(atoms.select_atoms("(protein and name CA) or (nucleic and name P)"))
                    text += f" · {n:,} backbone residues" if n else " · no protein or nucleic backbone to draw"
                if self.style.currentText() in ("cartoon", "tube") and self.scheme.currentText() == "element":
                    text += " · 'element' has no meaning for a backbone: it is drawn grey"
            except Exception as e:
                ok, text = False, f"⚠ {e}"
        self.count.setText(text)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def rep(self) -> dict:
        return {"sel": self.sel.currentText().strip() or "all", "style": self.style.currentText(),
                "color": self.scheme.currentText(), "custom": self.custom.color or "#4c8bf5",
                "scale": round(self.scale.value(), 3)}
