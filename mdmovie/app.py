"""Entry point.

    mdmovie [project.mdmovie.json]                 open the GUI
    mdmovie render project.json out.mp4 [options]  render without the GUI
    mdmovie demo DIR                               build a demo project (pip install "mdmovie[demo]")
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def _ensure_gui_app(headless: bool):
    from PySide6.QtGui import QGuiApplication
    if headless and not os.environ.get("QT_QPA_PLATFORM") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    return QGuiApplication.instance() or QGuiApplication(sys.argv[:1])


def cmd_render(args) -> int:
    app = _ensure_gui_app(True)  # noqa: F841  (fonts need a QGuiApplication)
    from mdmovie.analysis.registry import load_user_presets
    from mdmovie.core.project import Project
    from mdmovie.render.exporter import ExportOptions, export_movie

    project = Project.load(args.project)
    for e in load_user_presets([os.path.join(project.base_dir, "presets")]):
        print(e, file=sys.stderr)
    opts = ExportOptions(args.output, fps=args.fps, scale=args.scale, crf=args.crf, start=args.start,
                         stop=args.stop, gif_step=args.gif_step, gif_dither=args.gif_dither)
    t0 = time.time()

    def progress(i, n, msg):
        print(f"\r{msg:<40}", end="", flush=True)

    export_movie(project, opts, progress)
    print(f"\nWrote {args.output} in {time.time() - t0:.1f} s")
    return 0


def cmd_demo(args) -> int:
    _ensure_gui_app(True)
    from mdmovie.demo import build_demo
    path = build_demo(args.dir)
    print(f"Demo project: {path}")
    return 0


def cmd_gui(project_path: str | None) -> int:
    from PySide6.QtWidgets import QApplication

    from mdmovie.ui.main_window import MainWindow
    from mdmovie.ui.theme import apply_theme
    from mdmovie.ui.widgets import settings
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("MD Movie Maker")
    app.setOrganizationName("mdmovie")
    apply_theme(app, str(settings().value("theme", "dark")))
    win = MainWindow()
    win.show()
    if project_path:
        win.open_project(project_path)
    return app.exec()


def main(argv=None) -> int:
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="MDAnalysis")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("render", "demo"):
        ap = argparse.ArgumentParser(prog="mdmovie")
        sub = ap.add_subparsers(dest="cmd", required=True)
        r = sub.add_parser("render", help="render a project to MP4/GIF/PNG without the GUI")
        r.add_argument("project")
        r.add_argument("output", help="output file (.mp4, .gif or .png for a frame sequence)")
        r.add_argument("--fps", type=float, default=None)
        r.add_argument("--scale", type=float, default=1.0, help="output size relative to the canvas")
        r.add_argument("--crf", type=int, default=18, help="H.264 quality (lower is better)")
        r.add_argument("--start", type=int, default=0)
        r.add_argument("--stop", type=int, default=None)
        r.add_argument("--gif-step", type=int, default=1)
        r.add_argument("--gif-dither", choices=("none", "floyd-steinberg"), default="none",
                       help="GIF dithering over the shared palette")
        d = sub.add_parser("demo", help="build a demo project from MDAnalysisTests data")
        d.add_argument("dir")
        args = ap.parse_args(argv)
        return cmd_render(args) if args.cmd == "render" else cmd_demo(args)
    from mdmovie import __version__
    ap = argparse.ArgumentParser(prog="mdmovie", description="MD Movie Maker",
                                 epilog="Subcommands: 'mdmovie render -h', 'mdmovie demo -h'.")
    ap.add_argument("project", nargs="?")
    ap.add_argument("--version", action="version", version=f"mdmovie {__version__}")
    args = ap.parse_args(argv)
    return cmd_gui(args.project)


if __name__ == "__main__":
    raise SystemExit(main())
