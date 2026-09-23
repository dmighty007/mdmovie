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

    writer, gif_frames = None, []
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
            img = render_frame(project, g, opts.scale)
            if fmt == "mp4":
                writer.append_data(qimage_to_rgb(img))
            elif fmt == "gif":
                from PIL import Image
                gif_frames.append(Image.fromarray(qimage_to_rgb(img)).quantize(
                    colors=opts.gif_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG))
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
