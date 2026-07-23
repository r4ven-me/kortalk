"""Themes (system / Nord dark / Nord light), fonts and the tray icon."""

from __future__ import annotations

import os
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
        # Deliberately distinct from code_bg (used for button hover/chrome):
        # a code block should read as recessed — darker than the page in
        # dark mode, not the lighter shade that works for a hovered button.
        "code_block_bg": QColor(NORD["n00"]).darker(130).name() if dark else NORD["n4"],
        "highlight": NORD["n10"],
        "highlight_text": NORD["n6"],
    }


def markdown_content_stylesheet(colors: dict[str, str]) -> str:
    """Markdown rendering shared by every response view (popup, quick mode,
    dialog mode): a recessed, monospace background for `<pre>`/`<code>`,
    and breathing room between paragraphs/headings/lists/code blocks so a
    multi-turn dialog doesn't read as one solid, unbroken wall of text."""
    c = colors
    return f"""
        pre, code {{
            background-color: {c['code_block_bg']};
            font-family: 'JetBrains Mono', 'Fira Code', Consolas, Menlo, monospace;
        }}
        pre {{ padding: 8px 10px; margin: 8px 0; border-radius: 6px; }}
        p {{ margin: 6px 0; }}
        h1, h2, h3, h4, h5, h6 {{ margin: 14px 0 8px 0; }}
        ul, ol {{ margin: 6px 0; }}
        li {{ margin: 2px 0; }}
        hr {{ margin: 16px 0; }}
    """


def scrollbar_stylesheet(colors: dict[str, str]) -> str:
    """Slim, flat scrollbars (no arrow buttons, rounded handle) to replace
    the OS/Fusion default — thick troughs with visible step buttons read as
    dated next to the rest of the app's styling."""
    c = colors
    return f"""
        QScrollBar:vertical {{
            background: transparent; width: 11px; margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {c['border']}; border-radius: 4px; min-height: 24px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {c['highlight']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px; background: none; border: none;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

        QScrollBar:horizontal {{
            background: transparent; height: 11px; margin: 2px;
        }}
        QScrollBar::handle:horizontal {{
            background: {c['border']}; border-radius: 4px; min-width: 24px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {c['highlight']}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px; background: none; border: none;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
    """


def window_stylesheet(colors: dict[str, str]) -> str:
    """Chrome shared by the settings dialog and the main window: same flat
    background and field colours as the popup card, applied regardless of
    the selected Qt style so the three windows always match.

    Every interactive control gets an explicit hover/pressed/checked/
    disabled state — Fusion's defaults are too subtle to read as "this
    reacted to you", which is the point of styling them here at all."""
    c = colors
    return f"""
        QDialog, QMainWindow {{ background-color: {c['bg']}; }}
        QWidget {{ color: {c['fg']}; }}
        QLabel {{ color: {c['muted']}; background: transparent; }}
        QTabWidget::pane {{ border: 1px solid {c['border']}; top: -1px; }}
        QTabBar {{ qproperty-drawBase: 0; }}
        QTabBar::tab {{
            background: {c['bg']}; color: {c['muted']};
            padding: 6px 16px; margin-right: 3px;
            border: 1px solid {c['border']};
            border-top-left-radius: 6px; border-top-right-radius: 6px;
        }}
        QTabBar::tab:!selected {{
            margin-top: 3px; border-color: transparent;
        }}
        QTabBar::tab:selected {{
            color: {c['fg']}; background: {c['field_bg']};
            border-bottom-color: {c['field_bg']};
        }}
        QTabBar::tab:hover {{ color: {c['fg']}; }}

        QToolBar, QStatusBar {{
            background: {c['bg']}; border: none; color: {c['fg']}; spacing: 6px;
        }}
        QToolBar {{ padding: 4px 6px; }}

        QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox,
        QListWidget, QFontComboBox {{
            background-color: {c['field_bg']};
            color: {c['fg']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            selection-background-color: {c['highlight']};
            selection-color: {c['highlight_text']};
        }}
        QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover {{
            border-color: {c['muted']};
        }}
        QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border: 1px solid {c['highlight']};
        }}
        QComboBox::drop-down {{ border: none; width: 22px; }}
        QComboBox QAbstractItemView {{
            background-color: {c['field_bg']}; color: {c['fg']};
            border: 1px solid {c['border']};
            selection-background-color: {c['highlight']};
            selection-color: {c['highlight_text']};
            outline: none;
        }}

        QListWidget::item {{ padding: 4px 6px; border-radius: 4px; }}
        QListWidget::item:hover {{ background-color: {c['code_bg']}; }}
        QListWidget::item:selected {{
            background-color: {c['highlight']}; color: {c['highlight_text']};
        }}

        QPushButton, QToolButton {{
            background-color: {c['field_bg']}; color: {c['fg']};
            border: 1px solid {c['border']}; border-radius: 6px; padding: 5px 14px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background-color: {c['code_bg']}; border-color: {c['highlight']};
        }}
        QPushButton:pressed, QToolButton:pressed {{
            background-color: {c['highlight']}; color: {c['highlight_text']};
            border-color: {c['highlight']};
        }}
        QPushButton:disabled, QToolButton:disabled {{
            color: {c['muted']}; border-color: {c['border']}; background-color: {c['bg']};
        }}
        QPushButton:checkable:checked, QToolButton:checkable:checked {{
            background-color: {c['highlight']}; color: {c['highlight_text']};
            border-color: {c['highlight']};
        }}
        QToolBar QToolButton {{ padding: 5px 12px; margin: 0 2px; }}
        QToolButton#chatToggle:checked {{
            background-color: {c['highlight']}; color: {c['highlight_text']};
            border-color: {c['highlight']}; font-weight: 600;
        }}

        QPushButton#primaryButton {{
            background-color: {c['highlight']}; color: {c['highlight_text']};
            border-color: {c['highlight']}; font-weight: 600;
        }}
        QPushButton#primaryButton:hover {{ border-color: {c['fg']}; }}
        QPushButton#primaryButton:pressed {{ background-color: {c['muted']}; }}
        QPushButton#primaryButton:disabled {{
            background-color: {c['field_bg']}; color: {c['muted']}; border-color: {c['border']};
        }}

        QPushButton#iconButton {{ padding: 5px 4px; }}

        QSplitter::handle {{ background-color: transparent; }}
        QSplitter::handle:horizontal {{
            width: 11px; margin: 8px 3px;
            background-color: {c['border']}; border-radius: 3px;
        }}
        QSplitter::handle:vertical {{
            height: 11px; margin: 3px 8px;
            background-color: {c['border']}; border-radius: 3px;
        }}
        QSplitter::handle:horizontal:hover, QSplitter::handle:vertical:hover {{
            background-color: {c['highlight']};
        }}

        {scrollbar_stylesheet(c)}
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


# Static black icon file for the applications-menu launcher and the
# autostart entry (as opposed to make_tray_icon's runtime-recoloured
# pixmaps) — pip/pipx installs it nowhere, so kortalk writes it itself.
ICON_FILE = (Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
             / "icons" / "kortalk.svg")


def install_icon_file() -> Path:
    """Writes the icon to ICON_FILE and returns its path. Always overwrites
    so an icon update ships to existing installs — safe to call on every
    startup/save, pip/pipx never places this file on its own."""
    try:
        ICON_FILE.parent.mkdir(parents=True, exist_ok=True)
        ICON_FILE.write_text(_RAVEN_SVG, encoding="utf-8")
    except OSError:
        pass
    return ICON_FILE


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
