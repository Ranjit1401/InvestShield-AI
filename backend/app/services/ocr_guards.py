"""OCR submission policy and refusal wording (Phase 13).

This module owns the *policy* for accepting or refusing a
submitted image, and the fixed English wording for every
refusal that is the caller's fault. It is the image modality's
answer to :mod:`app.services.url_guards`, and follows the same
rules:

- **Pure.** Nothing here imports Pillow, pytesseract or any
  other optional dependency. The graph imports the wording
  tables from this module at start-up, so this module must load
  in a deployment that has no OCR libraries installed (D-009).
  Decoding an image needs Pillow and is therefore in
  :mod:`app.services.ocr_service`, not here.

- **Worded in one place.** Every caller-facing refusal is a
  constant here. A refusal that a service or a node words for
  itself is a second definition of "an unsupported image", and
  two definitions drift.

- **Accept or refuse, never judge.** The checks below decide
  only whether a submission *can* be read — is it small enough,
  is it an image at all, is it an image this version reads. None
  of them expresses an opinion about the content, and the wording
  is written so it cannot be read as one.

The size limit is enforced twice on purpose: once by the HTTP
layer, which reads at most one byte past the limit so an oversized
upload never reaches memory in full, and once here, inside the
service, so the limit holds for any caller and not only the one
route that happens to check first.
"""

from __future__ import annotations

from app.core.config import Settings

__all__ = [
    "OCR_IMAGE_FORMATS",
    "SYNTAX_MESSAGES",
    "format_mime",
    "is_image_content_type",
    "is_too_large",
]

#: The image formats this version can read. Keys are the format
#: names the image library reports for a successfully opened file;
#: values are the MIME type each decodes to, recorded as provenance.
#:
#: This set is the authoritative gate on what counts as "an image
#: we analyse". A file whose declared type is an image but whose
#: bytes decode to anything else — a GIF, a BMP, a script wearing
#: an ``image/`` label — is refused rather than passed to the
#: recognition engine.
OCR_IMAGE_FORMATS: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}

#: Fixed wording for every refusal that is the caller's fault.
#: Each maps a stable code to one sentence that states what was
#: refused and why, in the same neutral register as the URL
#: syntax refusals. None accuses, none advises, none characterises
#: the submission's content.
SYNTAX_MESSAGES: dict[str, str] = {
    "OCR_IMAGE_EMPTY": (
        "No image was submitted. Send the image to investigate."
    ),
    "OCR_IMAGE_TOO_LARGE": (
        "The submitted image is larger than the {max_bytes} byte "
        "limit this version accepts."
    ),
    "OCR_IMAGE_TYPE_UNSUPPORTED": (
        "The submitted file is not an image type this version "
        "analyses. PNG, JPEG and WebP images are accepted."
    ),
    "OCR_IMAGE_UNREADABLE": (
        "The submitted file could not be read as an image."
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


def is_image_content_type(content_type: str) -> bool:
    """Decide whether a declared media type is one this version reads.

    The declared type is untrusted, so this is a first-pass filter
    rather than the authority on what a file is: it refuses a
    submission that is plainly not an image before the image is
    decoded, and the decoded format is checked afterwards.

    Args:
        content_type: The media type the caller declared.

    Returns:
        Whether the declared type is an accepted image type.
    """
    return content_type in set(OCR_IMAGE_FORMATS.values())


def format_mime(format_name: str | None) -> str | None:
    """Map an image library's format name to its media type.

    Args:
        format_name: The format the image library reported, or
            ``None`` when it reported none.

    Returns:
        The media type the format decodes to, or ``None`` when the
        format is not one this version analyses.
    """
    if format_name is None:
        return None
    return OCR_IMAGE_FORMATS.get(format_name)
