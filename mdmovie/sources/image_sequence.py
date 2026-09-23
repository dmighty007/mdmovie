"""Image-sequence folders: scanning, time axis, and a thread-safe LRU image cache."""
from __future__ import annotations

import fnmatch
import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QImage, QImageReader

from mdmovie.core import crop as cropmath

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".webp", ".ppm")
DEFAULT_NUMBER_REGEX = r"(\d+)(?!.*\d)"  # last number in the file name


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def scan_folder(folder: str, pattern: str = "*") -> list[str]:
    """Image files in `folder` matching the glob `pattern` (comma-separated allowed), naturally sorted."""
    if folder and os.path.isfile(folder):  # a single picture
        return [folder] if folder.lower().endswith(IMAGE_EXTS) else []
    if not folder or not os.path.isdir(folder):
        return []
    pats = [p.strip() for p in (pattern or "*").split(",") if p.strip()] or ["*"]
    names = [n for n in os.listdir(folder)
             if n.lower().endswith(IMAGE_EXTS) and any(fnmatch.fnmatch(n, p) for p in pats)]
    names.sort(key=natural_key)
    return [os.path.join(folder, n) for n in names]


def parse_numbers(paths: list[str], regex: str = DEFAULT_NUMBER_REGEX) -> list[int | None]:
    rx = re.compile(regex or DEFAULT_NUMBER_REGEX)
    out = []
    for p in paths:
        m = rx.search(os.path.splitext(os.path.basename(p))[0])
        try:
            out.append(int(m.group(1) if m.groups() else m.group(0)) if m else None)
        except (ValueError, IndexError):
            out.append(None)
    return out


@dataclass
class Sequence:
    paths: list[str]
    times: np.ndarray        # ps, one per image, ascending
    gaps: list[int]          # missing frame numbers (filename indexing only)
    warning: str = ""

    def __len__(self):
        return len(self.paths)

    def index_at(self, t: float) -> int:
        """Index of the image whose time is nearest to `t`."""
        if not self.paths:
            return -1
        i = int(np.searchsorted(self.times, t))
        if i <= 0:
            return 0
        if i >= len(self.times):
            return len(self.times) - 1
        return i if abs(self.times[i] - t) < abs(t - self.times[i - 1]) else i - 1

    def time_info(self) -> tuple[float, float, float] | None:
        if not self.paths:
            return None
        dt = float(np.median(np.diff(self.times))) if len(self.times) > 1 else 1.0
        return float(self.times[0]), float(self.times[-1]), dt


_seq_cache: dict = {}


def build_sequence(folder: str, pattern: str, index_from: str, regex: str,
                   t0: float, dt: float) -> Sequence:
    """Scan a folder and give each image a simulation time: t = t0 + k*dt, where k is the
    image's position (index_from="order") or the number in its file name ("filename")."""
    try:
        mtime = os.stat(folder).st_mtime if folder else 0
    except OSError:
        mtime = 0
    key = (folder, pattern, index_from, regex, t0, dt, mtime)
    if key in _seq_cache:
        return _seq_cache[key]
    paths = scan_folder(folder, pattern)
    gaps, warning = [], ""
    if index_from == "filename" and paths:
        nums = parse_numbers(paths, regex)
        keep = [(n, p) for n, p in zip(nums, paths) if n is not None]
        if len(keep) < len(paths):
            warning = f"{len(paths) - len(keep)} file(s) without a number were skipped. "
        keep.sort()
        paths = [p for _, p in keep]
        ks = np.array([n for n, _ in keep], dtype=float)
        if len(ks) > 1:
            step = int(np.min(np.diff(ks))) or 1
            expected = set(range(int(ks[0]), int(ks[-1]) + 1, step))
            gaps = sorted(expected - set(int(k) for k in ks))
            if gaps:
                warning += f"{len(gaps)} missing frame number(s), e.g. {gaps[:5]}."
    else:
        ks = np.arange(len(paths), dtype=float)
    seq = Sequence(paths, t0 + ks * dt, gaps, warning)
    _seq_cache[key] = seq
    return seq


class ImageCache:
    """LRU cache of decoded (and cropped) QImages, safe to use from worker threads."""

    def __init__(self, max_bytes: int = 1_500_000_000):
        self.max_bytes = max_bytes
        self._data: OrderedDict = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="img-prefetch")
        self._pending: set = set()

    def get(self, path: str, crop=None) -> QImage | None:
        key = (path, tuple(crop) if crop else None)
        with self._lock:
            img = self._data.get(key)
            if img is not None:
                self._data.move_to_end(key)
                return img
        img = _load(path, crop)
        if img is None:
            return None
        with self._lock:
            if key not in self._data:
                self._data[key] = img
                self._bytes += img.sizeInBytes()
                while self._bytes > self.max_bytes and len(self._data) > 1:
                    _, old = self._data.popitem(last=False)
                    self._bytes -= old.sizeInBytes()
        return img

    def prefetch(self, paths: list[str], crop=None) -> None:
        for p in paths:
            key = (p, tuple(crop) if crop else None)
            with self._lock:
                if key in self._data or key in self._pending:
                    continue
                self._pending.add(key)
            self._pool.submit(self._prefetch_one, p, crop, key)

    def _prefetch_one(self, path, crop, key):
        try:
            self.get(path, crop)
        finally:
            with self._lock:
                self._pending.discard(key)

    def clear(self):
        with self._lock:
            self._data.clear()
            self._bytes = 0


def _load(path: str, crop=None) -> QImage | None:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    img = reader.read()
    if img.isNull():
        return None
    if crop:
        x, y, w, h = cropmath.to_int_rect(cropmath.denormalize(crop, img.width(), img.height()),
                                          img.width(), img.height())
        img = img.copy(x, y, w, h)
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def image_size(path: str) -> tuple[int, int]:
    s = QImageReader(path).size()
    return s.width(), s.height()


def qimage_to_array(img: QImage) -> np.ndarray:
    """RGBA uint8 array copy of a QImage."""
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())
    return buf[:, : w * 4].reshape(h, w, 4).copy()


IMAGE_CACHE = ImageCache()
