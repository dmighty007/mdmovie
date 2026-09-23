"""Analysis preset registry.

A preset is a function that reads an MDAnalysis Universe and returns a result
(TimeSeries, Matrix or Profile). Register one with the @preset decorator:

    from mdmovie.analysis.registry import preset, Sel, Float, TimeSeries, iter_frames

    @preset("My distance", params=[Sel("a", "resid 10"), Sel("b", "resid 50")], units="Å")
    def my_distance(u, frames, progress, cancelled, a, b):
        ga, gb = u.select_atoms(a), u.select_atoms(b)
        t, v = [], []
        for ts in iter_frames(u, frames, progress, cancelled):
            t.append(ts.time)
            v.append(np.linalg.norm(ga.center_of_mass() - gb.center_of_mass()))
        return TimeSeries(np.array(t), np.array(v), ["distance"], "Å")

`frames` is a (start, stop, step) tuple; `progress(i, n)` reports progress and
`cancelled()` returns True when the user pressed Cancel. Any .py file with
presets in ~/.config/mdmovie/presets/ or <project dir>/presets/ is loaded
automatically.
"""
from __future__ import annotations

import importlib.util
import os
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

USER_PRESET_DIR = os.path.join(os.path.expanduser("~"), ".config", "mdmovie", "presets")


class Cancelled(Exception):
    pass


# --- parameters -----------------------------------------------------------------------------

@dataclass
class Param:
    name: str
    kind: str            # sel | int | float | bool | str | choice
    default: Any
    label: str = ""
    options: tuple = ()
    help: str = ""
    minimum: float = -1e9
    maximum: float = 1e9

    @property
    def text(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()


def Sel(name, default="protein", label="", help=""):
    return Param(name, "sel", default, label, help=help)


def Int(name, default=0, label="", help="", minimum=-1_000_000, maximum=1_000_000):
    return Param(name, "int", int(default), label, help=help, minimum=minimum, maximum=maximum)


def Float(name, default=0.0, label="", help="", minimum=-1e9, maximum=1e9):
    return Param(name, "float", float(default), label, help=help, minimum=minimum, maximum=maximum)


def Bool(name, default=False, label="", help=""):
    return Param(name, "bool", bool(default), label, help=help)


def Str(name, default="", label="", help=""):
    return Param(name, "str", default, label, help=help)


def File(name, default="", label="", help=""):
    return Param(name, "file", default, label, help=help)


def Choice(name, options, default=None, label="", help=""):
    return Param(name, "choice", default if default is not None else options[0], label, tuple(options), help)


# --- results --------------------------------------------------------------------------------

@dataclass
class TimeSeries:
    time: np.ndarray                 # (n,) ps
    values: np.ndarray               # (n,) or (n, k)
    labels: list[str] = field(default_factory=list)
    units: str = ""
    kind: str = "timeseries"

    def __post_init__(self):
        self.time = np.asarray(self.time, float)
        v = np.asarray(self.values, float)
        self.values = v[:, None] if v.ndim == 1 else v
        if not self.labels:
            self.labels = [f"col {i}" for i in range(self.values.shape[1])]


@dataclass
class Matrix:
    time: np.ndarray                 # (n,)
    values: np.ndarray               # (n, m) numeric; one column per row label
    row_labels: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)  # names for integer codes, if categorical
    units: str = ""
    kind: str = "matrix"


@dataclass
class Profile:
    x: np.ndarray                    # (m,)
    values: np.ndarray               # (m,) or (m, k)
    labels: list[str] = field(default_factory=list)
    xlabel: str = ""
    units: str = ""
    kind: str = "profile"

    def __post_init__(self):
        self.x = np.asarray(self.x, float)
        v = np.asarray(self.values, float)
        self.values = v[:, None] if v.ndim == 1 else v
        if not self.labels:
            self.labels = [f"col {i}" for i in range(self.values.shape[1])]


Result = TimeSeries | Matrix | Profile


def result_to_npz(result: Result, path: str) -> None:
    arrays = {k: np.asarray(v) for k, v in result.__dict__.items()}
    np.savez_compressed(path, **arrays)


def result_from_npz(path: str) -> Result:
    with np.load(path, allow_pickle=False) as z:
        d = {k: z[k] for k in z.files}
    kind = str(d.pop("kind"))
    cls = {"timeseries": TimeSeries, "matrix": Matrix, "profile": Profile}[kind]
    for k, v in list(d.items()):
        if v.dtype.kind in "U":
            d[k] = str(v) if v.ndim == 0 else [str(s) for s in v]
    return cls(**d)


# --- registry -------------------------------------------------------------------------------

@dataclass
class PresetDef:
    name: str
    func: Callable
    params: list[Param]
    kind: str = "timeseries"   # timeseries | matrix | profile
    units: str = ""
    description: str = ""
    category: str = "General"
    origin: str = "built-in"
    needs_universe: bool = True   # False: reads its own files (e.g. a COLVAR); no trajectory required

    def defaults(self) -> dict:
        return {p.name: p.default for p in self.params}


PRESETS: dict[str, PresetDef] = {}
_loading_origin = "built-in"


def preset(name: str, params=(), kind: str = "timeseries", units: str = "", description: str = "",
           category: str = "General", needs_universe: bool = True):
    def deco(func):
        PRESETS[name] = PresetDef(name, func, list(params), kind, units,
                                  description or (func.__doc__ or "").strip(), category, _loading_origin,
                                  needs_universe)
        return func
    return deco


def iter_frames(u, frames, progress=None, cancelled=None):
    """Iterate a trajectory slice, reporting progress and honouring cancellation."""
    start, stop, step = frames
    sub = u.trajectory[start:stop:step]
    n = len(sub)
    for i, ts in enumerate(sub):
        if cancelled and cancelled():
            raise Cancelled()
        yield ts
        if progress:
            progress(i + 1, n)


def run_analysis(analysis, frames, progress=None, cancelled=None):
    """Run an MDAnalysis AnalysisBase object with progress reporting and cancellation."""
    start, stop, step = frames
    n = len(range(*slice(start, stop, step).indices(analysis._trajectory.n_frames)))
    inner = analysis._single_frame
    count = [0]

    def single_frame():
        if cancelled and cancelled():
            raise Cancelled()
        inner()
        count[0] += 1
        if progress:
            progress(count[0], n)

    analysis._single_frame = single_frame
    analysis.run(start=start, stop=stop, step=step)
    return analysis


def load_user_presets(dirs=()) -> list[str]:
    """Import every .py file in the preset folders. Returns error messages, if any."""
    global _loading_origin
    errors = []
    for d in [USER_PRESET_DIR, *dirs]:
        if not d or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            path = os.path.join(d, fn)
            _loading_origin = path
            try:
                spec = importlib.util.spec_from_file_location(f"mdmovie_user_presets.{fn[:-3]}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            except Exception:
                errors.append(f"{path}:\n{traceback.format_exc(limit=3)}")
            finally:
                _loading_origin = "built-in"
    return errors


def presets_for(kinds) -> list[PresetDef]:
    return sorted((p for p in PRESETS.values() if p.kind in kinds), key=lambda p: (p.category, p.name))
