"""Themes (system / Nord dark / Nord light), fonts and the tray icon."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
)

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
        R.ToolTipBase: NORD["n5"], R.ToolTipText: NORD["n0"],
        R.Link: NORD["n10"], R.BrightText: NORD["n11"],
    })


def _window_border_css(border: str) -> str:
    """A 1px border separating our windows from same-coloured backgrounds."""
    return (
        f"QMainWindow {{ border: 1px solid {border}; }}\n"
        f"QDialog {{ border: 1px solid {border}; }}"
    )


def apply_theme(app, theme: str) -> None:
    """system — leave everything alone (Qt picks up the environment theme);
    nord-dark / nord-light — Nord palette on top of the Fusion style."""
    if theme == "nord-dark":
        app.setStyle("Fusion")
        app.setPalette(nord_dark_palette())
        app.setStyleSheet(_window_border_css(NORD["n2"]))
    elif theme == "nord-light":
        app.setStyle("Fusion")
        app.setPalette(nord_light_palette())
        app.setStyleSheet(_window_border_css(NORD["n4"]))


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


def _raven_path() -> QPainterPath:
    """Raven head in profile with a heavy bill (Corvus — the korvus emblem).
    Coordinates 0..64, facing right."""
    path = QPainterPath()
    path.moveTo(13, 64)                              # neck at the bottom edge
    path.cubicTo(6, 46, 7, 18, 25, 7.5)              # nape -> crown
    path.cubicTo(30, 5.5, 36, 6, 41, 10)             # crown -> forehead
    path.lineTo(42.5, 10.6)                          # feathered step at the beak base
    path.cubicTo(50, 12, 58, 17, 63.2, 24.2)         # culmen arcs down to the tip
    path.cubicTo(63.6, 24.8, 63.2, 25.6, 62.2, 25.8)  # pointed tip, slight hook
    path.cubicTo(54, 27.6, 45.5, 26.8, 38.5, 25)     # lower mandible -> gape
    path.lineTo(39.5, 29.5)                          # throat hackles
    path.lineTo(35, 31)
    path.lineTo(38, 35)
    path.lineTo(33.5, 36.6)
    path.cubicTo(32, 44, 31, 54, 31, 64)             # chest
    path.closeSubpath()
    return path


def make_tray_icon(color: QColor | str | None = None) -> QIcon:
    """Monochrome raven head. The default colour is the text colour of the
    current application palette (adapts to light/dark panels)."""
    if color is None:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        color = (app.palette().color(QPalette.ColorRole.WindowText)
                 if app is not None else QColor("#e5e9f0"))
    color = QColor(color)

    raven = _raven_path()
    icon = QIcon()
    for size in (22, 24, 32, 48, 64, 128):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 64.0, size / 64.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(raven)
        # eye and nostril are "cut out" of the silhouette
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.drawEllipse(QRectF(32.5, 13.5, 5, 5))
        painter.drawEllipse(QRectF(45.5, 16, 5.5, 2))
        painter.end()
        icon.addPixmap(pixmap)
    return icon
