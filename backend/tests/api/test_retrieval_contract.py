"""What `GET /api/investigations/{id}` guarantees, stated field by field (Phase 10).

`tests/api/test_persistence_endpoints.py` asserts the strongest possible thing —
that the retrieved body *equals* the posted body. That test passes today and is
worth keeping. But an equality assertion does not say *why* it holds, or what it
depends on, and the documentation had drifted past what was actually guaranteed.
This module states the contract in the terms a client can rely on.

## The contract

**Semantic equivalence.** Every substantive field of the investigation survives
storage and comes back equal: the claims, the entities and their relationships, the
red flags with their spans, the verification results, the evidence with its
provenance, the risk assessment with its factors and its caveat, the warnings, the
errors, and the timeline's decisions.

**Timestamps are stored, not regenerated.** A `GET` returns the `started_at` the run
actually recorded, not the time of the read. So `POST` then `GET` gives byte-equal
bodies today. That is a stronger property than the contract requires, and the
contract does not depend on it: if a future phase regenerates a timestamp on read,
these tests are the ones that should be updated, and they will say so.

**`completed_at` is `null`, always.** Phase 7 stamps `started_at` when the input
stage begins and never stamps a completion time, so the key is absent from the state
and the adapter renders it as `null`. The project documentation previously said this
field was populated. It is not, and no test should imply otherwise.

**Two runs of identical content share an id and stay separate rows.** The id is a
content fingerprint from Phase 7; Phase 9's surrogate key is what lets both runs be
stored. So `GET` by that id returns the *newest* run, while both remain listed. Two
runs sharing an id are therefore not two retrievable bodies — which is the one
place where "identical content implies identical response" would be false.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.graph.state import TIMESTAMP_FIELDS, InvestigationInputType, investigation_id_for
from tests.persistence_factories import MESSY_CONTENT, REGULATORY_CONTENT

#: Every field the retrieval contract covers, mapped to how it is checked.
#:
#: Listed explicitly rather than diffing the two bodies, so that a field *dropped*
#: from the response is a failure here. A whole-body comparison is blind to a field
#: that vanished from the schema, because both sides would then lack it.
CONTRACT_FIELDS = (
    "investigation_id",
    "input_type",
    "status",
    "current_stage",
    "language",
    "claims",
    "entities",
    "red_flags",
    "verification_results",
    "evidence",
    "risk_assessment",
    "warnings",
    "errors",
    "timeline",
    "limitations",
)


def _post(client: TestClient, text: str) -> dict:
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


def _get(client: TestClient, investigation_id: str) -> dict:
    """Retrieve one stored investigation.

    Args:
        client: The client to read through.
        investigation_id: The Phase 7 public id.

    Returns:
        The decoded response body.
    """
    response = client.get(f"/api/investigations/{investigation_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _without_times(payload: object) -> object:
    """Drop timestamp fields from a response body, at any depth.

    Args:
        payload: A decoded response body, or any part of one.

    Returns:
        The same structure without any `TIMESTAMP_FIELDS`, plus the API's own
        timeline key `at`.
    """
    fields = TIMESTAMP_FIELDS | {"at"}
    if isinstance(payload, dict):
        return {
            key: _without_times(value)
            for key, value in payload.items()
            if key not in fields
        }
    if isinstance(payload, list):
        return [_without_times(item) for item in payload]
    return payload


class TestEveryContractFieldSurvivesTheRoundTrip:
    """Field by field, on content that produces a full investigation."""

    @pytest.mark.parametrize("text", [REGULATORY_CONTENT, MESSY_CONTENT])
    def test_each_field_comes_back_equal(self, api_client: TestClient, text: str) -> None:
        """The substantive fields are equal on the way out and the way back.

        Two very different bodies are used on purpose. `REGULATORY_CONTENT` produces
        evidence and a real score; `MESSY_CONTENT` produces guarantees and a payment
        instruction, which Phase 4 cannot settle externally, so it exercises the
        partial path. Checking only the rich case would leave the partial path
        unverified.

        Args:
            api_client: A client running the real app, offline.
            text: The content to investigate.
        """
        posted = _post(api_client, text)
        retrieved = _get(api_client, posted["investigation_id"])

        for field in CONTRACT_FIELDS:
            assert retrieved[field] == posted[field], f"{field} did not survive storage"

    def test_the_compared_bodies_are_not_trivially_empty(self, api_client: TestClient) -> None:
        """The round trip must carry content, or the field comparison proves nothing.

        Args:
            api_client: A client running the real app, offline.
        """
        body = _post(api_client, REGULATORY_CONTENT)

        assert body["claims"], "no claims were extracted"
        assert body["red_flags"], "no red flags were detected"
        assert body["verification_results"], "no claims were verified"
        assert body["evidence"], "no evidence was assembled"
        assert body["risk_assessment"] is not None
        assert body["timeline"], "the timeline is empty"

    def test_the_investigation_id_is_the_content_fingerprint(
        self, api_client: TestClient
    ) -> None:
        """The id a client is given is derivable, not opaque.

        Useful for a client building its own deduplication, and it pins that storage
        did not substitute a surrogate for the public identity.

        Args:
            api_client: A client running the real app, offline.
        """
        body = _post(api_client, REGULATORY_CONTENT)

        assert body["investigation_id"] == investigation_id_for(
            InvestigationInputType.TEXT, REGULATORY_CONTENT
        )

    def test_risk_factors_keep_their_traceability(self, api_client: TestClient) -> None:
        """A factor must still say which claims and which sources produced it.

        A stored factor that lost its `claim_ids` would score the same and explain
        nothing, which is the opposite of the product's purpose. `claim_ids` is
        plural by design — one red flag pattern can bear on several claims — so the
        assertion is that the linkage survives, not that a particular id is present.

        Args:
            api_client: A client running the real app, offline.
        """
        posted = _post(api_client, REGULATORY_CONTENT)
        retrieved = _get(api_client, posted["investigation_id"])

        factors = retrieved["risk_assessment"]["factors"]
        assert factors
        assert all(factor["claim_ids"] for factor in factors)
        assert all(factor["label"] for factor in factors)
        assert all(factor["reason"] for factor in factors)
        # The claim ids a factor names must exist in the stored investigation, or the
        # explanation points at nothing.
        known = {claim["id"] for claim in retrieved["claims"]}
        assert all(set(factor["claim_ids"]) & known for factor in factors)

    def test_evidence_keeps_its_provenance(self, api_client: TestClient) -> None:
        """Every stored citation still names where it came from.

        Args:
            api_client: A client running the real app, offline.
        """
        posted = _post(api_client, REGULATORY_CONTENT)
        retrieved = _get(api_client, posted["investigation_id"])

        items = [item for bundle in retrieved["evidence"] for item in bundle["evidence"]]
        assert items, "the rich content should produce evidence items"
        assert all(item["source"] for item in items)
        assert all(item["url"] for item in items)


class TestTheTimestampContract:
    """Stamped values are stored, and the one that was never set stays null."""

    def test_started_at_is_the_runs_own_time_not_the_reads(
        self, api_client: TestClient
    ) -> None:
        """A `GET` returns when the investigation ran, not when it was fetched.

        A clock difference between the two requests is what makes this testable
        rather than merely stated. If the adapter regenerated `started_at` on read,
        the two values would differ by however long the test took to get there.

        Args:
            api_client: A client running the real app, offline.
        """
        posted = _post(api_client, REGULATORY_CONTENT)
        retrieved = _get(api_client, posted["investigation_id"])

        assert retrieved["started_at"] == posted["started_at"]

    def test_completed_at_is_null_because_it_is_never_stamped(
        self, api_client: TestClient
    ) -> None:
        """The documented behaviour, asserted so the documentation cannot drift again.

        Phase 7 stamps `started_at` at the input stage and never stamps a completion
        time. The field is present in the response schema — a client can rely on the
        key existing — and its value is `null`. Project documentation had described
        it as populated; this is the test that says otherwise.

        Args:
            api_client: A client running the real app, offline.
        """
        body = _post(api_client, REGULATORY_CONTENT)

        assert "completed_at" in body, "the key should exist in the schema"
        assert body["completed_at"] is None, (
            "completed_at is null by design: no phase stamps it. If a phase has "
            "started stamping it, update docs/CURRENT_STATE.md as well"
        )

    def test_completed_at_is_still_null_after_a_round_trip(
        self, api_client: TestClient
    ) -> None:
        """Storage must not invent a completion time either.

        A repository that defaulted the column to "now" on write would make a
        retrieved run look finished when it was not, and `completed_at` is exactly
        the field a client would use to measure duration.

        Args:
            api_client: A client running the real app, offline.
        """
        posted = _post(api_client, REGULATORY_CONTENT)
        retrieved = _get(api_client, posted["investigation_id"])

        assert retrieved["completed_at"] is None

    def test_the_bodies_are_equal_exactly(self, api_client: TestClient) -> None:
        """Today's behaviour is stronger than the contract, and it is asserted.

        Because `started_at` is stored rather than regenerated, the retrieved body is
        byte-equal to the posted one. `tests/api/test_persistence_endpoints.py`
        asserts this too; it is repeated here because the *reason* is what this
        module is about, and a test that passes for a reason nobody wrote down is a
        test nobody can safely change.

        Args:
            api_client: A client running the real app, offline.
        """
        posted = _post(api_client, REGULATORY_CONTENT)
        retrieved = _get(api_client, posted["investigation_id"])

        assert retrieved == posted

    def test_the_semantic_comparison_ignores_only_time(
        self, api_client: TestClient
    ) -> None:
        """The weaker form of the same check, which is what the contract promises.

        If a future phase regenerates a timestamp on read, this test still holds
        while the exact comparison above needs updating. Having both makes that
        change a deliberate decision rather than a silent regression.

        Args:
            api_client: A client running the real app, offline.
        """
        posted = _post(api_client, REGULATORY_CONTENT)
        retrieved = _get(api_client, posted["investigation_id"])

        assert _without_times(retrieved) == _without_times(posted)


class TestTwoRunsOfOneContentAreNotOneRetrievableBody:
    """The limit of the contract, stated so nobody assumes otherwise."""

    def test_a_shared_id_resolves_to_the_newest_run(
        self, api_client: TestClient
    ) -> None:
        """Submitting the same text twice gives one id and one retrievable answer.

        Args:
            api_client: A client running the real app, offline.
        """
        first = _post(api_client, REGULATORY_CONTENT)
        second = _post(api_client, REGULATORY_CONTENT)

        assert first["investigation_id"] == second["investigation_id"]

        retrieved = _get(api_client, first["investigation_id"])
        assert retrieved["started_at"] == second["started_at"]

    def test_both_runs_remain_listed_and_distinguishable(
        self, api_client: TestClient
    ) -> None:
        """History keeps both, so the newer run is not simply replacing the older.

        Args:
            api_client: A client running the real app, offline.
        """
        _post(api_client, REGULATORY_CONTENT)
        _post(api_client, REGULATORY_CONTENT)

        body = api_client.get("/api/investigations").json()

        assert body["total"] == 2
        assert len({entry["created_at"] for entry in body["investigations"]}) == 2

    def test_a_third_run_of_other_content_is_retrievable_on_its_own(
        self, api_client: TestClient
    ) -> None:
        """Sharing an id does not make one run shadow an unrelated one.

        Args:
            api_client: A client running the real app, offline.
        """
        _post(api_client, REGULATORY_CONTENT)
        _post(api_client, REGULATORY_CONTENT)
        other = _post(api_client, MESSY_CONTENT)

        assert other["investigation_id"] != investigation_id_for(
            InvestigationInputType.TEXT, REGULATORY_CONTENT
        )
        assert _get(api_client, other["investigation_id"])["input_type"] == "TEXT"
