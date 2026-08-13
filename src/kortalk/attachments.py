"""Attachments (images, text files) for dialog-mode messages.

An Attachment always carries its payload as `str` (base64 for images, raw
decoded text for files) so a message dict with an "attachments" list drops
straight into session.py's existing `json.dumps(history)` — no separate
wire/storage format to keep in sync.

Providers differ in how they consume attachments (see providers.py):
Anthropic and OpenAI-compatible APIs take native multimodal content blocks;
Claude Code CLI has no such flag and instead gets images materialized into
a temp directory it's run with as `cwd`, referenced by name in the prompt
text (see _resolve_claude_cli_attachments there). Text-file attachments are
simply folded into the message text everywhere (split_text_attachments) —
identical handling needed only once.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODeviceBase, Qt
from PySide6.QtGui import QImage, QImageReader

from .i18n import tr

# Anthropic/OpenAI both recommend capping the long edge around this size —
# larger images cost more tokens without adding usable detail.
MAX_IMAGE_DIMENSION = 1568
# A generous cap for inlined text so one huge log file can't blow the
# context window; truncated rather than rejected outright.
MAX_TEXT_CHARS = 50_000
# Rejected before even trying to read/decode — no point loading a huge
# binary into memory just to find out it's not an image or text.
MAX_FILE_BYTES = 25 * 1024 * 1024


@dataclass
class Attachment:
    kind: str   # "image" | "file"
    name: str   # filename shown in the UI and folded into the prompt
    mime: str   # e.g. "image/png"; "" for text files
    data: str   # base64 (images) or raw decoded text (files)


def attachment_from_qimage(image: QImage, name: str) -> Attachment:
    """Downscales (if needed) and PNG-encodes a QImage — used for
    clipboard/drag image data that has no file of its own."""
    if image.width() > MAX_IMAGE_DIMENSION or image.height() > MAX_IMAGE_DIMENSION:
        image = image.scaled(
            MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION,
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
    buffer = QBuffer()
    buffer.open(QIODeviceBase.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    data = base64.b64encode(bytes(buffer.data())).decode("ascii")
    return Attachment(kind="image", name=name, mime="image/png", data=data)


def classify_and_read(path: Path) -> tuple[Attachment | None, str]:
    """Reads a dropped/pasted local file. Returns (attachment, "") on
    success, or (None, human-readable reason) when it's rejected — the
    caller shows that reason in the status bar rather than silently
    dropping the file."""
    if not path.is_file():
        return None, tr("{name}: not a file").format(name=path.name)

    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, tr("{name}: {error}").format(name=path.name, error=exc)
    if size > MAX_FILE_BYTES:
        return None, tr("{name}: too large (max {max} MB)").format(
            name=path.name, max=MAX_FILE_BYTES // (1024 * 1024))

    # Real format sniffing (magic bytes), not just the extension — a
    # renamed .png with a .txt suffix is still read as an image.
    reader = QImageReader(str(path))
    if reader.canRead():
        image = reader.read()
        if not image.isNull():
            return attachment_from_qimage(image, path.name), ""
        # canRead() lied (truncated/corrupt file) — fall through to text.

    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None, tr("{name}: unsupported file type").format(name=path.name)
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + tr("\n\n… (truncated)")
    return Attachment(kind="file", name=path.name, mime="", data=text), ""


def split_text_attachments(content: str, attachments: list[dict]) -> tuple[str, list[dict]]:
    """Folds every "file" (text) attachment straight into `content`;
    returns the amended text plus whatever "image" attachments remain for
    the caller to turn into its own provider-specific blocks."""
    images = []
    for att in attachments:
        if att["kind"] == "file":
            content += f"\n\n--- {att['name']} ---\n{att['data']}\n--- end {att['name']} ---"
        else:
            images.append(att)
    return content, images
