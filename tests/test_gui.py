"""GUI smoke tests (offscreen)."""
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("MDAnalysisTests")

from PySide6.QtCore import QPoint  # noqa: E402

from mdmovie.core import layout as L  # noqa: E402
from mdmovie.demo import build_demo  # noqa: E402
from mdmovie.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "_confirm_discard", lambda self: True)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    w.open_project(build_demo(str(tmp_path / "demo")))
    qtbot.waitUntil(lambda: not w.analysis.busy, timeout=60000)
    yield w
    w.analysis.shutdown()


def test_demo_loads_and_renders(win, qtbot):
    assert win.project.n_frames == 98
    win.set_frame(50)
    win.canvas.render_now()
    assert win.canvas._image is not None and not win.canvas._image.isNull()
    assert all("✓" in win.inspector._series_text(s)
               for p in win.project.panels.values() for s in p.series)


def test_edit_undo_redo(win):
    n_cells = len(list(L.iter_leaves(win.project.layout)))
    win.split_cell((0,), "v", True)
    assert len(list(L.iter_leaves(win.project.layout))) == n_cells + 1
    win.undo.undo()
    assert len(list(L.iter_leaves(win.project.layout))) == n_cells
    win.undo.redo()
    assert len(list(L.iter_leaves(win.project.layout))) == n_cells + 1
    win.apply_template("Grid 2×2")
    assert isinstance(win.project.layout, L.Split)


def test_independent_group_changes_length(win):
    pid = next(p.id for p in win.project.panels.values() if p.KIND == "plot")
    win.select(("panel", pid))
    win.inspector._change_group(pid, "__new__")
    g = win.project.panels[pid].group
    assert g != "g1"
    win.apply("shift", lambda p: setattr(p.groups[g], "start", 50))
    assert win.project.n_frames == 148
    assert win.project.group_time("g1", 60) != win.project.group_time(g, 60)


def test_dialogs_construct(win, qtbot):
    from mdmovie.ui.crop_dialog import CropDialog
    from mdmovie.ui.dialogs import SeriesDialog
    from mdmovie.ui.export_dialog import ExportDialog
    img = next(p for p in win.project.panels.values() if p.KIND == "image")
    seq = img.sequence(win.project)
    dlg = CropDialog(seq.paths, None, 16 / 9, win)
    dlg.lock.setChecked(True)
    x, y, w, h = dlg.canvas.rect
    assert w / h == pytest.approx(16 / 9, rel=1e-3)
    dlg._auto_trim()
    x, y, w, h = dlg.canvas.rect
    assert w / h == pytest.approx(16 / 9, rel=1e-3) and w < dlg.canvas.bounds[0]
    assert dlg.result_crop() is not None
    sd = SeriesDialog(win, ("timeseries", "profile"))
    assert sd.series().preset
    ExportDialog(win)


def test_canvas_context_and_drag(win, qtbot):
    win.resize(1400, 900)
    win.canvas.render_now()
    (path, i, srect, orient, line), *_ = win.canvas.dividers()
    before = [list(r) for _, _, r in L.iter_leaves(win.project.layout)]
    c = line.center().toPoint()
    qtbot.mousePress(win.canvas, qtbot_button(), pos=c)
    qtbot.mouseMove(win.canvas, c + QPoint(60, 0) if orient == "h" else c + QPoint(0, 40))
    qtbot.mouseRelease(win.canvas, qtbot_button(), pos=c + QPoint(60, 0) if orient == "h" else c + QPoint(0, 40))
    after = [list(r) for _, _, r in L.iter_leaves(win.project.layout)]
    assert before != after
    assert win.undo.canUndo()


def qtbot_button():
    from PySide6.QtCore import Qt
    return Qt.MouseButton.LeftButton


def test_image_folder_asks_for_pattern(win, tmp_path, monkeypatch):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QInputDialog
    d = tmp_path / "jpgs"
    d.mkdir()
    for n in range(3):
        img = QImage(8, 8, QImage.Format.Format_RGB32)
        img.fill(0)
        img.save(str(d / f"f.{n:03d}.jpg"))
    offered = []
    monkeypatch.setattr(QInputDialog, "getItem",
                        lambda parent, title, label, items, current, editable: (offered.append(items), (items[current], True))[1])
    pid = next(p for p, panel in win.project.panels.items() if panel.KIND == "image")
    win.set_image_folder(pid, str(d))
    assert offered == [["*.jpg"]]  # the stale default *.png matches nothing here, so it is not offered
    assert win.project.panels[pid].props["pattern"] == "*.jpg"
    assert len(win.project.panels[pid].sequence(win.project)) == 3
