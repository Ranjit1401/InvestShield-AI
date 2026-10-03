"""The screenshot recognition service (Phase 13).

The service owns three decisions and one outcome. It refuses a
submission that is too large or not an image this version reads;
it refuses bytes that do not decode; and it reports — rather than
raises — an engine that is absent or that failed. The outcome it
produces is a document carrying the recovered text, the image's
own facts, and any limitation, so the graph can record an honest
result without knowing how the text was obtained.

The engine itself is exercised for real where it is installed,
and the recognition-path logic (truncation, failure) is driven
deterministically without it, so the suite passes whether or not
a deployment has Tesseract.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

import pytest
from PIL import Image, ImageDraw

from app.core.config import Settings
from app.schemas.ocr import ImageDocument, ImageSource, ImageUpload
from app.services.ocr_service import (
    OCRError,
    OCRService,
    build_image_analysis_text,
)


def offline_settings(**overrides: Any) -> Settings:
    """Keyless settings that cannot reach a `.env` or the network.

    Args:
        **overrides: Settings fields to override.

    Returns:
        Keyless settings.
    """
    return Settings(_env_file=None, **overrides)


def _png_bytes(*, text: str = "", lines: tuple[str, ...] = ()) -> bytes:
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


def _gif_bytes() -> bytes:
    """Render a GIF, a format this version does not read.

    Returns:
        GIF bytes.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="GIF")
    return buffer.getvalue()


def _upload(
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
        content=content if content is not None else _png_bytes(),
        content_type=content_type,
        filename=filename,
    )


#: Skips the tests that need a real engine, so the suite passes in a
#: deployment that has no Tesseract installed.
requires_ocr = pytest.mark.skipif(
    not OCRService(settings=offline_settings()).available,
    reason="the Tesseract engine is not installed",
)


class TestExtractRefusals:
    """A submission the caller must fix is refused, not decoded."""

    def test_an_image_over_the_limit_is_refused(self) -> None:
        settings = offline_settings(max_upload_bytes=16)
        service = OCRService(settings=settings)

        with pytest.raises(OCRError) as caught:
            service.extract(
                ImageUpload(content=b"x" * 17, content_type="image/png")
            )

        assert caught.value.code == "OCR_IMAGE_TOO_LARGE"

    def test_a_non_image_media_type_is_refused(self) -> None:
        service = OCRService(settings=offline_settings())

        with pytest.raises(OCRError) as caught:
            service.extract(
                ImageUpload(content=b"text", content_type="text/plain")
            )

        assert caught.value.code == "OCR_IMAGE_TYPE_UNSUPPORTED"

    def test_the_limit_is_checked_before_the_type(self) -> None:
        """Both are the caller's fault, but size is the cheaper to check."""
        settings = offline_settings(max_upload_bytes=8)
        service = OCRService(settings=settings)

        with pytest.raises(OCRError) as caught:
            service.extract(
                ImageUpload(content=b"x" * 9, content_type="text/plain")
            )

        assert caught.value.code == "OCR_IMAGE_TOO_LARGE"


class TestExtractDegradation:
    """A run the engine cannot serve is reported, not raised."""

    def test_an_absent_engine_is_an_unavailable_document(self) -> None:
        settings = offline_settings(tesseract_cmd="/no/such/tesseract")
        service = OCRService(settings=settings)

        document = service.extract(_upload())

        assert document.limitation == "OCR_UNAVAILABLE"
        assert document.text == ""
        assert document.source.text_recovered is False
        # The image was never decoded, so its facts are left blank
        # rather than guessed at.
        assert document.source.detected_content_type is None
        assert document.source.width is None


class TestDecode:
    """The image library, not the declared type, is the authority."""

    def test_a_readable_image_is_decoded_and_measured(self) -> None:
        service = OCRService(settings=offline_settings())

        source, image = service._decode(_upload(_png_bytes(text="chart")))

        assert source.detected_content_type == "image/png"
        assert source.format == "PNG"
        assert source.width is not None and source.width > 0
        assert source.height is not None and source.height > 0
        assert image.size == (source.width, source.height)

    def test_bytes_that_do_not_decode_are_refused(self) -> None:
        service = OCRService(settings=offline_settings())
        upload = ImageUpload(content=b"not an image", content_type="image/png")

        with pytest.raises(OCRError) as caught:
            service._decode(upload)

        assert caught.value.code == "OCR_IMAGE_UNREADABLE"

    def test_a_format_the_version_does_not_read_is_refused(self) -> None:
        service = OCRService(settings=offline_settings())
        upload = ImageUpload(content=_gif_bytes(), content_type="image/gif")

        with pytest.raises(OCRError) as caught:
            service._decode(upload)

        assert caught.value.code == "OCR_IMAGE_TYPE_UNSUPPORTED"


class TestRecognitionPath:
    """The recognition outcome is mapped onto a document, not raised."""

    def test_text_longer_than_the_budget_is_cut(self, monkeypatch) -> None:
        monkeypatch.setattr(OCRService, "available", property(lambda self: True))
        settings = offline_settings(ocr_extraction_max_chars=20)
        service = OCRService(settings=settings)
        monkeypatch.setattr(service, "_recognize", lambda image: "a" * 100)

        document = service.extract(_upload())

        assert document.limitation is None
        assert document.truncated is True
        assert document.text == "a" * 20
        assert document.source.text_recovered is True
        assert document.source.truncated is True
        assert document.source.truncated_at == 20

    def test_text_within_the_budget_is_kept_whole(self, monkeypatch) -> None:
        monkeypatch.setattr(OCRService, "available", property(lambda self: True))
        settings = offline_settings(ocr_extraction_max_chars=20)
        service = OCRService(settings=settings)
        monkeypatch.setattr(service, "_recognize", lambda image: "abc")

        document = service.extract(_upload())

        assert document.limitation is None
        assert document.truncated is False
        assert document.text == "abc"
        assert document.source.truncated is False
        assert document.source.truncated_at is None

    def test_a_recognition_failure_is_reported_not_raised(self, monkeypatch) -> None:
        monkeypatch.setattr(OCRService, "available", property(lambda self: True))
        service = OCRService(settings=offline_settings())

        def _raise(_image: object) -> str:
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(service, "_recognize", _raise)

        document = service.extract(_upload())

        assert document.limitation == "OCR_FAILED"
        assert document.error_type == "RuntimeError"
        assert document.text == ""
        assert document.source.text_recovered is False

    @requires_ocr
    def test_a_generated_screenshot_yields_its_text(self) -> None:
        service = OCRService(settings=offline_settings())

        document = service.extract(_upload(_png_bytes(text="SEBI Registered")))

        assert document.limitation is None
        assert document.text.strip()
        assert document.source.text_recovered is True
        assert document.source.detected_content_type == "image/png"


class TestBuildImageAnalysisText:
    """The text the pipeline analyses names the image it came from."""

    def _document(self, text: str = "", *, filename: str | None = "shot.png") -> ImageDocument:
        """Build a document for the composer.

        Args:
            text: The recovered text.
            filename: The submitted filename.

        Returns:
            An `ImageDocument`.
        """
        return ImageDocument(
            source=ImageSource(
                filename=filename,
                content_type="image/png",
                detected_content_type="image/png",
                format="PNG",
                byte_size=10,
                width=2,
                height=1,
                ocr_language="eng",
                text_recovered=bool(text.strip()),
                processed_at=datetime.now(timezone.utc),
            ),
            text=text,
        )

    def test_the_analysis_names_the_image_and_its_text(self) -> None:
        composed = build_image_analysis_text(self._document("Guaranteed 30% returns."))

        assert "Screenshot submitted." in composed
        assert "File name: shot.png" in composed
        assert "Type: image/png" in composed
        assert "Size: 10 bytes" in composed
        assert "Dimensions: 2 x 1 pixels" in composed
        assert "Guaranteed 30% returns." in composed

    def test_an_image_with_no_text_still_names_the_image(self) -> None:
        composed = build_image_analysis_text(self._document())

        assert "Screenshot submitted." in composed
        assert "File name: shot.png" in composed
        assert "Type: image/png" in composed
        assert "Size: 10 bytes" in composed
        # The image's own facts are the whole of the analysis when it
        # yielded no text, so the header is still complete and is not
        # followed by a recovered-text separator.
        assert composed.endswith("Dimensions: 2 x 1 pixels")

    def test_an_unnamed_image_is_composed_without_a_file_name(self) -> None:
        composed = build_image_analysis_text(self._document("text", filename=None))

        assert "File name:" not in composed
        assert "text" in composed
