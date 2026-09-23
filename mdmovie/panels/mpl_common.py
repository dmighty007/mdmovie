"""Shared matplotlib machinery for plot-like panels.

Styles (including SciencePlots) work through matplotlib's global rcParams, while preview and
export draw from different threads. Every figure build and every frame draw therefore runs
inside `styled(props)`, which holds a lock and applies that panel's style for the duration.
"""
from __future__ import annotations

import contextlib
import json
import threading

import matplotlib
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import AutoMinorLocator, NullLocator
from PySide6.QtGui import QImage

from mdmovie.panels.base import Panel, Prop
from mdmovie.panels.series_data import series_results, series_time_info

try:  # registers the SciencePlots styles ("science", "nature", "ieee", "bright", ...)
    import scienceplots  # noqa: F401
    HAVE_SCIENCEPLOTS = True
except ImportError:
    HAVE_SCIENCEPLOTS = False

MPL_LOCK = threading.RLock()

THEMES = {
    "light": {"bg": "#ffffff", "fg": "#222222", "grid": "#dddddd", "ghost": "#bbbbbb"},
    "dark": {"bg": "#161a20", "fg": "#e6e6e6", "grid": "#3a4048", "ghost": "#5a6068"},
}
PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf"]
TIME_UNITS = {"ps": 1.0, "ns": 1e-3, "µs": 1e-6}

# name shown in the inspector → matplotlib style list ("clean" is the app's own look)
STYLES = {
    "clean": [],
    "science": ["science"],
    "science + grid": ["science", "grid"],
    "science (scatter)": ["science", "scatter"],
    "nature": ["science", "nature"],
    "ieee": ["science", "ieee"],
    "notebook": ["science", "notebook"],
    "bmh": ["bmh"],
    "ggplot": ["ggplot"],
    "seaborn paper": ["seaborn-v0_8-paper", "seaborn-v0_8-whitegrid"],
    "seaborn talk": ["seaborn-v0_8-talk", "seaborn-v0_8-ticks"],
    "fivethirtyeight": ["fivethirtyeight"],
    "classic": ["classic"],
}
PALETTES = {
    "style default": [],
    "bright": ["bright"], "vibrant": ["vibrant"], "muted": ["muted"], "high-contrast": ["high-contrast"],
    "high-vis": ["high-vis"], "light": ["light"], "retro": ["retro"], "std-colors": ["std-colors"],
    "petroff10": ["petroff10"], "colorblind (tableau)": ["tableau-colorblind10"],
    "colorblind (seaborn)": ["seaborn-v0_8-colorblind"],
}
SCIENCE_STYLES = {"science", "nature", "ieee", "notebook", "grid", "scatter", "bright", "vibrant", "muted",
                  "high-contrast", "high-vis", "light", "retro", "std-colors"}
FONT_FAMILIES = ("style default", "sans-serif", "serif", "monospace")

STYLE_PROPS = [
    Prop("style", "choice", "clean", "Plot style", tuple(STYLES), section="Style"),
    Prop("palette", "choice", "style default", "Colour palette", tuple(PALETTES), section="Style"),
    Prop("theme", "choice", "light", "Background", ("light", "dark"), section="Style"),
    Prop("transparent", "bool", False, "Transparent background", section="Style"),
    Prop("font_size", "float", 12.0, "Font size (pt)", minimum=4, maximum=72, step=0.5, section="Style"),
    Prop("font_family", "choice", "style default", "Font family", FONT_FAMILIES, section="Style"),
    Prop("latex", "bool", False, "LaTeX text (needs LaTeX; first draw takes seconds)", section="Style"),
    Prop("time_unit", "choice", "auto", "Time unit", ("auto", "ps", "ns", "µs"), section="Axes"),
    Prop("title", "str", "", "Title", section="Axes"),
    Prop("xlabel", "str", "", "X label (blank = auto)", section="Axes"),
]

AXES_PROPS = [
    Prop("tick_direction", "choice", "style default", "Tick direction", ("style default", "in", "out", "inout"),
         section="Axes"),
    Prop("minor_ticks", "choice", "style default", "Minor ticks", ("style default", "on", "off"), section="Axes"),
    Prop("spines", "choice", "box", "Frame", ("box", "left + bottom", "none"), section="Axes"),
    Prop("grid", "choice", "major", "Grid", ("off", "major", "major + minor", "style default"), section="Axes"),
    Prop("grid_alpha", "float", 0.5, "Grid opacity", minimum=0, maximum=1, step=0.05, section="Axes"),
]


def style_list(p: dict) -> list[str]:
    styles = ["dark_background"] if p.get("theme") == "dark" and p.get("style") != "clean" else []
    styles += STYLES.get(p.get("style", "clean"), [])
    styles += PALETTES.get(p.get("palette", "style default"), [])
    if not HAVE_SCIENCEPLOTS:
        styles = [s for s in styles if s not in SCIENCE_STYLES]
    if not p.get("latex") and any(st in SCIENCE_STYLES for st in styles):
        styles += ["no-latex"]  # SciencePlots' LaTeX-look fonts without needing LaTeX
    return styles


def rc_overrides(p: dict) -> dict:
    fs = float(p.get("font_size", 12))
    rc = {"font.size": fs, "axes.labelsize": fs, "axes.titlesize": fs * 1.15, "legend.fontsize": fs * 0.85,
          "xtick.labelsize": fs * 0.85, "ytick.labelsize": fs * 0.85, "text.usetex": bool(p.get("latex"))}
    fam = p.get("font_family", "style default")
    if fam != "style default":
        rc["font.family"] = fam
    if p.get("tick_direction", "style default") != "style default":
        rc["xtick.direction"] = rc["ytick.direction"] = p["tick_direction"]
    return rc


@contextlib.contextmanager
def styled(p: dict):
    """Hold the matplotlib lock with this panel's style + overrides applied."""
    with MPL_LOCK:
        with matplotlib.style.context(style_list(p), after_reset=True):
            with matplotlib.rc_context(rc_overrides(p)):
                yield


def palette_colors() -> list[str]:
    """Colours of the active style's cycle (call inside `styled`)."""
    cyc = matplotlib.rcParams["axes.prop_cycle"].by_key().get("color")
    return list(cyc) if cyc else PALETTE


def pick_time_unit(unit: str, max_t: float) -> tuple[str, float]:
    if unit == "auto":
        unit = "µs" if max_t >= 1e6 else "ns" if max_t >= 1e3 else "ps"
    return unit, TIME_UNITS[unit]


def make_figure(w: int, h: int, scale: float, p: dict):
    """Figure of exactly w×h pixels whose fonts/lines scale with `scale` (call inside `styled`)."""
    dpi = 100.0 * scale
    fig = Figure(figsize=(max(w, 1) / dpi, max(h, 1) / dpi), dpi=dpi, layout="constrained")
    canvas = FigureCanvasAgg(fig)
    if p.get("style", "clean") == "clean":
        fig.patch.set_facecolor(THEMES[p.get("theme", "light")]["bg"])
    fig.patch.set_alpha(0.0 if p.get("transparent") else 1.0)
    return fig, canvas


def style_axes(ax, p: dict, primary: bool = True) -> None:
    """Colours for the app's own "clean" style; tick/spine/grid options for every style."""
    clean = p.get("style", "clean") == "clean"
    theme = THEMES[p.get("theme", "light")]
    if clean:
        ax.set_facecolor(theme["bg"])
        for s in ax.spines.values():
            s.set_color(theme["fg"])
        ax.tick_params(colors=theme["fg"], which="both")
        for lab in (ax.xaxis.label, ax.yaxis.label, ax.title):
            lab.set_color(theme["fg"])
    if p.get("transparent"):
        ax.set_facecolor("none")
    spines = p.get("spines", "box")
    if spines != "box":
        for side in ("top", "right") if spines == "left + bottom" else ("top", "right", "left", "bottom"):
            ax.spines[side].set_visible(False)
        if spines == "left + bottom":
            ax.tick_params(top=False, right=False, which="both")
    minor = p.get("minor_ticks", "style default")
    if minor == "on":
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.yaxis.set_minor_locator(AutoMinorLocator())
    elif minor == "off":
        ax.xaxis.set_minor_locator(NullLocator())
        ax.yaxis.set_minor_locator(NullLocator())
    grid = p.get("grid", "major")
    if primary and grid != "style default":
        ax.grid(False, which="both")
        if grid != "off":
            kw = dict(alpha=p.get("grid_alpha", 0.5), linewidth=0.7)
            if clean:
                kw["color"] = theme["grid"]
            ax.grid(True, which="major", **kw)
            if grid == "major + minor":
                if minor != "off":
                    ax.xaxis.set_minor_locator(AutoMinorLocator())
                    ax.yaxis.set_minor_locator(AutoMinorLocator())
                ax.grid(True, which="minor", **{**kw, "alpha": kw["alpha"] * 0.5, "linewidth": 0.4})
            ax.set_axisbelow(True)


def fg_color(p: dict) -> str:
    """Foreground (text) colour to use for annotations (call inside `styled`)."""
    if p.get("style", "clean") == "clean":
        return THEMES[p.get("theme", "light")]["fg"]
    return matplotlib.rcParams["text.color"]


def canvas_to_qimage(canvas) -> QImage:
    buf = np.asarray(canvas.buffer_rgba())
    h, w = buf.shape[:2]
    return QImage(buf.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


def freeze_layout(fig, canvas) -> None:
    """Run constrained layout once, then keep the axes fixed (no jitter between frames)."""
    canvas.draw()
    fig.set_layout_engine("none")


class SeriesPanel(Panel):
    """Base for panels that draw analysis results; handles results lookup and figure caching."""
    HAS_SERIES = True
    SERIES_KINDS = ("timeseries",)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._renderer = None
        self._renderer_key = None

    @classmethod
    def migrate(cls, props: dict) -> dict:
        if isinstance(props.get("grid"), bool):  # before 0.2 the grid was on/off
            props["grid"] = "major" if props["grid"] else "off"
        return props

    def results(self, project):
        """[(series, result or None, status message)] for each series."""
        return series_results(project, self.series)

    def time_info(self, project):
        return series_time_info(project, self.series)

    def cache_key(self, w, h, scale, project) -> str:
        res_ids = [id(r) for _, r, _ in self.results(project)]
        return json.dumps([w, h, scale, self.props, [s.to_dict() for s in self.series], res_ids],
                          sort_keys=True, default=str)

    def renderer(self, w, h, scale, project):
        key = self.cache_key(w, h, scale, project)
        if key != self._renderer_key:
            with styled(self.props):
                self._renderer = self.build_renderer(w, h, scale, project)
            self._renderer_key = key
        return self._renderer

    def draw_frame(self, w, h, scale, project, t) -> QImage:
        r = self.renderer(w, h, scale, project)
        with styled(self.props):
            return r.draw(t)

    def build_renderer(self, w, h, scale, project):
        raise NotImplementedError
