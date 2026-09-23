"""End-to-end scenario: the use case the app was built for.

A protein image sequence (rendered every 2nd trajectory frame) next to an RMSD plot, synced by
simulation time; a radius-of-gyration plot in an independent sync group that starts later;
a 16:9 locked crop; a text overlay; layout edits with undo/redo; save/reload; MP4 + GIF export.

The synthetic "protein" images encode their trajectory frame number in the red channel of a
central block, so the test can check from pixels which image is on screen at each movie frame.
"""
import os

import imageio.v2 as iio
import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage, QPainter

pytest.importorskip("pytestqt")
pytest.importorskip("MDAnalysisTests")
from MDAnalysisTests.datafiles import DCD, PSF  # noqa: E402

from mdmovie.core import crop as C  # noqa: E402
from mdmovie.core import layout as L  # noqa: E402
from mdmovie.core.project import Overlay, Project  # noqa: E402
from mdmovie.panels import Series  # noqa: E402
from mdmovie.render.compositor import cell_rects, render_frame  # noqa: E402
from mdmovie.render.exporter import ExportOptions, export_movie, qimage_to_rgb  # noqa: E402
from mdmovie.sources.image_sequence import IMAGE_CACHE, qimage_to_array  # noqa: E402
from mdmovie.ui.main_window import MainWindow  # noqa: E402

STRIDE = 2          # one image every 2 trajectory frames
N_TRAJ = 98         # frames in the AdK DCD
BG = QColor("#ffffff")


def make_protein_frames(folder):
    """400×300 white images with a 160×90 block whose red value = 2 × trajectory frame."""
    os.makedirs(folder, exist_ok=True)
    for f in range(0, N_TRAJ, STRIDE):
        img = QImage(400, 300, QImage.Format.Format_RGB32)
        img.fill(BG)
        p = QPainter(img)
        p.fillRect(120, 105, 160, 90, QColor(2 * f, 40, 200))
        p.end()
        img.save(os.path.join(folder, f"frame.{f:04d}.png"))
    return folder


def encoded_frame(rgb, rect):
    """Trajectory frame encoded in the centre of a cell."""
    cx, cy = int(rect.center().x()), int(rect.center().y())
    return round(float(rgb[cy - 2:cy + 3, cx - 2:cx + 3, 0].mean()) / 2)


def cell_of(project, pid, scale=1.0):
    w, h = round(project.size[0] * scale), round(project.size[1] * scale)
    return next(r for _, p, r in cell_rects(project, w, h, scale) if p == pid)


def is_blank(rgb, rect, bg=(255, 255, 255)):
    x0, y0, x1, y1 = int(rect.left()) + 2, int(rect.top()) + 2, int(rect.right()) - 2, int(rect.bottom()) - 2
    return bool((np.abs(rgb[y0:y1, x0:x1].astype(int) - bg).max(axis=-1) < 10).all())


@pytest.fixture
def win(qtbot, monkeypatch):
    monkeypatch.setattr(MainWindow, "_confirm_discard", lambda self: True)
    w = MainWindow()
    qtbot.addWidget(w)
    yield w
    w.analysis.shutdown()


def test_protein_and_rmsd_movie(win, qtbot, tmp_path):
    frames_dir = make_protein_frames(str(tmp_path / "frames"))

    # --- build the project the way the GUI does (every step is an undoable edit) -------------
    ids = {}

    def build(p):
        p.width, p.height, p.fps = 1280, 720, 24
        traj = p.add_trajectory(PSF, [DCD], "AdK")
        meta = traj.meta(p.resolve_path)
        img = p.add_panel("image", "Protein")
        img.props.update(folder=frames_dir, pattern="frame.*.png", index_from="filename",
                         t0=meta.t0, dt=meta.dt)  # file number = trajectory frame
        rmsd = p.add_panel("plot", "RMSD")
        rmsd.series = [Series(traj.id, "RMSD", {"select": "backbone"}, label="RMSD")]
        rmsd.props.update(mode="reveal", show_value=True)
        rg = p.add_panel("plot", "Rg")
        rg.series = [Series(traj.id, "Radius of gyration", {}),
                     Series(traj.id, "End-to-end distance", {}, axis="right")]  # two presets, twin axes
        label = p.add_panel("text", "Clock")
        label.props.update(text="t = {t_ps:.0f} ps", background="")
        p.layout = L.Split("h", [0.5, 0.5], [L.Leaf(img.id), L.Leaf(rmsd.id)])
        p.overlays.append(Overlay(label.id, 0.02, 0.02, 0.3, 0.08))
        ids.update(traj=traj.id, img=img.id, rmsd=rmsd.id, rg=rg.id, label=label.id)
    win.apply("Build scenario", build)

    # dynamic layout: add a cell under the RMSD plot for Rg, then drag the divider
    win.split_cell((1,), "v", True)
    win.assign_panel((1, 1), ids["rg"])
    root = win.project.layout
    (path, i, srect, _), = [d for d in L.iter_dividers(root) if d[0] == ()]
    win.apply("Move divider", lambda p: L.set_divider(p.layout, path, i, 0.55, srect))
    assert L.panel_ids(win.project.layout) == [ids["img"], ids["rmsd"], ids["rg"]]

    # Rg is NOT synced: its own group, starting at movie frame 20, hidden before that
    win.select(("panel", ids["rg"]))
    win.inspector._change_group(ids["rg"], "__new__")
    g2 = win.project.panels[ids["rg"]].group
    assert g2 != win.project.panels[ids["img"]].group

    def unsync(p):
        p.groups[g2].start = 20
        p.groups[g2].before = "hide"
    win.apply("Offset Rg", unsync)

    # crop the protein to 16:9 around its content (what "Lock aspect 16:9" + "Auto-trim" does)
    seq = win.project.panels[ids["img"]].sequence(win.project)
    assert len(seq) == N_TRAJ // STRIDE and seq.gaps == []
    samples = [qimage_to_array(IMAGE_CACHE.get(pth)) for pth in seq.paths[::10]]
    crop = C.normalize(C.auto_trim(samples, aspect=16 / 9), 400, 300)
    win.apply("Crop", lambda p: p.panels[ids["img"]].props.__setitem__("crop", crop))
    cropped = IMAGE_CACHE.get(seq.paths[0], crop)
    assert cropped.width() / cropped.height() == pytest.approx(16 / 9, abs=0.02)
    assert cropped.width() < 400

    # analysis runs in the background
    qtbot.waitUntil(lambda: not win.analysis.busy, timeout=120000)
    pr = win.project

    # --- timing ------------------------------------------------------------------------------
    # group 1 advances one trajectory frame (1 ps) per movie frame; group 2 starts 20 frames later
    assert pr.n_frames == 20 + N_TRAJ
    g1 = pr.panels[ids["img"]].group
    assert pr.group_time(g1, 10) == pytest.approx(pr.group_time(g2, 30))
    assert pr.group_time(g2, 5) is None

    # --- pixels: correct image at each frame, RMSD synced, Rg hidden until it starts -----------
    img_cell, rg_cell = cell_of(pr, ids["img"]), cell_of(pr, ids["rg"])
    for g in (0, 11, 40, 97, 110):
        rgb = qimage_to_rgb(render_frame(pr, g))
        traj_frame = min(g, N_TRAJ - 1)            # group 1 holds its last frame after the end
        shown = encoded_frame(rgb, img_cell)
        assert abs(shown - traj_frame) <= STRIDE / 2, (g, shown)  # nearest rendered image
        assert is_blank(rgb, rg_cell) == (g < 20), g

    # the RMSD value label follows the same clock as the protein (reveal mode grows the line)
    rmsd_cell = cell_of(pr, ids["rmsd"])
    early, late = (qimage_to_rgb(render_frame(pr, g)) for g in (5, 90))

    def blue_px(rgb):  # pixels of the RMSD line colour (#1f77b4)
        r = rmsd_cell
        sub = rgb[int(r.top()):int(r.bottom()), int(r.left()):int(r.right())].astype(int)
        return int((np.abs(sub - (31, 119, 180)).max(axis=-1) < 30).sum())
    assert blue_px(late) > 3 * blue_px(early)

    # --- undo / redo keep working through the whole history ----------------------------------
    win.undo.undo()                                       # un-crop
    assert pr is not win.project and win.project.panels[ids["img"]].props["crop"] is None
    win.undo.redo()
    assert win.project.panels[ids["img"]].props["crop"] == crop

    # --- save, reload, render identically ----------------------------------------------------
    path = str(tmp_path / "scenario.mdmovie.json")
    win.project.path = path
    assert win.save()
    reloaded = Project.load(path)
    assert reloaded.to_dict() == win.project.to_dict()
    a = qimage_to_rgb(render_frame(win.project, 60, 0.5))
    b = qimage_to_rgb(render_frame(reloaded, 60, 0.5))
    assert np.array_equal(a, b)

    # --- export ------------------------------------------------------------------------------
    mp4 = export_movie(reloaded, ExportOptions(str(tmp_path / "movie.mp4"), crf=0, scale=0.5))
    r = iio.get_reader(mp4)
    assert r.count_frames() == reloaded.n_frames
    assert r.get_meta_data()["fps"] == pytest.approx(24)
    frame40 = r.get_data(40)
    assert frame40.shape == (360, 640, 3)
    assert abs(encoded_frame(frame40, cell_of(reloaded, ids["img"], 0.5)) - 40) <= STRIDE / 2 + 1

    gif = export_movie(reloaded, ExportOptions(str(tmp_path / "movie.gif"), scale=0.25, gif_step=4))
    from PIL import Image
    with Image.open(gif) as im:
        assert im.n_frames == len(range(0, reloaded.n_frames, 4))
        assert im.size == (320, 180)
