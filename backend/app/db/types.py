"""Portable column types for the persistence layer (Phase 9).

SQLite does not store timezone information, and the Phase 1-6 services all produce
timezone-aware UTC datetimes. Mixing the two is the kind of bug that stays hidden
until the day `DATABASE_URL` points at Neon and Postgres *does* keep the offset —
at which point the same code raises `TypeError: can't subtract offset-naive and
offset-aware datetimes` in production and nowhere else.

`UtcDateTime` closes that gap in one place: values are **written** as naive UTC
and **read** back as aware UTC. Storage stays portable and comparable across
SQLite and PostgreSQL (D-028's portability requirement), and every caller sees
the same aware datetime regardless of which backend answered.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

__all__ = ["UtcDateTime", "as_utc", "utc_now"]


def utc_now() -> datetime:
    """Return the current instant as an aware UTC datetime.

    The single clock this layer writes with. It exists so a test can compare two
    persisted investigations without excluding timestamps the way
    `semantic_view` has to for the live pipeline (D-034) — storage deliberately
    keeps the wall clock rather than stripping it.
    """
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Return `value` as an aware UTC datetime, assuming UTC when naive.

    Args:
        value: A datetime that may have lost its offset in storage.

    Returns:
        An aware datetime, or `None` for `None`. A naive value is *assumed* UTC
        rather than localised, because every naive value this layer stores was
        converted from aware UTC on the way in, so interpreting it in any other
        zone would be wrong rather than merely imprecise.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UtcDateTime(TypeDecorator):
    """A `DateTime` that is always aware UTC in Python, always naive in storage.

    Registered per column rather than globally so a future table storing local
    time, or a date with no time at all, is free to use the plain types.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        """Normalise an aware datetime to naive UTC before it reaches the driver.

        Args:
            value: The datetime being written, or `None`.
            dialect: The bound driver, unused — the stored form is
                backend-independent by design.

        Returns:
            A naive UTC datetime, or `None`.
        """
        aware = as_utc(value)
        return None if aware is None else aware.replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        """Re-attach UTC to a value that storage returned without an offset.

        Args:
            value: The datetime as the driver returned it, or `None`.
            dialect: The bound driver, unused.

        Returns:
            An aware UTC datetime, or `None`.
        """
        return as_utc(value)

    def compare_values(self, left: Any, right: Any) -> bool:
        """Compare two values as instants, ignoring an offset only one side kept.

        SQLite round-trips through a naive value and PostgreSQL through an aware
        one. Comparing the raw attributes would make a `WHERE created_at = :ts`
        filter match on one backend and miss on the other.
        """
        left_aware = as_utc(left)
        right_aware = as_utc(right)
        if isinstance(left_aware, datetime) and isinstance(right_aware, datetime):
            return left_aware == right_aware
        return bool(left == right)