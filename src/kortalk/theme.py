"""Темы (system / Nord dark / Nord light), шрифты и иконка трея."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)

# https://www.nordtheme.com/docs/colors-and-palettes
NORD = {
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
        R.Window: NORD["n0"], R.WindowText: NORD["n6"],
        R.Base: NORD["n1"], R.AlternateBase: NORD["n2"],
        R.Text: NORD["n6"], R.PlaceholderText: NORD["n3"],
        R.Button: NORD["n1"], R.ButtonText: NORD["n6"],
        R.Highlight: NORD["n10"], R.HighlightedText: NORD["n6"],
        R.ToolTipBase: NORD["n1"], R.ToolTipText: NORD["n6"],
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


def apply_theme(app, theme: str) -> None:
    """system — ничего не трогаем (Qt подхватывает тему окружения сам);
    nord-dark / nord-light — палитра Nord поверх стиля Fusion."""
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


def _raven_path() -> QPainterPath:
    """Силуэт ворона (Corvus — символ korvus; вид сбоку) в координатах 0..64."""
    path = QPainterPath()
    # тело
    path.addEllipse(QRectF(10, 22, 36, 26))
    # голова
    path.addEllipse(QRectF(33, 6, 21, 21))
    # клюв — острый клин
    beak = QPolygonF([QPointF(51, 12.5), QPointF(64, 17.5), QPointF(51, 21.5)])
    path.addPolygon(beak)
    path.closeSubpath()
    # хвост — клин назад-вниз
    tail = QPolygonF([
        QPointF(20, 28), QPointF(1, 50), QPointF(9, 55), QPointF(26, 44),
    ])
    path.addPolygon(tail)
    path.closeSubpath()
    return path.simplified()


def make_tray_icon(color: QColor | str | None = None) -> QIcon:
    """Монохромный силуэт ворона. Цвет по умолчанию — цвет текста текущей
    палитры приложения (подстраивается под светлую/тёмную панель)."""
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
        # лапки
        pen = QPen(color, 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(24, 47), QPointF(23, 58))
        painter.drawLine(QPointF(33, 47), QPointF(34, 58))
        # глаз — «вырезаем» из силуэта
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.drawEllipse(QRectF(42, 12, 5, 5))
        painter.end()
        icon.addPixmap(pixmap)
    return icon
