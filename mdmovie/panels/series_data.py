"""Looking up analysis results for a panel's series, and turning them into x/y data."""
from __future__ import annotations

import numpy as np

from mdmovie.analysis import runner


def series_results(project, series_list):
    """[(series, result or None, status message)] for each series."""
    out = []
    for s in series_list:
        key = runner.series_key(project, s)
        res = runner.cached(key)
        if res is not None:
            out.append((s, res, ""))
        else:
            err = runner.error(key)
            out.append((s, None, f"Error: {err}" if err else "computing…" if key else "missing trajectory"))
    return out


def frame_numbers(series, n: int) -> np.ndarray:
    """Trajectory frame (or data-file row) number of each of a series' n points, from its frame slice."""
    return float(series.start or 0) + float(series.step or 1) * np.arange(n, dtype=float)


def series_times(series, res, frames: bool = False) -> np.ndarray:
    """A result's time axis in ps, or its frame numbers when the panel is synced frame-wise."""
    return frame_numbers(series, len(res.time)) if frames else np.asarray(res.time, float)


def series_time_info(project, series_list, frames: bool = False):
    """(first, last, dt) in ps over all series (in frame numbers if `frames`), falling back to
    trajectory metadata before results exist."""
    lo = hi = dt = None
    for s, res, _ in series_results(project, series_list):
        t = getattr(res, "time", None)
        if t is None:
            traj = project.trajectories.get(s.traj)
            if traj is None:
                continue
            try:
                t = traj.meta(project.resolve_path).times(s.start, s.stop, s.step)
            except Exception:
                continue
        if len(t) == 0:
            continue
        if frames:
            t = frame_numbers(s, len(t))
        d = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
        lo = t[0] if lo is None else min(lo, t[0])
        hi = t[-1] if hi is None else max(hi, t[-1])
        dt = d if dt is None else min(dt, d)
    return None if lo is None else (float(lo), float(hi), float(dt))


def columns(project, series_list):
    """Every time-series column of the panel, in order: [(label, units, time, values)]."""
    out = []
    for s, res, _ in series_results(project, series_list):
        if res is None or res.kind != "timeseries":
            continue
        cols = range(res.values.shape[1]) if s.column is None else [s.column]
        for c in cols:
            if c < res.values.shape[1]:
                label = s.label if (s.label and len(cols) == 1) else res.labels[c]
                out.append((label, res.units, np.asarray(res.time, float), res.values[:, c]))
    return out


def column_names(project, series_list) -> list[str]:
    return [f"{i}: {lab}" for i, (lab, *_rest) in enumerate(columns(project, series_list))]


def xy_data(project, series_list, xi: int, yi: int):
    """(time, x, y, x label, y label) using columns xi and yi; y is interpolated onto x's times."""
    cols = columns(project, series_list)
    if len(cols) <= max(xi, yi) or xi < 0 or yi < 0:
        return None
    (lx, ux, tx, x), (ly, uy, ty, y) = cols[xi], cols[yi]
    if len(tx) != len(ty) or not np.allclose(tx, ty):
        y = np.interp(tx, ty, y)
    ok = np.isfinite(x) & np.isfinite(y)
    fmt = lambda lab, u: f"{lab} ({u})" if u else lab  # noqa: E731
    return tx[ok], x[ok], y[ok], fmt(lx, ux), fmt(ly, uy)


def current_index(t: np.ndarray, now: float | None) -> int:
    """Index of the last data point at or before `now` (0 before the start)."""
    if now is None or len(t) == 0:
        return -1
    return max(0, min(len(t) - 1, int(np.searchsorted(t, now, side="right")) - 1))
