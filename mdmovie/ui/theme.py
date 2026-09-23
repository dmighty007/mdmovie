"""Application look: Fusion style + palette + stylesheet, in a dark and a light variant.

Custom-painted widgets (canvas, timeline) read their colours from `C` so they follow the
theme; call `apply_theme()` again to switch.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Colors:
    name: str
    window: str      # app background
    panel: str       # docks, cards
    raised: str      # buttons, section headers
    input: str       # text fields
    stage: str       # behind the canvas
    border: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_text: str
    accent_soft: str  # selection backgrounds
    danger: str


DARK = Colors("dark", window="#1b1e23", panel="#22262c", raised="#2c3139", input="#191c20", stage="#141619",
              border="#343a44", text="#e4e7ec", muted="#939cab", faint="#5d6573", accent="#ff7a1a",
              accent_text="#1b1204", accent_soft="#4a2f1a", danger="#ff6b5f")
LIGHT = Colors("light", window="#eceef2", panel="#f7f8fa", raised="#ffffff", input="#ffffff", stage="#d9dde3",
               border="#d2d7df", text="#1c222b", muted="#5e6878", faint="#9aa3b1", accent="#e8650a",
               accent_text="#ffffff", accent_soft="#fbe2cf", danger="#d93a2f")

C: Colors = DARK  # current theme (read by custom-painted widgets)


def _palette(c: Colors) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: c.window, QPalette.ColorRole.WindowText: c.text,
        QPalette.ColorRole.Base: c.input, QPalette.ColorRole.AlternateBase: c.panel,
        QPalette.ColorRole.Text: c.text, QPalette.ColorRole.PlaceholderText: c.faint,
        QPalette.ColorRole.Button: c.raised, QPalette.ColorRole.ButtonText: c.text,
        QPalette.ColorRole.Highlight: c.accent, QPalette.ColorRole.HighlightedText: c.accent_text,
        QPalette.ColorRole.ToolTipBase: c.raised, QPalette.ColorRole.ToolTipText: c.text,
        QPalette.ColorRole.Mid: c.border, QPalette.ColorRole.Dark: c.stage, QPalette.ColorRole.Light: c.raised,
        QPalette.ColorRole.Link: c.accent,
    }
    for role, col in roles.items():
        p.setColor(role, QColor(col))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.faint))
    return p


def _stylesheet(c: Colors, check_path: str) -> str:
    return f"""
* {{ outline: none; }}
QMainWindow, QDialog {{ background: {c.window}; }}
QToolTip {{ background: {c.raised}; color: {c.text}; border: 1px solid {c.border}; padding: 5px 8px; border-radius: 6px; }}

/* menus */
QMenuBar {{ background: {c.window}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {c.raised}; }}
QMenu {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 26px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c.accent}; color: {c.accent_text}; }}
QMenu::separator {{ height: 1px; background: {c.border}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}

/* toolbar */
QToolBar {{ background: {c.window}; border: none; border-bottom: 1px solid {c.border}; padding: 5px 8px; spacing: 3px; }}
QToolBar::separator {{ width: 1px; background: {c.border}; margin: 4px 7px; }}
QToolBar QToolButton {{ padding: 5px 8px; border-radius: 7px; border: 1px solid transparent; }}
QToolBar QToolButton:hover {{ background: {c.raised}; border-color: {c.border}; }}
QToolBar QToolButton:pressed {{ background: {c.accent_soft}; }}
QToolBar QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#primary {{ background: {c.accent}; color: {c.accent_text}; font-weight: 600; padding: 6px 14px; }}
QToolButton#primary:hover {{ background: {c.accent}; border-color: {c.accent}; }}

/* docks */
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {c.window}; padding: 9px 12px 7px 12px; text-align: left; }}
QDockWidget > QWidget {{ background: {c.panel}; }}
QMainWindow::separator {{ background: {c.border}; width: 1px; height: 1px; }}
QSplitter::handle {{ background: {c.border}; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* tree */
QTreeWidget {{ background: {c.panel}; border: none; padding: 4px; }}
QTreeWidget::item {{ padding: 5px 4px; border-radius: 6px; margin: 1px 4px; }}
QTreeWidget::item:hover {{ background: {c.raised}; }}
QTreeWidget::item:selected {{ background: {c.accent_soft}; color: {c.text}; }}
QTreeWidget {{ show-decoration-selected: 0; }}
QTreeWidget::branch, QTreeWidget::branch:selected, QTreeWidget::branch:hover {{ background: {c.panel}; border: none; }}

/* inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QListWidget {{
    background: {c.input}; border: 1px solid {c.border}; border-radius: 6px; padding: 4px 7px;
    selection-background-color: {c.accent}; selection-color: {c.accent_text};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {c.accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {c.faint}; background: {c.panel}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: 6px; padding: 4px;
    selection-background-color: {c.accent}; selection-color: {c.accent_text}; }}
QListWidget::item {{ padding: 5px 4px; border-radius: 5px; }}
QListWidget::item:selected {{ background: {c.accent_soft}; color: {c.text}; }}
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {c.border}; background: {c.input}; }}
QCheckBox::indicator:checked {{ background: {c.accent}; border-color: {c.accent}; image: url({check_path}); }}

/* group boxes (dialogs) */
QGroupBox {{ border: 1px solid {c.border}; border-radius: 8px; margin-top: 14px; padding: 12px 10px 8px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c.muted}; font-weight: 600; }}

/* buttons */
QPushButton {{ background: {c.raised}; border: 1px solid {c.border}; border-radius: 6px; padding: 5px 12px; }}
QPushButton:hover {{ border-color: {c.faint}; }}
QPushButton:pressed {{ background: {c.accent_soft}; }}
QPushButton:default, QPushButton#primary {{ background: {c.accent}; color: {c.accent_text}; border-color: {c.accent}; font-weight: 600; }}
QPushButton:disabled {{ color: {c.faint}; }}
QToolButton {{ border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {c.raised}; }}
QToolButton:checked {{ background: {c.accent_soft}; }}

/* sliders */
QSlider::groove:horizontal {{ height: 4px; background: {c.border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c.text}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {c.accent}; }}

/* scroll bars */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {c.border}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {c.faint}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; height: 0; width: 0; }}

/* progress + status */
QProgressBar {{ background: {c.input}; border: 1px solid {c.border}; border-radius: 5px; height: 10px; text-align: center; font-size: 10px; }}
QProgressBar::chunk {{ background: {c.accent}; border-radius: 4px; }}
QStatusBar {{ background: {c.window}; border-top: 1px solid {c.border}; color: {c.muted}; }}
QStatusBar::item {{ border: none; }}

/* inspector */
QWidget#inspectorBody {{ background: {c.panel}; }}
QFrame#headerCard {{ background: {c.raised}; border: 1px solid {c.border}; border-radius: 10px; }}
QLabel#headerTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#headerSub, QLabel#note {{ color: {c.muted}; }}
QFrame#section {{ background: transparent; border: none; border-top: 1px solid {c.border}; }}
QToolButton#sectionHeader {{ background: transparent; border: none; padding: 9px 2px 7px 2px; color: {c.muted};
    font-size: 11px; font-weight: 700; letter-spacing: 1px; text-align: left; }}
QToolButton#sectionHeader:hover {{ color: {c.text}; }}
QLabel#formLabel {{ color: {c.muted}; }}

/* transport */
QWidget#transport {{ background: {c.panel}; border-top: 1px solid {c.border}; }}
QToolButton#playButton {{ background: {c.accent}; border-radius: 17px; }}
QToolButton#playButton:hover {{ background: {c.accent}; }}
QLabel#timecode {{ font-family: "JetBrains Mono", "IBM Plex Mono", "DejaVu Sans Mono", monospace; font-size: 17px; font-weight: 600; }}
QLabel#frameInfo, QLabel#perf {{ color: {c.muted}; font-family: "JetBrains Mono", "IBM Plex Mono", "DejaVu Sans Mono", monospace; }}

/* welcome card */
QFrame#welcome {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: 14px; }}
QLabel#welcomeTitle {{ font-size: 20px; font-weight: 700; }}
QPushButton#welcomeButton {{ text-align: left; padding: 10px 14px; border-radius: 8px; }}
QPushButton#welcomeButton:hover {{ border-color: {c.accent}; }}
"""


def apply_theme(app: QApplication, name: str = "dark") -> Colors:
    global C
    from mdmovie.ui import icons
    C = LIGHT if name == "light" else DARK
    icons.register_resources(C)
    app.setStyle("Fusion")
    font = app.font()
    font.setPointSizeF(max(font.pointSizeF(), 9.5))
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)
    app.setPalette(_palette(C))
    app.setStyleSheet(_stylesheet(C, icons.register_resources.check_path))
    return C
