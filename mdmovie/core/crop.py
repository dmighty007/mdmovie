"""Crop rectangle math (pixel space) with optional aspect-ratio lock.

Rectangles are (x, y, w, h) in image pixels. Stored crops are normalized by the
image size so a crop keeps working if the frames are re-rendered at another
resolution.
"""
from __future__ import annotations

import numpy as np

Rect = tuple[float, float, float, float]
MIN_SIZE = 8.0

# Handles: corners and edges, named by compass direction; "move" drags the whole rect.
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")

ASPECT_PRESETS = {
    "Free": None,
    "1:1": 1.0,
    "4:3": 4 / 3,
    "3:4": 3 / 4,
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "3:2": 3 / 2,
    "2:3": 2 / 3,
}


def normalize(rect: Rect, img_w: int, img_h: int) -> list[float]:
    x, y, w, h = rect
    return [x / img_w, y / img_h, w / img_w, h / img_h]


def denormalize(crop, img_w: int, img_h: int) -> Rect:
    if not crop:
        return (0.0, 0.0, float(img_w), float(img_h))
    x, y, w, h = crop
    return (x * img_w, y * img_h, w * img_w, h * img_h)


def to_int_rect(rect: Rect, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    x, y, w, h = rect
    x0 = int(round(max(0, min(x, img_w - 1))))
    y0 = int(round(max(0, min(y, img_h - 1))))
    x1 = int(round(max(x0 + 1, min(x + w, img_w))))
    y1 = int(round(max(y0 + 1, min(y + h, img_h))))
    return x0, y0, x1 - x0, y1 - y0


def fit_aspect(rect: Rect, aspect: float | None, bounds: tuple[float, float]) -> Rect:
    """Largest rect of the given aspect centred on `rect`'s centre, no bigger than rect's area
    along its limiting dimension, and inside bounds (W, H)."""
    if not aspect:
        return clamp_rect(rect, bounds)
    bw, bh = bounds
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    if w / h > aspect:
        w = h * aspect
    else:
        h = w / aspect
    # shrink further if the centred rect would leave the image
    max_w = 2 * min(cx, bw - cx)
    max_h = 2 * min(cy, bh - cy)
    if w > max_w:
        w, h = max_w, max_w / aspect
    if h > max_h:
        h, w = max_h, max_h * aspect
    if w < MIN_SIZE or h < MIN_SIZE:  # centre too close to an edge: fit the whole image instead
        w, h = (bh * aspect, bh) if bw / bh > aspect else (bw, bw / aspect)
        cx = min(max(cx, w / 2), bw - w / 2)
        cy = min(max(cy, h / 2), bh - h / 2)
    return (cx - w / 2, cy - h / 2, w, h)


def expand_to_aspect(rect: Rect, aspect: float | None, bounds: tuple[float, float]) -> Rect:
    """Grow `rect` (never shrink) to the aspect, then shift it inside bounds."""
    if not aspect:
        return clamp_rect(rect, bounds)
    bw, bh = bounds
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    if w / h > aspect:
        h = w / aspect
    else:
        w = h * aspect
    if w > bw:
        w, h = bw, bw / aspect
    if h > bh:
        h, w = bh, bh * aspect
    x = min(max(cx - w / 2, 0), bw - w)
    y = min(max(cy - h / 2, 0), bh - h)
    return (x, y, w, h)


def clamp_rect(rect: Rect, bounds: tuple[float, float]) -> Rect:
    bw, bh = bounds
    x, y, w, h = rect
    w = min(max(w, MIN_SIZE), bw)
    h = min(max(h, MIN_SIZE), bh)
    x = min(max(x, 0), bw - w)
    y = min(max(y, 0), bh - h)
    return (x, y, w, h)


def move_rect(rect: Rect, dx: float, dy: float, bounds: tuple[float, float]) -> Rect:
    x, y, w, h = rect
    return clamp_rect((x + dx, y + dy, w, h), bounds)


def resize_rect(rect: Rect, handle: str, px: float, py: float, aspect: float | None,
                bounds: tuple[float, float]) -> Rect:
    """Drag `handle` of `rect` to image point (px, py), keeping the opposite side fixed."""
    bw, bh = bounds
    px = min(max(px, 0), bw)
    py = min(max(py, 0), bh)
    x0, y0, w, h = rect
    x1, y1 = x0 + w, y0 + h

    if len(handle) == 2:  # corner: the opposite corner is the anchor
        ax = x1 if "w" in handle else x0
        ay = y1 if "n" in handle else y0
        sx = -1 if "w" in handle else 1
        sy = -1 if "n" in handle else 1
        nw = max(abs(px - ax), MIN_SIZE)
        nh = max(abs(py - ay), MIN_SIZE)
        max_w = ax if sx < 0 else bw - ax
        max_h = ay if sy < 0 else bh - ay
        if aspect:
            nw = max(nw, nh * aspect)  # follow whichever direction the user dragged furthest
            nw = min(nw, max_w, max_h * aspect)
            nh = nw / aspect
        else:
            nw, nh = min(nw, max_w), min(nh, max_h)
        nx = ax - nw if sx < 0 else ax
        ny = ay - nh if sy < 0 else ay
        return (nx, ny, nw, nh)

    # edge handle: move one side; with an aspect lock the other axis grows symmetrically
    if handle in ("e", "w"):
        ax = x0 if handle == "e" else x1
        nw = max(abs(px - ax), MIN_SIZE)
        nw = min(nw, bw - ax if handle == "e" else ax)
        nx = ax if handle == "e" else ax - nw
        if not aspect:
            return (nx, y0, nw, h)
        cy = y0 + h / 2
        nw = min(nw, 2 * min(cy, bh - cy) * aspect)
        nh = nw / aspect
        nx = ax if handle == "e" else ax - nw
        return (nx, cy - nh / 2, nw, nh)
    ay = y0 if handle == "s" else y1
    nh = max(abs(py - ay), MIN_SIZE)
    nh = min(nh, bh - ay if handle == "s" else ay)
    ny = ay if handle == "s" else ay - nh
    if not aspect:
        return (x0, ny, w, nh)
    cx = x0 + w / 2
    nh = min(nh, 2 * min(cx, bw - cx) / aspect)
    nw = nh * aspect
    ny = ay if handle == "s" else ay - nh
    return (cx - nw / 2, ny, nw, nh)


def content_bbox(rgb: np.ndarray, tol: int = 12, bg=None) -> Rect | None:
    """Bounding box of pixels that differ from the background colour (default: top-left pixel)."""
    arr = rgb[..., :3].astype(np.int16)
    bgc = np.asarray(bg if bg is not None else arr[0, 0], dtype=np.int16)
    mask = np.abs(arr - bgc).max(axis=-1) > tol
    if rgb.shape[-1] == 4:  # transparent pixels are background too
        mask &= rgb[..., 3] > tol
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return (float(xs.min()), float(ys.min()), float(xs.max() - xs.min() + 1), float(ys.max() - ys.min() + 1))


def auto_trim(frames: list[np.ndarray], tol: int = 12, margin: float = 0.02,
              aspect: float | None = None) -> Rect | None:
    """Union of content bounding boxes over sample frames, padded by `margin` and fitted to `aspect`."""
    boxes = [b for b in (content_bbox(f, tol) for f in frames) if b]
    if not boxes:
        return None
    h_img, w_img = frames[0].shape[:2]
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    pad = margin * max(x1 - x0, y1 - y0)
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(w_img, x1 + pad), min(h_img, y1 + pad)
    return expand_to_aspect((x0, y0, x1 - x0, y1 - y0), aspect, (w_img, h_img))
