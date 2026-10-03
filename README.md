<div align="center">

# MD Movie Maker

**Turn a molecular dynamics trajectory into a multi-panel movie:**<br>
the molecule, live analysis plots and labels, all in step, exported to MP4 or GIF.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab)](https://github.com/dmighty007/mdmovie/blob/main/pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/dmighty007/mdmovie/blob/main/LICENSE)
[![Tutorial](https://img.shields.io/badge/docs-illustrated%20tutorial-ff6d00)](https://github.com/dmighty007/mdmovie/blob/main/docs/TUTORIAL.md)
[![MDAnalysis](https://img.shields.io/badge/built%20on-MDAnalysis-f37021)](https://www.mdanalysis.org)

<img src="https://raw.githubusercontent.com/dmighty007/mdmovie/main/docs/images/quickstart.gif" width="760" alt="Adenylate kinase opening, drawn from the trajectory, next to its backbone RMSD and radius of gyration">

<sub>Made by one command, <code>mdmovie adk.psf adk.dcd</code>: adenylate kinase opening up, drawn from the trajectory, with backbone RMSD and radius of gyration.</sub>

</div>

## Try it in a minute

```bash
pip install "mdmovie[demo]"     # everything comes from pip, ffmpeg included; [demo] adds the example data
mdmovie --example               # the movie above, open in the app: drag the protein to turn it
```

With your own simulation:

```bash
mdmovie md.tpr md.xtc                    # a ready-made movie, open in the app to adjust
mdmovie render md.tpr md.xtc md.mp4      # or straight to MP4, with no display (clusters, scripts)
```

Any topology and trajectory that [MDAnalysis](https://userguide.mdanalysis.org/stable/formats/index.html) reads works: GROMACS, AMBER, CHARMM/NAMD, OpenMM, LAMMPS and more. Give the files in any order; several trajectories are joined in the order given. You can also drop the files on the window.

You get the molecule as a cartoon, RMSD and Rg plots that grow in step with it, and a time label. Molecules split across the periodic box are joined on the fly, and long runs are sped up to about 30 s. From there, change anything: the layout, the representations, the analyses, the timing.

## What else you can make

<table>
<tr>
<td width="50%" valign="top">
<img src="https://raw.githubusercontent.com/dmighty007/mdmovie/main/docs/images/demo.gif" alt="Rendered protein frames next to synced RMSD, Rg and secondary-structure plots"><br>
<b>Frames from your favourite viewer.</b> Images rendered in VMD, PyMOL or ChimeraX (here, simple stand-in drawings) next to synced RMSD, Rg and DSSP panels, with a clock in the corner.
</td>
<td width="50%" valign="top">
<img src="https://raw.githubusercontent.com/dmighty007/mdmovie/main/docs/images/fes_demo.gif" alt="A trajectory moving on a free-energy surface with a fading trail"><br>
<b>Trajectories on free-energy surfaces.</b> The current CV values move with a fading trail over a FES computed from PLUMED COLVAR, <code>.xvg</code> or CSV data, or over your own picture of one.
</td>
</tr>
</table>

<img src="https://raw.githubusercontent.com/dmighty007/mdmovie/main/docs/images/15_molecule_styles.png" alt="Cartoon, licorice, ball-and-stick and sphere representations drawn by the built-in renderer">

<sub>The built-in renderer: cartoon by secondary structure, rainbow with licorice side chains, ball-and-stick, spheres. No OpenGL needed, so it renders on headless machines too.</sub>

## Features

**The molecule**
- Drawn in the app from the trajectory: cartoon (helix ribbons, strand arrows), tube, licorice, ball-and-stick, spheres and lines, coloured by secondary structure, element, chain or residue. Drag to rotate, scroll to zoom.
- Helices and strands form and melt as the movie plays (DSSP, smoothed so the cartoon doesn't flicker). Overall tumbling is fitted away, and frames can be smoothed.
- **Periodic boundaries handled:** molecules split across the box are made whole on the fly, as `gmx trjconv -pbc mol -center` does, for drawing *and* for analysis. An RMSD or Rg of a broken protein is meaningless.
- Or bring frames rendered elsewhere as an image sequence, cropped to a fixed aspect ratio. *Auto-trim* fits the crop around the molecule over the whole run.

**Analysis and plots**
- Built-in presets run on the trajectory: RMSD, Rg, RMSF, distances, dihedrals, H-bonds, native contacts (Q), DSSP, atoms within a cutoff, custom Python expressions. They run in the background and the results are cached.
- PLUMED COLVAR, GROMACS `.xvg` and CSV files load directly; no trajectory needed.
- Four animation styles: the line grows, a marker moves, the axis scrolls, or the plot stays still, with an optional live value readout. Twin y-axes, several trajectories per plot.
- Publication styles ([SciencePlots](https://github.com/garrettj403/SciencePlots) science, nature, ieee), optional LaTeX, full control over ticks, limits, log scales, reference lines and legends.
- [Your own presets](https://github.com/dmighty007/mdmovie/blob/main/docs/TUTORIAL.md#12-write-your-own-analysis-preset) take a few lines of Python.

**Layout and timing**
- Any layout: split, resize and swap cells, or start from a template. Labels, logos and inset plots float on top. Resolution-independent: 1080p, 4K, square, portrait.
- Sync groups map movie frames to simulation time. Shift, speed up, trim, hold, hide or loop each group, so panels play in step or each on its own clock.

**Output**
- MP4 (H.264), GIF or a PNG sequence from the app, or headless with `mdmovie render`. The preview shows exactly what gets exported.
- Undo/redo for every edit. Projects are small JSON files with relative paths, easy to version and share.

## Learn more

The **[illustrated tutorial](https://github.com/dmighty007/mdmovie/blob/main/docs/TUTORIAL.md)** builds a complete movie step by step in about 15 minutes with the example data. It covers layouts, image sequences and cropping, timing, plot styles, free-energy surfaces and export, plus [troubleshooting](https://github.com/dmighty007/mdmovie/blob/main/docs/TUTORIAL.md#13-troubleshooting).

<img src="https://raw.githubusercontent.com/dmighty007/mdmovie/main/docs/images/08_sync.png" alt="The app: project tree, canvas, inspector and timeline">

## Install

```bash
pip install mdmovie            # the app
pip install "mdmovie[demo]"    # plus MDAnalysisTests, for the example trajectory and the tutorial
```

Python 3.10 or newer. Everything, ffmpeg included, comes from pip.

| Command | What it does |
|---|---|
| `mdmovie` | open the app |
| `mdmovie md.tpr md.xtc [more.xtc …]` | open a ready-made movie of a trajectory |
| `mdmovie --example` | the same for the adenylate kinase example |
| `mdmovie movie.mdmovie.json` | open a saved project |
| `mdmovie render movie.mdmovie.json movie.mp4` | render a project without the app (`--scale`, `--fps`, `--start`, `--stop`, …) |
| `mdmovie render md.tpr md.xtc movie.mp4` | render a ready-made movie of a trajectory without the app |

## Code map

```
mdmovie/core/       sync model (timemap), layout tree, crop math, project file, quick start from a trajectory
mdmovie/sources/    image sequences + LRU cache, trajectory metadata, periodic-boundary unwrapping
mdmovie/analysis/   preset registry, built-in presets, cached runner
mdmovie/mol/        molecule renderer: structure + secondary structure, cartoon mesh, software rasteriser
mdmovie/panels/     image (+ data overlay), molecule, plot, heatmap, CV map, text
mdmovie/render/     compositor (single drawing path for preview and export), exporter
mdmovie/ui/         main window, canvas editor, inspector, timeline, dialogs
docs/               tutorial + make_tutorial_images.py (regenerates every screenshot and GIF)
```

## Development

```bash
git clone https://github.com/dmighty007/mdmovie && cd mdmovie
pip install -e ".[dev]"
python -m pytest                                                   # headless
QT_QPA_PLATFORM=offscreen python docs/make_tutorial_images.py      # refresh the docs pictures
```

### Releasing

1. Bump `__version__` in `mdmovie/__init__.py` and commit.
2. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`.

The `publish` workflow then runs the tests, builds the sdist and wheel, checks that the tag matches the version, and uploads to PyPI with [Trusted Publishing](https://docs.pypi.org/trusted-publishers/), so no API token is stored anywhere. One-time setup: on PyPI, add a *pending publisher* for project `mdmovie`, owner `dmighty007`, repository `mdmovie`, workflow `publish.yml`, environment `pypi`. In the GitHub repository settings, create an environment named `pypi`.

## License

MIT, see [LICENSE](https://github.com/dmighty007/mdmovie/blob/main/LICENSE).
