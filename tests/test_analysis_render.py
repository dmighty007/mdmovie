import os

import imageio.v2 as iio
import numpy as np
import pytest

pytest.importorskip("MDAnalysisTests")
import MDAnalysis as mda  # noqa: E402
from MDAnalysis.analysis import rms  # noqa: E402
from MDAnalysisTests.datafiles import DCD, PSF  # noqa: E402

from mdmovie.analysis import runner  # noqa: E402
from mdmovie.analysis.registry import PRESETS, Cancelled  # noqa: E402
from mdmovie.core import layout as L  # noqa: E402
from mdmovie.core.project import Project  # noqa: E402
from mdmovie.panels import Series  # noqa: E402
from mdmovie.render.compositor import render_frame  # noqa: E402
from mdmovie.render.exporter import ExportOptions, export_movie, qimage_to_rgb  # noqa: E402

FR = (0, 20, 2)


@pytest.fixture(scope="module")
def u():
    return mda.Universe(PSF, DCD)


def run(u, name, **params):
    p = PRESETS[name]
    return p.func(u, FR, None, None, **{**p.defaults(), **params})


def test_rmsd_matches_mdanalysis(u):
    res = run(u, "RMSD")
    ref = rms.RMSD(u, u, select="backbone").run(*FR).results.rmsd
    np.testing.assert_allclose(res.values[:, 0], ref[:, 2], rtol=1e-6)
    np.testing.assert_allclose(res.time, ref[:, 1])


@pytest.mark.parametrize("name,params", [
    ("Radius of gyration", {}),
    ("End-to-end distance", {}),
    ("Distance between selections", {"mode": "minimum"}),
    ("Dihedral angle", {"resids": "10,20", "angle": "psi"}),
    ("Native contacts (Q)", {"select_a": "name CA", "select_b": "name CA"}),
    ("Hydrogen bonds", {}),
    ("Secondary structure fractions", {}),
    ("Custom expression", {"expression": "[ts.frame, 2*ts.frame]", "labels": "a,b"}),
])
def test_timeseries_presets(u, name, params):
    res = run(u, name, **params)
    assert res.kind == "timeseries"
    assert len(res.time) == 10 and res.values.shape[0] == 10
    assert np.isfinite(res.values).all()


def test_matrix_and_profile(u):
    m = run(u, "Secondary structure (DSSP)")
    assert m.values.shape == (10, len(m.row_labels))
    p = run(u, "RMSF per residue")
    assert p.values.shape[0] == len(p.x) == 214 and (p.values >= 0).all()


def test_cancel(u):
    with pytest.raises(Cancelled):
        PRESETS["Radius of gyration"].func(u, FR, None, lambda: True, "protein", True)


def build_project(tmp_path):
    pr = Project()
    pr.path = str(tmp_path / "p.mdmovie.json")
    pr.fps = 10
    traj = pr.add_trajectory(PSF, [DCD])
    plot = pr.add_panel("plot")
    plot.series = [Series(traj.id, "RMSD", {}, 0, 30)]
    plot.props["cursor_color"] = "#ff00ff"
    plot.props["mode"] = "marker"
    text = pr.add_panel("text")
    pr.layout = L.TEMPLATES["Side by side (1×2)"]([plot.id, text.id])
    return pr


def test_render_cursor_moves(tmp_path):
    pr = build_project(tmp_path)
    runner.compute_all(pr)
    assert pr.n_frames == 30

    def cursor_x(g):
        a = qimage_to_rgb(render_frame(pr, g, 0.5)).astype(int)
        mask = (a[..., 0] > 200) & (a[..., 1] < 80) & (a[..., 2] > 200)
        return np.nonzero(mask)[1].mean()

    assert cursor_x(5) < cursor_x(15) < cursor_x(25)


def test_export_mp4_and_gif(tmp_path):
    pr = build_project(tmp_path)
    mp4 = export_movie(pr, ExportOptions(str(tmp_path / "m.mp4"), scale=0.25))
    r = iio.get_reader(mp4)
    assert r.count_frames() == 30
    assert r.get_data(0).shape == (270, 480, 3)
    gif = export_movie(pr, ExportOptions(str(tmp_path / "m.gif"), scale=0.25, gif_step=3))
    from PIL import Image
    with Image.open(gif) as im:
        assert im.n_frames == 10 and im.size == (480, 270)
    assert os.path.getsize(gif) > 0


def test_user_preset_folder(u):
    from mdmovie.analysis.registry import load_user_presets
    here = os.path.dirname(__file__)
    assert load_user_presets([os.path.join(here, "..", "examples", "presets")]) == []
    res = run(u, "Salt bridge distance", acidic="resid 22 and name OE1 OE2", basic="resid 23 and name NZ")
    assert res.values.shape == (10, 2)
