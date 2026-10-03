"""Trajectory sources: a topology plus zero or more trajectory files read with MDAnalysis."""
from __future__ import annotations

import os
import threading
import warnings
from dataclasses import dataclass, field

_meta_cache: dict = {}
_meta_lock = threading.Lock()


@dataclass
class TrajMeta:
    n_frames: int
    t0: float   # ps
    dt: float   # ps
    n_atoms: int
    timed: bool = True   # False: the file stores no frame times, so frames are counted as 1 ps each

    def times(self, start=None, stop=None, step=None):
        import numpy as np
        return self.t0 + np.arange(self.n_frames)[slice(start, stop, step)] * self.dt


@dataclass
class TrajectorySource:
    id: str
    name: str = "Trajectory"
    topology: str = ""
    trajectories: list[str] = field(default_factory=list)
    dt: float = 0.0     # ps between frames given by the user; 0 = read the times from the file
    whole: bool = True  # undo periodic wrapping (molecules split across the box) when reading frames

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
        if self.dt > 0:
            sig.append(("dt", self.dt))
        if self.whole:
            sig.append(("whole",))
        return tuple(sig)

    def universe(self, resolve):
        """A fresh Universe (MDAnalysis Universes are not thread-safe, so don't share them)."""
        import MDAnalysis as mda
        top, *trajs = self.files(resolve)
        with warnings.catch_warnings():     # missing elements, no dt…: handled (and explained) by the app
            warnings.simplefilter("ignore")
            u = mda.Universe(top, *trajs) if trajs else mda.Universe(top)
        if self.whole:
            from mdmovie.sources.pbc import MakeWhole
            u.trajectory.add_transformations(MakeWhole(u))
        return u

    def meta(self, resolve) -> TrajMeta:
        sig = self.signature(resolve)
        with _meta_lock:
            if sig in _meta_cache:
                return _meta_cache[sig]
        u = self.universe(resolve)
        tr = u.trajectory
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t0 = float(tr[0].time) if tr.n_frames else 0.0
            try:
                dt = float(tr.dt)
            except Exception:
                dt = 1.0
        timed = dt > 0 or tr.n_frames < 2
        if self.dt > 0:
            dt, timed = self.dt, True
        meta = TrajMeta(tr.n_frames, t0, dt if dt > 0 else 1.0, u.atoms.n_atoms, timed=timed)
        with _meta_lock:
            _meta_cache[sig] = meta
        return meta

    def composition(self, resolve) -> list[tuple[str, str]]:
        """What the system contains, read from the topology: (kind, summary) rows such as
        ("protein", "214 residues in 2 chains"), to help write selections."""
        sig = ("composition",) + self.signature(resolve)
        with _meta_lock:
            if sig in _meta_cache:
                return _meta_cache[sig]
        import MDAnalysis as mda
        from mdmovie.sources.pbc import SOLVENT_AND_IONS
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            top, *trajs = self.files(resolve)
            u = mda.Universe(top, *trajs) if trajs else mda.Universe(top)     # the box comes from a frame
        rows = []
        for kind, sel in (("protein", "protein"), ("nucleic acid", "nucleic")):
            ag = u.select_atoms(sel)
            if len(ag):
                chains = len(set(ag.segids)) if hasattr(ag, "segids") else 1
                rows.append((kind, f"{ag.n_residues:,} residues" + (f" in {chains} chains" if chains > 1 else "")))
        water = u.select_atoms("water")
        ions = u.select_atoms(f"resname {SOLVENT_AND_IONS} and not water")
        other = u.select_atoms(f"not (protein or nucleic or water or resname {SOLVENT_AND_IONS})")

        def names(ag, limit=6):
            counts: dict[str, int] = {}
            for r in ag.residues.resnames:
                counts[str(r)] = counts.get(str(r), 0) + 1
            items = sorted(counts.items(), key=lambda kv: -kv[1])
            text = ", ".join(f"{n} ×{c:,}" if c > 1 else n for n, c in items[:limit])
            return text + (f", … ({len(items) - limit} more)" if len(items) > limit else "")
        if len(other):
            rows.append(("other residues", names(other)))
        if len(ions):
            rows.append(("ions", names(ions)))
        if len(water):
            rows.append(("water", f"{water.n_residues:,} molecules"))
        dims = u.dimensions
        if dims is not None and (dims[:3] > 0).all():
            a, b, c, al, be, ga = (float(x) for x in dims)
            shape = "" if max(abs(al - 90), abs(be - 90), abs(ga - 90)) < 0.01 else " (triclinic)"
            rows.append(("box", f"{a / 10:.2f} × {b / 10:.2f} × {c / 10:.2f} nm{shape}"))
        with _meta_lock:
            _meta_cache[sig] = rows
        return rows

    def to_dict(self, rel) -> dict:
        return {"id": self.id, "name": self.name, "topology": rel(self.topology),
                "trajectories": [rel(t) for t in self.trajectories], **({"dt": self.dt} if self.dt > 0 else {}),
                **({} if self.whole else {"whole": False})}

    @classmethod
    def from_dict(cls, d: dict) -> "TrajectorySource":
        return cls(d["id"], d.get("name", "Trajectory"), d.get("topology", ""), list(d.get("trajectories", [])),
                   float(d.get("dt", 0.0) or 0.0), bool(d.get("whole", True)))
