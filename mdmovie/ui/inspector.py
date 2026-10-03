"""Inspector: edits whatever is selected (canvas settings, a panel, a sync group, a trajectory
or an empty cell). Panel forms are generated from each panel class's Prop schema."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
                               QWidget)

from mdmovie.analysis import runner
from mdmovie.core.project import RESOLUTION_PRESETS
from mdmovie.core.timemap import EDGE_MODES
from mdmovie.panels import PANEL_TYPES
from mdmovie.panels.text_panel import FIELDS_HELP
from mdmovie.ui import icons
from mdmovie.ui.widgets import (ColorButton, HeaderCard, OptFloatEdit, Section, button_row, make_editor,
                                note)

NEW_GROUP = "__new__"
# less-used sections start folded
COLLAPSED_BY_DEFAULT = {"Frame", "Style", "Axes", "Reference lines", "Legend", "Data overlay"}
IMAGE_DATA_TITLE = "Overlay data (x, y columns)"


def _button(text: str, icon_name: str | None = None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icons.icon(icon_name))
    if tip:
        b.setToolTip(tip)
    return b


def _span(a: float, b: float | None = None) -> str:
    """A time or a time range in ps, written in the unit that suits it (ps, ns or µs)."""
    top = abs(b if b is not None else a)
    unit, f = ("µs", 1e-6) if top >= 1e6 else ("ns", 1e-3) if top >= 1e3 else ("ps", 1.0)
    fmt = lambda v: f"{v * f:.4g}"
    return f"{fmt(a)}–{fmt(b)} {unit}" if b is not None else f"{fmt(a)} {unit}"


def later(fn, *args) -> None:
    """Run fn once the current event is handled. Signals from item views and combo boxes must not open a
    dialog that rebuilds the inspector there and then: that deletes the view while it is still inside its
    own mouse handler, and Qt crashes on returning into it."""
    QTimer.singleShot(0, lambda: fn(*args))


class Inspector(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setMinimumWidth(340)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._page = None          # what the current form shows; the scroll position is kept per page
        self._series_list: QListWidget | None = None
        self._image_info: QLabel | None = None
        self._group_info: QLabel | None = None

    # --- helpers --------------------------------------------------------------------------
    def _set_prop(self, pid, name, value):
        def fn(p):
            p.panels[pid].props[name] = value
        self.win.apply(f"Change {name.replace('_', ' ')}", fn, merge=f"{pid}.{name}", structural=False)
        if name in ("folder", "pattern", "index_from", "number_regex", "t0", "dt"):
            self.refresh_status()
        panel = self.win.project.panels.get(pid)
        if panel is not None and any(name == cond[0] for p in panel.all_props() for cond in p.when):
            # other settings appear or disappear with this one; deferred: we are inside the editor's signal
            QTimer.singleShot(0, self.rebuild)

    def _set_attr(self, obj_getter, attr, value, label, structural=False):
        def fn(p):
            setattr(obj_getter(p), attr, value)
        self.win.apply(label, fn, merge=f"{label}.{attr}", structural=structural)
        self.refresh_status()

    # --- build ----------------------------------------------------------------------------
    def rebuild(self):
        sel = self.win.selection
        scroll = self.verticalScrollBar().value() if sel == self._page else 0
        self._page = sel
        self._series_list = self._image_info = self._group_info = None
        body = QWidget()
        body.setObjectName("inspectorBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(14, 6, 14, 14)
        lay.setSpacing(10)
        pr = self.win.project
        kind = sel[0] if sel else None
        if kind == "panel" and sel[1] in pr.panels:
            self._panel_page(lay, sel[1], None)
        elif kind == "overlay" and sel[1] < len(pr.overlays) and pr.overlays[sel[1]].panel in pr.panels:
            self._panel_page(lay, pr.overlays[sel[1]].panel, sel[1])
        elif kind == "group" and sel[1] in pr.groups:
            self._group_page(lay, sel[1])
        elif kind == "traj" and sel[1] in pr.trajectories:
            self._traj_page(lay, sel[1])
        elif kind == "cell":
            self._cell_page(lay, sel[1])
        else:
            self._project_page(lay)
        lay.addStretch(1)
        self.setWidget(body)
        if scroll:   # same selection as before: stay where the user was instead of jumping to the top
            body.adjustSize()
            self.verticalScrollBar().setValue(scroll)

    def refresh_status(self):
        """Update live status labels without rebuilding the form (keeps keyboard focus)."""
        pr = self.win.project
        sel = self.win.selection
        pid = sel[1] if sel and sel[0] == "panel" else (
            pr.overlays[sel[1]].panel if sel and sel[0] == "overlay" and sel[1] < len(pr.overlays) else None)
        panel = pr.panels.get(pid) if pid else None
        if self._series_list is not None and panel is not None:
            for i, s in enumerate(panel.series):
                item = self._series_list.item(i)
                if item is not None:
                    item.setText(self._series_text(s))
        if self._image_info is not None and panel is not None and panel.KIND == "image":
            self._image_info.setText(self._image_text(panel))
        if self._group_info is not None:
            gid = sel[1] if sel and sel[0] == "group" else (panel.group if panel else None)
            if gid in pr.groups:
                self._group_info.setText(self._group_text(gid))

    # --- project --------------------------------------------------------------------------
    def _project_page(self, lay):
        pr = self.win.project
        n = pr.n_frames
        lay.addWidget(HeaderCard("canvas", "Canvas",
                                 f"{pr.size[0]} × {pr.size[1]} px · {n} frames · {n / pr.fps:.2f} s"))
        out = Section("Output")
        preset = QComboBox()
        preset.addItem("Custom")
        preset.addItems(list(RESOLUTION_PRESETS))
        cur = next((k for k, v in RESOLUTION_PRESETS.items() if v == (pr.width, pr.height)), "Custom")
        preset.setCurrentText(cur)
        wsp, hsp = QSpinBox(), QSpinBox()
        for s, v in ((wsp, pr.width), (hsp, pr.height)):
            s.setRange(16, 8192)
            s.setSingleStep(2)
            s.setValue(v)
            s.setKeyboardTracking(False)

        def set_size(w, h):
            def fn(p):
                p.width, p.height = w, h
            self.win.apply("Change resolution", fn, merge="canvas.size", structural=False)

        preset.currentTextChanged.connect(
            lambda t: t in RESOLUTION_PRESETS and (wsp.setValue(RESOLUTION_PRESETS[t][0]),
                                                   hsp.setValue(RESOLUTION_PRESETS[t][1])))
        wsp.valueChanged.connect(lambda v: set_size(v, hsp.value()))
        hsp.valueChanged.connect(lambda v: set_size(wsp.value(), v))
        size_row = QWidget()
        sr = QHBoxLayout(size_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(wsp)
        sr.addWidget(QLabel("×"))
        sr.addWidget(hsp)
        out.add_row("Resolution", preset)
        out.add_row("Size (px)", size_row)

        def attr_editor(kind, attr, label, **kw):
            return make_editor(kind, getattr(pr, attr), lambda v: self.win.apply(
                f"Change {label.lower()}", lambda p: setattr(p, attr, v), merge=f"canvas.{attr}",
                structural=False), **kw)

        out.add_row("Frame rate (fps)", attr_editor("float", "fps", "Frame rate", minimum=1, maximum=240))
        length = attr_editor("int", "n_frames_override", "Movie length", minimum=0, maximum=10**7,
                             tooltip="auto = until the last sync group has finished")
        length.setSpecialValueText("auto")
        out.add_row("Movie length (frames)", length)
        lay.addWidget(out)

        look = Section("Look")
        look.add_row("Background", attr_editor("color", "background", "Background"))
        look.add_row("Gap between cells (px)", attr_editor("int", "gutter", "Gutter", minimum=0, maximum=500))
        lay.addWidget(look)
        lay.addWidget(note("Right-click the canvas to add panels, split cells or pick a layout template. "
                           "Drag the lines between cells to resize them."))

    # --- panel ----------------------------------------------------------------------------
    def _panel_page(self, lay, pid, overlay_index):
        pr = self.win.project
        panel = pr.panels[pid]
        subtitle = f"{panel.TITLE} panel" + (" · overlay" if overlay_index is not None else "")
        lay.addWidget(HeaderCard(icons.KIND_ICONS.get(panel.KIND, "canvas"), panel.name, subtitle))

        general = Section("General")
        name = QLineEdit(panel.name)
        name.editingFinished.connect(lambda: name.text().strip() and self._set_attr(
            lambda p: p.panels[pid], "name", name.text().strip(), "Rename panel"))
        general.add_row("Name", name)
        group = QComboBox()
        for g in pr.groups.values():
            group.addItem(icons.icon("group", "muted"), g.name, g.id)
        group.addItem(icons.icon("plus", "muted"), "New independent group…", NEW_GROUP)
        group.setCurrentIndex(max(0, group.findData(panel.group)))
        group.currentIndexChanged.connect(lambda i: later(self._change_group, pid, group.itemData(i)))
        general.add_row("Sync group", group)
        self._group_info = note(self._group_text(panel.group))
        general.add_widget(self._group_info)
        lay.addWidget(general)

        if overlay_index is not None:
            self._overlay_box(lay, overlay_index)
        if panel.KIND == "image":
            self._image_box(lay, pid)
        if panel.KIND == "molecule":
            self._molecule_box(lay, pid)
        if panel.HAS_SERIES:
            self._series_box(lay, pid)

        sections: dict[str, Section] = {}
        for prop in panel.all_props():
            if not prop.shown(panel.props) or (panel.KIND == "image" and prop.name == "folder"):
                continue
            title = prop.section or "Properties"
            if title not in sections:
                sections[title] = Section(title, collapsed=title in COLLAPSED_BY_DEFAULT)
                lay.addWidget(sections[title])
            ed = make_editor(prop.kind, panel.props[prop.name],
                             lambda v, n=prop.name: self._set_prop(pid, n, v), options=prop.options,
                             minimum=prop.minimum, maximum=prop.maximum, step=prop.step)
            sections[title].add_row(prop.text, ed)
            if panel.KIND == "text" and prop.name == "text":
                sections[title].add_widget(note(FIELDS_HELP))

    def _change_group(self, pid, gid):
        pr = self.win.project
        if gid == NEW_GROUP:
            name = pr.panels[pid].name

            def fn(p):
                p.panels[pid].group = p.add_group(f"{name} time").id
            self.win.apply("New sync group", fn)
            return
        self.win.apply("Change sync group", lambda p: setattr(p.panels[pid], "group", gid))

    def _group_text(self, gid) -> str:
        pr = self.win.project
        g = pr.groups[gid]
        m = pr.group_map(gid)
        auto = pr.group_auto(gid)
        others = [p.name for p in pr.group_members(gid)]
        return (f"Plays movie frames {m.start}–{m.end - 1}, showing t = {m.lo:g}–{m.hi:g} ps at {m.speed:g} ps "
                f"per frame{' (auto)' if not g.speed else ''}. In sync with: {', '.join(others)}."
                + ("" if auto else " No timed data yet."))

    def _overlay_box(self, lay, i):
        ov = self.win.project.overlays[i]
        box = Section("Overlay position (% of canvas)")
        for attr, label in (("x", "Left"), ("y", "Top"), ("w", "Width"), ("h", "Height")):
            s = QDoubleSpinBox()
            s.setRange(-100, 200)
            s.setDecimals(1)
            s.setSuffix(" %")
            s.setValue(getattr(ov, attr) * 100)
            s.setKeyboardTracking(False)
            s.valueChanged.connect(lambda v, a=attr: self._set_attr(lambda p: p.overlays[i], a, v / 100,
                                                                    "Move overlay"))
            box.add_row(label, s)
        lay.addWidget(box)

    # --- image-specific -------------------------------------------------------------------
    def _image_text(self, panel) -> str:
        seq = panel.sequence(self.win.project)
        if not len(seq):
            return "No images found. Choose a folder and check the file pattern."
        a, b, dt = seq.time_info()
        crop = panel.props["crop"]
        return (f"{len(seq)} images · t = {a:g}–{b:g} ps · every {dt:g} ps"
                + (f" · cropped to {crop[2] * 100:.0f}% × {crop[3] * 100:.0f}%" if crop else "")
                + (f"\n⚠ {seq.warning}" if seq.warning else ""))

    def _set_folder(self, pid, folder):
        if folder != self.win.project.panels[pid].props["folder"]:
            # deferred: the pattern dialog and the rebuild it triggers must not run inside the editor's signal
            QTimer.singleShot(0, lambda: pid in self.win.project.panels and self.win.set_image_folder(pid, folder))

    def _image_box(self, lay, pid):
        panel = self.win.project.panels[pid]
        box = Section("Images")
        box.add_row("Source", make_editor("dir", panel.props["folder"],
                                          lambda val: self._set_folder(pid, val)))
        self._image_info = note(self._image_text(panel))
        box.add_widget(self._image_info)
        crop = _button("Crop…", "crop")
        crop.clicked.connect(lambda: self.win.crop_panel(pid))
        reset = _button("Reset crop")
        reset.clicked.connect(lambda: self._set_prop(pid, "crop", None))
        match = _button("Match trajectory…", "trajectory",
                        "Set image timing from a loaded trajectory (frame offset and stride)")
        match.clicked.connect(lambda: self.win.match_timing(pid))
        calib = _button("Calibrate data overlay…", "cvmap",
                        "Mark the plot area of a pre-rendered picture (e.g. a FES) so data can be drawn on it")
        calib.clicked.connect(lambda: self.win.calibrate_overlay(pid))
        box.add_widget(button_row(crop, reset))
        box.add_widget(button_row(match, calib))
        lay.addWidget(box)

    # --- molecule-specific ----------------------------------------------------------------
    def _molecule_box(self, lay, pid):
        pr = self.win.project
        panel = pr.panels[pid]
        box = Section("Molecule")
        traj = QComboBox()
        for t in pr.trajectories.values():
            traj.addItem(icons.icon("trajectory", "muted"), t.name, t.id)
        traj.addItem(icons.icon("plus", "muted"), "Load trajectory…", NEW_GROUP)
        traj.setCurrentIndex(traj.findData(panel.props["traj"]))
        if panel.trajectory(pr) is None:
            traj.setPlaceholderText("none: choose or load one")
            traj.setCurrentIndex(-1)
        traj.activated.connect(lambda i: later(self.win.set_molecule_trajectory, pid, traj.itemData(i)))
        box.add_row("Trajectory", traj)

        lst = QListWidget()
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lst.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        reps = panel.props["reps"]
        try:      # say which representations select nothing in this trajectory (e.g. the cartoon of a non-protein)
            drawn = [len(r.index) for r in panel.scene(pr).reps]
        except Exception:
            drawn = [1] * len(reps)
        for r, n in zip(reps, drawn):
            text = f"{r.get('style')}  ·  {r.get('sel')}  ·  " + (r.get('color') if n else "nothing to draw")
            QListWidgetItem(icons.icon("molecule", "muted"), text, lst).setToolTip(text)
        lst.itemDoubleClicked.connect(lambda item: later(self.win.edit_rep, pid, lst.row(item)))
        if reps:
            lst.setCurrentRow(0)
            lst.setFixedHeight(min(len(reps), 5) * lst.sizeHintForRow(0) + 12)
            box.add_widget(lst)
        else:
            lst.hide()
            lst.setParent(box)
            empty = QLabel("Nothing is drawn yet. Add a representation.")
            empty.setObjectName("emptyHint")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.add_widget(empty)
        add = _button("Add", "plus", "Add a representation: a selection of atoms and how to draw it")
        add.setObjectName("primary")
        add.clicked.connect(lambda: self.win.edit_rep(pid, None))
        edit = _button("Edit…")
        edit.clicked.connect(lambda: lst.currentRow() >= 0 and self.win.edit_rep(pid, lst.currentRow()))
        rem = _button("", "trash", "Remove the selected representation")
        rem.clicked.connect(lambda: lst.currentRow() >= 0 and self.win.remove_rep(pid, lst.currentRow()))
        for b in (edit, rem):
            b.setEnabled(bool(reps))
        box.add_widget(button_row(add, edit, rem))

        views = []
        for name in ("front", "side", "top"):
            b = _button(name.capitalize(), tip=f"Look at the molecule from the {name}")
            b.clicked.connect(lambda _=False, n=name: self.win.set_molecule_view(pid, n))
            views.append(b)
        reset = _button("Reset view", tip="Front view, centred, zoomed to fit")
        reset.clicked.connect(lambda: self.win.set_molecule_view(pid, None))
        box.add_widget(button_row(*views, reset))
        box.add_widget(note("With this panel selected: drag in the preview to rotate, Shift-drag to move, "
                            "scroll to zoom."))
        lay.addWidget(box)

    # --- series-specific ------------------------------------------------------------------
    def _series_text(self, s) -> str:
        pr = self.win.project
        key = runner.series_key(pr, s)
        traj = pr.trajectories.get(s.traj)
        if runner.cached(key) is not None:
            status = "✓"
        elif runner.error(key):
            status = f"⚠ {runner.error(key)}"
        elif key is None:
            status = "⚠ missing trajectory"
        else:
            status = "⏳ computing…" if self.win.analysis.busy else "… pending"
        source = traj.name if traj else ("data file" if not s.traj else "?")
        return f"{s.label or s.preset}  ·  {source}   {status}"

    def _series_box(self, lay, pid):
        from mdmovie.panels.series_data import column_names
        panel = self.win.project.panels[pid]
        xy = panel.KIND in ("image", "cvmap")
        # most image panels are plain protein frames: their optional data overlay stays folded until used
        box = Section(IMAGE_DATA_TITLE, collapsed=not panel.series) if panel.KIND == "image" else Section(
            "Data: x and y columns" if xy else "Data series (analysis presets)")
        lst = QListWidget()
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lst.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        for s in panel.series:
            item = QListWidgetItem(icons.icon(icons.KIND_ICONS.get(panel.KIND, "plot"), "muted"),
                                   self._series_text(s), lst)
            item.setToolTip(item.text())
        lst.itemDoubleClicked.connect(lambda item: later(self.win.edit_series, pid, lst.row(item)))
        self._series_list = lst
        if panel.series:    # as tall as its rows (up to five), not a fixed box that is mostly empty
            lst.setCurrentRow(0)
            lst.setFixedHeight(min(len(panel.series), 5) * lst.sizeHintForRow(0) + 12)
            box.add_widget(lst)
        else:
            lst.hide()
            lst.setParent(box)
            empty = QLabel("No data yet. Add a series to draw something here.")
            empty.setObjectName("emptyHint")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.add_widget(empty)
        add = _button("Add", "plus")
        add.setObjectName("primary")
        add.clicked.connect(lambda: self.win.edit_series(pid, None))
        edit = _button("Edit…")
        edit.clicked.connect(lambda: lst.currentRow() >= 0 and self.win.edit_series(pid, lst.currentRow()))
        rem = _button("", "trash", "Remove the selected series")
        rem.clicked.connect(lambda: lst.currentRow() >= 0 and self.win.remove_series(pid, lst.currentRow()))
        re = _button("Recompute", tip="Discard the cached result and run the analysis again")
        re.clicked.connect(lambda: lst.currentRow() >= 0 and self.win.recompute_series(pid, lst.currentRow()))
        for b in (edit, re, rem):
            b.setEnabled(bool(panel.series))
        box.add_widget(button_row(add, edit, re, rem))
        if xy:
            names = column_names(self.win.project, panel.series)
            if panel.KIND == "cvmap" and len(names) == 1:
                using = "One column, so it is plotted against time (x = time, y = #0)."
            else:
                using = f"Using x = #{panel.props['x_col']}, y = #{panel.props['y_col']}."
            box.add_widget(note(
                "Add the two variables as series (or one series with two columns, e.g. a COLVAR)"
                + (", or a single series to plot it against time" if panel.KIND == "cvmap" else "") + ". "
                "Columns available: " + (", ".join(names) if names else "none yet") + ". " + using))
        lay.addWidget(box)

    # --- sync group -----------------------------------------------------------------------
    def _group_page(self, lay, gid):
        pr = self.win.project
        g = pr.groups[gid]
        lay.addWidget(HeaderCard("group", g.name, "Sync group · panels in it always show the same time"))
        grp = lambda p: p.groups[gid]  # noqa: E731
        general = Section("General")
        name = QLineEdit(g.name)
        name.editingFinished.connect(lambda: self._set_attr(grp, "name", name.text().strip() or g.name,
                                                            "Rename group", structural=False))
        general.add_row("Name", name)
        color = ColorButton(g.color)
        color.changed.connect(lambda c: self._set_attr(grp, "color", c, "Group colour"))
        general.add_row("Timeline colour", color)
        lay.addWidget(general)

        timing = Section("Timing")
        timing.add_row("Starts at movie frame", make_editor(
            "int", g.start, lambda v: self._set_attr(grp, "start", v, "Group start"), minimum=-10**6,
            maximum=10**7))
        speed = make_editor("float", g.speed, lambda v: self._set_attr(grp, "speed", v, "Group speed"),
                            minimum=0, maximum=1e12, tooltip="auto = one data frame per movie frame")
        speed.setSpecialValueText("auto")
        timing.add_row("Speed (ps / movie frame)", speed)
        s0 = OptFloatEdit(g.src_start)
        s0.changed.connect(lambda v: self._set_attr(grp, "src_start", v, "Group source start"))
        s1 = OptFloatEdit(g.src_end)
        s1.changed.connect(lambda v: self._set_attr(grp, "src_end", v, "Group source end"))
        timing.add_row("Play from time (ps)", s0)
        timing.add_row("Play until time (ps)", s1)
        for attr, label in (("before", "Before start"), ("after", "After end")):
            timing.add_row(label, make_editor("choice", getattr(g, attr),
                                              lambda v, a=attr: self._set_attr(grp, a, v, f"Group {a}"),
                                              options=EDGE_MODES))
        self._group_info = note(self._group_text(gid))
        timing.add_widget(self._group_info)
        lay.addWidget(timing)
        lay.addWidget(note("hold = keep showing the first/last frame · hide = blank panel · loop = repeat. "
                           "Drag the group's bar in the timeline to shift it."))
        rm = _button("Delete group", "trash", "Members move to the first group")
        rm.setEnabled(len(pr.groups) > 1)
        rm.clicked.connect(lambda: (self.win.apply("Delete group", lambda p: p.remove_group(gid)),
                                    self.win.select(None)))
        lay.addWidget(button_row(rm))

    # --- trajectory -----------------------------------------------------------------------
    def _traj_page(self, lay, tid):
        pr = self.win.project
        t = pr.trajectories[tid]
        m = None
        try:
            m = t.meta(pr.resolve_path)
            info = f"{m.n_atoms:,} atoms · {m.n_frames:,} frames · " + (
                f"{_span(m.t0, m.t0 + (m.n_frames - 1) * m.dt)}, every {_span(m.dt)}" if m.timed else
                "no frame times in the file: 1 frame is counted as 1 ps")
        except Exception as e:
            info = f"⚠ cannot load: {e}"
        lay.addWidget(HeaderCard("trajectory", t.name, info))
        if m is not None:
            try:
                rows = t.composition(pr.resolve_path)
            except Exception:
                rows = []
            if rows:
                comp = Section("System")
                for kind, text in rows:
                    comp.add_row(kind.capitalize(), note(text))
                lay.addWidget(comp)
        reading = Section("Reading")
        whole = QCheckBox("Make molecules whole")
        whole.setChecked(t.whole)
        whole.setToolTip("Join molecules split across the periodic box, as 'gmx trjconv -pbc mol -center' does, "
                         "for drawing and analysis. Water and ions are left as stored.")
        whole.toggled.connect(lambda on: later(self._set_attr, lambda p: p.trajectories[tid], "whole", on,
                                               "Periodic boundaries"))
        reading.add_row("Periodic boundaries", whole)
        lay.addWidget(reading)
        sec = Section("Files")
        name = QLineEdit(t.name)
        name.editingFinished.connect(lambda: self._set_attr(lambda p: p.trajectories[tid], "name",
                                                            name.text().strip() or t.name, "Rename trajectory"))
        sec.add_row("Name", name)
        sec.add_row("Topology", note(t.topology))
        sec.add_row("Trajectory", note("\n".join(t.trajectories) or "(coordinates from topology)"))
        lay.addWidget(sec)
        used = [p.name for p in pr.panels.values() for s in p.series if s.traj == tid]
        used += [p.name for p in pr.panels.values() if p.props.get("traj") == tid]
        lay.addWidget(note("Used by: " + (", ".join(sorted(set(used))) or "nothing yet")))
        change = _button("Change files…", "open")
        change.clicked.connect(lambda: self.win.edit_trajectory(tid))
        rm = _button("Remove", "trash")
        rm.clicked.connect(lambda: self.win.remove_trajectory(tid))
        lay.addWidget(button_row(change, rm))

    # --- empty cell -----------------------------------------------------------------------
    def _cell_page(self, lay, path):
        pr = self.win.project
        lay.addWidget(HeaderCard("layout", "Empty cell", "Put a new panel here, or show an existing one"))
        sec = Section("Add a panel")
        for kind, cls in PANEL_TYPES.items():
            b = _button(f"New {cls.TITLE.lower()} panel", icons.KIND_ICONS[kind])
            b.setStyleSheet("text-align: left; padding: 7px 12px;")
            b.clicked.connect(lambda _=False, k=kind: self.win.new_panel_in_cell(k, path))
            sec.add_widget(b)
        if pr.panels:
            combo = QComboBox()
            combo.addItem("Show existing panel…", None)
            for pid, p in pr.panels.items():
                combo.addItem(icons.icon(icons.KIND_ICONS.get(p.KIND, "canvas"), "muted"), p.name, pid)
            combo.currentIndexChanged.connect(lambda i: combo.itemData(i) and later(self.win.assign_panel,
                                                                               path, combo.itemData(i)))
            sec.add_widget(combo)
        lay.addWidget(sec)
        rm = _button("Remove cell", "trash")
        rm.clicked.connect(lambda: self.win.remove_cell(path))
        lay.addWidget(button_row(rm))
