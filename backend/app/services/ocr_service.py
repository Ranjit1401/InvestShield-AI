"""Screenshot text recognition (Phase 13).

This service turns a submitted image into the text the
pipeline analyses. It is the image modality's answer to
:class:`~app.services.url_fetch.URLFetchService` wrapped
together with :class:`~app.services.website_extractor.WebsiteContentExtractor`:
one dependency that takes an untrusted upload and returns
the text it carries, or a typed refusal.

Three rules govern it.

**Optional dependencies are imported lazily.** Pillow and
pytesseract are extras, not core dependencies, and the
graph imports this module's parent package at start-up.
Every import of an optional library therefore happens
inside a method, guarded by :func:`~app.services.capabilities.module_available`,
so a deployment without OCR still starts and still reports
the limitation honestly (D-009).

**The caller is told why text is missing, in the value.**
Recognition does not raise when the engine is absent or
fails on one image. It returns an :class:`~app.schemas.ocr.ImageDocument`
carrying a `limitation` code, because a missing engine is a
degradation to record and keep going past, not a crash. Only
a submission that is itself the problem — too large, not an
image, unreadable — is refused with :class:`OCRError`, which
the graph records as a typed error that ends the run.

**The engine is invoked, never trusted.** The image is
decoded by Pillow and handed to the Tesseract binary as
data. The submitted media type is a hint that is checked
against what the bytes actually decode to, so a label
cannot smuggle a non-image past the gate.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.ocr import ImageDocument, ImageSource, ImageUpload
from app.services.capabilities import module_available, resolve_tesseract_cmd
from app.services.ocr_guards import (
    SYNTAX_MESSAGES,
    format_mime,
    is_image_content_type,
    is_too_large,
)

__all__ = [
    "ENGINE_MESSAGES",
    "OCR_MESSAGES",
    "OCRError",
    "OCRService",
]

logger = get_logger(__name__)

#: Fixed wording for the one refusal that is the engine's
#: fault rather than the caller's. A recognition failure is
#: recorded as a limitation and the run continues, but the
#: code still needs one stable sentence, defined here rather
#: than wherever the engine happens to raise.
ENGINE_MESSAGES: dict[str, str] = {
    "OCR_FAILED": (
        "The submitted image could not be read by the text "
        "recognition engine."
    ),
}

#: Every code this service can refuse or report with, worded in
#: one place. The graph merges these into its own tables so its
#: promise — that every failure code it can record has fixed
#: wording — holds for the image modality too.
OCR_MESSAGES: dict[str, str] = {
    **SYNTAX_MESSAGES,
    **ENGINE_MESSAGES,
}


class OCRError(Exception):
    """A typed refusal to process a submitted image.

    Raised only for submissions that are themselves the
    problem — too large, not an image, unreadable. The engine
    being absent or failing on a valid image is *not* an
    :class:`OCRError`; those come back as a document with a
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
        message = OCR_MESSAGES[code]
        if fields:
            message = message.format(**fields)  # type: ignore[arg-type]
        super().__init__(message)
        self.code = code
        self.message = message
        self.cause_type = cause_type


class OCRService:
    """Recognises the text in a submitted screenshot.

    The service holds no open resources. It resolves the
    Tesseract executable once, on construction, and reports
    through :attr:`available` whether recognition can run in
    this deployment at all.

    Attributes:
        settings: The settings the service was built with.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Resolve the recognition engine.

        Args:
            settings: Settings to read the engine location and
                limits from. Defaults to the process settings.
        """
        self._settings = settings or Settings()
        self._tesseract_cmd = resolve_tesseract_cmd(self._settings)

    @property
    def available(self) -> bool:
        """Whether recognition can run in this deployment.

        Recognition needs three things: the Pillow library to
        decode the image, the pytesseract library to drive the
        engine, and the Tesseract executable itself. All three
        must be present; the absence of any one is the same
        outcome — the image cannot be read — and is reported as
        the single `OCR_UNAVAILABLE` limitation rather than
        three different ones.
        """
        return (
            self._tesseract_cmd is not None
            and module_available("PIL")
            and module_available("pytesseract")
        )

    def extract(self, upload: ImageUpload) -> ImageDocument:
        """Turn a submitted image into investigation text.

        The submission is validated and decoded first, because
        those steps refuse a bad submission outright and because
        they need only Pillow. Recognition runs afterwards, and
        only when the engine is present.

        Args:
            upload: The submitted image, already bounded by the
                upload limit.

        Returns:
            An :class:`~app.schemas.ocr.ImageDocument`. Its
            `text` is empty and its `limitation` set when
            recognition could not run or failed; otherwise it
            carries the recovered text.

        Raises:
            OCRError: The submission is the problem — too large,
                not an image type this version reads, or not a
                readable image. The graph records these as typed
                errors that end the run.
        """
        if is_too_large(len(upload.content), self._settings):
            raise OCRError(
                "OCR_IMAGE_TOO_LARGE",
                max_bytes=self._settings.max_upload_bytes,
            )

        if not is_image_content_type(upload.content_type):
            raise OCRError("OCR_IMAGE_TYPE_UNSUPPORTED")

        if not self.available:
            return ImageDocument(
                source=self._undecoded_source(upload),
                limitation="OCR_UNAVAILABLE",
            )

        source, image = self._decode(upload)

        try:
            raw_text = self._recognize(image)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            logger.info(
                "Text recognition failed",
                extra={"error_type": type(exc).__name__},
            )
            return ImageDocument(
                source=source,
                limitation="OCR_FAILED",
                error_type=type(exc).__name__,
            )

        budget = self._settings.ocr_extraction_max_chars
        truncated = len(raw_text) > budget
        text = raw_text[:budget] if truncated else raw_text

        final_source = source.model_copy(
            update={
                "text_recovered": bool(text.strip()),
                "truncated": truncated,
                "truncated_at": budget if truncated else None,
            }
        )
        return ImageDocument(
            source=final_source,
            text=text,
            truncated=truncated,
            truncated_at=budget if truncated else None,
        )

    def _undecoded_source(self, upload: ImageUpload) -> ImageSource:
        """Build the provenance for an image that was never decoded.

        Used when recognition cannot run at all, so the run still
        records what was submitted even though nothing could be
        measured from it.

        Args:
            upload: The submitted image.

        Returns:
            An :class:`~app.schemas.ocr.ImageSource` with every
            decode-dependent field left as ``None``.
        """
        return ImageSource(
            filename=upload.filename,
            content_type=upload.content_type,
            byte_size=len(upload.content),
            ocr_language=self._settings.ocr_languages,
            text_recovered=False,
            processed_at=datetime.now(timezone.utc),
        )

    def _decode(self, upload: ImageUpload) -> tuple[ImageSource, object]:
        """Decode the submitted image and measure it.

        The declared media type was already checked; this is the
        authoritative check, because it asks the image library
        what the bytes actually are. A file that declares itself
        an image but decodes to anything this version does not
        read is refused here.

        Args:
            upload: The submitted image.

        Returns:
            The provenance, and the decoded image ready for the
            recognition engine.

        Raises:
            OCRError: The bytes are not a readable image, or are
                not an image type this version analyses.
        """
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(upload.content))
            image.load()
        except Exception:
            raise OCRError("OCR_IMAGE_UNREADABLE") from None

        detected_mime = format_mime(image.format)
        if detected_mime is None:
            raise OCRError("OCR_IMAGE_TYPE_UNSUPPORTED")

        width, height = image.size
        source = ImageSource(
            filename=upload.filename,
            content_type=upload.content_type,
            detected_content_type=detected_mime,
            format=image.format,
            byte_size=len(upload.content),
            width=width,
            height=height,
            ocr_language=self._settings.ocr_languages,
            text_recovered=False,
            processed_at=datetime.now(timezone.utc),
        )
        return source, image

    def _recognize(self, image: object) -> str:
        """Run the Tesseract engine over a decoded image.

        Args:
            image: A decoded Pillow image.

        Returns:
            The text the engine recovered, unbounded.

        Raises:
            Exception: Whatever the engine raised. Caught by the
                caller and reported as the `OCR_FAILED` limitation.
        """
        import pytesseract

        # pytesseract reads the executable location from a
        # process-global, so it is set from this service's own
        # resolution immediately before the call. That keeps the
        # global correct for every caller of the service rather
        # than whichever service was constructed last.
        pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd
        return str(
            pytesseract.image_to_string(
                image,
                lang=self._settings.ocr_languages,
                timeout=self._settings.ocr_timeout_seconds,
            )
        )


def build_image_analysis_text(document: ImageDocument) -> str:
    """Compose the string the existing pipeline analyses for an image submission.

    The screenshot's recovered text is the subject, but the image's
    own facts are included ahead of it. A reader of the report needs
    to see which image was analysed, and those facts are evidence in
    their own right even when the image yielded no text — which is
    the whole reason the pipeline still runs over them rather than
    stopping at an empty image.

    Args:
        document: The recognition document.

    Returns:
        Text for Phase 1 and Phase 2 to work on.
    """
    source = document.source
    header = ["Screenshot submitted."]
    if source.filename:
        header.append(f"File name: {source.filename}")
    if source.detected_content_type:
        header.append(f"Type: {source.detected_content_type}")
    header.append(f"Size: {source.byte_size} bytes")
    if source.width is not None and source.height is not None:
        header.append(f"Dimensions: {source.width} x {source.height} pixels")

    preamble = "\n".join(header)
    if not document.text.strip():
        return preamble
    return f"{preamble}\n\n{document.text}"
