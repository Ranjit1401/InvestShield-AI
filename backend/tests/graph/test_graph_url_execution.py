"""Running a URL submission through the whole graph (Phase 12 §13).

The claim these tests exist to defend is a narrow one, and it is the reason
Phase 12 needed so little new code: **a URL submission is analysed by the
existing pipeline.** There is no URL-specific extractor, no URL-specific red
flag engine and no URL-specific scoring path. The submitted URL is retrieved, its
text becomes the pipeline's input, and Phase 1-6 run over it unchanged.

So the tests below assert reuse rather than new behaviour — that the same rules
fire, that the same services run, that a text submission is untouched by any of
it, and that provenance survives a round trip.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.graph.context import GraphContext, GraphDependencies
from app.graph.investigation_graph import run_investigation
from app.graph.state import InvestigationInputType, investigation_id_for, semantic_view
from app.schemas.url import UrlSource
from app.services.url_fetch import UrlFetchError
from tests.graph.graph_factories import fixed_clock, offline_settings, real_dependencies
from tests.persistence_factories import REGULATORY_CONTENT
from tests.url_factories import (
    JS_ONLY_PAGE,
    SCAM_PAGE,
    FakeFetchService,
    build_page,
    url_context,
    url_dependencies,
)


def context_for(fetcher: object) -> GraphContext:
    """An offline context with the genuine Phase 1-6 services and a fake fetcher.

    Args:
        fetcher: The stand-in for `URLFetchService`.

    Returns:
        A graph context ready to run a URL investigation.
    """
    dependencies, _ = real_dependencies(offline_settings())
    return url_context(dependencies, fetcher)


def run_url(context: GraphContext, url: str = "https://example.com/invest"):
    """Run one URL investigation.

    Args:
        context: The context to run against.
        url: The submitted URL.

    Returns:
        The final state.
    """
    return run_investigation(url, input_type=InvestigationInputType.URL, context=context)


class TestThePipelineIsReused:
    """A URL investigation is a text investigation of a retrieved page."""

    def test_the_existing_rules_fire_on_page_content(self) -> None:
        """Phase 1's rules find what they would find in submitted prose.

        A separate URL rule set would mean two places to keep in step, and a rule
        fixed for one input kind but not the other. This asserts the rule set is
        shared.
        """
        state = run_url(context_for(FakeFetchService(build_page())))

        codes = {flag.code for flag in state["red_flags"]}
        for expected in (
            "GUARANTEED_RETURN",
            "UNREALISTIC_RETURN",
            "FAKE_REGULATORY_CLAIM",
            "SUSPICIOUS_URL",
            "WHATSAPP_INVESTMENT_GROUP",
        ):
            assert expected in codes, f"{expected} did not fire on page content"

    def test_the_whole_pipeline_runs(self) -> None:
        """Every stage that runs for text runs for a URL too."""
        state = run_url(context_for(FakeFetchService(build_page())))

        stages = [event.stage.value for event in state["timeline"]]
        assert stages == [
            "input",
            "extraction",
            "red_flags",
            "verification",
            "evidence",
            "risk",
        ]
        assert state["risk_assessment"] is not None
        assert state["claims"]
        assert state["verification_results"]

    def test_the_investigation_id_comes_from_the_submitted_url(self) -> None:
        """The id is derived from what the caller submitted, not what came back.

        Deriving it from the retrieved text would give the same page a different id
        every time its content changed, so a re-run could not be compared with the
        run before it.
        """
        state = run_url(context_for(FakeFetchService(build_page())))

        assert state["investigation_id"] == investigation_id_for(
            InvestigationInputType.URL, "https://example.com/invest"
        )

    def test_the_same_url_yields_the_same_id_across_runs(self) -> None:
        """Two runs of one address are comparable."""
        first = run_url(context_for(FakeFetchService(build_page())))
        second = run_url(context_for(FakeFetchService(build_page())))

        assert first["investigation_id"] == second["investigation_id"]
        assert semantic_view(first) == semantic_view(second)

    def test_red_flag_spans_point_at_the_analysed_text(self) -> None:
        """A span's text is what that span actually covers.

        A span is an index into `raw_input`. The analysis text is what the user
        effectively asked about, so the span has to address it rather than the bare
        URL — otherwise every finding would point at a few characters of an address
        and the reader could not see why it fired.
        """
        state = run_url(context_for(FakeFetchService(build_page())))
        analysed = state["raw_input"]

        for flag in state["red_flags"]:
            span = flag.evidence_span
            assert analysed[span.start : span.end] == span.text

    def test_the_text_path_is_untouched(self) -> None:
        """A `TEXT` submission behaves exactly as it did before Phase 12."""
        dependencies, _ = real_dependencies(offline_settings())
        context = GraphContext(dependencies=dependencies, clock=fixed_clock)

        state = run_investigation(
            REGULATORY_CONTENT, input_type=InvestigationInputType.TEXT, context=context
        )

        assert state["input_type"] == "TEXT"
        assert "url_source" not in state
        assert state["raw_input"] == REGULATORY_CONTENT

    def test_a_url_context_does_not_change_text_results(self) -> None:
        """Wiring URL services in has no effect on the text path.

        The URL pair is two more optional dependencies. If installing them changed
        a text investigation, the dependency container would be doing something
        other than holding services.
        """
        dependencies, _ = real_dependencies(offline_settings())
        with_urls = GraphContext(
            dependencies=url_dependencies(
                dependencies, FakeFetchService(build_page())
            ),
            clock=fixed_clock,
        )
        without = GraphContext(dependencies=dependencies, clock=fixed_clock)

        first = run_investigation(
            REGULATORY_CONTENT, input_type=InvestigationInputType.TEXT, context=with_urls
        )
        second = run_investigation(
            REGULATORY_CONTENT, input_type=InvestigationInputType.TEXT, context=without
        )

        assert semantic_view(first) == semantic_view(second)


class TestProvenanceIsCarried:
    """What was read, and from where, travels with the result."""

    def test_the_source_is_stored_on_the_state(self) -> None:
        """A URL run records the retrieval it was based on."""
        state = run_url(context_for(FakeFetchService(build_page())))

        source = state["url_source"]
        assert isinstance(source, UrlSource)
        assert source.submitted_url == "https://example.com/invest"
        assert source.hostname == "example.com"
        assert source.http_status == 200
        assert source.page_title is not None

    def test_a_redirect_is_recorded(self) -> None:
        """The URL actually read is recorded alongside the one submitted.

        Without this a reader cannot tell that the page they were shown came from
        somewhere other than the address they submitted.
        """
        page = build_page(
            url="https://example.com/start",
            final_url="https://www.example.com/final",
            redirect_count=1,
        )
        state = run_url(context_for(FakeFetchService(page)), "https://example.com/start")

        source = state["url_source"]
        assert source.redirected is True
        assert source.redirect_count == 1
        assert source.final_url == "https://www.example.com/final"

    def test_no_source_is_recorded_for_a_text_run(self) -> None:
        """A `TEXT` run has no `url_source` key at all, not a null one.

        The key's absence is what distinguishes "there was no URL" from "the URL
        produced no provenance", and it is what keeps a reloaded run identical to
        the run that was stored.
        """
        dependencies, _ = real_dependencies(offline_settings())
        state = run_investigation(
            REGULATORY_CONTENT,
            input_type=InvestigationInputType.TEXT,
            context=GraphContext(dependencies=dependencies, clock=fixed_clock),
        )

        assert "url_source" not in state


class TestDegradedRuns:
    """A partial URL run is a result, not a failure."""

    def test_a_truncated_page_succeeds_with_a_limitation(self) -> None:
        """A page too large to read whole is analysed up to the limit."""
        state = run_url(context_for(FakeFetchService(build_page(truncated=True))))

        assert not state["errors"]
        assert state["risk_assessment"] is not None
        codes = {warning.code for warning in state["warnings"]}
        assert "URL_CONTENT_TRUNCATED" in codes

    def test_a_javascript_page_succeeds_with_a_limitation(self) -> None:
        """A page with no readable text is analysed for its URL and domain."""
        state = run_url(
            context_for(FakeFetchService(build_page(body=JS_ONLY_PAGE, url="https://spa.example/"))),
            "https://spa.example/",
        )

        assert not state["errors"]
        assert state["risk_assessment"] is not None
        codes = {warning.code for warning in state["warnings"]}
        assert "PAGE_TEXT_NOT_RETRIEVED" in codes
        assert "spa.example" in state["raw_input"]

    @pytest.mark.parametrize(
        "code",
        ["URL_TIMEOUT", "URL_HTTP_ERROR", "URL_DNS_FAILED", "URL_CONTENT_TYPE_UNSUPPORTED"],
    )
    def test_a_retrieval_failure_ends_the_run_with_a_typed_error(self, code: str) -> None:
        """Nothing is analysed when the page was never read.

        Args:
            code: The refusal the fetcher raises.
        """
        fields = {"status": 500} if code == "URL_HTTP_ERROR" else {}
        fetcher = FakeFetchService(error=UrlFetchError(code, **fields))

        state = run_url(context_for(fetcher))

        assert [error.code for error in state["errors"]] == [code]
        assert state.get("risk_assessment") is None
        assert not state.get("claims")
        assert state["current_stage"] == "input"

    def test_an_unwired_graph_refuses_the_capability(self) -> None:
        """A graph with no URL services says so instead of fetching nothing."""
        dependencies, _ = real_dependencies(offline_settings())
        state = run_url(GraphContext(dependencies=dependencies))

        assert [error.code for error in state["errors"]] == ["URL_FETCH_UNAVAILABLE"]

    def test_the_fetcher_sees_the_submitted_url_verbatim(self) -> None:
        """Nothing rewrites the address before it is fetched.

        Normalising before the fetch would mean the address recorded as submitted
        and the address actually requested could differ.
        """
        fetcher = FakeFetchService(build_page())

        run_url(context_for(fetcher), "  https://example.com/invest  ")

        assert fetcher.calls == ["  https://example.com/invest  "]


class TestTheComposedAnalysisText:
    """What the pipeline actually reads."""

    def test_it_starts_with_the_address_and_carries_the_body(self) -> None:
        """The submitted URL leads, then the page's own text."""
        state = run_url(context_for(FakeFetchService(build_page())))

        text = state["raw_input"]
        assert text.startswith("Website: https://example.com/invest")
        assert "SEBI registered investment advisor" in text

    def test_extracted_text_starts_as_the_analysis_text(self) -> None:
        """The input stage seeds the same string Phase 2 will normalise.

        The pattern stage anchors spans to `raw_input`, so seeding
        `extracted_text` with the composed text keeps the two consistent before
        Phase 2 has run at all.
        """
        state = run_url(context_for(FakeFetchService(build_page())))

        assert state["extracted_text"] == state["raw_input"]

    def test_a_benign_page_produces_fewer_findings_than_a_scam_page(self) -> None:
        """The rules distinguish pages, rather than flagging every URL.

        A detector that fires on every submission is a detector nobody reads. This
        is the check that URL support did not simply add a new way to raise scores.
        """
        scam = run_url(context_for(FakeFetchService(build_page())))
        benign = run_url(
            context_for(
                FakeFetchService(
                    build_page(
                        body=b"<html><head><title>About us</title></head><body>"
                        b"<p>We publish quarterly research notes for registered "
                        b"advisers.</p></body></html>"
                    )
                )
            ),
            "https://example.com/about",
        )

        assert len(scam["red_flags"]) > len(benign["red_flags"])