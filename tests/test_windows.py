"""Tests for windows.py: dragging the frameless popup by the mouse."""

import http.server
import threading
import time
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent, QWheelEvent

import kortalk.windows as windows_mod
from kortalk import session, theme
from kortalk.windows import (
    MainWindow,
    PopupWindow,
    _ChatInput,
    _ImagePreviewDialog,
    _ProseBrowser,
    _StreamingBrowser,
)


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


def test_popup_opens_at_the_width_configured_in_settings(qtbot, config):
    config.set("popup_width", 640)
    popup = PopupWindow(config, "Test Provider")
    qtbot.addWidget(popup)
    assert popup.width() == 640


def test_popup_height_adjustment_is_debounced(qtbot, config, monkeypatch):
    # A single content change fires contentChanged several times in a row
    # (see the comment above _height_debounce in PopupWindow.__init__) —
    # each must not resize the actual OS window on its own, or the popup
    # visibly snaps back and forth; only one _adjust_height call should
    # eventually go through for a whole burst.
    mock_adjust = MagicMock()
    monkeypatch.setattr(PopupWindow, "_adjust_height", mock_adjust)
    popup = PopupWindow(config, "Test Provider")
    qtbot.addWidget(popup)

    for _ in range(5):
        popup.browser.contentChanged.emit()
    assert mock_adjust.call_count == 0  # still inside the debounce window

    qtbot.waitUntil(lambda: mock_adjust.call_count == 1, timeout=2000)
    qtbot.wait(100)
    assert mock_adjust.call_count == 1  # the burst collapsed into a single call


def test_adjust_height_is_a_noop_after_a_manual_resize(qtbot, config):
    popup = PopupWindow(config, "Test Provider")
    qtbot.addWidget(popup)
    popup.browser.finish("some text\n\n" + "more text " * 40)  # non-trivial natural height

    popup._user_resized = True
    popup.resize(500, 321)
    popup._adjust_height()

    assert popup.height() == 321  # the user's own size sticks, not overwritten


def test_card_edge_press_starts_a_system_resize_and_pins_the_size(qtbot, config, monkeypatch):
    popup = PopupWindow(config, "Test Provider")
    qtbot.addWidget(popup)
    popup.resize(400, 200)
    card = popup.card
    card.resize(400, 200)

    fake_handle = MagicMock()
    fake_handle.startSystemResize.return_value = True
    monkeypatch.setattr(PopupWindow, "windowHandle", lambda self: fake_handle)

    press = _mouse_event(QEvent.Type.MouseButtonPress, QPoint(2, 100), QPoint(2, 100),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    card.mousePressEvent(press)

    fake_handle.startSystemResize.assert_called_once_with(windows_mod.Qt.Edge.LeftEdge)
    assert popup._user_resized is True
    assert card._drag_from is None  # the WM owns the drag now, not our own move-logic


def test_card_press_away_from_any_edge_still_just_drags(qtbot, config):
    # regression: the new edge-resize hit-testing must not swallow ordinary
    # drag-to-move clicks in the middle of the card
    popup = PopupWindow(config, "Test Provider")
    qtbot.addWidget(popup)
    popup.move(100, 100)
    popup.resize(400, 200)
    card = popup.card
    card.resize(400, 200)

    press = _mouse_event(QEvent.Type.MouseButtonPress, QPoint(200, 100), QPoint(300, 200),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    card.mousePressEvent(press)

    assert card._drag_from == QPoint(200, 100)
    assert popup._user_resized is False


def test_stop_button_has_a_red_style_while_enabled_and_dims_when_disabled(qtbot, config):
    popup = PopupWindow(config, "Test Provider")
    qtbot.addWidget(popup)
    assert popup.stop_btn.objectName() == "stopButton"
    stylesheet = popup.card.styleSheet()
    assert "#stopButton" in stylesheet
    assert theme.NORD["n11"] in stylesheet  # red accent for the active (enabled) state
    assert "#stopButton:disabled" in stylesheet  # dims back to muted once finished


def test_stop_button_enabled_state_tracks_generation(qtbot, config, monkeypatch):
    _patch_worker(monkeypatch)
    popup = PopupWindow(config, "Test Provider")
    qtbot.addWidget(popup)
    assert not popup.stop_btn.isEnabled()

    popup.ask(config.active_provider(), "hello")
    assert popup.stop_btn.isEnabled()

    popup._on_finished("answer")
    assert not popup.stop_btn.isEnabled()


def _wheel_event(dx: int, dy: int) -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(dx, dy),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )


def test_prose_browser_ignores_vertical_wheel_so_it_reaches_the_conversation_scroll(qtbot):
    # Regression: a QTextBrowser is a QAbstractScrollArea and accepts a
    # vertical wheel event by default even with both scrollbars off and
    # nothing of its own to scroll — which silently swallowed the
    # conversation's own scrolling wherever the mouse happened to be over
    # a paragraph (as opposed to a code block, already fixed the same way)
    # rather than the conversation's own outer QScrollArea. Qt only
    # auto-forwards an *ignored* wheel event to the parent widget for real
    # (spontaneous) hardware events, so this asserts the unit of logic —
    # ignore()/False for a vertical-dominant wheel — rather than the actual
    # cross-widget propagation, which isn't reproducible via a synthetic
    # sendEvent in a headless test.
    browser = _ProseBrowser()
    qtbot.addWidget(browser)

    vertical = _wheel_event(0, -120)
    assert browser.viewportEvent(vertical) is False
    assert not vertical.isAccepted()


def test_prose_browser_delegates_horizontal_wheel_to_the_base_class(qtbot, monkeypatch):
    # Only a vertical-dominant wheel is force-ignored (see the test above);
    # a horizontal-dominant one — Shift+wheel, a trackpad swipe — must fall
    # through to the normal QTextBrowser handling unmodified.
    from PySide6.QtWidgets import QTextBrowser

    calls = []

    def _fake_viewport_event(self, event):
        calls.append(event)
        return True

    monkeypatch.setattr(QTextBrowser, "viewportEvent", _fake_viewport_event)

    browser = _ProseBrowser()
    qtbot.addWidget(browser)

    horizontal = _wheel_event(-120, 0)
    assert browser.viewportEvent(horizontal) is True
    assert calls[-1] is horizontal  # delegated to the base class, not intercepted


def test_rendered_answer_does_not_fetch_remote_images(qtbot, config):
    # An AI response is untrusted content rendered as Markdown -> HTML in a
    # QTextBrowser. A remote `![]()`/`<img>` with a query string is a known
    # exfiltration channel in other LLM chat UIs (a "tracking pixel" that
    # leaks conversation content via the request URL) — this only stays
    # inert because nothing in the app wires a QNetworkAccessManager/
    # loadResource() into these browsers. This test pins that property down
    # so it can't regress silently (e.g. someone "fixing" broken remote
    # images later without realizing the security implication).
    hits = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        browser = _StreamingBrowser()
        qtbot.addWidget(browser)
        browser.set_colors(theme.card_colors(QGuiApplication.instance()))
        browser.finish(f"![tracker](http://127.0.0.1:{port}/tracker.png?leak=secret)")

        app = QGuiApplication.instance()
        for _ in range(20):
            app.processEvents()
            time.sleep(0.05)

        assert hits == []
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_defer_scroll_to_bottom_only_commits_first_and_last_pass(qtbot):
    # Regression: content height keeps settling for a few event-loop turns
    # after a rebuild (each block's real wrapped height only becomes known
    # once Qt actually lays it out), so scrollbar.maximum() can pass
    # through transient, not-yet-final values on the way to its real one.
    # An earlier version of _defer_scroll_to_bottom committed (setValue,
    # a real repaint) on *every* pass, including those transient values —
    # the view visibly jumped to a wrong position and then jerked back a
    # frame later. It must now only ever move the scrollbar on the first
    # pass (immediate feedback) and the last one (final correction), never
    # on the passes in between, however many intermediate values
    # maximum() passes through along the way.
    browser = _StreamingBrowser()
    qtbot.addWidget(browser)
    calls = []
    browser._scroll_to_bottom = lambda: calls.append(True)

    browser._defer_scroll_to_bottom(passes=4)
    # Polls rather than a fixed wait(): a busy event queue (e.g. the full
    # suite, with other tests' widgets/timers still winding down) can make
    # the four chained zero-delay passes take longer wall-clock time than
    # a fixed short wait reliably covers.
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=2000)

    assert len(calls) == 2


# -- _StreamingBrowser: live markdown preview while streaming ---------------------

def _started_browser(qtbot):
    browser = _StreamingBrowser()
    qtbot.addWidget(browser)
    browser.set_colors(theme.card_colors(QGuiApplication.instance()))
    browser.begin_stream("Thinking")
    return browser


def test_live_preview_renders_formatted_blocks_before_finish(qtbot):
    # The whole point: formatting (bold, code highlighting, ...) should
    # show up *while* the answer is still streaming in, not only once
    # finish() runs.
    browser = _started_browser(qtbot)

    browser.append_chunk("**bold text** and more")
    assert browser._block_widgets == []  # nothing rendered yet -- no tick has run

    browser._render_live_preview()

    assert len(browser._block_widgets) == 1
    # absorbed into the committed block — only the still-blinking cursor
    # glyph (streaming is still ongoing) is left in the live widget
    assert browser._live.toPlainText() == windows_mod._CURSOR_GLYPH


def test_live_preview_skips_an_unchanged_buffer(qtbot):
    browser = _started_browser(qtbot)
    browser.append_chunk("hello")
    browser._render_live_preview()
    first_block = browser._block_widgets[0]

    browser._render_live_preview()  # no new chunk arrived since the last tick

    assert browser._block_widgets[0] is first_block  # untouched, not torn down again


def test_live_preview_does_nothing_during_the_thinking_placeholder(qtbot):
    browser = _started_browser(qtbot)  # no append_chunk yet

    browser._render_live_preview()

    assert browser._block_widgets == []


def test_live_preview_does_not_bake_the_cursor_glyph_into_rendered_text(qtbot):
    browser = _started_browser(qtbot)
    browser.append_chunk("hello")  # leaves the blinking cursor glyph shown

    browser._render_live_preview()

    assert windows_mod._CURSOR_GLYPH not in browser._committed_markdown


def test_streaming_continues_normally_after_a_live_preview_tick(qtbot):
    browser = _started_browser(qtbot)
    browser.append_chunk("first part")
    browser._render_live_preview()

    browser.append_chunk(" second part")

    assert browser.text_content() == "first part second part"
    assert " second part" in browser._live.toPlainText()


def _started_visible_browser(qtbot):
    # A real, shown widget — unlike _started_browser(), which several other
    # tests here deliberately leave unshown since they only check block
    # *counts*. Actual scrolling needs a real layout pass: an offscreen
    # widget that's never shown reports scrollbar.maximum() == 0 forever,
    # regardless of how much content it holds.
    browser = _StreamingBrowser()
    qtbot.addWidget(browser)
    browser.set_colors(theme.card_colors(QGuiApplication.instance()))
    browser.resize(700, 300)
    browser.show()
    qtbot.waitExposed(browser)
    browser.begin_stream("Thinking")
    return browser


def test_live_preview_keeps_following_the_bottom_when_stuck(qtbot):
    browser = _started_visible_browser(qtbot)
    scrollbar = browser._scroll.verticalScrollBar()

    browser.append_chunk("first line\n\n" + "some text " * 80)
    browser._render_live_preview()

    qtbot.waitUntil(lambda: scrollbar.maximum() > 0, timeout=1000)
    qtbot.waitUntil(lambda: scrollbar.value() == scrollbar.maximum(), timeout=1000)


def test_live_preview_preserves_position_when_scrolled_away_from_bottom(qtbot):
    browser = _started_visible_browser(qtbot)
    scrollbar = browser._scroll.verticalScrollBar()
    browser.append_chunk("first line\n\n" + "some text " * 80)
    browser._render_live_preview()
    qtbot.waitUntil(lambda: scrollbar.maximum() > 0, timeout=1000)
    scrollbar.setValue(0)  # scrolled away from the bottom, to the top

    browser.append_chunk("\n\nmore text " * 80)
    browser._render_live_preview()
    qtbot.wait(150)  # let any deferred correction run its course

    assert scrollbar.value() == 0  # stayed put, not yanked back to the bottom


def test_finish_after_live_previews_still_renders_the_full_final_text(qtbot):
    browser = _started_browser(qtbot)
    browser.append_chunk("part one\n\npart two")
    browser._render_live_preview()
    browser.append_chunk(" and the rest")

    browser.finish("part one\n\npart two and the rest")

    assert browser._committed_markdown == "part one\n\npart two and the rest"
    assert browser._live.toPlainText() == ""


# -- MainWindow: "Quick questions" session --------------------------------------

def _patch_worker(monkeypatch):
    """Replaces AIWorker with a stand-in that records constructor args and
    never actually starts a thread/hits the network or CLI."""
    fake_worker = MagicMock()
    fake_worker_cls = MagicMock(return_value=fake_worker)
    monkeypatch.setattr(windows_mod, "AIWorker", fake_worker_cls)
    return fake_worker_cls


def test_quick_session_is_pinned_first_and_selectable(qtbot, config):
    win = MainWindow(config)
    qtbot.addWidget(win)

    first_item = win.session_list.item(0)
    assert first_item.data(Qt.ItemDataRole.UserRole) == windows_mod._QUICK_SESSION_ID

    win.session_list.setCurrentRow(0)
    assert win.session_id == windows_mod._QUICK_SESSION_ID
    assert win.chat_history is win.quick_history


def test_quick_session_message_is_never_persisted(qtbot, config, monkeypatch):
    _patch_worker(monkeypatch)
    win = MainWindow(config)
    qtbot.addWidget(win)
    win.session_list.setCurrentRow(0)  # "Quick questions"

    win.chat_input.setPlainText("what does grep -i do")
    win.send_chat()
    win._on_chat_finished("case-insensitive search")

    assert session.list_sessions() == []  # nothing written to session.sqlite3
    assert win.session_id == windows_mod._QUICK_SESSION_ID  # never assigned a real id
    assert len(win.quick_history) == 2


def test_quick_session_second_message_omits_earlier_turns(qtbot, config, monkeypatch):
    fake_worker_cls = _patch_worker(monkeypatch)
    win = MainWindow(config)
    qtbot.addWidget(win)
    win.session_list.setCurrentRow(0)

    win.chat_input.setPlainText("first question")
    win.send_chat()
    win._on_chat_finished("first answer")

    win.chat_input.setPlainText("second question")
    win.send_chat()

    messages_sent = fake_worker_cls.call_args[0][1]
    assert messages_sent == [{"role": "user", "content": "second question"}]
    # the visible transcript still keeps every earlier turn
    assert len(win.chat_history) == 3


def test_regular_session_still_sends_full_history(qtbot, config, monkeypatch):
    fake_worker_cls = _patch_worker(monkeypatch)
    win = MainWindow(config)
    qtbot.addWidget(win)
    win._new_dialog()  # a real (eventually persisted) session, not "Quick questions"

    win.chat_input.setPlainText("first question")
    win.send_chat()
    win._on_chat_finished("first answer")

    win.chat_input.setPlainText("second question")
    win.send_chat()

    messages_sent = fake_worker_cls.call_args[0][1]
    assert messages_sent == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
    ]
    assert session.list_sessions() != []  # persisted, unlike the quick session


def test_quick_session_survives_switching_away_and_back(qtbot, config, monkeypatch):
    _patch_worker(monkeypatch)
    win = MainWindow(config)
    qtbot.addWidget(win)
    win.session_list.setCurrentRow(0)

    win.chat_input.setPlainText("remember me")
    win.send_chat()
    win._on_chat_finished("ok")

    win._new_dialog()  # switch to a different, real dialog
    assert win.session_id != windows_mod._QUICK_SESSION_ID

    win.session_list.setCurrentRow(0)  # back to "Quick questions"
    assert win.chat_history == [
        {"role": "user", "content": "remember me"},
        {"role": "assistant", "content": "ok"},
    ]


def test_quick_session_delete_button_clears_instead_of_deleting(qtbot, config, monkeypatch):
    _patch_worker(monkeypatch)
    monkeypatch.setattr(windows_mod.QMessageBox, "question",
                        lambda *a, **k: windows_mod.QMessageBox.StandardButton.Yes)
    win = MainWindow(config)
    qtbot.addWidget(win)
    win.session_list.setCurrentRow(0)

    win.chat_input.setPlainText("hi")
    win.send_chat()
    win._on_chat_finished("hello")
    assert win.quick_history

    win._delete_dialog()

    assert win.quick_history == []
    assert win.chat_history == []
    assert win.session_id == windows_mod._QUICK_SESSION_ID  # stays selected, not removed
    # the pinned row must still be there, not deleted from the list
    assert win.session_list.item(0).data(Qt.ItemDataRole.UserRole) == (
        windows_mod._QUICK_SESSION_ID)


def test_dialog_label_reflects_quick_session(qtbot, config):
    win = MainWindow(config)
    qtbot.addWidget(win)

    win.session_list.setCurrentRow(0)
    assert "not kept" in win.dialog_label.text()

    win._new_dialog()
    assert "is kept" in win.dialog_label.text()


# -- MainWindow: attachments ------------------------------------------------------

def _solid_image(size: int = 10, color: str = "red") -> QImage:
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def test_chat_input_paste_image_emits_signal_instead_of_inserting_text(qtbot):
    widget = _ChatInput()
    qtbot.addWidget(widget)
    mime = QMimeData()
    mime.setImageData(_solid_image())

    received = []
    widget.imagePasted.connect(received.append)
    widget.insertFromMimeData(mime)

    assert len(received) == 1
    assert received[0].width() == 10
    assert widget.toPlainText() == ""


def test_chat_input_drop_file_url_emits_signal_instead_of_inserting_path(qtbot, tmp_path):
    widget = _ChatInput()
    qtbot.addWidget(widget)
    path = tmp_path / "notes.txt"
    path.write_text("hi", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])

    received = []
    widget.filesDropped.connect(received.append)
    widget.insertFromMimeData(mime)

    assert received == [[str(path)]]
    assert widget.toPlainText() == ""


def test_chat_input_plain_text_paste_is_unaffected(qtbot):
    widget = _ChatInput()
    qtbot.addWidget(widget)
    mime = QMimeData()
    mime.setText("hello")

    widget.insertFromMimeData(mime)

    assert widget.toPlainText() == "hello"


def test_chat_input_accepts_image_and_url_mime_types(qtbot):
    widget = _ChatInput()
    qtbot.addWidget(widget)

    image_mime = QMimeData()
    image_mime.setImageData(_solid_image())
    assert widget.canInsertFromMimeData(image_mime) is True

    url_mime = QMimeData()
    url_mime.setUrls([QUrl.fromLocalFile("/tmp/x.txt")])
    assert widget.canInsertFromMimeData(url_mime) is True


def test_paste_image_populates_pending_attachments_and_tray(qtbot, config):
    win = MainWindow(config)
    qtbot.addWidget(win)

    win.chat_input.imagePasted.emit(_solid_image())

    assert len(win._pending_attachments) == 1
    assert win._pending_attachments[0].kind == "image"
    assert not win.attachment_tray.isHidden()


def test_drop_unsupported_file_reports_error_and_adds_nothing(qtbot, config, tmp_path):
    win = MainWindow(config)
    qtbot.addWidget(win)
    bad_file = tmp_path / "bad.bin"
    bad_file.write_bytes(bytes([0, 159, 146, 150, 255, 0, 1, 2]) * 20)

    win.chat_input.filesDropped.emit([str(bad_file)])

    assert win._pending_attachments == []
    assert "bad.bin" in win.statusBar().currentMessage()
    assert win.attachment_tray.isHidden()


def test_drop_text_file_adds_a_file_attachment(qtbot, config, tmp_path):
    win = MainWindow(config)
    qtbot.addWidget(win)
    text_file = tmp_path / "log.txt"
    text_file.write_text("error on line 3", encoding="utf-8")

    win.chat_input.filesDropped.emit([str(text_file)])

    assert len(win._pending_attachments) == 1
    assert win._pending_attachments[0].kind == "file"
    assert win._pending_attachments[0].data == "error on line 3"


def test_remove_attachment_clears_pending_and_hides_tray(qtbot, config):
    win = MainWindow(config)
    qtbot.addWidget(win)
    win._on_image_pasted(_solid_image())
    assert len(win._pending_attachments) == 1

    win._remove_attachment(0)

    assert win._pending_attachments == []
    assert win.attachment_tray.isHidden()


def test_send_with_only_an_attachment_and_no_text_is_allowed(qtbot, config, monkeypatch):
    _patch_worker(monkeypatch)
    win = MainWindow(config)
    qtbot.addWidget(win)
    win._on_image_pasted(_solid_image())
    win.chat_input.setPlainText("")

    win.send_chat()

    sent = win.chat_history[-1]
    assert sent["content"] == ""
    assert sent["attachments"][0]["kind"] == "image"
    assert win._pending_attachments == []


def test_send_attaches_pending_items_to_the_message_and_clears_tray(qtbot, config, monkeypatch):
    fake_worker_cls = _patch_worker(monkeypatch)
    win = MainWindow(config)
    qtbot.addWidget(win)
    win._on_image_pasted(_solid_image())
    win.chat_input.setPlainText("what is this")

    win.send_chat()

    sent = win.chat_history[-1]
    assert sent["content"] == "what is this"
    assert len(sent["attachments"]) == 1
    assert win._pending_attachments == []
    assert win.attachment_tray.isHidden()

    request_messages = fake_worker_cls.call_args[0][1]
    assert request_messages[-1]["attachments"] == sent["attachments"]


def test_send_with_no_text_and_no_attachments_does_nothing(qtbot, config, monkeypatch):
    fake_worker_cls = _patch_worker(monkeypatch)
    win = MainWindow(config)
    qtbot.addWidget(win)
    win.chat_input.setPlainText("")

    win.send_chat()

    assert win.chat_history == []
    fake_worker_cls.assert_not_called()


# -- MainWindow: attach button, preview, splitter sizing ---------------------------

def test_attach_button_opens_file_dialog_and_adds_result(qtbot, config, tmp_path, monkeypatch):
    win = MainWindow(config)
    qtbot.addWidget(win)
    text_file = tmp_path / "notes.txt"
    text_file.write_text("hi", encoding="utf-8")

    monkeypatch.setattr(windows_mod.QFileDialog, "getOpenFileNames",
                        lambda *a, **k: ([str(text_file)], ""))

    win.attach_btn.click()

    assert len(win._pending_attachments) == 1
    assert win._pending_attachments[0].name == "notes.txt"


def test_attach_button_cancelled_dialog_adds_nothing(qtbot, config, monkeypatch):
    win = MainWindow(config)
    qtbot.addWidget(win)
    monkeypatch.setattr(windows_mod.QFileDialog, "getOpenFileNames", lambda *a, **k: ([], ""))

    win.attach_btn.click()

    assert win._pending_attachments == []


def test_clicking_image_chip_opens_preview_dialog(qtbot, config):
    win = MainWindow(config)
    qtbot.addWidget(win)
    win._on_image_pasted(_solid_image())
    chip = win.attachment_tray._layout.itemAt(0).widget()

    opened = []
    with patch.object(_ImagePreviewDialog, "exec", lambda self: opened.append(self.windowTitle())):
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        chip.mousePressEvent(press)

    assert opened == ["pasted-1.png"]


def test_clicking_file_chip_does_not_open_a_preview(qtbot, config, tmp_path):
    win = MainWindow(config)
    qtbot.addWidget(win)
    text_file = tmp_path / "notes.txt"
    text_file.write_text("hi", encoding="utf-8")
    win._on_files_dropped([str(text_file)])
    chip = win.attachment_tray._layout.itemAt(0).widget()

    opened = []
    with patch.object(_ImagePreviewDialog, "exec", lambda self: opened.append(True)):
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        chip.mousePressEvent(press)

    assert opened == []


def test_transcript_renders_clickable_link_for_image_attachment(qtbot, config, monkeypatch):
    _patch_worker(monkeypatch)
    win = MainWindow(config)
    qtbot.addWidget(win)
    win._on_image_pasted(_solid_image())
    win.chat_input.setPlainText("what is this")
    win.send_chat()
    win._on_chat_finished("a red square")

    md = win._chat_transcript(pending_answer=False)

    assert f"]({theme.ATTACHMENT_LINK_SCHEME}:0-0)" in md
    assert win._attachment_by_key["0-0"]["kind"] == "image"


def test_transcript_does_not_link_file_attachments(qtbot, config, tmp_path, monkeypatch):
    _patch_worker(monkeypatch)
    win = MainWindow(config)
    qtbot.addWidget(win)
    text_file = tmp_path / "notes.txt"
    text_file.write_text("hi", encoding="utf-8")
    win._on_files_dropped([str(text_file)])
    win.chat_input.setPlainText("explain")
    win.send_chat()
    win._on_chat_finished("ok")

    md = win._chat_transcript(pending_answer=False)

    assert theme.ATTACHMENT_LINK_SCHEME not in md
    assert "notes" in md and "txt" in md  # escape_user_text backslash-escapes the "."
    assert win._attachment_by_key == {}


def test_attachment_link_click_opens_preview_for_the_right_image(qtbot, config, monkeypatch):
    _patch_worker(monkeypatch)
    win = MainWindow(config)
    qtbot.addWidget(win)
    win._on_image_pasted(_solid_image(color="green"))
    win.chat_input.setPlainText("what is this")
    win.send_chat()
    win._on_chat_finished("a green square")
    win._chat_transcript(pending_answer=False)  # (re)builds _attachment_by_key

    opened = []
    with patch.object(_ImagePreviewDialog, "exec", lambda self: opened.append(self.windowTitle())):
        win._on_attachment_link_clicked("0-0")

    assert opened == ["pasted-1.png"]


def test_unknown_attachment_key_click_does_nothing(qtbot, config):
    win = MainWindow(config)
    qtbot.addWidget(win)

    with patch.object(_ImagePreviewDialog, "exec", lambda self: (_ for _ in ()).throw(
            AssertionError("must not open"))):
        win._on_attachment_link_clicked("9-9")  # no crash, no dialog


def test_pasting_image_does_not_shrink_the_message_input(qtbot, config):
    win = MainWindow(config)
    qtbot.addWidget(win)
    win.show()
    height_before = win.chat_input.height()

    win.chat_input.imagePasted.emit(_solid_image(size=200))
    win._grow_splitter_for_tray()  # normally deferred via QTimer.singleShot(0, ...)

    assert win.chat_input.height() >= height_before


def test_splitter_shrinks_back_after_removing_last_attachment(qtbot, config):
    win = MainWindow(config)
    qtbot.addWidget(win)
    win.show()
    original_sizes = win.chat_splitter.sizes()

    win._on_image_pasted(_solid_image())
    win._grow_splitter_for_tray()
    grown_sizes = win.chat_splitter.sizes()
    assert grown_sizes != original_sizes

    win._remove_attachment(0)  # empties the tray -> shrink runs synchronously

    assert win.chat_splitter.sizes() == original_sizes
