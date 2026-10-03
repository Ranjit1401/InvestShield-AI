"""PDF text extraction (Phase 14).

This service turns a submitted PDF into the text the
pipeline analyses. It is the PDF modality's answer to
:class:`~app.services.ocr_service.OCRService`: one
dependency that takes an untrusted upload and returns the
text it carries, or a typed refusal.

Three rules govern it.

**Optional dependencies are imported lazily.** PyMuPDF is an
extra, not a core dependency, and the graph imports this
module's parent package at start-up. Every import of the
library therefore happens inside a method, guarded by
:func:`~app.services.capabilities.module_available`, so a
deployment without PDF support still starts and still reports
the limitation honestly (D-009).

**The caller is told why text is missing, in the value.**
Extraction does not raise when the library is absent or fails
on one document. It returns a :class:`~app.schemas.pdf.PdfDocument`
carrying a `limitation` code, because a missing library is a
degradation to record and keep going past, not a crash. Only
a submission that is itself the problem — too large, not a
PDF, unreadable — is refused with :class:`PDFError`, which the
graph records as a typed error that ends the run.

**The engine is invoked, never trusted.** The bytes are parsed
by PyMuPDF as data. The submitted media type is a hint that is
checked against what the bytes actually parse to, so a label
cannot smuggle a non-PDF past the gate. The document is read
in memory — nothing is written to a temporary path — and its
links and embedded actions are never followed or executed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.pdf import PdfDocument, PdfSource, PdfUpload
from app.services.capabilities import module_available
from app.services.pdf_guards import (
    SYNTAX_MESSAGES,
    is_pdf_content_type,
    is_too_large,
)

__all__ = [
    "ENGINE_MESSAGES",
    "PDF_MESSAGES",
    "PDFError",
    "PDFService",
]

logger = get_logger(__name__)

#: Fixed wording for the one refusal that is the engine's
#: fault rather than the caller's. An extraction failure is
#: recorded as a limitation and the run continues, but the
#: code still needs one stable sentence, defined here rather
#: than wherever the engine happens to raise.
ENGINE_MESSAGES: dict[str, str] = {
    "PDF_EXTRACTION_FAILED": (
        "The submitted PDF could not be read by the text "
        "extraction engine."
    ),
}

#: Every code this service can refuse or report with, worded in
#: one place. The graph merges these into its own tables so its
#: promise — that every failure code it can record has fixed
#: wording — holds for the PDF modality too.
PDF_MESSAGES: dict[str, str] = {
    **SYNTAX_MESSAGES,
    **ENGINE_MESSAGES,
}


class PDFError(Exception):
    """A typed refusal to process a submitted PDF.

    Raised only for submissions that are themselves the
    problem — too large, not a PDF, unreadable. The library
    being absent or failing on a valid PDF is *not* a
    :class:`PDFError`; those come back as a document with a
    limitation, because the run continues past them.

    Attributes:
        code: The stable refusal code.
        message: The fixed wording for that code, with any
            fields filled in.
        cause_type: The class name of the underlying failure,
            when one was wrapped. A diagnostic, never shown to
            the caller.
    """

    def __init__(
        self,
        code: str,
        *,
        cause_type: str | None = None,
        **fields: object,
    ) -> None:
        message = PDF_MESSAGES[code]
        if fields:
            message = message.format(**fields)  # type: ignore[arg-type]
        super().__init__(message)
        self.code = code
        self.message = message
        self.cause_type = cause_type


class PDFService:
    """Extracts the text in a submitted PDF.

    The service holds no open resources. It reports through
    :attr:`available` whether extraction can run in this
    deployment at all.

    Attributes:
        settings: The settings the service was built with.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Read the extraction limits.

        Args:
            settings: Settings to read the limits from. Defaults
                to the process settings.
        """
        self._settings = settings or Settings()

    @property
    def available(self) -> bool:
        """Whether extraction can run in this deployment.

        Extraction needs the PyMuPDF library. Its absence is the
        single outcome — the document cannot be read — and is
        reported as the `PDF_UNAVAILABLE` limitation rather than
        a crash.
        """
        return module_available("pymupdf")

    def extract(self, upload: PdfUpload) -> PdfDocument:
        """Turn a submitted PDF into investigation text.

        The submission is validated and parsed first, because
        those steps refuse a bad submission outright and because
        they need only the checks here. Extraction runs
        afterwards, and only when the library is present.

        Args:
            upload: The submitted PDF, already bounded by the
                upload limit.

        Returns:
            A :class:`~app.schemas.pdf.PdfDocument`. Its `text`
            is empty and its `limitation` set when extraction
            could not run or failed; otherwise it carries the
            recovered text.

        Raises:
            PDFError: The submission is the problem — too large,
                not a PDF, or not a readable PDF. The graph
                records these as typed errors that end the run.
        """
        if is_too_large(len(upload.content), self._settings):
            raise PDFError(
                "PDF_FILE_TOO_LARGE",
                max_bytes=self._settings.max_upload_bytes,
            )

        if not is_pdf_content_type(upload.content_type):
            raise PDFError("PDF_TYPE_UNSUPPORTED")

        if not self.available:
            return PdfDocument(
                source=self._unparsed_source(upload),
                limitation="PDF_UNAVAILABLE",
            )

        source, document = self._parse(upload)

        try:
            raw_text, pages_processed = self._extract(document)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            logger.info(
                "PDF extraction failed",
                extra={"error_type": type(exc).__name__},
            )
            return PdfDocument(
                source=source,
                limitation="PDF_EXTRACTION_FAILED",
                error_type=type(exc).__name__,
            )
        finally:
            document.close()

        budget = self._settings.pdf_extraction_max_chars
        truncated = len(raw_text) > budget
        text = raw_text[:budget] if truncated else raw_text

        final_source = source.model_copy(
            update={
                "pages_processed": pages_processed,
                "text_recovered": bool(text.strip()),
                "truncated": truncated,
                "truncated_at": budget if truncated else None,
            }
        )
        return PdfDocument(
            source=final_source,
            text=text,
            truncated=truncated,
            truncated_at=budget if truncated else None,
        )

    def _unparsed_source(self, upload: PdfUpload) -> PdfSource:
        """Build the provenance for a PDF that was never parsed.

        Used when extraction cannot run at all, so the run still
        records what was submitted even though nothing could be
        measured from it.

        Args:
            upload: The submitted PDF.

        Returns:
            A :class:`~app.schemas.pdf.PdfSource` with every
            parse-dependent field left as ``None``.
        """
        return PdfSource(
            filename=upload.filename,
            content_type=upload.content_type,
            byte_size=len(upload.content),
            text_recovered=False,
            processed_at=datetime.now(timezone.utc),
        )

    def _parse(self, upload: PdfUpload) -> tuple[PdfSource, object]:
        """Parse the submitted PDF and measure it.

        The declared media type was already checked; this is the
        authoritative check, because it asks the PDF library what
        the bytes actually are. A file that declares itself a PDF
        but parses to anything this version does not read is
        refused here — PyMuPDF will open a non-PDF as a document
        that is not a PDF, so `is_pdf` is the gate, not the
        absence of an exception.

        Args:
            upload: The submitted PDF.

        Returns:
            The provenance, and the parsed document ready for
            text extraction. The caller must close it.

        Raises:
            PDFError: The bytes are not a readable PDF, or are
                not a PDF at all.
        """
        import pymupdf

        try:
            document = pymupdf.open(
                stream=upload.content,
                filetype="pdf",
            )
        except Exception:
            raise PDFError("PDF_UNREADABLE") from None

        if not document.is_pdf:
            document.close()
            raise PDFError("PDF_TYPE_UNSUPPORTED")

        source = PdfSource(
            filename=upload.filename,
            content_type=upload.content_type,
            detected_content_type="application/pdf",
            format="PDF",
            byte_size=len(upload.content),
            page_count=document.page_count,
            text_recovered=False,
            processed_at=datetime.now(timezone.utc),
        )
        return source, document

    def _extract(self, document: object) -> tuple[str, int]:
        """Read the text out of a parsed PDF.

        Only the first `pdf_max_pages` pages are read, so a
        pathological document cannot make the investigation read
        an unbounded number of pages. The returned page count is
        how many pages were actually read, which the caller
        compares against the document's total to record whether
        the later pages were skipped.

        Args:
            document: A parsed PyMuPDF document.

        Returns:
            The text the pages carried, and the number of pages
            read.

        Raises:
            Exception: Whatever the engine raised. Caught by the
                caller and reported as the `PDF_EXTRACTION_FAILED`
                limitation.
        """
        import pymupdf

        max_pages = self._settings.pdf_max_pages
        page_count = document.page_count  # type: ignore[attr-defined]
        pages_to_read = min(page_count, max_pages)

        chunks: list[str] = []
        for index in range(pages_to_read):
            page = document.load_page(index)  # type: ignore[attr-defined]
            chunks.append(page.get_text())

        return "\n".join(chunks), pages_to_read


def build_pdf_analysis_text(document: PdfDocument) -> str:
    """Compose the string the existing pipeline analyses for a PDF submission.

    The PDF's recovered text is the subject, but the document's
    own facts are included ahead of it. A reader of the report
    needs to see which PDF was analysed, and those facts are
    evidence in their own right even when the document yielded
    no text — which is the whole reason the pipeline still runs
    over them rather than stopping at an empty document.

    Args:
        document: The extraction document.

    Returns:
        Text for Phase 1 and Phase 2 to work on.
    """
    source = document.source
    header = ["PDF submitted."]
    if source.filename:
        header.append(f"File name: {source.filename}")
    if source.detected_content_type:
        header.append(f"Type: {source.detected_content_type}")
    header.append(f"Size: {source.byte_size} bytes")
    if source.page_count is not None:
        header.append(f"Pages: {source.page_count}")

    preamble = "\n".join(header)
    if not document.text.strip():
        return preamble
    return f"{preamble}\n\n{document.text}"
