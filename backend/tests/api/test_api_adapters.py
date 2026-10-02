"""Tests for the state-to-response adapter (Phase 8).

The adapter is where a `TypedDict` with eleven optional keys becomes a contract a
client can rely on, so it is tested directly rather than only through HTTP. The
two things worth pinning down here are the status derivation — which must never
promote an incomplete run to a clean one — and the handling of absent keys,
which is exactly where "we found nothing" and "we checked nothing" would
otherwise become the same response (D-006).
"""

from __future__ import annotations

import pytest

from app.api.adapters import (
    dedupe_codes,
    investigation_status,
    serialize_investigation,
)
from app.graph.state import (
    GraphError,
    GraphStage,
    GraphWarning,
    InvestigationInputType,
    TimelineEvent,
    TimelineStatus,
)
from app.schemas.api import InvestigationStatus, Language


def _event(stage: GraphStage, status: TimelineStatus) -> TimelineEvent:
    """Build a timeline event with the fixed wording a real node would use."""
    from datetime import datetime, timezone

    return TimelineEvent(
        stage=stage,
        status=status,
        message="Stage finished.",
        at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _warning(code: str, stage: GraphStage = GraphStage.VERIFICATION) -> GraphWarning:
    """Build a recorded warning."""
    return GraphWarning(code=code, stage=stage, message=f"{code} happened.")


def _error(code: str, stage: GraphStage = GraphStage.RED_FLAGS) -> GraphError:
    """Build a recorded failure."""
    return GraphError(code=code, stage=stage, message=f"{code} happened.", error_type="Boom")


# -- status derivation ---------------------------------------------------


def test_no_errors_and_no_timeline_is_complete() -> None:
    assert investigation_status((), ()) is InvestigationStatus.COMPLETED


def test_a_completed_timeline_is_complete() -> None:
    timeline = (_event(GraphStage.INPUT, TimelineStatus.COMPLETED),)

    assert investigation_status((), timeline) is InvestigationStatus.COMPLETED


def test_a_partial_stage_makes_the_run_partial() -> None:
    timeline = (
        _event(GraphStage.INPUT, TimelineStatus.COMPLETED),
        _event(GraphStage.VERIFICATION, TimelineStatus.PARTIAL),
    )

    assert investigation_status((), timeline) is InvestigationStatus.PARTIAL


def test_a_skipped_stage_makes_the_run_partial() -> None:
    timeline = (_event(GraphStage.VERIFICATION, TimelineStatus.SKIPPED),)

    assert investigation_status((), timeline) is InvestigationStatus.PARTIAL


def test_any_error_makes_the_run_failed() -> None:
    timeline = (_event(GraphStage.VERIFICATION, TimelineStatus.PARTIAL),)

    assert investigation_status((_error("X"),), timeline) is InvestigationStatus.FAILED


def test_no_red_flags_alone_is_not_a_degraded_run() -> None:
    """A clean finding must not read as an incomplete check.

    `NO_RED_FLAGS_DETECTED` is recorded whenever content matched no pattern, so
    treating "a warning exists" as "the run is partial" would report every benign
    submission as a degraded investigation. The stage statuses are what separate
    the two, which is exactly why the graph records them.
    """
    assert investigation_status((), ()) is InvestigationStatus.COMPLETED


def test_a_started_stage_alone_is_not_a_degradation() -> None:
    timeline = (
        _event(GraphStage.INPUT, TimelineStatus.STARTED),
        _event(GraphStage.EXTRACTION, TimelineStatus.COMPLETED),
    )

    assert investigation_status((), timeline) is InvestigationStatus.COMPLETED


# -- limitation codes ----------------------------------------------------


def test_dedupe_keeps_first_seen_order() -> None:
    warnings = (_warning("B"), _warning("A"), _warning("B"))

    assert dedupe_codes(warnings) == ["B", "A"]


def test_dedupe_collapses_the_same_code_from_two_stages() -> None:
    warnings = (_warning("X", GraphStage.EXTRACTION), _warning("X", GraphStage.EVIDENCE))

    assert dedupe_codes(warnings) == ["X"]


def test_dedupe_of_nothing_is_empty() -> None:
    assert dedupe_codes(()) == []


# -- serialization -------------------------------------------------------


def test_absent_optional_keys_become_empty_lists() -> None:
    response = serialize_investigation({"investigation_id": "inv_1"})

    assert response.claims == []
    assert response.entities == []
    assert response.red_flags == []
    assert response.verification_results == []
    assert response.evidence == []
    assert response.risk_assessment is None


def test_absent_keys_do_not_raise() -> None:
    """A state that stopped at the input node must still serialize.

    The API layer is the only place a caller can render a run that did not
    complete. If it raised here, a caller would have no way to show what the
    system actually knows about a failed investigation.
    """
    response = serialize_investigation({})

    assert response.status == InvestigationStatus.COMPLETED
    assert response.investigation_id == ""


def test_risk_assessment_is_passed_through_unchanged() -> None:
    from app.services.risk import RiskService

    assessment = RiskService().assess()

    response = serialize_investigation({"risk_assessment": assessment})

    assert response.risk_assessment is assessment


def test_input_type_defaults_to_text_when_unrecorded() -> None:
    response = serialize_investigation({})

    assert response.input_type is InvestigationInputType.TEXT


def test_unrecognised_input_type_does_not_break_serialization() -> None:
    response = serialize_investigation({"input_type": "PODCAST"})

    assert response.input_type is InvestigationInputType.TEXT


def test_warnings_expose_code_stage_and_message_only() -> None:
    response = serialize_investigation(
        {"warnings": (_warning("SEARCH_UNAVAILABLE", GraphStage.VERIFICATION),)}
    )

    assert response.warnings[0].code == "SEARCH_UNAVAILABLE"
    assert response.warnings[0].stage is GraphStage.VERIFICATION
    assert not hasattr(response.warnings[0], "error_type")


def test_errors_expose_the_exception_type_but_not_a_message() -> None:
    response = serialize_investigation({"errors": (_error("VERIFICATION_FAILED"),)})

    assert response.errors[0].error_type == "Boom"
    assert response.errors[0].code == "VERIFICATION_FAILED"


def test_language_defaults_to_english() -> None:
    assert serialize_investigation({}).language is Language.EN


def test_language_is_carried_through() -> None:
    response = serialize_investigation({}, language=Language.MR)

    assert response.language is Language.MR


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Language.EN, "en"), (Language.HI, "hi"), (Language.MR, "mr")],
)
def test_language_serialises_as_its_code(value: Language, expected: str) -> None:
    assert serialize_investigation({}, language=value).language.value == expected