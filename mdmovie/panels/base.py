"""Panel base class, property schema and render context."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen


@dataclass
class Prop:
    name: str
    kind: str             # str | text | int | float | optfloat | bool | choice | color | optcolor | dir
    default: Any
    label: str = ""
    options: tuple = ()
    minimum: float = 0
    maximum: float = 100000
    step: float = 1
    section: str = ""
    hidden: bool = False

    @property
    def text(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()


def with_defaults(props: list[Prop], **defaults) -> list[Prop]:
    """Copy of a shared property list with some defaults changed for one panel type."""
    import dataclasses
    return [dataclasses.replace(p, default=defaults[p.name]) if p.name in defaults else p for p in props]


COMMON_PROPS = [
    Prop("background", "optcolor", "", "Background", section="Frame"),
    Prop("padding", "int", 0, "Padding (px)", maximum=500, section="Frame"),
    Prop("border_width", "int", 0, "Border width (px)", maximum=50, section="Frame"),
    Prop("border_color", "color", "#333333", "Border colour", section="Frame"),
]


@dataclass
class RenderContext:
    project: Any
    gframe: int            # movie frame
    time: float | None     # simulation time (ps) of the panel's sync group; None = hidden
    scale: float = 1.0     # output pixels per project pixel (preview renders at < 1)


@dataclass
class Series:
    """One analysis result drawn by a plot or heatmap panel."""
    traj: str
    preset: str
    params: dict = field(default_factory=dict)
    start: int | None = None
    stop: int | None = None
    step: int | None = None
    label: str = ""
    color: str = ""
    axis: str = "left"     # left | right
    column: int | None = None  # None: all columns of a multi-column result
    # per-series look (plot panels)
    linestyle: str = "solid"   # solid | dashed | dotted | dashdot | none
    marker: str = "none"       # none | circle | square | triangle | diamond | cross | plus | point
    linewidth: float = 0.0     # 0 = the panel's line width
    alpha: float = 1.0
    fill: bool = False         # shade the area under the curve

    def to_dict(self) -> dict:
        return copy.deepcopy(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "Series":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class Panel:
    KIND = "base"
    TITLE = "Panel"
    PROPS: list[Prop] = []
    HAS_SERIES = False

    def __init__(self, id: str, name: str = "", group: str = "", props: dict | None = None):
        self.id = id
        self.name = name or self.TITLE
        self.group = group
        self.props = {p.name: copy.deepcopy(p.default) for p in self.all_props()}
        if props:
            self.props.update({k: v for k, v in self.migrate(dict(props)).items() if k in self.props})
        self.series: list[Series] = []

    @classmethod
    def migrate(cls, props: dict) -> dict:
        """Upgrade property values saved by older versions."""
        return props

    @classmethod
    def all_props(cls) -> list[Prop]:
        return cls.PROPS + COMMON_PROPS

    def __getitem__(self, key):
        return self.props[key]

    # --- time ---------------------------------------------------------------------------
    def time_info(self, project) -> tuple[float, float, float] | None:
        """(first time, last time, dt) in ps of this panel's data, or None if it has no time axis."""
        return None

    # --- drawing ------------------------------------------------------------------------
    def render(self, painter: QPainter, rect: QRectF, ctx: RenderContext) -> None:
        raise NotImplementedError

    def prefetch(self, project, times: list[float]) -> None:
        """Hint that these times will be drawn soon (image panels load them in the background)."""

    # --- serialisation ------------------------------------------------------------------
    def to_dict(self, rel) -> dict:
        d = {"id": self.id, "kind": self.KIND, "name": self.name, "group": self.group,
             "props": copy.deepcopy(self.props)}
        if self.HAS_SERIES:
            d["series"] = [s.to_dict() for s in self.series]
        return d

    def load_extra(self, d: dict, resolve) -> None:
        if self.HAS_SERIES:
            self.series = [Series.from_dict(s) for s in d.get("series", [])]


def draw_message(painter: QPainter, rect: QRectF, text: str, scale: float, color="#888888") -> None:
    painter.save()
    painter.setPen(QPen(QColor(color)))
    f = QFont()
    f.setPixelSize(max(8, int(18 * scale)))
    painter.setFont(f)
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), text)
    painter.restore()
