"""What to draw: atoms, bonds, colours and secondary structure of each representation, plus the
per-frame coordinates (aligned and optionally smoothed)."""
from __future__ import annotations

import json
import threading
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from mdmovie.sources.pbc import SOLVENT_AND_IONS  # noqa: F401  (re-exported)

STYLES = ("cartoon", "tube", "licorice", "ball and stick", "spheres", "lines")
COLOR_SCHEMES = ("secondary structure", "element", "chain", "rainbow", "single colour")
ALIGN_MODES = ("fit to first frame", "centre only", "none")
DEFAULT_REPS = [
    {"sel": "protein or nucleic", "style": "cartoon", "color": "secondary structure", "custom": "#4c8bf5",
     "scale": 1.0},
    {"sel": f"not (protein or nucleic or water) and not (resname {SOLVENT_AND_IONS})",
     "style": "ball and stick", "color": "element", "custom": "#4c8bf5", "scale": 1.0},
]

#            mass     colour     vdW   covalent
ELEMENTS = {
    "H": (1.008, "#f2f2f2", 1.20, 0.31), "C": (12.011, "#8c8f94", 1.70, 0.76),
    "N": (14.007, "#3050f8", 1.55, 0.71), "O": (15.999, "#f0322d", 1.52, 0.66),
    "F": (18.998, "#73d14a", 1.47, 0.57), "NA": (22.990, "#ab5cf2", 2.27, 0.0),
    "MG": (24.305, "#5aa800", 1.73, 0.0), "P": (30.974, "#ff8000", 1.80, 1.07),
    "S": (32.06, "#e6c200", 1.80, 1.05), "CL": (35.45, "#1fbf1f", 1.75, 1.02),
    "K": (39.098, "#8f40d4", 2.75, 0.0), "CA": (40.078, "#3aa53a", 2.31, 0.0),
    "FE": (55.845, "#e06633", 2.00, 0.0), "ZN": (65.38, "#7d80b0", 1.39, 0.0),
    "BR": (79.904, "#a62929", 1.85, 1.20), "I": (126.904, "#940094", 1.98, 1.39),
}
UNKNOWN = (0.0, "#d98cb3", 1.80, 0.9)
SS_COLORS = ("#d5d8dd", "#d62728", "#1f77b4")          # coil, helix, strand (as in the DSSP heatmap)
CHAIN_COLORS = ("#4c8bf5", "#e8710a", "#34a853", "#a142f4", "#ea4335", "#12b5cb", "#f9ab00", "#8c564b")
COIL, HELIX, STRAND = 0, 1, 2


def hex_rgb(color: str) -> np.ndarray:
    c = color.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], float) / 255.0


def guess_elements(atoms) -> list[str]:
    """Element symbols (upper case) from the topology, else from masses, else from atom names."""
    try:
        el = [str(e).upper() for e in atoms.elements]
        if all(el):
            return el
    except Exception:
        pass
    names = [str(n) for n in atoms.names]
    try:
        masses = np.asarray(atoms.masses, float)
    except Exception:
        masses = np.zeros(len(names))
    symbols = list(ELEMENTS)
    table = np.array([ELEMENTS[s][0] for s in symbols])
    out = []
    for name, m in zip(names, masses):
        if m > 0.5:
            k = int(np.abs(table - m).argmin())
            if abs(table[k] - m) < 0.6:
                out.append(symbols[k])
                continue
        letters = "".join(ch for ch in name if ch.isalpha()).upper()
        out.append(letters[:1] if letters[:1] in ELEMENTS else "X")
    return out


def find_bonds(atoms, positions: np.ndarray, elements: list[str]) -> np.ndarray:
    """(m, 2) local index pairs: bonds of the topology if it has them, else guessed from distances."""
    n = len(atoms)
    if n < 2:
        return np.zeros((0, 2), int)
    u = atoms.universe
    try:
        if len(u.bonds):
            local = np.full(u.atoms.n_atoms, -1)
            local[atoms.indices] = np.arange(n)
            pairs = local[u.bonds.indices]
            return pairs[(pairs >= 0).all(axis=1)]
    except Exception:
        pass
    from MDAnalysis.lib.distances import self_capped_distance
    rc = np.array([ELEMENTS.get(e, UNKNOWN)[3] for e in elements])
    pairs, d = self_capped_distance(positions.astype(np.float32), 2.3, min_cutoff=0.4)
    i, j = pairs[:, 0], pairs[:, 1]
    hydrogen = np.array([e == "H" for e in elements])
    ok = (rc[i] > 0) & (rc[j] > 0) & (d < rc[i] + rc[j] + 0.45) & ~(hydrogen[i] & hydrogen[j])
    return pairs[ok]


def kabsch(mobile: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rotation R and centroids (cm, cr) so that (x − cm) @ R.T + cr puts `mobile` onto `ref`."""
    cm, cr = mobile.mean(axis=0), ref.mean(axis=0)
    h = (mobile - cm).T @ (ref - cr)
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    r = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return r, cm, cr


def geometric_ss(ca: np.ndarray) -> np.ndarray:
    """Secondary structure from Cα positions alone (P-SEA style distance criteria): used when DSSP
    cannot run, e.g. for Cα-only or coarse-grained models."""
    n = len(ca)
    ss = np.zeros(n, int)
    if n < 5:
        return ss

    def dist(k):
        return np.linalg.norm(ca[k:] - ca[:-k], axis=1)
    d2, d3, d4 = dist(2), dist(3), dist(4)
    for i in range(n - 4):
        if abs(d2[i] - 5.5) < 0.6 and abs(d3[i] - 5.3) < 0.7 and abs(d4[i] - 6.4) < 0.8:
            ss[i:i + 5] = HELIX
    for i in range(n - 4):
        if abs(d2[i] - 6.7) < 0.7 and abs(d3[i] - 9.9) < 1.1 and abs(d4[i] - 12.4) < 1.4:
            ss[i:i + 5] = np.where(ss[i:i + 5] == HELIX, HELIX, STRAND)
    return ss


def clean_ss(ss: np.ndarray, breaks: np.ndarray) -> np.ndarray:
    """Turn helices shorter than 4 and strands shorter than 2 residues into coil (they draw badly).
    `breaks[i]` is True where residue i starts a new chain."""
    ss = ss.copy()
    n = len(ss)
    start = 0
    for i in range(1, n + 1):
        if i == n or ss[i] != ss[start] or breaks[i]:
            if (ss[start] == HELIX and i - start < 4) or (ss[start] == STRAND and i - start < 2):
                ss[start:i] = COIL
            start = i
    return ss


@dataclass
class Chain:
    """One continuous backbone stretch drawn as a cartoon or tube."""
    trace: np.ndarray                 # global atom indices of Cα (or P for nucleic acids)
    oxygen: np.ndarray | None         # global indices of the carbonyl O of each residue (None: unknown)
    rows: np.ndarray                  # row of each residue in the scene's secondary-structure array (−1: none)
    colors: np.ndarray                # (n, 3) per residue; overridden per frame for the ss scheme
    nucleic: bool = False


@dataclass
class Rep:
    style: str
    scheme: str
    scale: float
    index: np.ndarray                              # global atom indices
    colors: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    radii: np.ndarray = field(default_factory=lambda: np.zeros(0))     # van der Waals
    bonds: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), int))
    chains: list[Chain] = field(default_factory=list)
    residues: np.ndarray = field(default_factory=lambda: np.zeros(0, int))  # residue index of each atom
    error: str = ""


class Scene:
    """A trajectory plus the representations to draw. Owns its own Universe (they are not thread-safe),
    so there is one Scene per thread."""

    def __init__(self, traj, resolve, reps: list[dict], align: str, align_sel: str):
        self.u = traj.universe(resolve)
        self.n_frames = self.u.trajectory.n_frames
        self.align = align
        self._frames: OrderedDict[int, np.ndarray] = OrderedDict()
        self._ss: dict[int, np.ndarray] = {}
        self.u.trajectory[0]
        first = self.u.atoms.positions.astype(float)

        # secondary structure is tracked for every protein residue that has a Cα
        ca = self.u.select_atoms("protein and name CA")
        self._ss_ca = ca.indices
        self._ss_row = np.full(self.u.residues.n_residues, -1)
        self._ss_row[ca.resindices] = np.arange(len(ca))
        seg = ca.segindices
        gap = np.linalg.norm(np.diff(first[ca.indices], axis=0), axis=1) > 4.3 if len(ca) > 1 else np.zeros(0, bool)
        self._ss_breaks = np.r_[True, (seg[1:] != seg[:-1]) | gap] if len(ca) else np.zeros(0, bool)

        self.reps = [self._build_rep(r, first) for r in reps]
        shown = np.unique(np.concatenate([r.index for r in self.reps] or [np.zeros(0, int)])).astype(int)
        self.empty = len(shown) == 0
        fit = np.zeros(0, int)
        if align != "none":
            try:
                fit = self.u.select_atoms(align_sel).indices if align_sel.strip() else fit
            except Exception:
                fit = np.zeros(0, int)
            if len(fit) < 3:
                fit = shown                      # e.g. no protein: fit on whatever is displayed
        self._fit = fit
        self._fit_ref = first[fit] if len(fit) else None
        pts = first[shown] if len(shown) else np.zeros((1, 3))
        self.centre = 0.5 * (pts.min(axis=0) + pts.max(axis=0))
        self.radius = float(max(np.linalg.norm(pts - self.centre, axis=1).max() + 2.0, 4.0))

    # --- representations ----------------------------------------------------------------------
    def _build_rep(self, r: dict, first: np.ndarray) -> Rep:
        style = r.get("style", "cartoon")
        scheme = r.get("color", "element")
        rep = Rep(style, scheme, float(r.get("scale", 1.0) or 1.0), np.zeros(0, int))
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                atoms = self.u.select_atoms(r.get("sel", "all") or "all")
        except Exception as e:
            rep.error = f"selection '{r.get('sel')}': {e}"
            return rep
        if not len(atoms):
            return rep
        custom = hex_rgb(r.get("custom") or "#4c8bf5")
        if style in ("cartoon", "tube"):
            rep.chains = self._chains(atoms, first, scheme, custom)
            rep.index = np.concatenate([c.trace for c in rep.chains] or [np.zeros(0, int)])
            return rep
        rep.index = atoms.indices
        rep.residues = atoms.resindices
        elements = guess_elements(atoms)
        rep.radii = np.array([ELEMENTS.get(e, UNKNOWN)[2] for e in elements])
        rep.colors = self._atom_colors(atoms, elements, scheme, custom)
        if style != "spheres":
            rep.bonds = find_bonds(atoms, first[atoms.indices], elements)
        return rep

    def _atom_colors(self, atoms, elements, scheme, custom) -> np.ndarray:
        n = len(atoms)
        if scheme == "element":
            return np.array([hex_rgb(ELEMENTS.get(e, UNKNOWN)[1]) for e in elements])
        if scheme == "chain":
            return _palette(atoms.segindices, CHAIN_COLORS)
        if scheme == "rainbow":
            return _rainbow(_rank(atoms.resindices))
        if scheme == "secondary structure":
            return self.ss_colors(atoms.resindices, self.ss(0))
        return np.tile(custom, (n, 1))

    def _chains(self, atoms, first, scheme, custom) -> list[Chain]:
        chains = []
        for nucleic, sel, gap in ((False, "protein and name CA", 4.3), (True, "nucleic and name P", 8.0)):
            trace = atoms.select_atoms(sel)
            if len(trace) < 2:
                continue
            oxygen = None
            if not nucleic:
                o = atoms.select_atoms("name O OT1 O1")
                lookup = np.full(self.u.residues.n_residues, -1)
                lookup[o.resindices[::-1]] = o.indices[::-1]         # first matching atom of each residue
                found = lookup[trace.resindices]
                oxygen = found if (found >= 0).all() else None
            if scheme == "chain":
                colors = _palette(trace.segindices, CHAIN_COLORS)
            elif scheme == "rainbow":
                colors = _rainbow(np.linspace(0, 1, len(trace)))
            else:      # "secondary structure" is filled in per frame; "element" has no meaning here
                colors = np.tile(custom if scheme == "single colour" else hex_rgb(SS_COLORS[0]), (len(trace), 1))
            d = np.linalg.norm(np.diff(first[trace.indices], axis=0), axis=1)
            cut = np.flatnonzero((d > gap) | (trace.segindices[1:] != trace.segindices[:-1])) + 1
            for part in np.split(np.arange(len(trace)), cut):
                if len(part) < 2:
                    continue
                rows = np.full(len(part), -1) if nucleic else self._ss_row[trace.resindices[part]]
                chains.append(Chain(trace.indices[part], None if oxygen is None else oxygen[part], rows,
                                    colors[part], nucleic))
        return chains

    # --- per-frame data -----------------------------------------------------------------------
    def _read(self, i: int) -> np.ndarray:
        if i in self._frames:
            self._frames.move_to_end(i)
            return self._frames[i]
        self.u.trajectory[i]
        x = self.u.atoms.positions.astype(float)
        if self._fit_ref is not None and len(self._fit):
            if self.align == "fit to first frame":
                r, cm, cr = kabsch(x[self._fit], self._fit_ref)
                x = (x - cm) @ r.T + cr
            elif self.align == "centre only":
                x = x - x[self._fit].mean(axis=0) + self._fit_ref.mean(axis=0)
        self._frames[i] = x
        while len(self._frames) > 48:
            self._frames.popitem(last=False)
        return x

    def positions(self, i: int, smooth: int = 1) -> np.ndarray:
        """Coordinates of every atom at frame i, aligned; averaged over `smooth` frames around i."""
        i = int(min(max(i, 0), self.n_frames - 1))
        half = max(0, int(smooth) - 1) // 2
        if half == 0:
            return self._read(i)
        window = range(max(0, i - half), min(self.n_frames, i + half + 1))
        return np.mean([self._read(k) for k in window], axis=0)

    def ss_colors(self, residues: np.ndarray, ss: np.ndarray) -> np.ndarray:
        """Secondary-structure colour of atoms in these residues (coil for residues without a Cα)."""
        rows = self._ss_row[residues]
        codes = np.where(rows >= 0, ss[np.maximum(rows, 0)], COIL) if len(ss) else np.full(len(residues), COIL)
        return np.array([hex_rgb(c) for c in SS_COLORS])[codes]

    def ss_at(self, i: int, smooth: int = 1) -> np.ndarray:
        """Secondary structure at frame i; with smoothing, each residue takes its most frequent state over the
        same window of frames as the coordinates, so the cartoon doesn't flicker as DSSP changes its mind."""
        i = int(min(max(i, 0), self.n_frames - 1))
        half = max(0, int(smooth) - 1) // 2
        if half == 0 or len(self._ss_ca) == 0:
            return self.ss(i)
        window = np.array([self.ss(k) for k in range(max(0, i - half), min(self.n_frames, i + half + 1))])
        votes = np.stack([(window == c).sum(axis=0) for c in (COIL, HELIX, STRAND)])   # row = code
        own = self.ss(i)
        tie = votes.max(axis=0) == votes[own, np.arange(len(own))]     # a tie keeps the frame's own state
        return np.where(tie, own, votes.argmax(axis=0))

    def ss(self, i: int) -> np.ndarray:
        """Secondary-structure code (0 coil, 1 helix, 2 strand) of every tracked protein residue."""
        if i not in self._ss:
            self._ss[i] = self._compute_ss(i)
        return self._ss[i]

    def _compute_ss(self, i: int) -> np.ndarray:
        n = len(self._ss_ca)
        if n == 0:
            return np.zeros(0, int)
        codes = None
        try:
            from MDAnalysis.analysis.dssp import DSSP
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                d = DSSP(self.u.select_atoms("protein"), guess_hydrogens=True)
                d.run(start=i, stop=i + 1)
            found = np.asarray(d.results.dssp[0])
            if len(found) == n:
                codes = np.select([found == "H", found == "E"], [HELIX, STRAND], COIL)
        except Exception:
            codes = None
        if codes is None:
            self.u.trajectory[i]
            codes = geometric_ss(self.u.atoms.positions[self._ss_ca].astype(float))
        return clean_ss(codes, self._ss_breaks)


def _rank(values: np.ndarray) -> np.ndarray:
    """0..1 by order of the distinct values."""
    uniq, inv = np.unique(values, return_inverse=True)
    return inv / max(len(uniq) - 1, 1)


def _rainbow(f: np.ndarray) -> np.ndarray:
    from matplotlib import colormaps
    return colormaps["turbo"](0.08 + 0.84 * np.asarray(f, float))[:, :3]


def _palette(keys: np.ndarray, colors) -> np.ndarray:
    _, inv = np.unique(keys, return_inverse=True)
    return np.array([hex_rgb(c) for c in colors])[inv % len(colors)]


# --- scene cache ----------------------------------------------------------------------------------

_scenes: OrderedDict = OrderedDict()
_scene_lock = threading.Lock()


def get_scene(project, traj_id: str, reps: list[dict], align: str, align_sel: str) -> Scene:
    """The Scene for these settings, built once per thread (loading a Universe takes a while)."""
    traj = project.trajectories[traj_id]
    key = (traj.signature(project.resolve_path), json.dumps(reps, sort_keys=True), align, align_sel,
           threading.get_ident())
    with _scene_lock:
        if key in _scenes:
            _scenes.move_to_end(key)
            return _scenes[key]
    scene = Scene(traj, project.resolve_path, reps, align, align_sel)
    with _scene_lock:
        _scenes[key] = scene
        while len(_scenes) > 6:
            _scenes.popitem(last=False)
    return scene
