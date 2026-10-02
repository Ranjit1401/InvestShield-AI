"""The history endpoint must stay flat as a client's history grows (Phase 10).

`tests/db/test_query_efficiency.py` proves the flatness at the repository, where it
is designed in. This module proves it end to end, through the route a client
actually calls, because the two can diverge: a route can call `list_page` twice, or
add a per-row lookup of its own after the repository returns.

Measuring at the HTTP boundary is also the only place the *response size* claim
holds. A query count of two is not cheap if each row drags its evidence along, and
an N+1 that moved into the adapter rather than the repository would pass every
repository-level test.

The API suite's `api_client` runs the real graph against real offline services and
persists to a temporary SQLite file, so these are genuine write-then-read round
trips rather than hand-placed rows.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from fastapi.testclient import TestClient

from tests.persistence_factories import REGULATORY_CONTENT

#: How many runs to store before measuring the expensive end of the scale.
BULK = 10

#: Fields a list row must never carry.
#:
#: These are the run's payload. A summary that embedded them would make the
#: response grow with the size of each investigation even though the query count
#: stayed flat — the same cost problem by a different route.
PAYLOAD_FIELDS = (
    "claims",
    "entities",
    "claim_entity_links",
    "red_flags",
    "verification_results",
    "evidence",
    "timeline",
    "warnings",
    "errors",
    "risk_assessment",
    "extracted_text",
    "raw_input",
)


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

    event_listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event_remove(engine, "before_cursor_execute", capture)


def event_listen(engine: Any, name: str, fn: Any) -> None:
    """Attach a SQLAlchemy engine event listener.

    Args:
        engine: The engine to attach to.
        name: The event name.
        fn: The listener.
    """
    from sqlalchemy import event

    event.listen(engine, name, fn)


def event_remove(engine: Any, name: str, fn: Any) -> None:
    """Detach a SQLAlchemy engine event listener.

    Args:
        engine: The engine to detach from.
        name: The event name.
        fn: The listener.
    """
    from sqlalchemy import event

    event.remove(engine, name, fn)


def _post(client: TestClient, text: str) -> dict[str, Any]:
    """Submit one investigation and return the response body.

    Args:
        client: The client to submit through.
        text: Content to investigate.

    Returns:
        The decoded response body.
    """
    response = client.post("/api/investigations/text", json={"text": text})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _distinct(index: int) -> str:
    """Return content that is unique to `index`.

    Phase 7 derives the public id from the content, so identical text would produce
    one id and several rows. That is a legitimate design and
    `tests/api/test_persistence_endpoints.py` covers it; this module needs
    unrelated runs so the page genuinely holds `index + 1` of them.

    Args:
        index: Distinguishes this content from every other.

    Returns:
        Content no other run in these tests uses.
    """
    return f"{REGULATORY_CONTENT} Listing run {index} of {BULK}."


class TestHistoryCostIsFlatOverHttp:
    """Listing must cost the same for one run as for ten."""

    def test_the_endpoint_cost_does_not_grow_with_history(
        self, api_client: TestClient
    ) -> None:
        """Measured on the route, where an adapter-level regression would show.

        Args:
            api_client: A client running the real app, offline, persisting to a
                temporary SQLite file.
        """
        _post(api_client, _distinct(0))
        with count_queries(api_client.app.state.database.engine) as one_run:
            first = api_client.get("/api/investigations")
        assert first.status_code == 200
        assert len(first.json()["investigations"]) == 1

        for index in range(1, BULK):
            _post(api_client, _distinct(index))

        with count_queries(api_client.app.state.database.engine) as many_runs:
            second = api_client.get("/api/investigations")

        assert second.status_code == 200
        assert len(second.json()["investigations"]) == BULK
        assert len(one_run) == len(many_runs), (
            f"the history endpoint scaled with history: {len(one_run)} statements for "
            f"one run, {len(many_runs)} for {BULK}"
        )

    def test_paging_the_endpoint_is_flat_per_page(self, api_client: TestClient) -> None:
        """Each page costs the same, so walking a history stays linear.

        Args:
            api_client: A client running the real app, offline, persisting to a
                temporary SQLite file.
        """
        for index in range(BULK):
            _post(api_client, _distinct(index))

        with count_queries(api_client.app.state.database.engine) as first_page:
            page_one = api_client.get("/api/investigations", params={"limit": 3})
        with count_queries(api_client.app.state.database.engine) as last_page:
            page_two = api_client.get(
                "/api/investigations", params={"limit": 3, "offset": 3}
            )

        assert page_one.status_code == 200
        assert page_two.status_code == 200
        assert len(page_one.json()["investigations"]) == 3
        assert len(page_two.json()["investigations"]) == 3
        assert len(first_page) == len(last_page)

    def test_the_page_really_holds_every_run(self, api_client: TestClient) -> None:
        """Flatness must not come from reading less.

        A route that returned an empty page would issue almost no statements and
        would pass a count-based test perfectly. This is the assertion that stops
        "flat" from meaning "empty".

        Args:
            api_client: A client running the real app, offline, persisting to a
                temporary SQLite file.
        """
        for index in range(BULK):
            _post(api_client, _distinct(index))

        body = api_client.get("/api/investigations", params={"limit": 50}).json()

        assert body["total"] == BULK
        assert len(body["investigations"]) == BULK

    def test_a_list_row_carries_no_evidence_or_claims(
        self, api_client: TestClient
    ) -> None:
        """A summary is a summary.

        `REGULATORY_CONTENT` produces real claims, red flags and evidence, so this
        content is what makes the assertion meaningful: those rows exist in the
        database and are deliberately absent from the list.

        Args:
            api_client: A client running the real app, offline, persisting to a
                temporary SQLite file.
        """
        detailed = _post(api_client, REGULATORY_CONTENT)
        assert detailed["claims"], "the content must produce claims for this to test"
        assert detailed["evidence"], "the content must produce evidence for this to test"

        entry = api_client.get("/api/investigations").json()["investigations"][0]

        for field in PAYLOAD_FIELDS:
            assert field not in entry, (
                f"the history row carries {field}; a summary must stay small even "
                f"when the run behind it is large"
            )

    def test_the_counts_on_a_list_row_agree_with_the_retrieved_run(
        self, api_client: TestClient
    ) -> None:
        """The cheap counts must still be *true*.

        Denormalising counts is what makes the endpoint cheap, and it is also what
        makes it able to be wrong. This ties the two together: the number on the
        list row has to match the thing a client gets when it follows the link.

        Args:
            api_client: A client running the real app, offline, persisting to a
                temporary SQLite file.
        """
        detailed = _post(api_client, REGULATORY_CONTENT)

        entry = next(
            item
            for item in api_client.get("/api/investigations").json()["investigations"]
            if item["investigation_id"] == detailed["investigation_id"]
        )
        retrieved = api_client.get(
            f"/api/investigations/{detailed['investigation_id']}"
        ).json()

        assert entry["claim_count"] == len(retrieved["claims"])
        assert entry["entity_count"] == len(retrieved["entities"])
        assert entry["red_flag_count"] == len(retrieved["red_flags"])
        assert entry["verification_count"] == len(retrieved["verification_results"])
        assert entry["evidence_count"] == len(retrieved["evidence"])
        assert entry["status"] == retrieved["status"]


class TestDuplicateContentDoesNotDisturbTheListing:
    """Repeated identical content is a real client behaviour, not an edge case.

    A user re-running the same document is not an anomaly, and Phase 9 made room for
    it with a surrogate key. These tests pin that the resulting rows are still
    listed independently, so a client can see both runs.
    """

    def test_two_runs_of_the_same_content_are_both_listed(
        self, api_client: TestClient
    ) -> None:
        """Same id, two rows, both visible.

        Args:
            api_client: A client running the real app, offline, persisting to a
                temporary SQLite file.
        """
        first = _post(api_client, REGULATORY_CONTENT)
        second = _post(api_client, REGULATORY_CONTENT)

        assert first["investigation_id"] == second["investigation_id"]

        body = api_client.get("/api/investigations").json()
        assert body["total"] == 2
        assert len(body["investigations"]) == 2

    def test_both_runs_keep_their_own_timestamps(self, api_client: TestClient) -> None:
        """The two rows are distinguishable, and that is the point of the surrogate.

        They share a public id by design, so `created_at` is what tells a client
        which run it is looking at.

        Args:
            api_client: A client running the real app, offline, persisting to a
                temporary SQLite file.
        """
        first = _post(api_client, REGULATORY_CONTENT)
        second = _post(api_client, REGULATORY_CONTENT)

        entries = api_client.get("/api/investigations").json()["investigations"]
        stamps = [entry["created_at"] for entry in entries]

        assert len(set(stamps)) == 2, f"the two runs share a timestamp: {stamps}"
        assert stamps == sorted(stamps, reverse=True), "newest first"

    def test_retrieval_returns_the_newest_of_the_shared_id(
        self, api_client: TestClient
    ) -> None:
        """A shared id resolves to one run, and it is the newer one.

        Args:
            api_client: A client running the real app, offline, persisting to a
                temporary SQLite file.
        """
        first = _post(api_client, REGULATORY_CONTENT)
        second = _post(api_client, REGULATORY_CONTENT)

        retrieved = api_client.get(
            f"/api/investigations/{first['investigation_id']}"
        ).json()

        assert retrieved["started_at"] == second["started_at"]
        assert retrieved["started_at"] >= first["started_at"]
