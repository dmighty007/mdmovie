"""Build a demo project from the MDAnalysisTests adenylate kinase trajectory (PSF/DCD).

The "protein" images are simple CA-trace drawings rendered with matplotlib, standing in
for frames you would normally render with VMD, PyMOL or ChimeraX.
"""
from __future__ import annotations

import os


def render_ca_frames(u, out_dir: str, size=(900, 700), view=(45, 35)) -> int:
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    os.makedirs(out_dir, exist_ok=True)
    ca = u.select_atoms("name CA")
    u.trajectory[0]
    center = ca.center_of_geometry()
    fig = Figure(figsize=(9, 9 * size[1] / size[0]), dpi=size[0] / 9)  # same look at any pixel size
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    for ts in u.trajectory:
        ax.clear()
        ax.set_axis_off()
        ax.set_facecolor("white")
        xyz = ca.positions - center
        ax.plot(xyz[:, 0], xyz[:, 1], "-", color="#555", lw=1.2, zorder=1)
        ax.scatter(xyz[:, 0], xyz[:, 1], c=np.arange(len(xyz)), cmap="turbo", s=14, zorder=2)
        ax.set_xlim(-view[0], view[0])
        ax.set_ylim(-view[1], view[1])
        ax.set_aspect("equal")
        canvas.draw()
        canvas.print_png(os.path.join(out_dir, f"frame.{ts.frame:05d}.png"))
    return u.trajectory.n_frames


def write_toy_colvar(path: str, n: int = 600, stride: int = 25, seed: int = 3) -> str:
    """A PLUMED-style COLVAR of two CVs from overdamped Langevin dynamics on a 2-D double well
    (two basins joined by a ~3 kT barrier): a stand-in for real collective-variable data."""
    import numpy as np
    rng = np.random.default_rng(seed)

    def force(x, y):   # −∇V for V = 2.5 (x² − 1)² + 1.5 (y − 0.6 x)²
        fx = -10 * x * (x * x - 1) + 1.8 * (y - 0.6 * x)
        fy = -3 * (y - 0.6 * x)
        return fx, fy
    dt, x, y = 0.004, -1.0, -0.6
    rows = []
    for i in range(n * stride):
        fx, fy = force(x, y)
        x += fx * dt + np.sqrt(2 * dt) * rng.normal()
        y += fy * dt + np.sqrt(2 * dt) * rng.normal()
        if i % stride == 0:
            rows.append((len(rows) * 10.0, x, y))   # one sample every 10 ps
    with open(path, "w") as f:
        f.write("#! FIELDS time cv1 cv2\n")
        for r in rows:
            f.write(f"{r[0]:.1f} {r[1]:.5f} {r[2]:.5f}\n")
    return path


def build_demo(out_dir: str) -> str:
    """Write demo images and a project file into out_dir; returns the project path."""
    import MDAnalysis as mda
    from MDAnalysisTests.datafiles import DCD, PSF

    from mdmovie.core import layout as L
    from mdmovie.core.project import Overlay, Project
    from mdmovie.panels import Series

    out_dir = os.path.abspath(out_dir)
    img_dir = os.path.join(out_dir, "frames")
    u = mda.Universe(PSF, DCD)
    if not os.path.isdir(img_dir) or len(os.listdir(img_dir)) < u.trajectory.n_frames:
        render_ca_frames(u, img_dir)

    pr = Project()
    pr.path = os.path.join(out_dir, "demo.mdmovie.json")
    pr.fps = 15
    traj = pr.add_trajectory(PSF, [DCD], "AdK")
    meta = traj.meta(pr.resolve_path)

    img = pr.add_panel("image", "Protein")
    img.props.update(folder=img_dir, pattern="frame.*.png", index_from="filename",
                     t0=meta.t0, dt=meta.dt, fit="contain")
    plot = pr.add_panel("plot", "RMSD")
    plot.series = [Series(traj.id, "RMSD", {"select": "backbone"}, label="Backbone RMSD")]
    plot.props.update(mode="reveal", show_value=True, value_format="{value:.2f} Å", title="Backbone RMSD")
    rg = pr.add_panel("plot", "Radius of gyration")
    rg.series = [Series(traj.id, "Radius of gyration", {"select": "protein"}, color="#2ca02c")]
    rg.props.update(mode="marker")
    ss = pr.add_panel("heatmap", "Secondary structure")
    ss.series = [Series(traj.id, "Secondary structure (DSSP)", {"select": "protein"})]
    label = pr.add_panel("text", "Time label")
    label.props.update(text="t = {t_ps:.0f} ps", font_size=40, bold=True, align="left", valign="top")

    pr.layout = L.Split("h", [0.5, 0.5], [
        L.Leaf(img.id),
        L.Split("v", [1 / 3] * 3, [L.Leaf(plot.id), L.Leaf(rg.id), L.Leaf(ss.id)]),
    ])
    pr.overlays = [Overlay(label.id, 0.02, 0.02, 0.3, 0.08)]
    pr.save(pr.path)
    return pr.path
