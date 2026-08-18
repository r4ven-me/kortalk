"""kortalk windows: PopupWindow (near the cursor) and MainWindow (two columns)."""

from __future__ import annotations

import base64
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import shiboken6
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QLineF,
    QMimeData,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QClipboard,
    QColor,
    QCursor,
    QDesktopServices,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
    QRegion,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QTextBrowser,
    QTextEdit,
    QToolBar,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import attachments, session, theme
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


def _copy_to_clipboard(text: str) -> None:
    """Copies to the regular clipboard (Ctrl+V) and, on X11, also to the
    PRIMARY selection (middle-click paste) — on Linux those are two
    genuinely separate buffers, and a Copy button that only fills one of
    them surprises anyone in the habit of middle-click pasting. A no-op on
    platforms without a selection buffer (Wayland without the primary-
    selection protocol, Windows, macOS)."""
    clipboard = QGuiApplication.clipboard()
    clipboard.setText(text)
    if clipboard.supportsSelection():
        clipboard.setText(text, QClipboard.Mode.Selection)


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

    _INSET = 16     # blank space left at the leading end of the bar's length —
                     # matches the 16px margin around the markdown <hr>
                     # dividers in the transcript (theme.markdown_content_stylesheet)
    _THICKNESS = 4  # visible bar thickness

    def __init__(self, orientation, parent, trailing_inset: int | None = None):
        super().__init__(orientation, parent)
        self._hovered = False
        # Lets one end of the bar run closer to the panel's edge than the
        # other — the dialog list/conversation divider's bottom end sat too
        # far short of the buttons row beneath it otherwise.
        self._trailing_inset = self._INSET if trailing_inset is None else trailing_inset

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
            length = rect.height() - self._INSET - self._trailing_inset
            bar = QRectF((rect.width() - t) / 2, self._INSET, t, length)
        else:
            length = rect.width() - self._INSET - self._trailing_inset
            bar = QRectF(self._INSET, (rect.height() - t) / 2, length, t)
        painter.drawRoundedRect(bar, t / 2, t / 2)


class _InsetSplitter(QSplitter):
    """QSplitter with a short, rounded, inset handle (see
    _InsetSplitterHandle) — used for the divider between the session list
    and the dialog panel."""

    def __init__(self, orientation, parent=None, trailing_inset: int | None = None):
        super().__init__(orientation, parent)
        self._trailing_inset = trailing_inset

    def createHandle(self):
        return _InsetSplitterHandle(self.orientation(), self, self._trailing_inset)


_THINKING_TICK_MS = 400   # animated ellipsis while waiting for the first chunk
_CURSOR_BLINK_MS = 500    # blinking caret while chunks are arriving
_LIVE_FORMAT_MS = 400     # how often the still-arriving answer is re-rendered as markdown
_CURSOR_GLYPH = "▍"


class _ProseBrowser(QTextBrowser):
    """Read-only rich text for one prose segment: no scrollbar of its own in
    either direction (both disabled) — it just reports the height its
    content needs to word-wrap at whatever width its layout gives it, and
    stays that tall. A fenced code block never reaches this widget at all
    (see _CodeBlockWidget) — only prose ever needs to shrink/grow with the
    window, code deliberately doesn't.

    Also used, unstyled by markdown, as the "live" streaming target: chunks
    land here as plain text through a cursor while a response is arriving,
    the same auto-height behaviour applying to it too."""

    heightChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # outline (not just border) must be reset too — Qt's QSS engine
        # draws a dotted focus rectangle via the `outline` property whenever
        # a styled widget gains keyboard focus, which clicking an inline
        # `code` copy-link inside this browser does, regardless of
        # border:none; it showed up as a stray box appearing under the text
        # right after a click.
        self.setStyleSheet("background: transparent; border: none; outline: none;")
        self.document().setDocumentMargin(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit_height()

    def fit_height(self) -> None:
        """Recomputes the wrapped height at the current width. Called from
        resizeEvent when the *width* changes, and explicitly by whatever
        just mutated the *content* at a fixed width (streamed chunks) —
        resizeEvent alone wouldn't fire for those, since the widget's own
        size doesn't change until this runs."""
        doc = self.document()
        doc.setTextWidth(self.viewport().width())
        height = max(1, int(doc.size().height()))
        if self.height() != height:
            self.setFixedHeight(height)
            self.heightChanged.emit()

    def viewportEvent(self, event) -> bool:
        # Same fix as _CodeScrollArea/_GutterBrowser, for the same reason:
        # a QTextBrowser is itself a QAbstractScrollArea and accepts a
        # vertical wheel event by default even with both scrollbars off and
        # nothing of its own to scroll — which silently swallowed the
        # conversation's own scrolling wherever the cursor happened to be
        # over a paragraph rather than a code block or the empty margin
        # around them, making mouse-wheel scrolling feel like it randomly
        # stopped working right at a text/code boundary. Ignoring it here
        # instead lets it propagate to the enclosing conversation
        # QScrollArea, same as everywhere else in the transcript.
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta()
            if abs(delta.y()) > abs(delta.x()):
                event.ignore()
                return False
        return super().viewportEvent(event)


class _CodeScrollArea(QScrollArea):
    """QScrollArea with vertical scrolling permanently off (see
    _CodeBlockWidget) — but a QScrollArea still *accepts* a vertical wheel
    event by default even with nothing to do with it, which stopped the
    conversation from scrolling at all wherever the cursor sat over a code
    block. Wheel events with more vertical than horizontal motion are
    rejected here instead, so Qt's normal event propagation carries them up
    to the enclosing conversation QScrollArea; a mostly-horizontal one
    (Shift+wheel, a trackpad swipe) still scrolls the code block itself.

    Overriding wheelEvent() alone doesn't catch this: actual wheel input
    over the scrolled content lands on the *viewport* child widget, handled
    through QAbstractScrollArea's own viewportEvent() plumbing, not through
    this widget's own wheelEvent() (which only fires for the rare wheel
    turn directly over the scroll area's frame)."""

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta()
            if abs(delta.y()) > abs(delta.x()):
                event.ignore()
                return False
        return super().viewportEvent(event)


class _GutterBrowser(QTextBrowser):
    """The line-number gutter: same fix as _CodeScrollArea, and for the
    same reason — it's an unrelated widget (a QTextBrowser is itself a
    QAbstractScrollArea, same as QScrollArea), but sits right next to the
    code body, so the cursor is just as often over it as over the code
    when scrolling. With both its own scrollbars permanently off (nothing
    in it ever needs scrolling — see _CodeBlockWidget), a plain
    QTextBrowser still silently swallowed a vertical wheel turn instead of
    leaving it for the conversation to scroll, making the whole page
    seem to "stick" wherever the cursor happened to be over the numbers
    rather than the code next to them."""

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta()
            if abs(delta.y()) > abs(delta.x()):
                event.ignore()
                return False
        return super().viewportEvent(event)


def _indent_guide_columns(source: str) -> list[list[int]]:
    """Per source line, the character columns where an indent guide (as in
    code editors) should be drawn — every full indent level strictly before
    that line's own indentation, e.g. a line indented 8 spaces (two levels
    of 4) gets guides at columns 0 and 4, not at 8 where its code starts.

    The indent unit is guessed from the first indented line rather than
    computed precisely (no need for more than that here); blank lines
    borrow the shallower of their two surrounding non-blank lines' depths,
    so a guide runs through them continuously instead of leaving a gap."""
    lines = [ln.expandtabs(4) for ln in source.split("\n")]
    raw_depths: list[int | None] = []
    for line in lines:
        stripped = line.lstrip(" ")
        raw_depths.append(len(line) - len(stripped) if stripped else None)
    unit = next((d for d in raw_depths if d), 4)
    depths = list(raw_depths)
    prev = 0
    for i, d in enumerate(depths):
        if d is None:
            depths[i] = prev
        else:
            prev = d
    nxt = 0
    for i in range(len(depths) - 1, -1, -1):
        if raw_depths[i] is None:
            depths[i] = min(depths[i], nxt)
        else:
            nxt = raw_depths[i]
    return [list(range(0, d, unit)) for d in depths]


class _CodeBodyBrowser(QTextBrowser):
    """The code panel's own text view — same as a plain QTextBrowser except
    for one thing: paintEvent draws indent guides (as in code editors)
    after the base implementation renders the syntax-highlighted text.
    Drawing after rather than before doesn't risk drawing under a glyph:
    a guide's x column only ever falls inside a line's *own* leading
    whitespace (see _indent_guide_columns), never past where its text
    starts, so there's never a glyph there to cover.

    The guide positions are computed once, when the block is built (see
    set_guides), from the raw source — not re-derived from the
    syntax-highlighted HTML on every paint — so repainting (e.g. on
    scroll) is just a handful of cached drawLine calls, however long the
    file is."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._guide_lines: list[QLineF] = []
        self._guide_color = QColor()

    def set_guides(self, source: str, color: str) -> None:
        # The whole highlighted body is HTML `<br />`-joined into a *single*
        # <pre>, which QTextDocument turns into one QTextBlock containing a
        # line separator per source line — not one block per line — so
        # every source line's geometry has to be read from that one block's
        # layout().lineAt(N), not from findBlockByNumber(N) (which would
        # just be the same single block, or invalid, for every N > 0).
        self._guide_color = QColor(color)
        lines: list[QLineF] = []
        layout = self.document().firstBlock().layout()
        if layout is not None:
            columns_by_line = _indent_guide_columns(source)
            line_count = min(len(columns_by_line), layout.lineCount())

            def x_at(line_no: int, col: int) -> float:
                text_line = layout.lineAt(line_no)
                return layout.position().x() + text_line.cursorToX(text_line.textStart() + col)[0]

            def top_of(line_no: int) -> float:
                text_line = layout.lineAt(line_no)
                return layout.position().y() + text_line.y()

            def bottom_of(line_no: int) -> float:
                text_line = layout.lineAt(line_no)
                return layout.position().y() + text_line.y() + text_line.height()

            # Grouped by column, then merged into one segment per run of
            # consecutive lines — a QLineF per source line instead left a
            # hairline gap between adjacent segments at certain zoom levels,
            # reading as a broken/dashed line rather than one solid one.
            lines_by_column: dict[int, list[int]] = {}
            for line_no in range(line_count):
                for col in columns_by_line[line_no]:
                    lines_by_column.setdefault(col, []).append(line_no)
            for col, line_nos in lines_by_column.items():
                run_start = line_nos[0]
                prev = line_nos[0]
                for line_no in line_nos[1:]:
                    if line_no != prev + 1:
                        x = x_at(run_start, col)
                        lines.append(QLineF(x, top_of(run_start), x, bottom_of(prev)))
                        run_start = line_no
                    prev = line_no
                x = x_at(run_start, col)
                lines.append(QLineF(x, top_of(run_start), x, bottom_of(prev)))
        self._guide_lines = lines
        self.viewport().update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._guide_lines:
            return
        painter = QPainter(self.viewport())
        painter.setPen(self._guide_color)
        painter.drawLines(self._guide_lines)
        painter.end()


class _CodeBlockWidget(QWidget):
    """A fenced code block: language label + Copy button header, and a body
    with its own independent horizontal QScrollArea — a real widget, not an
    HTML table cell embedded in the shared conversation document.

    That distinction is the point: a `width="N"` table cell is only a
    hint — Qt still grows it to fit unwrappable content — so a long,
    un-wrapped line used to force the *entire* document, and from there the
    window itself, wider to fit it. A real QScrollArea can legitimately be
    narrower than its content and show a scrollbar instead, right under the
    block, the way most web chat UIs handle it — Qt's rich text has no
    equivalent notion of an independently scrollable region."""

    def __init__(
        self, label: str, source: str, highlighted_html: str,
        font_family: str = theme.CODE_FONT_FAMILY, parent=None,
    ):
        super().__init__(parent)
        self._source = source
        # Without an explicit Fixed vertical policy, this widget is the one
        # that silently absorbed leftover space whenever the conversation's
        # own QScrollArea (setWidgetResizable(True)) stretched its content
        # widget to fill a taller-than-needed viewport — ballooning a single
        # -line code block to several times its real height. _ProseBrowser
        # already pins itself the same way for the same reason.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 8)
        outer.setSpacing(0)

        header = QWidget(self)
        header.setStyleSheet(
            f"background-color: {theme.CODE_LABEL_BG}; "
            f"border: 1px solid {theme.CODE_BORDER}; border-bottom: none;"
        )
        head_row = QHBoxLayout(header)
        head_row.setContentsMargins(theme.CODE_LABEL_PADDING, 2, 2, 2)
        lang_label = QLabel(label or "text", header)
        lang_label.setStyleSheet(
            f"color: {theme.CODE_FG}; background: transparent; border: none; "
            f"font-family: {font_family}; font-size: 13px;"
        )
        head_row.addWidget(lang_label)
        head_row.addStretch(1)
        copy_btn = QPushButton("\U0001F4CB " + tr("Copy"), header)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.CODE_COPY_BG}; color: {theme.CODE_FG};
                border: none; border-radius: 4px; padding: 2px 10px;
                font-family: {font_family}; font-weight: 600; font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {theme.NORD['n10']}; color: {theme.NORD['n6']};
            }}
            QPushButton:pressed {{ background-color: {theme.NORD['n9']}; }}
        """)
        copy_btn.clicked.connect(self._copy)
        head_row.addWidget(copy_btn)
        outer.addWidget(header)

        body_row = QWidget(self)
        body_row.setStyleSheet(
            f"background-color: {theme.CODE_PANEL_BG}; "
            f"border: 1px solid {theme.CODE_BORDER}; border-top: none;"
        )
        body_row_layout = QHBoxLayout(body_row)
        # Left/right/bottom margins matching the 1px border width — without
        # them, the gutter and scroll area (both given explicit fixed
        # sizes in _fit_body) filled body_row's *entire* rect, painting
        # straight over where the border should be instead of leaving Qt
        # to inset the layout by the border's own width automatically
        # (which it doesn't reliably do for a widget whose children are
        # fixed-size rather than free to shrink). No top margin: border-
        # top is deliberately none (the header sits flush above it).
        body_row_layout.setContentsMargins(1, 0, 1, 1)
        body_row_layout.setSpacing(0)

        # The line-number gutter lives *outside* the horizontal QScrollArea
        # (a sibling next to it, not inside it) so it stays pinned in place
        # while a long line scrolls underneath it — the way line numbers
        # behave in most editors — rather than scrolling away with the code.
        line_count = source.count("\n") + 1
        self._gutter = _GutterBrowser(body_row)
        self._gutter.setReadOnly(True)
        self._gutter.setFrameShape(QFrame.Shape.NoFrame)
        self._gutter.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._gutter.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._gutter.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        # Same stylesheet/element as the body's <pre class="code-body"> (see
        # below) — a plain <div> here drifted out of sync with the body's
        # per-line height over many lines (different default line-height for
        # different block elements in Qt's rich text engine), so a long
        # block's numbers fell short of its last lines despite both being
        # set to the same pixel height in _fit_body.
        self._gutter.document().setDefaultStyleSheet(theme.pygments_stylesheet(font_family))
        self._gutter.document().setDocumentMargin(theme.CODE_BODY_PADDING)
        self._gutter.setStyleSheet(
            f"background-color: {theme.CODE_PANEL_BG}; "
            f"border: none; border-right: 1px solid {theme.CODE_BORDER}; "
            # Overrides window_stylesheet()'s global `QTextBrowser { border-
            # radius: 6px }` — with only one border edge actually drawn, the
            # inherited radius rounded that edge's top/bottom ends into a
            # small stray notch instead of a clean straight line.
            f"border-radius: 0;"
        )
        numbers = "<br />".join(str(i) for i in range(1, line_count + 1))
        self._gutter.setHtml(
            f'<pre class="code-body" style="text-align:right; '
            f'color:{theme.CODE_BORDER};">{numbers}</pre>'
        )
        body_row_layout.addWidget(self._gutter)

        self._scroll = _CodeScrollArea(body_row)
        self._scroll.setWidgetResizable(False)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setMinimumWidth(0)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {theme.CODE_PANEL_BG}; border: none; }}"
        )

        self._body = _CodeBodyBrowser(self._scroll)
        self._body.setReadOnly(True)
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._body.document().setDefaultStyleSheet(theme.pygments_stylesheet(font_family))
        self._body.document().setDocumentMargin(theme.CODE_BODY_PADDING)
        self._body.setStyleSheet(
            f"background-color: {theme.CODE_PANEL_BG}; border: none; border-radius: 0;"
        )
        self._body.setHtml(f'<pre class="code-body">{highlighted_html}</pre>')
        self._scroll.setWidget(self._body)
        body_row_layout.addWidget(self._scroll)
        outer.addWidget(body_row)

        self._fit_body()
        # Needs the body's document layout already computed (see _fit_body's
        # doc.setTextWidth call just above) — a QTextBlock's layout().lineAt
        # returns nothing useful before the document has been laid out once.
        self._body.set_guides(source, theme.CODE_GUIDE_COLOR)

    def _fit_body(self) -> None:
        # The body's natural (unwrapped) size, fixed on the widget itself —
        # the surrounding QScrollArea is free to be narrower than that and
        # show a horizontal scrollbar instead, without ever having to grow.
        doc = self._body.document()
        doc.setTextWidth(-1)
        width = max(1, int(doc.idealWidth()) + 4)
        height = max(1, int(doc.size().height()) + 4)
        self._body.setFixedSize(width, height)
        self._scroll.setFixedHeight(height + 2)
        # The gutter's own line height must match the body's exactly (same
        # font, same document margin) for the numbers to line up — its
        # height is just copied from the just-computed body height rather
        # than measured independently, guaranteeing they match to the pixel.
        gutter_doc = self._gutter.document()
        gutter_doc.setTextWidth(-1)
        gutter_width = max(1, int(gutter_doc.idealWidth()) + 4)
        self._gutter.setFixedSize(gutter_width, height + 2)

    def _copy(self) -> None:
        _copy_to_clipboard(self._source)
        QToolTip.showText(QCursor.pos(), tr("Copied"), self)


class _StreamingBrowser(QWidget):
    """Composite conversation view: a vertically scrolling column of one
    widget per prose/code block (see theme.split_markdown_blocks), plus one
    trailing "live" widget the current turn streams into.

    Rendering used to be a single QTextBrowser/QTextDocument for the whole
    conversation, with fenced code as HTML tables inside it — simpler, but
    it meant exactly one shared horizontal scrollbar for everything: a code
    block with an unwrappable long line forced the whole document, and from
    there the window itself, wider to fit it (a table cell's width is a
    hint, not a hard cap). Real per-block widgets fix that at the root: the
    outer QScrollArea below is resizable-width (its content always matches
    the viewport's width, never the other way around), a fenced code block
    gets its own independent horizontal QScrollArea instead of sharing the
    document's, and long prose just word-wraps like it always did.

    While a response streams in, text is appended as plain text through a
    cursor placed at the end of the live widget's document, instead of
    re-rendering (markdown + pygments highlighting) on every chunk — that
    fought any text selection the user was making and made the scrollbar
    visibly jump even while parked at the bottom. Markdown formatting is
    applied once, when the response is complete, at which point the live
    widget is cleared and its content reappears as ordinary block widgets.

    Two small animations mark the two waiting states: an animated "Thinking…"
    ellipsis before the first chunk arrives, and a blinking caret at the
    write position once text is streaming in — both edit only the last few
    characters of the live widget, so they're as selection-safe as the
    chunks themselves.

    An optional `prefix` (set via begin_stream/reset) is rendered ahead of
    the streamed text — dialog mode uses it to keep earlier turns of the
    conversation visible while the newest answer streams in below them."""

    contentChanged = Signal()  # natural (unconstrained) height may have changed
    attachmentClicked = Signal(str)  # ATTACHMENT_LINK_SCHEME link clicked; carries its key

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = ""
        self._prefix = ""
        self._committed_markdown = ""
        self._last_previewed_len = 0  # see _render_live_preview
        self._thinking_base = ""
        self._thinking_shown_len = 0
        self._thinking_frame = 0
        self._cursor_shown = False
        self._style_sheet = ""
        self._colors: dict[str, str] = {}
        self._block_widgets: list[QWidget] = []
        # The one direct child of each capped row that actually needs a
        # fixed pixel width (see _wrap_capped/resizeEvent) — either a block
        # widget itself, or its content_bg "card" wrapper if one exists.
        self._capped_widgets: list[QWidget] = []
        # The live streaming widget's own capped wrapper (see _rewrap_live)
        # — tracked separately from _capped_widgets since it isn't torn
        # down and rebuilt by _rebuild_blocks the way committed blocks are.
        self._live_row: QWidget | None = None
        self._live_capped: QWidget | None = None
        # 0 = full width; set by the owning window from the
        # "max_content_width" setting (Obsidian-style readable line length).
        self.content_max_width = 0
        # Fill colour for the capped text/code column itself, set by the
        # owning window from theme.card_colors()["content_bg"].
        self.content_bg = ""
        # "" = built-in monospace fallback stack; set by the owning window
        # from the "code_font_family" setting (Settings → General).
        self.code_font_family = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("background: transparent; border: none;")
        outer.addWidget(self._scroll)

        # What the scroll area actually stretches to fill the viewport
        # (both dimensions, via setWidgetResizable) is this *outer*
        # wrapper — transparent, so stretching it never paints anything.
        # self._content, the real message column, sits inside it at its
        # own natural height (Fixed vertical size policy: the wrapper's
        # layout sizes it to sizeHint and won't stretch it further),
        # pinned to the top by scroll_content_layout's trailing stretch.
        #
        # An earlier version set self._content directly as the scroll
        # area's widget and relied on manual resize() calls (mirroring
        # contentChanged) to keep it at its natural height instead of
        # Qt's own stretch. That fought Qt's own scroll-range bookkeeping
        # in ways that didn't reliably settle during streaming — the
        # scrollbar could end up pinned past the real end of the content,
        # into blank space, and never correct itself. Letting Qt's own
        # (already trusted, for width) widgetResizable machinery own the
        # stretching — just aimed at a transparent wrapper instead of the
        # coloured column — sidesteps that class of bug entirely instead
        # of chasing it.
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        scroll_content_layout = QVBoxLayout(scroll_content)
        scroll_content_layout.setContentsMargins(0, 0, 0, 0)
        scroll_content_layout.setSpacing(0)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        scroll_content_layout.addWidget(self._content)

        # The empty-dialog placeholder (see reset()'s bare=): unlike a
        # committed block, it isn't a capped, top-pinned card — it's meant
        # to fill the *entire* panel (Expanding, both directions, so it
        # takes all the space self._content isn't using) and center its
        # icon/text within that, reading as an empty-state illustration
        # rather than a message. Built once here and just shown/hidden by
        # _set_empty_state, not torn down and rebuilt like committed blocks.
        self._empty_state = QWidget()
        self._empty_state.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.addStretch(1)
        self._empty_icon = QLabel()
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(self._empty_icon)
        self._empty_text = QLabel(tr("Kar-kar…"))
        self._empty_text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(self._empty_text)
        empty_layout.addStretch(1)
        self._empty_state.setVisible(False)
        # A big stretch factor, not the implied one from its Expanding size
        # policy alone: box layouts split *surplus* space by explicit
        # stretch factor first — the trailing addStretch(1) right below,
        # with its own explicit factor of 1, otherwise claimed all of it
        # ahead of this widget's policy-implied (but factor-0) claim,
        # leaving the icon/text sized to their own minimum and stuck near
        # the top instead of centered in the panel.
        scroll_content_layout.addWidget(self._empty_state, 1000)

        scroll_content_layout.addStretch(1)
        self._scroll.setWidget(scroll_content)

        # Links are handled entirely by hand instead of Qt's default
        # navigation, since a rendered answer can contain both real
        # hyperlinks (open externally) and an inline `code` span's
        # click-to-copy link (theme.COPY_LINK_SCHEME).
        self._live = _ProseBrowser(self._content)
        self._live.anchorClicked.connect(self._on_live_anchor_clicked)
        self._live.heightChanged.connect(self.contentChanged)
        # content_max_width/content_bg aren't set yet at this point (the
        # owning window assigns them right after constructing this widget)
        # — this initial wrap is a no-op (uncapped), correctly redone once
        # they're known, from set_colors().
        self._rewrap_live()

        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(_THINKING_TICK_MS)
        self._thinking_timer.timeout.connect(self._tick_thinking)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(_CURSOR_BLINK_MS)
        self._blink_timer.timeout.connect(self._tick_blink)

        # Periodically re-renders the still-arriving answer as real
        # markdown instead of leaving it as plain streamed text until the
        # turn completes — see _render_live_preview.
        self._live_format_timer = QTimer(self)
        self._live_format_timer.setInterval(_LIVE_FORMAT_MS)
        self._live_format_timer.timeout.connect(self._render_live_preview)

    # -- public API -------------------------------------------------------

    def set_colors(self, colors: dict[str, str]) -> None:
        self._colors = colors
        code_font = theme.effective_code_font(self.code_font_family)
        self._style_sheet = theme.response_stylesheet(colors, code_font)
        self._live.document().setDefaultStyleSheet(self._style_sheet)
        # The strip either side of the readable-width column: same shade as
        # the session list panel (field_bg — QListWidget is styled with it
        # in window_stylesheet), so this view reads as its own panel rather
        # than bare page background. On _scroll itself, not _content: a
        # short conversation should still read as a *full*, complete panel
        # (matching the session list next to it) rather than looking cut
        # off partway down — but _scroll's own background paints its full
        # allotted rect regardless of its scrollable content's height,
        # while the scrollable *range* is still governed entirely by
        # scroll_content/_content's actual (natural, unstretched — see
        # __init__) size. Unlike an earlier version that painted this
        # same colour on _content directly: that widget WAS the scrolled
        # widget then (widgetResizable(True) on _scroll stretched it to
        # fill the viewport), so its background was reachable by actually
        # scrolling into empty space. It no longer is, so repainting the
        # backdrop here is safe again.
        self._scroll.setStyleSheet(f"background-color: {colors['field_bg']}; border: none;")
        self._content.setStyleSheet("background: transparent;")
        self._empty_state.setStyleSheet(f"background-color: {colors['field_bg']};")
        self._empty_icon.setPixmap(theme.make_tray_icon(colors["muted"]).pixmap(64, 64))
        self._empty_text.setStyleSheet(
            f"color: {colors['muted']}; font-style: italic; font-size: 15px;")
        self._rewrap_live()
        self._rebuild_blocks(self._committed_markdown)

    def _rewrap_live(self) -> None:
        """(Re)wraps the streaming widget in the same readable-width column
        as committed blocks — content_max_width/content_bg aren't known yet
        when __init__ first calls this (see there), and can change later via
        Settings, so this needs to be redone, not just done once.

        Without this, the "Thinking…"/streamed text sat un-capped at the
        content area's full width while streaming, in a different column
        than every other (committed) block — visually landing somewhere
        else entirely once the turn finished and _render_final() rebuilt it
        as an ordinary, capped block, which read as it "jumping" down/over
        rather than just continuing where it was."""
        was_visible = self._live_row is not None and self._live_row.isVisible()
        if self._live_row is not None:
            self._content_layout.removeWidget(self._live_row)
            if self._live_row is not self._live:
                self._live_row.setParent(None)
                self._live_row.deleteLater()
        self._live_row, self._live_capped = self._wrap_capped(self._live)
        # Inserted right after the last committed block (index
        # len(self._block_widgets)), not appended — appending would land it
        # after the trailing stretch instead of before it.
        self._content_layout.insertWidget(len(self._block_widgets), self._live_row)
        self._live_row.setVisible(was_visible)

    def begin_stream(self, placeholder: str, prefix: str = "") -> None:
        self._set_empty_state(False)
        self._prefix = prefix
        self._buffer = ""
        self._last_previewed_len = 0
        self._cursor_shown = False
        self._rebuild_blocks(prefix)
        self._live.clear()
        self._live_row.setVisible(True)
        # Reparenting an already-visible widget into a new layout (see
        # _rewrap_live) doesn't make Qt re-lay it out on its own — it just
        # keeps whatever size it had in its old parent until something
        # forces a fresh layout pass. Confirmed empirically: neither the new
        # parent's layout().activate() nor updateGeometry() does it, but a
        # hide/show cycle on the widget itself reliably does. Without this,
        # the live widget stayed stuck at its stale (tiny, pre-reparent)
        # width forever, wrapping "Thinking…"/the streamed answer into a
        # sliver a few characters wide instead of the full readable column —
        # reading as if it simply wasn't there.
        self._live.hide()
        self._live.show()
        # A fresh turn should immediately show where the answer streams in —
        # see _defer_scroll_to_bottom for why this needs more than one
        # deferred correction.
        QTimer.singleShot(0, self._defer_scroll_to_bottom)
        self._thinking_base = placeholder
        self._thinking_shown_len = 0
        self._thinking_frame = 0
        self._tick_thinking()
        self._thinking_timer.start()
        self._live_format_timer.start()

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

    def _render_live_preview(self) -> None:
        """Periodically re-renders the answer streamed in *so far* as real
        markdown, instead of only ever formatting it once the whole turn
        completes — the still-arriving tail keeps streaming in as plain
        text between ticks exactly as before, just periodically reabsorbed
        into proper formatting. Reuses _rebuild_blocks — the same, already
        scroll-safe machinery _render_final uses for the true final render
        — rather than a bespoke incremental-append path; the cost of
        redoing the whole markdown+pygments parse on every tick is
        negligible for realistic answer lengths, and every already-solved
        settling/scroll-jump edge case stays solved for free.

        A fenced code block only ever gets syntax-highlighted once its
        closing ``` has actually arrived (nothing short of that tells
        theme.split_markdown_blocks it's finished) — until then it still
        reads as a plain paragraph with visible backticks, same as today.

        Skips the render entirely if nothing new arrived since the last
        tick (the provider can pause between chunks, e.g. mid tool-use in
        the Claude Code CLI) — no point paying for an identical rebuild."""
        if not self._buffer.strip() or len(self._buffer) == self._last_previewed_len:
            return
        self._last_previewed_len = len(self._buffer)

        stick_to_bottom = self._is_stuck_to_bottom()
        pos = self._scroll.verticalScrollBar().value()
        was_cursor_shown = self._cursor_shown
        # Never bake the blinking caret glyph into committed markdown —
        # same reasoning as append_chunk hiding it before mutating _buffer.
        self._set_cursor_visible(False)

        self._rebuild_blocks(self._prefix + self._buffer)

        # Everything up to now is real block widgets again; the live
        # widget goes back to empty, ready for the next batch of chunks.
        self._live.clear()
        self._live_row.setVisible(True)
        self._thinking_shown_len = 0
        if was_cursor_shown:
            self._set_cursor_visible(True)

        if stick_to_bottom:
            QTimer.singleShot(0, self._defer_scroll_to_bottom)
        else:
            QTimer.singleShot(0, lambda: self._restore_scroll_position(pos))

    def finish(self, full_text: str) -> None:
        self._stop_animations()
        self._buffer = full_text
        self._render_final(full_text or tr("*(empty response)*"))

    def fail(self, message: str) -> None:
        self._stop_animations()
        self._render_final(f"**{tr('Error')}**\n\n{message}")

    def reset(self, placeholder: str = "", *, bare: bool = False) -> None:
        """Clears any streamed content and prefix — used to start a fresh
        dialog without the leftover transcript of the previous one.

        `bare`: shows the empty-dialog illustration (raven + "Kar-kar…",
        see _set_empty_state) instead of rendering `placeholder` as an
        ordinary message — a card just repeating "Dialog mode…" under a
        toolbar label already saying almost the same thing read as a
        redundant, oddly-isolated panel rather than an actual empty state."""
        self._stop_animations()
        self._prefix = ""
        self._buffer = ""
        self._live.clear()
        self._live_row.setVisible(False)
        self._set_empty_state(bare)
        self._rebuild_blocks("" if bare else placeholder)

    def _set_empty_state(self, active: bool) -> None:
        self._empty_state.setVisible(active)

    def text_content(self) -> str:
        return self._buffer

    def natural_height(self) -> int:
        """Unconstrained content height — the popup uses this to grow/shrink
        its own window height to fit, up to its configured max."""
        return self._content.sizeHint().height()

    # -- internals ------------------------------------------------------------

    def _rebuild_blocks(self, markdown_text: str) -> None:
        self._committed_markdown = markdown_text
        for widget in self._block_widgets:
            self._content_layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._block_widgets = []
        self._capped_widgets = []
        if markdown_text.strip():
            code_font = theme.effective_code_font(self.code_font_family)
            blocks, inline_sources = theme.split_markdown_blocks(
                markdown_text, self._colors, code_font)
            for i, block in enumerate(blocks):
                inner = self._make_block_widget(block, inline_sources)
                # No gap right before a code block (isinstance below) or
                # right after one — its own header panel already reads as a
                # clear break, so the usual paragraph gap there just looked
                # like a stray blank line; the gap on a code block's *other*
                # side (towards a preceding/following prose block) is kept.
                top = 0 if isinstance(block, theme.CodeBlock) else None
                bottom = 0 if i + 1 < len(blocks) and isinstance(blocks[i + 1], theme.CodeBlock) \
                    else None
                row, capped = self._wrap_capped(inner, top=top, bottom=bottom)
                self._content_layout.insertWidget(i, row)
                self._block_widgets.append(row)
                self._capped_widgets.append(capped)
        # The live widget isn't rebuilt here (only committed blocks are),
        # but this is also the most current, reliably-settled measurement
        # of the available width — resizeEvent alone can miss updates when
        # the viewport's width changes because a vertical scrollbar toggled
        # on/off as content height changed, since that isn't a resize of
        # this widget's own geometry and so doesn't fire resizeEvent here.
        if self._live_capped is not None:
            self._apply_capped_width(self._live_capped)
        self.contentChanged.emit()
        # Freshly inserted widgets haven't been through their first real
        # layout pass yet — an auto-height prose/code widget only reaches
        # its true size once Qt actually lays it out, one event loop turn
        # from now. Without this, the popup's auto-height (natural_height)
        # could settle one step early, on a still-too-small-then-too-tall
        # intermediate reading instead of the final one.
        QTimer.singleShot(0, self._emit_content_changed_if_alive)
        # Same reasoning, for width rather than height: _rebuild_blocks can
        # run before the window has ever been shown (e.g. the empty-dialog
        # placeholder, built while the panel/splitter hasn't settled its
        # real size yet) — measuring self._scroll.viewport().width() *now*
        # can catch a too-small, transient value, and nothing else ever
        # revisits it afterwards for a dialog that's never touched again:
        # resizeEvent doesn't fire for a viewport-only width change with no
        # matching change in this widget's own outer geometry (the same
        # quirk noted above), and _rebuild_blocks itself only reruns on the
        # next message/theme change. Re-measuring a few turns later once
        # things have actually settled catches that case.
        self._defer_resettle_widths()

    def _emit_content_changed_if_alive(self) -> None:
        if shiboken6.isValid(self):
            self.contentChanged.emit()

    def _defer_resettle_widths(self, passes: int = 4) -> None:
        if not shiboken6.isValid(self):
            return
        for widget in (*self._capped_widgets, self._live_capped):
            if widget is not None and shiboken6.isValid(widget):
                self._apply_capped_width(widget)
        if passes > 1:
            QTimer.singleShot(0, lambda: self._defer_resettle_widths(passes - 1))

    def _make_block_widget(self, block, inline_sources: list[str]) -> QWidget:
        if isinstance(block, theme.CodeBlock):
            code_font = theme.effective_code_font(self.code_font_family)
            return _CodeBlockWidget(block.label, block.source, block.highlighted_html, code_font)
        browser = _ProseBrowser()
        browser.document().setDefaultStyleSheet(self._style_sheet)
        browser.setHtml(block.html)
        browser.anchorClicked.connect(
            lambda url, sources=inline_sources: self._on_copy_link(url, sources))
        browser.heightChanged.connect(self.contentChanged)
        return browser

    def _wrap_capped(
        self, widget: QWidget, top: int | None = None, bottom: int | None = None,
    ) -> tuple[QWidget, QWidget]:
        """Caps a block's width and centers it (Obsidian's "readable line
        length") via a plain widget layout instead of an HTML table.
        `content_bg`, if set, gives the capped column its own recessed
        "card" background, distinct from the page/margin either side of it
        and from the code block's own (always darker) panel.

        `top`/`bottom` override the usual CONTENT_PADDING on those two edges
        — used by _rebuild_blocks to zero out the gap right before/after a
        fenced code block (its own header/body already reads as a distinct
        panel, so the extra blank paragraph either side just looked like a
        stray line break) without affecting every other block's spacing.

        Returns (row_to_insert, capped_widget) — the row is what goes in
        the layout, the capped widget is what resizeEvent needs to keep
        resizing; callers track the latter themselves (in self._capped_widgets
        for committed blocks, self._live_capped for the streaming widget —
        kept separate since only the block list is torn down and rebuilt on
        every render).

        The centered item's width is set explicitly (see resizeEvent),
        rather than left to stretch factors competing with the two flanking
        spacers: with equal stretch factors, Qt's box layout just splits the
        row's *total* width three ways and clamps to maximumWidth only if
        that equal share would exceed it — so on a wide window the column
        came out far short of the actual configured cap instead of growing
        to fill it. An explicit setFixedWidth(), recomputed on every
        resize, is unambiguous — and cheap enough to redo on every resize
        tick without a debounce, since it's just a layout property, not a
        markdown/pygments re-render."""
        if not self.content_max_width:
            return widget, widget
        if top is None:
            top = theme.CONTENT_PADDING
        if bottom is None:
            bottom = theme.CONTENT_PADDING
        row = QWidget()
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addStretch(1)
        # A padded wrapper regardless of content_bg — the breathing room
        # around the edge is wanted either way; only the background colour
        # (a plain, uniform page background when content_bg is unset, same
        # as the margin either side) is optional.
        card = QWidget()
        if self.content_bg:
            # No radius: adjacent blocks (e.g. the prose card right above a
            # code block, once the gap between them is zeroed — see top/
            # bottom above) are meant to read as one continuous surface.
            # Rounding each one individually broke that — two independently
            # rounded corners meeting with no gap between them show a
            # stray sliver of the page background peeking through instead
            # of a seamless join.
            card.setStyleSheet(f"background-color: {self.content_bg};")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(theme.CONTENT_PADDING, top, theme.CONTENT_PADDING, bottom)
        card_layout.addWidget(widget)
        row_layout.addWidget(card)
        row_layout.addStretch(1)
        self._apply_capped_width(card)
        return row, card

    def _apply_capped_width(self, widget: QWidget) -> None:
        available = self._scroll.viewport().width()
        if available > 0:
            widget.setFixedWidth(min(self.content_max_width, available))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        for widget in (*self._capped_widgets, self._live_capped):
            if widget is not None and shiboken6.isValid(widget):
                self._apply_capped_width(widget)
        self._update_scroll_mask()

    def _update_scroll_mask(self) -> None:
        """Rounds the conversation panel's own outer corners (matching the
        input box / session list elsewhere in the window). QSS border-
        radius alone doesn't do it here: the scroll area's viewport and
        the content widget inside it both paint their own square-cornered
        background right up to the same edges, so a CSS radius on the
        scroll area itself would just leave their square corners poking
        out past the curve. A widget mask clips *everything* painted
        inside it — content included — so the corners actually come out
        rounded regardless of what's drawn underneath."""
        path = QPainterPath()
        path.addRoundedRect(QRectF(self._scroll.rect()), 6, 6)
        self._scroll.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _on_live_anchor_clicked(self, url) -> None:
        # The live widget streams plain text only (no inline-code links or
        # attachment markers can exist in it yet — those only ever appear
        # in already-committed history), but a real hyperlink pasted as
        # literal text could still be auto-detected — keep hand-off
        # consistent with the committed block widgets.
        if url.scheme() == theme.ATTACHMENT_LINK_SCHEME:
            self.attachmentClicked.emit(url.path())
        elif url.scheme() != theme.COPY_LINK_SCHEME:
            QDesktopServices.openUrl(url)

    def _on_copy_link(self, url, sources: list[str]) -> None:
        if url.scheme() == theme.COPY_LINK_SCHEME:
            try:
                index = int(url.path())
            except ValueError:
                return
            if 0 <= index < len(sources):
                _copy_to_clipboard(sources[index])
                QToolTip.showText(QCursor.pos(), tr("Copied"), self)
        elif url.scheme() == theme.ATTACHMENT_LINK_SCHEME:
            # The key (e.g. "3-0") is resolved back to an Attachment by
            # whoever owns this browser (MainWindow) — this generic widget
            # has no notion of "attachments" of its own.
            self.attachmentClicked.emit(url.path())
        else:
            QDesktopServices.openUrl(url)

    def _stop_animations(self) -> None:
        self._thinking_timer.stop()
        self._blink_timer.stop()
        self._live_format_timer.stop()
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
        scrollbar = self._scroll.verticalScrollBar()
        return scrollbar.value() >= scrollbar.maximum() - 2

    def _scroll_to_bottom(self) -> None:
        # Guards a deferred QTimer.singleShot(0, ...) call (see begin_stream/
        # _render_final) against firing after the widget — e.g. a popup
        # closed right on the heels of finish() — has already been torn
        # down; PySide raises RuntimeError on any further use of it then.
        if not shiboken6.isValid(self):
            return
        scrollbar = self._scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _defer_scroll_to_bottom(self, passes: int = 4, _is_first: bool = True) -> None:
        """Re-pins to the bottom over a few event-loop turns, not just one
        — but only ever *moves* the scrollbar on the first turn (so
        following a new answer feels immediate) and the last one (a
        guaranteed final correction), not on every turn in between.

        A heavier rebuild — several committed blocks, a code block among
        them — can settle its *final* layout over more than one turn:
        measured empirically, the scrollbar's maximum() can land on a
        transient, inflated value on turn 1, grow slightly more on turn 2,
        then only drop to its real, smaller final value on turn 3. An
        earlier version of this method re-snapped to the bottom on *every*
        turn, i.e. to each of those transient values in sequence — which
        did eventually converge on the right position, but along the way
        actually *committed* (setValue, a real repaint) each wrong
        intermediate value, so the view visibly jumped past the real
        content and then jerked back on the very next frame. Skipping the
        middle turns entirely and only committing the first/last reads
        removes those visible round trips; the final commit still reads
        whatever settled value the old code eventually converged on."""
        if not shiboken6.isValid(self):
            return
        if _is_first or passes <= 1:
            self._scroll_to_bottom()
        if passes > 1:
            QTimer.singleShot(0, lambda: self._defer_scroll_to_bottom(passes - 1, False))

    def _append_plain(self, text: str) -> None:
        if not text:
            return
        stick_to_bottom = self._is_stuck_to_bottom()
        # A cursor created here, rather than self._live.textCursor(), never
        # touches whatever selection the user currently has in the widget.
        cursor = QTextCursor(self._live.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        # A fresh, plain QTextCharFormat keeps streamed text from picking up
        # the italic "thinking" styling (or anything else) left at the tail.
        cursor.insertText(text, QTextCharFormat())
        self._live.fit_height()
        if stick_to_bottom:
            self._scroll_to_bottom()

    def _replace_tail(self, old_len: int, new_text: str, italic: bool = False) -> None:
        stick_to_bottom = self._is_stuck_to_bottom()
        cursor = QTextCursor(self._live.document())
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
        self._live.fit_height()
        if stick_to_bottom:
            self._scroll_to_bottom()

    def _render_final(self, markdown_text: str) -> None:
        stick_to_bottom = self._is_stuck_to_bottom()
        pos = self._scroll.verticalScrollBar().value()
        self._live.clear()
        self._live_row.setVisible(False)
        self._rebuild_blocks(self._prefix + markdown_text)
        if stick_to_bottom:
            # See _defer_scroll_to_bottom for why one deferred correction
            # isn't always enough.
            QTimer.singleShot(0, self._defer_scroll_to_bottom)
        else:
            # Not at the bottom: keep the same absolute scroll position
            # rather than snapping wherever the (now taller) rebuilt content
            # happens to leave it, so the user doesn't lose their place.
            QTimer.singleShot(0, lambda: self._restore_scroll_position(pos))

    def _restore_scroll_position(self, pos: int) -> None:
        if not shiboken6.isValid(self):
            return
        scrollbar = self._scroll.verticalScrollBar()
        scrollbar.setValue(min(pos, scrollbar.maximum()))


_RESIZE_MARGIN = 6  # px from an edge that grabs a resize instead of a move

_RESIZE_CURSORS = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
    Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
}


class _DraggableCard(QFrame):
    """Card that can be dragged by the mouse from anywhere that isn't a
    button or the response text (those consume the press themselves) —
    lets the user reposition the frameless popup before closing it. A
    narrow margin along each edge resizes the (frameless, so borderless)
    popup window instead, via the window manager's own native resize
    (Qt.Window.startSystemResize) rather than manual geometry math — the
    same mechanism a bordered window's own edges use, so it gets the
    platform's usual resize cursor/behaviour for free."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_from: QPoint | None = None
        self.setMouseTracking(True)

    def _resize_edges(self, pos: QPoint) -> Qt.Edge:
        edges = Qt.Edge(0)
        if pos.x() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - _RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - _RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._resize_edges(event.position().toPoint())
            window = self.window()
            handle = window.windowHandle()
            if edges and handle is not None and handle.startSystemResize(edges):
                # The window manager owns the drag from here on (it delivers
                # no further move/release events to us) — mark the popup as
                # user-sized so its own auto-height-to-content no longer
                # fights whatever size the user just chose.
                if hasattr(window, "_user_resized"):
                    window._user_resized = True
                event.accept()
                return
            self._drag_from = event.globalPosition().toPoint() - window.pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()
        else:
            edges = self._resize_edges(event.position().toPoint())
            self.setCursor(_RESIZE_CURSORS.get(edges, Qt.CursorShape.ArrowCursor))
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None
        super().mouseReleaseEvent(event)


class PopupWindow(QWidget):
    """Popup near the cursor: rounded corners, selectable Markdown response,
    draggable by the mouse, stays on top of every other window until closed
    with its own ✕ button — losing focus or clicking outside it does
    nothing, unlike a native Qt.Popup."""

    open_in_window = Signal(str, str)  # prompt text, response text -> open in the main window

    RADIUS = 12

    def __init__(self, config: Config, provider_name: str):
        # Qt.Tool (not Qt.Popup): no taskbar entry, but — unlike Popup —
        # Qt doesn't auto-close it on an outside click or a focus loss, so
        # the ✕ button (_animated_close) is the only way to close it.
        # WindowStaysOnTopHint keeps it above every other window, including
        # ones that grab focus while the popup is still open.
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.config = config
        self.worker: AIWorker | None = None
        self._prompt = ""  # set by ask(); kept for "Open in window"
        self.max_height = int(config.get("popup_max_height"))
        # Opens at a fixed size derived from settings (see show_near_cursor
        # -> _adjust_height), but isn't a *fixed-size window*: the card's
        # edges accept a mouse-driven resize (_DraggableCard), which flips
        # _user_resized so the window keeps whatever size the user chose
        # instead of snapping back on the next streamed chunk.
        self._user_resized = False
        self.setMinimumSize(220, 90)
        self.resize(int(config.get("popup_width")), 90)

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
            QPushButton#stopButton {{
                color: {theme.NORD['n11']}; border-color: {theme.NORD['n11']};
            }}
            QPushButton#stopButton:hover, QPushButton#stopButton:pressed {{
                background-color: {theme.NORD['n11']}; color: {theme.NORD['n6']};
                border-color: {theme.NORD['n11']};
            }}
            QPushButton#stopButton:disabled {{
                color: {muted}; border-color: transparent; background: transparent;
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
        self.stop_btn.setObjectName("stopButton")
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
        self.browser.content_max_width = int(config.get("max_content_width"))
        self.browser.content_bg = colors["content_bg"]
        self.browser.code_font_family = str(config.get("code_font_family"))
        self.browser.set_colors(colors)
        self.browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.browser)

        self._fade_anim: QPropertyAnimation | None = None

        # A single content change (a streamed chunk, or a live-preview
        # re-render tick) fires contentChanged several times in a row — the
        # rebuild's own immediate emit, a deferred one a turn later once
        # freshly laid-out widgets report their true height, and every
        # child block's own heightChanged as it settles (see _rebuild_blocks
        # in _StreamingBrowser). Resizing the actual OS window on each of
        # those was visible as the popup repeatedly snapping to a
        # too-small intermediate height and back — debouncing collapses a
        # whole burst into the one resize that matters, at its end.
        self._height_debounce = QTimer(self)
        self._height_debounce.setSingleShot(True)
        self._height_debounce.setInterval(60)
        self._height_debounce.timeout.connect(self._adjust_height)
        self.browser.contentChanged.connect(self._height_debounce.start)

    # -- public API -----------------------------------------------------------

    def ask(self, provider, prompt: str) -> None:
        self._prompt = prompt  # kept for "Open in window", so context isn't lost
        self.browser.begin_stream(tr("Thinking"))
        self.stop_btn.setEnabled(True)
        self.worker = AIWorker(provider, [{"role": "user", "content": prompt}],
                               int(self.config.get("timeout")), int(self.config.get("max_tokens")),
                               web_search=bool(self.config.get("claude_web_search")))
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
        if self._user_resized or not shiboken6.isValid(self):
            return
        content_height = self.browser.natural_height()
        chrome = 64  # header + margins
        height = int(min(self.max_height, max(90, content_height + chrome)))
        self.resize(self.width(), height)

    def _copy_answer(self) -> None:
        _copy_to_clipboard(self.browser.text_content())

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
        # The ✕ button is the only way to close the popup (see __init__)
        # — a quick fade instead of an abrupt disappearance.
        anim = self._fade(self.windowOpacity(), 0.0, 110, QEasingCurve.Type.InCubic)
        anim.finished.connect(self.close)

    def closeEvent(self, event) -> None:
        _stop_worker(self.worker)
        super().closeEvent(event)


def _elide(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[:max_len - 1] + "…"


class _ChatInput(QPlainTextEdit):
    """Plain-text message composer that also accepts image paste
    (Ctrl+V) and drag-and-drop of local files. Both go through the same
    two Qt extension points — canInsertFromMimeData/insertFromMimeData —
    which QPlainTextEdit's own default drag-and-drop handling already
    calls into, so no separate dragEnterEvent/dropEvent overrides are
    needed here. Ordinary text paste/drop/typing is untouched (falls
    through to the base implementation)."""

    imagePasted = Signal(QImage)
    filesDropped = Signal(list)  # list[str] of local file paths

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                self.imagePasted.emit(image)
                return
        if source.hasUrls():
            paths = [u.toLocalFile() for u in source.urls() if u.isLocalFile()]
            if paths:
                self.filesDropped.emit(paths)
                return
        super().insertFromMimeData(source)


class _ImagePreviewDialog(QDialog):
    """Read-only, full(er)-size image preview — opened by clicking an
    image chip (before sending) or an attachment link in the transcript
    (after). A real window, not frameless: its own titlebar/close button
    plus QDialog's default Escape-closes behaviour are enough for a
    one-off viewer, no custom chrome needed."""

    def __init__(self, attachment: attachments.Attachment, parent=None):
        super().__init__(parent)
        self.setWindowTitle(attachment.name)
        pixmap = QPixmap()
        pixmap.loadFromData(base64.b64decode(attachment.data))

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry().size()
            max_size = QSize(int(available.width() * 0.85), int(available.height() * 0.85))
            if pixmap.width() > max_size.width() or pixmap.height() > max_size.height():
                pixmap = pixmap.scaled(
                    max_size, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel()
        label.setPixmap(pixmap)
        layout.addWidget(label)


class _AttachmentChip(QFrame):
    """One removable attachment preview: a thumbnail (images) or a generic
    file glyph, the (elided) filename, and a small ✕ button. Clicking an
    image thumbnail opens it full-size; text files have nothing to open."""

    removeClicked = Signal()

    _SIZE = 40

    def __init__(self, attachment: attachments.Attachment, colors: dict[str, str], parent=None):
        super().__init__(parent)
        self._attachment = attachment
        self.setStyleSheet(
            f"QFrame {{ background-color: {colors['code_bg']}; "
            f"border: 1px solid {colors['border']}; border-radius: 6px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 6, 4)
        layout.setSpacing(6)

        preview = QLabel()
        preview.setFixedSize(self._SIZE, self._SIZE)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setStyleSheet("background: transparent; border: none;")
        if attachment.kind == "image":
            pixmap = QPixmap()
            pixmap.loadFromData(base64.b64decode(attachment.data))
            preview.setPixmap(pixmap.scaled(
                self._SIZE, self._SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            preview.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(tr("Click to preview"))
        else:
            preview.setText("📄")
            preview.setStyleSheet(f"font-size: 20px; color: {colors['muted']};")
        layout.addWidget(preview)

        name = QLabel(_elide(attachment.name, 22))
        name.setToolTip(attachment.name)
        name.setStyleSheet(f"color: {colors['fg']}; background: transparent; border: none;")
        layout.addWidget(name)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedSize(20, 20)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {colors['muted']}; }}"
            f"QPushButton:hover {{ color: {colors['fg']}; }}"
        )
        remove_btn.clicked.connect(self.removeClicked)
        layout.addWidget(remove_btn)

    def mousePressEvent(self, event) -> None:
        # The remove button consumes its own clicks (it's a QPushButton),
        # so this only ever fires for the rest of the chip.
        if self._attachment.kind == "image" and event.button() == Qt.MouseButton.LeftButton:
            _ImagePreviewDialog(self._attachment, self.window()).exec()
            return
        super().mousePressEvent(event)


class _AttachmentTray(QWidget):
    """Horizontal strip of removable attachment chips shown above the
    message input — hidden entirely (takes no layout space) whenever
    there's nothing pending."""

    removed = Signal(int)  # index into the caller's pending-attachments list

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 8)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self.setVisible(False)

    def set_attachments(self, items: list[attachments.Attachment], colors: dict[str, str]) -> None:
        while self._layout.count() > 1:
            widget = self._layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        for i, att in enumerate(items):
            chip = _AttachmentChip(att, colors)
            chip.removeClicked.connect(lambda _checked=False, idx=i: self.removed.emit(idx))
            self._layout.insertWidget(self._layout.count() - 1, chip)
        self.setVisible(bool(items))


_USER_LABEL = "🧑"
_ASSISTANT_LABEL = "🤖"

# A fixed, non-database session slot for quick one-off questions: pinned at
# the top of the session list like any other dialog, but its history lives
# only in MainWindow.quick_history (never written to session.sqlite3, so it
# starts empty again every time kortalk restarts) and each message is sent
# to the provider on its own, without the rest of the session as context —
# see send_chat(). Negative so it can never collide with a real session's
# autoincrement id (starts at 1).
_QUICK_SESSION_ID = -1


class MainWindow(QMainWindow):
    """Full window: a session list next to a single conversation thread that
    keeps full context (every earlier turn is resent to the provider) —
    except the pinned "Quick questions" entry (_QUICK_SESSION_ID), which
    sends each message on its own and is never persisted to disk."""

    settings_requested = Signal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.worker: AIWorker | None = None
        self._fade_anim: QPropertyAnimation | None = None

        # In-memory only, see _QUICK_SESSION_ID — cleared whenever this
        # MainWindow (and so the daemon) is recreated, i.e. on every restart.
        self.quick_history: list[dict] = []

        # Attachments queued for the *next* message — cleared once it's
        # sent. See _ChatInput (paste/drop) and send_chat().
        self._pending_attachments: list[attachments.Attachment] = []
        self._card_colors: dict[str, str] = {}  # set by _apply_code_style
        self._tray_height_added = 0  # see _grow_splitter_for_tray/_shrink_splitter_for_tray

        # "message_index-attachment_index" -> raw attachment dict, rebuilt
        # by _chat_transcript() on every render — resolves a clicked
        # theme.ATTACHMENT_LINK_SCHEME link back to its image. Ephemeral
        # UI-only state, never persisted.
        self._attachment_by_key: dict[str, dict] = {}

        # Pick up the most recently active dialog, if any — the session
        # list (built below) lets the user switch to a different one.
        self.session_id: int | None = None
        self.chat_history: list[dict] = []
        sessions = session.list_sessions()
        if sessions:
            self.session_id = sessions[0].id
            self.chat_history = session.load_session(self.session_id)

        self.setWindowTitle("Kortalk")
        self.resize(int(config.get("window_width")), int(config.get("window_height")))
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

        self.web_search_check = QCheckBox(" " + tr("Web search"))
        self.web_search_check.setChecked(bool(config.get("claude_web_search")))
        self.web_search_check.setToolTip(
            tr("Claude Code CLI only — has no effect on other providers"))
        self.web_search_check.toggled.connect(self._web_search_toggled)
        toolbar.addWidget(self.web_search_check)

        self.local_commands_check = QCheckBox(" " + tr("Run commands"))
        self.local_commands_check.setChecked(bool(config.get("claude_local_commands")))
        self.local_commands_check.setToolTip(tr(
            "Allows Bash and file edits — Claude Code CLI only, off by default"))
        self.local_commands_check.toggled.connect(self._local_commands_toggled)
        toolbar.addWidget(self.local_commands_check)
        self._update_claude_toggles_enabled()

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        settings_action = QAction(tr("Settings"), self)
        settings_action.triggered.connect(self.settings_requested.emit)
        toolbar.addAction(settings_action)

        self.chat_page = self._build_chat_page()
        self.setCentralWidget(self.chat_page)

        QShortcut(QKeySequence("Ctrl+Return"), self, self.send_chat)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)

        self.statusBar().showMessage(tr("Ready"))

    # -- page construction ------------------------------------------------------

    def _build_chat_page(self) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        page_splitter = _InsetSplitter(Qt.Orientation.Horizontal, trailing_inset=4)
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
        self.dialog_label = QLabel()
        layout.addWidget(self.dialog_label)

        # A vertical splitter (not a fixed-height input box) lets the user
        # drag the divider up when a message needs more room to compose.
        # Kept as self.chat_splitter (not a local var): _refresh_attachment_
        # tray() resizes it when the tray appears/disappears, see there.
        self.chat_splitter = QSplitter(Qt.Orientation.Vertical)
        self.chat_splitter.setHandleWidth(4)

        # chat_browser and the input row are wrapped in their own containers
        # (rather than added to the splitter directly) purely to get a
        # margin on the side facing the divider — a thin handle plus a
        # bit of real breathing room either side of it, not a thicker bar.
        browser_container = QWidget()
        browser_layout = QVBoxLayout(browser_container)
        browser_layout.setContentsMargins(0, 0, 0, 8)
        self.chat_browser = _StreamingBrowser()
        self.chat_browser.attachmentClicked.connect(self._on_attachment_link_clicked)
        # Applied before the first render: set_colors() doesn't
        # retroactively restyle content a document already has, so calling
        # this after _refresh_chat_view() left the initial history unstyled
        # (no code highlighting) until something re-rendered it, e.g.
        # switching to another dialog.
        self._apply_code_style()
        self._refresh_chat_view()
        browser_layout.addWidget(self.chat_browser)
        self.chat_splitter.addWidget(browser_container)

        input_widget = QWidget()
        input_outer = QVBoxLayout(input_widget)
        input_outer.setContentsMargins(0, 8, 0, 0)
        input_outer.setSpacing(0)

        self.attachment_tray = _AttachmentTray()
        self.attachment_tray.removed.connect(self._remove_attachment)
        input_outer.addWidget(self.attachment_tray)

        input_row = QHBoxLayout()
        # Default QHBoxLayout spacing is 0 when it isn't parented to a
        # widget at construction (as here) — without this, the buttons on
        # either side sit flush against chat_input with no breathing room.
        input_row.setSpacing(8)
        self.attach_btn = QPushButton("📎")
        # Same treatment as the API-key show/hide button in Settings: the
        # base QPushButton rule's padding (5px 14px, sized for text labels)
        # otherwise fights a single emoji glyph — #iconButton's tighter
        # padding (theme.window_stylesheet) is the established fix.
        self.attach_btn.setObjectName("iconButton")
        self.attach_btn.setToolTip(tr("Attach files"))
        self.attach_btn.setFixedWidth(40)
        self.attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_btn.clicked.connect(self._open_file_dialog)
        input_row.addWidget(self.attach_btn)
        self.chat_input = _ChatInput()
        self.chat_input.setPlaceholderText(tr("Message… (Ctrl+Enter to send)"))
        self.chat_input.setMinimumHeight(40)
        self.chat_input.imagePasted.connect(self._on_image_pasted)
        self.chat_input.filesDropped.connect(self._on_files_dropped)
        input_row.addWidget(self.chat_input, 1)
        self.chat_send_btn = QPushButton(tr("Send (Ctrl+Enter)"))
        self.chat_send_btn.setObjectName("primaryButton")
        self.chat_send_btn.clicked.connect(self._chat_send_or_stop)
        input_row.addWidget(self.chat_send_btn)
        input_outer.addLayout(input_row)

        self.chat_splitter.addWidget(input_widget)

        self.chat_splitter.setStretchFactor(0, 1)
        self.chat_splitter.setStretchFactor(1, 0)
        self.chat_splitter.setSizes([420, 90])

        layout.addWidget(self.chat_splitter, 1)
        return panel

    # -- dialog mode --------------------------------------------------------------

    def seed_dialog_from_popup(self, prompt: str, answer: str) -> None:
        # "Open in window" always starts a fresh dialog seeded with the
        # popup's prompt/answer, rather than merging into whichever dialog
        # happened to be open — the popup's answer should be visible in the
        # response area, not silently dropped into an unrelated one.
        _stop_worker(self.worker)
        self.session_id = None
        self.chat_history = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ]
        self._persist_dialog()
        self._refresh_chat_view()

    def _chat_transcript(self, pending_answer: bool) -> str:
        turns = []
        self._attachment_by_key = {}
        for m_idx, m in enumerate(self.chat_history):
            if m["role"] == "user":
                label, content = f"{_USER_LABEL} {tr('You')}", theme.escape_user_text(m["content"])
            else:
                label, content = f"{_ASSISTANT_LABEL} {tr('Assistant')}", m["content"]
            for a_idx, att in enumerate(m.get("attachments") or []):
                # A link (images) or plain marker (files) — never an inline
                # thumbnail/embed, which would need markdown-image
                # rendering for local/base64 sources. Clicking an image
                # link opens _ImagePreviewDialog via _StreamingBrowser's
                # generic ATTACHMENT_LINK_SCHEME handling — see
                # _on_attachment_link_clicked.
                name = theme.escape_user_text(att["name"])
                if att["kind"] == "image":
                    key = f"{m_idx}-{a_idx}"
                    self._attachment_by_key[key] = att
                    content += f"\n\n[📎 *{name}*]({theme.ATTACHMENT_LINK_SCHEME}:{key})"
                else:
                    content += f"\n\n📎 *{name}*"
            turns.append(f"**{label}:**\n\n{content}")
        md = "\n\n---\n\n".join(turns)
        if pending_answer:
            # The streamed "Thinking…"/answer text is appended straight after
            # this prefix by cursor, not re-rendered as markdown — a trailing
            # "\n\n" here doesn't survive into an actual blank paragraph (markdown
            # drops trailing blank lines), so it landed glued to the label
            # ("Assistant:Thinking…") with no space at all.
            md += "\n\n---\n\n" + f"**{_ASSISTANT_LABEL} {tr('Assistant')}:** "
        return md

    def _refresh_chat_view(self) -> None:
        self._update_dialog_label()
        if not self.chat_history:
            self.chat_browser.reset(bare=True)
        else:
            self.chat_browser.reset(self._chat_transcript(pending_answer=False))

    def _update_dialog_label(self) -> None:
        text = (tr("Quick questions — context is not kept between messages")
                if self.session_id == _QUICK_SESSION_ID
                else tr("Dialog — context is kept between messages"))
        self.dialog_label.setText(text)

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

        # Pinned first row, always present, styled distinctly (bold, amber)
        # so it doesn't get mistaken for a saved dialog — see
        # _QUICK_SESSION_ID.
        quick_item = QListWidgetItem("⚡ " + tr("Quick questions"))
        quick_item.setData(Qt.ItemDataRole.UserRole, _QUICK_SESSION_ID)
        quick_font = quick_item.font()
        quick_font.setBold(True)
        quick_item.setFont(quick_font)
        quick_item.setForeground(QColor(theme.NORD["n13"]))
        quick_item.setToolTip(
            tr("Not saved between restarts — cleared when kortalk quits."))
        self.session_list.addItem(quick_item)
        if self.session_id == _QUICK_SESSION_ID:
            selected_row = 0

        for i, meta in enumerate(session.list_sessions(), start=1):
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
        self.chat_history = (
            self.quick_history if session_id == _QUICK_SESSION_ID
            else session.load_session(session_id)
        )
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
        if self.session_id == _QUICK_SESSION_ID:
            return  # in-memory only — never written to session.sqlite3
        if self.session_id is None:
            self.session_id = session.create_session(
                self._derive_session_title(), self.chat_history)
        else:
            session.save_session(self.session_id, self.chat_history)
        self._reload_session_list()

    def _delete_dialog(self) -> None:
        if self.session_id is None:
            return  # an unsaved new dialog — nothing to delete yet
        if self.session_id == _QUICK_SESSION_ID:
            confirmed = QMessageBox.question(
                self, tr("Clear quick questions"),
                tr("Clear this session's history? This cannot be undone."),
            ) == QMessageBox.StandardButton.Yes
            if not confirmed:
                return
            _stop_worker(self.worker)
            self.quick_history.clear()  # self.chat_history is the same list
            self.chat_input.clear()
            self._set_chat_sending(False)
            self._refresh_chat_view()
            self.statusBar().showMessage(tr("Quick questions cleared"))
            return
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
        if not text and not self._pending_attachments:
            return
        _stop_worker(self.worker)

        provider = self.config.provider(self.provider_combo.currentData())
        if provider is None:
            self.statusBar().showMessage(tr("Provider not found — check settings"))
            return

        user_message = {"role": "user", "content": text}
        if self._pending_attachments:
            user_message["attachments"] = [asdict(a) for a in self._pending_attachments]
            self._pending_attachments = []
            self._refresh_attachment_tray()
        self.chat_history.append(user_message)
        self._persist_dialog()
        self.chat_input.clear()
        self._set_chat_sending(True)
        self.statusBar().showMessage(tr("Requesting {name}…").format(name=provider.name))
        self.chat_browser.begin_stream(
            tr("Thinking"), prefix=self._chat_transcript(pending_answer=True))

        # Every other session resends the whole conversation as context;
        # "Quick questions" deliberately doesn't (see _QUICK_SESSION_ID) —
        # only the message just appended goes to the provider, even though
        # the transcript above still shows every earlier turn.
        request_messages = (
            [self.chat_history[-1]] if self.session_id == _QUICK_SESSION_ID
            else list(self.chat_history)
        )
        self.worker = AIWorker(provider, request_messages, int(self.config.get("timeout")),
                               int(self.config.get("max_tokens")),
                               web_search=self.web_search_check.isChecked(),
                               local_commands=self.local_commands_check.isChecked())
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
        self._card_colors = colors
        self.chat_browser.content_max_width = int(self.config.get("max_content_width"))
        self.chat_browser.content_bg = colors["content_bg"]
        self.chat_browser.code_font_family = str(self.config.get("code_font_family"))
        self.chat_browser.set_colors(colors)
        if hasattr(self, "attachment_tray"):  # not built yet on the first call
            self._refresh_attachment_tray()

    # -- attachments ------------------------------------------------------------

    def _on_image_pasted(self, image: QImage) -> None:
        name = f"pasted-{len(self._pending_attachments) + 1}.png"
        self._pending_attachments.append(attachments.attachment_from_qimage(image, name))
        self._refresh_attachment_tray()

    def _on_files_dropped(self, paths: list[str]) -> None:
        errors = []
        for raw_path in paths:
            attachment, error = attachments.classify_and_read(Path(raw_path))
            if attachment is not None:
                self._pending_attachments.append(attachment)
            else:
                errors.append(error)
        if errors:
            self.statusBar().showMessage(" · ".join(errors))
        self._refresh_attachment_tray()

    def _refresh_attachment_tray(self) -> None:
        was_hidden = self.attachment_tray.isHidden()
        self.attachment_tray.set_attachments(self._pending_attachments, self._card_colors)
        is_hidden = self.attachment_tray.isHidden()
        # Only ever fires on an actual empty<->non-empty *transition* —
        # repeated refreshes with the same emptiness (e.g. a theme change
        # while attachments are pending) must not keep resizing things.
        if was_hidden and not is_hidden:
            # Deferred: chips were *just* added above, and sizeHint() only
            # reports their real height once Qt has actually laid them out
            # — same reasoning as _StreamingBrowser's own deferred
            # remeasurements elsewhere in this file.
            QTimer.singleShot(0, self._grow_splitter_for_tray)
        elif not was_hidden and is_hidden:
            self._shrink_splitter_for_tray()

    def _grow_splitter_for_tray(self) -> None:
        # The tray sits *inside* the input pane's own fixed-size splitter
        # slot (see _build_conversation_panel) — without this, it simply
        # eats into chat_input's share of that fixed height instead of the
        # pane growing, which is exactly the "input box shrinks when you
        # paste an image" bug this works around. Grows the input pane by
        # exactly the tray's own height, taken from the browser pane (which
        # scrolls, so losing a little visible height there is far less
        # disruptive than the box you're actively typing into shrinking).
        if not shiboken6.isValid(self.chat_splitter):
            return
        sizes = self.chat_splitter.sizes()
        if len(sizes) < 2:
            return
        delta = self.attachment_tray.sizeHint().height()
        self._tray_height_added = delta
        sizes[0] -= delta
        sizes[1] += delta
        self.chat_splitter.setSizes(sizes)

    def _shrink_splitter_for_tray(self) -> None:
        # Symmetric with _grow_splitter_for_tray, and uses the exact same
        # stored amount rather than re-measuring sizeHint() now — the tray
        # is already empty again by this point, so its *current* sizeHint
        # no longer reflects the height that was actually added back then.
        if not self._tray_height_added:
            return
        sizes = self.chat_splitter.sizes()
        if len(sizes) >= 2:
            sizes[0] += self._tray_height_added
            sizes[1] -= self._tray_height_added
            self.chat_splitter.setSizes(sizes)
        self._tray_height_added = 0

    def _remove_attachment(self, index: int) -> None:
        if 0 <= index < len(self._pending_attachments):
            del self._pending_attachments[index]
            self._refresh_attachment_tray()

    def _open_file_dialog(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, tr("Attach files"))
        if paths:
            self._on_files_dropped(paths)

    def _on_attachment_link_clicked(self, key: str) -> None:
        raw = self._attachment_by_key.get(key)
        if raw is None or raw.get("kind") != "image":
            return
        _ImagePreviewDialog(attachments.Attachment(**raw), self).exec()

    def reload_providers(self) -> None:
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        active_id = str(self.config.get("active_provider"))
        for i, p in enumerate(self.config.providers()):
            self.provider_combo.addItem(p.name, p.id)
            if p.id == active_id:
                self.provider_combo.setCurrentIndex(i)
        self.provider_combo.blockSignals(False)

    def _provider_changed(self) -> None:
        pid = self.provider_combo.currentData()
        if pid:
            self.config.set("active_provider", pid)
            self.config.sync()
        self._update_claude_toggles_enabled()

    def _update_claude_toggles_enabled(self) -> None:
        provider = self.config.provider(self.provider_combo.currentData())
        enabled = provider is not None and provider.type == "claude-cli"
        self.web_search_check.setEnabled(enabled)
        self.local_commands_check.setEnabled(enabled)

    def _web_search_toggled(self, checked: bool) -> None:
        self.config.set("claude_web_search", checked)

    def _local_commands_toggled(self, checked: bool) -> None:
        self.config.set("claude_local_commands", checked)

    def closeEvent(self, event) -> None:
        # The window closes, the application stays alive in the tray.
        _stop_worker(self.worker)
        self.config.set("window_width", self.width())
        self.config.set("window_height", self.height())
        super().closeEvent(event)
