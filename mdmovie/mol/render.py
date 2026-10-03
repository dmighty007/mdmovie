"""Software rasteriser for molecules: shaded quads (cartoons), sphere sprites and stick lines are
depth-sorted and painted back to front with QPainter (orthographic camera, one light).

Sizes are in ångström and scaled by the camera, so the picture is the same at any resolution."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from mdmovie.mol.cartoon import NUCLEIC_RADIUS, chain_mesh
from mdmovie.mol.structure import SS_COLORS, Scene, hex_rgb

LIGHT = np.array([-0.35, 0.45, 0.82]) / np.linalg.norm([-0.35, 0.45, 0.82])     # camera space, +z = viewer
HALF = (LIGHT + [0, 0, 1]) / np.linalg.norm(LIGHT + [0, 0, 1])
AMBIENT, DIFFUSE = 0.42, 0.62
FOG_LEVELS = 8
SPRITE = 96
STICK_RADIUS = {"licorice": 0.24, "ball and stick": 0.13}
BALL_SCALE = 0.26         # ball radius = this × van der Waals radius


@dataclass
class View:
    rotation: np.ndarray = field(default_factory=lambda: np.eye(3))    # world → camera
    zoom: float = 1.0                                                  # 1 = the molecule fills the panel
    pan: tuple[float, float] = (0.0, 0.0)                              # ångström, in the camera plane

    @classmethod
    def from_props(cls, p: dict) -> "View":
        rot = np.asarray(p.get("rotation") or np.eye(3).ravel(), float).reshape(3, 3)
        return cls(rot, float(p.get("zoom") or 1.0), tuple(p.get("pan") or (0.0, 0.0)))


def rotated(rotation, dx: float, dy: float) -> list[float]:
    """Rotation after turning by dx about the screen's vertical and dy about its horizontal axis (radians)."""
    cx, sx, cy, sy = np.cos(dy), np.sin(dy), np.cos(dx), np.sin(dx)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    r = rx @ ry @ np.asarray(rotation, float).reshape(3, 3)
    u, _, vt = np.linalg.svd(r)                       # keep it a pure rotation after many small steps
    return [round(float(v), 6) for v in (u @ vt).ravel()]


AXIS_VIEWS = {
    "front": np.eye(3),
    "side": np.array([[0.0, 0, -1], [0, 1, 0], [1, 0, 0]]),
    "top": np.array([[1.0, 0, 0], [0, 0, -1], [0, 1, 0]]),
}


@lru_cache(maxsize=4096)
def _sprite(rgb: tuple[int, int, int], fog: int, fog_rgb: tuple[int, int, int], shine: float) -> QImage:
    """A lit sphere of this colour, mixed towards the background colour by fog/FOG_LEVELS."""
    y, x = (np.mgrid[0:SPRITE, 0:SPRITE] + 0.5) / (SPRITE / 2) - 1.0
    r2 = x * x + y * y
    z = np.sqrt(np.clip(1 - r2, 0, 1))
    n = np.stack([x, -y, z], axis=-1)
    light = AMBIENT + DIFFUSE * np.clip(n @ LIGHT, 0, 1)
    light *= 0.62 + 0.38 * z ** 0.6                                   # darker rim: reads as round
    spec = shine * np.clip(n @ HALF, 0, 1) ** 36
    col = np.asarray(rgb, float)[None, None] / 255 * light[..., None] + spec[..., None]
    f = fog / FOG_LEVELS
    col = (1 - f) * col + f * np.asarray(fog_rgb, float) / 255
    alpha = np.clip((1 - np.sqrt(r2)) * SPRITE / 2, 0, 1)
    out = np.zeros((SPRITE, SPRITE, 4), np.uint8)
    out[..., :3] = (np.clip(col, 0, 1) * alpha[..., None] * 255)[..., ::-1]      # premultiplied BGRA
    out[..., 3] = alpha * 255
    img = QImage(out.tobytes(), SPRITE, SPRITE, QImage.Format.Format_ARGB32_Premultiplied)
    return img.copy()


def _fog(z: np.ndarray, radius: float, depth_cue: float) -> np.ndarray:
    """0 (nearest) .. depth_cue (farthest) for camera depths z measured from the scene centre."""
    return depth_cue * np.clip((radius - z) / (2 * radius), 0, 1)


def render_molecule(scene: Scene, frame: int, w: int, h: int, view: View, *, smooth: int = 1,
                    ss_every_frame: bool = True, quality: int = 6, depth_cue: float = 0.3,
                    shine: float = 0.35, background: str = "#ffffff") -> QImage:
    """The scene at one trajectory frame as a transparent w × h image."""
    img = QImage(max(1, w), max(1, h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    if scene.empty:
        return img
    pos = scene.positions(frame, smooth)
    cam = (pos - scene.centre) @ view.rotation.T          # camera coordinates of every atom
    px = view.zoom * 0.46 * min(w, h) / scene.radius      # pixels per ångström
    ox, oy = w / 2 + view.pan[0] * px, h / 2 - view.pan[1] * px
    bg = hex_rgb(background)
    bg255 = tuple(int(round(v * 255)) for v in bg)

    depth, kinds, items = [], [], []       # one entry per primitive: sort key, type, drawing data

    # --- cartoon / tube meshes ----------------------------------------------------------------
    quads, qcol = [], []
    # secondary structure of this frame (smoothed like the coordinates), or the first frame's throughout
    ss = scene.ss_at(frame, smooth) if ss_every_frame else scene.ss(0)
    for rep in scene.reps:
        if rep.style not in ("cartoon", "tube"):
            continue
        tube = rep.style == "tube"
        for ch in rep.chains:
            colors = ch.colors
            codes = np.zeros(len(ch.trace), int)
            if not tube and not ch.nucleic and len(ss):
                codes = np.where(ch.rows >= 0, ss[np.maximum(ch.rows, 0)], 0)
            if rep.scheme == "secondary structure":
                colors = np.array([hex_rgb(c) for c in SS_COLORS])[codes]
            q, c = chain_mesh(cam[ch.trace], None if ch.oxygen is None else cam[ch.oxygen], codes, colors,
                              quality, rep.scale, tube, NUCLEIC_RADIUS * rep.scale if ch.nucleic else None)
            quads.append(q)
            qcol.append(c)
    if quads:
        q = np.concatenate(quads)
        c = np.concatenate(qcol)
        normal = np.cross(q[:, 2] - q[:, 0], q[:, 3] - q[:, 1])
        length = np.linalg.norm(normal, axis=1)
        front = (normal[:, 2] > 0) & (length > 1e-12)
        q, c, normal = q[front], c[front], normal[front] / length[front, None]
        z = q[:, :, 2].mean(axis=1)
        light = AMBIENT + DIFFUSE * np.clip(normal @ LIGHT, 0, 1)
        spec = shine * 0.6 * np.clip(normal @ HALF, 0, 1) ** 24
        rgb = c * light[:, None] + spec[:, None]
        f = _fog(z, scene.radius, depth_cue)[:, None]
        rgb = np.clip((1 - f) * rgb + f * bg, 0, 1)
        rgb255 = np.rint(rgb * 255).astype(int).tolist()
        sx = (ox + q[:, :, 0] * px).tolist()
        sy = (oy - q[:, :, 1] * px).tolist()
        for xs, ys, col in zip(sx, sy, rgb255):
            color = QColor(*col)
            items.append(([QPointF(xs[0], ys[0]), QPointF(xs[1], ys[1]), QPointF(xs[2], ys[2]),
                           QPointF(xs[3], ys[3])], color))
        depth.append(z)
        kinds.append(np.zeros(len(z), int))

    # --- atoms and bonds ------------------------------------------------------------------------
    for rep in scene.reps:
        if rep.style in ("cartoon", "tube") or not len(rep.index):
            continue
        p = cam[rep.index]
        colors = scene.ss_colors(rep.residues, ss) if rep.scheme == "secondary structure" else rep.colors
        fog = _fog(p[:, 2], scene.radius, depth_cue)
        if rep.style in ("spheres", "ball and stick"):
            radius = rep.radii * rep.scale * (BALL_SCALE if rep.style == "ball and stick" else 1.0)
            level = np.rint(fog * FOG_LEVELS).astype(int).tolist()
            keys = (np.rint(colors * 31) * 255 // 31).astype(int).tolist()   # 5 bits a channel bounds the cache
            x0, y0, d = ox + (p[:, 0] - radius) * px, oy - (p[:, 1] + radius) * px, 2 * radius * px
            for xx, yy, dd, key, lv in zip(x0.tolist(), y0.tolist(), d.tolist(), keys, level):
                items.append((QRectF(xx, yy, dd, dd), _sprite(tuple(key), lv, bg255, shine)))
            depth.append(p[:, 2])
            kinds.append(np.ones(len(p), int))
        if rep.style != "spheres" and len(rep.bonds):
            i, j = rep.bonds[:, 0], rep.bonds[:, 1]
            mid = 0.5 * (p[i] + p[j])
            for a, b in ((i, j), (j, i)):                       # each half takes the colour of its atom
                start = p[a]
                if rep.style == "ball and stick":               # start at the ball's surface, not its centre
                    ball = (rep.radii[a] * rep.scale * BALL_SCALE)[:, None]
                    start = start + _unit(p[b] - start) * ball * 0.85
                seg_mid = 0.5 * (start + mid)
                sfog = _fog(seg_mid[:, 2], scene.radius, depth_cue)[:, None]
                base = colors[a]
                x1, y1 = ox + start[:, 0] * px, oy - start[:, 1] * px
                x2, y2 = ox + mid[:, 0] * px, oy - mid[:, 1] * px
                if rep.style == "lines":
                    rgb = np.rint(np.clip((1 - sfog) * base + sfog * bg, 0, 1) * 255).astype(int).tolist()
                    width = max(1.0, 0.09 * rep.scale * px)
                    for xa, ya, xb, yb, col in zip(x1.tolist(), y1.tolist(), x2.tolist(), y2.tolist(), rgb):
                        items.append(((QLineF(xa, ya, xb, yb),), (_pen(col, width),)))
                else:
                    width = 2 * STICK_RADIUS[rep.style] * rep.scale * px
                    # a cylinder seen from the side: dark body, lighter core shifted towards the light
                    dx, dy = x2 - x1, y2 - y1
                    norm = np.hypot(dx, dy)
                    norm = np.where(norm > 1e-9, norm, 1.0)
                    nx, ny = -dy / norm, dx / norm
                    side = np.sign(nx * LIGHT[0] - ny * LIGHT[1])
                    side = np.where(side == 0, 1.0, side)
                    sx_, sy_ = nx * side * width * 0.16, ny * side * width * 0.16
                    tones = [np.rint(np.clip((1 - sfog) * np.clip(base * k + add, 0, 1) + sfog * bg, 0, 1) * 255)
                             .astype(int).tolist() for k, add in ((0.5, 0.0), (0.86, 0.0), (1.0, 0.25 * shine + 0.06))]
                    rows = zip(x1.tolist(), y1.tolist(), x2.tolist(), y2.tolist(), sx_.tolist(), sy_.tolist(), *tones)
                    for xa, ya, xb, yb, ex, ey, c0, c1, c2 in rows:
                        items.append(((QLineF(xa, ya, xb, yb),
                                       QLineF(xa + ex, ya + ey, xb + ex, yb + ey),
                                       QLineF(xa + 1.7 * ex, ya + 1.7 * ey, xb + 1.7 * ex, yb + 1.7 * ey)),
                                      (_pen(c0, width), _pen(c1, width * 0.62), _pen(c2, width * 0.22))))
                depth.append(seg_mid[:, 2])
                kinds.append(np.full(len(seg_mid), 2))

    if not items:
        return img
    order = np.argsort(np.concatenate(depth), kind="stable").tolist()
    kind = np.concatenate(kinds).tolist()
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    no_pen = QPen(Qt.PenStyle.NoPen)
    seam = max(0.6, min(1.2, px * 0.05))        # quads are stroked in their own colour: no antialiasing seams
    try:
        for k in order:
            t = kind[k]
            data, style = items[k]
            if t == 0:
                painter.setPen(QPen(style, seam))
                painter.setBrush(style)
                painter.drawConvexPolygon(data)
            elif t == 1:
                painter.setPen(no_pen)
                painter.drawImage(data, style)
            else:
                for line, pen in zip(data, style):
                    painter.setPen(pen)
                    painter.drawLine(line)
    finally:
        painter.end()
    return img


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n > 1e-9, n, 1.0)


def _pen(rgb, width: float) -> QPen:
    pen = QPen(QColor(*rgb), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen
