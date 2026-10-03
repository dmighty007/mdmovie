"""Main window: project tree | canvas | inspector, with transport and timeline at the bottom.

Every edit goes through `apply()`, which snapshots the project before and after so it can
be undone. Widgets keep ids (not objects) because undo swaps in a fresh Project.
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QElapsedTimer, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QKeySequence, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (QApplication, QDialog, QDockWidget, QFileDialog, QLabel, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSplitter, QToolBar,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from mdmovie.analysis.registry import load_user_presets
from mdmovie.core import layout as L
from mdmovie.core.project import FILE_SUFFIX, Overlay, Project
from mdmovie.panels import PANEL_TYPES
from mdmovie.render.compositor import cell_aspect
from mdmovie.render.exporter import export_frame_png
from mdmovie.ui import icons, theme
from mdmovie.ui.analysis_manager import AnalysisManager
from mdmovie.ui.canvas_view import CanvasView
from mdmovie.ui.inspector import Inspector
from mdmovie.ui.timeline_widget import TimelineWidget
from mdmovie.ui.transport import TransportBar
from mdmovie.ui.widgets import last_dir, remember_dir, settings

PROJECT_FILTER = f"MD movie project (*{FILE_SUFFIX} *.json)"


class SnapshotCommand(QUndoCommand):
    def __init__(self, win, text, before, after, merge=None):
        super().__init__(text)
        self.win, self.before, self.after, self.merge = win, before, after, merge
        self._first = True

    def id(self):
        return hash(self.merge) & 0x7FFFFFFF if self.merge else -1

    def mergeWith(self, other):
        if other.merge != self.merge:
            return False
        self.after = other.after
        return True

    def redo(self):
        if self._first:
            self._first = False
            return
        self.win.restore(self.after)

    def undo(self):
        self.win.restore(self.before)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1500, 920)
        self.setWindowIcon(icons.icon("film", "accent", 64))
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.setAcceptDrops(True)              # drop topology + trajectory files (or a project) on the window
        self.project = Project()
        self.frame = 0
        self.selection = None
        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(self._update_title)
        self.analysis = AnalysisManager(self)
        self.analysis.result_ready.connect(self._analysis_done)
        self.analysis.failed.connect(self._analysis_failed)
        self.analysis.progress.connect(self._analysis_progress)
        self.analysis.busy_changed.connect(self._analysis_busy)

        self.canvas = CanvasView(self)
        self.inspector = Inspector(self)
        self.timeline = TimelineWidget(self)
        self.transport = TransportBar()
        self.transport.seek.connect(self.set_frame)
        self.transport.play_toggled.connect(self.set_playing)

        center = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.canvas, 1)
        tl.addWidget(self.transport)
        center.addWidget(top)
        center.addWidget(self.timeline)
        center.setStretchFactor(0, 1)
        center.setSizes([760, 110])
        self.setCentralWidget(center)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(False)
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        left = QDockWidget("PROJECT")
        left.setObjectName("project_dock")
        left.setWidget(self.tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, left)
        right = QDockWidget("INSPECTOR")
        right.setObjectName("inspector_dock")
        right.setWidget(self.inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, right)
        for dock in (left, right):
            dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                             | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.resizeDocks([left, right], [240, 400], Qt.Orientation.Horizontal)

        # status bar: what the movie is (left), analysis progress (right)
        self.statusBar().setSizeGripEnabled(False)
        self.status_info = QLabel()
        self.status_info.setObjectName("statusInfo")
        self.statusBar().addWidget(self.status_info)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(180)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress_label = QLabel()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Cancel the running analysis")
        self.cancel_btn.clicked.connect(self.analysis.cancel)
        for w in (self.progress_label, self.progress, self.cancel_btn):
            self.statusBar().addPermanentWidget(w)
            w.hide()

        self.play_timer = QTimer(self)
        self.play_timer.setInterval(5)
        self.play_timer.timeout.connect(self._tick)
        self.clock = QElapsedTimer()
        self._play_start = 0
        self._fps_frames: list[float] = []

        self._build_menus()
        self._build_toolbar()
        errs = load_user_presets()
        if errs:
            QMessageBox.warning(self, "Preset errors", "\n\n".join(errs))
        self.on_project_changed()

    # --- menus ----------------------------------------------------------------------------
    def _build_menus(self):
        mb = self.menuBar()

        def act(menu, text, cb, shortcut=None, tip=""):
            a = QAction(text, self)
            a.triggered.connect(cb)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setStatusTip(tip)
            menu.addAction(a)
            return a

        f = mb.addMenu("&File")
        act(f, "&New project", self.new_project, QKeySequence.StandardKey.New)
        act(f, "&Open…", lambda: self.open_project(), QKeySequence.StandardKey.Open)
        act(f, "Open &demo project", self.open_demo, tip="AdK trajectory from MDAnalysisTests")
        act(f, "&Save", self.save, QKeySequence.StandardKey.Save)
        act(f, "Save &as…", self.save_as, QKeySequence.StandardKey.SaveAs)
        f.addSeparator()
        act(f, "&Export movie…", self.export_movie, "Ctrl+E")
        act(f, "Export current frame as PNG…", self.export_png, "Ctrl+Shift+E")
        f.addSeparator()
        act(f, "&Quit", self.close, QKeySequence.StandardKey.Quit)

        e = mb.addMenu("&Edit")
        u = self.undo.createUndoAction(self, "&Undo")
        u.setShortcut(QKeySequence.StandardKey.Undo)
        r = self.undo.createRedoAction(self, "&Redo")
        r.setShortcut(QKeySequence.StandardKey.Redo)
        e.addAction(u)
        e.addAction(r)
        e.addSeparator()
        act(e, "Delete selected panel", self._delete_selected, QKeySequence.StandardKey.Delete)
        act(e, "Canvas settings", lambda: self.select(None))

        add = mb.addMenu("&Add")
        for kind, cls in PANEL_TYPES.items():
            act(add, f"{cls.TITLE} panel", lambda _=False, k=kind: self.new_panel(k))
        add.addSeparator()
        ov = add.addMenu("Overlay")
        for kind, cls in PANEL_TYPES.items():
            act(ov, cls.TITLE, lambda _=False, k=kind: self.new_overlay(k, 0.03, 0.03))
        add.addSeparator()
        act(add, "Load &trajectory…", self.load_trajectory, "Ctrl+T")
        act(add, "Sync &group", lambda: self.apply("New sync group", lambda p: p.add_group()))

        lay = mb.addMenu("&Layout")
        for name in L.TEMPLATES:
            act(lay, name, lambda _=False, n=name: self.apply_template(n))

        an = mb.addMenu("A&nalysis")
        act(an, "Reload preset files", self.reload_presets,
            tip="Load presets from ~/.config/mdmovie/presets and <project>/presets")
        act(an, "Open user preset folder", self._open_preset_folder)
        act(an, "Cancel running analysis", self.analysis.cancel)

        v = mb.addMenu("&View")
        for dock in self.findChildren(QDockWidget):
            v.addAction(dock.toggleViewAction())
        v.addSeparator()
        tm = v.addMenu("Theme")
        group = QActionGroup(self)
        current = str(settings().value("theme", "dark"))
        for key, label in (("dark", "Dark"), ("light", "Light")):
            a = QAction(label, self, checkable=True, checked=key == current)
            a.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            group.addAction(a)
            tm.addAction(a)

        # playback shortcuts
        for key, cb in ((Qt.Key.Key_Space, lambda: self.set_playing(not self.play_timer.isActive())),
                        (Qt.Key.Key_Left, lambda: self.set_frame(self.frame - 1)),
                        (Qt.Key.Key_Right, lambda: self.set_frame(self.frame + 1)),
                        (Qt.Key.Key_Home, lambda: self.set_frame(0)),
                        (Qt.Key.Key_End, lambda: self.set_frame(self.project.n_frames - 1))):
            a = QAction(self)
            a.setShortcut(QKeySequence(key))
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            a.triggered.connect(cb)
            self.canvas.addAction(a)
            self.timeline.addAction(a)
            self.transport.addAction(a)

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        self._tb_actions = []

        def add(icon_name, text, cb, tip="", icon_only=False):
            a = QAction(icons.icon(icon_name), text, self)
            a.setToolTip(tip or text)
            a.triggered.connect(cb)
            tb.addAction(a)
            if icon_only:
                tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self._tb_actions.append((a, icon_name))
            return a

        add("new", "New", self.new_project, "New project (Ctrl+N)", True)
        add("open", "Open", lambda: self.open_project(), "Open project (Ctrl+O)", True)
        add("save", "Save", self.save, "Save project (Ctrl+S)", True)
        tb.addSeparator()
        undo = add("undo", "Undo", self.undo.undo, "Undo (Ctrl+Z)", True)
        redo = add("redo", "Redo", self.undo.redo, "Redo (Ctrl+Shift+Z)", True)
        self.undo.canUndoChanged.connect(undo.setEnabled)
        self.undo.canRedoChanged.connect(redo.setEnabled)
        undo.setEnabled(False)
        redo.setEnabled(False)
        tb.addSeparator()
        caption = QLabel("ADD")
        caption.setObjectName("toolbarCaption")
        caption.setToolTip("Add a panel to the selected (or first empty) cell")
        tb.addWidget(caption)
        add("image", "Frames", lambda: self.new_panel("image"), "Add an image-sequence panel (protein frames)")
        add("molecule", "Molecule", lambda: self.new_panel("molecule"),
            "Add a molecule panel: draw a trajectory here (cartoon, sticks, spheres) instead of loading images")
        add("plot", "Plot", lambda: self.new_panel("plot"), "Add a plot panel (analysis time series)")
        add("heatmap", "Heatmap", lambda: self.new_panel("heatmap"), "Add a heatmap panel (e.g. DSSP)")
        add("cvmap", "CV map", lambda: self.new_panel("cvmap"),
            "Add a CV map: two variables as a moving point over a free-energy surface")
        add("text", "Text", lambda: self.new_overlay("text", 0.03, 0.03), "Add a text overlay (titles, time label)")
        tb.addSeparator()
        add("trajectory", "Trajectory", self.load_trajectory, "Load a topology + trajectory (Ctrl+T)")
        lay_menu = QMenu(self)
        for name in L.TEMPLATES:
            lay_menu.addAction(name, lambda n=name: self.apply_template(n))
        lay_act = add("layout", "Layout ▾", lambda: None, "Apply a layout template")
        lay_act.setMenu(lay_menu)
        tb.widgetForAction(lay_act).setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        export = QAction(icons.icon("export", "on_accent"), "Export movie", self)
        export.setToolTip("Export MP4 / GIF (Ctrl+E)")
        export.triggered.connect(self.export_movie)
        tb.addAction(export)
        tb.widgetForAction(export).setObjectName("primary")
        self._export_action = export

    def set_theme(self, name: str):
        settings().setValue("theme", name)
        theme.apply_theme(QApplication.instance(), name)
        self.refresh_icons()

    def refresh_icons(self):
        """Re-tint every icon for the current theme."""
        self.setWindowIcon(icons.icon("film", "accent", 64))
        for a, name in getattr(self, "_tb_actions", []):
            a.setIcon(icons.icon(name))
        if hasattr(self, "_export_action"):
            self._export_action.setIcon(icons.icon("export", "on_accent"))
        self.transport.refresh_icons()
        old = self.canvas.welcome
        from mdmovie.ui.canvas_view import WelcomeCard
        self.canvas.welcome = WelcomeCard(self, self.canvas)
        old.deleteLater()
        self.canvas.update_welcome()
        self._rebuild_tree()
        self.inspector.rebuild()
        self.canvas.update()
        self.timeline.update()

    # --- edit machinery -------------------------------------------------------------------
    def apply(self, text, fn, merge=None, structural=True):
        """Run fn(project) as one undoable edit."""
        before = self.project.to_dict()
        fn(self.project)
        after = self.project.to_dict()
        if before == after:
            return
        self.undo.push(SnapshotCommand(self, text, before, after, merge))
        self.on_project_changed(structural)

    def push_snapshot(self, text, before, after):
        """Record an edit that was already applied live (e.g. a drag)."""
        self.undo.push(SnapshotCommand(self, text, before, after))
        self.on_project_changed(structural=False)

    def restore(self, d):
        self.project = Project.from_dict(d, self.project.path)
        self.on_project_changed(structural=True)

    def on_project_changed(self, structural=True):
        self._validate_selection()
        self.frame = min(self.frame, max(0, self.project.n_frames - 1))
        self.transport.set_state(self.frame, self.project.n_frames, self.project.fps)
        self.canvas.request_render()
        self.canvas.update_welcome()
        self.timeline.refresh()
        self._rebuild_tree()
        if structural:
            self.inspector.rebuild()
        else:
            self.inspector.refresh_status()
        self.analysis.ensure(self.project)
        self._update_title()
        self.update_status()

    def update_status(self):
        """Left side of the status bar: output size, length and how far the preview is scaled down."""
        pr = self.project
        n = pr.n_frames
        zoom = self.canvas.display_rect().width() / pr.size[0]
        self.status_info.setText(f"{pr.size[0]} × {pr.size[1]} px   ·   {pr.fps:g} fps   ·   {n} frames "
                                 f"({n / pr.fps:.2f} s)   ·   preview {zoom:.0%}")

    def _validate_selection(self):
        s, pr = self.selection, self.project
        if s is None:
            return
        ok = ((s[0] == "panel" and s[1] in pr.panels) or (s[0] == "group" and s[1] in pr.groups)
              or (s[0] == "traj" and s[1] in pr.trajectories)
              or (s[0] == "overlay" and s[1] < len(pr.overlays))
              or (s[0] == "cell" and self._is_leaf(s[1])))
        if not ok:
            self.selection = None

    def _is_leaf(self, path):
        try:
            return isinstance(L.get(self.project.layout, path), L.Leaf)
        except (IndexError, AttributeError):
            return False

    def select(self, sel):
        if sel is not None and sel[0] == "panel" and sel[1] is None:
            sel = None
        self.selection = sel
        self._validate_selection()
        self._rebuild_tree()
        self.inspector.rebuild()
        self.canvas.update()
        self.timeline.update()

    def _update_title(self):
        name = os.path.basename(self.project.path) if self.project.path else "Untitled"
        self.setWindowTitle(f"{name}{'' if self.undo.isClean() else ' •'} — MD Movie Maker")

    # --- tree -----------------------------------------------------------------------------
    def _rebuild_tree(self):
        pr = self.project
        self.tree.blockSignals(True)
        self.tree.clear()
        muted = QBrush(QColor(theme.C.muted))
        head_font = QFont(self.tree.font())
        head_font.setPointSizeF(head_font.pointSizeF() * 0.82)
        head_font.setWeight(QFont.Weight.Bold)
        head_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        canvas = QTreeWidgetItem(self.tree, ["Canvas"])
        canvas.setIcon(0, icons.icon("canvas"))
        canvas.setData(0, Qt.ItemDataRole.UserRole, None)
        placed = pr.placed_panels()
        sections = [
            ("Panels", [(("panel", pid), p.name + ("" if pid in placed else "  (hidden)"),
                         icons.KIND_ICONS.get(p.KIND, "canvas")) for pid, p in pr.panels.items()]),
            ("Sync groups", [(("group", gid), g.name, "group") for gid, g in pr.groups.items()]),
            ("Trajectories", [(("traj", tid), t.name, "trajectory") for tid, t in pr.trajectories.items()])]
        selected_item = canvas if self.selection is None else None
        for title, items in sections:
            parent = QTreeWidgetItem(self.tree, [f"{title.upper()}   {len(items)}"])
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            parent.setFont(0, head_font)
            parent.setForeground(0, muted)
            parent.setExpanded(True)
            for sel, text, icon_name in items:
                it = QTreeWidgetItem(parent, [text])
                it.setIcon(0, icons.icon(icon_name, "muted"))
                it.setData(0, Qt.ItemDataRole.UserRole, sel)
                if sel[0] == "group":
                    it.setToolTip(0, "Sync group")
                if sel == self.selection or (self.selection and self.selection[0] == "overlay" and sel == (
                        "panel", pr.overlays[self.selection[1]].panel)):
                    selected_item = it
        self.tree.expandAll()
        if selected_item is not None:
            selected_item.setSelected(True)
        self.tree.blockSignals(False)

    def _tree_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        sel = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.select(tuple(sel) if sel else None)

    def _tree_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        item = self.tree.itemAt(pos)
        sel = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        menu = QMenu(self)
        if sel and sel[0] == "panel":
            pid = sel[1]
            if pid not in self.project.placed_panels():
                menu.addAction("Show as overlay", lambda: self.apply(
                    "Show panel as overlay", lambda p: p.overlays.append(Overlay(pid))))
            menu.addAction("Delete panel", lambda: self.delete_panel(pid))
        elif sel and sel[0] == "traj":
            menu.addAction("Remove trajectory", lambda: self.remove_trajectory(sel[1]))
        elif sel and sel[0] == "group":
            menu.addAction("Delete group", lambda: self.apply("Delete group", lambda p: p.remove_group(sel[1])))
        else:
            for kind, cls in PANEL_TYPES.items():
                menu.addAction(f"New {cls.TITLE.lower()} panel", lambda k=kind: self.new_panel(k))
            menu.addAction("Load trajectory…", self.load_trajectory)
            menu.addAction("New sync group", lambda: self.apply("New sync group", lambda p: p.add_group()))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # --- playback -------------------------------------------------------------------------
    def set_frame(self, f):
        n = self.project.n_frames
        f = max(0, min(int(f), n - 1))
        if f == self.frame and not self.canvas._pending:
            self.transport.set_state(f, n, self.project.fps)
            return
        self.frame = f
        self.transport.set_state(f, n, self.project.fps)
        self.canvas.request_render()
        self.timeline.update()

    def set_playing(self, on):
        if on:
            if self.frame >= self.project.n_frames - 1:
                self.frame = 0
            self._play_start = self.frame
            self.clock.start()
            self._fps_frames.clear()
            self.play_timer.start()
        else:
            self.play_timer.stop()
            self.transport.perf.setText("")
        self.transport.set_playing(on)

    def _tick(self):
        if self.canvas._pending:
            return  # still drawing the previous frame: drop frames rather than lag
        n = self.project.n_frames
        target = self._play_start + int(self.clock.elapsed() * self.project.fps / 1000)
        if target >= n:
            if not self.transport.loop.isChecked():
                self.set_frame(n - 1)
                self.set_playing(False)
                return
            self._play_start, target = 0, 0
            self.clock.restart()
        if target != self.frame:
            self.set_frame(target)
            now = time.perf_counter()
            self._fps_frames = [t for t in self._fps_frames if now - t < 1.0] + [now]
            self.transport.perf.setText(f"{len(self._fps_frames)} fps drawn")

    # --- layout actions -------------------------------------------------------------------
    def new_panel(self, kind):
        """Create a panel and place it in the selected empty cell, the first empty cell, or a new cell."""
        box = {}

        def fn(p):
            panel = p.add_panel(kind)
            box["id"] = panel.id
            target = None
            if self.selection and self.selection[0] == "cell":
                target = self.selection[1]
            else:
                target = next((path for path, leaf, _ in L.iter_leaves(p.layout)
                               if leaf.panel is None or leaf.panel not in p.panels), None)
            if target is None:
                p.layout = L.split_leaf(p.layout, (), "h", panel.id) if isinstance(p.layout, L.Leaf) else \
                    L.split_leaf(p.layout, (len(p.layout.children) - 1,), p.layout.orient, panel.id)
            else:
                L.get(p.layout, target).panel = panel.id
        self.apply(f"New {PANEL_TYPES[kind].TITLE.lower()} panel", fn)
        self.select(("panel", box["id"]))
        self._after_new_panel(box["id"])

    def new_panel_in_cell(self, kind, path):
        box = {}

        def fn(p):
            panel = p.add_panel(kind)
            box["id"] = panel.id
            L.get(p.layout, path).panel = panel.id
        self.apply(f"New {PANEL_TYPES[kind].TITLE.lower()} panel", fn)
        self.select(("panel", box["id"]))
        self._after_new_panel(box["id"])

    def new_overlay(self, kind, x, y):
        box = {}

        def fn(p):
            panel = p.add_panel(kind)
            box["id"] = panel.id
            if kind == "text":
                panel.props["background"] = ""
            w, h = (0.3, 0.08) if kind == "text" else (0.3, 0.3)
            p.overlays.append(Overlay(panel.id, min(x, 1 - w), min(y, 1 - h), w, h))
        self.apply("New overlay", fn)
        self.select(("overlay", len(self.project.overlays) - 1))
        self._after_new_panel(box["id"])

    def _after_new_panel(self, pid):
        """Guide the user to the first thing a new panel needs."""
        panel = self.project.panels.get(pid)
        if panel is None:
            return
        if panel.KIND == "image":
            d = QFileDialog.getExistingDirectory(self, "Choose the folder with the image sequence", last_dir())
            if d:
                remember_dir(d)
                self.set_image_folder(pid, d)
        elif panel.KIND == "molecule":
            tid = next(iter(self.project.trajectories), None) or self.load_trajectory()
            if tid:
                self.set_molecule_trajectory(pid, tid)
        elif panel.HAS_SERIES:
            self.edit_series(pid, None)

    def set_image_folder(self, pid, folder):
        """Point an image panel at a folder (or single picture); for a folder, let the user pick the pattern."""
        from mdmovie.ui.dialogs import choose_pattern
        props = self.project.panels[pid].props
        pattern = choose_pattern(self, folder, props["pattern"]) if os.path.isdir(folder) else None

        def fn(p):
            p.panels[pid].props["folder"] = folder
            if pattern:
                p.panels[pid].props["pattern"] = pattern
        self.apply("Choose image folder", fn)

    def assign_panel(self, path, pid):
        self.apply("Assign panel", lambda p: setattr(L.get(p.layout, path), "panel", pid))
        self.select(("panel", pid) if pid else ("cell", path))

    def split_cell(self, path, orient, after):
        def fn(p):
            p.layout = L.split_leaf(p.layout, path, orient, None, after)
        self.apply("Split cell", fn)

    def remove_cell(self, path):
        def fn(p):
            p.layout = L.remove_leaf(p.layout, path)
        self.apply("Remove cell", fn)
        self.select(None)

    def swap_cells(self, a, b):
        def fn(p):
            la, lb = L.get(p.layout, a), L.get(p.layout, b)
            la.panel, lb.panel = lb.panel, la.panel
        self.apply("Swap cells", fn)

    def apply_template(self, name):
        def fn(p):
            p.layout = L.apply_template(name, p.layout)
        self.apply(f"Layout: {name}", fn)

    def delete_panel(self, pid):
        if pid not in self.project.panels:
            return
        self.apply("Delete panel", lambda p: p.remove_panel(pid))
        self.select(None)

    def _delete_selected(self):
        s = self.selection
        if s and s[0] == "panel":
            self.delete_panel(s[1])
        elif s and s[0] == "overlay":
            self.apply("Remove overlay", lambda p: p.overlays.pop(s[1]))
            self.select(None)
        elif s and s[0] == "cell":
            self.remove_cell(s[1])

    # --- images ---------------------------------------------------------------------------
    def crop_panel(self, pid):
        from mdmovie.ui.crop_dialog import CropDialog
        panel = self.project.panels.get(pid)
        if panel is None or panel.KIND != "image":
            return
        seq = panel.sequence(self.project)
        if not len(seq):
            QMessageBox.information(self, "Crop", "This panel has no images yet. Choose a folder first.")
            return
        t = self.project.group_time(panel.group, self.frame)
        start = seq.index_at(t) if t is not None else 0
        dlg = CropDialog(seq.paths, panel.props["crop"], cell_aspect(self.project, pid), self, start)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            crop = dlg.result_crop()
            self.apply("Crop", lambda p: p.panels[pid].props.__setitem__("crop", crop))

    def match_timing(self, pid):
        from mdmovie.ui.dialogs import MatchTimingDialog
        if not self.project.trajectories:
            if not self.load_trajectory():
                return
        dlg = MatchTimingDialog(self.project, self, self.project.panels[pid].props["index_from"])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            tm = dlg.timing()
            if tm:
                def fn(p):
                    p.panels[pid].props.update(t0=tm[0], dt=tm[1])
                self.apply("Match trajectory timing", fn)

    def calibrate_overlay(self, pid):
        """Mark where the plot axes sit in a pre-rendered picture so data can be drawn on it."""
        from mdmovie.panels.series_data import xy_data
        from mdmovie.ui.calibrate_dialog import CalibrateDialog
        panel = self.project.panels.get(pid)
        seq = panel.sequence(self.project) if panel is not None else None
        if not seq or not len(seq):
            QMessageBox.information(self, "Calibrate", "Choose the image (or folder) first.")
            return
        p = panel.props
        data = xy_data(self.project, panel.series, p["x_col"], p["y_col"])
        dlg = CalibrateDialog(seq.paths[0], p["calib"], (p["ax_xmin"], p["ax_xmax"], p["ax_ymin"], p["ax_ymax"]),
                              data, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            calib, (x0, x1, y0, y1) = dlg.result()

            def fn(pr):
                pr.panels[pid].props.update(calib=calib, ax_xmin=x0, ax_xmax=x1, ax_ymin=y0, ax_ymax=y1,
                                            overlay=True)
            self.apply("Calibrate overlay", fn)

    # --- molecule panels ------------------------------------------------------------------
    def set_molecule_trajectory(self, pid, tid):
        if tid == "__new__":
            tid = self.load_trajectory()
        if tid:
            self.apply("Choose trajectory", lambda p: p.panels[pid].props.__setitem__("traj", tid))
        self.inspector.rebuild()

    def _molecule_scene(self, pid):
        """The panel's loaded scene (None if it has no trajectory or it cannot be read)."""
        try:
            return self.project.panels[pid].scene(self.project)
        except Exception as e:
            self.statusBar().showMessage(f"Cannot read the trajectory: {e}", 8000)
            return None

    def edit_rep(self, pid, index):
        from mdmovie.ui.dialogs import RepDialog
        panel = self.project.panels[pid]
        if panel.trajectory(self.project) is None:
            QMessageBox.information(self, "Representation", "Choose a trajectory for this panel first.")
            return
        dlg = RepDialog(self, self._molecule_scene(pid), panel.props["reps"][index] if index is not None else None)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        rep = dlg.rep()

        def fn(p):
            reps = p.panels[pid].props["reps"]
            if index is None:
                reps.append(rep)
            else:
                reps[index] = rep
        self.apply("Edit representation" if index is not None else "Add representation", fn)

    def remove_rep(self, pid, index):
        self.apply("Remove representation", lambda p: p.panels[pid].props["reps"].pop(index))

    def set_molecule_view(self, pid, name):
        """A named axis view ('front', 'side', 'top'), or None to also re-centre and zoom to fit."""
        from mdmovie.mol.render import AXIS_VIEWS

        def fn(p):
            panel = p.panels[pid]
            if name is None:
                panel.props.update(panel.reset_view())
            else:
                panel.props["rotation"] = [float(v) for v in AXIS_VIEWS[name].ravel()]
        self.apply("Reset view" if name is None else f"{name.capitalize()} view", fn, structural=False)

    # --- trajectories and analysis --------------------------------------------------------
    def load_trajectory(self):
        """Ask for topology + trajectory files; returns the new trajectory id (or None)."""
        from mdmovie.ui.dialogs import TrajectoryDialog
        dlg = TrajectoryDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        top, trajs, name = dlg.values()
        dt, whole = dlg.dt.value(), dlg.whole.isChecked()
        box = {}

        def fn(p):
            t = p.add_trajectory(top, trajs, name)
            t.dt, t.whole = dt, whole
            box["id"] = t.id
            for panel in p.panels.values():       # molecule panels still waiting for a trajectory take this one
                if panel.KIND == "molecule" and panel.props["traj"] not in p.trajectories:
                    panel.props["traj"] = t.id
        self.apply("Load trajectory", fn)
        remember_dir(top)
        return box["id"]

    def edit_trajectory(self, tid):
        from mdmovie.ui.dialogs import TrajectoryDialog
        t = self.project.trajectories[tid]
        dlg = TrajectoryDialog(self, t.topology, t.trajectories, t.name, t.dt, t.whole)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            top, trajs, name = dlg.values()
            dt, whole = dlg.dt.value(), dlg.whole.isChecked()

            def fn(p):
                tr = p.trajectories[tid]
                tr.topology, tr.trajectories, tr.name, tr.dt, tr.whole = top, trajs, name, dt, whole
            self.apply("Change trajectory", fn)

    def remove_trajectory(self, tid):
        used = [p.name for p in self.project.panels.values() for s in p.series if s.traj == tid]
        used += [p.name for p in self.project.panels.values() if p.props.get("traj") == tid]
        if used and QMessageBox.question(
                self, "Remove trajectory", f"Used by {', '.join(sorted(set(used)))}. Remove anyway?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.apply("Remove trajectory", lambda p: p.trajectories.pop(tid, None))
        self.select(None)

    def edit_series(self, pid, index):
        from mdmovie.ui.dialogs import SeriesDialog
        panel = self.project.panels[pid]
        kinds = getattr(panel, "SERIES_KINDS", ("timeseries",))
        current = panel.series[index] if index is not None else None
        dlg = SeriesDialog(self, kinds, current, plot_style=panel.KIND == "plot")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        s = dlg.series()

        def fn(p):
            series = p.panels[pid].series
            if index is None:
                series.append(s)
            else:
                series[index] = s
        self.apply("Edit series" if index is not None else "Add series", fn)

    def remove_series(self, pid, index):
        self.apply("Remove series", lambda p: p.panels[pid].series.pop(index))

    def recompute_series(self, pid, index):
        self.analysis.recompute(self.project, self.project.panels[pid].series[index])
        self.canvas.request_render()
        self.inspector.refresh_status()

    def reload_presets(self):
        errs = load_user_presets([os.path.join(self.project.base_dir, "presets")])
        if errs:
            QMessageBox.warning(self, "Preset errors", "\n\n".join(errs))
        else:
            self.statusBar().showMessage("Presets reloaded", 4000)

    def _open_preset_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from mdmovie.analysis.registry import USER_PRESET_DIR
        os.makedirs(USER_PRESET_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(USER_PRESET_DIR))

    def _analysis_done(self, key):
        self.on_project_changed(structural=False)

    def _analysis_failed(self, key, err):
        if err != "cancelled":
            self.statusBar().showMessage(f"Analysis failed: {err}", 10000)
        self.on_project_changed(structural=False)

    def _analysis_progress(self, label, i, n):
        self.progress_label.setText(f"Analysis: {label}")
        self.progress.setMaximum(max(n, 1))
        self.progress.setValue(i)

    def _analysis_busy(self, busy):
        for w in (self.progress_label, self.progress, self.cancel_btn):
            w.setVisible(busy)
        self.inspector.refresh_status()

    # --- files ----------------------------------------------------------------------------
    def _confirm_discard(self) -> bool:
        if self.undo.isClean():
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes to the current project?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                 QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save()
        return r == QMessageBox.StandardButton.Discard

    def _set_project(self, pr):
        self.set_playing(False)
        self.project = pr
        self.frame = 0
        self.selection = None
        self.undo.clear()
        self.undo.setClean()
        if pr.path:
            load_user_presets([os.path.join(pr.base_dir, "presets")])
        self.on_project_changed()

    def new_project(self):
        if self._confirm_discard():
            self._set_project(Project())

    def open_project(self, path=None):
        if path is None:
            if not self._confirm_discard():
                return
            path, _ = QFileDialog.getOpenFileName(self, "Open project", last_dir(), PROJECT_FILTER)
            if not path:
                return
        try:
            pr = Project.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Open project", f"Cannot open {path}:\n{e}")
            return
        remember_dir(path)
        self._set_project(pr)

    def open_demo(self):
        if not self._confirm_discard():
            return
        try:
            from mdmovie.demo import build_demo
        except ImportError as e:
            QMessageBox.critical(self, "Demo", str(e))
            return
        self.statusBar().showMessage("Building demo project…")
        self.repaint()
        try:
            path = build_demo(os.path.join(os.path.expanduser("~"), ".cache", "mdmovie", "demo"))
        except Exception as e:
            QMessageBox.critical(self, "Demo", f"Could not build the demo (needs MDAnalysisTests: pip install \"mdmovie[demo]\"):\n{e}")
            return
        self.open_project(path)
        self.statusBar().showMessage("Demo loaded — analysis runs in the background", 6000)

    # --- starting from trajectory files -------------------------------------------------------
    def start_from_trajectory(self, files: list[str] | None = None) -> bool:
        """A new movie (molecule, RMSD and Rg plots, time label) from topology + trajectory files, given or
        chosen in the trajectory dialog."""
        from mdmovie.core.quickstart import split_files, trajectory_movie
        if not self._confirm_discard():
            return False
        if files:
            try:
                top, trajs = split_files(files)
            except ValueError as e:
                QMessageBox.warning(self, "Start from a trajectory", str(e))
                return False
            name, dt, whole = "", 0.0, True
        else:
            from mdmovie.ui.dialogs import TrajectoryDialog
            dlg = TrajectoryDialog(self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return False
            (top, trajs, name), dt, whole = dlg.values(), dlg.dt.value(), dlg.whole.isChecked()
        self.statusBar().showMessage(f"Reading {os.path.basename(top)}…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            pr = Project()
            tid = trajectory_movie(pr, top, trajs, name, dt, whole)
        except Exception as e:
            QMessageBox.critical(self, "Cannot load trajectory", f"{type(e).__name__}: {e}")
            self.statusBar().clearMessage()
            return False
        finally:
            QApplication.restoreOverrideCursor()
        remember_dir(top)
        self._set_project(pr)
        self.undo.resetClean()                 # a new, unsaved project
        self._update_title()
        meta = pr.trajectories[tid].meta(pr.resolve_path)
        self.statusBar().showMessage(
            f"{meta.n_frames:,} frames, {meta.n_atoms:,} atoms. Drag the molecule to turn it; analysis runs in "
            "the background." + ("" if meta.timed else " The file stores no frame times: set the time between "
                                 "frames on the trajectory's page."), 12000)
        return True

    def add_trajectory_files(self, files: list[str]) -> None:
        """Dropped topology + trajectory files on a project that already has content: add them as a trajectory
        (molecule panels still waiting for one take it)."""
        from mdmovie.core.quickstart import split_files
        try:
            top, trajs = split_files(files)
            from mdmovie.sources.trajectory import TrajectorySource
            TrajectorySource("tmp", "", top, trajs).meta(lambda p: p)
        except Exception as e:
            QMessageBox.warning(self, "Add trajectory", f"{e}")
            return
        box = {}

        def fn(p):
            t = p.add_trajectory(top, trajs)
            box["id"] = t.id
            for panel in p.panels.values():
                if panel.KIND == "molecule" and panel.props["traj"] not in p.trajectories:
                    panel.props["traj"] = t.id
        self.apply("Load trajectory", fn)
        remember_dir(top)
        self.select(("traj", box["id"]))
        self.statusBar().showMessage("Trajectory added: pick it in a molecule panel or a plot series.", 8000)

    @staticmethod
    def _dropped_files(e) -> list[str]:
        from mdmovie.core.quickstart import PROJECT_EXT, is_md_file
        md = e.mimeData()
        files = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
        return files if files and all(is_md_file(f) or f.lower().endswith(PROJECT_EXT) for f in files) else []

    def dragEnterEvent(self, e):
        if self._dropped_files(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = self._dropped_files(e)
        if not files:
            return
        e.acceptProposedAction()
        projects = [f for f in files if f.lower().endswith(".json")]
        # let the drop finish before opening dialogs
        if projects:
            QTimer.singleShot(0, lambda: self._confirm_discard() and self.open_project(projects[0]))
        elif self.project.panels:
            QTimer.singleShot(0, lambda: self.add_trajectory_files(files))
        else:
            QTimer.singleShot(0, lambda: self.start_from_trajectory(files))

    def save(self) -> bool:
        if not self.project.path:
            return self.save_as()
        try:
            self.project.save(self.project.path)
        except Exception as e:
            QMessageBox.critical(self, "Save", str(e))
            return False
        self.undo.setClean()
        self._update_title()
        self.statusBar().showMessage(f"Saved {self.project.path}", 4000)
        return True

    def save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save project", self.project.path or os.path.join(
            last_dir(), "movie" + FILE_SUFFIX), PROJECT_FILTER)
        if not path:
            return False
        if not path.endswith(".json"):
            path += FILE_SUFFIX
        remember_dir(path)
        self.project.path = os.path.abspath(path)
        return self.save()

    def export_movie(self):
        from mdmovie.ui.export_dialog import ExportDialog
        if self.analysis.busy:
            self.statusBar().showMessage("Analysis still running; the export will compute what it needs.", 6000)
        ExportDialog(self).exec()

    def export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export frame", os.path.join(
            last_dir("export_dir"), f"frame_{self.frame:05d}.png"), "PNG (*.png)")
        if path:
            remember_dir(path, "export_dir")
            export_frame_png(self.project, self.frame, path)
            self.statusBar().showMessage(f"Saved {path}", 5000)

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self.set_playing(False)
        self.analysis.shutdown()
        e.accept()

