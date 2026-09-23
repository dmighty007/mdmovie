"""Text panel: titles, captions and live labels such as "t = {t_ns:.1f} ns"."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from mdmovie.panels.base import Panel, Prop, RenderContext

FIELDS_HELP = ("Fields: {t_ps} {t_ns} {t_us} (simulation time), {frame} (movie frame), "
               "{seconds} (movie time), e.g. 't = {t_ns:.1f} ns'")

ALIGN_H = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
           "right": Qt.AlignmentFlag.AlignRight}
ALIGN_V = {"top": Qt.AlignmentFlag.AlignTop, "middle": Qt.AlignmentFlag.AlignVCenter,
           "bottom": Qt.AlignmentFlag.AlignBottom}


class _Blank(float):
    """Formats as empty text whatever the format spec (used when time is unknown)."""

    def __format__(self, spec):
        return ""


def format_text(template: str, ctx: RenderContext) -> str:
    t = ctx.time
    fields = {"frame": ctx.gframe, "seconds": ctx.gframe / max(ctx.project.fps, 1e-9)}
    if t is None:
        fields.update(t_ps=_Blank(), t_ns=_Blank(), t_us=_Blank())
    else:
        fields.update(t_ps=t, t_ns=t / 1e3, t_us=t / 1e6)
    try:
        return template.format(**fields)
    except (KeyError, ValueError, IndexError, AttributeError) as e:
        return f"{template}  [{type(e).__name__}: {e}]"


class TextPanel(Panel):
    KIND = "text"
    TITLE = "Text"
    PROPS = [
        Prop("text", "text", "t = {t_ns:.2f} ns", "Text", section="Text"),
        Prop("font_family", "str", "", "Font family (blank = default)", section="Text"),
        Prop("font_size", "float", 36.0, "Font size (px at output size)", minimum=2, maximum=1000, section="Text"),
        Prop("bold", "bool", False, "Bold", section="Text"),
        Prop("italic", "bool", False, "Italic", section="Text"),
        Prop("color", "color", "#222222", "Colour", section="Text"),
        Prop("align", "choice", "center", "Horizontal align", ("left", "center", "right"), section="Text"),
        Prop("valign", "choice", "middle", "Vertical align", ("top", "middle", "bottom"), section="Text"),
    ]

    def render(self, painter: QPainter, rect: QRectF, ctx: RenderContext) -> None:
        p = self.props
        painter.save()
        f = QFont(p["font_family"]) if p["font_family"] else QFont()
        f.setPixelSize(max(1, round(p["font_size"] * ctx.scale)))
        f.setBold(p["bold"])
        f.setItalic(p["italic"])
        painter.setFont(f)
        painter.setPen(QPen(QColor(p["color"])))
        flags = ALIGN_H[p["align"]] | ALIGN_V[p["valign"]] | Qt.TextFlag.TextWordWrap
        painter.drawText(rect, int(flags), format_text(p["text"], ctx))
        painter.restore()
