"""Factories for generated PDFs (Phase 14).

The service and the endpoint tests both need PDFs the
PDF library can parse, and bytes that are not a PDF at
all. Generating them in one place keeps the two suites
from drifting apart, and keeps the *how* of building a
test PDF out of the *what* each test asserts.

The library is imported inside the builders rather than
at the top of the module, so this module loads in a
deployment that has no PDF library installed: the builders
are only ever called from tests the `requires_pdf` marker
has already skipped in such a deployment.
"""

from __future__ import annotations

from app.schemas.pdf import PdfUpload

__all__ = [
    "garbage_bytes",
    "multi_page_pdf_bytes",
    "pdf_bytes",
    "pdf_upload",
]


def pdf_bytes(*, text: str = "", lines: tuple[str, ...] = ()) -> bytes:
    """Render a one-page PDF the PDF library can parse.

    Args:
        text: A single line to render.
        lines: Several lines to render, taking precedence over `text`.

    Returns:
        PDF bytes.
    """
    import pymupdf

    rows = lines if lines else (text,) if text else ()
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    for index, row in enumerate(rows):
        page.insert_text((72, 72 + 20 * index), row, fontsize=12)
    payload = document.tobytes()
    document.close()
    return payload


def multi_page_pdf_bytes(page_count: int = 3, *, text: str = "page") -> bytes:
    """Render a multi-page PDF, for the page-limit tests.

    Args:
        page_count: How many pages to render.
        text: The text to put on every page.

    Returns:
        PDF bytes with `page_count` pages.
    """
    import pymupdf

    document = pymupdf.open()
    for _ in range(page_count):
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 72), text, fontsize=12)
    payload = document.tobytes()
    document.close()
    return payload


def garbage_bytes() -> bytes:
    """Bytes that declare a PDF but do not parse as one.

    Returns:
        Non-PDF bytes.
    """
    return b"not a pdf at all"


def pdf_upload(
    content: bytes | None = None,
    *,
    content_type: str = "application/pdf",
    filename: str | None = "document.pdf",
) -> PdfUpload:
    """Build an upload for the service.

    Args:
        content: The bytes. A blank PDF when omitted.
        content_type: The declared media type.
        filename: The submitted filename.

    Returns:
        A `PdfUpload`.
    """
    return PdfUpload(
        content=content if content is not None else pdf_bytes(),
        content_type=content_type,
        filename=filename,
    )
