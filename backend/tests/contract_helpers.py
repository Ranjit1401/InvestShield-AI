"""Shared helpers for the Phase 10 contract, safety and edge-case suites.

Three things live here because three separate test modules would otherwise each
write them and the copies would drift:

- **Semantic comparison** of two API responses, ignoring the fields that
  legitimately differ between a live run and a retrieved one.
- **Secret-shaped string collection** — every string reachable in a response body,
  so a leakage test can assert over *all* of them rather than a hand-picked few.
- **Credential fixtures** — realistic key/DSN strings used to prove a secret is not
  echoed back when it appears in an exception or a configuration error.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

__all__ = [
    "CREDENTIALS",
    "IGNORED_RESPONSE_PATHS",
    "TIMESTAMP_KEYS",
    "collect_strings",
    "credential_probes",
    "iter_json_strings",
    "looks_like_a_secret",
    "semantic_response",
]


#: Response keys that carry wall-clock metadata.
#:
#: A live run stamps `started_at` when the graph starts; a retrieved run reports
#: whatever was stored, which for those two fields is the same instant but for
#: `assessed_at` / `built_at` / `retrieved_at` is the instant Phase 9 wrote back.
#: None of them is part of what the investigation *decided*.
TIMESTAMP_KEYS = frozenset(
    {
        "at",
        "assessed_at",
        "built_at",
        "completed_at",
        "created_at",
        "retrieved_at",
        "searched_at",
        "started_at",
    }
)


#: Dotted paths excluded from a semantic comparison, with the reason each is
#: excluded. An empty mapping means nothing is excluded.
IGNORED_RESPONSE_PATHS: dict[str, str] = {}


def _is_timestamp_key(key: object) -> bool:
    """Whether a mapping key names a wall-clock field.

    Args:
        key: A JSON object key.

    Returns:
        `True` when the key is one of :data:`TIMESTAMP_KEYS`.
    """
    return isinstance(key, str) and key in TIMESTAMP_KEYS


def semantic_response(payload: Any) -> Any:
    """Reduce a response body to the part two runs must agree on.

    Timestamps are removed wherever they appear, at any depth, because they are
    stamped from real clocks by Phases 1-6 and by the graph. Nothing else is
    removed: if a retrieved investigation differs from the live one in any other
    field, that difference is a defect and must fail the comparison.

    Args:
        payload: A decoded JSON body, or any nested part of one.

    Returns:
        A structure of the same shape with every timestamp key dropped.
    """
    if isinstance(payload, dict):
        return {
            key: semantic_response(value)
            for key, value in payload.items()
            if not _is_timestamp_key(key)
        }
    if isinstance(payload, list):
        return [semantic_response(item) for item in payload]
    return payload


def iter_json_strings(payload: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    """Yield every string in a decoded JSON body with the path that reached it.

    Walks the whole body rather than a chosen subset of fields. A leakage test
    that checks only the fields it knows about cannot catch a secret in a field
    added later, which is exactly the regression a safety suite exists to catch.

    Args:
        payload: A decoded JSON body.
        path: Dotted path of `payload`, used to make a failure readable.

    Yields:
        `(path, string)` for every string at any depth.
    """
    if isinstance(payload, str):
        yield path, payload
    elif isinstance(payload, dict):
        for key, value in payload.items():
            yield from iter_json_strings(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from iter_json_strings(value, f"{path}[{index}]")


def collect_strings(payload: Any) -> list[str]:
    """Return every string reachable in a decoded JSON body.

    Args:
        payload: A decoded JSON body.

    Returns:
        Each distinct string, in first-seen order.
    """
    seen: dict[str, None] = {}
    for _, value in iter_json_strings(payload):
        seen.setdefault(value, None)
    return list(seen)


#: Realistic credential-shaped strings. Never real keys — they are the kind of
#: value that appears in a DSN, an `Authorization` header or a provider error
#: body, which is where a leak would come from.
CREDENTIALS: dict[str, str] = {
    "groq_key": "gsk_TESTKEY0123456789abcdefghijklmnopqrstuvwxyz",
    "serpapi_key": "TEST_SERPAPI_KEY_0123456789abcdefghijklmnop",
    "postgres_password": "hunter2-SUPER-SECRET",
    "postgres_dsn": (
        "postgresql+psycopg://investshield:hunter2-SUPER-SECRET@"
        "db.internal.example.invalid:5432/investshield"
    ),
    "bearer_token": "Bearer eyJhbGciOiJIUzI1NiJ9.PROBE.TOKEN",
    "aws_style_key": "AKIAPROBEKEY0000000",
}


def credential_probes() -> list[str]:
    """Every credential value, as a list of strings a leak would expose.

    Returns:
        The values of :data:`CREDENTIALS`.
    """
    return list(CREDENTIALS.values())


#: Patterns that identify a string as credential-shaped even when the exact value
#: is unknown — a driver message can quote the DSN it was configured with without
#: the test having planted that particular string.
_SECRET_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"postgresql(?:\+\w+)?://[^\s:@]+:[^\s@]+@", re.IGNORECASE),
    re.compile(r"mysql(?:\+\w+)?://[^\s:@]+:[^\s@]+@", re.IGNORECASE),
    re.compile(r"sqlite:///[^\s]*[A-Za-z]:\\", re.IGNORECASE),
    re.compile(r"\bgsk_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*\S{8,}"),
    re.compile(r"(?i)\bpassword\s*[=:]\s*\S+"),
)


def looks_like_a_secret(text: str) -> bool:
    """Whether `text` has the shape of a credential.

    A shape check rather than an exact-value check, because a driver or provider
    formats a secret into a message we never planted and would therefore not be
    caught by comparing against our own fixtures.

    Args:
        text: A string from a response body.

    Returns:
        `True` when the string looks like it carries a credential.
    """
    return any(pattern.search(text) for pattern in _SECRET_SHAPES)
