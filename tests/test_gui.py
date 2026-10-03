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


def _form_labels(win):
    from PySide6.QtWidgets import QLabel
    return {w.text() for w in win.inspector.widget().findChildren(QLabel) if w.objectName() == "formLabel"}


def test_inspector_shows_only_relevant_settings(win, qtbot):
    pid = {}
    win.apply("cv", lambda p: pid.setdefault("id", p.add_panel("cvmap").id))
    win.select(("panel", pid["id"]))
    labels = _form_labels(win)
    assert "Histogram bins" in labels and "Background image" not in labels and "File columns x, y, F" not in labels
    win.inspector._set_prop(pid["id"], "bg_mode", "free energy file")
    qtbot.wait(50)                                   # the form rebuilds itself after the change
    labels = _form_labels(win)
    assert "File columns x, y, F" in labels and "Histogram bins" not in labels and "Temperature (K)" not in labels
    win.inspector._set_prop(pid["id"], "bg_mode", "none")
    qtbot.wait(50)
    assert not {"Colormap", "Background opacity", "Contour lines"} & _form_labels(win)


def test_inspector_never_wider_than_its_dock(win, qtbot):
    win.show()
    for pid in win.project.panels:                   # long labels wrap instead of pushing fields out of view
        win.select(("panel", pid))
        qtbot.wait(20)
        assert win.inspector.widget().width() <= win.inspector.viewport().width()


def test_double_click_list_that_rebuilds_inspector(win, qtbot, monkeypatch):
    """Double-clicking a series (or representation) opens a dialog whose OK rebuilds the inspector, deleting
    the list that was double-clicked. Done inside the list's mouse handler, that crashed Qt (SIGSEGV)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidget
    pid = next(p.id for p in win.project.panels.values() if p.KIND == "plot")
    win.select(("panel", pid))
    edited = []

    def edit_series(p, index):                       # what pressing OK in the dialog does
        edited.append(index)
        win.apply("Edit series", lambda pr: setattr(pr.panels[p].series[index], "label", "renamed"))
    monkeypatch.setattr(win, "edit_series", edit_series)
    lst = win.inspector._series_list
    at = lst.visualItemRect(lst.item(0)).center()
    qtbot.mouseClick(lst.viewport(), Qt.MouseButton.LeftButton, pos=at)   # a double-click starts with a press
    qtbot.mouseDClick(lst.viewport(), Qt.MouseButton.LeftButton, pos=at)
    qtbot.waitUntil(lambda: edited == [0])
    assert win.project.panels[pid].series[0].label == "renamed"
    assert win.inspector.widget().findChildren(QListWidget)


def test_dropping_trajectory_files_starts_a_movie(qtbot, monkeypatch):
    from MDAnalysisTests.datafiles import DCD, PSF
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    monkeypatch.setattr(MainWindow, "_confirm_discard", lambda self: True)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(DCD), QUrl.fromLocalFile(PSF)])
    w.dropEvent(QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier))
    qtbot.waitUntil(lambda: any(p.KIND == "molecule" for p in w.project.panels.values()), timeout=20000)
    assert not w.undo.isClean()                     # an unsaved new project
    md.setUrls([QUrl.fromLocalFile(DCD), QUrl.fromLocalFile(PSF)])
    w.dropEvent(QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier))
    qtbot.waitUntil(lambda: len(w.project.trajectories) == 2)   # added to the movie, not a new one
    w.analysis.shutdown()
