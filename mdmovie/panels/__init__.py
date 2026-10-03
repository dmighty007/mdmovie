from mdmovie.panels.base import Panel, Prop, RenderContext, Series
from mdmovie.panels.cvmap_panel import CVMapPanel
from mdmovie.panels.heatmap_panel import HeatmapPanel
from mdmovie.panels.image_panel import ImagePanel
from mdmovie.panels.molecule_panel import MoleculePanel
from mdmovie.panels.plot_panel import PlotPanel
from mdmovie.panels.text_panel import TextPanel

PANEL_TYPES: dict[str, type[Panel]] = {c.KIND: c for c in (ImagePanel, MoleculePanel, PlotPanel, HeatmapPanel,
                                                            CVMapPanel, TextPanel)}

__all__ = ["PANEL_TYPES", "Panel", "Prop", "RenderContext", "Series",
           "ImagePanel", "MoleculePanel", "PlotPanel", "HeatmapPanel", "CVMapPanel", "TextPanel"]
