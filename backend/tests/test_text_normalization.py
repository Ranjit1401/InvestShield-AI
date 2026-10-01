"""Tests for input normalisation and its reversible offset map.

The offset map is the load-bearing part of Phase 2: every evidence span in the
product is derived from it. These tests pin the contract that any normalised
offset maps back to a real position in the original string, and that text
structure the extractors depend on survives normalisation.
"""

from __future__ import annotations

import pytest

from app.services.text_normalization import normalize_text

SAMPLES = [
    "Simple text with single spaces.",
    "  Leading and trailing whitespace.  ",
    "Line one\nLine two\n\nParagraph two starts here.",
    "Tabs\tand\ttabs plus   runs    of     spaces.",
    "Zero\u200bwidth\u200djoiner and soft\u00adhyphen characters.",
    "Control\x07chars\x00removed here.",
    "Emoji 🚀 and Devanagari मराठी and नमस्ते text.",
    "\n\n\nMany leading newlines.\n\n",
    "",
    " ",
    "é decomposed vs é composed",
]


class TestNormalizeText:
    """Normalisation must be safe to scan and reversible to locate."""

    def test_sample_texts_normalise_without_error(self) -> None:
        for raw in SAMPLES:
            normalized = normalize_text(raw)
            assert isinstance(normalized.text, str)
            assert normalized.original == raw

    def test_original_is_preserved_verbatim(self) -> None:
        raw = "  Hello   world \n\n Bye  "
        assert normalize_text(raw).original == raw

    def test_no_control_or_zero_width_characters_remain(self) -> None:
        raw = "Clean\x07me\u200bzero\u00adwidth\x00end"

        normalized = normalize_text(raw)

        assert "\x07" not in normalized.text
        assert "\u200b" not in normalized.text
        assert "\u00ad" not in normalized.text
        assert "\x00" not in normalized.text

    def test_whitespace_runs_collapse_to_single_spaces(self) -> None:
        assert normalize_text("a    b\t\tc").text == "a b c"

    def test_newlines_are_preserved(self) -> None:
        raw = "First paragraph.\n\nSecond paragraph."
        assert normalize_text(raw).text == raw

    def test_text_is_trimmed_at_the_ends(self) -> None:
        assert normalize_text("   padded   ").text == "padded"

    def test_unicode_is_nfc_composed(self) -> None:
        # "e" + combining acute -> single precomposed character.
        assert normalize_text("cafe\u0301").text == "caf\u00e9"


class TestIndexMap:
    """The index map must address the original string exactly."""

    def test_map_length_is_normalised_length_plus_sentinel(self) -> None:
        for raw in SAMPLES:
            normalized = normalize_text(raw)
            assert len(normalized.index_map) == len(normalized.text) + 1

    def test_every_mapped_offset_is_a_valid_original_index(self) -> None:
        for raw in SAMPLES:
            normalized = normalize_text(raw)
            for offset in normalized.index_map:
                assert 0 <= offset <= len(raw)

    def test_map_is_monotonic_non_decreasing(self) -> None:
        for raw in SAMPLES:
            normalized = normalize_text(raw)
            offsets = list(normalized.index_map)
            assert offsets == sorted(offsets)

    def test_to_original_maps_back_to_matching_substring(self) -> None:
        raw = "Guaranteed\t30%  returns  today."
        normalized = normalize_text(raw)

        start, end = normalized.to_original(0, len(normalized.text))

        # Collapsed whitespace is covered by the span, so the original slice is
        # the same sentence with its original spacing intact.
        assert raw[start:end] == "Guaranteed\t30%  returns  today."
        assert normalized.text == "Guaranteed 30% returns today."

    def test_to_original_round_trips_normalised_offsets(self) -> None:
        raw = "Contact support@example.com for details."
        normalized = normalize_text(raw)
        needle = "support@example.com"
        index = normalized.text.index(needle)

        start, end = normalized.to_original(index, index + len(needle))

        assert raw[start:end] == needle

    def test_to_original_rejects_out_of_range_offsets(self) -> None:
        normalized = normalize_text("Hello world")

        with pytest.raises(ValueError):
            normalized.to_original(-5, 3)
        with pytest.raises(ValueError):
            normalized.to_original(0, 10_000)
        with pytest.raises(ValueError):
            normalized.to_original(5, 2)

    def test_substring_survives_multi_space_and_newline_input(self) -> None:
        raw = "Line one\n\nLine  two   with   spaces."
        normalized = normalize_text(raw)
        # The needle is taken from the normalised text, where runs are collapsed.
        needle = normalized.text[normalized.text.index("Line two") :]

        index = normalized.text.index(needle)
        start, end = normalized.to_original(index, index + len(needle))

        assert raw[start:end] == "Line  two   with   spaces."

    def test_composed_characters_map_to_their_original_start(self) -> None:
        raw = "cafe\u0301 break"
        normalized = normalize_text(raw)

        index = normalized.text.index("caf\u00e9")
        start, end = normalized.to_original(index, index + len("caf\u00e9"))

        assert raw[start:end] == "cafe\u0301"