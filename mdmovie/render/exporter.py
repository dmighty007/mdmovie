"""Export the movie to MP4 (H.264 via imageio-ffmpeg), GIF (Pillow) or a PNG sequence."""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QImage

from mdmovie.analysis import runner
from mdmovie.analysis.registry import Cancelled
from mdmovie.render.compositor import render_frame


@dataclass
class ExportOptions:
    path: str
    fmt: str = ""              # mp4 | gif | png; blank = from the file extension
    fps: float | None = None   # None = project fps
    scale: float = 1.0         # output size relative to the project canvas
    crf: int = 18              # H.264 quality: 0 lossless … 51 worst; 18 ≈ visually lossless
    preset: str = "medium"     # x264 speed/size trade-off
    start: int = 0
    stop: int | None = None    # exclusive; None = end of movie
    gif_step: int = 1          # keep every n-th frame for GIFs
    gif_colors: int = 256
    gif_dither: str = "none"   # none | floyd-steinberg (smoother gradients, but noise that shimmers)

    @property
    def format(self) -> str:
        if self.fmt:
            return self.fmt
        ext = os.path.splitext(self.path)[1].lower().lstrip(".")
        return {"gif": "gif", "png": "png"}.get(ext, "mp4")


def qimage_to_rgb(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())
    return buf[:, : w * 3].reshape(h, w, 3).copy()


GIF_PALETTE_SAMPLES = 16
GIF_DITHER = ("none", "floyd-steinberg")


class GifPalette:
    """One palette for the whole GIF, with exact nearest-colour mapping.

    A shared palette keeps unchanged pixels identical between frames: no colour flicker, and the GIF
    writer only stores the rectangle that changed. (Pillow's own palette lookup goes through a coarse
    colour grid and is off by up to ~5 levels, e.g. it turns pure white into 250, so mapping is done
    here, remembering the entry for each colour already seen.)"""

    def __init__(self, colors: np.ndarray):
        self.colors = np.asarray(colors, np.uint8).reshape(-1, 3)
        self._lut = np.full(1 << 24, -1, np.int16)   # packed RGB → palette index, -1 = not computed

    @classmethod
    def fit(cls, frames_rgb: list[np.ndarray], colors: int = 256, iters: int = 4, max_px: int = 400_000):
        """Fitted to pixels sampled from `frames_rgb`: an octree palette, then a few k-means (Lloyd) steps
        that move each entry to the mean of its pixels. That keeps faint colours, e.g. light grid lines,
        that octree alone merges into white. An entry whose pixels are mostly one exact colour (flat
        background, text, grid) snaps to that colour instead of the mean, so white stays pure white."""
        from PIL import Image
        colors = max(2, min(256, int(colors)))
        px = np.concatenate([a.reshape(-1, 3) for a in frames_rgb])
        if len(px) > max_px:
            px = px[np.random.default_rng(0).choice(len(px), max_px, replace=False)]
        side = max(1, int(np.sqrt(len(px))))
        px = px[: side * side]
        octree = Image.fromarray(px.reshape(side, side, 3)).quantize(colors, method=Image.Quantize.FASTOCTREE,
                                                                       dither=Image.Dither.NONE)
        pal = cls(np.array(octree.getpalette()[: 3 * colors]))
        key = _pack(px)
        for _ in range(iters):
            label = pal.indices(px)
            n = np.bincount(label, minlength=len(pal.colors))
            used = n > 0
            centres = pal.colors.astype(float)
            for c in range(3):
                centres[used, c] = np.bincount(label, weights=px[:, c], minlength=len(centres))[used] / n[used]
            # most common exact colour of each entry (pairs sorted by entry, then by count)
            pairs, cnt = np.unique((label.astype(np.int64) << 24) | key, return_counts=True)
            order = np.lexsort((cnt, pairs >> 24))
            last = np.r_[(pairs[order] >> 24)[1:] != (pairs[order] >> 24)[:-1], True]
            top, top_n = pairs[order][last], cnt[order][last]
            ent, col = top >> 24, top & 0xFFFFFF
            flat = top_n > 0.5 * n[ent]
            centres[ent[flat]] = _unpack(col[flat])
            pal = cls(np.clip(np.rint(centres), 0, 255))
        return pal

    def indices(self, rgb: np.ndarray) -> np.ndarray:
        """Index of the nearest palette colour for each pixel (same shape without the channel axis)."""
        key = _pack(rgb.reshape(-1, 3))
        idx = self._lut[key]
        todo = np.unique(key[idx < 0])
        pal = self.colors.astype(np.int32)
        for i in range(0, len(todo), 16384):
            chunk = todo[i:i + 16384]
            d = ((_unpack(chunk).astype(np.int32)[:, None, :] - pal[None]) ** 2).sum(axis=2)
            self._lut[chunk] = d.argmin(axis=1)
        if len(todo):
            idx = self._lut[key]
        return idx.astype(np.uint8).reshape(rgb.shape[:-1])

    def image(self, rgb: np.ndarray, dither: str = "none"):
        """The frame as a "P" image on this palette."""
        from PIL import Image
        if dither == "floyd-steinberg":  # Pillow's dithering (its approximate lookup is lost in the noise)
            return Image.fromarray(rgb).quantize(palette=self._pil(), dither=Image.Dither.FLOYDSTEINBERG)
        im = Image.fromarray(self.indices(rgb), "P")
        im.putpalette(self.colors.flatten().tolist())
        return im

    def _pil(self):
        from PIL import Image
        im = Image.new("P", (1, 1))
        im.putpalette(self.colors.flatten().tolist())
        return im


def _pack(px: np.ndarray) -> np.ndarray:
    return (px[:, 0].astype(np.int64) << 16) | (px[:, 1].astype(np.int64) << 8) | px[:, 2]


def _unpack(key: np.ndarray) -> np.ndarray:
    return np.stack([(key >> 16) & 255, (key >> 8) & 255, key & 255], axis=1)


def export_movie(project, opts: ExportOptions, progress=None, cancelled=None) -> str:
    """Render every frame and write the file. Works from any thread: renders a private clone.

    progress(done, total, message) is called along the way; cancelled() aborts with Cancelled.
    """
    project = project.clone()
    say = progress or (lambda *a: None)
    errs = runner.compute_all(project, lambda i, n: say(0, 1, f"Analysis {i}/{n}"), cancelled)
    if errs:
        say(0, 1, "Analysis errors: " + "; ".join(errs))

    n = project.n_frames
    start = max(0, opts.start)
    stop = n if opts.stop is None else min(opts.stop, n)
    step = opts.gif_step if opts.format == "gif" else 1
    frames = list(range(start, stop, max(1, step)))
    fps = opts.fps or project.fps
    fmt = opts.format
    os.makedirs(os.path.dirname(os.path.abspath(opts.path)) or ".", exist_ok=True)

    writer, gif_frames, gif_pal, rendered = None, [], None, {}
    if fmt == "gif" and frames:
        picks = sorted({frames[round(i)] for i in np.linspace(0, len(frames) - 1, GIF_PALETTE_SAMPLES)})
        for i, g in enumerate(picks):
            if cancelled and cancelled():
                raise Cancelled()
            rendered[g] = qimage_to_rgb(render_frame(project, g, opts.scale))  # reused below
            say(0, 1, f"GIF palette: frame {i + 1}/{len(picks)}")
        gif_pal = GifPalette.fit(list(rendered.values()), opts.gif_colors)
    if fmt == "mp4":
        import imageio.v2 as imageio
        writer = imageio.get_writer(opts.path, format="FFMPEG", mode="I", fps=fps, codec="libx264",
                                    pixelformat="yuv420p", quality=None, macro_block_size=2,
                                    ffmpeg_params=["-crf", str(int(opts.crf)), "-preset", opts.preset,
                                                   "-movflags", "+faststart"],
                                    ffmpeg_log_level="error")
    try:
        for k, g in enumerate(frames):
            if cancelled and cancelled():
                raise Cancelled()
            if fmt == "gif":
                rgb = rendered.pop(g, None)
                if rgb is None:
                    rgb = qimage_to_rgb(render_frame(project, g, opts.scale))
                gif_frames.append(gif_pal.image(rgb, opts.gif_dither))
            else:
                img = render_frame(project, g, opts.scale)
                if fmt == "mp4":
                    writer.append_data(qimage_to_rgb(img))
                else:
                    base, _ = os.path.splitext(opts.path)
                    img.save(f"{base}_{g:05d}.png")
            say(k + 1, len(frames), f"Frame {k + 1}/{len(frames)}")
        if fmt == "gif" and gif_frames:
            gif_frames[0].save(opts.path, save_all=True, append_images=gif_frames[1:], loop=0,
                               duration=max(20, round(1000 * step / fps)), disposal=1, optimize=False)
    except BaseException:
        if writer is not None:
            writer.close()
            writer = None
            if fmt == "mp4" and os.path.exists(opts.path):
                os.remove(opts.path)
        raise
    finally:
        if writer is not None:
            writer.close()
    return opts.path


def export_frame_png(project, gframe: int, path: str, scale: float = 1.0) -> str:
    render_frame(project, gframe, scale).save(path)
    return path
