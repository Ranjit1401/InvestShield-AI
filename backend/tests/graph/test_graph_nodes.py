"""Per-node tests (Phase 7).

Each node is called directly with a state and a context, so a failure points at
one stage rather than at "the pipeline produced something unexpected".

Two things are pinned here.

**Call contracts.** Each node passes exactly the objects the service it owns
actually needs, in the order its real API declares. These tests exist because a
graph can appear to work while quietly handing a stage the wrong thing — passing
normalized text where spans must be anchored to the submitted string, or handing
the risk stage evidence in place of verification results.

**Failure handling.** Every node's failure path is exercised with a named
exception, and the resulting state is asserted rather than merely "did not
raise". A node that swallowed an exception into silence would satisfy a
smoke test; the product's own rule is that nothing is swallowed silently.
"""

from __future__ import annotations

import pytest

from app.graph.nodes import (
    ERROR_CODES,
    WARNING_CODES,
    evidence_node,
    extraction_node,
    input_node,
    red_flags_node,
    risk_node,
    verification_node,
)
from app.graph.state import GraphStage, TimelineStatus
from app.schemas.red_flags import RedFlagCode
from app.schemas.verification import VerificationStatus
from tests.graph.graph_factories import (
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
    result_for,
    verification_response,
)


def context_with(**kwargs: object) -> object:
    """Build an offline context whose dependencies are the given fakes."""
    return offline_context(fake_dependencies(**kwargs))


class TestInputNode:
    """Validating the submission."""

    def test_valid_text_is_accepted(self) -> None:
        result = input_node(
            {"raw_input": "some content", "input_type": "TEXT"}, context_with()
        )
        assert result.get("errors", ()) == ()
        assert result["investigation_id"].startswith("inv_")

    def test_the_submitted_text_is_passed_through_untouched(self) -> None:
        """Red-flag spans index this string, so it must not be normalized here."""
        submitted = "  Acme guarantees 30% returns  "
        result = input_node({"raw_input": submitted, "input_type": "TEXT"}, context_with())
        assert result["raw_input"] == submitted

    def test_an_empty_submission_is_rejected(self) -> None:
        result = input_node({"raw_input": "", "input_type": "TEXT"}, context_with())
        assert [error.code for error in result["errors"]] == ["INPUT_EMPTY"]

    def test_whitespace_only_is_rejected(self) -> None:
        result = input_node({"raw_input": "   \n\t ", "input_type": "TEXT"}, context_with())
        assert [error.code for error in result["errors"]] == ["INPUT_EMPTY"]

    @pytest.mark.parametrize("kind", ["SPREADSHEET"])
    def test_an_unimplemented_input_type_is_refused(self, kind: str) -> None:
        """A kind this version does not analyse is refused, not guessed at.

        `URL` was in this list until Phase 12 and `IMAGE` until Phase 13,
        and `PDF` until Phase 14, which implemented them. The list is empty
        now — every kind the product's vocabulary carries is analysed — so a
        kind outside the vocabulary stands in for the one shape of submission
        left that no stage may touch.
        """
        result = input_node({"raw_input": "x", "input_type": kind}, context_with())
        assert [error.code for error in result["errors"]] == [
            "INPUT_TYPE_NOT_SUPPORTED"
        ]

    def test_an_unknown_input_type_is_rejected(self) -> None:
        result = input_node({"raw_input": "x", "input_type": "SPREADSHEET"}, context_with())
        assert [error.code for error in result["errors"]] == [
            "INPUT_TYPE_NOT_SUPPORTED"
        ]

    def test_a_rejected_submission_still_records_a_start_time(self) -> None:
        """So a run that stopped still has a record of when it happened."""
        result = input_node({"raw_input": "", "input_type": "TEXT"}, context_with())
        assert result["started_at"] is not None


class TestExtractionNode:
    """Delegating to Phase 2."""

    def test_the_submitted_text_reaches_the_service(self) -> None:
        service = RecordingExtractionService()
        extraction_node({"raw_input": "the content"}, context_with(extraction=service))
        assert service.calls == ["the content"]

    def test_claims_entities_and_links_reach_the_state(self) -> None:
        claim = build_claim()
        entity = build_entity()
        service = RecordingExtractionService(
            result=extraction_with((claim,), (entity,))
        )
        result = extraction_node({"raw_input": "x"}, context_with(extraction=service))
        assert result["claims"] == (claim,)
        assert result["entities"] == (entity,)

    def test_the_whole_extraction_is_kept_for_phase_four(self) -> None:
        """Phase 4 has `verify_extraction`, which needs the whole object."""
        payload = extraction_with((build_claim(),))
        service = RecordingExtractionService(result=payload)
        result = extraction_node({"raw_input": "x"}, context_with(extraction=service))
        assert result["extraction"] is payload

    def test_processing_warnings_are_preserved(self) -> None:
        """Phase 2's own limitations must not be dropped by the graph."""
        service = RecordingExtractionService(
            result=extraction_with((build_claim(),), warnings=("Claim dropped.",))
        )
        result = extraction_node({"raw_input": "x"}, context_with(extraction=service))
        assert [w.message for w in result["warnings"]] == ["Claim dropped."]

    def test_a_fallback_extraction_is_reported_as_weaker(self) -> None:
        from app.schemas.extraction import ExtractionMode

        service = RecordingExtractionService(
            result=extraction_with((build_claim(),), mode=ExtractionMode.FALLBACK)
        )
        result = extraction_node({"raw_input": "x"}, context_with(extraction=service))
        assert "EXTRACTION_FALLBACK" in [w.code for w in result["warnings"]]

    def test_no_claims_is_reported(self) -> None:
        service = RecordingExtractionService(result=extraction_with(()))
        result = extraction_node({"raw_input": "x"}, context_with(extraction=service))
        assert "NO_CLAIMS_EXTRACTED" in [w.code for w in result["warnings"]]

    def test_a_fallback_with_no_claims_reports_only_the_missing_claims(self) -> None:
        """An empty fallback is not additionally a degraded extraction."""
        from app.schemas.extraction import ExtractionMode

        service = RecordingExtractionService(
            result=extraction_with((), mode=ExtractionMode.FALLBACK)
        )
        result = extraction_node({"raw_input": "x"}, context_with(extraction=service))
        assert [w.code for w in result["warnings"]] == ["NO_CLAIMS_EXTRACTED"]

    def test_a_contract_violation_stops_the_run(self) -> None:
        """Phase 2 documents that `extract` never raises, so this is a bug."""
        service = RecordingExtractionService(raises=RuntimeError("boom"))
        result = extraction_node({"raw_input": "x"}, context_with(extraction=service))
        assert [e.code for e in result["errors"]] == ["EXTRACTION_FAILED"]

    def test_the_exception_message_is_not_surfaced(self) -> None:
        """Only the class name is kept; the message stays in the log."""
        service = RecordingExtractionService(raises=RuntimeError("secret detail"))
        result = extraction_node({"raw_input": "x"}, context_with(extraction=service))
        assert result["errors"][0].error_type == "RuntimeError"
        assert "secret detail" not in result["errors"][0].message


class TestRedFlagsNode:
    """Delegating to Phase 1."""

    def test_the_submitted_text_reaches_the_engine(self) -> None:
        engine = RecordingRedFlagEngine()
        red_flags_node({"raw_input": "the content"}, context_with(red_flags=engine))
        assert engine.calls == ["the content"]

    def test_normalized_text_is_never_substituted_for_the_submitted_text(self) -> None:
        """A span computed over a normalized variant would point at the wrong text."""
        engine = RecordingRedFlagEngine()
        red_flags_node(
            {
                "raw_input": "submitted",
                "extracted_text": "NORMALIZED SOMETHING ELSE",
            },
            context_with(red_flags=engine),
        )
        assert engine.calls == ["submitted"]

    def test_detected_flags_reach_the_state_unchanged(self) -> None:
        flags = (build_flag(RedFlagCode.GUARANTEED_RETURN),)
        engine = RecordingRedFlagEngine(flags=flags)
        result = red_flags_node({"raw_input": "x"}, context_with(red_flags=engine))
        assert result["red_flags"] == flags

    def test_no_flags_reports_the_absence_of_patterns(self) -> None:
        result = red_flags_node({"raw_input": "x"}, context_with(red_flags=RecordingRedFlagEngine()))
        assert [w.code for w in result["warnings"]] == ["NO_RED_FLAGS_DETECTED"]

    def test_no_score_is_computed_here(self) -> None:
        """Scoring is Phase 6's, and this node has no access to a weight table."""
        result = red_flags_node(
            {"raw_input": "x"},
            context_with(red_flags=RecordingRedFlagEngine((build_flag(),))),
        )
        assert "risk_assessment" not in result
        assert "score" not in result

    def test_a_contract_violation_stops_the_run(self) -> None:
        engine = RecordingRedFlagEngine(raises=RuntimeError("boom"))
        result = red_flags_node({"raw_input": "x"}, context_with(red_flags=engine))
        assert [e.code for e in result["errors"]] == ["RED_FLAG_DETECTION_FAILED"]


class TestVerificationNode:
    """Delegating to Phase 4."""

    def test_claims_and_entities_reach_the_service(self) -> None:
        claim = build_claim()
        entity = build_entity()
        service = RecordingVerificationService()
        verification_node(
            {"claims": (claim,), "entities": (entity,)},
            context_with(verification=service),
        )
        assert service.calls == [((claim,), (entity,))]

    def test_results_reach_the_state_in_order(self) -> None:
        claim = build_claim()
        response = verification_response(result_for(claim))
        service = RecordingVerificationService(response=response)
        result = verification_node(
            {"claims": (claim,), "entities": ()}, context_with(verification=service)
        )
        assert result["verification_results"] == response.results

    def test_no_claims_skips_the_service_entirely(self) -> None:
        """The guard that replaces a routing branch for this case."""
        service = RecordingVerificationService()
        result = verification_node(
            {"claims": (), "entities": ()}, context_with(verification=service)
        )
        assert service.calls == []
        assert result["verification_results"] == ()

    def test_a_skipped_stage_says_so_on_the_timeline(self) -> None:
        result = verification_node(
            {"claims": (), "entities": ()}, context_with()
        )
        assert [event.status for event in result["timeline"]] == [
            TimelineStatus.SKIPPED
        ]

    def test_unverified_claims_report_partial_coverage(self) -> None:
        claim = build_claim()
        service = RecordingVerificationService(
            response=verification_response(
                result_for(claim, VerificationStatus.UNVERIFIED)
            )
        )
        result = verification_node(
            {"claims": (claim,), "entities": ()}, context_with(verification=service)
        )
        assert "PARTIAL_VERIFICATION" in [w.code for w in result["warnings"]]

    def test_fully_verified_claims_report_no_gap(self) -> None:
        """A confirmed answer is a complete answer, not a limitation."""
        from app.schemas.verification import AUTHORITATIVE_SOURCE_CONFIRMS

        claim = build_claim()
        service = RecordingVerificationService(
            response=verification_response(
                result_for(
                    claim,
                    VerificationStatus.VERIFIED,
                    reason_code=AUTHORITATIVE_SOURCE_CONFIRMS,
                    matched_result_ids=("res_abc123",),
                    source_ids=("src_abc123",),
                )
            )
        )
        result = verification_node(
            {"claims": (claim,), "entities": ()}, context_with(verification=service)
        )
        assert "PARTIAL_VERIFICATION" not in [w.code for w in result["warnings"]]

    def test_a_missing_recorder_is_reported(self) -> None:
        """Without recorded searches the evidence stage cannot cite a query."""
        claim = build_claim()
        service = RecordingVerificationService(
            response=verification_response(result_for(claim))
        )
        result = verification_node(
            {"claims": (claim,), "entities": ()},
            context_with(verification=service, recorder=None),
        )
        assert "SEARCH_RESULTS_NOT_RECORDED" in [w.code for w in result["warnings"]]

    def test_an_unavailable_search_is_reported(self) -> None:
        from app.graph.context import RecordingSearchService

        recorder = RecordingSearchService(_unavailable_service())
        claim = build_claim()
        result = verification_node(
            {"claims": (claim,), "entities": ()},
            context_with(
                verification=RecordingVerificationService(
                    response=verification_response(result_for(claim))
                ),
                recorder=recorder,
            ),
        )
        assert "SEARCH_UNAVAILABLE" in [w.code for w in result["warnings"]]

    def test_a_contract_violation_stops_the_run(self) -> None:
        service = RecordingVerificationService(raises=RuntimeError("boom"))
        result = verification_node(
            {"claims": (build_claim(),), "entities": ()},
            context_with(verification=service),
        )
        assert [e.code for e in result["errors"]] == ["VERIFICATION_FAILED"]


def _unavailable_service() -> object:
    """A search service that reports itself unusable."""
    from app.services.search import SearchService

    return SearchService(
        settings=__import__(
            "tests.graph.graph_factories", fromlist=["offline_settings"]
        ).offline_settings(),
        provider=RecordingSearchProvider(available=False),
    )


class TestEvidenceNode:
    """Delegating to Phase 5."""

    def test_claims_entities_and_verification_reach_the_service(self) -> None:
        claim = build_claim()
        entity = build_entity()
        response = verification_response(result_for(claim))
        service = RecordingEvidenceService()
        evidence_node(
            {"claims": (claim,), "entities": (entity,), "verification": response},
            context_with(evidence=service),
        )
        call = service.calls[0]
        assert call["claims"] == (claim,)
        assert call["entities"] == (entity,)
        assert call["verification"] is response

    def test_recorded_results_are_passed_by_claim_id(self) -> None:
        """The grouping is what lets Phase 5 tie a document to a claim."""
        claim = build_claim()
        service = RecordingEvidenceService()
        recorder = _recorder_holding(("acme query", "https://www.sebi.gov.in/a"))
        evidence_node(
            {
                "claims": (claim,),
                "verification": verification_response(result_for(claim)),
                "verification_results": (result_for(claim, queries=("acme query",)),),
            },
            context_with(evidence=service, recorder=recorder),
        )
        grouped = service.calls[0]["results_by_claim"]
        assert len(grouped[claim.id]) == 1

    def test_no_claims_skips_the_service_entirely(self) -> None:
        service = RecordingEvidenceService()
        result = evidence_node({"claims": ()}, context_with(evidence=service))
        assert service.calls == []
        assert result["evidence"] == ()

    def test_assembled_responses_reach_the_state(self) -> None:
        from app.schemas.evidence import EvidenceBundleResponse, EvidenceResponse

        response = EvidenceResponse(claim_id="claim_001")
        service = RecordingEvidenceService(
            bundle=EvidenceBundleResponse(responses=(response,))
        )
        result = evidence_node(
            {"claims": (build_claim(),)}, context_with(evidence=service)
        )
        assert result["evidence"] == (response,)

    def test_a_failure_degrades_rather_than_stopping(self) -> None:
        """Phase 6 established evidence carries no weight, so it must not stop a run."""
        service = RecordingEvidenceService(raises=RuntimeError("boom"))
        result = evidence_node(
            {"claims": (build_claim(),)}, context_with(evidence=service)
        )
        assert result.get("errors", ()) == ()
        assert "EVIDENCE_UNAVAILABLE" in [w.code for w in result["warnings"]]

    def test_a_failure_records_the_exception_type_for_diagnosis(self) -> None:
        service = RecordingEvidenceService(raises=RuntimeError("boom"))
        result = evidence_node(
            {"claims": (build_claim(),)}, context_with(evidence=service)
        )
        assert result["warnings"][0].error_type == "RuntimeError"

    def test_a_failure_does_not_leak_the_exception_message(self) -> None:
        service = RecordingEvidenceService(raises=RuntimeError("secret detail"))
        result = evidence_node(
            {"claims": (build_claim(),)}, context_with(evidence=service)
        )
        assert "secret detail" not in result["warnings"][0].message


def _recorder_holding(*pairs: tuple[str, str]) -> object:
    """Build a recorder already holding results for the given query/result pairs."""
    from app.graph.context import RecordingSearchService
    from app.schemas.search import SearchResponse, SearchResult
    from tests.graph.graph_factories import offline_settings

    class _Service:
        available = True
        provider_name = "stub"

        def search(self, query: str, max_results: int = 5) -> SearchResponse:
            return SearchResponse(query=query)

    recorder = RecordingSearchService(_Service())
    grouped: dict[str, list[SearchResult]] = {}
    for query, url in pairs:
        grouped.setdefault(query, []).append(
            SearchResult(title="t", url=url, position=1)
        )
    for query, results in grouped.items():
        recorder._responses.append(
            SearchResponse(query=query, results=tuple(results))
        )
    return recorder


class TestRiskNode:
    """Delegating to Phase 6."""

    def test_all_four_inputs_reach_the_service(self) -> None:
        claim = build_claim()
        flag = build_flag()
        verification = result_for(claim)
        service = RecordingRiskService()
        risk_node(
            {
                "claims": (claim,),
                "verification_results": (verification,),
                "evidence": ("evidence-placeholder",),
                "red_flags": (flag,),
            },
            context_with(risk=service),
        )
        call = service.calls[0]
        assert call["claims"] == (claim,)
        assert call["verification_results"] == (verification,)
        assert call["evidence"] == ("evidence-placeholder",)
        assert call["red_flags"] == (flag,)

    def test_the_assessment_reaches_the_state(self) -> None:
        service = RecordingRiskService()
        result = risk_node({}, context_with(risk=service))
        assert result["risk_assessment"] is service.assessment

    def test_no_arithmetic_happens_in_the_node(self) -> None:
        """The node stores what Phase 6 computed; it adds nothing of its own."""
        service = RecordingRiskService()
        result = risk_node(
            {"red_flags": (build_flag(), build_flag(RedFlagCode.URGENCY_PRESSURE, start=50, end=90),)},
            context_with(risk=service),
        )
        assert result["risk_assessment"].risk_score == service.assessment.risk_score

    def test_a_failure_stops_the_run(self) -> None:
        """A run with no score must not read like a run with a low score."""
        service = RecordingRiskService(raises=RuntimeError("boom"))
        result = risk_node({}, context_with(risk=service))
        assert [e.code for e in result["errors"]] == ["RISK_ASSESSMENT_FAILED"]
        assert "risk_assessment" not in result

    def test_a_failure_does_not_leak_the_exception_message(self) -> None:
        service = RecordingRiskService(raises=RuntimeError("secret detail"))
        result = risk_node({}, context_with(risk=service))
        assert "secret detail" not in result["errors"][0].message


class TestCodeVocabulary:
    """The stable code tables are complete and self-consistent."""

    @pytest.mark.parametrize(
        "code", WARNING_CODES, ids=lambda code: code.lower()
    )
    def test_every_warning_code_has_fixed_wording(self, code: str) -> None:
        """A caller branches on the code, so every code must resolve to text."""
        from app.graph.nodes import WARNING_MESSAGES

        assert WARNING_MESSAGES[code].strip()

    @pytest.mark.parametrize(
        "code", ERROR_CODES, ids=lambda code: code.lower()
    )
    def test_every_error_code_has_fixed_wording(self, code: str) -> None:
        from app.graph.nodes import ERROR_MESSAGES

        assert ERROR_MESSAGES[code].strip()

    def test_the_two_code_tables_do_not_overlap(self) -> None:
        """A code cannot mean both a limitation and a failure."""
        assert not set(WARNING_CODES) & set(ERROR_CODES)

    def test_every_stage_that_records_a_limitation_is_represented(self) -> None:
        """The stages the product cares about are all present in the vocabulary."""
        assert {stage.value for stage in GraphStage} == {
            "input",
            "extraction",
            "red_flags",
            "verification",
            "evidence",
            "risk",
            "completed",
        }