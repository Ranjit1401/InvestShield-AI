"""Tests for query normalization (Phase 3, §10).

The central risk is over-eager cleanup. Rewriting a query turns a search for one
claim into a search for a different claim, so these tests are as much about
what must be left alone as about what gets fixed.
"""

from __future__ import annotations

import pytest

from app.services.search.query import (
    MAX_QUERY_LENGTH,
    is_meaningful_query,
    normalize_query,
)


class TestWhitespaceAndControlCharacters:
    """Pasted, messy input becomes one clean line."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("   SEBI   registration   XYZ   ", "SEBI registration XYZ"),
            ("SEBI\tregistration\tXYZ", "SEBI registration XYZ"),
            ("SEBI\nregistration\nXYZ", "SEBI registration XYZ"),
            ("SEBI\r\nregistration\r\nXYZ", "SEBI registration XYZ"),
            ("SEBI     registration          XYZ", "SEBI registration XYZ"),
            ("SEBI\u00a0registration\u00a0XYZ", "SEBI registration XYZ"),
            ("  \n\t  ", ""),
        ],
    )
    def test_whitespace_is_collapsed_and_trimmed(self, raw: str, expected: str) -> None:
        assert normalize_query(raw) == expected

    def test_control_characters_are_removed(self) -> None:
        assert "\x00" not in normalize_query("SEBI\x00 registration\x07 here")
        assert "\x1b" not in normalize_query("\x1b[31mSEBI\x1b[0m")

    def test_zero_width_and_bidi_characters_are_removed(self) -> None:
        raw = "SEBI\u200bregis\u200dtration\u202etesting"

        assert normalize_query(raw) == "SEBIregistrationtesting"

    def test_newlines_never_leak_into_a_provider_query(self) -> None:
        assert "\n" not in normalize_query("line one\nline two\n\nline three")


class TestWhatMustNotChange:
    """Names, identifiers and URLs pass through untouched."""

    @pytest.mark.parametrize(
        "raw",
        [
            "acmefunds@okhdfcbank",
            "INH0000123456",
            "HDFC0000123",
            "https://example.com/a/b?c=d&e=f",
            "www.example.com",
            "SEBI/RA/A1234567",
            "₹25,000 minimum investment",
            "35% monthly returns",
            "O'Hara Capital (P) Ltd.",
            "guaranteed returns (100% safe)",
            "Dr. Ramesh Kumar",
        ],
    )
    def test_meaningful_input_is_preserved(self, raw: str) -> None:
        assert normalize_query(raw) == raw

    def test_punctuation_is_not_stripped(self) -> None:
        assert normalize_query("ABC-123/XYZ") == "ABC-123/XYZ"

    def test_unicode_text_survives(self) -> None:
        assert normalize_query("SEBI पंजीकरण") == "SEBI पंजीकरण"

    def test_unicode_is_nfc_composed(self) -> None:
        assert normalize_query("cafe\u0301") == "caf\u00e9"

    def test_no_terms_are_invented(self) -> None:
        raw = "guaranteed 35% monthly returns"
        normalized = normalize_query(raw)

        assert normalized == raw
        assert len(normalized.split()) == len(raw.split())


class TestLengthLimits:
    """Queries are bounded so a bug cannot ask for an unbounded request."""

    def test_short_query_is_untouched(self) -> None:
        assert normalize_query("SEBI registration") == "SEBI registration"

    def test_long_query_is_truncated(self) -> None:
        raw = "word " * 500

        assert len(normalize_query(raw)) <= MAX_QUERY_LENGTH

    def test_truncation_happens_at_a_word_boundary(self) -> None:
        raw = "alpha beta gamma delta " * 100
        truncated = normalize_query(raw)

        assert len(truncated) <= MAX_QUERY_LENGTH
        assert not truncated.endswith((" ", "-", ","))

    def test_explicit_limit_is_respected(self) -> None:
        raw = "alpha beta gamma delta epsilon"

        assert len(normalize_query(raw, max_length=12)) <= 12

    def test_non_positive_limit_disables_truncation(self) -> None:
        raw = "x" * (MAX_QUERY_LENGTH + 50)

        assert normalize_query(raw, max_length=0) == raw


class TestEdgeCases:
    """Empty and meaningless input is reported, not sent."""

    @pytest.mark.parametrize("raw", [None, "", "   ", "\n\n", "\t"])
    def test_blank_queries_normalize_to_empty(self, raw: str | None) -> None:
        assert normalize_query(raw) == ""

    def test_punctuation_only_is_not_meaningful(self) -> None:
        assert is_meaningful_query(normalize_query("!!! ... ???")) is False

    def test_identifier_is_meaningful(self) -> None:
        assert is_meaningful_query(normalize_query("acmefunds@okhdfcbank")) is True

    def test_unicode_word_is_meaningful(self) -> None:
        assert is_meaningful_query(normalize_query("पंजीकरण")) is True