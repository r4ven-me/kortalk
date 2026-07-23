"""kortalk windows: PopupWindow (near the cursor) and MainWindow (two columns)."""

from __future__ import annotations

from datetime import datetime

import shiboken6
from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPalette,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QTextBrowser,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import session, theme
from .config import Config
from .i18n import tr
from .providers import AIWorker


def _worker_running(worker: AIWorker | None) -> bool:
    return worker is not None and shiboken6.isValid(worker) and worker.isRunning()


def _stop_worker(worker: AIWorker | None) -> None:
    """Stops the worker if it is still alive: a finished worker deletes
    itself via deleteLater, and touching it raises RuntimeError."""
    if _worker_running(worker):
        worker.stop()


def _style_as_stop(button: QPushButton, active: bool) -> None:
    """Recolours a primary Send button to Nord's red while it doubles as
    Stop — relabelling it alone is too easy to miss at a glance."""
    if active:
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.NORD['n11']}; color: {theme.NORD['n6']};
                border-color: {theme.NORD['n11']};
            }}
            QPushButton:hover {{ border-color: {theme.NORD['n6']}; }}
            QPushButton:pressed {{ background-color: {theme.NORD['n0']}; }}
        """)
    else:
        button.setStyleSheet("")


class _InsetSplitterHandle(QSplitterHandle):
    """Paints a short, rounded-end bar centered in the handle's own track,
    instead of a line spanning it edge-to-edge.

    QSplitter::handle's CSS width/height/margin turned out unreliable: Qt
    computes a single shared handleWidth() from whichever orientation's
    rule it happens to pick, regardless of the splitter's actual
    orientation — so two splitters needing different thin/thick styling
    fought each other. Painting the handle directly sidesteps that
    entirely and is what makes the "short, rounded, floating" look actually
    possible (the draggable track still spans the full length)."""

    _INSET = 4      # blank space left at each end of the bar's length
    _THICKNESS = 4  # visible bar thickness

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self._hovered = False

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        colors = theme.card_colors(QGuiApplication.instance())
        color = QColor(colors["highlight"] if self._hovered else colors["border"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        rect = self.rect()
        t = self._THICKNESS
        if self.orientation() == Qt.Orientation.Horizontal:
            bar = QRectF((rect.width() - t) / 2, self._INSET, t, rect.height() - 2 * self._INSET)
        else:
            bar = QRectF(self._INSET, (rect.height() - t) / 2, rect.width() - 2 * self._INSET, t)
        painter.drawRoundedRect(bar, t / 2, t / 2)


class _InsetSplitter(QSplitter):
    """QSplitter with a short, rounded, inset handle (see
    _InsetSplitterHandle) — used for the left/right dividers (quick mode,
    and the session list next to the dialog) so both read as the same
    shape rather than a line running the full height."""

    def createHandle(self):
        return _InsetSplitterHandle(self.orientation(), self)


_THINKING_TICK_MS = 400   # animated ellipsis while waiting for the first chunk
_CURSOR_BLINK_MS = 500    # blinking caret while chunks are arriving
_CURSOR_GLYPH = "▍"


class _StreamingBrowser(QTextBrowser):
    """QTextBrowser that streams AI output.

    While a response streams in, text is appended as plain text through a
    cursor placed at the end of the document, instead of the previous
    approach of calling setMarkdown() on a timer to re-render everything —
    that rebuilt the whole document several times a second, which fought
    any text selection the user was making and made the scrollbar visibly
    jump even while parked at the bottom. Markdown formatting (bold, code
    blocks, links) is applied once, when the response is complete.

    Two small animations mark the two waiting states: an animated "Thinking…"
    ellipsis before the first chunk arrives, and a blinking caret at the
    write position once text is streaming in — both edit only the last few
    characters of the document, so they're as selection-safe as the chunks
    themselves.

    An optional `prefix` (set via begin_stream/reset) is rendered ahead of
    the streamed text — dialog mode uses it to keep earlier turns of the
    conversation visible while the newest answer streams in below them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self._buffer = ""
        self._prefix = ""
        self._thinking_base = ""
        self._thinking_shown_len = 0
        self._thinking_frame = 0
        self._cursor_shown = False

        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(_THINKING_TICK_MS)
        self._thinking_timer.timeout.connect(self._tick_thinking)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(_CURSOR_BLINK_MS)
        self._blink_timer.timeout.connect(self._tick_blink)

    def begin_stream(self, placeholder: str, prefix: str = "") -> None:
        self._prefix = prefix
        self._buffer = ""
        self._cursor_shown = False
        self.setMarkdown(prefix)
        self._thinking_base = placeholder
        self._thinking_shown_len = 0
        self._thinking_frame = 0
        self._tick_thinking()
        self._thinking_timer.start()

    def append_chunk(self, delta: str) -> None:
        if not delta:
            return
        if not self._buffer:
            # first chunk: the animated "Thinking…" placeholder is done
            self._thinking_timer.stop()
            self._replace_tail(self._thinking_shown_len, "")
            self._thinking_shown_len = 0
            self._blink_timer.start()
        self._set_cursor_visible(False)  # the caret glyph must not become part of the text
        self._buffer += delta
        self._append_plain(delta)
        self._set_cursor_visible(True)

    def finish(self, full_text: str) -> None:
        self._stop_animations()
        self._buffer = full_text
        self._render_final(full_text or tr("*(empty response)*"))

    def fail(self, message: str) -> None:
        self._stop_animations()
        self._render_final(f"**{tr('Error')}**\n\n{message}")

    def reset(self, placeholder: str = "") -> None:
        """Clears any streamed content and prefix — used to start a fresh
        dialog without the leftover transcript of the previous one."""
        self._stop_animations()
        self._prefix = ""
        self._buffer = ""
        self.setMarkdown(placeholder)

    def text_content(self) -> str:
        return self._buffer

    # -- internals ------------------------------------------------------------

    def _stop_animations(self) -> None:
        self._thinking_timer.stop()
        self._blink_timer.stop()
        self._cursor_shown = False

    def _tick_thinking(self) -> None:
        dots = "." * (self._thinking_frame % 4)
        self._thinking_frame += 1
        text = self._thinking_base + dots
        self._replace_tail(self._thinking_shown_len, text, italic=True)
        self._thinking_shown_len = len(text)

    def _tick_blink(self) -> None:
        self._set_cursor_visible(not self._cursor_shown)

    def _set_cursor_visible(self, show: bool) -> None:
        if show == self._cursor_shown:
            return
        self._cursor_shown = show
        if show:
            self._append_plain(_CURSOR_GLYPH)
        else:
            self._replace_tail(len(_CURSOR_GLYPH), "")

    def _is_stuck_to_bottom(self) -> bool:
        scrollbar = self.verticalScrollBar()
        return scrollbar.value() >= scrollbar.maximum() - 2

    def _scroll_to_bottom(self) -> None:
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _append_plain(self, text: str) -> None:
        if not text:
            return
        stick_to_bottom = self._is_stuck_to_bottom()
        # A cursor created here, rather than self.textCursor(), never touches
        # whatever selection the user currently has in the widget.
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        # A fresh, plain QTextCharFormat keeps streamed text from picking up
        # the italic "thinking" styling (or anything else) left at the tail.
        cursor.insertText(text, QTextCharFormat())
        if stick_to_bottom:
            self._scroll_to_bottom()

    def _replace_tail(self, old_len: int, new_text: str, italic: bool = False) -> None:
        stick_to_bottom = self._is_stuck_to_bottom()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if old_len:
            cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter,
                                QTextCursor.MoveMode.KeepAnchor, old_len)
            cursor.removeSelectedText()
        if new_text:
            fmt = QTextCharFormat()
            if italic:
                fmt.setFontItalic(True)
                fmt.setForeground(self.palette().color(QPalette.ColorRole.PlaceholderText))
            cursor.insertText(new_text, fmt)
        if stick_to_bottom:
            self._scroll_to_bottom()

    def _render_final(self, markdown_text: str) -> None:
        stick_to_bottom = self._is_stuck_to_bottom()
        self.setMarkdown(self._prefix + markdown_text)
        if stick_to_bottom:
            self._scroll_to_bottom()


class _DraggableCard(QFrame):
    """Card that can be dragged by the mouse from anywhere that isn't a
    button or the response text (those consume the press themselves) —
    lets the user reposition the frameless popup before closing it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_from: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.window().pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None
        super().mouseReleaseEvent(event)


class PopupWindow(QWidget):
    """Popup near the cursor: rounded corners, auto-close on an outside
    click (Qt.Popup) and Escape, selectable Markdown response, draggable
    by the mouse until it's closed."""

    open_in_window = Signal(str, str)  # prompt text, response text -> open in the main window

    RADIUS = 12

    def __init__(self, config: Config, provider_name: str):
        # Qt.Popup gives the native "click outside closes" behaviour while
        # clicks INSIDE (text selection, buttons) keep working.
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.config = config
        self.worker: AIWorker | None = None
        self._prompt = ""  # set by ask(); kept for "Open in window"
        width = int(config.get("popup_width"))
        self.max_height = int(config.get("popup_max_height"))
        self.setFixedWidth(width)

        app = QGuiApplication.instance()
        colors = theme.card_colors(app)
        bg, fg = colors["bg"], colors["fg"]
        border, muted, code_bg = colors["border"], colors["muted"], colors["code_bg"]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = _DraggableCard(self)
        self.card.setObjectName("card")
        self.card.setStyleSheet(f"""
            QFrame#card {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: {self.RADIUS}px;
            }}
            QLabel {{ color: {muted}; background: transparent; border: none; }}
            QTextBrowser {{
                background: transparent; border: none; color: {fg};
                selection-background-color: {theme.NORD['n10']};
                selection-color: {theme.NORD['n6']};
            }}
            QPushButton {{
                background: transparent; border: 1px solid transparent; color: {muted};
                padding: 3px 8px; border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {code_bg}; color: {fg}; border-color: {border};
            }}
            QPushButton:pressed {{
                background-color: {theme.NORD['n10']}; color: {theme.NORD['n6']};
                border-color: {theme.NORD['n10']};
            }}

            {theme.scrollbar_stylesheet(colors)}
        """)
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.addWidget(QLabel(provider_name))
        header.addStretch()
        self.stop_btn = QPushButton(tr("Stop"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_answer)
        header.addWidget(self.stop_btn)
        copy_btn = QPushButton(tr("Copy"))
        copy_btn.clicked.connect(self._copy_answer)
        header.addWidget(copy_btn)
        window_btn = QPushButton(tr("Open in window"))
        window_btn.clicked.connect(self._open_in_window)
        header.addWidget(window_btn)
        close_btn = QPushButton("✕")
        close_btn.clicked.connect(self._animated_close)
        header.addWidget(close_btn)
        layout.addLayout(header)

        self.browser = _StreamingBrowser(self.card)
        self.browser.document().setDocumentMargin(0)
        self.browser.document().setDefaultStyleSheet(theme.markdown_content_stylesheet(colors))
        self.browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.browser)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._animated_close)
        self._fade_anim: QPropertyAnimation | None = None

        self.browser.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._adjust_height()
        )

    # -- public API -----------------------------------------------------------

    def ask(self, provider, prompt: str) -> None:
        self._prompt = prompt  # kept for "Open in window", so context isn't lost
        self.browser.begin_stream(tr("Thinking"))
        self.stop_btn.setEnabled(True)
        self.worker = AIWorker(provider, [{"role": "user", "content": prompt}],
                               int(self.config.get("timeout")), int(self.config.get("max_tokens")))
        self.worker.chunk.connect(self.browser.append_chunk)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def show_near_cursor(self) -> None:
        self._adjust_height()
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(pos.x() + 10, geo.right() - self.width() - 8)
        y = min(pos.y() + 12, geo.bottom() - self.height() - 8)
        self.move(max(geo.left() + 8, x), max(geo.top() + 8, y))
        self.setWindowOpacity(0.0)
        self.show()
        self._fade(0.0, 1.0, 130, QEasingCurve.Type.OutCubic)

    # -- internals ------------------------------------------------------------

    def _on_finished(self, text: str) -> None:
        self.browser.finish(text)
        self.stop_btn.setEnabled(False)

    def _on_failed(self, message: str) -> None:
        self.browser.fail(message)
        self.stop_btn.setEnabled(False)

    def _stop_answer(self) -> None:
        _stop_worker(self.worker)
        self.browser.finish(self.browser.text_content())
        self.stop_btn.setEnabled(False)

    def _adjust_height(self) -> None:
        doc_height = self.browser.document().size().height()
        chrome = 64  # header + margins
        height = int(min(self.max_height, max(90, doc_height + chrome)))
        self.setFixedHeight(height)

    def _copy_answer(self) -> None:
        QGuiApplication.clipboard().setText(self.browser.text_content())

    def _open_in_window(self) -> None:
        answer = self.browser.text_content()
        prompt = self._prompt
        self.close()
        self.open_in_window.emit(prompt, answer)

    def _fade(self, start: float, end: float, duration_ms: int,
              easing: QEasingCurve.Type) -> QPropertyAnimation:
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(duration_ms)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(easing)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_anim = anim  # keep a live reference until it finishes
        return anim

    def _animated_close(self) -> None:
        # Escape / the ✕ button get a quick fade instead of an abrupt
        # disappearance; an outside click still closes instantly (native
        # Qt.Popup behaviour) since intercepting that path isn't worth
        # the complexity for a one-off popup.
        anim = self._fade(self.windowOpacity(), 0.0, 110, QEasingCurve.Type.InCubic)
        anim.finished.connect(self.close)

    def closeEvent(self, event) -> None:
        _stop_worker(self.worker)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    """Full window with two modes, switched by a dedicated toolbar button:

    - quick mode (default): prompt+text on the left, response on the right —
      every send is independent, matching the popup's fast, stateless feel.
    - dialog mode: a single conversation thread that keeps full context
      (every earlier turn is resent to the provider), for when a quick
      one-off isn't enough and the user wants to go back and forth.

    The two are kept on separate stack pages rather than blended into one
    view: dialog mode is opt-in and never changes what the quick panel does,
    so reaching for fast, no-context answers stays exactly as immediate as
    before.
    """

    settings_requested = Signal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.worker: AIWorker | None = None
        self._fade_anim: QPropertyAnimation | None = None

        # Pick up the most recently active dialog, if any — the session
        # list (built below) lets the user switch to a different one.
        self.session_id: int | None = None
        self.chat_history: list[dict] = []
        sessions = session.list_sessions()
        if sessions:
            self.session_id = sessions[0].id
            self.chat_history = session.load_session(self.session_id)

        self.setWindowTitle("Kortalk")
        self.resize(960, 560)
        self.setWindowIcon(theme.make_tray_icon())
        theme.apply_window_theme(self)

        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel(" " + tr("Provider:") + " "))
        self.provider_combo = QComboBox()
        self.reload_providers()
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        toolbar.addWidget(self.provider_combo)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.chat_toggle = QToolButton()
        self.chat_toggle.setObjectName("chatToggle")
        self.chat_toggle.setCheckable(True)
        self.chat_toggle.setToolTip(tr(
            "Dialog mode: keeps the conversation and its context across "
            "messages. The quick panel stays untouched for fast one-off asks."
        ))
        self._set_chat_toggle_label(False)
        self.chat_toggle.toggled.connect(self._toggle_chat_mode)
        toolbar.addWidget(self.chat_toggle)

        settings_action = QAction(tr("Settings"), self)
        settings_action.triggered.connect(self.settings_requested.emit)
        toolbar.addAction(settings_action)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.quick_page = self._build_quick_page()
        self.chat_page = self._build_chat_page()
        self.stack.addWidget(self.quick_page)
        self.stack.addWidget(self.chat_page)
        self._apply_code_style()

        QShortcut(QKeySequence("Ctrl+Return"), self, self._send_active)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)

        self.statusBar().showMessage(tr("Ready"))

    # -- page construction ------------------------------------------------------

    def _build_quick_page(self) -> QWidget:
        splitter = _InsetSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 10, 8)
        left_layout.addWidget(QLabel(tr("Prompt + text:")))
        self.input_edit = QPlainTextEdit()
        left_layout.addWidget(self.input_edit)
        self.send_btn = QPushButton(tr("Send (Ctrl+Enter)"))
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.clicked.connect(self._send_or_stop)
        left_layout.addWidget(self.send_btn)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 8, 8, 8)
        right_layout.addWidget(QLabel(tr("Response:")))
        self.output = _StreamingBrowser()
        right_layout.addWidget(self.output)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([480, 480])
        return splitter

    def _build_chat_page(self) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        page_splitter = _InsetSplitter(Qt.Orientation.Horizontal)
        page_splitter.setHandleWidth(8)
        page_splitter.addWidget(self._build_session_panel())
        page_splitter.addWidget(self._build_conversation_panel())
        page_splitter.setSizes([220, 700])
        outer.addWidget(page_splitter)
        return page

    def _build_session_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 12, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(QLabel(tr("Dialogs:")))

        self.session_list = QListWidget()
        self.session_list.currentItemChanged.connect(self._session_row_changed)
        layout.addWidget(self.session_list, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.new_dialog_btn = QPushButton(tr("New dialog"))
        self.new_dialog_btn.clicked.connect(self._new_dialog)
        btn_row.addWidget(self.new_dialog_btn, 1)
        self.delete_dialog_btn = QPushButton(tr("Delete"))
        self.delete_dialog_btn.setToolTip(tr("Delete this dialog"))
        self.delete_dialog_btn.clicked.connect(self._delete_dialog)
        btn_row.addWidget(self.delete_dialog_btn, 1)
        layout.addLayout(btn_row)

        self._reload_session_list()
        return panel

    def _build_conversation_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 12, 8, 10)
        layout.setSpacing(8)
        layout.addWidget(QLabel(tr("Dialog — context is kept between messages")))

        # A vertical splitter (not a fixed-height input box) lets the user
        # drag the divider up when a message needs more room to compose.
        chat_splitter = QSplitter(Qt.Orientation.Vertical)
        chat_splitter.setHandleWidth(4)

        # chat_browser and the input row are wrapped in their own containers
        # (rather than added to the splitter directly) purely to get a
        # margin on the side facing the divider — a thin handle plus a
        # bit of real breathing room either side of it, not a thicker bar.
        browser_container = QWidget()
        browser_layout = QVBoxLayout(browser_container)
        browser_layout.setContentsMargins(0, 0, 0, 8)
        self.chat_browser = _StreamingBrowser()
        self._refresh_chat_view()
        browser_layout.addWidget(self.chat_browser)
        chat_splitter.addWidget(browser_container)

        input_widget = QWidget()
        input_row = QHBoxLayout(input_widget)
        input_row.setContentsMargins(0, 8, 0, 0)
        self.chat_input = QPlainTextEdit()
        self.chat_input.setPlaceholderText(tr("Message… (Ctrl+Enter to send)"))
        self.chat_input.setMinimumHeight(40)
        input_row.addWidget(self.chat_input, 1)
        self.chat_send_btn = QPushButton(tr("Send (Ctrl+Enter)"))
        self.chat_send_btn.setObjectName("primaryButton")
        self.chat_send_btn.clicked.connect(self._chat_send_or_stop)
        input_row.addWidget(self.chat_send_btn)
        chat_splitter.addWidget(input_widget)

        chat_splitter.setStretchFactor(0, 1)
        chat_splitter.setStretchFactor(1, 0)
        chat_splitter.setSizes([420, 90])

        layout.addWidget(chat_splitter, 1)
        return panel

    # -- dialog mode --------------------------------------------------------------

    def _set_chat_toggle_label(self, in_chat_mode: bool) -> None:
        # The colour change alone (QSS :checked state) is easy to miss in a
        # toolbar — swapping the label makes "how do I get back" obvious.
        text = ("◀ " + tr("Quick mode")) if in_chat_mode else ("💬 " + tr("Dialog"))
        self.chat_toggle.setText(text)

    def _toggle_chat_mode(self, checked: bool) -> None:
        self._set_chat_toggle_label(checked)
        if checked:
            self._seed_chat_from_quick()
            self.stack.setCurrentWidget(self.chat_page)
            self.chat_input.setFocus()
        else:
            self.stack.setCurrentWidget(self.quick_page)

    def _seed_chat_from_quick(self) -> None:
        # First switch only: carry the quick panel's last Q&A into the
        # dialog so context isn't lost when moving between the two modes.
        # Once the dialog has turns of its own, it's left alone.
        if not self.chat_history:
            prompt = self.input_edit.toPlainText().strip()
            answer = self.output.text_content().strip()
            if prompt and answer:
                self.chat_history = [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": answer},
                ]
                self._persist_dialog()
        self._refresh_chat_view()

    def _chat_transcript(self, pending_answer: bool) -> str:
        turns = [
            f"**{tr('You') if m['role'] == 'user' else tr('Assistant')}:**\n\n{m['content']}"
            for m in self.chat_history
        ]
        md = "\n\n---\n\n".join(turns)
        if pending_answer:
            md += "\n\n---\n\n" + f"**{tr('Assistant')}:**\n\n"
        return md

    def _refresh_chat_view(self) -> None:
        if not self.chat_history:
            self.chat_browser.reset(
                f"*{tr('Dialog mode — context is kept between messages.')}*")
        else:
            self.chat_browser.reset(self._chat_transcript(pending_answer=False))

    # -- session list ---------------------------------------------------------

    def _session_label(self, meta: session.SessionMeta) -> str:
        try:
            stamp = datetime.fromisoformat(meta.updated_at).astimezone().strftime("%d.%m %H:%M")
        except ValueError:
            stamp = ""
        return f"{meta.title}   —   {stamp}" if stamp else meta.title

    def _reload_session_list(self) -> None:
        self.session_list.blockSignals(True)
        self.session_list.clear()
        selected_row = -1
        for i, meta in enumerate(session.list_sessions()):
            item = QListWidgetItem(self._session_label(meta))
            item.setData(Qt.ItemDataRole.UserRole, meta.id)
            self.session_list.addItem(item)
            if meta.id == self.session_id:
                selected_row = i
        if selected_row >= 0:
            self.session_list.setCurrentRow(selected_row)
        self.session_list.blockSignals(False)

    def _session_row_changed(self, current: QListWidgetItem | None,
                             _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        session_id = current.data(Qt.ItemDataRole.UserRole)
        if session_id == self.session_id:
            return
        _stop_worker(self.worker)
        self.session_id = session_id
        self.chat_history = session.load_session(session_id)
        self.chat_input.clear()
        self._set_chat_sending(False)
        self._refresh_chat_view()

    def _derive_session_title(self) -> str:
        for m in self.chat_history:
            if m["role"] == "user" and m["content"].strip():
                text = " ".join(m["content"].split())
                return text[:40] + ("…" if len(text) > 40 else "")
        return tr("Dialog")

    def _persist_dialog(self) -> None:
        if self.session_id is None:
            self.session_id = session.create_session(
                self._derive_session_title(), self.chat_history)
        else:
            session.save_session(self.session_id, self.chat_history)
        self._reload_session_list()

    def _delete_dialog(self) -> None:
        if self.session_id is None:
            return  # an unsaved new dialog — nothing to delete yet
        confirmed = QMessageBox.question(
            self, tr("Delete this dialog"),
            tr("Delete this dialog permanently? This cannot be undone."),
        ) == QMessageBox.StandardButton.Yes
        if not confirmed:
            return
        _stop_worker(self.worker)
        session.delete_session(self.session_id)
        remaining = session.list_sessions()
        self.session_id = remaining[0].id if remaining else None
        self.chat_history = session.load_session(self.session_id) if remaining else []
        self.chat_input.clear()
        self._set_chat_sending(False)
        self._refresh_chat_view()
        self._reload_session_list()
        self.statusBar().showMessage(tr("Dialog deleted"))

    def _new_dialog(self) -> None:
        _stop_worker(self.worker)
        self.session_id = None  # the next message starts a new saved dialog
        self.chat_history = []
        self.chat_input.clear()
        self._set_chat_sending(False)
        self._refresh_chat_view()
        self._reload_session_list()
        self.statusBar().showMessage(tr("New dialog started"))

    def send_chat(self) -> None:
        text = self.chat_input.toPlainText().strip()
        if not text:
            return
        _stop_worker(self.worker)

        provider = self.config.provider(self.provider_combo.currentData())
        if provider is None:
            self.statusBar().showMessage(tr("Provider not found — check settings"))
            return

        self.chat_history.append({"role": "user", "content": text})
        self._persist_dialog()
        self.chat_input.clear()
        self._set_chat_sending(True)
        self.statusBar().showMessage(tr("Requesting {name}…").format(name=provider.name))
        self.chat_browser.begin_stream(
            tr("Thinking"), prefix=self._chat_transcript(pending_answer=True))

        self.worker = AIWorker(provider, list(self.chat_history), int(self.config.get("timeout")),
                               int(self.config.get("max_tokens")))
        self.worker.chunk.connect(self.chat_browser.append_chunk)
        self.worker.finished_ok.connect(self._on_chat_finished)
        self.worker.failed.connect(self._on_chat_failed)
        self.worker.start()

    def _set_chat_sending(self, sending: bool) -> None:
        self.chat_send_btn.setText(tr("Stop") if sending else tr("Send (Ctrl+Enter)"))
        _style_as_stop(self.chat_send_btn, sending)

    def _chat_send_or_stop(self) -> None:
        if _worker_running(self.worker):
            _stop_worker(self.worker)
            # keep whatever streamed in so far as the turn's answer — dropping
            # it silently would mean the next message loses that context too
            partial = self.chat_browser.text_content().strip()
            if partial:
                self.chat_history.append({"role": "assistant", "content": partial})
                self._persist_dialog()
                self.chat_browser.finish(partial)
            else:
                self._refresh_chat_view()  # nothing streamed yet: drop the placeholder
            self._set_chat_sending(False)
            self.statusBar().showMessage(tr("Stopped"))
        else:
            self.send_chat()

    def _on_chat_finished(self, text: str) -> None:
        self.chat_history.append({"role": "assistant", "content": text})
        self._persist_dialog()
        self.chat_browser.finish(text)
        self._set_chat_sending(False)
        self.statusBar().showMessage(tr("Done"))

    def _on_chat_failed(self, message: str) -> None:
        self.chat_browser.fail(message)
        self._set_chat_sending(False)
        self.statusBar().showMessage(tr("Error"))

    def _send_active(self) -> None:
        if self.stack.currentWidget() is self.chat_page:
            self.send_chat()
        else:
            self.send()

    # -- shared -------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setWindowOpacity(0.0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(160)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_anim = anim

    def refresh_theme(self) -> None:
        self.setWindowIcon(theme.make_tray_icon())
        theme.apply_window_theme(self)
        self._apply_code_style()

    def _apply_code_style(self) -> None:
        colors = theme.card_colors(QGuiApplication.instance())
        stylesheet = theme.markdown_content_stylesheet(colors)
        self.output.document().setDefaultStyleSheet(stylesheet)
        self.chat_browser.document().setDefaultStyleSheet(stylesheet)

    def reload_providers(self) -> None:
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        active_id = str(self.config.get("active_provider"))
        for i, p in enumerate(self.config.providers()):
            self.provider_combo.addItem(p.name, p.id)
            if p.id == active_id:
                self.provider_combo.setCurrentIndex(i)
        self.provider_combo.blockSignals(False)

    def set_input(self, text: str) -> None:
        self.input_edit.setPlainText(text)

    def set_output(self, text: str) -> None:
        self.output.finish(text)

    def send(self) -> None:
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        _stop_worker(self.worker)

        provider = self.config.provider(self.provider_combo.currentData())
        if provider is None:
            self.statusBar().showMessage(tr("Provider not found — check settings"))
            return

        self._set_quick_sending(True)
        self.statusBar().showMessage(tr("Requesting {name}…").format(name=provider.name))
        self.output.begin_stream(tr("Thinking"))

        self.worker = AIWorker(provider, [{"role": "user", "content": text}],
                               int(self.config.get("timeout")), int(self.config.get("max_tokens")))
        self.worker.chunk.connect(self.output.append_chunk)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _set_quick_sending(self, sending: bool) -> None:
        self.send_btn.setText(tr("Stop") if sending else tr("Send (Ctrl+Enter)"))
        _style_as_stop(self.send_btn, sending)

    def _send_or_stop(self) -> None:
        # While a response is streaming the same button doubles as Stop —
        # no separate control needed, and it's always the obvious thing to
        # click since it's the one that just said "Send".
        if _worker_running(self.worker):
            _stop_worker(self.worker)
            self.output.finish(self.output.text_content())
            self._set_quick_sending(False)
            self.statusBar().showMessage(tr("Stopped"))
        else:
            self.send()

    def _on_finished(self, text: str) -> None:
        self.output.finish(text)
        self._set_quick_sending(False)
        self.statusBar().showMessage(tr("Done"))

    def _on_failed(self, message: str) -> None:
        self.output.fail(message)
        self._set_quick_sending(False)
        self.statusBar().showMessage(tr("Error"))

    def _provider_changed(self) -> None:
        pid = self.provider_combo.currentData()
        if pid:
            self.config.set("active_provider", pid)
            self.config.sync()

    def closeEvent(self, event) -> None:
        # The window closes, the application stays alive in the tray.
        _stop_worker(self.worker)
        super().closeEvent(event)
