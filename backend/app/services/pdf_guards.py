"""PDF submission policy and refusal wording (Phase 14).

This module owns the *policy* for accepting or refusing a
submitted PDF, and the fixed English wording for every refusal
that is the caller's fault. It is the PDF modality's answer to
:mod:`app.services.url_guards` and :mod:`app.services.ocr_guards`,
and follows the same rules:

- **Pure.** Nothing here imports PyMuPDF or any other optional
  dependency. The graph imports the wording tables from this
  module at start-up, so this module must load in a deployment
  that has no PDF library installed (D-009). Parsing a PDF needs
  PyMuPDF and is therefore in :mod:`app.services.pdf_service`,
  not here.

- **Worded in one place.** Every caller-facing refusal is a
  constant here. A refusal that a service or a node words for
  itself is a second definition of "an unsupported PDF", and
  two definitions drift.

- **Accept or refuse, never judge.** The checks below decide
  only whether a submission *can* be read — is it small enough,
  is it a PDF at all, is it a PDF this version reads. None of
  them expresses an opinion about the content, and the wording
  is written so it cannot be read as one.

The size limit is enforced twice on purpose: once by the HTTP
layer, which reads at most one byte past the limit so an
oversized upload never reaches memory in full, and once here,
inside the service, so the limit holds for any caller and not
only the one route that happens to check first.
"""

from __future__ import annotations

from app.core.config import Settings

__all__ = [
    "PDF_CONTENT_TYPES",
    "SYNTAX_MESSAGES",
    "is_pdf_content_type",
    "is_too_large",
]

#: The media types this version reads as a PDF. There is one,
#: because there is one document format this version analyses.
#:
#: This set is the authoritative first-pass gate on what counts
#: as "a PDF we analyse". It is a *first pass*: the declared
#: type is untrusted, so a submission that plainly is not a PDF
#: is refused before the bytes are parsed, and the parsed
#: document is the authority checked afterwards. A file that
#: declares itself a PDF but parses to anything else is refused
#: rather than passed to the extraction engine.
PDF_CONTENT_TYPES: frozenset[str] = frozenset({"application/pdf"})

#: Fixed wording for every refusal that is the caller's fault.
#: Each maps a stable code to one sentence that states what was
#: refused and why, in the same neutral register as the URL
#: syntax refusals. None accuses, none advises, none characterises
#: the submission's content.
SYNTAX_MESSAGES: dict[str, str] = {
    "PDF_EMPTY": (
        "No PDF was submitted. Send the PDF to investigate."
    ),
    "PDF_FILE_TOO_LARGE": (
        "The submitted PDF is larger than the {max_bytes} byte "
        "limit this version accepts."
    ),
    "PDF_TYPE_UNSUPPORTED": (
        "The submitted file is not a PDF this version analyses. "
        "PDF documents are accepted."
    ),
    "PDF_UNREADABLE": (
        "The submitted file could not be read as a PDF."
    ),
}


def is_too_large(byte_size: int, settings: Settings) -> bool:
    """Decide whether a submission exceeds the upload limit.

    Args:
        byte_size: The number of bytes actually submitted.
        settings: The settings carrying the limit.

    Returns:
        Whether the submission is too large to accept.
    """
    return byte_size > settings.max_upload_bytes


def is_pdf_content_type(content_type: str) -> bool:
    """Decide whether a declared media type is one this version reads.

    The declared type is untrusted, so this is a first-pass filter
    rather than the authority on what a file is: it refuses a
    submission that is plainly not a PDF before the document is
    parsed, and the parsed document is checked afterwards.

    Args:
        content_type: The media type the caller declared.

    Returns:
        Whether the declared type is the accepted PDF type.
    """
    return content_type in PDF_CONTENT_TYPES
