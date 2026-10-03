"""Compute analysis series with caching (memory + ~/.cache/mdmovie/analysis/*.npz)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import traceback

from mdmovie.analysis import builtin  # noqa: F401  (registers built-in presets)
from mdmovie.analysis.registry import PRESETS, Cancelled, result_from_npz, result_to_npz

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "mdmovie", "analysis")

_mem: dict = {}
_errors: dict = {}
_lock = threading.Lock()


def _file_signature(path: str):
    try:
        st = os.stat(path)
        return [os.path.abspath(path), st.st_mtime_ns, st.st_size]
    except OSError:
        return None


def series_key(project, series) -> str | None:
    """Stable hash of (trajectory files + mtimes, preset, params incl. data-file mtimes, frame slice)."""
    pdef = PRESETS.get(series.preset)
    traj = project.trajectories.get(series.traj)
    if traj is None and (pdef is None or pdef.needs_universe):
        return None
    files = [p for p in (project.resolve_path(str(v)) for v in series.params.values() if isinstance(v, str) and v)
             if os.path.isfile(p)]
    payload = {"files": traj.signature(project.resolve_path) if traj else [],
               "data_files": [_file_signature(f) for f in files], "preset": series.preset,
               "params": series.params, "frames": [series.start, series.stop, series.step]}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:20]


def cached(key: str | None):
    if key is None:
        return None
    with _lock:
        if key in _mem:
            return _mem[key]
    path = os.path.join(CACHE_DIR, key + ".npz")
    if os.path.exists(path):
        try:
            res = result_from_npz(path)
        except Exception:
            return None
        with _lock:
            _mem[key] = res
        return res
    return None


def error(key: str | None) -> str | None:
    with _lock:
        return _errors.get(key)


def compute(project, series, progress=None, cancelled=None):
    """Compute (or fetch) a series result. Raises on failure; errors are remembered per key."""
    key = series_key(project, series)
    if key is None:
        raise ValueError(f"Unknown trajectory '{series.traj}'")
    res = cached(key)
    if res is not None:
        return res
    pdef = PRESETS.get(series.preset)
    if pdef is None:
        raise ValueError(f"Unknown preset '{series.preset}' (is its preset file loaded?)")
    params = {**pdef.defaults(), **{k: v for k, v in series.params.items() if k in pdef.defaults()}}
    for prm in pdef.params:  # data files are stored relative to the project
        if prm.kind == "file" and params.get(prm.name):
            params[prm.name] = project.resolve_path(params[prm.name])
    try:
        u = project.trajectories[series.traj].universe(project.resolve_path) if pdef.needs_universe else None
        res = pdef.func(u, (series.start, series.stop, series.step), progress, cancelled, **params)
        if u is not None:
            _count_untimed_frames(project, series, res)
    except Cancelled:
        raise
    except Exception as e:
        with _lock:
            _errors[key] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
        raise
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        result_to_npz(res, os.path.join(CACHE_DIR, key + ".npz"))
    except Exception:
        traceback.print_exc()
    with _lock:
        _mem[key] = res
        _errors.pop(key, None)
    return res


def _count_untimed_frames(project, series, res) -> None:
    """Some trajectories store no frame times (every frame reports the same time), and the user may
    have given the time between frames. Either way, time the result as the trajectory source does, so
    plots stay in step with molecule and image panels."""
    import numpy as np
    t = getattr(res, "time", None)
    traj = project.trajectories[series.traj]
    if t is None or len(t) < 2 or (np.ptp(t) > 0 and traj.dt <= 0):
        return
    meta = traj.meta(project.resolve_path)
    frames = np.arange(meta.n_frames)[slice(series.start, series.stop, series.step)]
    if len(frames) == len(t):
        res.time = meta.t0 + frames * meta.dt


def forget(key: str) -> None:
    """Drop a cached result so it is recomputed."""
    with _lock:
        _mem.pop(key, None)
        _errors.pop(key, None)
    try:
        os.remove(os.path.join(CACHE_DIR, key + ".npz"))
    except OSError:
        pass


def missing_series(project):
    """(key, series) pairs that have neither a result nor a remembered error."""
    out, seen = [], set()
    for panel in project.panels.values():
        for s in getattr(panel, "series", []):
            key = series_key(project, s)
            if key and key not in seen and cached(key) is None and error(key) is None:
                seen.add(key)
                out.append((key, s))
    return out


def compute_all(project, progress=None, cancelled=None) -> list[str]:
    """Compute every missing series synchronously. Returns error messages."""
    errs = []
    for key, s in missing_series(project):
        try:
            compute(project, s, progress, cancelled)
        except Cancelled:
            raise
        except Exception as e:
            errs.append(f"{s.label or s.preset}: {e}")
    return errs
