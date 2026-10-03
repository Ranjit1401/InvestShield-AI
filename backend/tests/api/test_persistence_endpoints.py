"""Persistence behaviour of the investigation endpoints (Phase 9).

Phase 8's suite asserts what a run *contains*. This one asserts what happens to
it afterwards: that a `POST` leaves something behind, that the `GET` returns the
same thing, and that the history lists both.

The load-bearing test is `test_retrieval_returns_exactly_what_the_post_returned`.
The two endpoints share `serialize_investigation` and the repository returns an
`InvestigationState` precisely so that comparison is meaningful — if either path
ever reshaped the result, this assertion would be the thing that noticed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_graph_context_dep
from app.core.config import Settings
from app.db.session import Database
from app.main import create_app
from app.repositories.investigations import InvestigationRepository
from tests.persistence_factories import MESSY_CONTENT, REGULATORY_CONTENT
from tests.graph.graph_factories import GraphContext


@pytest.fixture
def persisted_client(
    db_settings: Settings, real_context: GraphContext
) -> Iterator[TestClient]:
    """A client whose requests write to, and read from, a temporary SQLite file.

    The graph context is overridden with the offline one. Without that the app
    would build its production context from `db_settings`, which has no API keys,
    so every run would degrade to "no search available" and the evidence tables
    would go untested — passing for the wrong reason.

    Args:
        db_settings: Isolated settings for this test.
        real_context: The offline graph context requests run against.

    Yields:
        A `TestClient` over an app whose schema has been created.
    """
    app = create_app(db_settings)
    app.dependency_overrides[get_graph_context_dep] = lambda: real_context
    app.state.database.create_all()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def stored_client(
    db_settings: Settings, real_context: GraphContext
) -> Iterator[tuple[TestClient, InvestigationRepository]]:
    """A client sharing one database with an inspectable session.

    Needed by the tests that assert something *was* written, which the client
    alone cannot see. The session is opened after the app has created its schema.

    Args:
        db_settings: Isolated settings for this test.
        real_context: The offline graph context requests run against.

    Yields:
        The client, and a repository on the same database.
    """
    app = create_app(db_settings)
    app.dependency_overrides[get_graph_context_dep] = lambda: real_context
    database: Database = app.state.database
    database.create_all()
    with TestClient(app) as client:
        session: Session = database.session_factory()
        try:
            yield client, InvestigationRepository(session)
        finally:
            session.close()


def _post(client: TestClient, text: str = MESSY_CONTENT) -> dict:
    """Investigate some text and return the response body.

    Args:
        client: The test client.
        text: Content to investigate.

    Returns:
        The parsed response body.
    """
    response = client.post("/api/investigations/text", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


# -- write path ----------------------------------------------------------


def test_a_post_stores_the_run(persisted_client: TestClient) -> None:
    """A successful investigation is persisted, not merely returned.

    The id in the response is a promise that the result can be looked up again.
    Returning one that leads nowhere is what Phase 8 deliberately avoided by not
    offering retrieval at all; Phase 9 removes that reason, so the promise now
    has to hold.
    """
    body = _post(persisted_client)

    retrieved = persisted_client.get(f"/api/investigations/{body['investigation_id']}")

    assert retrieved.status_code == 200


def test_a_partial_run_is_stored_too(persisted_client: TestClient) -> None:
    """A degraded run is kept, because "we could not check this" is a result.

    Refusing to store partial runs would make the one outcome a user most needs
    to revisit — the run that could not reach a provider — the one they can never
    see again (D-006).
    """
    body = _post(persisted_client)

    assert body["status"] in {"COMPLETED", "PARTIAL"}
    retrieved = persisted_client.get(f"/api/investigations/{body['investigation_id']}")

    assert retrieved.status_code == 200
    assert retrieved.json()["limitations"] == body["limitations"]


def test_a_refused_submission_is_not_stored(
    stored_client: tuple[TestClient, InvestigationRepository],
) -> None:
    """A rejected request leaves no row, so history shows only real runs.

    The graph returns a state carrying a recorded error rather than raising, so
    a route that stored every state without checking would fill the history with
    entries describing investigations that never happened.
    """
    client, repository = stored_client
    response = client.post("/api/investigations/text", json={"text": "   "})

    assert response.status_code == 422
    page, total = repository.list_page(limit=20)
    assert total == 0
    assert page == []


def test_an_unsupported_input_type_is_not_stored(
    stored_client: tuple[TestClient, InvestigationRepository],
) -> None:
    """A refused submission is not analysed and not stored either.

    Storing it would produce a history entry describing an analysis of something
    the client never sent — a run that cannot have happened.

    Two refusals are checked, because Phase 12 gave URL failures a third status
    and each must leave the history untouched:

    - an unsupported kind, refused with `422`;
    - a URL this deployment cannot analyse, refused with `503`.

    A `URL` submission is deliberately not in the first case any more. It left
    that set in Phase 12, and `IMAGE` left it in Phase 13, and `PDF`
    left it in Phase 14; all three are now refused by capability
    rather than by kind.
    """
    client, repository = stored_client

    for body, expected in (
        ({"input_type": "PODCAST", "text": "some content"}, 422),
        ({"input_type": "URL", "text": "https://example.invalid/promo"}, 503),
    ):
        response = client.post("/api/investigations", json=body)

        assert response.status_code == expected, f"{body}: {response.text}"
        assert repository.list_page(limit=20)[1] == 0


# -- retrieval -----------------------------------------------------------


def test_retrieval_returns_exactly_what_the_post_returned(
    persisted_client: TestClient,
) -> None:
    """The `GET` body equals the `POST` body, field for field.

    This is the guarantee the whole retrieval design exists to provide, and it
    holds because the repository returns an `InvestigationState` and both routes
    hand it to the same adapter. Comparing the whole body — rather than a few
    fields — is what makes it a real check: a field that storage quietly dropped
    changes this comparison and nothing else.
    """
    created = _post(persisted_client, REGULATORY_CONTENT)

    retrieved = persisted_client.get(f"/api/investigations/{created['investigation_id']}")

    assert retrieved.status_code == 200
    assert retrieved.json() == created


def test_retrieval_preserves_the_risk_assessment_verbatim(
    persisted_client: TestClient,
) -> None:
    """A stored assessment still carries its score, its factors and its caveat.

    The caveat is what stops the score being read as a probability. It is appended
    by `RiskAssessment` during validation, so rebuilding the assessment through
    the model restores it without needing a column to hold it (D-025).
    """
    created = _post(persisted_client, REGULATORY_CONTENT)
    assert created["risk_assessment"] is not None

    body = persisted_client.get(
        f"/api/investigations/{created['investigation_id']}"
    ).json()

    assert body["risk_assessment"] == created["risk_assessment"]


def test_retrieval_preserves_evidence_and_its_provenance(
    persisted_client: TestClient,
) -> None:
    """Every evidence item comes back with the source it was actually read from.

    The excerpt, the origin field and the URL together are what make an item
    checkable by a reader. A retrieval that returned the excerpt but lost the
    document would look complete and be unauditable.
    """
    created = _post(persisted_client, REGULATORY_CONTENT)
    items = [item for response in created["evidence"] for item in response["evidence"]]
    assert items, "fixture must produce evidence or the test proves nothing"

    body = persisted_client.get(
        f"/api/investigations/{created['investigation_id']}"
    ).json()
    stored_items = [item for response in body["evidence"] for item in response["evidence"]]

    assert stored_items == items
    for item in stored_items:
        assert item["excerpt"]
        assert item["url"]
        assert item["source_id"]


def test_an_unknown_id_is_a_404_with_the_standard_envelope(
    persisted_client: TestClient,
) -> None:
    """A miss uses the same error shape as every other failure.

    A client handling one envelope handles a missing investigation too. The
    detail is `null` and the id is not echoed, so the endpoint cannot be used to
    find out which ids exist.
    """
    response = persisted_client.get("/api/investigations/inv_ffffffffffffffff")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "INVESTIGATION_NOT_FOUND"
    assert body["error"]["detail"] is None
    assert "inv_ffffffffffffffff" not in response.text


def test_retrieval_reports_the_run_as_typed_not_reinterpreted(
    persisted_client: TestClient,
) -> None:
    """The stored `input_type` is the one that was submitted.

    Recovering the type from the stored text instead would let a `URL` submission
    come back looking like text, which is the confusion Phase 8's typed endpoint
    exists to prevent.
    """
    created = persisted_client.post(
        "/api/investigations", json={"input_type": "TEXT", "text": REGULATORY_CONTENT}
    ).json()

    body = persisted_client.get(
        f"/api/investigations/{created['investigation_id']}"
    ).json()

    assert body["input_type"] == "TEXT"


def test_the_newest_run_wins_for_a_shared_id(persisted_client: TestClient) -> None:
    """A `GET` answers with the run the client most recently caused.

    A client's flow is `POST` then `GET` with the id it was just handed. Returning
    the older of two runs under a shared content digest would make it see a
    result that disagrees with the one it was just given.
    """
    first = _post(persisted_client, MESSY_CONTENT)
    second = _post(persisted_client, MESSY_CONTENT)

    assert first["investigation_id"] == second["investigation_id"]
    body = persisted_client.get(
        f"/api/investigations/{first['investigation_id']}"
    ).json()

    assert body == second


# -- history -------------------------------------------------------------


def test_history_is_empty_before_anything_runs(persisted_client: TestClient) -> None:
    """A fresh database lists nothing and reports a zero total."""
    response = persisted_client.get("/api/investigations")

    assert response.status_code == 200
    body = response.json()
    assert body["investigations"] == []
    assert body["total"] == 0
    assert body["limit"] == 20
    assert body["offset"] == 0


def test_history_lists_a_submitted_run(persisted_client: TestClient) -> None:
    """A run appears in the history with the id needed to retrieve it."""
    created = _post(persisted_client, REGULATORY_CONTENT)

    body = persisted_client.get("/api/investigations").json()

    assert body["total"] == 1
    entry = body["investigations"][0]
    assert entry["investigation_id"] == created["investigation_id"]
    assert entry["status"] == created["status"]
    assert entry["input_type"] == "TEXT"


def test_history_reports_counts_that_match_the_retrieved_run(
    persisted_client: TestClient,
) -> None:
    """The summary's counts describe the investigation it points at.

    They are stored on the run rather than aggregated per request, so this is a
    check that the denormalisation is faithful. A summary claiming two claims for
    an investigation that has one would send a client to a result that does not
    match what it was promised.
    """
    created = _post(persisted_client, REGULATORY_CONTENT)
    entry = persisted_client.get("/api/investigations").json()["investigations"][0]

    assert entry["claim_count"] == len(created["claims"])
    assert entry["red_flag_count"] == len(created["red_flags"])
    assert entry["entity_count"] == len(created["entities"])
    assert entry["evidence_count"] == sum(
        len(response["evidence"]) for response in created["evidence"]
    )
    assert entry["factor_count"] == len(created["risk_assessment"]["factors"])


def test_history_is_newest_first(persisted_client: TestClient) -> None:
    """The most recent run is first, which is the reason a client asks."""
    first = _post(persisted_client, "First submission concerning Acme Capital Advisors.")
    second = _post(persisted_client, "Second submission concerning Beta Holdings Ltd.")
    third = _post(persisted_client, "Third submission concerning Gamma Ventures LLP.")

    entries = persisted_client.get("/api/investigations").json()["investigations"]

    assert [entry["investigation_id"] for entry in entries] == [
        third["investigation_id"],
        second["investigation_id"],
        first["investigation_id"],
    ]


def test_history_pages_without_repeating_or_skipping(
    persisted_client: TestClient,
) -> None:
    """Walking the history in pages visits every run exactly once.

    Ordering is `created_at` with the surrogate key as a tie-break, so two runs
    stored in the same millisecond still have a defined order. Without it the
    database may return tied rows differently per query, and a client paging
    would see one row twice and skip another.
    """
    created = [
        _post(persisted_client, f"Submission {index} concerning Acme Capital Advisors.")
        for index in range(5)
    ]

    seen: list[str] = []
    offset = 0
    while True:
        page = persisted_client.get(
            "/api/investigations", params={"limit": 2, "offset": offset}
        ).json()
        assert page["total"] == 5
        if not page["investigations"]:
            break
        seen.extend(entry["investigation_id"] for entry in page["investigations"])
        offset += 2

    assert len(seen) == len(set(seen)) == 5
    assert set(seen) == {body["investigation_id"] for body in created}


def test_history_rejects_a_limit_outside_its_range(
    persisted_client: TestClient,
) -> None:
    """`limit` is bounded, so one request cannot ask for the whole table.

    A `limit` of zero would return nothing and a very large one would make the
    endpoint a full table scan dressed as a page. Both are refused with the same
    `422` envelope as any other invalid request.
    """
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        response = persisted_client.get("/api/investigations", params=params)

        assert response.status_code == 422, params
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_both_runs_of_the_same_content_are_listed(
    persisted_client: TestClient,
) -> None:
    """Investigating the same text twice produces two history entries.

    The second run is the evidence that this content was checked twice. Collapsing
    them — which keying history on the content digest would do — would make the
    endpoint unable to answer the question it exists for.
    """
    first = _post(persisted_client, MESSY_CONTENT)
    second = _post(persisted_client, MESSY_CONTENT)

    body = persisted_client.get("/api/investigations").json()

    assert first["investigation_id"] == second["investigation_id"]
    assert body["total"] == 2


# -- interaction with the Phase 8 contract --------------------------------


def test_the_limits_endpoint_is_not_shadowed_by_the_id_route(
    persisted_client: TestClient,
) -> None:
    """`/investigations/limits` still answers as the limits document.

    FastAPI matches routes in declaration order. Registering
    `/investigations/{investigation_id}` first would have made this path resolve
    to the parameterised route and return a `404` about a missing investigation —
    a regression in a Phase 8 endpoint caused entirely by adding a Phase 9 one.
    """
    response = persisted_client.get("/api/investigations/limits")

    assert response.status_code == 200
    assert response.json()["supported_input_types"] == ["TEXT", "URL", "IMAGE", "PDF"]


def test_phase_eight_response_shape_is_unchanged(
    persisted_client: TestClient,
) -> None:
    """Persisting a run added no field to the write response.

    A client written against Phase 8 must keep working. `persisted` is
    deliberately *not* a new field: the id was already there and is what the
    client uses to retrieve, so a flag saying "this was stored" would be a second
    answer to a question the id already answers.
    """
    body = _post(persisted_client, REGULATORY_CONTENT)

    assert set(body) == {
        "investigation_id",
        "status",
        "input_type",
        "language",
        "current_stage",
        "claims",
        "entities",
        "red_flags",
        "verification_results",
        "evidence",
        "risk_assessment",
        "timeline",
        "limitations",
        "warnings",
        "errors",
        "started_at",
        "completed_at",
        # Phase 12: null for a TEXT run, populated for a URL one.
        "url_source",
        # Phase 13: null unless the run was a screenshot.
        "image_source",
        # Phase 14: null unless the run was a PDF.
        "pdf_source",
    }


def test_history_never_exposes_anything_the_write_response_does_not(
    persisted_client: TestClient,
) -> None:
    """A summary leaks no raw input and no free text.

    The history is a list view a client will render without reading each run in
    full, so it must carry only what a list legitimately needs. It holds no
    `raw_input`, no excerpts and no provider wording — a summary is not a place
    to restate content.
    """
    _post(persisted_client, MESSY_CONTENT)

    entry = persisted_client.get("/api/investigations").json()["investigations"][0]

    assert set(entry) == {
        "investigation_id",
        "status",
        "input_type",
        "current_stage",
        "started_at",
        "completed_at",
        "created_at",
        "claim_count",
        "entity_count",
        "red_flag_count",
        "verification_count",
        "source_count",
        "evidence_count",
        "factor_count",
    }
    assert MESSY_CONTENT not in str(entry)


def test_a_storage_failure_is_reported_rather_than_swallowed(
    db_settings: Settings, real_context: GraphContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that cannot be stored is a `500`, not a `200` that lost its result.

    The run itself succeeded, so returning `200` while discarding the result
    would tell the client its investigation is safe to look up later when it is
    not. The client's only correct response to a `500` is to retry, which is the
    honest outcome.
    """
    from app.repositories.investigations import InvestigationRepository

    def refuse(self: InvestigationRepository, state: dict) -> None:
        raise RuntimeError("storage is unavailable")

    monkeypatch.setattr(InvestigationRepository, "save", refuse)

    app = create_app(db_settings)
    app.dependency_overrides[get_graph_context_dep] = lambda: real_context
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/investigations/text", json={"text": MESSY_CONTENT})

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    # The driver's own message must not reach the client.
    assert "storage is unavailable" not in response.text
