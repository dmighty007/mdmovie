"""CV map panel: two collective variables as a moving point + trail over a 2-D background —
a free-energy surface computed from the data (−kT ln P), a density, or an image file.
With a single data column (e.g. just an RMSD) it is drawn against time instead."""
from __future__ import annotations

import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.colors import to_rgba
from PySide6.QtCore import QRectF

from mdmovie.panels.base import Prop, RenderContext, draw_message, with_defaults
from mdmovie.panels.mpl_common import (AXES_PROPS, STYLE_PROPS, SeriesPanel, canvas_to_qimage, fg_color,
                                       freeze_layout, make_figure, style_axes)
from mdmovie.panels.overlay import MARKER_SHAPES, trail_props, trail_range
from mdmovie.panels.series_data import columns, current_index, xy_data

KB = {"kJ/mol": 0.0083144626, "kcal/mol": 0.0019872043, "kT": None}
MPL_MARKERS = dict(zip(MARKER_SHAPES, ("o", "s", "D", "^", "*", "X")))
PX_TO_PT = 0.72   # figures are drawn at 100 dpi × scale, so 1 output px = 0.72 pt


def smooth2d(h: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian smoothing of a histogram (in bins); keeps empty regions empty."""
    if sigma <= 0:
        return h
    try:
        from scipy.ndimage import gaussian_filter
    except ImportError:
        return h
    out = gaussian_filter(h, sigma, mode="constant")
    out[gaussian_filter((h > 0).astype(float), sigma, mode="constant") < 0.05] = 0
    return out


def free_energy(x, y, bins: int, temperature: float, unit: str, rng=None, sigma: float = 0.0):
    """F(x, y) = −kT ln P(x, y), shifted so the minimum is 0; empty bins are NaN."""
    h, xe, ye = np.histogram2d(x, y, bins=bins, range=rng, density=True)
    h = smooth2d(h, sigma)
    with np.errstate(divide="ignore"):
        f = -np.log(h)
    kt = 1.0 if KB[unit] is None else KB[unit] * temperature
    f = kt * f
    f[~np.isfinite(f)] = np.nan
    if np.isfinite(f).any():
        f -= np.nanmin(f)
    return f.T, xe, ye      # transposed so rows = y for imshow/contourf


def cv_data(project, series_list, xi: int, yi: int):
    """(xy_data result or None, vs_time). A single column is plotted against time (x = t)."""
    cols = columns(project, series_list)
    if len(cols) == 1:
        lab, units, t, v = cols[0]
        ok = np.isfinite(v)
        return (t[ok], t[ok], v[ok], "Time (ps)", f"{lab} ({units})" if units else lab), True
    return xy_data(project, series_list, xi, yi), False


class CVMapPanel(SeriesPanel):
    KIND = "cvmap"
    TITLE = "CV map"
    SERIES_KINDS = ("timeseries",)
    PROPS = [
        Prop("bg_mode", "choice", "free energy", "Background",
             ("free energy", "density", "image file", "none"), section="Background"),
        Prop("bins", "int", 40, "Histogram bins", minimum=5, maximum=1000, section="Background"),
        Prop("smoothing", "float", 1.0, "Smoothing (bins, 0 = off)", minimum=0, maximum=20, step=0.25,
             section="Background"),
        Prop("temperature", "float", 300.0, "Temperature (K)", minimum=1, maximum=5000, section="Background"),
        Prop("energy_unit", "choice", "kJ/mol", "Energy unit", tuple(KB), section="Background"),
        Prop("fes_max", "optfloat", None, "Free-energy cap (blank = auto)", section="Background"),
        Prop("colormap", "str", "viridis", "Colormap", section="Background"),
        Prop("contours", "bool", True, "Contour lines", section="Background"),
        Prop("levels", "int", 12, "Contour levels", minimum=2, maximum=100, section="Background"),
        Prop("contour_color", "color", "#ffffff", "Contour colour", section="Background"),
        Prop("colorbar", "bool", True, "Colour bar", section="Background"),
        Prop("image", "image", "", "Background image", section="Background"),
        Prop("img_xmin", "optfloat", None, "Image x min (blank = data)", section="Background"),
        Prop("img_xmax", "optfloat", None, "Image x max", section="Background"),
        Prop("img_ymin", "optfloat", None, "Image y min", section="Background"),
        Prop("img_ymax", "optfloat", None, "Image y max", section="Background"),
        Prop("bg_alpha", "float", 1.0, "Background opacity", minimum=0, maximum=1, step=0.05, section="Background"),
        *trail_props("Point and trail"),
        *STYLE_PROPS,
        Prop("ylabel", "str", "", "Y label (blank = auto)", section="Axes"),
        Prop("xmin", "optfloat", None, "X min", section="Axes"),
        Prop("xmax", "optfloat", None, "X max", section="Axes"),
        Prop("ymin", "optfloat", None, "Y min", section="Axes"),
        Prop("ymax", "optfloat", None, "Y max", section="Axes"),
        Prop("equal_aspect", "bool", False, "Equal axis scales", section="Axes"),
        *with_defaults(AXES_PROPS, grid="off"),
    ]

    def render(self, painter, rect: QRectF, ctx: RenderContext) -> None:
        if not self.series:
            draw_message(painter, rect, "No data\n(add series for the x and y CVs)", ctx.scale)
            return
        data, _ = cv_data(ctx.project, self.series, self.props["x_col"], self.props["y_col"])
        if data is None:
            pending = [m for _, r, m in self.results(ctx.project) if r is None]
            draw_message(painter, rect, "\n".join(pending) or
                         "Needs two data columns (x and y): add a second series or pick other columns", ctx.scale)
            return
        w, h = max(1, round(rect.width())), max(1, round(rect.height()))
        painter.drawImage(rect.topLeft(), self.draw_frame(w, h, ctx.scale, ctx.project, ctx.time))

    def build_renderer(self, w, h, scale, project):
        return _CVMapRenderer(self, w, h, scale, project)


class _CVMapRenderer:
    def __init__(self, panel: CVMapPanel, w, h, scale, project):
        p = self.p = panel.props
        data, vs_time = cv_data(project, panel.series, p["x_col"], p["y_col"])
        self.t, self.x, self.y, xl, yl = data
        self.fig, self.canvas = make_figure(w, h, scale, p)
        ax = self.ax = self.fig.add_subplot(111)
        style_axes(ax, p)
        fg = fg_color(p)
        x, y = self.x, self.y
        pad_x = 0.03 * ((x.max() - x.min()) or 1)
        pad_y = 0.03 * ((y.max() - y.min()) or 1)
        rng = [[x.min() - pad_x, x.max() + pad_x], [y.min() - pad_y, y.max() + pad_y]]

        bg = p["bg_mode"]
        if vs_time and bg in ("free energy", "density"):
            bg = "none"  # a histogram over (time, value) has no meaning
        mappable = None
        if bg in ("free energy", "density") and len(x) > 2:
            if bg == "free energy":
                f, xe, ye = free_energy(x, y, p["bins"], p["temperature"], p["energy_unit"], rng, p["smoothing"])
                vmax = p["fes_max"] if p["fes_max"] is not None else np.nanmax(f)
                f = np.minimum(f, vmax)
                label = f"Free energy ({p['energy_unit']})"
            else:
                h2, xe, ye = np.histogram2d(x, y, bins=p["bins"], range=rng, density=True)
                h2 = smooth2d(h2, p["smoothing"])
                f = np.where(h2.T > 0, h2.T, np.nan)
                vmax = np.nanmax(f)
                label = "Probability density"
            xc, yc = 0.5 * (xe[1:] + xe[:-1]), 0.5 * (ye[1:] + ye[:-1])
            levels = np.linspace(0 if bg == "free energy" else np.nanmin(f), vmax, p["levels"] + 1)
            mappable = ax.contourf(xc, yc, f, levels=levels, cmap=p["colormap"], alpha=p["bg_alpha"], zorder=0)
            if p["contours"]:
                ax.contour(xc, yc, f, levels=levels[::2], colors=p["contour_color"], linewidths=0.5, alpha=0.5,
                           zorder=1)
        elif bg == "image file" and p["image"]:
            import matplotlib.image as mpimg
            img = mpimg.imread(project.resolve_path(p["image"]))
            ext = [p["img_xmin"] if p["img_xmin"] is not None else rng[0][0],
                   p["img_xmax"] if p["img_xmax"] is not None else rng[0][1],
                   p["img_ymin"] if p["img_ymin"] is not None else rng[1][0],
                   p["img_ymax"] if p["img_ymax"] is not None else rng[1][1]]
            ax.imshow(img, extent=ext, origin="upper", aspect="auto", alpha=p["bg_alpha"], zorder=0)
            rng = [ext[:2], ext[2:]]
        if mappable is not None and p["colorbar"]:
            cb = self.fig.colorbar(mappable, ax=ax)
            cb.set_label(label, color=fg)
            cb.ax.tick_params(colors=fg)

        if p["path"]:
            ax.plot(x, y, color=p["path_color"], alpha=p["path_alpha"], lw=p["trail_width"] * PX_TO_PT * 0.5,
                    zorder=2)
        ax.set_xlim(p["xmin"] if p["xmin"] is not None else rng[0][0],
                    p["xmax"] if p["xmax"] is not None else rng[0][1])
        ax.set_ylim(p["ymin"] if p["ymin"] is not None else rng[1][0],
                    p["ymax"] if p["ymax"] is not None else rng[1][1])
        if p["equal_aspect"]:
            ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel(p["xlabel"] or xl)
        ax.set_ylabel(p["ylabel"] or yl)
        if p["title"]:
            ax.set_title(p["title"])

        color = p["marker_color"]
        trail_rgba = to_rgba(p["trail_color"] or color)
        self.trail_rgba = np.array(trail_rgba)
        self.trail = LineCollection([], linewidths=p["trail_width"] * PX_TO_PT, capstyle="round",
                                    animated=True, zorder=3)
        ax.add_collection(self.trail)
        self.dots = ax.scatter([], [], s=(p["trail_width"] * 1.8 * PX_TO_PT) ** 2, animated=True, zorder=3,
                               linewidths=0)
        self.point = ax.plot([], [], MPL_MARKERS.get(p["marker"], "o"), ms=p["marker_size"] * PX_TO_PT,
                             color=color, mec=p["marker_edge"], mew=1.2, animated=True, zorder=4)[0]
        freeze_layout(self.fig, self.canvas)
        self.background = self.canvas.copy_from_bbox(self.fig.bbox)

    def draw(self, t):
        self.canvas.restore_region(self.background)
        k = current_index(self.t, t)
        if k >= 0:
            p = self.p
            idx = np.fromiter(trail_range(k, p), int)
            style = p["trail"]
            if style != "none" and len(idx) > 1:
                n = len(idx)
                alpha = np.linspace(0.08, 1.0, n) if style.startswith("fading") else np.ones(n)
                rgba = np.tile(self.trail_rgba, (n, 1))
                rgba[:, 3] = alpha * self.trail_rgba[3]
                if style in ("fading line", "line"):
                    pts = np.column_stack([self.x[idx], self.y[idx]])
                    self.trail.set_segments(np.stack([pts[:-1], pts[1:]], axis=1))
                    self.trail.set_color(rgba[1:])
                    self.ax.draw_artist(self.trail)
                else:
                    self.dots.set_offsets(np.column_stack([self.x[idx[:-1]], self.y[idx[:-1]]]))
                    self.dots.set_facecolor(rgba[:-1])
                    self.ax.draw_artist(self.dots)
            self.point.set_data([self.x[k]], [self.y[k]])
            self.ax.draw_artist(self.point)
        return canvas_to_qimage(self.canvas)
