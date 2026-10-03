"""Screenshot / image investigation input and output contracts (Phase 13).

Kept apart from :mod:`app.schemas.api` for the same reason the URL
contracts are: these describe *an image input modality* rather than the
API surface, and are consumed by the graph, the persistence layer and
the adapter. The HTTP endpoint itself accepts ``multipart/form-data``
rather than a JSON body, so there is no request model to discover here;
the upload transport, the provenance record and the recognition result
are what belong in one place.

Two rules govern everything here, inherited from Phase 12.

**An image is evidence, not a verdict.** `ImageSource` records what was
uploaded and what the text-recognition engine recovered from it. Nothing
in this module states that an image is fraudulent, and the field names
are chosen so a value cannot be read as a finding: `text_recovered` is
a fact about the OCR run, `detected_content_type` is what the bytes
actually parse as, and neither implies anything about legitimacy.

**An upload is untrusted input.** It arrives from a user and is handed
to a native library that decodes it. The validation here is therefore
about *accepting or refusing* a submission, never about scoring it, and
the decoded image is treated as data throughout — never executed, never
interpreted as an instruction.

The module deliberately imports nothing optional. The graph, the
persistence layer and the API all import these models, so they must
load in a deployment that has no OCR libraries installed (D-009); the
recognition itself lives in :mod:`app.services.ocr_service`, which
imports its optional dependencies lazily.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ImageDocument",
    "ImageSource",
    "ImageUpload",
    "content_digest",
]


class ImageUpload(BaseModel):
    """One submitted image, exactly as it arrived.

    This is the transport between the HTTP layer and the graph. The route
    reads the multipart file into memory (bounded by the upload limit),
    wraps it in one of these, and hands it to the graph; the graph's
    input stage is the only consumer. It is deliberately a Pydantic model
    rather than a bare ``bytes`` so the declared type and filename travel
    with the content and the value is validated on construction.

    The model is *not* frozen: the bytes are large and copying them would
    be wasteful, and nothing downstream mutates an upload. It is also
    deliberately absent from the investigation response — it is input,
    not a finding.

    Attributes:
        content: The uploaded bytes, already bounded by the size limit.
        content_type: The media type the client declared, lowercased.
            Untrusted: the decoded format is the authority, not this.
        filename: The filename the client supplied, if any. Descriptive
            only; never used to decide anything.
    """

    model_config = ConfigDict(extra="forbid")

    content: bytes = Field(description="The uploaded image bytes.")
    content_type: str = Field(
        default="application/octet-stream",
        description="The declared media type, lowercased. Untrusted.",
    )
    filename: str | None = Field(
        default=None,
        description="The submitted filename, when the client supplied one.",
    )


class ImageSource(BaseModel):
    """What was uploaded, and what the engine recovered from it.

    Persisted on the run and returned in the investigation response so a
    report can state which image it is describing. Every field is either
    a fact about the submission, a measurement of the decoded image, or a
    fact about the OCR run. None is a judgement.

    The decode-dependent fields — `detected_content_type`, `format`,
    `width` and `height` — are ``None`` when the image was never decoded,
    which is the normal state of a run whose OCR libraries are absent.
    Recording the submission's facts anyway is what lets a report say
    "a screenshot was submitted and could not be read" as one coherent
    object rather than two half-records.

    Attributes:
        filename: The filename exactly as the caller supplied it, or
            ``None`` when there was none.
        content_type: The declared media type, lowercased.
        detected_content_type: The media type the bytes actually decoded
            to, as reported by the image library, or ``None`` when the
            image was not decoded. This is the authority on what the
            file is, and it can differ from `content_type`.
        format: The image library's format name (``PNG``, ``JPEG`` or
            ``WEBP``), or ``None`` when the image was not decoded.
        byte_size: Bytes actually uploaded.
        width: Decoded image width in pixels, or ``None`` when the
            image was not decoded.
        height: Decoded image height in pixels, or ``None`` when the
            image was not decoded.
        ocr_language: The language pack the engine was asked to use.
        text_recovered: Whether the engine produced any text. A fact
            about the run, not a statement about the image's content.
        truncated: Whether the recovered text was cut at the character
            budget.
        truncated_at: The character offset the text was cut at, or
            ``None`` when it was not truncated.
        processed_at: When the image was processed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str | None = None
    content_type: str
    detected_content_type: str | None = None
    format: str | None = None
    byte_size: int
    width: int | None = None
    height: int | None = None
    ocr_language: str
    text_recovered: bool
    truncated: bool = False
    truncated_at: int | None = None
    processed_at: datetime

    def as_metadata(self) -> dict[str, object]:
        """Render as JSON-safe primitives for the persistence layer.

        The run stores this as a JSON blob in the `image_metadata`
        column — a column of its own, parallel to the `source_metadata`
        a URL submission uses — because it is descriptive metadata
        about one input modality that nothing joins to or filters on.
        A text or URL investigation has none of it, and a nullable
        column per field would spread image concerns across the core
        schema.
        """
        return self.model_dump(mode="json")


class ImageDocument(BaseModel):
    """The result of turning a submitted image into investigation text.

    This is the value the graph's input stage consumes; it is never
    persisted and never serialised to a client. `limitation` and
    `error_type` exist so the engine can report *why* it produced no
    text without raising, leaving the graph free to decide how that
    outcome is recorded (Phase 13 records it as a limitation and keeps
    going, exactly as a page with no readable text does).

    Attributes:
        source: The upload record the text came from.
        text: Recovered text, already bounded by the character budget.
            Empty when the image yielded nothing readable.
        truncated: Whether `text` was cut short against the budget. A
            truncated image produces a recorded limitation so the pipeline
            never claims to have read text it did not see.
        truncated_at: The character offset the text was cut at.
        limitation: The code of the degradation that prevented
            recognition, or ``None`` when recognition ran. Carries
            `OCR_UNAVAILABLE` when the engine could not run at all and
            `OCR_FAILED` when it ran and failed.
        error_type: The exception class that ended recognition, for
            diagnosis. Present only with the `OCR_FAILED` limitation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: ImageSource
    text: str = ""
    truncated: bool = False
    truncated_at: int | None = None
    limitation: str | None = None
    error_type: str | None = None


def content_digest(upload: ImageUpload) -> str:
    """Derive a stable identity for a submitted image.

    A digest of the content rather than the filename, because two
    spellings of one screenshot share an investigation id no matter what
    the caller named the file. The image bytes are mixed with the input
    type by the caller, so an image and a text submission cannot collide.

    Args:
        upload: The submitted image.

    Returns:
        A hex digest of the image content.
    """
    return hashlib.sha256(upload.content).hexdigest()
