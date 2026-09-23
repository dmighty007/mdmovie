"""Trajectory sources: a topology plus zero or more trajectory files read with MDAnalysis."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

_meta_cache: dict = {}
_meta_lock = threading.Lock()


@dataclass
class TrajMeta:
    n_frames: int
    t0: float   # ps
    dt: float   # ps
    n_atoms: int

    def times(self, start=None, stop=None, step=None):
        import numpy as np
        return self.t0 + np.arange(self.n_frames)[slice(start, stop, step)] * self.dt


@dataclass
class TrajectorySource:
    id: str
    name: str = "Trajectory"
    topology: str = ""
    trajectories: list[str] = field(default_factory=list)

    def files(self, resolve) -> list[str]:
        return [resolve(self.topology)] + [resolve(t) for t in self.trajectories]

    def signature(self, resolve) -> tuple:
        sig = []
        for f in self.files(resolve):
            try:
                st = os.stat(f)
                sig.append((os.path.abspath(f), st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((f, 0, 0))
        return tuple(sig)

    def universe(self, resolve):
        """A fresh Universe (MDAnalysis Universes are not thread-safe, so don't share them)."""
        import MDAnalysis as mda
        top, *trajs = self.files(resolve)
        return mda.Universe(top, *trajs) if trajs else mda.Universe(top)

    def meta(self, resolve) -> TrajMeta:
        sig = self.signature(resolve)
        with _meta_lock:
            if sig in _meta_cache:
                return _meta_cache[sig]
        u = self.universe(resolve)
        tr = u.trajectory
        t0 = float(tr[0].time) if tr.n_frames else 0.0
        try:
            dt = float(tr.dt)
        except Exception:
            dt = 1.0
        meta = TrajMeta(tr.n_frames, t0, dt if dt > 0 else 1.0, u.atoms.n_atoms)
        with _meta_lock:
            _meta_cache[sig] = meta
        return meta

    def to_dict(self, rel) -> dict:
        return {"id": self.id, "name": self.name, "topology": rel(self.topology),
                "trajectories": [rel(t) for t in self.trajectories]}

    @classmethod
    def from_dict(cls, d: dict) -> "TrajectorySource":
        return cls(d["id"], d.get("name", "Trajectory"), d.get("topology", ""), list(d.get("trajectories", [])))
