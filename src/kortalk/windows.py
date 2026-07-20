"""kortalk windows: PopupWindow (near the cursor) and MainWindow (two columns)."""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCursor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .config import Config
from .i18n import tr
from .providers import AIWorker

_RENDER_INTERVAL_MS = 80  # Markdown re-render rate while streaming


def _stop_worker(worker: AIWorker | None) -> None:
    """Stops the worker if it is still alive: a finished worker deletes
    itself via deleteLater, and touching it raises RuntimeError."""
    if worker is not None and shiboken6.isValid(worker) and worker.isRunning():
        worker.stop()


class _StreamingBrowser(QTextBrowser):
    """QTextBrowser that accumulates streamed text and renders Markdown."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self._buffer = ""
        self._dirty = False
        self._timer = QTimer(self)
        self._timer.setInterval(_RENDER_INTERVAL_MS)
        self._timer.timeout.connect(self._render_if_dirty)

    def begin_stream(self, placeholder: str) -> None:
        self._buffer = ""
        self._dirty = False
        self.setMarkdown(placeholder)
        self._timer.start()

    def append_chunk(self, delta: str) -> None:
        self._buffer += delta
        self._dirty = True

    def finish(self, full_text: str) -> None:
        self._timer.stop()
        self._buffer = full_text
        self.setMarkdown(full_text or tr("*(empty response)*"))

    def fail(self, message: str) -> None:
        self._timer.stop()
        self.setMarkdown(f"**{tr('Error')}**\n\n{message}")

    def text_content(self) -> str:
        return self._buffer

    def _render_if_dirty(self) -> None:
        if self._dirty:
            self._dirty = False
            scrollbar = self.verticalScrollBar()
            stick_to_bottom = scrollbar.value() >= scrollbar.maximum() - 4
            self.setMarkdown(self._buffer + " ▌")
            if stick_to_bottom:
                scrollbar.setValue(scrollbar.maximum())


class PopupWindow(QWidget):
    """Popup near the cursor: rounded corners, auto-close on an outside
    click (Qt.Popup) and Escape, selectable Markdown response."""

    open_in_window = Signal(str)  # response text -> open in the main window

    RADIUS = 12

    def __init__(self, config: Config, provider_name: str):
        # Qt.Popup gives the native "click outside closes" behaviour while
        # clicks INSIDE (text selection, buttons) keep working.
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.config = config
        self.worker: AIWorker | None = None
        width = int(config.get("popup_width"))
        self.max_height = int(config.get("popup_max_height"))
        self.setFixedWidth(width)

        app = QGuiApplication.instance()
        colors = theme.card_colors(app)
        bg, fg = colors["bg"], colors["fg"]
        border, muted, code_bg = colors["border"], colors["muted"], colors["code_bg"]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
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
                background: transparent; border: none; color: {muted};
                padding: 2px 6px; border-radius: 4px;
            }}
            QPushButton:hover {{ background-color: {code_bg}; color: {fg}; }}
        """)
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.addWidget(QLabel(provider_name))
        header.addStretch()
        copy_btn = QPushButton(tr("Copy"))
        copy_btn.clicked.connect(self._copy_answer)
        header.addWidget(copy_btn)
        window_btn = QPushButton(tr("Open in window"))
        window_btn.clicked.connect(self._open_in_window)
        header.addWidget(window_btn)
        close_btn = QPushButton("✕")
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        layout.addLayout(header)

        self.browser = _StreamingBrowser(self.card)
        self.browser.document().setDocumentMargin(0)
        # background for Markdown code blocks
        self.browser.document().setDefaultStyleSheet(
            f"pre, code {{ background-color: {code_bg}; }}"
        )
        self.browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.browser)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)

        self.browser.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._adjust_height()
        )

    # -- public API -----------------------------------------------------------

    def ask(self, provider, prompt: str) -> None:
        self.browser.begin_stream(tr("*Thinking…*"))
        self.worker = AIWorker(provider, prompt, int(self.config.get("timeout")),
                               int(self.config.get("max_tokens")))
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
        self.show()

    # -- internals ------------------------------------------------------------

    def _on_finished(self, text: str) -> None:
        self.browser.finish(text)

    def _on_failed(self, message: str) -> None:
        self.browser.fail(message)

    def _adjust_height(self) -> None:
        doc_height = self.browser.document().size().height()
        chrome = 64  # header + margins
        height = int(min(self.max_height, max(90, doc_height + chrome)))
        self.setFixedHeight(height)

    def _copy_answer(self) -> None:
        QGuiApplication.clipboard().setText(self.browser.text_content())

    def _open_in_window(self) -> None:
        text = self.browser.text_content()
        self.close()
        self.open_in_window.emit(text)

    def closeEvent(self, event) -> None:
        _stop_worker(self.worker)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    """Two-column window: prompt+text on the left, response on the right;
    provider selector in the toolbar."""

    settings_requested = Signal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.worker: AIWorker | None = None

        self.setWindowTitle("kortalk")
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

        settings_action = QAction(tr("Settings"), self)
        settings_action.triggered.connect(self.settings_requested.emit)
        toolbar.addAction(settings_action)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 4, 8)
        left_layout.addWidget(QLabel(tr("Prompt + text:")))
        self.input_edit = QPlainTextEdit()
        left_layout.addWidget(self.input_edit)
        self.send_btn = QPushButton(tr("Send (Ctrl+Enter)"))
        self.send_btn.clicked.connect(self.send)
        left_layout.addWidget(self.send_btn)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 8, 8, 8)
        right_layout.addWidget(QLabel(tr("Response:")))
        self.output = _StreamingBrowser()
        right_layout.addWidget(self.output)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([480, 480])

        QShortcut(QKeySequence("Ctrl+Return"), self, self.send)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)

        self.statusBar().showMessage(tr("Ready"))

    def refresh_theme(self) -> None:
        self.setWindowIcon(theme.make_tray_icon())
        theme.apply_window_theme(self)

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

        self.send_btn.setEnabled(False)
        self.statusBar().showMessage(tr("Requesting {name}…").format(name=provider.name))
        self.output.begin_stream(tr("*Thinking…*"))

        self.worker = AIWorker(provider, text, int(self.config.get("timeout")),
                               int(self.config.get("max_tokens")))
        self.worker.chunk.connect(self.output.append_chunk)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_finished(self, text: str) -> None:
        self.output.finish(text)
        self.send_btn.setEnabled(True)
        self.statusBar().showMessage(tr("Done"))

    def _on_failed(self, message: str) -> None:
        self.output.fail(message)
        self.send_btn.setEnabled(True)
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
