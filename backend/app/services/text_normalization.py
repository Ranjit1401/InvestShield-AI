"""Text normalisation with a reversible offset map (Phase 2, §20).

Extraction runs on normalised text (Unicode NFC, collapsed whitespace, no
control characters), but **every evidence span is reported against the original
input**. `NormalizedText` carries the index map that makes this exact rather
than approximate.

Without this, normalising `"Join   our   group"` into `"Join our group"` would
shift every offset after it, and a span computed against the normalised string
would silently point at the wrong characters in the original — the exact failure
the product brief forbids.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field

#: Control characters that carry no meaning for extraction. CR is included so a
#: Windows line ending collapses cleanly.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0d\x0e-\x1f\x7f]")

#: Zero-width and other invisible format characters that some social media copy
#: injects. They carry no meaning for extraction and would corrupt offsets.
_ZERO_WIDTH = frozenset("\u00ad\u200b\u200c\u200d\u2060\ufeff")

#: Unicode general categories for combining marks.
_COMBINING_CATEGORIES = frozenset({"Mn", "Mc", "Me"})


def _is_invisible(char: str) -> bool:
    """Whether `char` is dropped before normalisation.

    Args:
        char: A single character.

    Returns:
        True for control characters and invisible format characters.
    """
    return bool(_CONTROL.match(char)) or char in _ZERO_WIDTH


def _is_combining(char: str) -> bool:
    """Whether `char` is a combining mark that belongs to the previous letter.

    Args:
        char: A single character.

    Returns:
        True for non-spacing, spacing and enclosing marks.
    """
    return unicodedata.category(char) in _COMBINING_CATEGORIES


def _iter_grapheme_units(raw: str) -> Iterator[tuple[int, str]]:
    """Yield ``(original_offset, composed_text)`` units for NFC composition.

    Unicode NFC cannot be applied character by character: a combining mark
    composes with the *preceding* base character, so normalising each character
    in isolation would leave `"e" + combining acute` uncombined. Units are
    therefore emitted as a base character plus any combining marks that follow
    it, which is exactly the scope over which composition is meaningful.

    Args:
        raw: The original input text.

    Yields:
        ``(original_offset, composed)`` pairs in source order. Every character of
        a unit maps to that unit's original offset, so the mapping stays
        monotonic and inside the original string.
    """
    unit: list[str] = []
    origin = 0
    for position, char in enumerate(raw):
        if _is_invisible(char):
            continue
        if _is_combining(char):
            # Attach to the pending base character; it composes with it.
            unit.append(char)
            continue
        if unit:
            yield origin, unicodedata.normalize("NFC", "".join(unit))
        unit = [char]
        origin = position
    if unit:
        yield origin, unicodedata.normalize("NFC", "".join(unit))


@dataclass(frozen=True)
class NormalizedText:
    """Normalised text plus a map back to original character offsets.

    Attributes:
        original: The untouched input. All spans index into this.
        text: The normalised string that extraction scans.
        index_map: ``index_map[i]`` is the offset in ``original`` of
            ``text[i]``. Has ``len(text) + 1`` entries so that end offsets can be
            mapped too.
    """

    original: str
    text: str
    index_map: tuple[int, ...]
    _reverse_map: dict[int, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Build the reverse offset map once."""
        reverse: dict[int, int] = {}
        for normalized_index, original_index in enumerate(self.index_map):
            reverse.setdefault(original_index, normalized_index)
        object.__setattr__(self, "_reverse_map", reverse)

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        """Map a normalised ``[start, end)`` range back to original offsets.

        Args:
            start: Start offset in `text`.
            end: End offset in `text`.

        Returns:
            ``(original_start, original_end)``.

        Raises:
            ValueError: If the range lies outside `text`.
        """
        if start < 0 or end > len(self.text) or start > end:
            raise ValueError(f"invalid normalised range ({start}, {end})")
        return self.index_map[start], self.index_map[end]

    def to_normalized(self, start: int, end: int) -> tuple[int, int] | None:
        """Map original offsets into the normalised string.

        Args:
            start: Start offset in `original`.
            end: End offset in `original`.

        Returns:
            ``(text_start, text_end)``, or ``None`` when the original range falls
            inside a collapsed whitespace run and has no exact normalised
            counterpart.
        """
        if start < 0 or end > len(self.original) or start > end:
            return None
        try:
            return self._reverse_map[start], self._reverse_map[end]
        except KeyError:
            return None

    @property
    def warnings(self) -> tuple[str, ...]:
        """Notes about normalisation that affected the text."""
        notes: list[str] = []
        if len(self.text) != len(self.original):
            notes.append(
                "Input text was normalised (Unicode and whitespace); all evidence spans "
                "are reported against the original text."
            )
        return tuple(notes)


def normalize_text(raw: str) -> NormalizedText:
    """Normalise `raw` and build the map back to original offsets.

    Normalisation is deliberately minimal and lossless with respect to character
    order: NFC composition, removal of control and zero-width characters, and
    collapsing of *horizontal* whitespace runs to a single space.

    **Newlines are preserved.** Collapsing them would destroy the blank-line
    paragraph structure that sentence segmentation relies on, silently merging
    unrelated lines into one "sentence" — and with it, one giant claim.

    Args:
        raw: The original input text.

    Returns:
        A :class:`NormalizedText` whose `text` is safe to scan and whose
        `index_map` maps every normalised character back to the original.
    """
    chars: list[str] = []
    index_map: list[int] = []
    pending_space = False
    # The emitted space maps to the *last* whitespace character of the original
    # run. Mapping it to the following character instead would make every span
    # that ends before a space include that space.
    space_origin = 0

    for origin, unit in _iter_grapheme_units(raw):
        for composed in unit:
            if composed == "\n":
                pending_space = False
                chars.append("\n")
                index_map.append(origin)
            elif composed.isspace():
                pending_space = True
                space_origin = origin
            else:
                if pending_space and chars and chars[-1] not in (" ", "\n"):
                    chars.append(" ")
                    index_map.append(space_origin)
                pending_space = False
                chars.append(composed)
                index_map.append(origin)

    # Trim leading/trailing whitespace by rebuilding the map slices.
    start_offset = 0
    end_offset = len(chars)
    while start_offset < end_offset and chars[start_offset].isspace():
        start_offset += 1
    while end_offset > start_offset and chars[end_offset - 1].isspace():
        end_offset -= 1

    chars = chars[start_offset:end_offset]
    index_map = index_map[start_offset:end_offset]

    # Sentinel so an end offset at the very end of the string is mappable.
    index_map.append(len(raw))

    return NormalizedText(original=raw, text="".join(chars), index_map=tuple(index_map))


__all__ = ["NormalizedText", "normalize_text"]
