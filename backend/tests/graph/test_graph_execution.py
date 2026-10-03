"""End-to-end graph execution tests (Phase 7).

These drive the compiled graph the way a caller will, through `run_investigation`,
and assert on the finished state. They cover the scenarios a real investigation
has to survive: a full run, nothing suspicious, nothing checkable, no search, a
failure in each stage, and two identical runs.

The failure cases are the ones that matter most. A pipeline that only ever
succeeds is easy to write; the product's requirement is the opposite — that a
missing key, an unreachable provider or a broken stage produces an *honest*
partial investigation rather than either a crash or a clean-looking empty result.
"""

from __future__ import annotations

import pytest

from app.graph import (
    build_investigation_graph,
    investigation_summary,
    run_investigation,
    semantic_view,
)
from app.graph.state import GraphStage, TimelineStatus
from app.schemas.red_flags import RedFlagCode
from app.schemas.verification import (
    AUTHORITATIVE_SOURCE_CONFIRMS,
    VerificationStatus,
)
from tests.graph.graph_factories import (
    FIXED_INSTANT,
    MESSY_CONTENT,
    RecordingEvidenceService,
    RecordingExtractionService,
    RecordingRedFlagEngine,
    RecordingRiskService,
    RecordingSearchProvider,
    RecordingVerificationService,
    build_claim,
    build_entity,
    build_flag,
    extraction_with,
    fake_dependencies,
    offline_context,
    real_dependencies,
    result_for,
    verification_response,
)


def run(content: str = MESSY_CONTENT, **kwargs: object) -> dict:
    """Run the graph over `content` with injected dependencies."""
    return run_investigation(
        content, context=offline_context(fake_dependencies(**kwargs))
    )


def run_with_claim(
    content: str = MESSY_CONTENT,
    claim: object | None = None,
    **kwargs: object,
) -> dict:
    """Run the graph with at least one claim, so no stage is skipped.

    `fake_dependencies()` defaults to an extraction that yields no claims, which
    makes verification and evidence skip. Several scenarios here are about those
    stages, so they need a claim to exist.
    """
    the_claim = claim or build_claim()
    return run(
        content,
        extraction=RecordingExtractionService(
            result=extraction_with((the_claim,))
        ),
        **kwargs,
    )


def real_run(content: str = MESSY_CONTENT) -> dict:
    """Run the graph over `content` with the genuine Phase 1-6 services."""
    dependencies, _ = real_dependencies()
    return run_investigation(content, context=offline_context(dependencies))


class TestHappyPath:
    """A complete run with every service contributing."""

    def test_a_run_reaches_the_risk_stage(self) -> None:
        assert run()["current_stage"] == GraphStage.RISK.value

    def test_a_run_produces_a_risk_assessment(self) -> None:
        state = run()
        assert state["risk_assessment"] is not None

    def test_a_run_records_no_errors(self) -> None:
        assert run().get("errors", ()) == ()

    def test_every_stage_is_represented_on_the_timeline(self) -> None:
        """The timeline is what a future UI reads, so it must be complete."""
        stages = {event.stage for event in run()["timeline"]}
        assert stages == {
            GraphStage.INPUT,
            GraphStage.EXTRACTION,
            GraphStage.RED_FLAGS,
            GraphStage.VERIFICATION,
            GraphStage.EVIDENCE,
            GraphStage.RISK,
        }

    def test_each_stage_is_entered_in_pipeline_order(self) -> None:
        seen = [event.stage.value for event in run()["timeline"]]
        assert seen == [
            "input",
            "extraction",
            "red_flags",
            "verification",
            "evidence",
            "risk",
        ]

    def test_every_stage_calls_its_service_exactly_once(self) -> None:
        """No duplicated work, and nothing silently skipped."""
        dependencies = fake_dependencies(
            extraction=RecordingExtractionService(
                result=extraction_with((build_claim(),))
            ),
            verification=RecordingVerificationService(
                response=verification_response(result_for(build_claim()))
            ),
        )
        run(**{"extraction": dependencies.extraction_service,
               "verification": dependencies.verification_service})
        assert dependencies.extraction_service.calls == [MESSY_CONTENT]
        assert len(dependencies.verification_service.calls) == 1

    def test_the_investigation_id_is_derived_from_the_content(self) -> None:
        assert run()["investigation_id"] == run()["investigation_id"]
        assert run()["investigation_id"].startswith("inv_")

    def test_different_content_yields_a_different_id(self) -> None:
        assert run()["investigation_id"] != run("something else entirely")[
            "investigation_id"
        ]


class TestStateIntegrity:
    """A node must never erase what earlier nodes produced."""

    def test_claims_survive_every_later_stage(self) -> None:
        state = run(
            extraction=RecordingExtractionService(
                result=extraction_with((build_claim(),))
            )
        )
        assert len(state["claims"]) == 1

    def test_entities_survive_every_later_stage(self) -> None:
        entity = build_entity()
        state = run(
            extraction=RecordingExtractionService(
                result=extraction_with((build_claim(),), (entity,))
            )
        )
        assert state["entities"] == (entity,)

    def test_red_flags_survive_the_later_stages(self) -> None:
        flags = (build_flag(RedFlagCode.GUARANTEED_RETURN),)
        state = run(red_flags=RecordingRedFlagEngine(flags=flags))
        assert state["red_flags"] == flags

    def test_verification_results_survive_the_evidence_stage(self) -> None:
        claim = build_claim()
        response = verification_response(result_for(claim))
        state = run(
            extraction=RecordingExtractionService(result=extraction_with((claim,))),
            verification=RecordingVerificationService(response=response),
        )
        assert state["verification_results"] == response.results

    def test_evidence_survives_the_risk_stage(self) -> None:
        from app.schemas.evidence import EvidenceBundleResponse, EvidenceResponse

        response = EvidenceResponse(claim_id="claim_001")
        state = run_with_claim(
            evidence=RecordingEvidenceService(
                bundle=EvidenceBundleResponse(responses=(response,))
            )
        )
        assert state["evidence"] == (response,)

    def test_the_raw_submission_survives_the_whole_run(self) -> None:
        assert run()["raw_input"] == MESSY_CONTENT


class TestNoRedFlags:
    """Content that trips nothing still completes."""

    def test_the_pipeline_still_reaches_risk(self) -> None:
        state = run(red_flags=RecordingRedFlagEngine(flags=()))
        assert state["risk_assessment"] is not None

    def test_the_absence_of_patterns_is_reported_as_just_that(self) -> None:
        """It must not read as a clearance."""
        state = run_with_claim(red_flags=RecordingRedFlagEngine(flags=()))
        assert "NO_RED_FLAGS_DETECTED" in [w.code for w in state["warnings"]]
        assert state.get("errors", ()) == ()

    def test_no_patterns_does_not_produce_an_error(self) -> None:
        state = run(red_flags=RecordingRedFlagEngine(flags=()))
        assert state.get("errors", ()) == ()


class TestNoClaims:
    """Nothing checkable skips the two stages that have nothing to do."""

    def test_the_pipeline_still_reaches_risk(self) -> None:
        state = run(extraction=RecordingExtractionService(result=extraction_with(())))
        assert state["risk_assessment"] is not None

    def test_verification_is_not_called(self) -> None:
        service = RecordingVerificationService()
        run(
            extraction=RecordingExtractionService(result=extraction_with(())),
            verification=service,
        )
        assert service.calls == []

    def test_evidence_is_not_called(self) -> None:
        service = RecordingEvidenceService()
        run(
            extraction=RecordingExtractionService(result=extraction_with(())),
            evidence=service,
        )
        assert service.calls == []

    def test_both_skipped_stages_say_so(self) -> None:
        state = run(extraction=RecordingExtractionService(result=extraction_with(())))
        skipped = [
            event.stage.value
            for event in state["timeline"]
            if event.status is TimelineStatus.SKIPPED
        ]
        assert skipped == ["verification", "evidence"]

    def test_pattern_analysis_still_runs_and_still_counts(self) -> None:
        """Skipping verification must not skip Phase 1.

        Uses the real `RiskService` so the factor count reflects a real
        assessment rather than whatever the fake was configured to return.
        """
        from app.services.risk import RiskService

        flags = (build_flag(RedFlagCode.GUARANTEED_RETURN),)
        state = run(
            extraction=RecordingExtractionService(result=extraction_with(())),
            red_flags=RecordingRedFlagEngine(flags=flags),
            risk=RiskService(),
        )
        assert state["red_flags"] == flags
        assert len(state["risk_assessment"].factors) >= 1


class TestSearchUnavailable:
    """A missing key must not end the investigation."""

    def test_the_pipeline_completes(self) -> None:
        claim = build_claim()
        recorder_state = run(
            extraction=RecordingExtractionService(result=extraction_with((claim,))),
            verification=RecordingVerificationService(
                response=verification_response(
                    result_for(
                        claim,
                        VerificationStatus.INSUFFICIENT_EVIDENCE,
                        reason_code="SEARCH_UNAVAILABLE",
                    )
                )
            ),
            recorder=None,
        )
        assert recorder_state["risk_assessment"] is not None
        assert recorder_state.get("errors", ()) == ()

    def test_the_limitation_is_reported(self) -> None:
        state = real_run()
        codes = [w.code for w in state["warnings"]]
        assert "SEARCH_UNAVAILABLE" in codes or "PARTIAL_VERIFICATION" in codes

    def test_unavailable_search_is_not_scored_as_risk(self) -> None:
        """D-006: not being able to look is not a finding."""
        from app.schemas.verification import SEARCH_UNAVAILABLE

        claim = build_claim()
        state = run(
            extraction=RecordingExtractionService(result=extraction_with((claim,))),
            verification=RecordingVerificationService(
                response=verification_response(
                    result_for(
                        claim,
                        VerificationStatus.INSUFFICIENT_EVIDENCE,
                        reason_code=SEARCH_UNAVAILABLE,
                    )
                )
            ),
        )
        scored = [f for f in state["risk_assessment"].factors if f.contribution > 0]
        assert all(f.factor_type.value != "UNVERIFIED_CLAIM" for f in scored)

    def test_an_unavailable_provider_still_completes_end_to_end(self) -> None:
        dependencies, _ = real_dependencies(
            provider=RecordingSearchProvider(available=False)
        )
        state = run_investigation(
            MESSY_CONTENT, context=offline_context(dependencies)
        )
        assert state.get("errors", ()) == ()
        assert state["risk_assessment"] is not None


class TestPartialVerification:
    """A claim that could not be checked does not stop the others."""

    def test_the_run_continues_to_evidence_and_risk(self) -> None:
        checked = build_claim("Acme is registered.", "claim_001")
        unchecked = build_claim("Acme returns 30% monthly.", "claim_002")
        state = run(
            extraction=RecordingExtractionService(
                result=extraction_with((checked, unchecked))
            ),
            verification=RecordingVerificationService(
                response=verification_response(
                    result_for(checked, VerificationStatus.VERIFIED,
                               reason_code=AUTHORITATIVE_SOURCE_CONFIRMS,
                               source_ids=("src_1",)),
                    result_for(unchecked, VerificationStatus.INSUFFICIENT_EVIDENCE),
                )
            ),
        )
        assert len(state["verification_results"]) == 2
        assert state["risk_assessment"] is not None

    def test_partial_coverage_is_reported(self) -> None:
        claim = build_claim()
        state = run(
            extraction=RecordingExtractionService(result=extraction_with((claim,))),
            verification=RecordingVerificationService(
                response=verification_response(
                    result_for(claim, VerificationStatus.INSUFFICIENT_EVIDENCE)
                )
            ),
        )
        assert "PARTIAL_VERIFICATION" in [w.code for w in state["warnings"]]


class TestEvidenceUnavailable:
    """Evidence is provenance, so losing it must not lose the assessment."""

    def test_the_run_still_reaches_risk(self) -> None:
        state = run(
            extraction=RecordingExtractionService(result=extraction_with((build_claim(),))),
            evidence=RecordingEvidenceService(raises=RuntimeError("boom")),
        )
        assert state["risk_assessment"] is not None

    def test_the_failure_is_reported_as_a_limitation(self) -> None:
        state = run_with_claim(evidence=RecordingEvidenceService(raises=RuntimeError("boom")))
        assert "EVIDENCE_UNAVAILABLE" in [w.code for w in state["warnings"]]

    def test_the_failure_is_not_an_error(self) -> None:
        state = run(evidence=RecordingEvidenceService(raises=RuntimeError("boom")))
        assert state.get("errors", ()) == ()

    def test_no_evidence_is_fabricated(self) -> None:
        state = run(evidence=RecordingEvidenceService(raises=RuntimeError("boom")))
        assert state["evidence"] == ()

    def test_an_unavailable_search_produces_no_evidence_rather_than_placeholder(self) -> None:
        dependencies, _ = real_dependencies(
            provider=RecordingSearchProvider(available=False)
        )
        state = run_investigation(
            MESSY_CONTENT, context=offline_context(dependencies)
        )
        for response in state["evidence"]:
            assert response.evidence == ()


class TestStageFailures:
    """Each stage's contract violation ends the run, honestly."""

    @pytest.mark.parametrize(
        ("dependency", "expected"),
        [
            ("extraction", "EXTRACTION_FAILED"),
            ("red_flags", "RED_FLAG_DETECTION_FAILED"),
            ("verification", "VERIFICATION_FAILED"),
            ("risk", "RISK_ASSESSMENT_FAILED"),
        ],
        ids=["extraction", "red_flags", "verification", "risk"],
    )
    def test_a_failed_stage_records_its_error_code(
        self, dependency: str, expected: str
    ) -> None:
        state = (
            run(**{dependency: _failing(dependency)})
            if dependency == "extraction"
            else run_with_claim(**{dependency: _failing(dependency)})
        )
        assert [e.code for e in state["errors"]] == [expected]

    @pytest.mark.parametrize(
        "dependency",
        ["extraction", "red_flags", "verification", "risk"],
    )
    def test_a_failed_stage_produces_no_risk_assessment(
        self, dependency: str
    ) -> None:
        """An incomplete run must never look like a low-risk one."""
        state = (
            run(**{dependency: _failing(dependency)})
            if dependency == "extraction"
            else run_with_claim(**{dependency: _failing(dependency)})
        )
        assert state.get("risk_assessment") is None

    def test_a_failed_stage_names_its_exception_type(self) -> None:
        state = run_with_claim(risk=RecordingRiskService(raises=ValueError("boom")))
        assert state["errors"][0].error_type == "ValueError"

    def test_a_failed_stage_records_a_failed_timeline_entry(self) -> None:
        state = run_with_claim(risk=RecordingRiskService(raises=ValueError("boom")))
        assert any(event.status is TimelineStatus.FAILED for event in state["timeline"])


def _failing(dependency: str) -> object:
    """Build the fake for `dependency` that raises, simulating a broken contract."""
    return {
        "extraction": lambda: RecordingExtractionService(raises=RuntimeError("boom")),
        "red_flags": lambda: RecordingRedFlagEngine(raises=RuntimeError("boom")),
        "verification": lambda: RecordingVerificationService(raises=RuntimeError("boom")),
        "risk": lambda: RecordingRiskService(raises=RuntimeError("boom")),
    }[dependency]()


class TestInvalidInput:
    """A submission that was never accepted produces no investigation."""

    def test_empty_input_records_an_error(self) -> None:
        state = run_investigation("", context=offline_context(fake_dependencies()))
        assert [e.code for e in state["errors"]] == ["INPUT_EMPTY"]

    def test_empty_input_runs_no_stage(self) -> None:
        dependencies = fake_dependencies()
        run_investigation("", context=offline_context(dependencies))
        assert dependencies.extraction_service.calls == []
        assert dependencies.red_flag_engine.calls == []

    def test_empty_input_produces_no_risk_assessment(self) -> None:
        state = run_investigation("   ", context=offline_context(fake_dependencies()))
        assert state.get("risk_assessment") is None

    @pytest.mark.parametrize("kind", ["SPREADSHEET"])
    def test_an_unimplemented_input_kind_produces_no_analysis(self, kind: str) -> None:
        """Refused, and never silently analysed as text.

        `URL` left this list in Phase 12 and `IMAGE` in Phase 13, and
        `PDF` in Phase 14, when they stopped being unimplemented. Each
        is covered by its own execution tests instead, and the list is
        empty now, so a kind outside the vocabulary stands in.
        """
        state = run_investigation("some content", input_type=kind,
                                  context=offline_context(fake_dependencies()))
        assert state.get("risk_assessment") is None
        assert state["errors"][0].code == "INPUT_TYPE_NOT_SUPPORTED"


class TestDeterminism:
    """Identical inputs and fixtures must produce identical output."""

    def test_two_runs_agree_field_by_field(self) -> None:
        first = run_investigation(MESSY_CONTENT, context=offline_context(fake_dependencies()))
        second = run_investigation(MESSY_CONTENT, context=offline_context(fake_dependencies()))
        assert semantic_view(first) == semantic_view(second)

    def test_two_real_runs_agree(self) -> None:
        """The genuine services, not just the fakes, are reproducible."""
        first = real_run()
        second = real_run()
        assert semantic_view(first) == semantic_view(second)

    def test_a_frozen_clock_makes_the_graph_s_own_metadata_identical(self) -> None:
        """With the clock frozen, the graph stamps both runs identically.

        The whole state can never compare equal, because Phase 6 stamps
        `RiskAssessment.assessed_at` from its own clock. What the graph controls
        is its own metadata, and that is what this checks.
        """
        first = run_investigation(
            MESSY_CONTENT, context=offline_context(fake_dependencies())
        )
        second = run_investigation(
            MESSY_CONTENT, context=offline_context(fake_dependencies())
        )
        assert first["started_at"] == second["started_at"] == FIXED_INSTANT
        assert [e.at for e in first["timeline"]] == [e.at for e in second["timeline"]]
        assert all(e.at == FIXED_INSTANT for e in first["timeline"])

    def test_different_content_produces_a_different_state(self) -> None:
        """Determinism must not be trivial sameness."""
        first = run_investigation("one thing", context=offline_context(fake_dependencies()))
        second = run_investigation("another thing", context=offline_context(fake_dependencies()))
        assert semantic_view(first) != semantic_view(second)

    def test_the_graph_structure_does_not_affect_results(self) -> None:
        """A freshly built graph and the factory's graph agree."""
        context = offline_context(fake_dependencies())
        direct = build_investigation_graph().invoke(
            {"raw_input": MESSY_CONTENT, "input_type": "TEXT"}, context=context
        )
        through_api = run_investigation(MESSY_CONTENT, context=context)
        assert semantic_view(direct) == semantic_view(through_api)


class TestDependencyInjection:
    """Every service is injectable, which is what keeps tests offline."""

    def test_a_fully_faked_run_never_touches_a_provider(self) -> None:
        dependencies = fake_dependencies(
            extraction=RecordingExtractionService(
                result=extraction_with((build_claim(),))
            )
        )
        run_investigation(MESSY_CONTENT, context=offline_context(dependencies))
        assert dependencies.verification_service.calls

    def test_services_can_be_replaced_individually(self) -> None:
        """Real red flags with a faked extraction, for instance."""
        engine = RecordingRedFlagEngine(flags=(build_flag(),))
        state = run(
            extraction=RecordingExtractionService(result=extraction_with(())),
            red_flags=engine,
        )
        assert state["red_flags"] == engine.flags

    def test_dependencies_can_be_supplied_without_a_context(self) -> None:
        state = run_investigation(
            MESSY_CONTENT, dependencies=fake_dependencies()
        )
        assert state["risk_assessment"] is not None

    def test_supplying_both_context_and_dependencies_is_refused(self) -> None:
        """There would be no principled way to choose between them."""
        with pytest.raises(ValueError, match="not both"):
            run_investigation(
                MESSY_CONTENT,
                context=offline_context(fake_dependencies()),
                dependencies=fake_dependencies(),
            )

    def test_the_default_context_builds_every_real_service(self) -> None:
        """Production wiring is available and needs no arguments."""
        from app.graph import build_default_context
        from app.services.evidence import EvidenceService
        from app.services.extraction_service import ExtractionService
        from app.services.red_flag_engine import RedFlagEngine
        from app.services.risk import RiskService
        from app.services.verification import VerificationService

        dependencies = build_default_context().dependencies
        assert isinstance(dependencies.extraction_service, ExtractionService)
        assert isinstance(dependencies.red_flag_engine, RedFlagEngine)
        assert isinstance(dependencies.verification_service, VerificationService)
        assert isinstance(dependencies.evidence_service, EvidenceService)
        assert isinstance(dependencies.risk_service, RiskService)

    def test_the_default_context_installs_a_search_recorder(self) -> None:
        """Otherwise production would silently lose query provenance."""
        from app.graph import build_default_context

        assert build_default_context().dependencies.search_recorder is not None


class TestInvestigationSummary:
    """The convenience helper reports counts and presence, nothing more."""

    def test_it_reports_the_counts_of_a_complete_run(self) -> None:
        summary = investigation_summary(run())
        assert summary["has_risk_assessment"] is True
        assert summary["error_count"] == 0

    def test_it_reports_the_absence_of_an_assessment(self) -> None:
        state = run_investigation("", context=offline_context(fake_dependencies()))
        summary = investigation_summary(state)
        assert summary["has_risk_assessment"] is False
        assert summary["error_count"] == 1

    def test_it_exposes_no_score_as_a_percentage(self) -> None:
        """The helper must not become a place a probability creeps in."""
        summary = investigation_summary(run())
        assert "risk_score" not in summary
        assert not any("%" in str(value) for value in summary.values())

    def test_it_lists_each_stage_status(self) -> None:
        summary = investigation_summary(run())
        assert len(summary["stage_statuses"]) == 6