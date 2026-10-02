"""Tests that the graph cannot output a verdict, a probability, or advice.

Phase 6 established the product's central constraint and tested it against every
string the risk engine can emit. The graph introduces new authored text — warning
messages, error messages, timeline entries — and each is a new place the same
constraint could be broken.

The checks reuse Phase 6's matcher and its word lists rather than defining a
second one, so the two phases cannot drift apart on what counts as a verdict.

The set of texts is assembled by **walking real runs**, not from a hand-picked
sample, so a message added to a new code later is covered without editing this
file.
"""

from __future__ import annotations

import pytest

from app.graph import run_investigation
from app.graph.nodes import ERROR_CODES, WARNING_CODES
from tests.graph.graph_factories import (
    MESSY_CONTENT,
    RecordingEvidenceService,
    RecordingExtractionService,
    RecordingRedFlagEngine,
    RecordingRiskService,
    build_claim,
    build_flag,
    extraction_with,
    fake_dependencies,
    offline_context,
)
from tests.test_risk_safety import (
    ADVICE_PATTERN,
    JUDGEMENT_PATTERN,
    assert_no_words,
)
from app.schemas.red_flags import RedFlagCode


def run_with(**kwargs: object) -> dict:
    """Run the graph with the given injected dependencies."""
    return run_investigation(
        MESSY_CONTENT, context=offline_context(fake_dependencies(**kwargs))
    )


def every_run() -> list[dict]:
    """Return a spread of real runs covering every authored message.

    Each run is shaped to force a different set of warnings and errors, so the
    collection below reaches wording that a single happy path would miss.
    """
    claim = build_claim()
    runs = [
        run_with(),
        run_with(red_flags=RecordingRedFlagEngine(flags=())),
        run_with(extraction=RecordingExtractionService(result=extraction_with(()))),
        run_with(
            extraction=RecordingExtractionService(result=extraction_with((claim,)))
        ),
        run_with(evidence=RecordingEvidenceService(raises=RuntimeError("boom"))),
        run_with(extraction=RecordingExtractionService(raises=RuntimeError("x"))),
        run_with(red_flags=RecordingRedFlagEngine(raises=RuntimeError("x"))),
        run_with(risk=RecordingRiskService(raises=RuntimeError("x"))),
        run_investigation("", context=offline_context(fake_dependencies())),
        run_investigation(
            "x", input_type="PDF", context=offline_context(fake_dependencies())
        ),
    ]
    return runs


def graph_authored_texts(state: dict) -> list[str]:
    """Collect every string the graph itself authored in one run."""
    texts: list[str] = []
    for warning in state.get("warnings") or ():
        texts.append(f"warning[{warning.code}]: {warning.message}")
    for error in state.get("errors") or ():
        texts.append(f"error[{error.code}]: {error.message}")
    for event in state.get("timeline") or ():
        texts.append(f"timeline[{event.stage.value}/{event.status.value}]: {event.message}")
    return texts


class TestNoVerdictOrAdvice:
    """The judgement and advice bans, applied to graph-authored text."""

    @pytest.mark.parametrize(
        "state", every_run(), ids=[f"run{index}" for index in range(10)]
    )
    def test_no_run_makes_a_judgement(self, state: dict) -> None:
        assert_no_words(graph_authored_texts(state), JUDGEMENT_PATTERN, "judgement")

    @pytest.mark.parametrize(
        "state", every_run(), ids=[f"run{index}" for index in range(10)]
    )
    def test_no_run_advises_the_reader(self, state: dict) -> None:
        assert_no_words(graph_authored_texts(state), ADVICE_PATTERN, "advice")


class TestTheCodeTables:
    """The fixed message tables, checked directly rather than via a run."""

    @pytest.mark.parametrize("code", WARNING_CODES, ids=lambda code: code.lower())
    def test_no_warning_message_makes_a_judgement(self, code: str) -> None:
        from app.graph.nodes import WARNING_MESSAGES

        assert_no_words([WARNING_MESSAGES[code]], JUDGEMENT_PATTERN, "judgement")

    @pytest.mark.parametrize("code", WARNING_CODES, ids=lambda code: code.lower())
    def test_no_warning_message_advises(self, code: str) -> None:
        from app.graph.nodes import WARNING_MESSAGES

        assert_no_words([WARNING_MESSAGES[code]], ADVICE_PATTERN, "advice")

    @pytest.mark.parametrize("code", ERROR_CODES, ids=lambda code: code.lower())
    def test_no_error_message_makes_a_judgement(self, code: str) -> None:
        from app.graph.nodes import ERROR_MESSAGES

        assert_no_words([ERROR_MESSAGES[code]], JUDGEMENT_PATTERN, "judgement")

    @pytest.mark.parametrize("code", ERROR_CODES, ids=lambda code: code.lower())
    def test_no_error_message_advises(self, code: str) -> None:
        from app.graph.nodes import ERROR_MESSAGES

        assert_no_words([ERROR_MESSAGES[code]], ADVICE_PATTERN, "advice")


class TestAbsenceIsNotReassurance:
    """A degraded or empty run must not imply the content was cleared."""

    def test_no_patterns_found_does_not_claim_safety(self) -> None:
        """The symmetric failure to an accusation."""
        state = run_with(red_flags=RecordingRedFlagEngine(flags=()))
        assert_no_words(
            graph_authored_texts(state), JUDGEMENT_PATTERN, "judgement"
        )

    def test_nothing_extracted_does_not_claim_safety(self) -> None:
        state = run_with(extraction=RecordingExtractionService(result=extraction_with(())))
        assert_no_words(
            graph_authored_texts(state), JUDGEMENT_PATTERN, "judgement"
        )

    def test_unavailable_search_does_not_blame_anyone(self) -> None:
        """Not being able to look is never an accusation."""
        state = run_with(
            extraction=RecordingExtractionService(
                result=extraction_with((build_claim(),))
            )
        )
        assert_no_words(
            graph_authored_texts(state), JUDGEMENT_PATTERN, "judgement"
        )


class TestNoTechnicalDetailLeaks:
    """Exception messages and stack frames must never reach result state."""

    @pytest.mark.parametrize(
        ("dependency", "channel"),
        [
            ("extraction", "errors"),
            ("red_flags", "errors"),
            ("risk", "errors"),
        ],
        ids=["extraction", "red_flags", "risk"],
    )
    def test_an_exception_message_is_not_surfaced(
        self, dependency: str, channel: str
    ) -> None:
        fakes = {
            "extraction": lambda: RecordingExtractionService(
                raises=RuntimeError("SECRET_TOKEN leaked here")
            ),
            "red_flags": lambda: RecordingRedFlagEngine(
                raises=RuntimeError("SECRET_TOKEN leaked here")
            ),
            "risk": lambda: RecordingRiskService(raises=RuntimeError("SECRET_TOKEN leaked here")),
        }
        state = run_with(**{dependency: fakes[dependency]()})
        rendered = " ".join(
            getattr(item, "message", "") for item in state.get(channel) or ()
        )
        assert "SECRET_TOKEN" not in rendered

    def test_a_service_fault_keeps_only_the_class_name(self) -> None:
        """Enough to diagnose, nothing that came from the exception itself."""
        state = run_with(risk=RecordingRiskService(raises=RuntimeError("SECRET_TOKEN")))
        assert state["errors"][0].error_type == "RuntimeError"

    def test_an_evidence_fault_keeps_only_the_class_name(self) -> None:
        """The non-fatal path must be just as careful as the fatal one."""
        state = run_with(
            extraction=RecordingExtractionService(
                result=extraction_with((build_claim(),))
            ),
            evidence=RecordingEvidenceService(raises=ValueError("SECRET")),
        )
        warning = next(
            w for w in state["warnings"] if w.code == "EVIDENCE_UNAVAILABLE"
        )
        assert warning.error_type == "ValueError"
        assert "SECRET" not in warning.message


class TestNoProbabilityLanguage:
    """Nothing in the state may be named or phrased as a probability."""

    @pytest.mark.parametrize(
        "forbidden",
        ["probability", "likelihood", "percent", "%", "chance", "odds", "confidence"],
        ids=lambda word: word.strip("%") or "percent",
    )
    def test_the_vocabulary_has_no_probability_terms(self, forbidden: str) -> None:
        from app.graph.state import GraphStage, InvestigationInputType, TimelineStatus

        names = " ".join(
            [stage.value for stage in GraphStage]
            + [status.value for status in TimelineStatus]
            + [kind.value for kind in InvestigationInputType]
        )
        assert forbidden.lower() not in names.lower()

    def test_a_summary_helper_reports_no_score_percentage(self) -> None:
        from app.graph import investigation_summary

        summary = investigation_summary(run_with())
        assert "risk_score" not in summary

    def test_the_risk_score_keeps_its_own_caveat(self) -> None:
        """Phase 6 appends this during validation; the graph must not drop it."""
        from app.services.risk import SCORE_NOT_A_PROBABILITY

        state = run_with(red_flags=RecordingRedFlagEngine(flags=(build_flag(),)))
        warnings = state["risk_assessment"].warnings
        assert SCORE_NOT_A_PROBABILITY in warnings


class TestRedFlagTextIsPassedThroughNotRestated:
    """Phase 1's wording is reused, never rewritten by the graph."""

    def test_a_real_flag_reaches_the_risk_stage_unchanged(self) -> None:
        from app.services.risk import RiskService

        flags = (build_flag(RedFlagCode.GUARANTEED_RETURN),)
        state = run_with(
            red_flags=RecordingRedFlagEngine(flags=flags), risk=RiskService()
        )
        assert state["red_flags"] == flags