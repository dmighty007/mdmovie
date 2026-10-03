"""Undo periodic wrapping on the fly, so molecules split across the box are drawn and analysed whole."""
from __future__ import annotations

import numpy as np

# residue names of solvent and ions (water is also matched by the "water" keyword)
SOLVENT_AND_IONS = ("HOH WAT TIP3 TIP4 SOL SPC NA NA+ Na+ SOD CL CL- Cl- CLA K K+ POT LI LI+ RB CS "
                    "MG MG2+ CAL CA2+ ZN ZN2+")


class MakeWhole:
    """MDAnalysis on-the-fly transformation that undoes periodic wrapping, like `gmx trjconv -pbc mol
    -center`: each chain (the protein and nucleic acid atoms of a segment, in order) and each other
    residue is made whole by joining every atom to the previous one by the shortest periodic vector,
    then every piece is moved to the periodic image nearest the largest chain. Needs only the
    topology, so it also works for PDB/GRO files without bonds. Water and ions are left as stored
    (they are rarely drawn or analysed and would dominate the cost)."""

    def __init__(self, u):
        polymer = np.zeros(u.atoms.n_atoms, bool)
        solvent = np.zeros(u.atoms.n_atoms, bool)
        try:
            polymer[u.select_atoms("protein or nucleic").indices] = True
            solvent[u.select_atoms(f"water or resname {SOLVENT_AND_IONS}").indices] = True
        except Exception:
            pass
        seg, res = u.atoms.segindices, u.atoms.resindices
        # pieces: the polymer atoms of each segment, then every other residue
        key = np.where(polymer, seg, -1 - res)
        keep = np.flatnonzero(~solvent | polymer)
        order = keep[np.lexsort((keep, key[keep]))]              # atoms of a piece together, in index order
        k = key[order]
        starts = np.r_[0, np.flatnonzero(k[1:] != k[:-1]) + 1]
        self.order, self.starts = order, starts
        self.counts = np.diff(np.r_[starts, len(order)])
        poly_piece = polymer[order[starts]]
        weights = np.where(poly_piece, self.counts, 0) if poly_piece.any() else self.counts
        self.ref = int(np.argmax(weights))
        self.jumps = starts[1:] - 1                              # diffs that cross into a new piece

    def __call__(self, ts):
        box = ts.dimensions
        if box is None or not np.all(np.asarray(box[:3]) > 0) or len(self.order) < 2:
            return ts
        from MDAnalysis.lib.distances import minimize_vectors
        box = np.asarray(box, np.float32)
        x = ts.positions[self.order].astype(np.float64)
        raw = np.diff(x, axis=0)
        d = minimize_vectors(raw.astype(np.float32), box).astype(np.float64)
        d[self.jumps] = raw[self.jumps]                          # each piece keeps its first atom where it was
        y = np.empty_like(x)
        y[0] = x[0]
        np.cumsum(d, axis=0, out=y[1:])
        y[1:] += x[0]
        centres = np.add.reduceat(y, self.starts) / self.counts[:, None]
        off = centres - centres[self.ref]
        shift = minimize_vectors(off.astype(np.float32), box).astype(np.float64) - off
        y += np.repeat(shift, self.counts, axis=0)
        pos = ts.positions
        pos[self.order] = y
        ts.positions = pos
        return ts
