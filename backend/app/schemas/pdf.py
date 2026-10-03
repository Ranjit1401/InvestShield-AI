"""PDF investigation input and output contracts (Phase 14).

Kept apart from :mod:`app.schemas.api` for the same reason the
URL and image contracts are: these describe *a PDF input modality*
rather than the API surface, and are consumed by the graph, the
persistence layer and the adapter. The HTTP endpoint itself accepts
``multipart/form-data`` rather than a JSON body, so there is no
request model to discover here; the upload transport, the provenance
record and the extraction result are what belong in one place.

Two rules govern everything here, inherited from Phases 12 and 13.

**A PDF is evidence, not a verdict.** `PdfSource` records what was
uploaded and what the text-extraction engine recovered from it.
Nothing in this module states that a PDF is fraudulent, and the
field names are chosen so a value cannot be read as a finding:
`text_recovered` is a fact about the extraction run,
`detected_content_type` is what the bytes actually parse as, and
neither implies anything about legitimacy.

**An upload is untrusted input.** It arrives from a user and is
handed to a native library that parses it. The validation here is
therefore about *accepting or refusing* a submission, never about
scoring it, and the parsed document is treated as data throughout —
never executed, never interpreted as an instruction, and never
followed: links and embedded actions inside the PDF are not acted on.

The module deliberately imports nothing optional. The graph, the
persistence layer and the API all import these models, so they must
load in a deployment that has no PDF library installed (D-009); the
extraction itself lives in :mod:`app.services.pdf_service`, which
imports its optional dependency lazily.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PdfDocument",
    "PdfSource",
    "PdfUpload",
    "content_digest",
]


class PdfUpload(BaseModel):
    """One submitted PDF, exactly as it arrived.

    This is the transport between the HTTP layer and the graph. The
    route reads the multipart file into memory (bounded by the upload
    limit), wraps it in one of these, and hands it to the graph; the
    graph's input stage is the only consumer. It is deliberately a
    Pydantic model rather than a bare ``bytes`` so the declared type
    and filename travel with the content and the value is validated on
    construction.

    The model is *not* frozen: the bytes are large and copying them
    would be wasteful, and nothing downstream mutates an upload. It is
    also deliberately absent from the investigation response — it is
    input, not a finding.

    Attributes:
        content: The uploaded bytes, already bounded by the size limit.
        content_type: The media type the client declared, lowercased.
            Untrusted: the parsed document is the authority, not this.
        filename: The filename the client supplied, if any. Descriptive
            only; never used to decide anything.
    """

    model_config = ConfigDict(extra="forbid")

    content: bytes = Field(description="The uploaded PDF bytes.")
    content_type: str = Field(
        default="application/octet-stream",
        description="The declared media type, lowercased. Untrusted.",
    )
    filename: str | None = Field(
        default=None,
        description="The submitted filename, when the client supplied one.",
    )


class PdfSource(BaseModel):
    """What was uploaded, and what the engine recovered from it.

    Persisted on the run and returned in the investigation response so a
    report can state which PDF it is describing. Every field is either
    a fact about the submission, a measurement of the parsed document,
    or a fact about the extraction run. None is a judgement.

    The parse-dependent fields — `detected_content_type`, `format`,
    `page_count` and `pages_processed` — are ``None`` when the document
    was never opened, which is the normal state of a run whose PDF
    library is absent. Recording the submission's facts anyway is what
    lets a report say "a PDF was submitted and could not be read" as
    one coherent object rather than two half-records.

    Attributes:
        filename: The filename exactly as the caller supplied it, or
            ``None`` when there was none.
        content_type: The declared media type, lowercased.
        detected_content_type: The media type the bytes actually parsed
            as, or ``None`` when the document was not parsed. This is
            the authority on what the file is, and it can differ from
            `content_type`.
        format: The document format name (``PDF``), or ``None`` when the
            document was not parsed.
        byte_size: Bytes actually uploaded.
        page_count: The number of pages in the document, or ``None``
            when the document was not parsed.
        pages_processed: The number of pages text was actually read
            from, or ``None`` when the document was not parsed. This is
            the lesser of `page_count` and the configured page limit,
            so a value below `page_count` means the later pages were
            not read.
        text_recovered: Whether the engine produced any text. A fact
            about the run, not a statement about the document's content.
        truncated: Whether the recovered text was cut at the character
            budget.
        truncated_at: The character offset the text was cut at, or
            ``None`` when it was not truncated.
        processed_at: When the document was processed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str | None = None
    content_type: str
    detected_content_type: str | None = None
    format: str | None = None
    byte_size: int
    page_count: int | None = None
    pages_processed: int | None = None
    text_recovered: bool
    truncated: bool = False
    truncated_at: int | None = None
    processed_at: datetime

    def as_metadata(self) -> dict[str, object]:
        """Render as JSON-safe primitives for the persistence layer.

        The run stores this as a JSON blob in the `pdf_metadata`
        column — a column of its own, parallel to the `source_metadata`
        a URL submission uses and the `image_metadata` an image
        submission uses — because it is descriptive metadata about one
        input modality that nothing joins to or filters on. A text, URL
        or image investigation has none of it, and a nullable column per
        field would spread PDF concerns across the core schema.
        """
        return self.model_dump(mode="json")


class PdfDocument(BaseModel):
    """The result of turning a submitted PDF into investigation text.

    This is the value the graph's input stage consumes; it is never
    persisted and never serialised to a client. `limitation` and
    `error_type` exist so the engine can report *why* it produced no
    text without raising, leaving the graph free to decide how that
    outcome is recorded (Phase 14 records it as a limitation and keeps
    going, exactly as a page with no readable text does).

    Attributes:
        source: The upload record the text came from.
        text: Recovered text, already bounded by the character budget.
            Empty when the document yielded nothing readable.
        truncated: Whether `text` was cut short against the budget. A
            truncated document produces a recorded limitation so the
            pipeline never claims to have read text it did not see.
        truncated_at: The character offset the text was cut at.
        limitation: The code of the degradation that prevented
            extraction, or ``None`` when extraction ran. Carries
            `PDF_UNAVAILABLE` when the engine could not run at all and
            `PDF_EXTRACTION_FAILED` when it ran and failed.
        error_type: The exception class that ended extraction, for
            diagnosis. Present only with the `PDF_EXTRACTION_FAILED`
            limitation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: PdfSource
    text: str = ""
    truncated: bool = False
    truncated_at: int | None = None
    limitation: str | None = None
    error_type: str | None = None


def content_digest(upload: PdfUpload) -> str:
    """Derive a stable identity for a submitted PDF.

    A digest of the content rather than the filename, because two
    spellings of one document share an investigation id no matter what
    the caller named the file. The PDF bytes are mixed with the input
    type by the caller, so a PDF and a text submission cannot collide.

    Args:
        upload: The submitted PDF.

    Returns:
        A hex digest of the PDF content.
    """
    return hashlib.sha256(upload.content).hexdigest()
