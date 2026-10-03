"""The PDF extraction service (Phase 14).

The service owns three decisions and one outcome. It refuses a
submission that is too large or not a PDF this version reads; it
refuses bytes that do not parse; and it reports — rather than
raises — an engine that is absent or that failed. The outcome it
produces is a document carrying the extracted text, the document's
own facts, and any limitation, so the graph can record an honest
result without knowing how the text was obtained.

The engine is exercised for real where it is installed, and the
extraction-path logic (truncation, page limit) is driven
deterministically, so the suite passes whether or not a deployment
has PyMuPDF.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.config import Settings
from app.schemas.pdf import PdfDocument, PdfSource
from app.services.pdf_service import (
    PDFError,
    PDFService,
    build_pdf_analysis_text,
)
from tests.pdf_factories import (
    garbage_bytes,
    multi_page_pdf_bytes,
    pdf_bytes,
    pdf_upload,
)


def offline_settings(**overrides: Any) -> Settings:
    """Keyless settings that cannot reach a `.env` or the network.

    Args:
        **overrides: Settings fields to override.

    Returns:
        Keyless settings.
    """
    return Settings(_env_file=None, **overrides)


#: Skips the tests that need a real engine, so the suite passes in a
#: deployment that has no PDF library installed.
requires_pdf = pytest.mark.skipif(
    not PDFService(settings=offline_settings()).available,
    reason="the PyMuPDF library is not installed",
)


class TestExtractRefusals:
    """A submission the caller must fix is refused, not parsed."""

    def test_a_pdf_over_the_limit_is_refused(self) -> None:
        settings = offline_settings(max_upload_bytes=16)
        service = PDFService(settings=settings)

        with pytest.raises(PDFError) as caught:
            service.extract(pdf_upload(content=b"x" * 17))

        assert caught.value.code == "PDF_FILE_TOO_LARGE"
        assert "16" in caught.value.message

    def test_a_non_pdf_media_type_is_refused(self) -> None:
        service = PDFService(settings=offline_settings())

        with pytest.raises(PDFError) as caught:
            service.extract(
                pdf_upload(content=b"never parsed", content_type="text/plain")
            )

        assert caught.value.code == "PDF_TYPE_UNSUPPORTED"

    @requires_pdf
    def test_bytes_that_do_not_parse_are_refused(self) -> None:
        service = PDFService(settings=offline_settings())

        with pytest.raises(PDFError) as caught:
            service.extract(pdf_upload(content=garbage_bytes()))

        assert caught.value.code == "PDF_UNREADABLE"


class TestExtract:
    """A readable PDF yields its text, bounded by the budgets."""

    @requires_pdf
    def test_a_readable_pdf_yields_its_text(self) -> None:
        service = PDFService(settings=offline_settings())

        document = service.extract(
            pdf_upload(content=pdf_bytes(text="Acme Capital Advisors"))
        )

        assert "Acme Capital Advisors" in document.text
        assert document.limitation is None
        assert document.source.text_recovered is True
        assert document.source.page_count == 1
        assert document.source.pages_processed == 1
        assert document.source.format == "PDF"
        assert document.source.detected_content_type == "application/pdf"

    @requires_pdf
    def test_a_blank_pdf_yields_no_text(self) -> None:
        service = PDFService(settings=offline_settings())

        document = service.extract(pdf_upload(content=pdf_bytes()))

        assert document.text.strip() == ""
        assert document.limitation is None
        assert document.source.text_recovered is False

    @requires_pdf
    def test_recovered_text_is_cut_at_the_character_budget(self) -> None:
        settings = offline_settings(pdf_extraction_max_chars=10)
        service = PDFService(settings=settings)

        document = service.extract(
            pdf_upload(content=pdf_bytes(text="0123456789abcdef"))
        )

        assert document.text == "0123456789"
        assert document.truncated is True
        assert document.truncated_at == 10
        assert document.source.truncated is True
        assert document.source.truncated_at == 10

    @requires_pdf
    def test_only_the_first_pages_are_read(self) -> None:
        settings = offline_settings(pdf_max_pages=2)
        service = PDFService(settings=settings)
        content = multi_page_pdf_bytes(page_count=5)

        document = service.extract(pdf_upload(content=content))

        assert document.source.page_count == 5
        assert document.source.pages_processed == 2


class TestAnalysisText:
    """The string the pipeline analyses states the document's facts."""

    def _source(self, **overrides: Any) -> PdfSource:
        """Build a provenance record with a fixed clock.

        Args:
            **overrides: PdfSource fields to override.

        Returns:
            A `PdfSource`.
        """
        fields: dict[str, Any] = {
            "filename": "offer.pdf",
            "content_type": "application/pdf",
            "detected_content_type": "application/pdf",
            "format": "PDF",
            "byte_size": 1234,
            "page_count": 2,
            "pages_processed": 2,
            "text_recovered": True,
            "processed_at": datetime.now(timezone.utc),
        }
        fields.update(overrides)
        return PdfSource(**fields)

    def test_the_facts_precede_the_recovered_text(self) -> None:
        document = PdfDocument(
            source=self._source(), text="the recovered text"
        )

        analysis = build_pdf_analysis_text(document)

        assert analysis.startswith("PDF submitted.")
        assert "offer.pdf" in analysis
        assert "application/pdf" in analysis
        assert "1234" in analysis
        assert "Pages: 2" in analysis
        assert analysis.endswith("the recovered text")

    def test_a_text_free_document_still_states_its_facts(self) -> None:
        document = PdfDocument(
            source=self._source(text_recovered=False), text=""
        )

        analysis = build_pdf_analysis_text(document)

        assert "PDF submitted." in analysis
        assert "offer.pdf" in analysis
        assert "Pages: 2" in analysis
        assert not analysis.endswith("\n\n")
