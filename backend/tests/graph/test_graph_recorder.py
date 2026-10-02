"""`RecordingSearchService` tests (Phase 7).

The recorder exists to solve one problem: Phase 4 performs the searches and then
discards the `SearchResponse` objects, while Phase 5 needs them to know which
query surfaced which document. These tests pin both halves of the solution — that
recording is a free side effect of delegation, and that mapping responses back to
claims by `VerificationResult.queries` cannot attach a document to a claim that
did not cause it.

The negative tests are the important ones. A mapping bug here would put evidence
in front of a verification that never saw the underlying document, which is
precisely what D-023 forbids.
"""

from __future__ import annotations

from app.graph.context import RecordingSearchService
from app.schemas.search import SearchResponse
from app.services.search import SearchService
from tests.graph.graph_factories import (
    RecordingSearchProvider,
    build_claim,
    real_dependencies,
    sebi_result,
    offline_settings,
)


def recorder_with(
    *results: object, available: bool = True
) -> tuple[RecordingSearchService, RecordingSearchProvider]:
    """Build a recorder over a fixture provider, returning both."""
    provider = RecordingSearchProvider(results, available=available)
    inner = SearchService(settings=offline_settings(), provider=provider)
    return RecordingSearchService(inner), provider


class TestDelegation:
    """Every call reaches the wrapped service unchanged."""

    def test_a_search_is_delegated_to_the_inner_service(self) -> None:
        recorder, provider = recorder_with(sebi_result())
        recorder.search("acme sebi", 5)
        assert provider.queries == ["acme sebi"]

    def test_the_inner_response_is_returned_unchanged(self) -> None:
        recorder, _ = recorder_with(sebi_result())
        response = recorder.search("acme", 5)
        assert isinstance(response, SearchResponse)
        assert len(response.results) == 1

    def test_availability_is_delegated(self) -> None:
        assert recorder_with(available=True)[0].available is True
        assert recorder_with(available=False)[0].available is False

    def test_the_provider_name_is_delegated(self) -> None:
        assert recorder_with()[0].provider_name == "recording"

    def test_the_inner_service_remains_reachable(self) -> None:
        recorder, _ = recorder_with()
        assert recorder.inner.available is True

    def test_max_results_is_forwarded(self) -> None:
        recorder, _ = recorder_with(sebi_result(position=1), sebi_result(position=2))
        response = recorder.search("acme", 1)
        assert len(response.results) == 1


class TestRecording:
    """Recording happens as a side effect, not as a separate step."""

    def test_a_search_is_recorded(self) -> None:
        recorder, _ = recorder_with(sebi_result())
        recorder.search("acme", 5)
        assert [r.query for r in recorder.responses] == ["acme"]

    def test_responses_are_recorded_in_call_order(self) -> None:
        recorder, _ = recorder_with(sebi_result())
        recorder.search("first", 5)
        recorder.search("second", 5)
        assert [r.query for r in recorder.responses] == ["first", "second"]

    def test_nothing_is_recorded_before_a_search(self) -> None:
        assert recorder_with(sebi_result())[0].responses == ()

    def test_clearing_discards_recorded_responses(self) -> None:
        recorder, _ = recorder_with(sebi_result())
        recorder.search("acme", 5)
        recorder.clear()
        assert recorder.responses == ()

    def test_a_repeated_query_is_recorded_each_time_it_runs(self) -> None:
        """Two calls are two searches, however identical their text."""
        recorder, _ = recorder_with(sebi_result())
        recorder.search("acme", 5)
        recorder.search("acme", 5)
        assert len(recorder.responses) == 2


class TestResultsByClaim:
    """Responses map back to claims through the queries Phase 4 recorded."""

    def test_a_claim_receives_the_results_its_own_queries_returned(self) -> None:
        recorder, _ = recorder_with(sebi_result())
        recorder.search("acme sebi registration", 5)
        from tests.graph.graph_factories import result_for

        claim = build_claim()
        results = recorder.results_by_claim(
            (result_for(claim, queries=("acme sebi registration",)),)
        )
        assert len(results[claim.id]) == 1

    def test_a_claim_with_no_recorded_query_gets_nothing(self) -> None:
        """The mapping cannot invent a result Phase 4 did not retrieve."""
        recorder, _ = recorder_with(sebi_result())
        recorder.search("acme", 5)
        from tests.graph.graph_factories import result_for

        claim = build_claim()
        results = recorder.results_by_claim((result_for(claim, queries=()),))
        assert results[claim.id] == ()

    def test_an_unissued_query_is_ignored(self) -> None:
        """A query Phase 4 never ran cannot pull in unrelated results."""
        recorder, _ = recorder_with(sebi_result())
        recorder.search("issued query", 5)
        from tests.graph.graph_factories import result_for

        claim = build_claim()
        results = recorder.results_by_claim((result_for(claim, queries=("never ran",)),))
        assert results[claim.id] == ()

    def test_documents_are_deduplicated_within_a_claim(self) -> None:
        """One document found by two queries of the same claim appears once."""
        recorder, _ = recorder_with(sebi_result())
        recorder.search("query one", 5)
        recorder.search("query two", 5)
        from tests.graph.graph_factories import result_for

        claim = build_claim()
        results = recorder.results_by_claim(
            (result_for(claim, queries=("query one", "query two")),)
        )
        assert len(results[claim.id]) == 1

    def test_two_claims_sharing_a_query_both_receive_its_results(self) -> None:
        """Both were genuinely assessed against those documents."""
        recorder, _ = recorder_with(sebi_result())
        recorder.search("shared query", 5)
        from tests.graph.graph_factories import result_for

        first = build_claim(claim_id="claim_001")
        second = build_claim(claim_id="claim_002")
        results = recorder.results_by_claim(
            (
                result_for(first, queries=("shared query",)),
                result_for(second, queries=("shared query",)),
            )
        )
        assert len(results["claim_001"]) == 1
        assert len(results["claim_002"]) == 1

    def test_no_verification_results_means_no_mapping(self) -> None:
        recorder, _ = recorder_with(sebi_result())
        recorder.search("acme", 5)
        assert recorder.results_by_claim(()) == {}

    def test_a_query_returning_nothing_maps_to_empty(self) -> None:
        """A completed search with zero results is a gap, not an error."""
        recorder, _ = recorder_with()
        recorder.search("nothing found", 5)
        from tests.graph.graph_factories import result_for

        claim = build_claim()
        results = recorder.results_by_claim(
            (result_for(claim, queries=("nothing found",)),)
        )
        assert results[claim.id] == ()


class TestWithTheRealVerificationService:
    """The recorder works against the genuine Phase 3 and Phase 4 services."""

    def test_phase_four_searches_are_captured_without_extra_searches(self) -> None:
        """Exactly the queries Phase 4 issued are recorded.

        This is the property that lets the graph hand Phase 5 real documents
        without issuing a single search of its own. If the recorder caused extra
        searches, or missed any, the evidence stage would be working from a
        different result set than the verification that judged it.
        """
        from tests.graph.graph_factories import MESSY_CONTENT

        dependencies, provider = real_dependencies()
        recorder = dependencies.search_recorder
        assert recorder is not None

        verification = dependencies.verification_service.verify_claims(
            dependencies.extraction_service.extract(MESSY_CONTENT).claims,
            (),
        )

        assert recorder.responses, "Phase 4 searched but nothing was recorded"
        assert [r.query for r in recorder.responses] == provider.queries

    def test_the_grouped_results_are_traceable_to_real_claims(self) -> None:
        """Each real claim's grouping comes only from its own recorded queries."""
        from tests.graph.graph_factories import MESSY_CONTENT

        dependencies, _ = real_dependencies()
        extraction = dependencies.extraction_service.extract(MESSY_CONTENT)
        verification = dependencies.verification_service.verify_claims(
            extraction.claims, extraction.entities
        )
        recorder = dependencies.search_recorder
        assert recorder is not None

        grouped = recorder.results_by_claim(verification.results)

        assert set(grouped) == {claim.id for claim in extraction.claims}
        issued = {
            query
            for result in verification.results
            for query in result.queries
        }
        for result in verification.results:
            for item in grouped[result.claim_id]:
                assert any(
                    any(candidate.url == item.url for candidate in response.results)
                    for response in recorder.responses
                    if response.query in issued
                )