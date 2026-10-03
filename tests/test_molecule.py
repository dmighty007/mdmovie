"""Molecule panel: structure handling, cartoon geometry, the software renderer and its GUI controls."""
import json

import numpy as np
import pytest
from MDAnalysisTests.datafiles import DCD, PSF
from PySide6.QtCore import QPoint, Qt

from mdmovie.core import layout as L
from mdmovie.core.project import Project
from mdmovie.mol.cartoon import chain_mesh
from mdmovie.mol.render import View, render_molecule, rotated
from mdmovie.mol.structure import HELIX, STRAND, geometric_ss, get_scene, kabsch
from mdmovie.render.compositor import render_frame
from mdmovie.render.exporter import qimage_to_rgb


def helix(n=16):
    """Ideal α-helix Cα trace: 100° and 1.5 Å per residue on a 2.3 Å radius."""
    i = np.arange(n)
    return np.c_[2.3 * np.cos(np.radians(100) * i), 2.3 * np.sin(np.radians(100) * i), 1.5 * i]


@pytest.fixture
def project():
    pr = Project()
    traj = pr.add_trajectory(PSF, [DCD], "AdK")
    mol = pr.add_panel("molecule")
    mol.props["traj"] = traj.id
    pr.layout = L.Leaf(mol.id)
    return pr, mol


def test_kabsch_recovers_a_rotation():
    rng = np.random.default_rng(1)
    ref = rng.normal(size=(30, 3))
    turn = np.array(rotated(np.eye(3).ravel(), 0.7, -0.4)).reshape(3, 3)
    mobile = ref @ turn.T + [3.0, -2.0, 5.0]
    r, cm, cr = kabsch(mobile, ref)
    assert np.allclose((mobile - cm) @ r.T + cr, ref, atol=1e-5)
    assert np.isclose(np.linalg.det(r), 1.0)


def test_geometric_secondary_structure():
    assert (geometric_ss(helix())[2:-2] == HELIX).all()
    strand = np.c_[3.3 * np.arange(10), 0.9 * (-1.0) ** np.arange(10), np.zeros(10)]
    assert (geometric_ss(strand) == STRAND).all()


def test_cartoon_mesh_faces_outwards():
    pts = np.c_[np.arange(12) * 3.8, np.zeros(12), np.zeros(12)]
    for ss in (np.zeros(12, int), np.r_[0, 0, [STRAND] * 5, 0, 0, 0, 0, 0], np.r_[0, 0, [HELIX] * 6, 0, 0, 0, 0]):
        quads, colors = chain_mesh(pts, None, ss, np.ones((12, 3)))
        assert len(quads) == len(colors) and np.isfinite(quads).all()
        normal = np.cross(quads[:, 2] - quads[:, 0], quads[:, 3] - quads[:, 1])
        centre = quads.mean(axis=1)
        radial = centre * [0, 1, 1]
        side = np.abs(normal[:, 0]) < 0.9 * np.linalg.norm(normal, axis=1)
        assert ((normal * radial).sum(axis=1)[side] > 0).all()
    # a coil is a thin tube; a strand is a wide ribbon whose arrow head is wider still
    coil, _ = chain_mesh(pts, None, np.zeros(12, int), np.ones((12, 3)))
    strand, _ = chain_mesh(pts, None, np.r_[0, 0, [STRAND] * 5, 0, 0, 0, 0, 0], np.ones((12, 3)))
    assert np.abs(coil[:, :, 1:]).max() < 0.3 and np.abs(strand[:, :, 1:]).max() > 1.4


def test_scene_and_render(project):
    pr, mol = project
    scene = mol.scene(pr)
    assert not scene.empty and scene.n_frames == 98
    ss = scene.ss(0)
    assert (ss == HELIX).sum() > 60 and (ss == STRAND).sum() > 10        # AdK is mostly helical with a β-sheet
    assert len(scene.reps[0].chains) == 1 and len(scene.reps[1].index) == 0   # protein only: no ligand to draw
    img = render_molecule(scene, 0, 400, 300, View())
    a = qimage_to_rgb(img.convertToFormat(img.Format.Format_ARGB32))
    assert img.pixelColor(0, 0).alpha() == 0 and (a.sum(axis=2) > 0).mean() > 0.05
    turned = render_molecule(scene, 0, 400, 300, View(np.array(rotated(np.eye(3).ravel(), 1.2, 0.3)).reshape(3, 3)))
    assert turned != img
    # fitting to the first frame removes tumbling: the fitted atoms stay close to where they started
    ca = scene.u.select_atoms("protein and name CA").indices
    assert np.abs(scene.positions(60)[ca].mean(axis=0) - scene.positions(0)[ca].mean(axis=0)).max() < 1e-3


def test_every_style_draws(project):
    pr, mol = project
    for style in ("tube", "licorice", "ball and stick", "spheres", "lines"):
        reps = [{"sel": "resid 1:40", "style": style, "color": "chain"}]
        img = render_molecule(get_scene(pr, mol.props["traj"], reps, "none", ""), 3, 200, 160, View())
        assert (qimage_to_rgb(img.convertToFormat(img.Format.Format_ARGB32)).sum(axis=2) > 0).any(), style
    bad = get_scene(pr, mol.props["traj"], [{"sel": "name ((", "style": "lines"}], "none", "")
    assert bad.empty and bad.reps[0].error


def test_panel_times_and_project_roundtrip(project, tmp_path):
    pr, mol = project
    meta = pr.trajectories[mol.props["traj"]].meta(pr.resolve_path)
    assert mol.time_info(pr) == (meta.t0, meta.t0 + 97 * meta.dt, meta.dt) and pr.n_frames == 98
    mol.props.update(rotation=rotated(mol.props["rotation"], 0.5, 0.2), zoom=1.4)
    mol.props["reps"].append({"sel": "resid 30:40", "style": "licorice", "color": "element", "scale": 1.0})
    pr.save(str(tmp_path / "p.mdmovie.json"))
    again = Project.load(str(tmp_path / "p.mdmovie.json"))
    assert json.dumps(again.to_dict(), sort_keys=True) == json.dumps(pr.to_dict(), sort_keys=True)
    a, b = render_frame(again, 0, 0.25), render_frame(again, 60, 0.25)
    assert a != b and (qimage_to_rgb(a) < 250).any()


def test_drag_rotates_and_undo_restores(qtbot, monkeypatch, project):
    from mdmovie.ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_confirm_discard", lambda self: True)
    pr, mol = project
    win = MainWindow()
    qtbot.addWidget(win)
    win.resize(1100, 700)
    win.show()
    win._set_project(pr)
    win.select(("panel", mol.id))
    qtbot.wait(50)
    start = list(win.project.panels[mol.id].props["rotation"])
    c = next(r for _, p, r in win.canvas.cells() if p == mol.id).center().toPoint()
    qtbot.mousePress(win.canvas, Qt.MouseButton.LeftButton, pos=c)
    qtbot.mouseMove(win.canvas, c + QPoint(80, 30))
    qtbot.mouseRelease(win.canvas, Qt.MouseButton.LeftButton, pos=c + QPoint(80, 30))
    assert win.project.panels[mol.id].props["rotation"] != start and win.undo.undoText() == "Rotate view"
    win.undo.undo()
    assert win.project.panels[mol.id].props["rotation"] == start
    win.set_molecule_view(mol.id, "top")
    assert win.project.panels[mol.id].props["rotation"][5] == -1.0
    win.analysis.shutdown()


def test_make_whole_repairs_molecules_split_by_the_box():
    """The GROMACS test trajectory stores AdK split across a triclinic box (bonds ~80 Å long)."""
    import MDAnalysis as mda
    from MDAnalysisTests.datafiles import TPR, XTC

    from mdmovie.sources.pbc import MakeWhole

    def longest_bond(u):
        b = u.select_atoms("protein").bonds.indices
        return np.linalg.norm(u.atoms.positions[b[:, 0]] - u.atoms.positions[b[:, 1]], axis=1).max()
    raw, whole = mda.Universe(TPR, XTC), mda.Universe(TPR, XTC)
    whole.trajectory.add_transformations(MakeWhole(whole))
    for f in (0, 5, 9):
        raw.trajectory[f]
        whole.trajectory[f]
        assert longest_bond(raw) > 40 and longest_bond(whole) < 2.5


def test_start_from_trajectory_files(tmp_path):
    """`mdmovie top traj` / dropped files: molecule + RMSD + Rg + time label, files in any order."""
    from mdmovie.analysis import runner
    from mdmovie.core.quickstart import split_files, trajectory_movie

    assert split_files([DCD, PSF]) == (PSF, [DCD])
    with pytest.raises(ValueError):
        split_files([DCD])                          # coordinates only
    pr = Project()
    tid = trajectory_movie(pr, *split_files([DCD, PSF]))
    kinds = sorted(p.KIND for p in pr.panels.values())
    assert kinds == ["molecule", "plot", "plot", "text"]
    assert set(L.panel_ids(pr.layout)) | {o.panel for o in pr.overlays} == set(pr.panels)
    plots = [p for p in pr.panels.values() if p.KIND == "plot"]
    assert [s.params["select"] for p in plots for s in p.series] == ["backbone", "protein"]
    assert all(runner.compute(pr, s) is not None for p in plots for s in p.series)
    assert 5 <= pr.n_frames / pr.fps <= 30         # 98 frames would be over in 3 s: played slower
    t = pr.trajectories[tid]
    rows = dict(t.composition(pr.resolve_path))
    assert rows["protein"].startswith("214 residues")
    clone = Project.from_dict(json.loads(json.dumps(pr.to_dict())))
    assert clone.trajectories[tid].whole
    t.whole = False
    assert Project.from_dict(pr.to_dict()).trajectories[tid].whole is False
    assert t.signature(pr.resolve_path) != clone.trajectories[tid].signature(clone.resolve_path)


def test_secondary_structure_follows_the_frame(monkeypatch):
    """Helices and strands that form or melt show up as the movie plays; with smoothing each residue takes
    its majority state over the window, so single-frame DSSP flips don't make the cartoon flicker."""
    pr = Project()
    t = pr.add_trajectory(PSF, [DCD])
    scene = get_scene(pr, t.id, [{"sel": "protein", "style": "cartoon", "color": "secondary structure"}],
                      "none", "")
    n = len(scene.ss(0))
    fake = {k: np.full(n, HELIX if k < 10 else STRAND) for k in range(scene.n_frames)}
    fake[5] = np.full(n, 0)                                       # a one-frame blip
    monkeypatch.setattr(scene, "ss", lambda i: fake[i])
    assert (scene.ss_at(5) == 0).all() and (scene.ss_at(5, smooth=5) == HELIX).all()
    assert (scene.ss_at(30, smooth=5) == STRAND).all()
    strands = qimage_to_rgb(render_molecule(scene, 30, 160, 120, View()))
    helices = qimage_to_rgb(render_molecule(scene, 30, 160, 120, View(), ss_every_frame=False))   # frame 0's
    assert (strands != helices).any()
