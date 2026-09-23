"""Example user preset. Copy this file to ~/.config/mdmovie/presets/ (or <project dir>/presets/)
and choose Analysis › Reload preset files; it then appears in the series dialog."""
import numpy as np

from mdmovie.analysis.registry import Float, Sel, TimeSeries, iter_frames, preset


@preset("Salt bridge distance", category="Interactions", units="Å",
        params=[Sel("acidic", "resid 22 and name OE1 OE2", "Acidic oxygens"),
                Sel("basic", "resid 23 and name NZ", "Basic nitrogens"),
                Float("cutoff", 4.0, "Formed below (Å)", minimum=0)])
def salt_bridge(u, frames, progress, cancelled, acidic, basic, cutoff):
    """Minimum distance between two charged groups, plus a 0/1 'formed' column."""
    from MDAnalysis.lib.distances import distance_array
    a, b = u.select_atoms(acidic), u.select_atoms(basic)
    if not a or not b:
        raise ValueError("a selection matched no atoms")  # shown next to the series in the inspector
    t, d = [], []
    for ts in iter_frames(u, frames, progress, cancelled):  # reports progress and handles Cancel
        t.append(ts.time)
        d.append(distance_array(a.positions, b.positions, box=ts.dimensions).min())
    d = np.array(d)
    return TimeSeries(t, np.column_stack([d, d < cutoff]), ["distance", "formed"], "Å")
