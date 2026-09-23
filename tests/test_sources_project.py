import json

from PySide6.QtGui import QColor, QImage

from mdmovie.core import layout as L
from mdmovie.core.project import Overlay, Project
from mdmovie.sources import image_sequence as S



def make_frames(folder, numbers, size=(64, 48)):
    folder.mkdir(exist_ok=True)
    for n in numbers:
        img = QImage(*size, QImage.Format.Format_RGB32)
        img.fill(QColor(n % 256, 0, 0))
        img.save(str(folder / f"frame.{n:04d}.png"))
    return folder


def test_natural_sort_and_filename_times(tmp_path):
    d = make_frames(tmp_path / "f", [0, 2, 10, 4, 8])
    assert [p.rsplit(".", 2)[1] for p in S.scan_folder(str(d), "*.png")] == ["0000", "0002", "0004", "0008", "0010"]
    seq = S.build_sequence(str(d), "*.png", "filename", S.DEFAULT_NUMBER_REGEX, 100.0, 5.0)
    assert list(seq.times) == [100, 110, 120, 140, 150]
    assert seq.gaps == [6]
    assert seq.index_at(129) == 2 and seq.index_at(131) == 3 and seq.index_at(-5) == 0
    order = S.build_sequence(str(d), "*.png", "order", "", 0.0, 1.0)
    assert list(order.times) == [0, 1, 2, 3, 4]


def test_image_cache_crop(tmp_path):
    d = make_frames(tmp_path / "f", [1], size=(100, 50))
    img = S.IMAGE_CACHE.get(str(d / "frame.0001.png"), [0.1, 0.2, 0.5, 0.4])
    assert (img.width(), img.height()) == (50, 20)


def test_project_roundtrip_relative_paths(tmp_path):
    d = make_frames(tmp_path / "frames", [0, 1])
    pr = Project()
    pr.path = str(tmp_path / "p.mdmovie.json")
    img = pr.add_panel("image")
    img.props["folder"] = str(d)
    img.props["crop"] = [0.1, 0.1, 0.5, 0.5]
    txt = pr.add_panel("text")
    g2 = pr.add_group("Other")
    txt.group = g2.id
    pr.layout = L.TEMPLATES["Side by side (1×2)"]([img.id, txt.id])
    pr.overlays.append(Overlay(txt.id))
    pr.save(pr.path)
    raw = json.load(open(pr.path))
    assert raw["panels"][0]["props"]["folder"] == "frames"
    pr2 = Project.load(pr.path)
    assert pr2.panels[img.id].props["folder"] == str(d)
    assert pr2.panels[txt.id].group == g2.id
    assert pr2.to_dict() == pr.to_dict()
    assert pr2.n_frames == 2


def test_independent_groups_drive_length(tmp_path):
    d = make_frames(tmp_path / "frames", range(5))
    pr = Project()
    a = pr.add_panel("image")
    a.props["folder"] = str(d)
    b = pr.add_panel("image")
    b.props["folder"] = str(d)
    b.group = pr.add_group().id
    pr.groups[b.group].start = 10
    pr.layout = L.TEMPLATES["Side by side (1×2)"]([a.id, b.id])
    assert pr.n_frames == 15
    assert pr.group_time(a.group, 12) == 4 and pr.group_time(b.group, 12) == 2
