"""A small set of line icons (24×24, 1.8 px strokes) drawn as SVG and tinted to the theme."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Only the shape: stroke colour, width and caps are added by _svg().
PATHS = {
    "new": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
           '<path d="M12 11v6M9 14h6"/>',
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "save": '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z"/><path d="M8 3v5h7V3"/>'
            '<rect x="8" y="13" width="8" height="6" rx="1"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/>'
             '<path d="m21 16-5-5-9 9"/>',
    "plot": '<path d="M4 4v16h16"/><path d="m7 15 4-5 3 3 5-7"/>',
    "heatmap": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    "text": '<path d="M5 6V4h14v2"/><path d="M12 4v16"/><path d="M9 20h6"/>',
    "trajectory": '<circle cx="12" cy="12" r="2"/><ellipse cx="12" cy="12" rx="9" ry="3.6"/>'
                  '<ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(60 12 12)"/>'
                  '<ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(120 12 12)"/>',
    "group": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "canvas": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18"/>',
    "layout": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 3v18M12 12h9"/>',
    "export": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 19h16"/>',
    "play": '<path d="M7 4.5v15a.8.8 0 0 0 1.2.7l12-7.5a.8.8 0 0 0 0-1.4l-12-7.5A.8.8 0 0 0 7 4.5z" fill="currentColor"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/>'
             '<rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/>',
    "first": '<path d="M6 5v14"/><path d="M18 5 9 12l9 7z" fill="currentColor"/>',
    "last": '<path d="M18 5v14"/><path d="m6 5 9 7-9 7z" fill="currentColor"/>',
    "prev": '<path d="m15 6-6 6 6 6"/>',
    "next": '<path d="m9 6 6 6-6 6"/>',
    "loop": '<path d="m17 2 3 3-3 3"/><path d="M4 11V9a4 4 0 0 1 4-4h12"/><path d="m7 22-3-3 3-3"/>'
            '<path d="M20 13v2a4 4 0 0 1-4 4H4"/>',
    "crop": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/>'
             '<path d="M9 7V4h6v3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "sparkle": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 17v4M17 19h4"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-right": '<path d="m9 6 6 6-6 6"/>',
    "check": '<path d="m5 12 5 5 9-10"/>',
    "overlay": '<rect x="3" y="3" width="14" height="14" rx="2"/><rect x="9" y="9" width="12" height="12" rx="2"/>',
    "film": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
    "cvmap": '<path d="M4 4v16h16"/><circle cx="9" cy="13" r="1.3"/><circle cx="13" cy="9" r="1.3"/>'
             '<circle cx="16" cy="14" r="1.3"/><path d="M7 17c3-1 4-6 9-9" stroke-dasharray="2 2.2"/>',
    "molecule": '<circle cx="6" cy="17" r="2.6"/><circle cx="12" cy="7" r="3.2"/><circle cx="18.5" cy="16" r="2.2"/>'
                '<path d="m7.4 14.8 2.9-5M14.5 9.3l2.9 4.8"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "unlock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
           'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
}

KIND_ICONS = {"image": "image", "molecule": "molecule", "plot": "plot", "heatmap": "heatmap", "cvmap": "cvmap", "text": "text"}

_colors = {"normal": "#e4e7ec", "muted": "#939cab", "disabled": "#5d6573", "accent": "#ff7a1a",
           "on_accent": "#1b1204"}


def _svg(name: str, color: str, width: float = 1.8) -> bytes:
    body = PATHS[name].replace("currentColor", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int = 18, dpr: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color)))
    img = QImage(round(size * dpr), round(size * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, img.width(), img.height()))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, role: str = "normal", size: int = 18) -> QIcon:
    """Theme-tinted icon; `role` is normal | muted | accent | on_accent."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, _colors[role], size), QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, _colors["disabled"], size), QIcon.Mode.Disabled)
    ic.addPixmap(pixmap(name, _colors[role], size), QIcon.Mode.Active)
    ic.addPixmap(pixmap(name, _colors["normal"] if role != "on_accent" else _colors[role], size),
                 QIcon.Mode.Selected)
    return ic


ICON_SIZE = QSize(18, 18)


def register_resources(c) -> None:
    """Remember the theme colours and write the checkbox tick used by the stylesheet."""
    import os
    import tempfile
    _colors.update(normal=c.text, muted=c.muted, disabled=c.faint, accent=c.accent, on_accent=c.accent_text)
    pixmap.cache_clear()
    # Qt stylesheets can only load images from files/resources: write the tick to a temp file
    path = os.path.join(tempfile.gettempdir(), f"mdmovie-check-{c.name}.svg")
    with open(path, "wb") as f:
        f.write(_svg("check", c.accent_text, 3.0))
    register_resources.check_path = path.replace("\\", "/")
