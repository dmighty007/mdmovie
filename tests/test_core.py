import numpy as np
import pytest

from mdmovie.core import crop as C
from mdmovie.core import layout as L
from mdmovie.core.timemap import SyncGroup


# --- time map -------------------------------------------------------------------------------

def test_auto_speed_is_one_source_frame_per_movie_frame():
    m = SyncGroup("g").resolve((10.0, 20.0, 2.0))
    assert m.n_frames == 6 and m.end == 6
    assert [m.source_time(i) for i in range(6)] == [10, 12, 14, 16, 18, 20]


def test_offset_and_edges():
    g = SyncGroup("g", start=5, before="hide", after="hold")
    m = g.resolve((0.0, 4.0, 1.0))
    assert m.source_time(4) is None
    assert m.source_time(5) == 0
    assert m.source_time(9) == 4
    assert m.source_time(100) == 4
    g.after = "hide"
    assert g.resolve((0.0, 4.0, 1.0)).source_time(10) is None


def test_loop_and_speed():
    g = SyncGroup("g", speed=0.5, after="loop", before="loop")
    m = g.resolve((0.0, 1.0, 1.0))
    assert m.n_frames == 3
    assert [m.source_time(i) for i in range(7)] == [0, 0.5, 1.0, 0, 0.5, 1.0, 0]
    assert m.source_time(-1) == 1.0


def test_source_range_override():
    m = SyncGroup("g", src_start=2, src_end=3).resolve((0.0, 10.0, 1.0))
    assert (m.lo, m.hi, m.n_frames) == (2, 3, 2)


# --- layout ---------------------------------------------------------------------------------

def rects(root):
    return {leaf.panel: tuple(round(v, 6) for v in r) for _, leaf, r in L.iter_leaves(root)}


def test_split_and_remove():
    root = L.Leaf("a")
    root = L.split_leaf(root, (), "h", "b")
    assert rects(root) == {"a": (0, 0, 0.5, 1), "b": (0.5, 0, 0.5, 1)}
    root = L.split_leaf(root, (1,), "h", "c")  # joins the existing horizontal split
    assert isinstance(root, L.Split) and len(root.children) == 3
    root = L.split_leaf(root, (0,), "v", "d")
    assert rects(root)["d"] == (0, 0.5, 0.5, 0.5)
    root = L.remove_leaf(root, (0, 1))
    assert rects(root)["a"] == (0, 0, 0.5, 1)
    assert L.from_dict(root.to_dict()).to_dict() == root.to_dict()


def test_dividers():
    root = L.TEMPLATES["Side by side (1×2)"](["a", "b"])
    (path, i, srect, pos), = list(L.iter_dividers(root))
    assert pos == pytest.approx(0.5)
    L.set_divider(root, path, i, 0.7, srect)
    assert rects(root)["a"][2] == pytest.approx(0.7)
    L.set_divider(root, path, i, 5.0, srect)  # clamped
    assert rects(root)["b"][2] == pytest.approx(L.MIN_RATIO)


def test_templates_keep_panels():
    root = L.TEMPLATES["Grid 2×2"](["a", "b", "c"])
    assert L.panel_ids(L.apply_template("Big left + 2 right", root)) == ["a", "b", "c"]


# --- crop -----------------------------------------------------------------------------------

@pytest.mark.parametrize("handle", C.HANDLES)
def test_resize_keeps_aspect_and_bounds(handle):
    bounds = (1000, 600)
    r = C.resize_rect((200, 150, 320, 180), handle, 990, -50, 16 / 9, bounds)
    x, y, w, h = r
    assert w / h == pytest.approx(16 / 9, rel=1e-6)
    assert x >= -1e-9 and y >= -1e-9 and x + w <= 1000 + 1e-9 and y + h <= 600 + 1e-9


def test_resize_free_moves_one_corner():
    assert C.resize_rect((10, 10, 100, 100), "se", 60, 210, None, (500, 500)) == (10, 10, 50, 200)


def test_fit_and_expand_aspect():
    x, y, w, h = C.fit_aspect((0, 0, 400, 400), 2.0, (400, 400))
    assert (w, h) == (400, 200) and y == 100
    x, y, w, h = C.expand_to_aspect((100, 100, 100, 100), 2.0, (400, 400))
    assert (w, h) == (200, 100) and x == 50


def test_auto_trim():
    img = np.full((100, 200, 3), 255, np.uint8)
    img[40:60, 50:90] = 0
    x, y, w, h = C.auto_trim([img], margin=0)
    assert (x, y, w, h) == (50, 40, 40, 20)
    x, y, w, h = C.auto_trim([img], margin=0, aspect=1.0)
    assert w == h == 40


def test_normalize_roundtrip():
    r = (10, 20, 30, 40)
    assert C.denormalize(C.normalize(r, 100, 200), 100, 200) == pytest.approx(r)
