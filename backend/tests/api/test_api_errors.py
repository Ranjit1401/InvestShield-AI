"""Tests for the API error taxonomy (Phase 8).

The mapping under test is the one that decides what a client is told when a run
does not succeed: which status code, and how much of the cause is allowed out.
Two properties matter most and are asserted most directly — a caller's mistake
must be distinguishable from our defect, and neither may leak internal detail
onto the wire.
"""

from __future__ import annotations

import pytest
from fastapi import status

from app.api.errors import (
    UNPROCESSABLE_CONTENT,
    GraphContractError,
    SubmissionRejected,
    UnsupportedInputType,
    error_status_for,
    raise_for_graph_errors,
)
from app.graph.state import GraphError, GraphStage


def _error(code: str, error_type: str | None = None) -> GraphError:
    """Build a recorded graph failure."""
    return GraphError(
        code=code,
        stage=GraphStage.INPUT,
        message="A fixed message.",
        error_type=error_type,
    )


# -- status mapping ------------------------------------------------------


@pytest.mark.parametrize("code", ["INPUT_EMPTY", "INPUT_TYPE_NOT_SUPPORTED"])
def test_caller_faults_map_to_422(code: str) -> None:
    assert error_status_for(_error(code)) == UNPROCESSABLE_CONTENT


@pytest.mark.parametrize(
    "code",
    [
        "EXTRACTION_FAILED",
        "RED_FLAG_DETECTION_FAILED",
        "VERIFICATION_FAILED",
        "RISK_ASSESSMENT_FAILED",
    ],
)
def test_contract_violations_map_to_500(code: str) -> None:
    assert error_status_for(_error(code)) == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_an_unknown_code_is_treated_as_our_defect() -> None:
    """A code nobody recognises must not default to blaming the caller."""
    assert error_status_for(_error("SOMETHING_NEW")) == status.HTTP_500_INTERNAL_SERVER_ERROR


# -- raising -------------------------------------------------------------


def test_nothing_is_raised_when_no_errors() -> None:
    raise_for_graph_errors(())


def test_empty_submission_raises_submission_rejected() -> None:
    with pytest.raises(SubmissionRejected) as caught:
        raise_for_graph_errors((_error("INPUT_EMPTY"),))

    assert caught.value.status_code == UNPROCESSABLE_CONTENT


def test_unsupported_type_raises_its_own_error() -> None:
    with pytest.raises(UnsupportedInputType) as caught:
        raise_for_graph_errors(
            (_error("INPUT_TYPE_NOT_SUPPORTED"),),
            submitted_input_type="PDF",
        )

    assert caught.value.status_code == UNPROCESSABLE_CONTENT


def test_unsupported_type_names_the_supported_kinds() -> None:
    with pytest.raises(UnsupportedInputType) as caught:
        raise_for_graph_errors(
            (_error("INPUT_TYPE_NOT_SUPPORTED"),),
            submitted_input_type="PDF",
        )

    detail = caught.value.detail
    assert detail["submitted_input_type"] == "PDF"
    assert detail["supported_input_types"] == ["TEXT", "URL", "IMAGE"]


def test_contract_violation_raises_500() -> None:
    with pytest.raises(GraphContractError) as caught:
        raise_for_graph_errors((_error("VERIFICATION_FAILED", "TimeoutError"),))

    assert caught.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_detail_lists_every_recorded_error() -> None:
    with pytest.raises(GraphContractError) as caught:
        raise_for_graph_errors(
            (_error("EXTRACTION_FAILED", "ValueError"), _error("VERIFICATION_FAILED"))
        )

    codes = [entry["code"] for entry in caught.value.detail["errors"]]
    assert codes == ["EXTRACTION_FAILED", "VERIFICATION_FAILED"]


def test_detail_is_json_serialisable() -> None:
    """The envelope is rendered by `JSONResponse`, which handles plain data only."""
    import json

    with pytest.raises(GraphContractError) as caught:
        raise_for_graph_errors((_error("EXTRACTION_FAILED", "ValueError"),))

    assert json.dumps(caught.value.detail)


def test_detail_carries_the_exception_type_only() -> None:
    with pytest.raises(GraphContractError) as caught:
        raise_for_graph_errors((_error("EXTRACTION_FAILED", "ValueError"),))

    entry = caught.value.detail["errors"][0]
    assert entry["error_type"] == "ValueError"
    assert "message" not in entry


def test_an_empty_submission_does_not_report_an_input_type() -> None:
    """The input-type fields explain a refusal; naming TEXT explains nothing.

    A caller who sent nothing already sent the supported kind, so reporting it
    back would be noise in the one error body most likely to be read by a human.
    """
    with pytest.raises(SubmissionRejected) as caught:
        raise_for_graph_errors(
            (_error("INPUT_EMPTY"),),
            submitted_input_type="TEXT",
        )

    assert caught.value.detail["submitted_input_type"] is None
    assert caught.value.detail["supported_input_types"] == []


def test_a_contract_violation_does_not_report_an_input_type() -> None:
    with pytest.raises(GraphContractError) as caught:
        raise_for_graph_errors(
            (_error("VERIFICATION_FAILED", "TimeoutError"),),
            submitted_input_type="TEXT",
        )

    assert caught.value.detail["submitted_input_type"] is None


def test_the_first_error_decides_the_raised_type() -> None:
    """The graph stops at the first failure, so the first is the cause."""
    with pytest.raises(SubmissionRejected):
        raise_for_graph_errors((_error("INPUT_EMPTY"), _error("EXTRACTION_FAILED")))


def test_submission_rejected_is_a_422_by_construction() -> None:
    assert SubmissionRejected("C", "m").status_code == UNPROCESSABLE_CONTENT


def test_graph_contract_error_is_a_500_by_construction() -> None:
    assert GraphContractError("C", "m").status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_unsupported_type_is_a_submission_rejection() -> None:
    """A caller can handle every 422 through one branch."""
    assert issubclass(UnsupportedInputType, SubmissionRejected)