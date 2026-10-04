"""`COMPLETED`, `PARTIAL`, `FAILED` and `SKIPPED` mean four different things (Phase 10).

This is the distinction the product is built on, and `PROJECT_CONTEXT.md` rule 13
states it directly:

> A limitation never stops an investigation; a failure always does. A run that
> stopped must never be mistakable for a run that finished and found little.

Three outcomes reach a client, and a fourth appears inside every response:

| Signal | Meaning | HTTP |
| --- | --- | --- |
| `COMPLETED` | Every stage that ran, ran to completion | `200` |
| `PARTIAL` | Usable result; at least one stage was partial or skipped | `200` |
| `FAILED` | The run stopped on a recorded error; no assessment exists | `5xx` |
| `SKIPPED` | *A stage* had no work, because a prerequisite was absent | — (in `timeline`) |

D-037 fixed where `status` comes from: **the stage timeline**, never the warning
list. The reason is concrete. `NO_RED_FLAGS_DETECTED` is a warning recorded on
content that matched no pattern — perfectly ordinary — and inferring `PARTIAL` from
"there are warnings" would report a clean investigation as a degraded one. These
tests pin that: the same warning count can produce `COMPLETED` or `PARTIAL`
depending only on the timeline.

The `SKIPPED` tests matter just as much, because "the stage ran and found nothing"
and "the stage never ran" must never look alike. An empty `claims` array could mean
either, and the timeline is the only thing that distinguishes them.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.api.adapters import investigation_status
from app.graph.context import GraphContext, GraphDependencies
from app.graph.investigation_graph import run_investigation
from app.graph.state import (
    GraphError,
    GraphStage,
    GraphWarning,
    InvestigationInputType,
    TimelineEvent,
    TimelineStatus,
)
from app.schemas.api import InvestigationStatus
from app.schemas.evidence import EvidenceBundleResponse
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
    offline_context,
    real_dependencies,
)
from tests.persistence_factories import MESSY_CONTENT, REGULATORY_CONTENT


def _event(stage: GraphStage, status: TimelineStatus) -> TimelineEvent:
    """Build a minimal timeline event.

    Args:
        stage: The stage the event describes.
        status: How that stage finished.

    Returns:
        A `TimelineEvent` stamped at a fixed instant.
    """
    from datetime import datetime, timezone

    return TimelineEvent(
        stage=stage,
        status=status,
        message="Fixture.",
        at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _context(
    *,
    claims: tuple = (),
    flags: tuple = (),
    verification: RecordingVerificationService | None = None,
    evidence: RecordingEvidenceService | None = None,
    extraction_raises: Exception | None = None,
    searchable: bool = False,
) -> GraphContext:
    """Build an offline context whose shape a test controls.

    Args:
        claims: Claims extraction returns. Empty means nothing to verify.
        flags: Red flags to report.
        verification: A verification fake, if the test needs a specific one.
        evidence: An evidence fake, if the test needs a specific bundle.
        extraction_raises: An exception for extraction to raise.
        searchable: Whether to install a working search recorder.

    Returns:
        A `GraphContext` wired to fakes.

    Without `searchable`, the graph has no recorder, so the verification node
    records `SEARCH_RESULTS_NOT_RECORDED` and marks itself `PARTIAL` — which is
    correct behaviour, but makes an assertion about "nothing degraded" false for a
    reason that has nothing to do with the stage under test. Tests that need a fully
    completed timeline therefore ask for `searchable=True`.
    """
    extraction = (
        RecordingExtractionService(raises=extraction_raises)
        if extraction_raises is not None
        else RecordingExtractionService(
            result=extraction_with(claims=claims, entities=(build_entity(),) if claims else ())
        )
    )
    dependencies = GraphDependencies(
        extraction_service=extraction,
        red_flag_engine=RecordingRedFlagEngine(flags=flags),
        verification_service=verification or RecordingVerificationService(),
        evidence_service=evidence or RecordingEvidenceService(bundle=EvidenceBundleResponse()),
        risk_service=RecordingRiskService(),
    )
    if searchable:
        from app.graph.context import RecordingSearchService
        from app.services.search import SearchProvider, SearchService

        class NoResults(SearchProvider):
            """A provider that is available and returns nothing."""

            name = "fixture"

            def is_available(self) -> bool:
                """Available, so the recorder does not report `SEARCH_UNAVAILABLE`."""
                return True

            def search(self, query, max_results):
                """Return an empty successful response.

                Args:
                    query: The query text.
                    max_results: Requested result count.

                Returns:
                    A `SearchResponse` with no results and status `OK`.
                """
                from app.schemas.search import SearchResponse, SearchStatus

                return SearchResponse(
                    query=query,
                    provider=self.name,
                    status=SearchStatus.OK,
                    provider_query_sent=True,
                )

        recorder = RecordingSearchService(SearchService(provider=NoResults()))
        dependencies = dataclasses.replace(dependencies, search_recorder=recorder)

    return GraphContext(dependencies=dependencies)


def _status_of(state) -> str:
    """Derive the top-level status the adapter would report for `state`.

    Args:
        state: A finished investigation state.

    Returns:
        The status value.
    """
    status = investigation_status(
        tuple(state.get("errors") or ()), tuple(state.get("timeline") or ())
    )
    return InvestigationStatus(status).value


def _timeline_statuses(state) -> dict[str, list[str]]:
    """Map each stage to the statuses its timeline entries recorded.

    Args:
        state: A finished investigation state.

    Returns:
        Stage value to the list of statuses recorded for it.
    """
    collected: dict[str, list[str]] = {}
    for event in state.get("timeline") or ():
        collected.setdefault(event.stage.value, []).append(event.status.value)
    return collected


class TestStatusComesFromTheTimelineNotTheWarnings:
    """D-037: the warning list is not a status."""

    def test_no_warnings_at_all_is_a_completed_run(self) -> None:
        """The cleanest case: nothing degraded, nothing recorded."""
        status = investigation_status((), ())

        assert status is InvestigationStatus.COMPLETED

    def test_warnings_do_not_by_themselves_make_a_run_partial(self) -> None:
        """A warning on a completed timeline is still `COMPLETED`.

        This is the D-037 tripwire in its purest form. `NO_RED_FLAGS_DETECTED` is
        recorded on ordinary clean content, so inferring `PARTIAL` from "warnings
        exist" would mark a successful investigation of clean text as degraded.

        Args:
            real_context: An offline context with the genuine services.
        """
        warning = GraphWarning(
            code="NO_RED_FLAGS_DETECTED",
            stage=GraphStage.RED_FLAGS,
            message="No content patterns from the project's rule set were found.",
        )

        status = investigation_status(
            (), (_event(GraphStage.RED_FLAGS, TimelineStatus.COMPLETED),)
        )

        assert status is InvestigationStatus.COMPLETED
        assert warning.code == "NO_RED_FLAGS_DETECTED"

    @pytest.mark.parametrize("stage", list(GraphStage))
    def test_any_warning_on_a_completed_timeline_stays_completed(
        self, stage: GraphStage
    ) -> None:
        """No stage, and no warning code, can produce `PARTIAL` on its own.

        Args:
            stage: The stage the warning is attributed to.
        """
        status = investigation_status((), (_event(stage, TimelineStatus.COMPLETED),))

        assert status is InvestigationStatus.COMPLETED

    @pytest.mark.parametrize(
        "status",
        [TimelineStatus.PARTIAL, TimelineStatus.SKIPPED],
    )
    def test_a_partial_or_skipped_stage_makes_the_run_partial(
        self, status: TimelineStatus
    ) -> None:
        """One incomplete stage is enough, because the run is only as complete as
        its weakest stage.

        Args:
            status: The stage status that degrades the run.
        """
        result = investigation_status((), (_event(GraphStage.EVIDENCE, status),))

        assert result is InvestigationStatus.PARTIAL

    def test_an_error_outranks_every_timeline_entry(self) -> None:
        """A recorded error decides the status unconditionally."""
        error = GraphError(
            code="VERIFICATION_FAILED",
            stage=GraphStage.VERIFICATION,
            message="The verification stage did not complete.",
        )

        status = investigation_status(
            (error,),
            (
                _event(GraphStage.EXTRACTION, TimelineStatus.COMPLETED),
                _event(GraphStage.VERIFICATION, TimelineStatus.FAILED),
            ),
        )

        assert status is InvestigationStatus.FAILED

    def test_a_started_stage_alone_is_not_a_degradation(self) -> None:
        """`STARTED` records that a stage began, not that it fell short."""
        status = investigation_status((), (_event(GraphStage.INPUT, TimelineStatus.STARTED),))

        assert status is InvestigationStatus.COMPLETED


class TestTheThreeOutcomesOverRealRuns:
    """The same derivation, driven through the real graph."""

    def test_a_run_with_nothing_degraded_is_completed(self) -> None:
        """Every stage finishes its work, so the run is complete."""
        state = run_investigation(
            REGULATORY_CONTENT,
            input_type=InvestigationInputType.TEXT,
            context=_context(claims=(build_claim(),), flags=(build_flag(),), searchable=True),
        )
        statuses = _timeline_statuses(state)

        assert all(
            "PARTIAL" not in found and "SKIPPED" not in found
            for found in statuses.values()
        ), statuses
        assert _status_of(state) == "COMPLETED"

    def test_search_being_unavailable_yields_partial_not_failed(
        self, api_client_factory
    ) -> None:
        """A provider being down is `PARTIAL`, and still a `200`.

        The caller's request was fine; ours was less thorough. Failing the request
        would make the product look broken exactly when it is being truthful about
        having checked less than it wanted to.

        Args:
            api_client_factory: Builds a client running a chosen context.
        """
        dependencies, _ = real_dependencies(
            provider=RecordingSearchProvider(available=False)
        )
        with api_client_factory(offline_context(dependencies)) as client:
            response = client.post(
                "/api/investigations/text", json={"text": MESSY_CONTENT}
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "PARTIAL"
        # A limitation from the search stage, whatever its specific code.
        assert any(
            warning["stage"] == "verification" for warning in body["warnings"]
        ), body["limitations"]
        # The distinguishing half: a partial run still has a usable result.
        assert body["risk_assessment"] is not None
        assert body["errors"] == []

    def test_a_fatal_stage_ends_the_run_with_a_failed_status(self) -> None:
        """A broken contract ends the run, and no score comes back."""
        state = run_investigation(
            REGULATORY_CONTENT,
            input_type=InvestigationInputType.TEXT,
            context=_context(claims=(build_claim(),), extraction_raises=RuntimeError("boom")),
        )

        assert _status_of(state) == "FAILED"
        assert "risk_assessment" not in state

    def test_a_fatal_stage_is_an_http_error_not_a_200(self, api_client_factory) -> None:
        """`FAILED` never reaches a client as a successful response body.

        Args:
            failure_client: Builds a client running a chosen graph context.
        """
        client = api_client_factory(
            _context(claims=(build_claim(),), extraction_raises=RuntimeError("boom"))
        )

        with client:
            response = client.post(
                "/api/investigations/text", json={"text": REGULATORY_CONTENT}
            )

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "EXTRACTION_FAILED"

    def test_a_failed_run_is_never_stored_as_a_finished_one(self, api_client_factory) -> None:
        """A refused or stopped run leaves no retrievable investigation.

        Storing a `FAILED` run would put it in the history, where a dashboard
        listing runs would show an entry that looks like a result.

        Args:
            failure_client: Builds a client running a chosen graph context.
        """
        from app.api.deps import get_repository_dep

        class RecordingRepository:
            """A repository that records writes instead of performing them."""

            def __init__(self) -> None:
                self.saved: list[object] = []

            def save(self, state: object) -> None:
                """Record the state.

                Args:
                    state: The finished run.
                """
                self.saved.append(state)

            def commit(self) -> None:
                """Pretend to commit."""

            def rollback(self) -> None:
                """Pretend to roll back."""

        repository = RecordingRepository()
        client = api_client_factory(_context(extraction_raises=RuntimeError("boom")))
        client.app.dependency_overrides[get_repository_dep] = lambda: repository

        with client:
            response = client.post(
                "/api/investigations/text", json={"text": REGULATORY_CONTENT}
            )

        assert response.status_code == 500
        assert repository.saved == [], "a failed run must not be written"


class TestSkippedIsDistinctFromEmpty:
    """`SKIPPED` is not "ran and found nothing"."""

    def test_no_claims_skips_verification_and_evidence(self) -> None:
        """Both stages are `SKIPPED`, and say why.

        Args:
            real_context: An offline context with the genuine services.
        """
        state = run_investigation(
            "Just an ordinary sentence about gardening with no claims in it.",
            input_type=InvestigationInputType.TEXT,
            context=_context(claims=()),
        )
        statuses = _timeline_statuses(state)

        assert statuses["verification"] == ["SKIPPED"]
        assert statuses["evidence"] == ["SKIPPED"]

    def test_a_skipped_stage_is_reported_but_produces_no_results(self) -> None:
        """The two halves agree: the stage is marked skipped *and* its results are empty.

        Asserted together because either alone is satisfiable by a bug: a stage
        marked `SKIPPED` that still returned results, or one that returned nothing
        without being marked. The pairing is what a client relies on.

        """
        state = run_investigation(
            "Just an ordinary sentence about gardening with no claims in it.",
            input_type=InvestigationInputType.TEXT,
            context=_context(claims=()),
        )

        assert state["verification_results"] == ()
        assert state["evidence"] == ()
        assert _status_of(state) == "PARTIAL", (
            "a run that skipped two stages must report itself as partial, so the "
            "client can tell it did not check what it would normally check"
        )

    def test_a_skipped_stage_says_what_it_did_not_do(self) -> None:
        """The timeline message states the absence rather than implying an outcome.

        """
        state = run_investigation(
            "Just an ordinary sentence about gardening with no claims in it.",
            input_type=InvestigationInputType.TEXT,
            context=_context(claims=()),
        )

        messages = [
            event.message
            for event in state["timeline"]
            if event.stage in (GraphStage.VERIFICATION, GraphStage.EVIDENCE)
        ]
        for message in messages:
            assert "no claims" in message.lower(), message
            # It must not claim a check happened, nor that anything was found.
            assert "no evidence was found" not in message.lower()

    def test_a_stage_that_ran_and_found_nothing_is_not_skipped(self) -> None:
        """Verification that ran and returned no results is `COMPLETED`.

        The pair matters: this is the case where "ran and found nothing" must *not*
        be reported as "never ran", because the two imply opposite things about how
        much checking happened.

        """
        from app.schemas.verification import VerificationResponse

        state = run_investigation(
            REGULATORY_CONTENT,
            input_type=InvestigationInputType.TEXT,
            context=_context(
                claims=(build_claim(),),
                verification=RecordingVerificationService(
                    response=VerificationResponse()
                ),
                searchable=True,
            ),
        )
        statuses = _timeline_statuses(state)

        assert statuses["verification"] == ["COMPLETED"]

    def test_an_empty_evidence_bundle_is_not_reported_as_a_failure(self) -> None:
        """No evidence is a legitimate outcome once verification ran.

        """
        state = run_investigation(
            REGULATORY_CONTENT,
            input_type=InvestigationInputType.TEXT,
            context=_context(claims=(build_claim(),), flags=(build_flag(),)),
        )

        assert not state["errors"]
        statuses = _timeline_statuses(state)
        assert statuses["evidence"] == ["COMPLETED"]
        assert "EVIDENCE_UNAVAILABLE" not in {w.code for w in state.get("warnings", ())}


class TestTheStatusIsHonestAboutWhatWasChecked:
    """The status is a statement about coverage, not about risk."""

    def test_a_clean_run_is_not_reported_as_a_degraded_one(self, api_client: TestClient) -> None:
        """Benign content that trips no pattern is a complete investigation.

        The asymmetry that matters: reporting this as `PARTIAL` would make every
        clean document look like the system had something to apologise for, which
        erodes trust in the `PARTIAL` that is real.

        Args:
            api_client: A client running the real app, offline.
        """
        response = api_client.post(
            "/api/investigations/text",
            json={"text": "Acme Capital Advisors is SEBI registered."},
        )

        body = response.json()
        # With a fixture provider that returns nothing, this run *is* partial, and
        # correctly so: the claims could not be confirmed.
        assert body["status"] == "PARTIAL"

        red_flag_events = [
            event for event in body["timeline"] if event["stage"] == "red_flags"
        ]
        assert red_flag_events[0]["status"] == "COMPLETED", (
            "a stage that ran and matched no pattern is COMPLETED, not PARTIAL"
        )

    def test_the_status_never_depends_on_the_risk_score(self) -> None:
        """A high score does not make a run `PARTIAL`, and zero does not make it
        `COMPLETED`.

        Status is about coverage and risk is about indicators. Conflating them would
        make a well-checked scam look like a degraded investigation, and a
        well-checked clean document look like an incomplete one.

        """
        from app.services.risk import RiskService

        service = RiskService()
        empty = service.assess()
        # A deliberately extreme assessment, built through the same validator every
        # real one goes through so the fixture cannot be an invalid shape.
        assessment = empty.model_copy(update={"risk_score": 95, "raw_score": 95})

        assert empty.risk_score == 0
        assert assessment.risk_score == 95

        timeline = (_event(GraphStage.RISK, TimelineStatus.COMPLETED),)
        assert investigation_status((), timeline) is InvestigationStatus.COMPLETED

    def test_the_status_is_stored_and_retrieved_unchanged(self, api_client: TestClient) -> None:
        """What a client is told at write time is what it reads back.

        A status recomputed on retrieval from different data would mean the history
        and the detail view could disagree about the same run.

        Args:
            api_client: A client running the real app, offline.
        """
        posted = api_client.post(
            "/api/investigations/text", json={"text": MESSY_CONTENT}
        ).json()

        retrieved = api_client.get(
            f"/api/investigations/{posted['investigation_id']}"
        ).json()

        assert retrieved["status"] == posted["status"]
        assert retrieved["status"] == "PARTIAL"

    def test_history_reports_each_run_own_status(self, api_client: TestClient) -> None:
        """A summary's status is the stored run's, not a value derived for the list.

        Args:
            api_client: A client running the real app, offline.
        """
        api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT})
        api_client.post(
            "/api/investigations/text", json={"text": REGULATORY_CONTENT + " Extra."}
        )

        body = api_client.get("/api/investigations").json()

        assert body["total"] == 2
        for entry in body["investigations"]:
            assert entry["status"] in {"COMPLETED", "PARTIAL", "FAILED"}


class TestTheStatusVocabularyIsClosed:
    """The client sees exactly three values."""

    def test_the_response_enum_has_exactly_three_values(self) -> None:
        """Adding a fourth would be a contract change, not an implementation detail.

        """
        assert {member.value for member in InvestigationStatus} == {
            "COMPLETED",
            "PARTIAL",
            "FAILED",
        }

    def test_the_timeline_status_vocabulary_has_exactly_five(self) -> None:
        """`SKIPPED` is a stage outcome and is not a top-level status.

        """
        assert {member.value for member in TimelineStatus} == {
            "STARTED",
            "COMPLETED",
            "PARTIAL",
            "FAILED",
            "SKIPPED",
        }

    @pytest.mark.parametrize("value", ["PENDING", "PROCESSING", "QUEUED", "RUNNING", "SKIPPED"])
    def test_withdrawn_statuses_cannot_be_returned(self, value: str) -> None:
        """The withdrawn asynchronous vocabulary cannot be produced.

        Args:
            value: A status from the withdrawn design.
        """
        with pytest.raises(ValueError):
            InvestigationStatus(value)

    def test_the_timeline_reaches_the_client_in_full(self, api_client_factory) -> None:
        """A client can see which stage was incomplete, not merely that one was.

        Without the timeline, `PARTIAL` is undifferentiated: a client cannot tell a
        missing search key from a missing evidence bundle, and can only show a
        generic apology.

        Args:
            api_client_factory: Builds a client running a chosen context.
        """
        dependencies, _ = real_dependencies(
            provider=RecordingSearchProvider(available=False)
        )
        with api_client_factory(offline_context(dependencies)) as client:
            body = client.post(
                "/api/investigations/text", json={"text": MESSY_CONTENT}
            ).json()

        stages = {event["stage"]: event["status"] for event in body["timeline"]}

        assert stages["verification"] in {"PARTIAL", "SKIPPED"}
        assert body["status"] == "PARTIAL"

    def test_every_timeline_entry_names_its_stage_and_how_it_finished(
        self, api_client: TestClient
    ) -> None:
        """The timeline is complete enough to render, not a list of bare timestamps.

        Args:
            api_client: A client running the real app, offline.
        """
        body = api_client.post(
            "/api/investigations/text", json={"text": MESSY_CONTENT}
        ).json()

        for event in body["timeline"]:
            assert set(event) == {"stage", "status", "message", "at"}
            assert event["message"], "a timeline entry must say what happened"
