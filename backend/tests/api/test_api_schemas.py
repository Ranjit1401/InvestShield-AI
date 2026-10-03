"""Tests for the API request and response contracts (Phase 8).

These are the shapes a client codes against, so they are asserted directly rather
than only through a request. The rules worth pinning are the ones that stop a
contract from quietly drifting: unknown body fields are refused, an oversized
submission is refused at the edge, and the response model will not accept
anything the graph did not produce.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.graph.state import GraphStage, InvestigationInputType
from app.schemas.api import (
    MAX_TEXT_LENGTH,
    InvestigationCreateRequest,
    InvestigationResponse,
    InvestigationStatus,
    Language,
    TextInvestigationRequest,
)


# -- request contracts ---------------------------------------------------


def test_text_is_required() -> None:
    with pytest.raises(ValidationError):
        TextInvestigationRequest()


def test_empty_text_is_refused_by_the_schema() -> None:
    with pytest.raises(ValidationError):
        TextInvestigationRequest(text="")


def test_text_at_the_limit_is_accepted() -> None:
    request = TextInvestigationRequest(text="x" * MAX_TEXT_LENGTH)

    assert len(request.text) == MAX_TEXT_LENGTH


def test_text_over_the_limit_is_refused() -> None:
    with pytest.raises(ValidationError):
        TextInvestigationRequest(text="x" * (MAX_TEXT_LENGTH + 1))


def test_unknown_body_fields_are_refused() -> None:
    """A typo'd field must not be silently dropped and analysed as absent."""
    with pytest.raises(ValidationError):
        TextInvestigationRequest(text="content", risk_level="HIGH")


def test_language_defaults_to_english() -> None:
    assert TextInvestigationRequest(text="content").language is Language.EN


@pytest.mark.parametrize("value", ["en", "hi", "mr"])
def test_supported_languages_are_accepted(value: str) -> None:
    assert TextInvestigationRequest(text="content", language=value).language.value == value


def test_unsupported_language_is_refused() -> None:
    with pytest.raises(ValidationError):
        TextInvestigationRequest(text="content", language="fr")


def test_typed_request_defaults_to_text() -> None:
    assert InvestigationCreateRequest(text="content").input_type is InvestigationInputType.TEXT


@pytest.mark.parametrize("value", ["TEXT", "URL", "IMAGE", "PDF"])
def test_every_declared_input_kind_is_accepted_by_the_typed_request(value: str) -> None:
    """The product commits to four kinds, so the contract must name all four."""
    request = InvestigationCreateRequest(input_type=value, text="content")

    assert request.input_type.value == value


def test_an_invented_input_kind_is_refused() -> None:
    with pytest.raises(ValidationError):
        InvestigationCreateRequest(input_type="PODCAST", text="content")


# -- response contract ---------------------------------------------------


def test_every_documented_response_field_exists() -> None:
    fields = set(InvestigationResponse.model_fields)

    assert fields == {
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
        # Phase 12: null for a TEXT submission, populated for a URL one.
        "url_source",
        # Phase 13: null unless the input was a screenshot, whose recovered
        # text and decoded shape are reported so the analysis is checkable.
        "image_source",
        # Phase 14: null unless the input was a PDF, whose parsed
        # shape, page count and recovered text are reported so the
        # analysis is checkable.
        "pdf_source",
    }


def test_response_refuses_unknown_fields() -> None:
    """The response is `extra="forbid"`, so drift shows up in the suite."""
    with pytest.raises(ValidationError):
        InvestigationResponse(investigation_id="inv_1", summary="a verdict")


def test_status_values_are_the_three_documented_ones() -> None:
    assert {member.value for member in InvestigationStatus} == {
        "COMPLETED",
        "PARTIAL",
        "FAILED",
    }


def test_limitations_default_to_a_list_not_none() -> None:
    response = InvestigationResponse(
        investigation_id="inv_1",
        status=InvestigationStatus.COMPLETED,
        input_type=InvestigationInputType.TEXT,
        current_stage="risk",
    )

    assert response.limitations == []
    assert response.claims == []


def test_a_wrong_stage_enum_is_refused() -> None:
    with pytest.raises(ValidationError):
        InvestigationResponse(investigation_id="inv_1", current_stage="RISK")


def test_a_wrong_status_value_is_refused() -> None:
    with pytest.raises(ValidationError):
        InvestigationResponse(investigation_id="inv_1", status="OK")


def test_graph_stage_values_are_stable() -> None:
    """Stage labels double as node names, so changing one is a breaking change."""
    assert [stage.value for stage in GraphStage] == [
        "input",
        "extraction",
        "red_flags",
        "verification",
        "evidence",
        "risk",
        "completed",
    ]