"""Regenerate the screenshots and diagrams used in docs/TUTORIAL.md.

Drives the real GUI off-screen through the tutorial steps, so the pictures always match the
current app:

    QT_QPA_PLATFORM=offscreen .venv/bin/python docs/make_tutorial_images.py

Needs MDAnalysisTests (for the AdK example trajectory).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "images")
ACCENT = QColor("#ff6d00")

app = QApplication.instance() or QApplication(sys.argv[:1])
from mdmovie.ui.theme import apply_theme  # noqa: E402

apply_theme(app, os.environ.get("MDMOVIE_THEME", "dark"))

from MDAnalysisTests.datafiles import DCD, PSF  # noqa: E402

from mdmovie.core import crop as C  # noqa: E402
from mdmovie.core import layout as L  # noqa: E402
from mdmovie.core.project import Overlay  # noqa: E402
from mdmovie.demo import render_ca_frames  # noqa: E402
from mdmovie.panels import Series  # noqa: E402
from mdmovie.render.compositor import cell_aspect, render_frame  # noqa: E402
from mdmovie.render.exporter import ExportOptions, export_movie  # noqa: E402
from mdmovie.sources.image_sequence import IMAGE_CACHE, qimage_to_array  # noqa: E402
from mdmovie.ui.main_window import MainWindow  # noqa: E402


# --- helpers ------------------------------------------------------------------------------------

def pump(n=15):
    for _ in range(n):
        app.processEvents()


def rect_of(widget: QWidget, win: QWidget, pad=4) -> QRect:
    tl = widget.mapTo(win, QPoint(0, 0))
    return QRect(tl.x() - pad, tl.y() - pad, widget.width() + 2 * pad, widget.height() + 2 * pad)


def group_box(win, title) -> QWidget:
    """An inspector section by its title."""
    from mdmovie.ui.widgets import Section
    return next(b for b in win.inspector.widget().findChildren(Section) if b.title() == title)


def annotate(img: QImage, marks) -> QImage:
    """Draw numbered callouts: marks = [(QRect, number or text), ...]."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    f = QFont()
    f.setPixelSize(17)
    f.setBold(True)
    p.setFont(f)
    for r, label in marks:
        p.setPen(QPen(ACCENT, 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(r), 8, 8)
        text = str(label)
        w = max(30, p.fontMetrics().horizontalAdvance(text) + 16)
        # centred on the top edge; fully above short targets so the label stays readable
        badge = QRectF(r.center().x() - w / 2, r.top() - (33 if r.height() < 60 else 15), w, 30)
        badge.moveTop(max(2, badge.top()))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ACCENT)
        p.drawRoundedRect(badge, 15, 15)
        p.setPen(QColor("white"))
        p.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), text)
    p.end()
    return img


def side_by_side(images, labels, gap=24, label_h=40) -> QImage:
    h = max(i.height() for i in images) + label_h
    w = sum(i.width() for i in images) + gap * (len(images) - 1)
    out = QImage(w, h, QImage.Format.Format_ARGB32)
    out.fill(QColor("#ffffff"))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    f = QFont()
    f.setPixelSize(20)
    f.setBold(True)
    p.setFont(f)
    x = 0
    for img, lab in zip(images, labels):
        p.setPen(QColor("#333333"))
        p.drawText(QRectF(x, 0, img.width(), label_h), int(Qt.AlignmentFlag.AlignCenter), lab)
        p.drawImage(x, label_h, img)
        x += img.width() + gap
    p.end()
    return out


def save(img: QImage, name: str, width: int | None = None):
    if width and img.width() > width:
        img = img.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    path = os.path.join(OUT, name)
    img.save(path)
    print("wrote", os.path.relpath(path, HERE))


def wait_analysis(win, timeout=120):
    t0 = time.time()
    while win.analysis.busy and time.time() - t0 < timeout:
        pump(3)
        time.sleep(0.02)
    pump()


def grab_dialog(dlg, size=None) -> QImage:
    if size:
        dlg.resize(*size)
    dlg.show()
    pump()
    img = dlg.grab().toImage()
    dlg.close()
    return img


# --- diagrams (matplotlib) ----------------------------------------------------------------------

def sync_diagram():
    """Movie frames vs simulation time for two sync groups and the edge modes."""
    from matplotlib.figure import Figure
    fig = Figure(figsize=(10, 3.9), dpi=120, layout="constrained")
    axes = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.25, 1]})
    ax = axes[0]
    g = np.arange(0, 130)
    a = np.clip(g, 0, 97)
    b = np.clip(g - 30, 0, 97).astype(float)
    b[g < 30] = np.nan
    ax.plot(g, a, lw=2.5, color="#4c8bf5", label="Group “Main” (protein, time label)")
    ax.plot(g, b, lw=2.5, color="#e8710a", label="Group “RMSD time” (start = 30, before = hide)")
    ax.axvline(60, color="#888", ls="--", lw=1)
    ax.annotate("movie frame 60:\nprotein shows frame 60,\nRMSD shows frame 30", (60, 45), (68, 8),
                fontsize=9, color="#444", arrowprops=dict(arrowstyle="->", color="#888"))
    ax.set_xlabel("Movie frame")
    ax.set_ylabel("Simulation time shown (ps)")
    ax.set_title("Each sync group maps movie frames → simulation time", fontsize=11)
    ax.legend(fontsize=8.5, loc="upper left", frameon=False)
    ax.grid(color="#eee")
    ax = axes[1]
    g = np.arange(0, 80)
    start, n = 20, 30
    local = g - start
    rows = {"hold": np.where(local < 0, 0, np.where(local >= n, n - 1, local)).astype(float),
            "hide": np.where((local < 0) | (local >= n), np.nan, local).astype(float),
            "loop": (local % n).astype(float)}
    colors = {"hold": "#34a853", "hide": "#ea4335", "loop": "#a142f4"}
    for i, (k, v) in enumerate(rows.items()):
        ax.plot(g, v + i * 40, lw=2.2, color=colors[k])
        ax.text(81, i * 40 + 12, k, color=colors[k], fontsize=11, weight="bold", va="center")
    ax.axvspan(start, start + n, color="#f1f3f4")
    ax.text(start + n / 2, 118, "group plays", ha="center", fontsize=9, color="#555")
    ax.set_yticks([])
    ax.set_xlabel("Movie frame")
    ax.set_title("Before / after behaviour", fontsize=11)
    ax.set_xlim(0, 90)
    ax.set_ylim(-5, 125)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    fig.savefig(os.path.join(OUT, "diagram_sync.png"))
    print("wrote images/diagram_sync.png")


def timing_diagram():
    """How images get their simulation time: t = t0 + k × dt."""
    from matplotlib.figure import Figure
    fig = Figure(figsize=(10, 2.6), dpi=120, layout="constrained")
    ax = fig.add_subplot(111)
    frames = np.arange(0, 13)
    ax.scatter(frames, np.ones_like(frames), s=70, color="#4c8bf5", zorder=3)
    for f in frames:
        ax.text(f, 1.18, f"{f}", ha="center", fontsize=8, color="#4c8bf5")
    imgs = frames[::3]
    ax.scatter(imgs, np.zeros_like(imgs), s=260, marker="s", color="#e8710a", zorder=3)
    for k, f in enumerate(imgs):
        ax.text(f, -0.36, f"frame.{f:04d}.png\nk = {f} (filename)\nk = {k} (order)", ha="center",
                va="top", fontsize=7.5, color="#8a4b0f")
        ax.annotate("", (f, 0.9), (f, 0.12), arrowprops=dict(arrowstyle="<-", color="#bbb"))
    ax.text(-0.9, 1, "trajectory frames\n(dt = 1 ps)", ha="right", va="center", fontsize=9, color="#4c8bf5")
    ax.text(-0.9, 0, "rendered images\n(every 3rd frame)", ha="right", va="center", fontsize=9,
            color="#8a4b0f")
    ax.set_xlim(-4.5, 13.9)
    ax.set_ylim(-1.3, 1.45)
    ax.axis("off")
    ax.set_title("Image k is shown at  t = t0 + k × dt   →   filename: dt = 1 ps     order: dt = 3 ps",
                 fontsize=10.5)
    fig.savefig(os.path.join(OUT, "diagram_timing.png"))
    print("wrote images/diagram_timing.png")


def plot_modes(win, pid):
    pr = win.project
    panel = pr.panels[pid]
    tiles, labels = [], []
    for mode, text in (("reveal", "reveal – line grows"), ("marker", "marker – dot moves"),
                       ("window", "window – axis scrolls"), ("static", "static")):
        panel.props["mode"] = mode
        panel._renderer_key = None
        img = QImage(460, 260, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor("white"))
        p = QPainter(img)
        from mdmovie.panels.base import RenderContext
        t = pr.group_time(panel.group, 55)
        panel.render(p, QRectF(0, 0, 460, 260), RenderContext(pr, 55, t, 0.8))
        p.end()
        tiles.append(img)
        labels.append(text)
    panel.props["mode"] = "reveal"
    panel._renderer_key = None
    top = side_by_side(tiles[:2], labels[:2])
    bottom = side_by_side(tiles[2:], labels[2:])
    out = QImage(top.width(), top.height() + bottom.height() + 10, QImage.Format.Format_ARGB32)
    out.fill(QColor("white"))
    p = QPainter(out)
    p.drawImage(0, 0, top)
    p.drawImage(0, top.height() + 10, bottom)
    p.end()
    save(out, "plot_modes.png")


# --- the tutorial walk-through ------------------------------------------------------------------

def main():
    os.makedirs(OUT, exist_ok=True)
    work = tempfile.mkdtemp(prefix="mdmovie-tutorial-")
    frames_dir = os.path.join(work, "frames")
    import MDAnalysis as mda
    render_ca_frames(mda.Universe(PSF, DCD), frames_dir, size=(1800, 1400), view=(80, 62))  # generous margins

    MainWindow._confirm_discard = lambda self: True
    win = MainWindow()
    win.resize(1440, 880)
    win.show()
    pump()
    W = 1440

    # 1. window tour
    marks = [(rect_of(win.tree, win), 1), (rect_of(win.canvas, win), 2), (rect_of(win.inspector, win), 3),
             (rect_of(win.transport, win), 4), (rect_of(win.timeline, win), 5)]
    save(annotate(win.grab().toImage(), marks), "01_window_tour.png", W)

    # 2. layout template + empty cell inspector
    win.apply_template("Big left + 2 right")
    win.select(("cell", (0,)))
    pump()
    buttons = [b for b in win.inspector.widget().findChildren(QWidget) if b.metaObject().className() == "QPushButton"]
    btn_rect = rect_of(buttons[0], win).united(rect_of(buttons[3], win))
    save(annotate(win.grab().toImage(), [(rect_of(win.canvas, win, -40), 1), (btn_rect, 2)]),
         "02_layout.png", W)

    # 3. image panel
    tid = {}

    def add_image(p):
        img = p.add_panel("image", "Protein")
        img.props.update(folder=frames_dir, pattern="frame.*.png", index_from="filename")
        L.get(p.layout, (0,)).panel = img.id
        tid["img"] = img.id
    win.apply("New image panel", add_image)
    win.select(("panel", tid["img"]))
    pump()
    save(annotate(win.grab().toImage(), [(rect_of(group_box(win, "Images"), win), 1),
                                         (rect_of(group_box(win, "Timing"), win), 2)]), "03_image_panel.png", W)

    # 4. crop dialog: free vs 16:9 + auto-trim
    from mdmovie.ui.crop_dialog import CropDialog
    seq = win.project.panels[tid["img"]].sequence(win.project)
    dlg = CropDialog(seq.paths, None, cell_aspect(win.project, tid["img"]), win, 40)
    before = grab_dialog(dlg, (820, 600))
    dlg = CropDialog(seq.paths, None, None, win, 40)
    dlg.aspect_combo.setCurrentText("16:9")
    dlg.lock.setChecked(True)
    dlg._auto_trim()
    after = grab_dialog(dlg, (820, 600))
    after = annotate(after, [(rect_of(dlg.lock, dlg).united(rect_of(dlg.aspect_combo, dlg)), 1),
                             (_button(dlg, "Auto-trim"), 2)])
    save(side_by_side([before, after], ["Opened: whole image", "Lock 16:9 → Auto-trim → OK"]), "04_crop.png", W)
    crop = C.normalize(C.auto_trim([qimage_to_array(IMAGE_CACHE.get(p)) for p in seq.paths[::10]],
                                   aspect=16 / 9), *IMAGE_CACHE.get(seq.paths[0]).size().toTuple())
    win.apply("Crop", lambda p: p.panels[tid["img"]].props.__setitem__("crop", crop))

    # 5. trajectory dialog
    from mdmovie.ui.dialogs import MatchTimingDialog, SeriesDialog, TrajectoryDialog
    save(grab_dialog(TrajectoryDialog(win, PSF, [DCD], "AdK"), (720, 360)), "05_trajectory_dialog.png")

    def add_traj(p):
        tid["traj"] = p.add_trajectory(PSF, [DCD], "AdK").id
    win.apply("Load trajectory", add_traj)

    md = MatchTimingDialog(win.project, win, "order")
    md.stride.setValue(2)
    save(grab_dialog(md, (560, 230)), "05b_match_timing.png")
    meta = win.project.trajectories[tid["traj"]].meta(win.project.resolve_path)
    win.apply("Match timing", lambda p: p.panels[tid["img"]].props.update(t0=meta.t0, dt=meta.dt))

    # 6. series dialog
    sd = SeriesDialog(win, ("timeseries", "profile"))
    sd.preset.setCurrentIndex(sd.preset.findData("RMSD"))
    img = grab_dialog(sd, (600, 600))
    save(annotate(img, [(rect_of(sd.preset, sd), 1), (rect_of(sd.param_box, sd), 2)]), "06_series_dialog.png")

    # 7. plot panel
    def add_plots(p):
        rmsd = p.add_panel("plot", "RMSD")
        rmsd.series = [Series(tid["traj"], "RMSD", {"select": "backbone"}, label="Backbone RMSD")]
        rmsd.props.update(show_value=True, value_format="{value:.2f} Å", title="Backbone RMSD")
        rg = p.add_panel("plot", "Rg")
        rg.series = [Series(tid["traj"], "Radius of gyration", {}, color="#2ca02c"),
                     Series(tid["traj"], "End-to-end distance", {}, axis="right", color="#9467bd")]
        rg.props.update(mode="marker", legend="upper left")
        L.get(p.layout, (1, 0)).panel = rmsd.id
        L.get(p.layout, (1, 1)).panel = rg.id
        tid.update(rmsd=rmsd.id, rg=rg.id)
    win.apply("Add plots", add_plots)
    wait_analysis(win)
    win.set_frame(60)
    win.select(("panel", tid["rmsd"]))
    pump()
    win.canvas.render_now()
    save(annotate(win.grab().toImage(), [(rect_of(group_box(win, "Data series (analysis presets)"), win), 1),
                                         (rect_of(group_box(win, "Animation"), win), 2)]), "07_plot_panel.png", W)
    plot_modes(win, tid["rmsd"])

    # 8. unsynchronised panel
    win.inspector._change_group(tid["rmsd"], "__new__")
    g2 = win.project.panels[tid["rmsd"]].group
    win.apply("Offset", lambda p: (setattr(p.groups[g2], "start", 30), setattr(p.groups[g2], "before", "hide")))
    win.select(("group", g2))
    win.set_frame(60)
    pump()
    win.canvas.render_now()
    form_rect = rect_of(group_box(win, "General"), win).united(rect_of(group_box(win, "Timing"), win))
    save(annotate(win.grab().toImage(), [(rect_of(win.timeline, win), 1), (form_rect, 2)]), "08_sync.png", W)

    # 9. text overlay
    def add_label(p):
        lab = p.add_panel("text", "Clock")
        lab.props.update(text="t = {t_ps:.0f} ps", font_size=40, bold=True, align="left", valign="top",
                         background="")
        p.overlays.append(Overlay(lab.id, 0.02, 0.02, 0.3, 0.08))
    win.apply("Add overlay", add_label)
    win.select(("overlay", 0))
    pump()
    win.canvas.render_now()
    save(annotate(win.grab().toImage(), [(rect_of(group_box(win, "Overlay position (% of canvas)"), win), 1),
                                         (rect_of(group_box(win, "Text"), win), 2)]),
         "09_overlay.png", W)

    # 10. export dialog + outputs
    from mdmovie.ui.export_dialog import ExportDialog
    win.project.path = os.path.join(work, "tutorial.mdmovie.json")
    win.project.save(win.project.path)
    save(grab_dialog(ExportDialog(win), (560, 380)), "10_export_dialog.png")
    save(render_frame(win.project, 70, 0.75), "00_result.png")
    export_movie(win.project, ExportOptions(os.path.join(OUT, "demo.gif"), scale=0.4, gif_step=2, gif_colors=128))
    print("wrote images/demo.gif")

    sync_diagram()
    timing_diagram()
    win.analysis.shutdown()


def _button(dlg, text) -> QRect:
    from PySide6.QtWidgets import QPushButton
    b = next(b for b in dlg.findChildren(QPushButton) if b.text() == text)
    return rect_of(b, dlg)


if __name__ == "__main__":
    main()
