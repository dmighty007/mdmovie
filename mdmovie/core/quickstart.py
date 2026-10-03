"""A ready-made movie from a topology and trajectory files: the molecule drawn from the trajectory next to
RMSD and radius-of-gyration plots, with a time label. Used by `mdmovie topol.gro traj.xtc`, by dropping
files on the window and by "Start from a trajectory" on the welcome card."""
from __future__ import annotations

import math
import os

TOPOLOGY_EXT = (".tpr", ".psf", ".prmtop", ".parm7", ".top", ".gro", ".pdb", ".mol2", ".pqr", ".data", ".crd")
TRAJECTORY_EXT = (".xtc", ".trr", ".dcd", ".nc", ".netcdf", ".ncdf", ".mdcrd", ".lammpstrj", ".xyz", ".h5", ".trz")
PROJECT_EXT = (".json",)
MAX_SECONDS = 30.0      # a long trajectory is played faster so the movie stays watchable,
MIN_SECONDS = 5.0       # a short one slower, so it is not over in a blink


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def is_md_file(path: str) -> bool:
    return _ext(path) in TOPOLOGY_EXT + TRAJECTORY_EXT


def split_files(paths: list[str]) -> tuple[str, list[str]]:
    """(topology, trajectories) from files given in any order. The topology is the file with the richest
    topology format (a .tpr or .psf beats a .gro or .pdb); every trajectory-format file is a trajectory,
    concatenated in the order given. A lone .pdb/.gro is a single-frame structure."""
    paths = [os.path.abspath(os.path.expanduser(p)) for p in paths]
    tops = [p for p in paths if _ext(p) in TOPOLOGY_EXT]
    trajs = [p for p in paths if _ext(p) in TRAJECTORY_EXT]
    unknown = [p for p in paths if p not in tops and p not in trajs]
    if unknown:
        raise ValueError("Not a topology or trajectory file: " + ", ".join(os.path.basename(p) for p in unknown))
    if not tops:
        raise ValueError("A topology is needed too (.tpr, .psf, .prmtop, .gro, .pdb…): trajectory files store "
                         "only coordinates.")
    top = min(tops, key=lambda p: TOPOLOGY_EXT.index(_ext(p)))
    # a second structure file (e.g. a .gro next to a .tpr) is read as extra frames only if it is the only one
    extra = [p for p in tops if p != top]
    return top, (trajs or extra)


def _selections(u) -> tuple[str, str] | None:
    """What RMSD and Rg are computed on: the backbone and the whole of the main polymer."""
    if len(u.select_atoms("protein")):
        return "backbone", "protein"
    if len(u.select_atoms("nucleic")):
        return "nucleicbackbone", "nucleic"
    from mdmovie.sources.pbc import SOLVENT_AND_IONS
    solute = f"not (water or resname {SOLVENT_AND_IONS})"
    if len(u.select_atoms(solute)) >= 3:
        heavy = f"{solute} and not name H*"
        return heavy, heavy
    return None


def _time_label(meta) -> str:
    if not meta.timed:
        return "frame {frame}"
    total = (meta.n_frames - 1) * meta.dt + meta.t0
    if total >= 1e6:
        return "t = {t_us:.3f} µs"
    if total >= 1e3:
        decimals = max(0, min(3, math.ceil(-math.log10(meta.dt / 1e3)))) if meta.dt > 0 else 1
        return "t = {t_ns:.%df} ns" % decimals
    return "t = {t_ps:.0f} ps"


def trajectory_movie(pr, topology: str, trajectories: list[str], name: str = "", dt: float = 0.0,
                     whole: bool = True) -> str:
    """Add the trajectory and a molecule + plots + time label layout to `pr` (whose layout and overlays
    are replaced). Returns the trajectory id. Raises if the files cannot be read."""
    from mdmovie.core import layout as L
    from mdmovie.core.project import Overlay
    from mdmovie.panels import Series

    t = pr.add_trajectory(topology, trajectories, name)
    t.dt, t.whole = dt, whole
    try:
        meta = t.meta(pr.resolve_path)
        sels = _selections(t.universe(pr.resolve_path)) if meta.n_frames > 1 else None
    except Exception:
        pr.trajectories.pop(t.id, None)
        raise

    mol = pr.add_panel("molecule", t.name)
    mol.props["traj"] = t.id
    leaves = []
    if sels:
        fit, whole_sel = sels
        rmsd = pr.add_panel("plot", "RMSD")
        rmsd.series = [Series(t.id, "RMSD", {"select": fit}, label=f"RMSD ({fit})")]
        rmsd.props.update(mode="reveal", show_value=True, value_format="{value:.2f} Å", title="RMSD")
        rg = pr.add_panel("plot", "Radius of gyration")
        rg.series = [Series(t.id, "Radius of gyration", {"select": whole_sel}, label="Rg", color="#2ca02c")]
        rg.props.update(mode="reveal", show_value=True, value_format="{value:.2f} Å", title="Radius of gyration")
        for plot in (rmsd, rg):               # legible on a projector and in a shrunk GIF
            plot.props.update(font_size=20.0, line_width=3.0)
        leaves = [L.Leaf(rmsd.id), L.Leaf(rg.id)]
    pr.layout = (L.Split("h", [0.56, 0.44], [L.Leaf(mol.id), L.Split("v", [0.5, 0.5], leaves)]) if leaves
                 else L.Leaf(mol.id))

    label = pr.add_panel("text", "Time label")
    label.props.update(text=_time_label(meta), font_size=40, bold=True, align="left", valign="top")
    pr.overlays = [Overlay(label.id, 0.015, 0.02, 0.3, 0.08)]

    seconds = meta.n_frames / max(pr.fps, 1.0)
    if meta.n_frames > 1 and not MIN_SECONDS <= seconds <= MAX_SECONDS:   # 10 000 frames: 30 s, not 5.5 min
        target = min(max(seconds, MIN_SECONDS), MAX_SECONDS)
        pr.groups[mol.group].speed = meta.dt * (meta.n_frames - 1) / (target * pr.fps)
    return t.id
