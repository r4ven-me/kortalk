"""Themes (system / Nord dark / Nord light), fonts and the tray icon."""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from pathlib import Path

import markdown as _markdown
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap, QPolygon
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
        # Inline `code` chips need their own shade, clearly darker than
        # content_bg (below) — they sit right on top of it, and a previous,
        # lighter factor here (125) actually came out *lighter* than
        # content_bg once that was itself lightened, making chips nearly
        # invisible. Not as dark as code_block_bg either, so it still
        # reads as a lighter-weight chip rather than a full code panel.
        # In light mode it must also differ from content_bg (also n5,
        # below) for the same reason — n4 matches code_block_bg's tier.
        "inline_code_bg": QColor(NORD["n1"]).darker(175).name() if dark else NORD["n4"],
        "highlight": NORD["n10"],
        "highlight_text": NORD["n6"],
        # The readable-width column's own background: lighter than
        # CODE_PANEL_BG (the code block's own, fixed-dark panel) so the two
        # don't blend together, but still a touch darker than field_bg (the
        # strip either side of it) so the response still reads as its own
        # surface within that panel.
        "content_bg": QColor(NORD["n00"]).lighter(120).name() if dark else NORD["n5"],
    }


def markdown_content_stylesheet(colors: dict[str, str]) -> str:
    """Markdown rendering shared by every response view (popup, dialog):
    breathing room between paragraphs/headings/lists/code blocks so a
    multi-turn dialog doesn't read as one solid, unbroken wall of text,
    plus a small, subtle chip style for inline `code` spans.

    Fenced code blocks never reach the bare `pre` rule below — they're
    rendered as a `.code-hl` card instead (see pygments_stylesheet) — so
    this only has to cover the rare indented-code-block fallback. It
    deliberately has no border-radius/margin: Qt splits a multi-line `<pre>`
    into one QTextBlock per line, and per-block radius/margin there would
    paint a separate rounded pill per line instead of a single block."""
    c = colors
    return f"""
        pre {{
            background-color: {c['code_block_bg']};
            font-family: 'JetBrains Mono', 'Fira Code', Consolas, Menlo, monospace;
            padding: 0 8px;
        }}
        code {{
            background-color: {c['inline_code_bg']};
            font-family: 'JetBrains Mono', 'Fira Code', Consolas, Menlo, monospace;
            padding: 1px 5px;
        }}
        /* line-height, not just margin: an inline `code` chip's background
        paints edge-to-edge on every wrapped line but the last (a Qt rich-
        text limitation — confirmed even for a plain <span>, no link, no
        padding involved), which read as one solid merged block when lines
        sat flush against each other. Extra line spacing puts a visible gap
        between those bars instead, so a wrapped chip reads as separate
        highlighted line segments rather than a smear. */
        p, li {{ line-height: 130%; }}
        p {{ margin: 6px 0; }}
        h1, h2, h3, h4, h5, h6 {{ margin: 14px 0 8px 0; }}
        ul, ol {{ margin: 6px 0; }}
        li {{ margin: 2px 0; }}
        hr {{ margin: 16px 0; }}
    """


# -- syntax-highlighted code blocks -----------------------------------------
#
# Qt's rich-text engine has no concept of syntax highlighting, so fenced
# code blocks are pulled out and highlighted with Pygments. Each becomes its
# own real widget (see windows.py's _CodeBlockWidget) rather than an HTML
# table embedded in the shared conversation document: a wide, unwrappable
# line (a long shell command, most often) otherwise forced the *entire*
# document — and from there the window itself — wider to fit it, since a
# table cell's width is a hint Qt happily grows past, not a hard cap. Only a
# real QScrollArea can legitimately stay narrower than its content and show
# a scrollbar instead, right under the block, the way most web chat UIs do
# it — and Qt's rich text has no equivalent notion of an independently
# scrollable region.
#
# Pygments ships a "nord" style matching the app's own Nord theme, used
# unconditionally (not just in the app's own dark mode): its token colours
# are tuned for Nord's own dark Polar Night background, and several — plain
# identifiers and punctuation among them — sit almost exactly on top of the
# app's light-theme background (both are the same pale Nord "Snow Storm"
# family), rendering as invisible text. Rather than fork a separate light
# palette, the code card keeps a fixed Nord-dark panel in both app themes —
# also a common, deliberate choice in other editors/chat UIs.
_PYGMENTS_STYLE = "nord"
CODE_PANEL_BG = QColor(NORD["n00"]).darker(130).name()
CODE_LABEL_BG = NORD["n1"]
# A visibly distinct shade for the Copy control specifically — otherwise
# it's just bold text sitting on the same flat bar as the language label,
# which read as a label itself rather than a clickable button.
CODE_COPY_BG = NORD["n2"]
CODE_BORDER = NORD["n3"]
CODE_FG = NORD["n4"]
# Indent guides use the same hue as CODE_BORDER (the gutter's own divider
# line), faded — they used to be visually identical, which read as the
# guide being just another structural divider rather than a subtler,
# secondary hint the way indent guides read in most editors.
_code_guide = QColor(CODE_BORDER)
_code_guide.setAlpha(110)
CODE_GUIDE_COLOR = _code_guide.name(QColor.NameFormat.HexArgb)
# Fallback stack used whenever the user hasn't chosen a specific monospace
# font (Settings → General → "Code font") — also appended after their pick,
# so a font that turns out to be unavailable just degrades to this instead
# of Qt's generic (often serif) font-family fallback.
CODE_FONT_FAMILY = "'JetBrains Mono', 'Fira Code', Consolas, Menlo, monospace"
CODE_LABEL_PADDING = 2
CODE_BODY_PADDING = 12
# Padding for the readable-width-capped text/code column itself (see
# windows.py's _StreamingBrowser._wrap_capped) — a real QWidget layout
# margin there, not table cellpadding.
CONTENT_PADDING = 16
_FENCE_RE = re.compile(r"^```([^\n`]*)\n(.*?)\n```[ \t]*$", re.MULTILINE | re.DOTALL)

# scheme for the per-block "Copy" link — _StreamingBrowser.anchorClicked
# intercepts it instead of letting QTextBrowser try to navigate to it
COPY_LINK_SCHEME = "kortalk-copy"
# same idea, for an image-attachment marker in the transcript — MainWindow
# turns a click into an in-app preview instead of QDesktopServices.openUrl
ATTACHMENT_LINK_SCHEME = "kortalk-attachment"

_MARKDOWN_SPECIAL_RE = re.compile(r'([\\`*_{}\[\]()#+.!<>|~-])')
# A line's own leading run of spaces/tabs, or any run of 2+ spaces further
# in — single inter-word spaces are left alone (nothing to preserve there).
_WHITESPACE_RUN_RE = re.compile(r"(?m)(^[ \t]+)|( {2,})")


def _keep_whitespace_run(match: re.Match) -> str:
    run = match.group(1) or match.group(2)
    return "&nbsp;" * len(run.expandtabs(4))


def escape_user_text(text: str) -> str:
    """Prepares raw user-typed/pasted text for the transcript so it renders
    exactly as it was typed/pasted, rather than as Markdown:

    - Markdown's special characters are backslash-escaped — otherwise an
      identifier's underscores, or a "-"/"#" starting a line, get read as
      emphasis, a list, or a heading.
    - Indentation and multi-space alignment (pasted JSON/YAML/logs) are
      turned into non-breaking spaces — plain HTML text collapses repeated
      whitespace to a single space, which otherwise flattened them away."""
    return _MARKDOWN_SPECIAL_RE.sub(r"\\\1", _WHITESPACE_RUN_RE.sub(_keep_whitespace_run, text))


def _lexer_for(language: str, code: str):
    if language:
        try:
            return get_lexer_by_name(language, stripall=False)
        except ClassNotFound:
            pass
    try:
        return guess_lexer(code)
    except ClassNotFound:
        return TextLexer()


@dataclass
class ProseBlock:
    html: str


@dataclass
class CodeBlock:
    label: str
    source: str
    highlighted_html: str  # pygments spans, "<br />"-joined lines — no <pre>/table wrapper


_INLINE_CODE_RE = re.compile(r"<code>(.*?)</code>", re.DOTALL)


def effective_code_font(configured: str) -> str:
    """Combines the user's chosen monospace font (Settings → General →
    "Code font") with the built-in fallback stack, so a font that turns out
    to be unavailable (or left unset) just degrades to CODE_FONT_FAMILY
    instead of Qt's generic (often serif) default."""
    return f"'{configured}', {CODE_FONT_FAMILY}" if configured else CODE_FONT_FAMILY


def _wrap_inline_code(
    html_fragment: str, sources: list[str], colors: dict[str, str], code_font: str
) -> str:
    """Wraps every inline `code` span in a click-to-copy link (skipped for a
    <pre><code> block from 4-space-indented code, recognisable by an actual
    newline in its content — that's a multi-line block, not a short inline
    chip, and isn't meant to act like a fenced code block's Copy button).

    The chip's own background/padding/font are repeated here as an inline
    `style=` attribute on the `<a>` rather than left to the `code {...}`
    stylesheet rule that already styles every *other* inline `code` span:
    checked empirically — Qt's rich text engine silently drops a
    stylesheet's `background-color` (both a `code { }` element rule and an
    `a.inline-code { }` class rule) on a `<code>` nested inside an `<a>`,
    rendering it as a bare underlined link with no chip at all. A literal
    `style=` attribute on the anchor is the only form that actually paints."""
    style = (
        f"background-color:{colors['inline_code_bg']}; padding:1px 5px; "
        f"font-family:{code_font}; text-decoration:none; color:{colors['fg']};"
    )

    def _sub(match: re.Match) -> str:
        raw = html.unescape(match.group(1))
        if "\n" in raw:
            return match.group(0)
        index = len(sources)
        sources.append(raw)
        return (f'<a href="{COPY_LINK_SCHEME}:{index}" style="{style}">'
                f'{html.escape(raw)}</a>')
    return _INLINE_CODE_RE.sub(_sub, html_fragment)


def _render_prose(
    markdown_text: str, inline_code_sources: list[str], colors: dict[str, str], code_font: str
) -> str:
    # nl2br: a single typed/pasted newline is otherwise left as a bare "\n"
    # in the HTML output, which any renderer (Qt included) collapses to a
    # space rather than a line break — nl2br turns it into a real <br/>.
    rendered = _markdown.markdown(markdown_text, extensions=["extra", "sane_lists", "nl2br"])
    return _wrap_inline_code(rendered, inline_code_sources, colors, code_font)


def _highlighted_code(language: str, code: str) -> CodeBlock:
    lexer = _lexer_for(language, code)
    formatted = highlight(code, lexer, HtmlFormatter(nowrap=True, style=_PYGMENTS_STYLE))
    body = formatted.rstrip("\n").replace("\n", "<br />")
    label = language or (lexer.aliases[0] if lexer.aliases else "text")
    return CodeBlock(label=label, source=code, highlighted_html=body)


def split_markdown_blocks(
    markdown_text: str,
    colors: dict[str, str],
    code_font: str = CODE_FONT_FAMILY,
) -> tuple[list[ProseBlock | CodeBlock], list[str]]:
    """Splits a full AI answer (Markdown) into an ordered list of prose and
    fenced-code blocks. Used only for the complete, final render — chunks
    are still streamed in as plain text while a response is arriving.

    Each block becomes its own widget (see windows.py's _CodeBlockWidget)
    instead of one shared QTextDocument: a fenced code block with an
    unwrappable long line otherwise forced the *entire* document — and from
    there the window itself — wider to fit it, since Qt's rich-text tables
    have no notion of an independently scrollable region; a `width="N"`
    table cell is a hint, not a hard cap, so unwrappable content just grows
    the cell past it. Giving code its own real, horizontally scrollable
    strip (positioned right under it, like most web chat UIs) needs it to
    be a real QWidget with a real QScrollArea, not an HTML table cell.

    `colors` (theme.card_colors()) styles inline `code` copy-links — see
    _wrap_inline_code for why that can't be left to the stylesheet.
    `code_font` (see effective_code_font) styles those same chips — fenced
    code blocks apply it separately, in windows.py's _CodeBlockWidget.

    Also returns the raw source behind every inline `code` copy-link (see
    COPY_LINK_SCHEME / _wrap_inline_code), indexed to match."""
    blocks: list[ProseBlock | CodeBlock] = []
    inline_code_sources: list[str] = []
    pos = 0
    for m in _FENCE_RE.finditer(markdown_text):
        prose = markdown_text[pos:m.start()]
        if prose.strip():
            blocks.append(ProseBlock(_render_prose(prose, inline_code_sources, colors, code_font)))
        blocks.append(_highlighted_code(m.group(1).strip(), m.group(2)))
        pos = m.end()
    tail = markdown_text[pos:]
    if tail.strip() or not blocks:
        blocks.append(ProseBlock(_render_prose(tail, inline_code_sources, colors, code_font)))
    return blocks, inline_code_sources


def pygments_stylesheet(font_family: str = CODE_FONT_FAMILY) -> str:
    """CSS for the syntax-highlighted token colours inside a code block's
    body widget (see windows.py's _CodeBlockWidget, which owns the
    label/Copy header and the block's card chrome as real QWidget styling —
    this only has to cover Pygments' own `<span class="...">` token classes,
    scoped under .code-body so they don't leak into ordinary inline `code`
    chips elsewhere in the response)."""
    token_css = HtmlFormatter(style=_PYGMENTS_STYLE).get_style_defs(".code-body")
    return f"""
        {token_css}
        .code-body {{
            white-space: pre;
            color: {CODE_FG};
            background-color: {CODE_PANEL_BG};
            font-family: {font_family};
        }}
    """


def response_stylesheet(colors: dict[str, str], code_font: str = CODE_FONT_FAMILY) -> str:
    """Combined stylesheet for every response view (popup, dialog): base
    Markdown spacing plus syntax-highlighted code cards."""
    return markdown_content_stylesheet(colors) + pygments_stylesheet(code_font)


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


# Qt's stylesheet engine renders a QSpinBox's up/down glyphs as a plain
# filled rectangle when their border colours are set to fake a triangle via
# CSS's usual "transparent sides" trick (checked: no border mitering is
# applied at all) — the shape has to come from a real image instead. A
# `data:` URI in `image: url(...)` is silently dropped too (checked: no
# error, just no image); only an actual file path renders, hence generating
# these once to a small cache directory rather than embedding them inline.
_SPIN_ARROW_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "kortalk"
)


def _spin_arrow_path(up: bool, color: str) -> Path:
    path = _SPIN_ARROW_DIR / f"spin-{'up' if up else 'down'}-{color.lstrip('#')}.png"
    if not path.exists():
        pixmap = QPixmap(10, 6)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        triangle = (QPolygon([QPoint(1, 5), QPoint(9, 5), QPoint(5, 1)]) if up
                    else QPolygon([QPoint(1, 1), QPoint(9, 1), QPoint(5, 5)]))
        painter.drawPolygon(triangle)
        painter.end()
        try:
            _SPIN_ARROW_DIR.mkdir(parents=True, exist_ok=True)
            pixmap.save(str(path), "PNG")
        except OSError:
            pass
    return path


def window_stylesheet(colors: dict[str, str]) -> str:
    """Chrome shared by the settings dialog and the main window: same flat
    background and field colours as the popup card, applied regardless of
    the selected Qt style so the three windows always match.

    Every interactive control gets an explicit hover/pressed/checked/
    disabled state — Fusion's defaults are too subtle to read as "this
    reacted to you", which is the point of styling them here at all."""
    c = colors
    up_arrow = _spin_arrow_path(True, c["fg"])
    down_arrow = _spin_arrow_path(False, c["fg"])
    up_arrow_muted = _spin_arrow_path(True, c["muted"])
    down_arrow_muted = _spin_arrow_path(False, c["muted"])
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

        /* QApplication.setPalette()'s ToolTipBase/ToolTipText roles aren't
        picked up here — QToolTip keeps its own separate, OS-default
        palette unless styled explicitly, which read as a random mismatched
        colour (e.g. pale system tooltip yellow/white) against this theme —
        shown by the code block / inline-code "Copied" tooltip. */
        QToolTip {{
            background-color: {c['field_bg']}; color: {c['fg']};
            border: 1px solid {c['border']}; padding: 4px 8px;
        }}

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
        QComboBox {{ padding: 4px 8px; }}
        QComboBox::drop-down {{ border: none; width: 22px; }}
        QComboBox QAbstractItemView {{
            background-color: {c['field_bg']}; color: {c['fg']};
            border: 1px solid {c['border']};
            selection-background-color: {c['highlight']};
            selection-color: {c['highlight_text']};
            outline: none;
        }}

        QSpinBox {{ padding-right: 2px; }}
        QSpinBox::up-button, QSpinBox::down-button {{
            background-color: {c['code_bg']};
            border: none;
            border-left: 1px solid {c['border']};
            width: 18px;
        }}
        QSpinBox::up-button {{ border-top-right-radius: 6px; }}
        QSpinBox::down-button {{ border-bottom-right-radius: 6px; }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background-color: {c['highlight']};
        }}
        QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{
            background-color: {c['muted']};
        }}
        QSpinBox::up-button:disabled, QSpinBox::down-button:disabled {{
            background-color: {c['field_bg']};
        }}
        QSpinBox::up-arrow {{ image: url({up_arrow}); width: 10px; height: 6px; }}
        QSpinBox::down-arrow {{ image: url({down_arrow}); width: 10px; height: 6px; }}
        QSpinBox::up-arrow:disabled {{ image: url({up_arrow_muted}); }}
        QSpinBox::down-arrow:disabled {{ image: url({down_arrow_muted}); }}

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

        QSplitter::handle {{ background-color: {c['border']}; }}
        QSplitter::handle:hover {{ background-color: {c['highlight']}; }}

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
