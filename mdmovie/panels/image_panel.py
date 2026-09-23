"""Image panel: an image sequence (frames rendered with VMD, PyMOL, ChimeraX) or a single picture
(e.g. a pre-rendered free-energy surface), optionally with analysis data drawn on top as a
moving point + trail. The data overlay is placed by calibrating where the plot axes sit in the
picture (which pixel box corresponds to which x/y range)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter

from mdmovie.panels.base import Panel, Prop, RenderContext, draw_message
from mdmovie.panels.overlay import draw_point_and_trail, trail_props
from mdmovie.panels.series_data import current_index, series_results, series_time_info, xy_data
from mdmovie.sources.image_sequence import DEFAULT_NUMBER_REGEX, IMAGE_CACHE, Sequence, build_sequence, image_size

PREFETCH_AHEAD = 12


class ImagePanel(Panel):
    KIND = "image"
    TITLE = "Image sequence"
    HAS_SERIES = True               # series = data for the optional overlay
    SERIES_KINDS = ("timeseries",)
    PROPS = [
        Prop("folder", "dir", "", "Folder or image file", section="Source"),
        Prop("pattern", "str", "*.png", "File pattern", section="Source"),
        Prop("index_from", "choice", "order", "Frame index from", ("order", "filename"), section="Timing"),
        Prop("number_regex", "str", DEFAULT_NUMBER_REGEX, "Number regex", section="Timing"),
        Prop("t0", "float", 0.0, "Time of index 0 (ps)", minimum=-1e12, maximum=1e12, section="Timing"),
        Prop("dt", "float", 1.0, "Time per index (ps)", minimum=1e-9, maximum=1e12, step=0.1, section="Timing"),
        Prop("fit", "choice", "contain", "Fit", ("contain", "cover", "stretch"), section="Display"),
        Prop("crop", "crop", None, "Crop", hidden=True),
        # --- data overlay (scatter on top of e.g. a pre-rendered FES) ---
        Prop("overlay", "bool", False, "Draw data on the image", section="Data overlay"),
        Prop("calib", "rect", [0.0, 0.0, 1.0, 1.0], "Plot area in the image", hidden=True),
        Prop("ax_xmin", "float", 0.0, "X at left edge of plot area", minimum=-1e12, maximum=1e12, step=0.1,
             section="Data overlay"),
        Prop("ax_xmax", "float", 1.0, "X at right edge", minimum=-1e12, maximum=1e12, step=0.1,
             section="Data overlay"),
        Prop("ax_ymin", "float", 0.0, "Y at bottom edge", minimum=-1e12, maximum=1e12, step=0.1,
             section="Data overlay"),
        Prop("ax_ymax", "float", 1.0, "Y at top edge", minimum=-1e12, maximum=1e12, step=0.1,
             section="Data overlay"),
        Prop("clip_overlay", "bool", True, "Clip to the plot area", section="Data overlay"),
        *trail_props("Data overlay"),
    ]

    def sequence(self, project) -> Sequence:
        p = self.props
        return build_sequence(project.resolve_path(p["folder"]), p["pattern"], p["index_from"],
                              p["number_regex"], float(p["t0"]), float(p["dt"]))

    def overlay_on(self) -> bool:
        return bool(self.props["overlay"] and self.series)

    def time_info(self, project):
        infos = []
        seq = self.sequence(project)
        if len(seq) > 1:  # a single picture is a static background, not a time axis
            infos.append(seq.time_info())
        if self.overlay_on():
            infos.append(series_time_info(project, self.series))
        infos = [i for i in infos if i]
        if not infos:
            return None
        return min(i[0] for i in infos), max(i[1] for i in infos), min(i[2] for i in infos)

    def index_at(self, project, t: float) -> int:
        return self.sequence(project).index_at(t)

    def results(self, project):
        return series_results(project, self.series)

    def render(self, painter: QPainter, rect: QRectF, ctx: RenderContext) -> None:
        seq = self.sequence(ctx.project)
        if not len(seq):
            draw_message(painter, rect, "No images\n(choose a folder or an image file in the inspector)", ctx.scale)
            return
        i = seq.index_at(ctx.time) if len(seq) > 1 else 0
        img = IMAGE_CACHE.get(seq.paths[i], self.props["crop"])
        if img is None:
            draw_message(painter, rect, f"Cannot read\n{seq.paths[i]}", ctx.scale, "#c33")
            return
        IMAGE_CACHE.prefetch(seq.paths[i + 1: i + 1 + PREFETCH_AHEAD], self.props["crop"])
        target = draw_fitted(painter, rect, img, self.props["fit"])
        if self.overlay_on():
            self._draw_overlay(painter, rect, target, seq.paths[i], ctx)

    def data_to_image(self, x, y):
        """Data coordinates → normalized position in the *uncropped* image (0..1, y down); arrays ok."""
        p = self.props
        cx, cy, cw, ch = p["calib"] or [0, 0, 1, 1]
        fx = (x - p["ax_xmin"]) / ((p["ax_xmax"] - p["ax_xmin"]) or 1.0)
        fy = (y - p["ax_ymin"]) / ((p["ax_ymax"] - p["ax_ymin"]) or 1.0)
        return cx + fx * cw, cy + (1.0 - fy) * ch

    def _draw_overlay(self, painter, rect, target: QRectF, path: str, ctx):
        data = xy_data(ctx.project, self.series, self.props["x_col"], self.props["y_col"])
        if data is None:
            draw_message(painter, rect, "Overlay: add series for the x and y columns", ctx.scale, "#c33")
            return
        t, x, y, _, _ = data
        k = current_index(t, ctx.time)
        crop = self.props["crop"] or [0, 0, 1, 1]

        def to_px(u, v):  # uncropped normalized → widget pixels (works on arrays too)
            uc, vc = (u - crop[0]) / crop[2], (v - crop[1]) / crop[3]
            return target.x() + uc * target.width(), target.y() + vc * target.height()

        px, py = to_px(*self.data_to_image(x, y))
        painter.save()
        clip = QRectF(rect)
        if self.props["clip_overlay"]:
            c = self.props["calib"] or [0, 0, 1, 1]
            clip = clip.intersected(QRectF(QPointF(*to_px(c[0], c[1])), QPointF(*to_px(c[0] + c[2], c[1] + c[3]))))
        painter.setClipRect(clip)
        draw_point_and_trail(painter, px, py, k, self.props, ctx.scale)
        painter.restore()

    def prefetch(self, project, times):
        seq = self.sequence(project)
        if len(seq):
            IMAGE_CACHE.prefetch([seq.paths[seq.index_at(t)] for t in times], self.props["crop"])

    def calibration_image_size(self, project) -> tuple[int, int] | None:
        seq = self.sequence(project)
        return image_size(seq.paths[0]) if len(seq) else None


def fitted_rect(rect: QRectF, w: int, h: int, fit: str) -> QRectF:
    if fit == "stretch" or w == 0 or h == 0:
        return QRectF(rect)
    s = (min if fit == "contain" else max)(rect.width() / w, rect.height() / h)
    tw, th = w * s, h * s
    return QRectF(rect.center().x() - tw / 2, rect.center().y() - th / 2, tw, th)


def draw_fitted(painter: QPainter, rect: QRectF, img, fit: str) -> QRectF:
    """Draw the image fitted into rect; returns where it landed."""
    target = fitted_rect(rect, img.width(), img.height(), fit)
    painter.save()
    painter.setClipRect(rect)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    # pre-scale with a high quality filter when shrinking a lot (drawImage's bilinear aliases)
    if target.width() < img.width() * 0.6:
        img = img.scaled(max(1, round(target.width())), max(1, round(target.height())),
                         Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    painter.drawImage(target, img)
    painter.restore()
    return target

