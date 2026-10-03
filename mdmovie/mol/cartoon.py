"""Cartoon geometry: a backbone trace becomes a mesh of quads: helices as ribbons wound along the
Cα spiral, strands as flat arrows, everything else as a thin tube.

The backbone is a Catmull-Rom spline through the Cα atoms. The ribbon is oriented by the peptide
plane (the C=O direction), which in a helix points along the helix axis and in a sheet lies in the
sheet, so both come out the way they are usually drawn."""
from __future__ import annotations

import numpy as np

from mdmovie.mol.structure import COIL, HELIX, STRAND

SIDES = 8          # vertices around the cross-section
#                 half-width  half-thickness  squareness (1 = ellipse, → 0 = box)
PROFILE = {COIL: (0.22, 0.22, 1.0), HELIX: (0.95, 0.20, 0.75), STRAND: (0.90, 0.20, 0.30)}
ARROW = 1.7        # arrow head width relative to the strand
TUBE_RADIUS = 0.45
NUCLEIC_RADIUS = 0.6


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n > 1e-9, n, 1.0)


def guide_normals(p: np.ndarray, oxygen: np.ndarray | None) -> np.ndarray:
    """One vector per residue lying in the peptide plane, perpendicular to the backbone, without flips."""
    n = len(p)
    d = np.zeros((n, 3))
    if oxygen is not None:
        a = p[1:] - p[:-1]
        d[:-1] = np.cross(np.cross(a, oxygen[:-1] - p[:-1]), a)
    elif n >= 3:
        d[1:-1] = np.cross(p[1:-1] - p[:-2], p[2:] - p[1:-1])     # binormal of the Cα trace
        d[0] = d[1]
    d[-1] = d[-2]
    d = _unit(d)
    for i in range(n):                       # degenerate (straight) spots take their neighbour's vector
        if not d[i].any():
            d[i] = d[i - 1] if i and d[i - 1].any() else np.array([0.0, 0.0, 1.0])
    for i in range(1, n):                    # neighbouring peptide planes alternate by ~180° in a strand
        if d[i] @ d[i - 1] < 0:
            d[i] = -d[i]
    if n >= 3:
        d[1:-1] = _unit(d[:-2] + 2 * d[1:-1] + d[2:])
    return d


def _spline(p: np.ndarray, m: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Catmull-Rom through p with m steps per interval: positions, tangents and the parameter g
    (g = i exactly at point i)."""
    n = len(p)
    ext = np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    g = np.arange((n - 1) * m + 1) / m
    seg = np.minimum(g.astype(int), n - 2)
    t = (g - seg)[:, None]
    p0, p1, p2, p3 = ext[seg], ext[seg + 1], ext[seg + 2], ext[seg + 3]
    a, b, c = -p0 + p2, 2 * p0 - 5 * p1 + 4 * p2 - p3, -p0 + 3 * p1 - 3 * p2 + p3
    pos = 0.5 * (2 * p1 + a * t + b * t * t + c * t ** 3)
    tan = 0.5 * (a + 2 * b * t + 3 * c * t * t)
    return pos, _unit(tan), g


def _smooth(x: np.ndarray, times: int) -> np.ndarray:
    for _ in range(times):
        x = np.r_[x[:1], 0.25 * x[:-2] + 0.5 * x[1:-1] + 0.25 * x[2:], x[-1:]] if len(x) > 2 else x
    return x


def chain_rings(p, oxygen, ss, colors, m: int = 6, scale: float = 1.0, tube: bool = False,
                coil_radius: float | None = None):
    """Cross-sections along one chain: (centre, normal, binormal, half-width, half-thickness,
    squareness, colour), one row per ring."""
    p = np.asarray(p, float).copy()
    n = len(p)
    ss = np.zeros(n, int) if tube else np.asarray(ss, int)
    strand = ss == STRAND
    inner = np.zeros(n, bool)
    inner[1:-1] = strand[1:-1]
    flat = p.copy()                       # strands zigzag (pleat): average it out so the arrow is straight
    flat[1:-1] = 0.25 * p[:-2] + 0.5 * p[1:-1] + 0.25 * p[2:]
    p[inner] = flat[inner]
    d = guide_normals(p, None if oxygen is None else np.asarray(oxygen, float))
    pos, tan, g = _spline(p, m)
    res = np.clip(np.floor(g + 0.5).astype(int), 0, n - 1)        # residue i owns g in [i − ½, i + ½)
    seg = np.minimum(g.astype(int), n - 2)
    t = (g - seg)[:, None]
    normal = _unit((1 - t) * d[seg] + t * d[seg + 1])
    normal = _unit(normal - (normal * tan).sum(axis=1, keepdims=True) * tan)
    binormal = np.cross(tan, normal)

    prof = dict(PROFILE)
    r = coil_radius if coil_radius is not None else (TUBE_RADIUS if tube else PROFILE[COIL][0])
    prof[COIL] = (r, r, 1.0)
    table = np.array([prof[k] for k in (COIL, HELIX, STRAND)])
    table[:, :2] *= scale
    per_res = table[ss]                                             # (n, 3)
    blend = max(1, m // 2)
    w, h, e = (_smooth(per_res[res, k], blend) for k in range(3))

    # arrow heads: the last residue of each strand widens abruptly, then tapers to the coil
    ends = np.flatnonzero(strand & ~np.r_[strand[1:], False])
    insert = []
    if len(ends):
        as_coil = per_res.copy()
        as_coil[ends] = table[COIL]
        w_after = _smooth(as_coil[res, 0], blend)
        for end in ends:
            f = g - (end - 0.5)
            head = (f >= 0) & (f <= 1.0)
            if not head.any():
                continue
            k0 = int(np.flatnonzero(head)[0])
            body = w[max(k0 - 1, 0)]
            w[head] = (1 - f[head]) * ARROW * table[STRAND, 0] + f[head] * table[COIL, 0]
            h[head], e[head] = table[STRAND, 1], table[STRAND, 2]
            tail = (f > 1.0) & (f <= 2.0)          # the residue after the tip: no bulge left over
            w[tail] = w_after[tail]
            insert.append((k0, body))
    rings = [pos, normal, binormal, w, h, e, np.asarray(colors, float)[res]]
    if insert:                                 # a second ring at the arrow's base makes the step
        at = [k for k, _ in insert]
        rings = [np.insert(a, at, a[at], axis=0) for a in rings]
        for j, (k, body) in enumerate(sorted(insert)):
            rings[3][k + j] = body
    return rings


def rings_to_quads(rings) -> tuple[np.ndarray, np.ndarray]:
    """Quads (q, 4, 3) wound so their normals point outwards, and a colour per quad."""
    pos, normal, binormal, w, h, e, col = rings
    k = len(pos)
    theta = (np.arange(SIDES) + 0.5) * 2 * np.pi / SIDES
    c, s = np.cos(theta)[None], np.sin(theta)[None]
    x = w[:, None] * np.sign(c) * np.abs(c) ** e[:, None]
    y = h[:, None] * np.sign(s) * np.abs(s) ** e[:, None]
    v = pos[:, None] + x[..., None] * normal[:, None] + y[..., None] * binormal[:, None]       # (k, SIDES, 3)
    nxt = np.roll(v, -1, axis=1)
    side = np.stack([v[:-1], nxt[:-1], nxt[1:], v[1:]], axis=2).reshape(-1, 4, 3)
    side_col = np.repeat(col[:-1], SIDES, axis=0)

    def cap(ring, centre, flip):
        a, b, d = ring[0::2], ring[1::2], np.roll(ring, -2, axis=0)[0::2]
        q = np.stack([np.broadcast_to(centre, a.shape), a, b, d], axis=1)
        return q[:, [0, 3, 2, 1]] if flip else q
    quads = np.concatenate([side, cap(v[0], pos[0], True), cap(v[-1], pos[-1], False)])
    colors = np.concatenate([side_col, np.tile(col[0], (SIDES // 2, 1)), np.tile(col[-1], (SIDES // 2, 1))])
    return quads, colors


def chain_mesh(p, oxygen, ss, colors, m: int = 6, scale: float = 1.0, tube: bool = False,
               coil_radius: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    return rings_to_quads(chain_rings(p, oxygen, ss, colors, m, scale, tube, coil_radius))
