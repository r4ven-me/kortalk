"""Tests for windows.py: dragging the frameless popup by the mouse."""

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from kortalk.windows import PopupWindow


def _mouse_event(event_type, pos: QPoint, global_pos: QPoint, button, buttons):
    return QMouseEvent(event_type, QPointF(pos), QPointF(global_pos), button, buttons,
                       Qt.KeyboardModifier.NoModifier)


def test_popup_card_drag_moves_the_window(qtbot, config):
    popup = PopupWindow(config, "Test Provider")
    qtbot.addWidget(popup)
    popup.move(100, 100)
    card = popup.card

    press = _mouse_event(QEvent.Type.MouseButtonPress, QPoint(10, 10), QPoint(110, 110),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    card.mousePressEvent(press)
    assert card._drag_from == QPoint(10, 10)  # offset of the click inside the window

    move = _mouse_event(QEvent.Type.MouseMove, QPoint(60, 60), QPoint(160, 160),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    card.mouseMoveEvent(move)
    assert popup.pos() == QPoint(150, 150)  # window followed the cursor, offset preserved

    release = _mouse_event(QEvent.Type.MouseButtonRelease, QPoint(60, 60), QPoint(160, 160),
                           Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    card.mouseReleaseEvent(release)
    assert card._drag_from is None


def test_popup_card_drag_ignores_other_buttons(qtbot, config):
    popup = PopupWindow(config, "Test Provider")
    qtbot.addWidget(popup)
    popup.move(100, 100)
    card = popup.card

    press = _mouse_event(QEvent.Type.MouseButtonPress, QPoint(10, 10), QPoint(110, 110),
                         Qt.MouseButton.RightButton, Qt.MouseButton.RightButton)
    card.mousePressEvent(press)
    assert card._drag_from is None


def test_open_in_window_carries_the_original_prompt(qtbot, config):
    # "Open in window" must not lose context: the left pane should get the
    # prompt+selection that was actually asked, not just the answer.
    popup = PopupWindow(config, "Test Provider")
    qtbot.addWidget(popup)
    popup._prompt = "Explain:\n\nsome selected text"
    popup.browser.finish("**answer**")

    received = []
    popup.open_in_window.connect(lambda prompt, answer: received.append((prompt, answer)))
    popup._open_in_window()

    assert received == [("Explain:\n\nsome selected text", "**answer**")]
