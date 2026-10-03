"""Entry point.

    mdmovie [project.mdmovie.json]                 open the GUI
    mdmovie topol.tpr traj.xtc [more.xtc …]        open the GUI with a movie of that trajectory
    mdmovie --example                              the same for the AdK example (pip install "mdmovie[demo]")
    mdmovie render project.json out.mp4 [options]  render without the GUI
    mdmovie render topol.tpr traj.xtc out.mp4      render a ready-made movie of a trajectory without the GUI
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

    *inputs, output = args.inputs
    if not inputs:
        raise SystemExit("mdmovie render: give a project (or topology + trajectory files) and an output file")
    for f in inputs:
        if not os.path.exists(f):
            raise SystemExit(f"mdmovie render: no such file: {f}")
    if len(inputs) == 1 and inputs[0].lower().endswith(".json"):
        project = Project.load(inputs[0])
    else:                                     # topology + trajectories: the quick-start movie
        from mdmovie.core.quickstart import split_files, trajectory_movie
        project = Project()
        try:
            trajectory_movie(project, *split_files(inputs))
        except Exception as e:
            raise SystemExit(f"mdmovie render: {e}")
    for e in load_user_presets([os.path.join(project.base_dir, "presets")]):
        print(e, file=sys.stderr)
    opts = ExportOptions(output, fps=args.fps, scale=args.scale, crf=args.crf, start=args.start,
                         stop=args.stop, gif_step=args.gif_step, gif_dither=args.gif_dither)
    t0 = time.time()

    def progress(i, n, msg):
        print(f"\r{msg:<40}", end="", flush=True)

    export_movie(project, opts, progress)
    print(f"\nWrote {output} in {time.time() - t0:.1f} s")
    return 0


def cmd_demo(args) -> int:
    _ensure_gui_app(True)
    from mdmovie.demo import build_demo
    path = build_demo(args.dir)
    print(f"Demo project: {path}")
    return 0


def cmd_gui(files: list[str]) -> int:
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
    if len(files) == 1 and files[0].lower().endswith(".json"):
        win.open_project(files[0])
    elif files:
        win.start_from_trajectory(files)
    _quit_on_ctrl_c(app, win)
    try:
        return app.exec()
    finally:
        win.analysis.shutdown()      # never leave the analysis thread running at exit (Qt aborts on that)


def _quit_on_ctrl_c(app, win) -> None:
    """Ctrl+C in the terminal closes the window (asking about unsaved changes); a second one quits at once."""
    import signal

    from PySide6.QtCore import QTimer
    pressed = []

    def on_sigint(*_):
        if pressed:                  # quit without asking: dismiss an open "Save changes?" (or any) dialog first
            win._confirm_discard = lambda: True
            modal = app.activeModalWidget()
            if modal is not None:
                modal.close()
            QTimer.singleShot(0, app.quit)
            return
        pressed.append(True)
        print("\nClosing… (Ctrl+C again to quit without saving)", file=sys.stderr)
        QTimer.singleShot(0, win.close)  # not from inside the handler: a dialog there would block the next Ctrl+C
    signal.signal(signal.SIGINT, on_sigint)
    tick = QTimer(app)               # Python only handles signals between Qt events: wake it up regularly
    tick.timeout.connect(lambda: None)
    tick.start(250)


def main(argv=None) -> int:
    import faulthandler
    import warnings
    faulthandler.enable()  # a crash in Qt or a C extension prints the Python stack instead of just "SIGSEGV"
    # MDAnalysis notes the app handles itself (no dt, guessed elements, DCD timestep copies…). MDAnalysis puts
    # its own filters first when it is imported, so import it before adding ours in front of them.
    import MDAnalysis  # noqa: F401
    for category in (DeprecationWarning, UserWarning):
        warnings.filterwarnings("ignore", category=category, module=r"MDAnalysis(\.|$)")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("render", "demo"):
        ap = argparse.ArgumentParser(prog="mdmovie")
        sub = ap.add_subparsers(dest="cmd", required=True)
        r = sub.add_parser("render", help="render a project to MP4/GIF/PNG without the GUI",
                           epilog="Examples: 'mdmovie render movie.mdmovie.json movie.mp4', "
                                  "'mdmovie render md.tpr md.xtc md.mp4' (molecule, RMSD, Rg and a time label).")
        r.add_argument("inputs", nargs="+", metavar="INPUT",
                       help="a project file, or topology + trajectory files; then the output file "
                            "(.mp4, .gif, or .png for a frame sequence)")
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
                                 epilog="Examples: 'mdmovie md.tpr md.xtc', 'mdmovie complex.psf run1.dcd run2.dcd'. "
                                        "Subcommands: 'mdmovie render -h', 'mdmovie demo -h'.")
    ap.add_argument("files", nargs="*", metavar="FILE",
                    help="a saved project (.mdmovie.json), or a topology plus trajectory files")
    ap.add_argument("--example", action="store_true",
                    help="open a movie of the adenylate kinase example trajectory (needs MDAnalysisTests)")
    ap.add_argument("--version", action="version", version=f"mdmovie {__version__}")
    args = ap.parse_args(argv)
    if args.example:
        try:
            from MDAnalysisTests.datafiles import DCD, PSF
        except ImportError:
            ap.error('the example trajectory comes with MDAnalysisTests: pip install "mdmovie[demo]"')
        args.files = [PSF, DCD]
    for f in args.files:
        if not os.path.exists(f):
            ap.error(f"no such file: {f}")
    return cmd_gui(args.files)


if __name__ == "__main__":
    raise SystemExit(main())
