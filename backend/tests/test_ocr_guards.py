"""The screenshot acceptance policy (Phase 13).

The policy is an allowlist in two parts: how large a submission may
be, and what kind of file it may be. Both are decided before any
image is decoded, because a submission that is too large or not an
image this version reads is the caller's to fix, and the caller
should be told so without paying the cost of decoding.

The wording is owned here and nowhere else, so a refusal and the
policy that produces it cannot drift apart.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.ocr_guards import (
    OCR_IMAGE_FORMATS,
    SYNTAX_MESSAGES,
    format_mime,
    is_image_content_type,
    is_too_large,
)


def offline_settings(max_upload_bytes: int = 1024) -> Settings:
    """Settings that cannot reach a `.env` or the network.

    Args:
        max_upload_bytes: The byte limit to test against.

    Returns:
        Keyless settings with the given upload limit.
    """
    return Settings(_env_file=None, max_upload_bytes=max_upload_bytes)


class TestSizeLimit:
    """The byte limit is the caller's to respect."""

    def test_content_within_the_limit_is_accepted(self) -> None:
        settings = offline_settings()

        assert not is_too_large(0, settings)
        assert not is_too_large(settings.max_upload_bytes - 1, settings)

    def test_content_at_the_limit_is_accepted(self) -> None:
        settings = offline_settings()

        assert not is_too_large(settings.max_upload_bytes, settings)

    def test_content_over_the_limit_is_refused(self) -> None:
        settings = offline_settings()

        assert is_too_large(settings.max_upload_bytes + 1, settings)

    def test_the_limit_is_read_from_settings_not_a_constant(self) -> None:
        settings = Settings(_env_file=None, max_upload_bytes=16)

        assert not is_too_large(16, settings)
        assert is_too_large(17, settings)


class TestMediaTypeAllowlist:
    """Only the formats the image library can decode are accepted."""

    @pytest.mark.parametrize("content_type", sorted(OCR_IMAGE_FORMATS.values()))
    def test_a_readable_format_is_accepted(self, content_type: str) -> None:
        assert is_image_content_type(content_type)

    @pytest.mark.parametrize(
        "content_type",
        [
            "application/pdf",
            "text/plain",
            "text/html",
            "application/json",
            "image/gif",
            "image/tiff",
            "image/bmp",
            "image/svg+xml",
            "image",
            "",
        ],
    )
    def test_an_unreadable_format_is_refused(self, content_type: str) -> None:
        assert not is_image_content_type(content_type)

    def test_the_check_matches_lowercase_only(self) -> None:
        """The route lowercases the declared type before it arrives.

        The policy is deliberately an exact match against lowercase
        values, so a mixed-case value that was not normalized is
        refused rather than guessed at.
        """
        assert is_image_content_type("image/png")
        assert not is_image_content_type("IMAGE/PNG")
        assert not is_image_content_type("image/PNG")


class TestFormatToMime:
    """The image library's format name maps to the media type it means."""

    @pytest.mark.parametrize(
        ("image_format", "mime"),
        sorted(OCR_IMAGE_FORMATS.items()),
    )
    def test_a_known_format_maps_to_its_mime(
        self, image_format: str, mime: str
    ) -> None:
        assert format_mime(image_format) == mime

    @pytest.mark.parametrize("image_format", ["GIF", "TIFF", "BMP", "SVG", "", None])
    def test_an_unknown_format_maps_to_no_mime(
        self, image_format: str | None
    ) -> None:
        assert format_mime(image_format) is None


class TestRefusalWording:
    """Every refusal is worded once, in plain English, and says no more."""

    @pytest.mark.parametrize("code", sorted(SYNTAX_MESSAGES))
    def test_every_code_has_plain_ascii_wording(self, code: str) -> None:
        message = SYNTAX_MESSAGES[code]

        assert isinstance(message, str)
        assert message
        assert message.endswith(".")
        assert all(ord(character) < 128 for character in message)

    def test_the_wording_names_no_host_or_internal_detail(self) -> None:
        """A refusal carries only what the caller already sent.

        The wording is fixed per code and filled with nothing else, so
        it cannot become a channel for one submission to learn about
        the deployment or the bytes of another.
        """
        for message in SYNTAX_MESSAGES.values():
            assert "Traceback" not in message
            assert "tesseract" not in message.lower()

    def test_every_syntax_code_is_a_distinct_message(self) -> None:
        """Two codes that said the same thing would be one code."""
        messages = list(SYNTAX_MESSAGES.values())

        assert len(messages) == len(set(messages))
