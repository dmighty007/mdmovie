"""Export dialog (MP4 / GIF / PNG sequence) running the exporter on a worker thread."""
from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressDialog, QSpinBox,
                               QToolButton)

from mdmovie.analysis.registry import Cancelled
from mdmovie.render.exporter import ExportOptions, export_movie
from mdmovie.ui.widgets import last_dir, remember_dir

FORMATS = {"MP4 (H.264)": ("mp4", ".mp4"), "GIF": ("gif", ".gif"), "PNG sequence": ("png", ".png")}
SCALES = {"100%": 1.0, "75%": 0.75, "50%": 0.5, "33%": 1 / 3, "25%": 0.25}


class ExportThread(QThread):
    progress = Signal(int, int, str)
    done = Signal(str, str)  # path, error ("" on success, "cancelled")

    def __init__(self, project, opts):
        super().__init__()
        self.project, self.opts = project, opts
        self.cancel = threading.Event()

    def run(self):
        try:
            export_movie(self.project, self.opts, lambda i, n, m: self.progress.emit(i, n, m), self.cancel.is_set)
            self.done.emit(self.opts.path, "")
        except Cancelled:
            self.done.emit(self.opts.path, "cancelled")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.done.emit(self.opts.path, f"{type(e).__name__}: {e}")


class ExportDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        pr = win.project
        self.setWindowTitle("Export movie")
        self.fmt = QComboBox()
        self.fmt.addItems(list(FORMATS))
        base = os.path.splitext(os.path.splitext(os.path.basename(pr.path or "movie"))[0])[0]
        self.path = QLineEdit(os.path.join(os.path.dirname(pr.path) if pr.path else last_dir("export_dir"),
                                           base + ".mp4"))
        browse = QToolButton()
        browse.setText("…")
        browse.clicked.connect(self._browse)
        prow = QHBoxLayout()
        prow.addWidget(self.path, 1)
        prow.addWidget(browse)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(1, 240)
        self.fps.setValue(pr.fps)
        self.scale = QComboBox()
        self.scale.addItems(list(SCALES))
        self.size_label = QLabel()
        self.crf = QSpinBox()
        self.crf.setRange(0, 51)
        self.crf.setValue(18)
        self.crf.setToolTip("Lower = better quality, bigger file. 18 is visually lossless, 23 is the x264 default.")
        self.preset = QComboBox()
        self.preset.addItems(["ultrafast", "veryfast", "fast", "medium", "slow", "veryslow"])
        self.preset.setCurrentText("medium")
        self.gif_step = QSpinBox()
        self.gif_step.setRange(1, 100)
        self.gif_step.setToolTip("Keep every n-th frame (GIFs get large quickly)")
        self.start = QSpinBox()
        self.stop = QSpinBox()
        n = pr.n_frames
        for s in (self.start, self.stop):
            s.setRange(0, max(0, n))
        self.stop.setValue(n)
        rng = QHBoxLayout()
        rng.addWidget(self.start)
        rng.addWidget(QLabel("to (exclusive)"))
        rng.addWidget(self.stop)

        form = QFormLayout(self)
        form.addRow("Format", self.fmt)
        form.addRow("File", prow)
        form.addRow("Frame rate", self.fps)
        form.addRow("Output size", self.scale)
        form.addRow("", self.size_label)
        self.crf_row = ("Quality (CRF)", self.crf)
        form.addRow(*self.crf_row)
        form.addRow("Encoder speed", self.preset)
        form.addRow("GIF: frame step", self.gif_step)
        form.addRow("Frames", rng)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        bb.accepted.connect(self._export)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.form = form
        self.fmt.currentTextChanged.connect(self._fmt_changed)
        self.scale.currentTextChanged.connect(self._update_size)
        self._fmt_changed(self.fmt.currentText())
        self._update_size()

    def _fmt_changed(self, name):
        fmt, ext = FORMATS[name]
        base, _ = os.path.splitext(self.path.text())
        self.path.setText(base + ext)
        for w in (self.crf, self.preset):
            w.setEnabled(fmt == "mp4")
        self.gif_step.setEnabled(fmt == "gif")
        if fmt == "gif" and self.scale.currentText() == "100%":
            self.scale.setCurrentText("50%")

    def _update_size(self, *_):
        w, h = self.win.project.size
        s = SCALES[self.scale.currentText()]
        self.size_label.setText(f"{round(w * s)}×{round(h * s)} px")

    def _browse(self):
        fmt, ext = FORMATS[self.fmt.currentText()]
        p, _ = QFileDialog.getSaveFileName(self, "Export to", self.path.text(), f"*{ext}")
        if p:
            if not p.endswith(ext):
                p += ext
            self.path.setText(p)

    def _export(self):
        fmt, _ = FORMATS[self.fmt.currentText()]
        path = self.path.text().strip()
        if not path:
            return
        if os.path.exists(path) and fmt != "png" and QMessageBox.question(
                self, "Export", f"{os.path.basename(path)} exists. Overwrite?") != QMessageBox.StandardButton.Yes:
            return
        remember_dir(path, "export_dir")
        opts = ExportOptions(path, fmt, self.fps.value(), SCALES[self.scale.currentText()], self.crf.value(),
                             self.preset.currentText(), self.start.value(), self.stop.value() or None,
                             self.gif_step.value())
        self.win.set_playing(False)
        thread = ExportThread(self.win.project, opts)
        dlg = QProgressDialog("Preparing…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("Exporting")
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        t0 = time.time()

        def on_progress(i, n, msg):
            dlg.setMaximum(max(n, 1))
            dlg.setValue(i)
            eta = (time.time() - t0) / i * (n - i) if i and n > 1 else 0
            dlg.setLabelText(msg + (f"   (≈{eta:.0f} s left)" if eta > 1 else ""))

        result = {}

        def on_done(p, err):
            result["err"] = err
            dlg.close()

        thread.progress.connect(on_progress)
        thread.done.connect(on_done)
        dlg.canceled.connect(thread.cancel.set)
        thread.start()
        dlg.exec()
        if "err" not in result:  # dialog closed without finishing: stop the worker
            thread.cancel.set()
        thread.wait()
        err = result.get("err", "cancelled")
        if err == "cancelled":
            self.win.statusBar().showMessage("Export cancelled", 5000)
            return
        if err:
            QMessageBox.critical(self, "Export failed", err)
            return
        self.win.statusBar().showMessage(f"Exported {path} in {time.time() - t0:.1f} s", 10000)
        self.accept()
