"""Tests for attachments.py: reading, classifying, downscaling, folding."""

from __future__ import annotations

import base64

from PySide6.QtGui import QColor, QImage

from kortalk.attachments import (
    MAX_FILE_BYTES,
    MAX_IMAGE_DIMENSION,
    MAX_TEXT_CHARS,
    attachment_from_qimage,
    classify_and_read,
    split_text_attachments,
)


def _solid_image(width: int, height: int, color: str = "red") -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


# -- attachment_from_qimage -----------------------------------------------------

def test_attachment_from_qimage_small_image_is_not_resized():
    image = _solid_image(50, 40)
    att = attachment_from_qimage(image, "small.png")
    assert att.kind == "image"
    assert att.mime == "image/png"
    assert att.name == "small.png"

    decoded = QImage()
    decoded.loadFromData(base64.b64decode(att.data))
    assert (decoded.width(), decoded.height()) == (50, 40)


def test_attachment_from_qimage_large_image_is_downscaled_keeping_aspect():
    image = _solid_image(3000, 2000)
    att = attachment_from_qimage(image, "big.png")

    decoded = QImage()
    decoded.loadFromData(base64.b64decode(att.data))
    assert max(decoded.width(), decoded.height()) <= MAX_IMAGE_DIMENSION
    # 3000x2000 is a 3:2 ratio — must be preserved after scaling
    assert abs(decoded.width() / decoded.height() - 3000 / 2000) < 0.01


# -- classify_and_read ------------------------------------------------------------

def test_classify_and_read_text_file(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello\nworld", encoding="utf-8")

    att, error = classify_and_read(path)

    assert error == ""
    assert att.kind == "file"
    assert att.name == "notes.txt"
    assert att.mime == ""
    assert att.data == "hello\nworld"


def test_classify_and_read_truncates_long_text(tmp_path):
    path = tmp_path / "huge.txt"
    path.write_text("x" * (MAX_TEXT_CHARS + 500), encoding="utf-8")

    att, error = classify_and_read(path)

    assert error == ""
    assert len(att.data) < MAX_TEXT_CHARS + 500
    assert att.data.endswith("(truncated)")


def test_classify_and_read_real_image(tmp_path):
    path = tmp_path / "pic.png"
    _solid_image(20, 20, "blue").save(str(path))

    att, error = classify_and_read(path)

    assert error == ""
    assert att.kind == "image"
    assert att.mime == "image/png"


def test_classify_and_read_rejects_binary_file(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(bytes([0, 159, 146, 150, 255, 0, 1, 2]) * 20)

    att, error = classify_and_read(path)

    assert att is None
    assert "data.bin" in error


def test_classify_and_read_rejects_oversized_file(tmp_path):
    path = tmp_path / "huge.bin"
    with open(path, "wb") as f:
        f.seek(MAX_FILE_BYTES + 10)
        f.write(b"0")

    att, error = classify_and_read(path)

    assert att is None
    assert "huge.bin" in error


def test_classify_and_read_missing_file(tmp_path):
    att, error = classify_and_read(tmp_path / "nope.txt")

    assert att is None
    assert "nope.txt" in error


def test_classify_and_read_renamed_image_is_still_detected_by_content(tmp_path):
    # A real PNG saved with a misleading .txt extension — must still be
    # read as an image (magic-byte sniffing via QImageReader, not the
    # filename extension).
    path = tmp_path / "actually_a_picture.txt"
    _solid_image(10, 10).save(str(path), "PNG")

    att, error = classify_and_read(path)

    assert error == ""
    assert att.kind == "image"


# -- split_text_attachments --------------------------------------------------------

def test_split_text_attachments_folds_file_into_content():
    content, images = split_text_attachments(
        "explain this", [{"kind": "file", "name": "a.py", "mime": "", "data": "print(1)"}])

    assert "print(1)" in content
    assert "a.py" in content
    assert content.startswith("explain this")
    assert images == []


def test_split_text_attachments_leaves_images_untouched():
    image_att = {"kind": "image", "name": "x.png", "mime": "image/png", "data": "AAAA"}
    content, images = split_text_attachments("look at this", [image_att])

    assert content == "look at this"
    assert images == [image_att]


def test_split_text_attachments_mixed():
    file_att = {"kind": "file", "name": "log.txt", "mime": "", "data": "error on line 3"}
    image_att = {"kind": "image", "name": "screenshot.png", "mime": "image/png", "data": "BBBB"}
    content, images = split_text_attachments("what's wrong", [file_att, image_att])

    assert "what's wrong" in content
    assert "error on line 3" in content
    assert "log.txt" in content
    assert images == [image_att]
