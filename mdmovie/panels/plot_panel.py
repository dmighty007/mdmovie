"""Line-plot panel: time series from analysis presets with a moving time cursor."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter

from mdmovie.panels.base import Prop, RenderContext, draw_message
from mdmovie.panels.mpl_common import (AXES_PROPS, STYLE_PROPS, SeriesPanel, canvas_to_qimage, fg_color,
                                       freeze_layout, make_figure, palette_colors, pick_time_unit, style_axes)

LINESTYLES = {"solid": "-", "dashed": "--", "dotted": ":", "dashdot": "-.", "none": "none"}
MARKERS = {"none": "", "circle": "o", "square": "s", "triangle": "^", "diamond": "D", "cross": "x",
           "plus": "+", "point": ".", "star": "*"}


def smooth(y: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(y) < n:
        return y
    k = np.ones(n) / n
    pad = np.pad(y, (n // 2, n - 1 - n // 2), mode="edge")
    return np.convolve(pad, k, mode="valid")


def parse_floats(text: str) -> list[float]:
    out = []
    for part in str(text or "").replace(";", ",").split(","):
        try:
            out.append(float(part))
        except ValueError:
            pass
    return out


class PlotPanel(SeriesPanel):
    KIND = "plot"
    TITLE = "Plot"
    SERIES_KINDS = ("timeseries", "profile")
    PROPS = [
        Prop("mode", "choice", "reveal", "Animation",
             ("reveal", "marker", "window", "static"), section="Animation"),
        Prop("window", "float", 0.0, "Window width (ps, 0 = auto)", minimum=0, maximum=1e12, section="Animation"),
        Prop("ghost", "bool", True, "Show full curve faintly", section="Animation"),
        Prop("ghost_alpha", "float", 0.2, "Faint curve opacity", minimum=0, maximum=1, step=0.05,
             section="Animation"),
        Prop("cursor_line", "bool", True, "Vertical time cursor", section="Animation"),
        Prop("cursor_color", "color", "#888888", "Cursor colour", section="Animation"),
        Prop("cursor_style", "choice", "dashed", "Cursor line", ("dashed", "solid", "dotted"), section="Animation"),
        Prop("marker_size", "float", 7.0, "Current-value dot size", maximum=50, section="Animation"),
        Prop("show_value", "bool", False, "Show current value", section="Animation"),
        Prop("value_format", "str", "{label}: {value:.2f} {units}", "Value format", section="Animation"),
        Prop("value_position", "choice", "top left", "Value position",
             ("top left", "top right", "bottom left", "bottom right"), section="Animation"),
        *STYLE_PROPS,
        Prop("line_width", "float", 2.0, "Line width", maximum=20, step=0.25, section="Style"),
        Prop("smoothing", "int", 1, "Running mean (points)", minimum=1, maximum=10000, section="Style"),
        Prop("fill_alpha", "float", 0.2, "Fill opacity (series with fill)", minimum=0, maximum=1, step=0.05,
             section="Style"),
        Prop("ylabel", "str", "", "Y label (blank = auto)", section="Axes"),
        Prop("ylabel_right", "str", "", "Right Y label", section="Axes"),
        Prop("xmin", "optfloat", None, "X min", section="Axes"),
        Prop("xmax", "optfloat", None, "X max", section="Axes"),
        Prop("ymin", "optfloat", None, "Y min", section="Axes"),
        Prop("ymax", "optfloat", None, "Y max", section="Axes"),
        Prop("y2min", "optfloat", None, "Right Y min", section="Axes"),
        Prop("y2max", "optfloat", None, "Right Y max", section="Axes"),
        Prop("yscale", "choice", "linear", "Y scale", ("linear", "log", "symlog"), section="Axes"),
        *AXES_PROPS,
        Prop("hlines", "str", "", "Reference lines at y = (comma-separated)", section="Reference lines"),
        Prop("hline_color", "color", "#888888", "Reference line colour", section="Reference lines"),
        Prop("hline_style", "choice", "dotted", "Reference line style", ("dotted", "dashed", "solid"),
             section="Reference lines"),
        Prop("vlines", "str", "", "Marks at time = (ps, comma-separated)", section="Reference lines"),
        Prop("legend", "choice", "best", "Legend",
             ("none", "best", "upper left", "upper right", "lower left", "lower right", "outside top",
              "outside right"), section="Legend"),
        Prop("legend_frame", "bool", False, "Legend frame", section="Legend"),
        Prop("legend_columns", "int", 1, "Legend columns", minimum=1, maximum=8, section="Legend"),
    ]

    def render(self, painter: QPainter, rect: QRectF, ctx: RenderContext) -> None:
        if not self.series:
            draw_message(painter, rect, "No data\n(add a series in the inspector)", ctx.scale)
            return
        w, h = max(1, round(rect.width())), max(1, round(rect.height()))
        painter.drawImage(rect.topLeft(), self.draw_frame(w, h, ctx.scale, ctx.project, ctx.time))

    def build_renderer(self, w, h, scale, project):
        return _PlotRenderer(self, w, h, scale, self.results(project))


class _PlotRenderer:
    """Built and drawn inside `styled(props)` (see SeriesPanel)."""

    def __init__(self, panel: PlotPanel, w, h, scale, results):
        p = self.p = panel.props
        self.fig, self.canvas = make_figure(w, h, scale, p)
        ax = self.fig.add_subplot(111)
        style_axes(ax, p)
        self.ax, self.ax2 = ax, None
        fg = fg_color(p)
        colors = palette_colors()

        timeseries = [(s, r) for s, r, _ in results if r is not None and r.kind == "timeseries"]
        profiles = [(s, r) for s, r, _ in results if r is not None and r.kind == "profile"]
        pending = [msg for s, r, msg in results if r is None]
        self.is_profile = not timeseries and bool(profiles)
        max_t = max((float(r.time[-1]) for _, r in timeseries if len(r.time)), default=0.0)
        self.unit, self.tf = pick_time_unit(p["time_unit"], max_t)

        self.lines = []  # dicts: x, y, label, units, line, marker, axes, fill
        mode = p["mode"]
        color_i = 0
        for s, res in (profiles if self.is_profile else timeseries):
            cols = range(res.values.shape[1]) if s.column is None else [s.column]
            target = ax
            if s.axis == "right":
                if self.ax2 is None:
                    self.ax2 = ax.twinx()
                    style_axes(self.ax2, {**p, "transparent": True, "spines": "box"}, primary=False)
                target = self.ax2
            for c in cols:
                if c >= res.values.shape[1]:
                    continue
                color = s.color if s.color and len(cols) == 1 else colors[color_i % len(colors)]
                color_i += 1
                x = (res.x if self.is_profile else res.time * self.tf)
                y = smooth(res.values[:, c], int(p["smoothing"]))
                label = s.label or res.labels[c]
                if s.label and len(cols) > 1:
                    label = f"{s.label} {res.labels[c]}"
                lw = s.linewidth or p["line_width"]
                style = dict(color=color, lw=lw, ls=LINESTYLES.get(s.linestyle, "-"),
                             marker=MARKERS.get(s.marker, ""), ms=max(3.0, lw * 2.2), alpha=s.alpha)
                entry = {"x": x, "y": y, "label": label, "units": res.units, "color": color, "ax": target,
                         "fill": None}
                animate = not self.is_profile and mode in ("reveal", "window")
                if animate and p["ghost"]:
                    target.plot(x, y, color=color, lw=lw, ls=style["ls"], alpha=p["ghost_alpha"] * s.alpha)
                if s.fill and not animate:
                    target.fill_between(x, y, color=color, alpha=p["fill_alpha"] * s.alpha, lw=0)
                elif s.fill and mode == "reveal":
                    entry["fill"] = (target, color, p["fill_alpha"] * s.alpha)
                entry["line"] = target.plot(x if not animate else [], y if not animate else [], label=label,
                                            animated=animate and mode == "reveal", **style)[0]
                entry["animated"] = animate
                entry["fill_artist"] = None
                if not self.is_profile and mode != "static" and p["marker_size"] > 0:
                    edge = "white" if p["theme"] == "light" else "#161a20"
                    entry["marker"] = target.plot([], [], "o", color=color, ms=p["marker_size"],
                                                  mec=edge, mew=1.0, animated=mode != "window", zorder=5)[0]
                self.lines.append(entry)

        self._set_limits()
        units = next((e["units"] for e in self.lines if e["ax"] is ax), "")
        ax.set_xlabel(p["xlabel"] or (profiles[0][1].xlabel if self.is_profile else f"Time ({self.unit})"))
        left = [e for e in self.lines if e["ax"] is ax]
        ylab = p["ylabel"] or ((left[0]["label"] + (f" ({units})" if units else "")) if len(left) == 1 else units)
        ax.set_ylabel(ylab)
        if self.ax2 is not None:
            u2 = next((e["units"] for e in self.lines if e["ax"] is self.ax2), "")
            self.ax2.set_ylabel(p["ylabel_right"] or u2)
        if p["title"]:
            ax.set_title(p["title"])

        ls_map = {"dotted": ":", "dashed": "--", "solid": "-"}
        for yv in parse_floats(p["hlines"]):
            ax.axhline(yv, color=p["hline_color"], ls=ls_map[p["hline_style"]], lw=1.0, zorder=1)
        if not self.is_profile:
            for tv in parse_floats(p["vlines"]):
                ax.axvline(tv * self.tf, color=p["hline_color"], ls=ls_map[p["hline_style"]], lw=1.0, zorder=1)

        if p["legend"] != "none" and len(self.lines) > 1:
            handles = [e["line"] for e in self.lines]
            kw = dict(frameon=p["legend_frame"], ncols=p["legend_columns"])
            if p["style"] == "clean":
                kw["labelcolor"] = fg
            if p["legend"] == "outside top":
                self.fig.legend(handles=handles, loc="outside upper center", **{**kw, "ncols": max(
                    kw["ncols"], min(4, len(handles)))})
            elif p["legend"] == "outside right":
                self.fig.legend(handles=handles, loc="outside right upper", **kw)
            else:
                ax.legend(handles=handles, loc=p["legend"], **kw)
        if pending:
            ax.text(0.5, 0.5, "\n".join(pending), transform=ax.transAxes, ha="center", va="center",
                    color="#999999", usetex=False)

        self.vline = None
        if not self.is_profile and p["cursor_line"] and mode != "static" and self.lines:
            self.vline = ax.axvline(self.lines[0]["x"][0], color=p["cursor_color"], lw=1.2,
                                    ls=ls_map[p["cursor_style"]], animated=mode != "window", zorder=4)
        self.value_text = None
        if p["show_value"] and not self.is_profile and self.lines:
            pos = p["value_position"]
            x, ha = (0.02, "left") if "left" in pos else (0.98, "right")
            y, va = (0.97, "top") if "top" in pos else (0.03, "bottom")
            # usetex off: a LaTeX run per frame would be far too slow for a changing number
            self.value_text = ax.text(x, y, "", transform=ax.transAxes, va=va, ha=ha, color=fg, usetex=False,
                                      family="monospace", animated=mode != "window", zorder=6)

        freeze_layout(self.fig, self.canvas)
        self.background = self.canvas.copy_from_bbox(self.fig.bbox)
        self.dynamic = list(self._dynamic_artists())

    def _set_limits(self):
        p, ax = self.p, self.ax
        if self.lines:
            xs = np.concatenate([e["x"] for e in self.lines])
            ax.set_xlim(xs.min(), xs.max() if xs.max() > xs.min() else xs.min() + 1)
            for a in (ax, self.ax2):
                ys = [e["y"] for e in self.lines if e["ax"] is a] if a is not None else []
                if ys:
                    ya = np.concatenate(ys)
                    ya = ya[np.isfinite(ya)]
                    if len(ya):
                        lo, hi = ya.min(), ya.max()
                        pad = 0.06 * (hi - lo or abs(hi) or 1)
                        a.set_ylim(lo - pad, hi + pad)
        if p["yscale"] != "linear":
            ax.set_yscale(p["yscale"])
        xf = 1.0 if self.is_profile else self.tf
        lo, hi = ax.get_xlim()
        ax.set_xlim(p["xmin"] * xf if p["xmin"] is not None else lo, p["xmax"] * xf if p["xmax"] is not None else hi)
        for a, kmin, kmax in ((ax, "ymin", "ymax"), (self.ax2, "y2min", "y2max")):
            if a is not None and (p[kmin] is not None or p[kmax] is not None):
                lo, hi = a.get_ylim()
                a.set_ylim(p[kmin] if p[kmin] is not None else lo, p[kmax] if p[kmax] is not None else hi)

    def _dynamic_artists(self):
        for e in self.lines:
            if e["line"].get_animated():
                yield e["line"]
            if e.get("marker") is not None and e["marker"].get_animated():
                yield e["marker"]
        if self.vline is not None and self.vline.get_animated():
            yield self.vline
        if self.value_text is not None and self.value_text.get_animated():
            yield self.value_text

    def draw(self, t: float | None):
        p = self.p
        if self.is_profile or p["mode"] == "static" or not self.lines:
            return canvas_to_qimage(self.canvas)  # nothing moves: the buffer holds the full plot
        if t is None:
            self.canvas.restore_region(self.background)
            return canvas_to_qimage(self.canvas)
        tx = t * self.tf
        texts, fills = [], []
        for e in self.lines:
            x, y = e["x"], e["y"]
            k = int(np.searchsorted(x, tx, side="right"))
            yv = float(np.interp(tx, x, y)) if len(x) else np.nan
            if e["animated"]:
                xs = np.append(x[:k], tx) if 0 < k < len(x) else x[:k]
                ys = np.append(y[:k], yv) if 0 < k < len(x) else y[:k]
                if p["mode"] == "window":
                    xs, ys = x, y
                e["line"].set_data(xs, ys)
                if e["fill"] is not None and len(xs) > 1:  # growing filled area (reveal mode)
                    target, color, alpha = e["fill"]
                    fills.append(target.fill_between(xs, ys, color=color, alpha=alpha, lw=0, animated=True))
            if e.get("marker") is not None:
                e["marker"].set_data([min(max(tx, x[0]), x[-1])], [yv])
            try:
                texts.append(p["value_format"].format(label=e["label"], value=yv, units=e["units"]))
            except (KeyError, ValueError, IndexError):
                texts.append(f"{e['label']}: {yv:.3g}")
        if self.vline is not None:
            self.vline.set_xdata([tx, tx])
        if self.value_text is not None:
            self.value_text.set_text("\n".join(texts))

        if p["mode"] == "window":
            if p["window"] > 0:
                half = p["window"] * self.tf
            else:  # auto: a quarter of the data's time span
                half = 0.25 * (max(e["x"][-1] for e in self.lines) - min(e["x"][0] for e in self.lines))
            self.ax.set_xlim(tx - half * 0.8, tx + half * 0.2)
            self.canvas.draw()
        else:
            self.canvas.restore_region(self.background)
            for f in fills:
                f.axes.draw_artist(f)
            for a in self.dynamic:
                a.axes.draw_artist(a)
            for f in fills:
                f.remove()
        return canvas_to_qimage(self.canvas)
