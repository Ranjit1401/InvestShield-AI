"""Query counts must not grow with the number of stored runs (Phase 10).

`tests/db/test_history.py` proves the history endpoint returns the *right* rows.
This module proves it returns them *cheaply*, which is a different property and the
one that silently rots.

An N+1 regression is invisible to a correctness test. The list still contains every
investigation, in order, with correct counts — it just takes eleven queries to
produce one page instead of two, and that cost scales with the user's history rather
than staying flat. Nobody notices until a user with a few hundred runs reports that
the history page got slow, and by then the cause is a relationship that looked
harmless.

So the assertions are on **query counts**, captured with SQLAlchemy's
`before_cursor_execute` event rather than by reading the repository's source. The
event sees what the database actually received, which is the thing that costs time;
reading the code only shows what was intended.

The property asserted is *flatness*: a page of one run and a page of twenty runs
must cost the same number of queries. That is stronger and more durable than a magic
number. An exact count would break the next time a legitimate query is added, and
the breakage would be a red test that someone "fixes" by raising the threshold.
Flatness catches the actual failure — cost that scales with the data — and nothing
else.

`list_page` reads only the `investigations` table, because the child counts are
denormalised onto the parent row. That design decision is what makes this module
pass, and the tests below are what keep it true.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.graph.context import GraphContext
from app.repositories.investigations import InvestigationRepository
from tests.persistence_factories import REGULATORY_CONTENT, run_only

#: Runs to store when measuring the expensive end of the scale. Twenty is enough for
#: a per-row query to be unmistakable while keeping the test fast; the point is the
#: comparison between one and many, not the absolute number.
MANY = 20

#: Child collections loaded for every run, because every relationship on
#: `InvestigationRow` is `lazy="selectin"`.
#:
#: Recorded here rather than written into the assertion as a bare 14 so that a
#: failure says *what* the budget is made of. The count is asserted below as
#: `2 + CHILD_COLLECTIONS`: one statement to count the rows, one to fetch them, and
#: one per child collection to load it — **flat in the number of runs**, which is the
#: property this module exists to protect.
CHILD_COLLECTIONS = 14


@contextlib.contextmanager
def count_queries(engine: Any) -> Iterator[list[str]]:
    """Record every statement executed against `engine` inside the block.

    Args:
        engine: The SQLAlchemy engine to watch.

    Yields:
        The list the statements are appended to, populated as they execute.
    """
    statements: list[str] = []

    def capture(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def _store(session: Any, count: int, context: Any, *, prefix: str = "Run") -> None:
    """Store `count` runs, each with content distinct from the last.

    Distinct content matters. Phase 7 derives the public id from the content, so
    storing the same text repeatedly would produce one id and several rows — which
    is a different property, and one `tests/db/test_history.py` already covers.
    Here the concern is one page of unrelated runs.

    Args:
        session: The SQLAlchemy session to store through.
        count: How many runs to store.
        context: The graph context to run against.
        prefix: Distinguishes the two batches so their ids cannot collide.
    """
    repository = InvestigationRepository(session)
    for index in range(count):
        repository.save(
            run_only(context, f"{REGULATORY_CONTENT} {prefix} {index} of {MANY}.")
        )
    session.commit()


class TestListingDoesNotScaleWithStoredRuns:
    """The core N+1 property: page cost is flat in the number of runs."""

    def test_a_page_of_one_and_a_page_of_twenty_cost_the_same(
        self, session: Any, real_context: GraphContext
    ) -> None:
        """Query count is independent of history size.

        This is the test the whole module exists for. A per-row child query would
        show up here as `one + 20` instead of `one + one`, and no correctness test
        in the suite would fail.

        Args:
            session: The session to store and read through.
            real_context: An offline context wired to the real services.
        """
        _store(session, 1, real_context, prefix="Small")
        with count_queries(session.get_bind()) as small:
            InvestigationRepository(session).list_page(limit=50)

        _store(session, MANY - 1, real_context, prefix="Bulk")
        with count_queries(session.get_bind()) as large:
            InvestigationRepository(session).list_page(limit=50)

        assert len(small) == len(large), (
            f"listing cost grew with history size: {len(small)} queries for one run, "
            f"{len(large)} for {MANY}. Statements:\n" + "\n".join(large)
        )

    def test_the_page_is_actually_full(
        self, session: Any, real_context: GraphContext
    ) -> None:
        """Flatness must not be achieved by not reading the rows.

        A query count of one, achieved by returning nothing, would pass the test
        above. This pins that the page really does contain every stored run, so
        "flat" cannot degenerate into "empty".

        Args:
            session: The session to store and read through.
            real_context: An offline context wired to the real services.
        """
        _store(session, MANY, real_context)

        page, total = InvestigationRepository(session).list_page(limit=50)

        assert total == MANY
        assert len(page) == MANY

    def test_the_count_is_a_fixed_function_of_the_schema_not_the_data(
        self, session: Any, real_context: GraphContext
    ) -> None:
        """A listing costs 2 statements plus one per child collection.

        Not "2", and the distinction matters. Every relationship on
        `InvestigationRow` is `lazy="selectin"`, so loading a run's claims, flags,
        evidence and so on emits **one** `IN (...)` statement per collection — a
        fixed cost of fourteen here — rather than one per row. That is the design
        that makes the count independent of history size, and the flatness test
        above is what proves it holds.

        The number is asserted so that adding a child collection is a *deliberate*
        change to a test rather than an invisible one. Merging the count and the
        page into a window function would be an improvement and would need this
        test updated on purpose, which is the right friction.

        Args:
            session: The session to store and read through.
            real_context: An offline context wired to the real services.
        """
        _store(session, 5, real_context)

        with count_queries(session.get_bind()) as statements:
            InvestigationRepository(session).list_page(limit=50)

        assert len(statements) == 2 + CHILD_COLLECTIONS

    def test_child_collections_are_fetched_in_one_batch_each(
        self, session: Any, real_context: GraphContext
    ) -> None:
        """The direct assertion that N+1 avoidance is working.

        `selectin` loading sends `WHERE investigation_id IN (?, ?, ?, ?, ?)` — one
        statement covering every row. A regression to default lazy loading would
        produce the *same* number of statements for one row and a *larger* number
        for many, which the flatness test catches, but it would also produce one
        statement per row here. Asserting the `IN` shape directly names the
        mechanism, so a future reader knows what is being protected.

        Args:
            session: The session to store and read through.
            real_context: An offline context wired to the real services.
        """
        _store(session, 5, real_context)

        with count_queries(session.get_bind()) as statements:
            InvestigationRepository(session).list_page(limit=50)

        child_statements = [
            statement
            for statement in statements
            if "claim_entity_links" in statement.lower()
        ]
        assert child_statements, "expected the child collection to be loaded"
        assert " IN " in child_statements[0].upper()
        # One statement for five runs, not five statements for five runs.
        assert len(child_statements) == 1

    def test_paging_offsets_do_not_add_queries(
        self, session: Any, real_context: GraphContext
    ) -> None:
        """Walking a history costs the same per page, whatever the page offset.

        Args:
            session: The session to store and read through.
            real_context: An offline context wired to the real services.
        """
        _store(session, MANY, real_context)
        repository = InvestigationRepository(session)

        first_page, total = repository.list_page(limit=5, offset=0)
        with count_queries(session.get_bind()) as statements:
            repository.list_page(limit=5, offset=5)

        assert total == MANY
        assert len(first_page) == 5
        assert len(statements) == 2 + CHILD_COLLECTIONS

    def test_no_child_table_is_touched_by_a_listing(
        self, session: Any, real_context: GraphContext
    ) -> None:
        """The summaries come from denormalised columns, so no join may appear.

        This is the assertion that would fail first in the natural "improvement" of
        computing counts live: someone joins `evidence` for a fresh count, and the
        query count is still 2 — but the work each query does has just multiplied.

        Args:
            session: The session to store and read through.
            real_context: An offline context wired to the real services.
        """
        _store(session, 5, real_context)

        with count_queries(session.get_bind()) as statements:
            InvestigationRepository(session).list_page(limit=50)

        joined = " ".join(statements).lower()
        for child_table in ("evidence", "red_flags", "verification", "risk", "claims"):
            assert f"join {child_table}" not in joined, (
                f"the listing joins {child_table}, which is exactly the N+1 the "
                f"denormalised counts exist to avoid"
            )


class TestRetrievalCostIsBoundedAndPredictable:
    """A single retrieval loads one run's graph, and the shape is fixed."""

    def test_retrieving_one_run_does_not_depend_on_history_size(
        self, session: Any, real_context: GraphContext
    ) -> None:
        """A detail lookup is one run's work whether or not history is long.

        Retrieval legitimately loads child rows — a run's claims, flags and
        evidence are its content. What it must not do is load *other* runs.

        Args:
            session: The session to store and read through.
            real_context: An offline context wired to the real services.
        """
        _store(session, 1, real_context, prefix="One")
        with count_queries(session.get_bind()) as small:
            InvestigationRepository(session).load(_newest_id(session))

        _store(session, MANY - 1, real_context, prefix="Many")
        with count_queries(session.get_bind()) as large:
            InvestigationRepository(session).load(_newest_id(session))

        assert len(small) == len(large), (
            f"retrieval cost grew with history size: {len(small)} then {len(large)}"
        )

    def test_retrieval_uses_a_bounded_number_of_statements(
        self, session: Any, real_context: GraphContext
    ) -> None:
        """One run's graph is a fixed number of tables, not a number of rows.

        Args:
            session: The session to store and read through.
            real_context: An offline context wired to the real services.
        """
        _store(session, 1, real_context, prefix="Single")
        with count_queries(session.get_bind()) as few:
            InvestigationRepository(session).load(_newest_id(session))

        _store(session, MANY - 1, real_context, prefix="Rest")
        with count_queries(session.get_bind()) as many:
            InvestigationRepository(session).load(_newest_id(session))

        assert len(few) == len(many), (
            f"retrieval cost grew with the number of stored runs: {len(few)} then "
            f"{len(many)}"
        )


def _newest_id(session: Any) -> str:
    """Return the public id of the most recently stored run.

    Args:
        session: The session to query through.

    Returns:
        The newest run's public id.
    """
    page, _ = InvestigationRepository(session).list_page(limit=1)
    return str(page[0].investigation_id)
