"""Shared schema primitives.

`EvidenceSpan` is the single definition used by every stage that must point back
to an exact location in the original submitted text. It was introduced with the
Phase 1 red-flag schemas and is shared from Phase 2 onwards.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceSpan(BaseModel):
    """Exact location of a detection or extraction inside the original text.

    `start`/`end` are character offsets into the **original** input string, so
    ``original_text[start:end] == text`` always holds for a correctly aligned
    span. Offsets are never computed against a normalised copy of the input.

    Attributes:
        start: Inclusive character offset into the original input string.
        end: Exclusive character offset into the original input string.
        text: The verbatim substring ``original[start:end]``.
    """

    model_config = ConfigDict(frozen=True)

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str

    @model_validator(mode="after")
    def _validate_span(self) -> EvidenceSpan:
        """Ensure the span is a non-empty forward range."""
        if self.end <= self.start:
            raise ValueError("evidence span end must be greater than start")
        return self


def make_span(original: str, start: int, end: int) -> EvidenceSpan:
    """Build a span from offsets, slicing the text verbatim.

    Args:
        original: The original submitted text the offsets refer to.
        start: Inclusive start offset.
        end: Exclusive end offset.

    Returns:
        An :class:`EvidenceSpan` whose ``text`` is exactly
        ``original[start:end]``.

    Raises:
        ValueError: If the range is invalid or lies outside ``original``.
    """
    if start < 0 or end <= start or end > len(original):
        raise ValueError(f"invalid span ({start}, {end}) for text of length {len(original)}")
    return EvidenceSpan(start=start, end=end, text=original[start:end])


def span_slice_matches(original: str, span: EvidenceSpan) -> bool:
    """Return True when the span still slices exactly out of ``original``.

    Args:
        original: The original submitted text.
        span: The span to verify.

    Returns:
        Whether ``original[span.start:span.end] == span.text``.
    """
    if span.start < 0 or span.end > len(original) or span.end <= span.start:
        return False
    return original[span.start : span.end] == span.text


__all__ = ["EvidenceSpan", "make_span", "span_slice_matches"]
