"""Themes (system / Nord dark / Nord light), fonts and the tray icon."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

# https://www.nordtheme.com/docs/colors-and-palettes
# n00 is a darker Polar Night shade used by many Nord ports for backgrounds.
NORD = {
    "n00": "#242933",
    "n0": "#2e3440", "n1": "#3b4252", "n2": "#434c5e", "n3": "#4c566a",
    "n4": "#d8dee9", "n5": "#e5e9f0", "n6": "#eceff4",
    "n8": "#88c0d0", "n9": "#81a1c1", "n10": "#5e81ac",
    "n11": "#bf616a", "n13": "#ebcb8b", "n14": "#a3be8c",
}


def _build_palette(colors: dict[QPalette.ColorRole, str]) -> QPalette:
    palette = QPalette()
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    return palette


def nord_dark_palette() -> QPalette:
    R = QPalette.ColorRole
    return _build_palette({
        R.Window: NORD["n00"], R.WindowText: NORD["n5"],
        R.Base: NORD["n0"], R.AlternateBase: NORD["n1"],
        R.Text: NORD["n5"], R.PlaceholderText: NORD["n3"],
        R.Button: NORD["n0"], R.ButtonText: NORD["n5"],
        R.Highlight: NORD["n10"], R.HighlightedText: NORD["n6"],
        R.ToolTipBase: NORD["n0"], R.ToolTipText: NORD["n5"],
        R.Link: NORD["n8"], R.BrightText: NORD["n11"],
    })


def nord_light_palette() -> QPalette:
    R = QPalette.ColorRole
    return _build_palette({
        R.Window: NORD["n6"], R.WindowText: NORD["n0"],
        R.Base: "#ffffff", R.AlternateBase: NORD["n5"],
        R.Text: NORD["n0"], R.PlaceholderText: NORD["n3"],
        R.Button: NORD["n5"], R.ButtonText: NORD["n0"],
        R.Highlight: NORD["n8"], R.HighlightedText: NORD["n0"],
        R.Link: NORD["n10"], R.BrightText: NORD["n11"],
        R.ToolTipBase: NORD["n5"], R.ToolTipText: NORD["n0"],
    })


def apply_theme(app, theme: str) -> None:
    """system — leave everything alone (Qt picks up the environment theme);
    nord-dark / nord-light — Nord palette on top of the Fusion style."""
    if theme == "nord-dark":
        app.setStyle("Fusion")
        app.setPalette(nord_dark_palette())
    elif theme == "nord-light":
        app.setStyle("Fusion")
        app.setPalette(nord_light_palette())


def apply_font(app, family: str, size: int) -> None:
    if not family and size <= 0:
        return
    font = QFont(app.font())
    if family:
        font.setFamily(family)
    if size > 0:
        font.setPointSize(size)
    app.setFont(font)


def is_dark(app) -> bool:
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


# -- shared "card" surface: popup, settings dialog, main window ---------------
#
# These three windows are meant to read as one visual system regardless of
# which theme is selected (system / nord-dark / nord-light), the same way
# the popup already looked before this module grew a settings dialog and a
# main window — so their colours are derived here once and reused by all
# three instead of each picking its own shade of the palette.

def card_colors(app) -> dict[str, str]:
    dark = is_dark(app)
    return {
        "bg": NORD["n00"] if dark else NORD["n6"],
        "field_bg": NORD["n1"] if dark else "#ffffff",
        "fg": NORD["n5"] if dark else NORD["n0"],
        "border": NORD["n3"] if dark else NORD["n4"],
        "muted": NORD["n4"] if dark else NORD["n3"],
        "code_bg": NORD["n1"] if dark else NORD["n5"],
        "highlight": NORD["n10"],
        "highlight_text": NORD["n6"],
    }


def window_stylesheet(colors: dict[str, str]) -> str:
    """Chrome shared by the settings dialog and the main window: same flat
    background and field colours as the popup card, applied regardless of
    the selected Qt style so the three windows always match."""
    return f"""
        QDialog, QMainWindow {{ background-color: {colors['bg']}; }}
        QWidget {{ color: {colors['fg']}; }}
        QLabel {{ color: {colors['muted']}; background: transparent; }}
        QTabWidget::pane {{ border: 1px solid {colors['border']}; top: -1px; }}
        QTabBar::tab {{
            background: {colors['bg']}; color: {colors['muted']};
            padding: 6px 14px; border: 1px solid transparent;
        }}
        QTabBar::tab:selected {{
            color: {colors['fg']}; border: 1px solid {colors['border']};
            border-bottom-color: {colors['bg']};
        }}
        QToolBar, QStatusBar {{
            background: {colors['bg']}; border: none; color: {colors['fg']};
        }}
        QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox,
        QListWidget, QFontComboBox {{
            background-color: {colors['field_bg']};
            color: {colors['fg']};
            border: 1px solid {colors['border']};
            border-radius: 4px;
        }}
        QListWidget::item {{ padding: 3px 4px; }}
        QListWidget::item:selected {{
            background-color: {colors['highlight']}; color: {colors['highlight_text']};
        }}
        QPushButton {{
            background-color: {colors['field_bg']}; color: {colors['fg']};
            border: 1px solid {colors['border']}; border-radius: 4px; padding: 4px 12px;
        }}
        QPushButton:hover {{ background-color: {colors['code_bg']}; }}
        QSplitter::handle {{ background-color: {colors['border']}; }}
    """


def apply_window_theme(window) -> None:
    """Applies the shared card stylesheet to a settings dialog or main
    window instance. Call again after the palette changes (theme/font)."""
    colors = card_colors(_app_instance())
    window.setStyleSheet(window_stylesheet(colors))


def _app_instance():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance()


# -- tray icon ------------------------------------------------------------
#
# Raven silhouette by SVG Repo (https://www.svgrepo.com/svg/156257/raven),
# recoloured at runtime to match the current theme.

_RAVEN_SVG = (Path(__file__).parent / "assets" / "raven.svg").read_text(encoding="utf-8")
_RAVEN_FILL = 'fill="#000000"'


def _tinted_raven_svg(color: QColor) -> bytes:
    return _RAVEN_SVG.replace(_RAVEN_FILL, f'fill="{color.name()}"', 1).encode("utf-8")


def make_tray_icon(color: QColor | str | None = None) -> QIcon:
    """Monochrome raven silhouette. The default colour is the text colour
    of the current application palette, so the icon is light on dark
    panels and dark on light panels."""
    if color is None:
        app = _app_instance()
        color = (app.palette().color(QPalette.ColorRole.WindowText)
                 if app is not None else QColor(NORD["n5"]))
    color = QColor(color)

    renderer = QSvgRenderer(_tinted_raven_svg(color))
    icon = QIcon()
    for size in (22, 24, 32, 48, 64, 128):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
