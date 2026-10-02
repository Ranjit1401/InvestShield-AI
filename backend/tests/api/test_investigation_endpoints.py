"""Tests for the investigation endpoints (Phase 8).

These assert the *contract* a client depends on: what status code comes back,
what the body contains, and — most importantly — that a degraded run is still a
successful request. The last group of tests is the reason this product can be
honest: a search provider being down must never look like a broken API, and the
only way to know that is to test the endpoint while the provider is down.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.graph.context import GraphContext, GraphDependencies, RecordingSearchService
from app.graph.investigation_graph import run_investigation
from app.graph.state import semantic_view
from app.schemas.search import SearchResponse
from app.services.evidence import EvidenceService
from app.services.extraction_service import ExtractionService
from app.services.red_flag_engine import RedFlagEngine
from app.services.risk import RiskService
from app.services.search import SearchProvider, SearchService
from app.services.verification import VerificationService
from tests.graph.graph_factories import (
    MESSY_CONTENT,
    RecordingExtractionService,
    RecordingRedFlagEngine,
    build_claim,
    build_entity,
    build_flag,
    extraction_with,
    offline_context,
    offline_settings,
)


class OfflineSearchProvider(SearchProvider):
    """A provider that reports itself unusable, exactly as a missing key would.

    `search` raises if it is ever called. That is the assertion that matters: an
    unavailable provider must be *reported* as unavailable, not queried and
    allowed to fail. A test that passed because the provider returned an error
    would hide exactly the network call it exists to forbid.
    """

    name = "offline"

    def is_available(self) -> bool:
        """Always report the provider as unusable."""
        return False

    def search(self, query: str, max_results: int) -> SearchResponse:
        """Fail loudly if an unavailable provider is ever consulted."""
        raise AssertionError(f"an unavailable provider must never be queried: {query}")


#: Content that trips no Phase 1 rule, so the run has a genuine clean result to
#: report. Used to prove that "nothing found" is still surfaced rather than
#: collapsed into an empty response.
NEUTRAL_CONTENT = "The quarterly maintenance window has been rescheduled to Friday."


def test_text_investigation_returns_200(api_client: TestClient) -> None:
    response = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT})

    assert response.status_code == 200


def test_response_exposes_the_documented_fields(api_client: TestClient) -> None:
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    for field in (
        "investigation_id",
        "status",
        "input_type",
        "claims",
        "entities",
        "red_flags",
        "verification_results",
        "evidence",
        "risk_assessment",
        "timeline",
        "limitations",
        "errors",
    ):
        assert field in body, f"missing documented field: {field}"


def test_response_carries_a_risk_assessment(api_client: TestClient) -> None:
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    assert body["risk_assessment"] is not None
    assert body["risk_assessment"]["risk_score"] >= 0
    assert body["risk_assessment"]["risk_level"]


def test_risk_assessment_keeps_its_caveat(api_client: TestClient) -> None:
    """The heuristic score must not be restated as a probability (D-025)."""
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    warnings = body["risk_assessment"]["warnings"]
    assert any("not a probability" in warning for warning in warnings)


def test_risk_assessment_is_forwarded_verbatim(api_client: TestClient) -> None:
    """The adapter must not re-project the assessment into a second shape."""
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    assert set(body["risk_assessment"]) == set(RiskService().assess().model_dump())


def test_investigation_id_is_derived_from_the_input(api_client: TestClient) -> None:
    first = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()
    second = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()
    other = api_client.post("/api/investigations/text", json={"text": "Something else entirely."}).json()

    assert first["investigation_id"] == second["investigation_id"]
    assert first["investigation_id"] != other["investigation_id"]
    assert first["investigation_id"].startswith("inv_")


def test_input_type_is_reported_as_text(api_client: TestClient) -> None:
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    assert body["input_type"] == "TEXT"


def test_typed_endpoint_defaults_to_text(api_client: TestClient) -> None:
    body = api_client.post("/api/investigations", json={"text": MESSY_CONTENT}).json()

    assert body["input_type"] == "TEXT"
    assert body["investigation_id"]


def test_typed_endpoint_accepts_an_explicit_text_type(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/investigations",
        json={"input_type": "TEXT", "text": MESSY_CONTENT},
    )

    assert response.status_code == 200
    assert response.json()["input_type"] == "TEXT"


def test_language_is_echoed_back(api_client: TestClient) -> None:
    body = api_client.post(
        "/api/investigations/text",
        json={"text": MESSY_CONTENT, "language": "hi"},
    ).json()

    assert body["language"] == "hi"


def test_both_investigation_endpoints_agree(api_client: TestClient) -> None:
    """The convenience form and the typed form must not be two pipelines."""
    convenience = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()
    typed = api_client.post("/api/investigations", json={"text": MESSY_CONTENT}).json()

    assert convenience["investigation_id"] == typed["investigation_id"]
    assert (
        convenience["risk_assessment"]["risk_score"]
        == typed["risk_assessment"]["risk_score"]
    )


# -- validation and refusal ---------------------------------------------


def test_empty_text_is_refused(api_client: TestClient) -> None:
    response = api_client.post("/api/investigations/text", json={"text": ""})

    assert response.status_code == 422


def test_whitespace_only_text_is_refused(api_client: TestClient) -> None:
    response = api_client.post("/api/investigations/text", json={"text": "   \n  "})

    assert response.status_code == 422


def test_missing_text_is_refused(api_client: TestClient) -> None:
    response = api_client.post("/api/investigations/text", json={})

    assert response.status_code == 422


def test_oversized_text_is_refused_before_any_work(api_client: TestClient) -> None:
    response = api_client.post("/api/investigations/text", json={"text": "x" * 20_001})

    assert response.status_code == 422


def test_unknown_body_field_is_refused(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/investigations/text",
        json={"text": MESSY_CONTENT, "risk_score": 99},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("input_type", ["URL", "IMAGE", "PDF"])
def test_unsupported_input_types_are_refused_with_422(
    api_client: TestClient, input_type: str
) -> None:
    response = api_client.post(
        "/api/investigations",
        json={"input_type": input_type, "text": "https://example.invest/opportunity"},
    )

    assert response.status_code == 422


def test_unsupported_input_type_names_what_is_supported(api_client: TestClient) -> None:
    body = api_client.post(
        "/api/investigations",
        json={"input_type": "URL", "text": "https://example.invest/opportunity"},
    ).json()

    detail = body["error"]["detail"]
    assert body["error"]["code"] == "INPUT_TYPE_NOT_SUPPORTED"
    assert detail["submitted_input_type"] == "URL"
    assert detail["supported_input_types"] == ["TEXT"]
    assert detail["errors"][0]["stage"] == "input"


def test_unrecognised_input_type_is_refused(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/investigations",
        json={"input_type": "PODCAST", "text": "some content"},
    )

    assert response.status_code == 422


def test_error_responses_use_the_shared_envelope(api_client: TestClient) -> None:
    body = api_client.post("/api/investigations/text", json={"text": ""}).json()

    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "detail"}


def test_unsupported_type_does_not_analysed_text_as_prose(
    api_client: TestClient,
) -> None:
    """A URL must be refused, never quietly investigated as words."""
    response = api_client.post(
        "/api/investigations",
        json={"input_type": "URL", "text": MESSY_CONTENT},
    )

    assert response.status_code == 422
    assert "claims" not in response.json()


# -- degradation: a partial run is a 200 --------------------------------


def test_search_unavailable_yields_200_and_partial(
    api_client_factory,
) -> None:
    """The core honesty property: a missing provider is a degraded result, not a failure."""
    settings = offline_settings()
    recorder = RecordingSearchService(SearchService(settings=settings, provider=OfflineSearchProvider()))
    context = GraphContext(
        dependencies=GraphDependencies(
            extraction_service=ExtractionService(settings=settings),
            red_flag_engine=RedFlagEngine(settings=settings),
            verification_service=VerificationService(
                settings=settings, search_service=recorder
            ),
            evidence_service=EvidenceService(),
            risk_service=RiskService(settings=settings),
            search_recorder=recorder,
        )
    )

    with api_client_factory(context) as client:
        response = client.post("/api/investigations/text", json={"text": MESSY_CONTENT})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PARTIAL"
    assert "SEARCH_UNAVAILABLE" in body["limitations"]
    assert body["risk_assessment"] is not None


def test_limitations_are_deduplicated_codes(api_client: TestClient) -> None:
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    assert isinstance(body["limitations"], list)
    assert len(body["limitations"]) == len(set(body["limitations"]))
    assert all(isinstance(code, str) for code in body["limitations"])


def test_warnings_carry_readable_messages_with_their_codes(api_client: TestClient) -> None:
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    warning = body["warnings"][0]
    assert set(warning) == {"code", "stage", "message"}
    assert warning["code"] in body["limitations"]


def test_warnings_never_expose_error_type(api_client: TestClient) -> None:
    """`error_type` is a log-side diagnostic and must not reach a user-facing body."""
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    assert all("error_type" not in warning for warning in body["warnings"])


def test_no_red_flags_is_a_limitation(api_client: TestClient) -> None:
    """A clean finding is still reported: what was looked for, not found."""
    body = api_client.post(
        "/api/investigations/text",
        json={"text": NEUTRAL_CONTENT},
    ).json()

    assert "NO_RED_FLAGS_DETECTED" in body["limitations"]
    assert body["red_flags"] == []


# -- contract violations -------------------------------------------------


def test_contract_violation_returns_500(api_client_factory) -> None:
    """A stage raising what it documented it never raises is our bug, not the caller's."""
    context = offline_context(
        _dependencies_with(red_flags=RecordingRedFlagEngine(raises=RuntimeError("boom")))
    )

    with api_client_factory(context) as client:
        response = client.post("/api/investigations/text", json={"text": MESSY_CONTENT})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "RED_FLAG_DETECTION_FAILED"


def test_contract_violation_never_leaks_the_exception_message(
    api_client_factory,
) -> None:
    context = offline_context(
        _dependencies_with(red_flags=RecordingRedFlagEngine(raises=RuntimeError("boom")))
    )

    with api_client_factory(context) as client:
        raw = client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).text

    assert "boom" not in raw
    assert "Traceback" not in raw


def test_500_uses_the_shared_envelope(api_client_factory) -> None:
    context = offline_context(
        _dependencies_with(red_flags=RecordingRedFlagEngine(raises=RuntimeError("boom")))
    )

    with api_client_factory(context) as client:
        body = client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    assert set(body["error"]) == {"code", "message", "detail"}
    assert body["error"]["detail"]["errors"][0]["error_type"] == "RuntimeError"


# -- shape of the returned pipeline --------------------------------------


def test_claims_entities_and_red_flags_are_reported(api_client_factory) -> None:
    """The response carries what every stage found, not just what it failed to find."""
    claim = build_claim()
    entity = build_entity()
    context = offline_context(
        _dependencies_with(
            extraction=RecordingExtractionService(
                result=extraction_with(claims=(claim,), entities=(entity,))
            ),
            red_flags=RecordingRedFlagEngine(flags=(build_flag(),)),
        )
    )

    with api_client_factory(context) as client:
        body = client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    assert [item["id"] for item in body["claims"]] == [claim.id]
    assert [item["id"] for item in body["entities"]] == [entity.id]
    assert len(body["red_flags"]) == 1


def test_red_flags_keep_their_spans(api_client: TestClient) -> None:
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    flag = body["red_flags"][0]
    assert flag["evidence_span"]["text"] == MESSY_CONTENT[
        flag["evidence_span"]["start"] : flag["evidence_span"]["end"]
    ]


def test_timeline_lists_every_stage(api_client: TestClient) -> None:
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    stages = [event["stage"] for event in body["timeline"]]
    assert "extraction" in stages
    assert "risk" in stages
    assert body["current_stage"] == "risk"


def test_timeline_entries_have_a_timestamp(api_client: TestClient) -> None:
    body = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT}).json()

    assert all(event["at"] for event in body["timeline"])


def test_run_is_reproducible_through_the_api(api_client: TestClient) -> None:
    """Same input, same semantic result — timestamps are the only permitted drift."""
    context = offline_context()
    first = semantic_view(run_investigation(MESSY_CONTENT, context=context))
    second = semantic_view(run_investigation(MESSY_CONTENT, context=context))

    assert first == second


# -- discovery -----------------------------------------------------------


def test_limits_endpoint_reports_supported_inputs(api_client: TestClient) -> None:
    body = api_client.get("/api/investigations/limits").json()

    assert body["supported_input_types"] == ["TEXT"]
    assert body["max_text_length"] == 20_000
    assert body["translation_enabled"] is False


def test_openapi_documents_every_investigation_endpoint(api_client: TestClient) -> None:
    paths = api_client.get("/openapi.json").json()["paths"]

    assert "/api/investigations/text" in paths
    assert "/api/investigations" in paths
    assert "/api/investigations/limits" in paths
    assert "/api/health" in paths


def test_openapi_documents_the_error_responses(api_client: TestClient) -> None:
    operation = api_client.get("/openapi.json").json()["paths"]["/api/investigations/text"][
        "post"
    ]

    assert "422" in operation["responses"]
    assert "500" in operation["responses"]


def _dependencies_with(**overrides: object) -> GraphDependencies:
    """Build offline dependencies, replacing whichever services are named.

    Args:
        **overrides: Service fakes to substitute, keyed as in `fake_dependencies`.

    Returns:
        A `GraphDependencies` with the requested fakes installed.
    """
    from tests.graph.graph_factories import fake_dependencies

    return fake_dependencies(**overrides)  # type: ignore[arg-type]