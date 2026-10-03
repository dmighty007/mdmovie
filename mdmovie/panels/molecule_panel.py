"""Molecule panel: draws a loaded trajectory itself (cartoon, sticks, spheres…) with the built-in
software renderer: an alternative to rendering an image sequence in VMD, PyMOL or ChimeraX first.
The camera is set by dragging in the preview and stored with the project."""
from __future__ import annotations

import copy
import json
import threading
from collections import OrderedDict

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from mdmovie.mol.structure import ALIGN_MODES, DEFAULT_REPS
from mdmovie.panels.base import Panel, Prop, RenderContext, draw_message

CACHE_BYTES = 400_000_000
_images: OrderedDict = OrderedDict()      # rendered frames, shared by all molecule panels
_images_lock = threading.Lock()


def _cached(key, make) -> QImage:
    with _images_lock:
        if key in _images:
            _images.move_to_end(key)
            return _images[key]
    img = make()
    with _images_lock:
        _images[key] = img
        total = sum(i.sizeInBytes() for i in _images.values())
        while total > CACHE_BYTES and len(_images) > 1:
            total -= _images.popitem(last=False)[1].sizeInBytes()
    return img


class MoleculePanel(Panel):
    KIND = "molecule"
    TITLE = "Molecule"
    PROPS = [
        Prop("traj", "str", "", "Trajectory", hidden=True),
        Prop("reps", "reps", DEFAULT_REPS, "Representations", hidden=True),
        Prop("rotation", "matrix", [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0], "View rotation", hidden=True),
        Prop("zoom", "float", 1.0, "Zoom", hidden=True),
        Prop("pan", "vector", [0.0, 0.0], "View shift", hidden=True),
        Prop("align", "choice", "fit to first frame", "Remove overall motion", ALIGN_MODES, section="Motion"),
        Prop("align_sel", "sel", "protein and name CA", "Fit on (blank = what is shown)", section="Motion",
             when=(("align", *ALIGN_MODES[:2]),)),
        Prop("smooth", "int", 1, "Smoothing (frames, 1 = off)", minimum=1, maximum=99, section="Motion"),
        Prop("ss_update", "choice", "every frame", "Secondary structure from", ("every frame", "first frame"),
             section="Motion"),
        Prop("quality", "int", 8, "Cartoon smoothness", minimum=2, maximum=16, section="Look"),
        Prop("depth_cue", "float", 0.3, "Depth fade", minimum=0, maximum=1, step=0.05, section="Look"),
        Prop("shine", "float", 0.35, "Shininess", minimum=0, maximum=1, step=0.05, section="Look"),
    ]

    def trajectory(self, project):
        return project.trajectories.get(self.props["traj"])

    def scene(self, project):
        from mdmovie.mol.structure import get_scene
        p = self.props
        return get_scene(project, p["traj"], p["reps"], p["align"], p["align_sel"])

    def time_info(self, project):
        traj = self.trajectory(project)
        if traj is None:
            return None
        try:
            m = traj.meta(project.resolve_path)
        except Exception:
            return None
        return (m.t0, m.t0 + (m.n_frames - 1) * m.dt, m.dt) if m.n_frames > 1 else None

    def frame_at(self, project, t: float | None) -> int:
        m = self.trajectory(project).meta(project.resolve_path)
        if t is None or m.n_frames < 2:
            return 0
        return int(min(max(round((t - m.t0) / m.dt), 0), m.n_frames - 1))

    def render(self, painter: QPainter, rect: QRectF, ctx: RenderContext) -> None:
        from mdmovie.mol.render import View, render_molecule
        project, p = ctx.project, self.props
        traj = self.trajectory(project)
        if traj is None:
            draw_message(painter, rect, "No trajectory loaded yet: there is no molecule to draw.\n"
                         "Double-click here, or use Trajectory in the toolbar, and pick a topology (+ trajectory).",
                         ctx.scale)
            return
        scene = self.scene(project)
        errors = [r.error for r in scene.reps if r.error]
        if scene.empty:
            draw_message(painter, rect, "\n".join(errors) or
                         "None of the representations selects any atoms\n(edit them in the inspector, e.g. 'all')",
                         ctx.scale, "#c33" if errors else "#888888")
            return
        frame = self.frame_at(project, ctx.time)
        w, h = max(1, round(rect.width())), max(1, round(rect.height()))
        background = p.get("background") or project.background
        key = (traj.signature(project.resolve_path), frame, w, h, background,
               json.dumps({k: p[k] for k in p if k not in ("background", "padding", "border_width", "border_color")},
                          sort_keys=True))
        img = _cached(key, lambda: render_molecule(
            scene, frame, w, h, View.from_props(p), smooth=p["smooth"],
            ss_every_frame=p["ss_update"] == "every frame", quality=p["quality"], depth_cue=p["depth_cue"],
            shine=p["shine"], background=background))
        painter.drawImage(rect.topLeft(), img)
        if errors:
            draw_message(painter, QRectF(rect.x(), rect.bottom() - 40 * ctx.scale, rect.width(), 40 * ctx.scale),
                         errors[0], ctx.scale * 0.7, "#c33")

    def reset_view(self) -> dict:
        return {"rotation": [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0], "zoom": 1.0, "pan": [0.0, 0.0]}

    @staticmethod
    def default_rep() -> dict:
        return copy.deepcopy(DEFAULT_REPS[0])
