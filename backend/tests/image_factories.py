"""Factories for generated images (Phase 13).

The service and the endpoint tests both need images the
image library can decode, and an image the library cannot.
Generating them in one place keeps the two suites from
drifting apart, and keeps the *how* of building a test
image out of the *what* each test asserts.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.schemas.ocr import ImageUpload

__all__ = [
    "blank_png_bytes",
    "gif_bytes",
    "image_upload",
    "png_bytes",
]


def png_bytes(*, text: str = "", lines: tuple[str, ...] = ()) -> bytes:
    """Render a PNG the image library can decode.

    Args:
        text: A single line to render.
        lines: Several lines to render, taking precedence over `text`.

    Returns:
        PNG bytes.
    """
    rows = lines if lines else (text,) if text else ()
    width, height = 1200, 100 * max(len(rows), 1) + 60
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for index, row in enumerate(rows):
        draw.text((30, 30 + 100 * index), row, fill="black")
    # The default bitmap font is small; scale the canvas up so the
    # engine has enough pixels to read it where it is installed.
    image = image.resize((width * 2, height * 2))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def gif_bytes() -> bytes:
    """Render a GIF, a format this version does not read.

    Returns:
        GIF bytes.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="GIF")
    return buffer.getvalue()


def blank_png_bytes() -> bytes:
    """Render a PNG with nothing drawn on it.

    Returns:
        PNG bytes.
    """
    return png_bytes()


def image_upload(
    content: bytes | None = None,
    *,
    content_type: str = "image/png",
    filename: str | None = "shot.png",
) -> ImageUpload:
    """Build an upload for the service.

    Args:
        content: The bytes. A blank PNG when omitted.
        content_type: The declared media type.
        filename: The submitted filename.

    Returns:
        An `ImageUpload`.
    """
    return ImageUpload(
        content=content if content is not None else png_bytes(),
        content_type=content_type,
        filename=filename,
    )
