"""SciencePlots styles, plot customization, data files, CV map and image data overlays."""
import json
import threading

import numpy as np
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from mdmovie.analysis import runner
from mdmovie.analysis.registry import PRESETS
from mdmovie.core import layout as L
from mdmovie.core.project import Project
from mdmovie.panels import Series
from mdmovie.panels.base import RenderContext
from mdmovie.panels.cvmap_panel import cv_data, free_energy
from mdmovie.panels.mpl_common import STYLES, style_list
from mdmovie.panels.series_data import series_time_info
from mdmovie.render.compositor import cell_rects, render_frame
from mdmovie.render.exporter import qimage_to_rgb
from mdmovie.sources.datafile import parse_columns, read_table

DATA_FILE = "Data file (COLVAR / xvg / csv)"
N = 60


def write_colvar(path, n=N):
    t = np.arange(n) * 2.0
    x = np.linspace(1, 9, n)
    y = 5 + 3 * np.sin(np.linspace(0, 3, n))
    with open(path, "w") as f:
        f.write("#! FIELDS time phi psi\n#! SET min_phi -pi\n")
        for row in zip(t, x, y):
            f.write(" ".join(f"{v:.6f}" for v in row) + "\n")
    return t, x, y


# --- data files -------------------------------------------------------------------------------

def test_read_colvar_xvg_csv(tmp_path):
    write_colvar(tmp_path / "COLVAR")
    table, names = read_table(str(tmp_path / "COLVAR"))
    assert table.shape == (N, 3) and names == ["time", "phi", "psi"]

    (tmp_path / "e.xvg").write_text('# gmx\n@    title "Energy"\n@    xaxis  label "Time (ps)"\n'
                                    '@ s0 legend "Potential"\n@ s1 legend "Temperature"\n'
                                    "0 -100 300\n1 -101 301\n")
    table, names = read_table(str(tmp_path / "e.xvg"))
    assert names == ["Time (ps)", "Potential", "Temperature"] and table[1, 2] == 301

    (tmp_path / "d.csv").write_text("time,rmsd,rg\n0,0.1,10\n1,0.2,11\n")
    table, names = read_table(str(tmp_path / "d.csv"))
    assert names == ["time", "rmsd", "rg"] and table.shape == (2, 3)
    assert parse_columns("", 3) == [1, 2] and parse_columns("1-2", 5) == [1, 2]


def test_data_file_series_needs_no_trajectory(tmp_path):
    write_colvar(tmp_path / "COLVAR")
    pr = Project()
    pr.path = str(tmp_path / "p.mdmovie.json")
    s = Series("", DATA_FILE, {"path": str(tmp_path / "COLVAR"), "columns": "1,2"})
    assert not PRESETS[DATA_FILE].needs_universe
    assert runner.series_key(pr, s) is not None
    res = runner.compute(pr, s)
    assert res.labels == ["phi", "psi"] and res.values.shape == (N, 2)
    # editing the file changes the cache key, so the result is recomputed
    old = runner.series_key(pr, s)
    write_colvar(tmp_path / "COLVAR", n=N + 1)
    assert runner.series_key(pr, s) != old
    with pytest.raises(ValueError, match="do not exist"):
        runner.compute(pr, Series("", DATA_FILE, {"path": str(tmp_path / "COLVAR"), "columns": "7"}))


# --- styles and plot options ------------------------------------------------------------------

def test_clean_style_does_not_pick_up_sciencplots_fonts():
    assert "no-latex" not in style_list({"style": "clean"})
    assert style_list({"style": "science"})[-1] == "no-latex"
    assert "no-latex" not in style_list({"style": "science", "latex": True})


def plot_project(tmp_path, **props):
    t, x, y = write_colvar(tmp_path / "COLVAR")
    pr = Project()
    pr.path = str(tmp_path / "p.mdmovie.json")
    plot = pr.add_panel("plot")
    plot.series = [Series("", DATA_FILE, {"path": str(tmp_path / "COLVAR"), "columns": "1"}, label="phi",
                          linestyle="dashed", marker="circle", fill=True),
                   Series("", DATA_FILE, {"path": str(tmp_path / "COLVAR"), "columns": "2"}, label="psi",
                          axis="right", alpha=0.6)]
    plot.props.update(props)
    pr.layout = L.Leaf(plot.id)
    runner.compute_all(pr)
    return pr, plot


@pytest.mark.parametrize("style", [s for s in STYLES if s != "classic"])
def test_every_style_renders(tmp_path, style):
    pr, plot = plot_project(tmp_path, style=style, palette="vibrant", show_value=True, hlines="3, 7",
                            vlines="40", minor_ticks="on", grid="major + minor", yscale="log",
                            legend="outside top", spines="left + bottom", theme="dark")
    a = qimage_to_rgb(render_frame(pr, 10, 0.4))
    b = qimage_to_rgb(render_frame(pr, 40, 0.4))
    assert a.std() > 5 and not np.array_equal(a, b)   # something drawn, and it moves


def test_plot_frame_axis_syncs_with_images(tmp_path):
    """COLVAR rows are 2 ps apart; images at default timing are one per index. Frame-wise, they line up."""
    pr, plot = plot_project(tmp_path, mode="marker", cursor_line=False, marker_size=12)
    frames = tmp_path / "frames"
    frames.mkdir()
    for k in range(N):
        img = QImage(16, 16, QImage.Format.Format_RGB32)
        img.fill(QColor(k * 4, 0, 0))
        img.save(str(frames / f"f.{k:03d}.png"))
    im = pr.add_panel("image")
    im.props.update(folder=str(frames))
    pr.layout = L.Split("h", [0.5, 0.5], [L.Leaf(im.id), L.Leaf(plot.id)])
    assert pr.n_frames == 2 * (N - 1) + 1          # by time: 118 ps of data at 1 ps per movie frame
    plot.props["x_axis"] = "frame"
    assert plot.time_info(pr) == (0.0, N - 1.0, 1.0)
    assert pr.n_frames == N                        # frame k of the data ↔ image k ↔ movie frame k
    plot.series[1].step = 2                        # every 2nd row keeps its own frame numbers 0, 2, 4, …
    runner.compute_all(pr)
    assert series_time_info(pr, plot.series[1:], frames=True) == (0.0, N - 2.0, 2.0)
    a = qimage_to_rgb(render_frame(pr, 5, 0.5))
    b = qimage_to_rgb(render_frame(pr, 50, 0.5))
    assert a.std() > 5 and not np.array_equal(a, b)


def test_concurrent_styled_rendering(tmp_path):
    """Preview and export threads drawing panels with different styles must not interfere."""
    pr1, _ = plot_project(tmp_path, style="science")
    pr2, _ = plot_project(tmp_path, style="ggplot")
    errors = []

    def work(pr):
        try:
            for g in range(0, N, 3):
                render_frame(pr.clone(), g, 0.3)
        except Exception as e:  # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=work, args=(p,)) for p in (pr1, pr2, pr1)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors


def test_legacy_bool_grid_is_migrated():
    pr = Project.from_dict({"panels": [{"id": "plot1", "kind": "plot", "props": {"grid": False}}],
                            "groups": [{"id": "g1"}]})
    assert pr.panels["plot1"].props["grid"] == "off"


# --- CV map -----------------------------------------------------------------------------------

def test_free_energy_surface():
    rng = np.random.default_rng(0)
    x, y = rng.normal(0, 1, 20000), rng.normal(0, 1, 20000)
    f, xe, ye = free_energy(x, y, 30, 300.0, "kJ/mol")
    assert np.nanmin(f) == 0
    centre = f[15, 15]
    corner = f[0, 0]
    assert np.isnan(corner) or corner > centre + 5   # far from the minimum costs energy
    kt, *_ = free_energy(x, y, 30, 300.0, "kT")
    assert np.nanmax(kt) < np.nanmax(f)             # kT units are smaller than kJ/mol at 300 K


def test_cvmap_panel_point_moves(tmp_path):
    t, x, y = write_colvar(tmp_path / "COLVAR")
    pr = Project()
    pr.path = str(tmp_path / "p.mdmovie.json")
    cv = pr.add_panel("cvmap")
    cv.series = [Series("", DATA_FILE, {"path": str(tmp_path / "COLVAR")})]  # one series, two columns
    cv.props.update(marker_color="#ff00ff", trail="fading dots", bins=20)
    pr.layout = L.Leaf(cv.id)
    runner.compute_all(pr)
    assert pr.n_frames == N

    def magenta_x(g):
        a = qimage_to_rgb(render_frame(pr, g, 0.5)).astype(int)
        m = (a[..., 0] > 220) & (a[..., 1] < 60) & (a[..., 2] > 220)
        return np.nonzero(m)[1].mean()
    assert magenta_x(5) < magenta_x(30) < magenta_x(55)   # x grows linearly in the COLVAR


def test_cvmap_single_column_plots_against_time(tmp_path):
    t, x, y = write_colvar(tmp_path / "COLVAR")
    pr = Project()
    pr.path = str(tmp_path / "p.mdmovie.json")
    cv = pr.add_panel("cvmap")
    cv.series = [Series("", DATA_FILE, {"path": str(tmp_path / "COLVAR"), "columns": "2"})]  # psi only
    cv.props.update(marker_color="#ff00ff", trail="none")
    pr.layout = L.Leaf(cv.id)
    runner.compute_all(pr)
    (ts, xs, ys, xl, yl), vs_time = cv_data(pr, cv.series, 0, 1)
    assert vs_time and xl == "Time (ps)" and yl == "psi"
    assert np.allclose(xs, t) and np.allclose(ys, y)

    def magenta(g):
        a = qimage_to_rgb(render_frame(pr, g, 0.5)).astype(int)
        m = (a[..., 0] > 220) & (a[..., 1] < 60) & (a[..., 2] > 220)
        assert m.any(), g   # drawn, not the "needs two columns" message
        cy, cx = np.nonzero(m)
        return cx.mean(), cy.mean()
    (x0, _), (x1, y1), (x2, y2) = magenta(0), magenta(30), magenta(59)
    assert x0 < x1 < x2                       # time runs left to right
    assert (y2 > y1) == (y[59] < y[30])       # image rows grow downwards


# --- image overlay on a pre-rendered FES ------------------------------------------------------

def test_image_overlay_lands_at_calibrated_position(tmp_path):
    # a stand-in "pre-rendered FES": 400×300 picture whose plot area is the box (40,30)-(360,270)
    img = QImage(400, 300, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.fillRect(40, 30, 320, 240, QColor("#dddddd"))
    p.end()
    img.save(str(tmp_path / "fes.png"))
    t, x, y = write_colvar(tmp_path / "COLVAR")

    pr = Project()
    pr.path = str(tmp_path / "p.mdmovie.json")
    pr.width, pr.height, pr.gutter = 400, 300, 0
    im = pr.add_panel("image")
    im.props.update(folder=str(tmp_path / "fes.png"), overlay=True, calib=[0.1, 0.1, 0.8, 0.8],
                    ax_xmin=0, ax_xmax=10, ax_ymin=0, ax_ymax=10, marker_color="#00ff00", trail="none",
                    marker_size=10, fit="stretch")
    im.series = [Series("", DATA_FILE, {"path": str(tmp_path / "COLVAR")})]
    pr.layout = L.Leaf(im.id)
    runner.compute_all(pr)
    assert pr.n_frames == N   # the single picture is static; the data sets the clock

    for g in (0, 25, 59):
        a = qimage_to_rgb(render_frame(pr, g)).astype(int)
        m = (a[..., 1] > 200) & (a[..., 0] < 80) & (a[..., 2] < 80)
        cy, cx = np.nonzero(m)
        exp_x = 40 + x[g] / 10 * 320
        exp_y = 30 + (1 - y[g] / 10) * 240
        assert abs(cx.mean() - exp_x) < 2 and abs(cy.mean() - exp_y) < 2, g


def test_paths_saved_relative(tmp_path):
    write_colvar(tmp_path / "COLVAR")
    pr = Project()
    pr.path = str(tmp_path / "p.mdmovie.json")
    cv = pr.add_panel("cvmap")
    cv.props["image"] = str(tmp_path / "fes.png")
    cv.series = [Series("", DATA_FILE, {"path": str(tmp_path / "COLVAR")})]
    pr.save(pr.path)
    raw = json.load(open(pr.path))
    assert raw["panels"][0]["props"]["image"] == "fes.png"
    assert raw["panels"][0]["series"][0]["params"]["path"] == "COLVAR"
    back = Project.load(pr.path)
    assert back.panels[cv.id].series[0].params["path"] == str(tmp_path / "COLVAR")
    assert pr.panels[cv.id].series[0].params["path"] == str(tmp_path / "COLVAR")  # saving didn't mutate


# --- dialogs ----------------------------------------------------------------------------------

def test_series_and_calibrate_dialogs(qtbot, tmp_path, monkeypatch):
    from mdmovie.ui.calibrate_dialog import CalibrateDialog
    from mdmovie.ui.dialogs import NO_TRAJ, SeriesDialog
    from mdmovie.ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_confirm_discard", lambda self: True)
    win = MainWindow()
    qtbot.addWidget(win)
    dlg = SeriesDialog(win, ("timeseries", "profile"), plot_style=True)
    dlg.preset.setCurrentIndex(dlg.preset.findData(DATA_FILE))
    assert dlg.traj.currentText() == NO_TRAJ
    dlg.params["path"] = str(tmp_path / "COLVAR")
    dlg.linestyle.setCurrentText("dotted")
    dlg.fill.setChecked(True)
    dlg._ok()
    assert dlg.result() == dlg.DialogCode.Accepted
    s = dlg.series()
    assert s.traj == "" and s.linestyle == "dotted" and s.fill

    img = QImage(200, 100, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    img.save(str(tmp_path / "fes.png"))
    data = (np.arange(3.0), np.array([1.0, 2, 3]), np.array([4.0, 5, 6]), "x", "y")
    cal = CalibrateDialog(str(tmp_path / "fes.png"), [0.1, 0.2, 0.5, 0.5], (0, 1, 0, 1), data)
    cal._data_range()
    calib, ranges = cal.result()
    assert calib == pytest.approx([0.1, 0.2, 0.5, 0.5]) and ranges == (1, 3, 4, 6)
    win.analysis.shutdown()


def test_render_context_smoke(tmp_path):
    """Panels draw into an arbitrary painter rect (as the preview does)."""
    pr, plot = plot_project(tmp_path, style="nature")
    img = QImage(300, 200, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("white"))
    p = QPainter(img)
    plot.render(p, QRectF(0, 0, 300, 200), RenderContext(pr, 5, pr.group_time(plot.group, 5), 0.5))
    p.end()
    assert cell_rects(pr, 300, 200, 1.0)


def test_property_names_are_unique_per_panel_type():
    from mdmovie.panels import PANEL_TYPES
    for kind, cls in PANEL_TYPES.items():
        names = [p.name for p in cls.all_props()]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"{kind}: duplicate property names {dupes}"
