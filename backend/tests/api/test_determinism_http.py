"""Determinism seen from outside, over HTTP (Phase 10).

The same guarantee `tests/test_determinism.py` states for the pipeline, made
through the route a client actually calls. It lives in `tests/api/` because the
fixtures it needs — an offline graph context that also persists — are defined in
`tests/api/conftest.py` and are deliberately not visible to the suite root.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.graph.state import (
    TIMESTAMP_FIELDS,
    InvestigationInputType,
    investigation_id_for,
)
from tests.persistence_factories import REGULATORY_CONTENT

#: Timestamp keys as they appear in the **API response**, which are not quite the
#: names used inside the state.
#:
#: `TimelineEvent` exposes its instant as `at` rather than `started_at`, so
#: stripping only `TIMESTAMP_FIELDS` would leave the whole timeline differing
#: between two runs and make the comparison above fail for a reason that has nothing
#: to do with determinism. The two sets are combined rather than one replacing the
#: other, so a new timestamp field has to be added deliberately in both places.
RESPONSE_TIME_FIELDS = frozenset(TIMESTAMP_FIELDS | {"at"})


class TestTheApiIsDeterministic:
    """Two identical submissions produce identical answers, modulo time."""

    def test_two_submissions_agree_on_the_body(
        self, api_client: TestClient
    ) -> None:
        """The response is compared after its timestamps are removed.

        Args:
            api_client: A client running the real app, offline.
        """
        first = api_client.post("/api/investigations/text", json={"text": REGULATORY_CONTENT})
        second = api_client.post("/api/investigations/text", json={"text": REGULATORY_CONTENT})

        assert first.status_code == second.status_code == 200
        assert _without_times(first.json()) == _without_times(second.json())

    def test_two_submissions_share_an_investigation_id(
        self, api_client: TestClient
    ) -> None:
        """The id is derived, so re-submitting the same text reuses it.

        Args:
            api_client: A client running the real app, offline.
        """
        first = api_client.post("/api/investigations/text", json={"text": REGULATORY_CONTENT})
        second = api_client.post("/api/investigations/text", json={"text": REGULATORY_CONTENT})

        assert first.json()["investigation_id"] == second.json()["investigation_id"]
        assert first.json()["investigation_id"] == investigation_id_for(
            InvestigationInputType.TEXT, REGULATORY_CONTENT
        )

    def test_retrieval_matches_the_live_response(
        self, api_client: TestClient
    ) -> None:
        """POST, then GET, must describe the same investigation.

        Args:
            api_client: A client running the real app, offline.
        """
        posted = api_client.post(
            "/api/investigations/text", json={"text": REGULATORY_CONTENT}
        ).json()
        retrieved = api_client.get(
            f"/api/investigations/{posted['investigation_id']}"
        ).json()

        assert _without_times(retrieved) == _without_times(posted)


def _without_times(payload: dict) -> dict:
    """Drop the timestamp fields from a response body.

    Args:
        payload: A decoded response body.

    Returns:
        The body without any `TIMESTAMP_FIELDS` keys, at any depth.
    """
    if isinstance(payload, dict):
        return {
            key: _without_times(value)
            for key, value in payload.items()
            if key not in RESPONSE_TIME_FIELDS
        }
    if isinstance(payload, list):
        return [_without_times(item) for item in payload]
    return payload


