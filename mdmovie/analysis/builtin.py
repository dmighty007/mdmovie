"""Built-in analysis presets (all based on MDAnalysis)."""
from __future__ import annotations

import warnings

import numpy as np

from mdmovie.analysis.registry import (Bool, Choice, File, Float, Int, Matrix, Profile, Sel, Str, TimeSeries,
                                       iter_frames, preset, run_analysis)


def _times(u, frames):
    start, stop, step = frames
    idx = range(*slice(start, stop, step).indices(u.trajectory.n_frames))
    t0 = u.trajectory[0].time
    dt = u.trajectory.dt
    return np.array([t0 + i * dt for i in idx])


def _select(u, sel, what="selection"):
    ag = u.select_atoms(sel)
    if ag.n_atoms == 0:
        raise ValueError(f"{what} '{sel}' matched no atoms")
    return ag


# --- structure ------------------------------------------------------------------------------

@preset("RMSD", category="Structure", units="Å",
        params=[Sel("select", "backbone", help="Atoms used for superposition and RMSD"),
                Int("ref_frame", 0, "Reference frame", minimum=0),
                Str("extra_groups", "", "Extra groups (;-separated)",
                    help="Optional selections whose RMSD is computed after fitting on 'select'")])
def rmsd(u, frames, progress, cancelled, select, ref_frame, extra_groups):
    """RMSD after optimal superposition onto a reference frame."""
    from MDAnalysis.analysis import rms
    _select(u, select)
    groups = [g.strip() for g in extra_groups.split(";") if g.strip()]
    r = rms.RMSD(u, u, select=select, ref_frame=ref_frame, groupselections=groups or None)
    run_analysis(r, frames, progress, cancelled)
    res = r.results.rmsd
    return TimeSeries(res[:, 1], res[:, 2:], [f"RMSD {select}"] + [f"RMSD {g}" for g in groups], "Å")


@preset("Radius of gyration", category="Structure", units="Å",
        params=[Sel("select", "protein"), Bool("mass_weighted", True)])
def radius_of_gyration(u, frames, progress, cancelled, select, mass_weighted):
    """Radius of gyration of a selection."""
    ag = _select(u, select)
    t, v = [], []
    for ts in iter_frames(u, frames, progress, cancelled):
        t.append(ts.time)
        if mass_weighted:
            v.append(ag.radius_of_gyration())
        else:
            x = ag.positions - ag.positions.mean(axis=0)
            v.append(np.sqrt((x * x).sum(axis=1).mean()))
    return TimeSeries(t, v, ["Rg"], "Å")


@preset("RMSF per residue", kind="profile", category="Structure", units="Å",
        params=[Sel("select", "protein and name CA"), Int("ref_frame", 0, "Reference frame", minimum=0)])
def rmsf(u, frames, progress, cancelled, select, ref_frame):
    """Root-mean-square fluctuation of each atom after superposition onto a reference frame
    (use one atom per residue, e.g. CA, for a per-residue profile)."""
    from MDAnalysis.analysis.align import rotation_matrix
    ag = _select(u, select)
    u.trajectory[ref_frame]
    ref = ag.positions - ag.center_of_geometry()
    n, mean, m2 = 0, np.zeros_like(ref, dtype=float), np.zeros_like(ref, dtype=float)
    for _ in iter_frames(u, frames, progress, cancelled):
        pos = ag.positions - ag.center_of_geometry()
        R, _ = rotation_matrix(pos, ref)
        x = pos @ np.asarray(R).T
        n += 1
        delta = x - mean
        mean += delta / n
        m2 += delta * (x - mean)
    values = np.sqrt(m2.sum(axis=1) / max(n, 1))
    return Profile(ag.resids, values, ["RMSF"], "Residue", "Å")


@preset("End-to-end distance", category="Structure", units="Å",
        params=[Sel("select", "protein and name CA")])
def end_to_end(u, frames, progress, cancelled, select):
    """Distance between the first and last atom of a selection."""
    ag = _select(u, select)
    a, b = ag[0], ag[-1]
    t, v = [], []
    for ts in iter_frames(u, frames, progress, cancelled):
        t.append(ts.time)
        v.append(np.linalg.norm(a.position - b.position))
    return TimeSeries(t, v, ["end-to-end"], "Å")


# --- geometry -------------------------------------------------------------------------------

@preset("Distance between selections", category="Geometry", units="Å",
        params=[Sel("select_a", "resid 1-10", "Selection A"), Sel("select_b", "resid 100-110", "Selection B"),
                Choice("mode", ["center of mass", "center of geometry", "minimum"])])
def distance(u, frames, progress, cancelled, select_a, select_b, mode):
    """Distance between two selections (centres or closest atoms, periodic box aware)."""
    from MDAnalysis.lib.distances import distance_array
    ga, gb = _select(u, select_a, "Selection A"), _select(u, select_b, "Selection B")
    t, v = [], []
    for ts in iter_frames(u, frames, progress, cancelled):
        t.append(ts.time)
        if mode == "minimum":
            v.append(distance_array(ga.positions, gb.positions, box=ts.dimensions).min())
        else:
            f = "center_of_mass" if mode == "center of mass" else "center_of_geometry"
            v.append(distance_array(getattr(ga, f)()[None], getattr(gb, f)()[None], box=ts.dimensions)[0, 0])
    return TimeSeries(t, v, [f"d({select_a}, {select_b})"], "Å")


@preset("Dihedral angle", category="Geometry", units="°",
        params=[Str("resids", "10", "Residue ids (comma-separated)"),
                Choice("angle", ["phi", "psi", "omega", "chi1"]),
                Str("segid", "", "Segment id (optional)")])
def dihedral(u, frames, progress, cancelled, resids, angle, segid):
    """Backbone or side-chain dihedral of one or more residues."""
    groups, labels = [], []
    for rid in [int(r) for r in resids.replace(" ", "").split(",") if r]:
        sel = f"resid {rid}" + (f" and segid {segid}" if segid else "")
        res = _select(u, sel).residues[0]
        ag = getattr(res, f"{angle}_selection")()
        if ag is None:
            raise ValueError(f"{angle} is not defined for residue {res.resname}{rid}")
        groups.append(ag)
        labels.append(f"{angle} {res.resname}{rid}")
    t, v = [], []
    for ts in iter_frames(u, frames, progress, cancelled):
        t.append(ts.time)
        v.append([g.dihedral.value() for g in groups])
    return TimeSeries(t, v, labels, "°")


@preset("Atoms within cutoff", category="Geometry", units="count",
        params=[Sel("select", "resname TIP3 and name OH2", "Count atoms of"),
                Sel("around", "protein", "Around selection"), Float("cutoff", 3.5, "Cutoff (Å)", minimum=0)])
def count_within(u, frames, progress, cancelled, select, around, cutoff):
    """Number of atoms (e.g. waters or ions) within a cutoff of a selection."""
    ag = u.select_atoms(f"({select}) and around {cutoff} ({around})", updating=True)
    t, v = [], []
    for ts in iter_frames(u, frames, progress, cancelled):
        t.append(ts.time)
        v.append(ag.n_atoms)
    return TimeSeries(t, v, [f"n within {cutoff} Å"], "count")


# --- interactions ---------------------------------------------------------------------------

@preset("Hydrogen bonds", category="Interactions", units="count",
        params=[Str("hydrogens_sel", "", "Hydrogens (blank = guess)"),
                Str("acceptors_sel", "", "Acceptors (blank = guess)"),
                Sel("guess_within", "protein", "Guess within"),
                Float("d_a_cutoff", 3.0, "D–A cutoff (Å)", minimum=0),
                Float("angle_cutoff", 150.0, "D–H–A angle (°)", minimum=0, maximum=180)])
def hbonds(u, frames, progress, cancelled, hydrogens_sel, acceptors_sel, guess_within, d_a_cutoff, angle_cutoff):
    """Number of hydrogen bonds per frame (needs charges in the topology to guess selections)."""
    from MDAnalysis.analysis.hydrogenbonds import HydrogenBondAnalysis as HBA
    h = HBA(u, d_a_cutoff=d_a_cutoff, d_h_a_angle_cutoff=angle_cutoff)
    h.hydrogens_sel = hydrogens_sel or f"({h.guess_hydrogens(guess_within)})"
    h.acceptors_sel = acceptors_sel or f"({h.guess_acceptors(guess_within)})"
    run_analysis(h, frames, progress, cancelled)
    return TimeSeries(h.times, h.count_by_time(), ["H-bonds"], "count")


@preset("Native contacts (Q)", category="Interactions", units="Q",
        params=[Sel("select_a", "protein and not name H*", "Selection A"),
                Sel("select_b", "protein and not name H*", "Selection B"),
                Int("ref_frame", 0, "Reference frame", minimum=0),
                Float("radius", 4.5, "Radius (Å)", minimum=0),
                Choice("method", ["hard_cut", "soft_cut", "radius_cut"])])
def native_contacts(u, frames, progress, cancelled, select_a, select_b, ref_frame, radius, method):
    """Fraction of reference-frame contacts that are present in each frame."""
    from MDAnalysis.analysis import contacts
    ga, gb = _select(u, select_a), _select(u, select_b)
    u.trajectory[ref_frame]  # Contacts reads the reference distances from the current frame
    c = contacts.Contacts(u, select=(select_a, select_b), refgroup=(ga, gb), radius=radius, method=method)
    run_analysis(c, frames, progress, cancelled)
    return TimeSeries(_times(u, frames), c.results.timeseries[:, 1], ["Q"], "Q")


# --- secondary structure --------------------------------------------------------------------

DSSP_CODES = ["-", "H", "E"]
DSSP_NAMES = ["Coil", "Helix", "Strand"]


def _dssp(u, frames, progress, cancelled, select):
    from MDAnalysis.analysis.dssp import DSSP
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        d = DSSP(u.select_atoms(select), guess_hydrogens=True)
        run_analysis(d, frames, progress, cancelled)
    ss = np.asarray(d.results.dssp)
    codes = np.zeros(ss.shape, dtype=float)
    for i, c in enumerate(DSSP_CODES):
        codes[ss == c] = i
    return d, codes


@preset("Secondary structure (DSSP)", kind="matrix", category="Secondary structure",
        params=[Sel("select", "protein")])
def dssp_matrix(u, frames, progress, cancelled, select):
    """Per-residue secondary structure (coil / helix / strand) over time."""
    d, codes = _dssp(u, frames, progress, cancelled, select)
    resids = [str(r) for r in d.results.resids]
    return Matrix(_times(u, frames), codes, resids, DSSP_NAMES, "")


@preset("Secondary structure fractions", category="Secondary structure", units="fraction",
        params=[Sel("select", "protein")])
def dssp_fractions(u, frames, progress, cancelled, select):
    """Fraction of residues in helix, strand and coil per frame."""
    _, codes = _dssp(u, frames, progress, cancelled, select)
    fr = np.stack([(codes == i).mean(axis=1) for i in (1, 2, 0)], axis=1)
    return TimeSeries(_times(u, frames), fr, ["Helix", "Strand", "Coil"], "fraction")


# --- custom ---------------------------------------------------------------------------------

@preset("Custom expression", category="Custom",
        params=[Str("expression", "u.select_atoms('protein').radius_of_gyration()", "Expression",
                    help="Evaluated every frame with u, ts, np, mda in scope; return a number or list"),
                Str("labels", "value", "Labels (comma-separated)"), Str("units", "", "Units")])
def custom_expression(u, frames, progress, cancelled, expression, labels, units):
    """Evaluate a Python expression on every frame."""
    import MDAnalysis as mda
    code = compile(expression, "<expression>", "eval")
    t, v = [], []
    for ts in iter_frames(u, frames, progress, cancelled):
        t.append(ts.time)
        v.append(np.atleast_1d(np.asarray(eval(code, {"np": np, "mda": mda, "u": u, "ts": ts}), float)))
    labs = [s.strip() for s in labels.split(",") if s.strip()]
    return TimeSeries(t, np.array(v), labs if len(labs) == len(v[0]) else [], units)


# --- data files -------------------------------------------------------------------------------

@preset("Data file (COLVAR / xvg / csv)", category="Data files", needs_universe=False,
        params=[File("path", "", "File", help="PLUMED COLVAR, GROMACS .xvg, CSV or whitespace-separated table"),
                Int("time_column", 0, "Time column", minimum=0),
                Str("columns", "", "Value columns (e.g. 1,2 or 1-3; blank = all)"),
                Float("time_scale", 1.0, "Time unit → ps factor", minimum=1e-12,
                      help="Multiply the time column by this to get ps (1000 if the file is in ns)"),
                Str("units", "", "Units")])
def data_file(u, frames, progress, cancelled, path, time_column, columns, time_scale, units):
    """Columns from a text data file, e.g. collective variables from PLUMED, energies from GROMACS.
    No trajectory needed; the first column is time unless you choose another."""
    from mdmovie.sources.datafile import parse_columns, read_table
    if not path:
        raise ValueError("Choose a data file")
    table, names = read_table(path)
    cols = [c for c in parse_columns(columns, table.shape[1]) if c != time_column]
    bad = [c for c in cols if c >= table.shape[1]]
    if bad:
        raise ValueError(f"Column(s) {bad} do not exist; the file has columns 0–{table.shape[1] - 1}: "
                         + ", ".join(names))
    start, stop, step = frames
    rows = table[slice(start, stop, step)]
    return TimeSeries(rows[:, time_column] * time_scale, rows[:, cols], [names[c] for c in cols], units)
