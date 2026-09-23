"""Heatmap panel: a Matrix result (rows × time, e.g. DSSP per residue) with a moving cursor."""
from __future__ import annotations

import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch, Rectangle
from PySide6.QtCore import QRectF

from mdmovie.panels.base import Prop, RenderContext, draw_message
from mdmovie.panels.mpl_common import (STYLE_PROPS, SeriesPanel, canvas_to_qimage, fg_color, freeze_layout,
                                       make_figure, pick_time_unit, style_axes)

CATEGORY_COLORS = ["#e8e8e8", "#d62728", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#17becf"]


class HeatmapPanel(SeriesPanel):
    KIND = "heatmap"
    TITLE = "Heatmap"
    SERIES_KINDS = ("matrix",)
    PROPS = [
        Prop("mode", "choice", "reveal", "Animation", ("reveal", "cursor", "static"), section="Animation"),
        Prop("cursor_color", "color", "#000000", "Cursor colour", section="Animation"),
        *STYLE_PROPS,
        Prop("ylabel", "str", "", "Y label (blank = auto)", section="Axes"),
        Prop("colormap", "str", "viridis", "Colormap (numeric data)", section="Style"),
        Prop("category_colors", "str", "", "Category colours (comma-separated, blank = default)", section="Style"),
        Prop("colorbar", "bool", True, "Colour bar / legend", section="Style"),
    ]

    def render(self, painter, rect: QRectF, ctx: RenderContext) -> None:
        mats = [(s, r) for s, r, _ in self.results(ctx.project) if r is not None and r.kind == "matrix"]
        if not self.series:
            draw_message(painter, rect, "No data\n(add a matrix series, e.g. DSSP)", ctx.scale)
            return
        if not mats:
            msgs = [m for _, r, m in self.results(ctx.project) if r is None] or ["Series is not a matrix"]
            draw_message(painter, rect, "\n".join(msgs), ctx.scale)
            return
        w, h = max(1, round(rect.width())), max(1, round(rect.height()))
        painter.drawImage(rect.topLeft(), self.draw_frame(w, h, ctx.scale, ctx.project, ctx.time))

    def build_renderer(self, w, h, scale, project):
        return _HeatmapRenderer(self, w, h, scale, self.results(project))


class _HeatmapRenderer:
    def __init__(self, panel, w, h, scale, results):
        p = self.p = panel.props
        fg = fg_color(p)
        s, res = next((s, r) for s, r, _ in results if r is not None and r.kind == "matrix")
        self.fig, self.canvas = make_figure(w, h, scale, p)
        ax = self.ax = self.fig.add_subplot(111)
        style_axes(ax, {**p, "grid": "off"})
        t = np.asarray(res.time, float)
        self.unit, self.tf = pick_time_unit(p["time_unit"], float(t[-1]) if len(t) else 0.0)
        tx = t * self.tf
        dt = (tx[1] - tx[0]) if len(tx) > 1 else 1.0
        self.x0, self.x1 = tx[0] - dt / 2, tx[-1] + dt / 2
        values = np.asarray(res.values, float).T  # rows × time
        n_rows = values.shape[0]
        extent = (self.x0, self.x1, -0.5, n_rows - 0.5)
        if len(res.categories):
            cats = list(res.categories)
            custom = [c.strip() for c in p["category_colors"].split(",") if c.strip()]
            cmap = ListedColormap((custom + CATEGORY_COLORS[len(custom):])[: len(cats)])
            norm = BoundaryNorm(np.arange(len(cats) + 1) - 0.5, len(cats))
            im = ax.imshow(values, aspect="auto", origin="lower", extent=extent, cmap=cmap, norm=norm,
                           interpolation="nearest")
            if p["colorbar"]:
                self.fig.legend(handles=[Patch(color=cmap(i), label=c) for i, c in enumerate(cats)],
                                loc="outside upper center", frameon=False, ncols=len(cats), labelcolor=fg)
        else:
            im = ax.imshow(values, aspect="auto", origin="lower", extent=extent, cmap=p["colormap"],
                           interpolation="nearest")
            if p["colorbar"]:
                cb = self.fig.colorbar(im, ax=ax)
                cb.ax.tick_params(colors=fg)
                if res.units:
                    cb.set_label(res.units, color=fg)
        labels = list(res.row_labels)
        if labels:
            step = max(1, n_rows // 8)
            ax.set_yticks(range(0, n_rows, step), labels[::step])
        ax.set_xlabel(p["xlabel"] or f"Time ({self.unit})")
        ax.set_ylabel(p["ylabel"] or ("Residue" if labels else ""))
        if p["title"]:
            ax.set_title(p["title"])
        animated = p["mode"] != "static"
        self.cover = ax.add_patch(Rectangle((self.x1, -0.5), 0, n_rows, facecolor=ax.get_facecolor(),
                                            edgecolor="none", animated=animated,
                                            visible=p["mode"] == "reveal"))
        self.vline = ax.axvline(self.x0, color=p["cursor_color"], lw=1.5, animated=animated,
                                visible=animated)
        freeze_layout(self.fig, self.canvas)
        self.background = self.canvas.copy_from_bbox(self.fig.bbox)

    def draw(self, t):
        if self.p["mode"] == "static":
            return canvas_to_qimage(self.canvas)
        self.canvas.restore_region(self.background)
        if t is not None:
            tx = min(max(t * self.tf, self.x0), self.x1)
            self.cover.set_x(tx)
            self.cover.set_width(max(self.x1 - tx, 0))
            self.vline.set_xdata([tx, tx])
            for a in (self.cover, self.vline):
                if a.get_visible():
                    self.ax.draw_artist(a)
        return canvas_to_qimage(self.canvas)
