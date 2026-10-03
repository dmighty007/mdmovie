"""Interactive preview of the movie canvas.

Shows the composited frame (rendered at display size by the same compositor used for
export) and lets you edit the layout: select cells, drag dividers, split/merge cells,
assign panels and move/resize overlays.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from mdmovie.core import layout as L
from mdmovie.panels import PANEL_TYPES
from mdmovie.render.compositor import cell_rects, overlay_rect, render_frame

DIVIDER_GRAB = 6
HANDLE = 8


class WelcomeCard(QFrame):
    """Shown on the canvas while the project has no panels."""

    def __init__(self, win, parent):
        super().__init__(parent)
        from mdmovie.ui import icons
        self.setObjectName("welcome")
        self.setFixedWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 24)
        lay.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(icons.pixmap("film", icons._colors["accent"], 34))
        title = QLabel("Start a new movie")
        title.setObjectName("welcomeTitle")
        sub = QLabel("Arrange a molecule (rendered frames, or drawn here from the trajectory) next to live "
                     "analysis plots, then export an MP4.")
        sub.setObjectName("note")
        sub.setWordWrap(True)
        lay.addWidget(logo)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(8)
        for icon_name, text, cb in (
                ("molecule", "Start from a trajectory…", lambda: win.start_from_trajectory()),
                ("image", "Start from rendered images", lambda: win.new_panel("image")),
                ("sparkle", "Open the demo project", win.open_demo),
                ("open", "Open a saved project…", lambda: win.open_project())):
            b = QPushButton(icons.icon(icon_name, "accent" if icon_name == "molecule" else "normal"), "  " + text)
            b.setObjectName("welcomeButton")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(cb)
            lay.addWidget(b)
        tip = QLabel("A trajectory gives the molecule with RMSD and Rg plots and a time label, ready to "
                     "change. Or drop topology + trajectory files here.")
        tip.setWordWrap(True)
        tip.setObjectName("note")
        lay.addSpacing(6)
        lay.addWidget(tip)
        self.adjustSize()


class CanvasView(QWidget):
    selection_changed = Signal()

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setMinimumSize(320, 200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._image: QImage | None = None
        self._pending = False
        self._drag = None
        self._hover_div = None
        self.show_guides = True
        self.last_render_ms = 0.0
        self.welcome = WelcomeCard(win, self)
        self.welcome.hide()

    def update_welcome(self):
        show = not self.win.project.panels
        self.welcome.setVisible(show)
        if show:
            self.welcome.adjustSize()
            self.welcome.move((self.width() - self.welcome.width()) // 2,
                              (self.height() - self.welcome.height()) // 2)
            self.welcome.raise_()

    # --- rendering ------------------------------------------------------------------------
    def request_render(self):
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._render)

    def render_now(self):
        self._pending = True
        self._render()

    def _render(self):
        if not self._pending:
            return
        self._pending = False
        import time
        t0 = time.perf_counter()
        pr = self.win.project
        d = self.display_rect()
        dpr = self.devicePixelRatioF()
        scale = min(1.0, d.width() * dpr / pr.size[0])
        self._image = render_frame(pr, self.win.frame, scale)
        self.last_render_ms = (time.perf_counter() - t0) * 1000
        self.update()

    def resizeEvent(self, e):
        self.update_welcome()
        self.request_render()
        if hasattr(self.win, "status_info"):
            self.win.update_status()

    # --- geometry -------------------------------------------------------------------------
    def display_rect(self) -> QRectF:
        pw, ph = self.win.project.size
        m = 12
        s = min((self.width() - 2 * m) / pw, (self.height() - 2 * m) / ph)
        s = max(s, 0.01)
        return QRectF((self.width() - pw * s) / 2, (self.height() - ph * s) / 2, pw * s, ph * s)

    def _to_widget(self, r: QRectF) -> QRectF:
        d = self.display_rect()
        pw, ph = self.win.project.size
        sx, sy = d.width() / pw, d.height() / ph
        return QRectF(d.x() + r.x() * sx, d.y() + r.y() * sy, r.width() * sx, r.height() * sy)

    def _to_canvas(self, p: QPointF) -> QPointF:
        d = self.display_rect()
        pw, ph = self.win.project.size
        return QPointF((p.x() - d.x()) * pw / d.width(), (p.y() - d.y()) * ph / d.height())

    def cells(self):
        pw, ph = self.win.project.size
        return [(path, pid, self._to_widget(r)) for path, pid, r in cell_rects(self.win.project, pw, ph, 1.0)]

    def overlays(self):
        pw, ph = self.win.project.size
        return [(i, ov, self._to_widget(overlay_rect(ov, pw, ph))) for i, ov in enumerate(self.win.project.overlays)]

    def _layout_to_widget(self, pos: float, horizontal: bool) -> float:
        """Normalized layout coordinate → widget coordinate (accounts for the outer gutter)."""
        pr = self.win.project
        pw, ph = pr.size
        g = pr.gutter
        d = self.display_rect()
        if horizontal:
            return d.x() + (g / 2 + pos * (pw - g)) * d.width() / pw
        return d.y() + (g / 2 + pos * (ph - g)) * d.height() / ph

    def _widget_to_layout(self, v: float, horizontal: bool) -> float:
        pr = self.win.project
        pw, ph = pr.size
        g = pr.gutter
        d = self.display_rect()
        if horizontal:
            return (((v - d.x()) * pw / d.width()) - g / 2) / (pw - g)
        return (((v - d.y()) * ph / d.height()) - g / 2) / (ph - g)

    def dividers(self):
        """[(split_path, index, split_rect, orient, widget line QRectF)]"""
        out = []
        for path, i, srect, pos in L.iter_dividers(self.win.project.layout):
            split = L.get(self.win.project.layout, path)
            x, y, w, h = srect
            if split.orient == "h":
                xw = self._layout_to_widget(pos, True)
                y0, y1 = self._layout_to_widget(y, False), self._layout_to_widget(y + h, False)
                line = QRectF(xw - DIVIDER_GRAB, y0, 2 * DIVIDER_GRAB, y1 - y0)
            else:
                yw = self._layout_to_widget(pos, False)
                x0, x1 = self._layout_to_widget(x, True), self._layout_to_widget(x + w, True)
                line = QRectF(x0, yw - DIVIDER_GRAB, x1 - x0, 2 * DIVIDER_GRAB)
            out.append((path, i, srect, split.orient, line))
        return out

    # --- hit testing ----------------------------------------------------------------------
    def _hit(self, p: QPointF):
        sel = self.win.selection
        for i, ov, r in reversed(self.overlays()):
            if sel == ("overlay", i):
                br = QRectF(r.right() - HANDLE, r.bottom() - HANDLE, 2 * HANDLE, 2 * HANDLE)
                if br.contains(p):
                    return ("overlay-resize", i, r)
            if r.contains(p):
                return ("overlay", i, r)
        for d in self.dividers():
            if d[4].contains(p):
                return ("divider", d)
        for path, pid, r in self.cells():
            if r.contains(p):
                return ("cell", path, pid)
        return None

    # --- painting -------------------------------------------------------------------------
    def paintEvent(self, _):
        from mdmovie.ui import icons
        from mdmovie.ui.theme import C
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(C.stage))
        d = self.display_rect()
        for i in range(1, 9):  # soft drop shadow under the frame
            shadow = QColor(0, 0, 0, max(0, 26 - 3 * i))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(shadow)
            p.drawRoundedRect(d.adjusted(-i, -i + 3, i, i + 3), i, i)
        if self._image is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(d, self._image)
        if not self.show_guides:
            return
        accent = QColor(C.accent)
        sel = self.win.selection
        f = QFont(self.font())
        p.setFont(f)
        plus = icons.pixmap("plus", "#8a94a3", 22)
        for path, pid, r in self.cells():
            if pid is None or pid not in self.win.project.panels:
                wash = QColor(C.accent)
                wash.setAlpha(14)
                p.fillRect(r, wash)
                p.setPen(QPen(QColor(138, 148, 163, 170), 1.2, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 6, 6)
                if r.height() > 70 and r.width() > 140 and not self.welcome.isVisible():
                    c = r.center()
                    p.drawPixmap(int(c.x() - 11), int(c.y() - 30), plus)
                    p.setPen(QColor("#6b7482"))
                    p.drawText(QRectF(r.x(), c.y() - 4, r.width(), 40), int(Qt.AlignmentFlag.AlignHCenter),
                               "Empty cell\nright-click or use the inspector to add a panel")
            if sel == ("cell", path) or (pid is not None and sel == ("panel", pid)):
                p.setPen(QPen(accent, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
        for i, ov, r in self.overlays():
            selected = sel == ("overlay", i) or sel == ("panel", ov.panel)
            p.setPen(QPen(accent if selected else QColor(128, 128, 128, 150), 1.5 if selected else 1,
                          Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            if selected:
                p.setPen(QPen(QColor("white"), 1.5))
                p.setBrush(accent)
                p.drawEllipse(QRectF(r.right() - HANDLE / 2 - 1, r.bottom() - HANDLE / 2 - 1, HANDLE + 2, HANDLE + 2))
        if self._hover_div is not None:
            hv = QColor(C.accent)
            hv.setAlpha(120)
            p.fillRect(self._hover_div.adjusted(DIVIDER_GRAB - 2, 0, -(DIVIDER_GRAB - 2), 0)
                       if self._hover_div.width() < self._hover_div.height()
                       else self._hover_div.adjusted(0, DIVIDER_GRAB - 2, 0, -(DIVIDER_GRAB - 2)), hv)

    # --- mouse ----------------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit(e.position())
        pr = self.win.project
        if hit is None:
            self.win.select(None)
            return
        if hit[0] == "divider":
            self._drag = ("divider", hit[1], pr.to_dict())
        elif hit[0] in ("overlay", "overlay-resize"):
            i = hit[1]
            self.win.select(("overlay", i))
            ov = pr.overlays[i]
            self._drag = (hit[0], i, pr.to_dict(), self._to_canvas(e.position()), (ov.x, ov.y, ov.w, ov.h))
        elif hit[0] == "cell":
            _, path, pid = hit
            if self._is_molecule(pid) and self.win.selection == ("panel", pid):
                # already selected: dragging turns the molecule (Shift: moves it)
                props = pr.panels[pid].props
                shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._drag = ("mol-pan" if shift else "mol-rotate", pid, pr.to_dict(), e.position(),
                              (list(props["rotation"]), list(props["pan"])))
                return
            self.win.select(("panel", pid) if pid in pr.panels else ("cell", path))

    def mouseMoveEvent(self, e):
        pos = e.position()
        pr = self.win.project
        if self._drag is None:
            hit = self._hit(pos)
            self._hover_div = None
            if hit and hit[0] == "divider":
                self._hover_div = hit[1][4]
                self.setCursor(QCursor(Qt.CursorShape.SplitHCursor if hit[1][3] == "h"
                                       else Qt.CursorShape.SplitVCursor))
            elif hit and hit[0] == "overlay-resize":
                self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
            elif hit and hit[0] == "overlay":
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            elif hit and hit[0] == "cell" and self._is_molecule(hit[2]) and self.win.selection == ("panel", hit[2]):
                self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            else:
                self.unsetCursor()
            self.update()
            return
        kind = self._drag[0]
        if kind in ("mol-rotate", "mol-pan"):
            _, pid, _, start, (rotation, pan) = self._drag
            panel = pr.panels.get(pid)
            if panel is None:
                return
            dx, dy = pos.x() - start.x(), pos.y() - start.y()
            if kind == "mol-rotate":
                from mdmovie.mol.render import rotated
                panel.props["rotation"] = rotated(rotation, dx * 0.01, dy * 0.01)
            else:
                per_px = self._molecule_angstrom_per_px(pid)
                panel.props["pan"] = [round(pan[0] + dx * per_px, 4), round(pan[1] - dy * per_px, 4)]
        elif kind == "divider":
            path, i, srect, orient, _ = self._drag[1]
            v = self._widget_to_layout(pos.x() if orient == "h" else pos.y(), orient == "h")
            L.set_divider(pr.layout, path, i, v, srect)
        else:
            _, i, _, start, (x, y, w, h) = self._drag
            c = self._to_canvas(pos)
            pw, ph = pr.size
            dx, dy = (c.x() - start.x()) / pw, (c.y() - start.y()) / ph
            ov = pr.overlays[i]
            if kind == "overlay":
                ov.x = min(max(x + dx, -w + 0.02), 0.98)
                ov.y = min(max(y + dy, -h + 0.02), 0.98)
            else:
                ov.w = max(0.02, w + dx)
                ov.h = max(0.02, h + dy)
        self.request_render()

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        kind, before = self._drag[0], self._drag[2]
        self._drag = None
        after = self.win.project.to_dict()
        if after != before:
            names = {"mol-rotate": "Rotate view", "mol-pan": "Move view", "divider": "Move divider"}
            self.win.push_snapshot(names.get(kind, "Move overlay"), before, after)

    def mouseDoubleClickEvent(self, e):
        hit = self._hit(e.position())
        pid = None
        if hit and hit[0] == "cell":
            pid = hit[2]
        elif hit and hit[0] in ("overlay", "overlay-resize"):
            pid = self.win.project.overlays[hit[1]].panel
        panel = self.win.project.panels.get(pid) if pid else None
        if panel is not None and panel.KIND == "image":
            self.win.crop_panel(pid)
        elif panel is not None and panel.KIND == "molecule" and panel.trajectory(self.win.project) is None:
            self.win.set_molecule_trajectory(pid, "__new__")

    # --- molecule panels ------------------------------------------------------------------
    def _is_molecule(self, pid) -> bool:
        panel = self.win.project.panels.get(pid) if pid else None
        return panel is not None and panel.KIND == "molecule"

    def _molecule_angstrom_per_px(self, pid) -> float:
        """How far one preview pixel is in the molecule (for Shift-drag)."""
        pr = self.win.project
        panel = pr.panels[pid]
        rect = next((r for _, p, r in self.cells() if p == pid), None)
        try:
            radius = panel.scene(pr).radius
        except Exception:
            return 0.1
        size = min(rect.width(), rect.height()) if rect is not None else 300
        return radius / (max(panel.props["zoom"], 1e-3) * 0.46 * max(size, 1))

    def wheelEvent(self, e):
        hit = self._hit(e.position())
        if not (hit and hit[0] == "cell" and self._is_molecule(hit[2]) and self.win.selection == ("panel", hit[2])):
            return super().wheelEvent(e)
        pid = hit[2]
        factor = 1.0015 ** e.angleDelta().y()
        self.win.apply("Zoom view", lambda p: p.panels[pid].props.__setitem__(
            "zoom", round(min(max(p.panels[pid].props["zoom"] * factor, 0.05), 50.0), 4)),
            merge=f"{pid}.zoom", structural=False)
        e.accept()

    def leaveEvent(self, e):
        self._hover_div = None
        self.update()

    # --- context menu ---------------------------------------------------------------------
    def contextMenuEvent(self, e):
        pos = QPointF(e.pos())
        hit = self._hit(pos)
        win, pr = self.win, self.win.project
        menu = QMenu(self)
        if hit and hit[0] in ("overlay", "overlay-resize"):
            i = hit[1]
            pid = pr.overlays[i].panel
            win.select(("overlay", i))
            if pr.panels.get(pid) and pr.panels[pid].KIND == "image":
                menu.addAction("Crop…", lambda: win.crop_panel(pid))
            menu.addAction("Bring to front", lambda: win.apply("Bring overlay to front", lambda p: p.overlays.append(
                p.overlays.pop(i))))
            menu.addAction("Remove overlay", lambda: win.apply("Remove overlay", lambda p: p.overlays.pop(i)))
            menu.addAction("Remove overlay and delete its panel", lambda: win.delete_panel(pid))
            menu.exec(e.globalPos())
            return
        if hit and hit[0] == "cell":
            _, path, pid = hit
            panel = pr.panels.get(pid) if pid else None
            win.select(("panel", pid) if panel else ("cell", path))
            new = menu.addMenu("New panel here")
            for kind, cls in PANEL_TYPES.items():
                new.addAction(cls.TITLE, lambda k=kind, pa=path: win.new_panel_in_cell(k, pa))
            assign = menu.addMenu("Show existing panel")
            for opid, op in pr.panels.items():
                a = assign.addAction(op.name, lambda o=opid, pa=path: win.assign_panel(pa, o))
                a.setCheckable(True)
                a.setChecked(opid == pid)
            assign.setEnabled(bool(pr.panels))
            menu.addSeparator()
            split = menu.addMenu("Split cell")
            split.addAction("Add cell to the right", lambda: win.split_cell(path, "h", True))
            split.addAction("Add cell to the left", lambda: win.split_cell(path, "h", False))
            split.addAction("Add cell below", lambda: win.split_cell(path, "v", True))
            split.addAction("Add cell above", lambda: win.split_cell(path, "v", False))
            others = [(op, opid) for op, opid, _ in self.cells() if op != path]
            if others:
                swap = menu.addMenu("Swap with")
                for op, opid in others:
                    name = pr.panels[opid].name if opid in pr.panels else "empty cell"
                    swap.addAction(f"{name}", lambda a=path, b=op: win.swap_cells(a, b))
            if panel is not None:
                menu.addAction("Clear cell (keep panel)", lambda: win.assign_panel(path, None))
            menu.addAction("Remove cell", lambda: win.remove_cell(path))
            if panel is not None:
                menu.addSeparator()
                if panel.KIND == "image":
                    menu.addAction("Crop…", lambda: win.crop_panel(pid))
                menu.addAction("Delete panel", lambda: win.delete_panel(pid))
        menu.addSeparator()
        c = self._to_canvas(pos)
        pw, ph = pr.size
        ov_menu = menu.addMenu("Add overlay here")
        for kind, cls in PANEL_TYPES.items():
            ov_menu.addAction(cls.TITLE, lambda k=kind: win.new_overlay(k, c.x() / pw, c.y() / ph))
        tmpl = menu.addMenu("Layout template")
        for name in L.TEMPLATES:
            tmpl.addAction(name, lambda n=name: win.apply_template(n))
        guides = QAction("Show layout guides", menu, checkable=True, checked=self.show_guides)
        guides.toggled.connect(self._set_guides)
        menu.addAction(guides)
        menu.exec(e.globalPos())

    def _set_guides(self, on):
        self.show_guides = on
        self.update()
