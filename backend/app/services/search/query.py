"""Query normalization (Phase 3, §10).

A search query that has been through user input, a claim string, or an entity
name needs predictable cleanup before it is sent to a provider. That cleanup is
purely mechanical and **never uses an LLM** (project rule 4: LLM only where
reasoning or language understanding is genuinely required).

What it does:

* strips control and invisible characters, converting tabs and newlines to a
  single space so a multi-line paste becomes one query
* collapses whitespace runs and trims
* caps length at a word boundary
* leaves every meaningful character alone

What it must never do:

* rewrite names, financial identifiers, or URLs. ``acmefunds@okhdfcbank``,
  ``INH0000123456`` and ``https://example.com/a`` pass through untouched.
* invent, expand, translate or "improve" terms. Paraphrasing a query is how a
  search for one claim silently becomes a search for a different claim.
"""

from __future__ import annotations

import re
import unicodedata

#: Control characters and C1 range. Removed outright; tabs and newlines are
#: treated as whitespace first so that pasted multi-line text stays readable.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

#: Zero-width and bidi-control characters, invisible but able to break an exact
#: match against a later authoritative page.
_INVISIBLE = re.compile(r"[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")

_WHITESPACE_RUN = re.compile(r"\s+")

#: Hard cap on query length. Providers reject long queries, and an unbounded
#: query is also an unbounded request surface.
MAX_QUERY_LENGTH = 512

#: Characters that make a query useless rather than merely long.
_MIN_MEANINGFUL_LENGTH = 2


def normalize_query(raw: str | None, max_length: int = MAX_QUERY_LENGTH) -> str:
    """Normalize `raw` into a clean, single-line search query.

    Args:
        raw: Raw query text from a claim, entity name or user input.
        max_length: Maximum characters to keep; longer queries are truncated at
            the last word boundary. Non-positive values disable truncation.

    Returns:
        The normalized query, or ``""`` when nothing usable remains.
    """
    if not raw:
        return ""

    text = unicodedata.normalize("NFC", str(raw))
    # Tabs and newlines are whitespace, not content: fold them before stripping
    # the remaining control characters.
    text = text.replace("\t", " ").replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = _CONTROL.sub(" ", text)
    text = _INVISIBLE.sub("", text)
    text = _WHITESPACE_RUN.sub(" ", text).strip()

    if max_length > 0 and len(text) > max_length:
        text = _truncate_at_word_boundary(text, max_length)

    return text


def _truncate_at_word_boundary(text: str, max_length: int) -> str:
    """Cut `text` to at most `max_length` characters on a word boundary.

    Args:
        text: Text to shorten.
        max_length: Character budget.

    Returns:
        The shortened text, without a trailing space.
    """
    clipped = text[:max_length]
    last_space = clipped.rfind(" ")
    if last_space > max_length // 2:
        clipped = clipped[:last_space]
    return clipped.rstrip()


def is_meaningful_query(query: str) -> bool:
    """Return True when `query` is worth sending to a provider.

    A query of only punctuation or invisible characters cannot return useful
    results, and sending it would waste quota while implying we searched.

    Args:
        query: A normalized query.

    Returns:
        Whether the query contains at least one alphanumeric character.
    """
    return any(char.isalnum() for char in query)


__all__ = ["MAX_QUERY_LENGTH", "is_meaningful_query", "normalize_query"]