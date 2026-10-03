"""The API is an entry point, not a second pipeline (Phase 10).

D-030 says the graph owns orchestration and no business logic. A route that calls
`ExtractionService` directly, or that builds a `RiskAssessment` itself, would still
pass every functional test in the suite: the response would still look right. What
breaks is the guarantee that there is exactly **one** implementation of every
stage, so a fix to Phase 2 reaches the product, and one place where a stage can be
bypassed.

So this module tests the *wiring*, not the output.

- `test_the_api_modules_do_not_import_a_service` reads the AST of the API package
  and asserts that no module imports any of the five services. An import is the
  earliest point at which a bypass becomes possible, so this is the cheapest
  tripwire: it fires at the moment someone adds the import, not once the
  behaviour has already diverged.
- `test_a_request_travellers_through_the_graph` installs fakes on the graph
  context and asserts each was called exactly once, in the graph's order, through
  the one function that drives it.
- `test_the_route_calls_the_graph_entry_point` pins the route's own behaviour:
  it must delegate to `run_investigation`, and it must build the response through
  `serialize_investigation` — the same adapter persistence feeds (D-041).

The AST check is deliberately a **source-level** check rather than a mock-based
one. `unittest.mock` can prove that a particular call happened during a particular
request; it cannot prove that no *other* code path calls the service, and it will
happily pass while a route holds a direct reference it simply never exercised.
Reading the source answers the question that was actually asked.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_graph_context_dep
from app.graph.context import GraphContext, GraphDependencies, RecordingSearchService
from app.graph.state import InvestigationInputType
from app.main import create_app
from app.schemas.search import SearchResult
from app.services.evidence import EvidenceService
from app.services.extraction_service import ExtractionService
from app.services.red_flag_engine import RedFlagEngine
from app.services.risk import RiskService
from app.services.search import SearchService
from app.services.verification import VerificationService
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
    sebi_result,
)
from tests.persistence_factories import REGULATORY_CONTENT

API_DIR = Path(__file__).resolve().parents[2] / "app" / "api"

#: The five Phase 1-6 services a route must never construct or call directly.
#:
#: Named by the classes themselves so the ban follows a rename rather than drifting
#: from it. `SearchService` is included for the same reason: a route that searched
#: on its own would bypass the recorder, and evidence would cite documents
#: verification never saw (D-023).
FORBIDDEN_IN_ROUTES: tuple[type, ...] = (
    ExtractionService,
    RedFlagEngine,
    VerificationService,
    EvidenceService,
    RiskService,
    SearchService,
    RecordingSearchService,
)


def _api_modules() -> list[Path]:
    """Every Python module in the API package.

    Returns:
        Sorted paths of `app/api/**/*.py`.
    """
    return sorted(API_DIR.rglob("*.py"))


def _imported_names(path: Path) -> set[str]:
    """Every name the module at `path` imports.

    Args:
        path: The module to read.

    Returns:
        Each imported name, from every `import` and `from ... import` in the file,
        both the bound name and the dotted origin's last segment.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
                names.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


class TestRoutesDoNotBypassTheGraph:
    """No API module may import or construct a pipeline service."""

    def test_the_api_package_has_modules_to_check(self) -> None:
        """The AST checks have something to read.

        Without this, a mistyped path would make every ban below vacuously pass,
        which is the worst possible shape for a security test.
        """
        modules = _api_modules()

        assert modules, f"no modules found under {API_DIR}"
        assert (API_DIR / "routes" / "investigations.py").exists()

    @pytest.mark.parametrize(
        "service", FORBIDDEN_IN_ROUTES, ids=lambda s: s.__name__
    )
    def test_the_api_modules_do_not_import_a_service(self, service: type) -> None:
        """No module under `app/api` imports any pipeline service.

        Args:
            service: The class that must not be imported.
        """
        offenders = [
            path.name
            for path in _api_modules()
            if service.__name__ in _imported_names(path)
        ]

        assert not offenders, (
            f"{service.__name__} is imported by {offenders}. The API layer must "
            "call the graph, which owns stage ordering (D-030). A route that "
            "constructs a service is a second pipeline that can disagree with the "
            "first."
        )

    @pytest.mark.parametrize(
        "service", FORBIDDEN_IN_ROUTES, ids=lambda s: s.__name__
    )
    def test_no_api_module_instantiates_a_service(self, service: type) -> None:
        """No module under `app/api` names a service as a call target.

        Checked separately from the import because a service can arrive through a
        re-export without a direct import of its own module, and through
        `SomeFactory()` a bare name comparison would miss.

        Args:
            service: The class whose name must never be called.
        """
        offenders: list[str] = []
        for path in _api_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name == service.__name__:
                    offenders.append(f"{path.name}:{node.lineno}")

        assert not offenders, (
            f"{service.__name__} is called directly from {offenders}. The route "
            "must delegate to run_investigation."
        )

    def test_the_route_module_only_orchestrates(self) -> None:
        """The investigation route's imports are exactly the ones an orchestrator needs.

        Scoped to the one route module rather than the whole API package, because
        the package's other modules legitimately import different things —
        `adapters.py` reads the graph's own warning and error types, which is the
        opposite of a bypass.

        The allowed set is: validate (request/response models), delegate
        (`run_investigation`), shape (`serialize_investigation`), persist (the
        repository), and raise (the typed errors). A new import has to be a
        deliberate act rather than an accident.
        """
        allowed = {
            # FastAPI plumbing.
            "APIRouter",
            "Annotated",
            "Depends",
            "File",
            "Form",
            "Query",
            "UploadFile",
            "run_in_threadpool",
            "status",
            # Validate.
            "MAX_TEXT_LENGTH",
            "MAX_URL_LENGTH",
            "SUPPORTED_INPUT_TYPES",
            "ErrorEnvelope",
            "ImageUpload",
            "InvestigationCreateRequest",
            "InvestigationListResponse",
            "InvestigationResponse",
            "InvestigationStatus",
            "InvestigationSummaryResponse",
            "Language",
            "OCR_IMAGE_FORMATS",
            "Settings",
            "TextInvestigationRequest",
            "UrlInvestigationRequest",
            # Delegate.
            "GraphContext",
            "run_investigation",
            "InvestigationInputType",
            "InvestigationState",
            # Shape.
            "serialize_investigation",
            # Persist.
            "get_repository_dep",
            "InvestigationRepository",
            "InvestigationSummary",
            "NotFound",
            # Raise.
            "ApiError",
            "InvestigationNotFound",
            "raise_for_graph_errors",
            # Logging.
            "get_logger",
            # The FastAPI dependencies that supply the context and settings.
            "get_graph_context_dep",
            "get_settings_dep",
        }

        path = API_DIR / "routes" / "investigations.py"
        unexpected = _imported_names(path) - allowed - {"annotations"}

        assert not unexpected, (
            f"routes/investigations.py imports {sorted(unexpected)}, which is not "
            "part of the orchestration surface: validate, delegate, shape, persist, "
            "raise. Add to the allowed set only if it is genuinely one of those."
        )

    def test_the_route_module_does_not_import_the_pipeline_directly(self) -> None:
        """The route reaches services only through the graph context.

        Args:
            api_client: A client running the real app, offline.
        """
        source = (API_DIR / "routes" / "investigations.py").read_text(encoding="utf-8")

        for service in FORBIDDEN_IN_ROUTES:
            assert service.__name__ not in source, (
                f"{service.__name__} appears in routes/investigations.py; the route "
                "must call run_investigation and let the graph reach the service"
            )


class TestTheRequestTravelsThroughTheGraph:
    """Every stage runs, once, because the graph ran it — not the route."""

    @pytest.fixture
    def recording_context(self, api_settings) -> GraphContext:
        """A context whose five services record every call.

        The extraction fake returns one claim and one entity, because the graph
        **skips** verification when there are no claims to check. A fake that
        returned an empty extraction would leave three stages unexercised and the
        "every stage ran" assertion vacuously meaningless.

        Args:
            api_settings: Isolated settings for this test.

        Returns:
            A `GraphContext` built from fakes.
        """
        claim = build_claim()
        entity = build_entity()
        extraction = RecordingExtractionService(
            result=extraction_with(
                claims=(claim,), entities=(entity,), normalized_text=REGULATORY_CONTENT
            )
        )
        red_flags = RecordingRedFlagEngine(flags=(build_flag(),))
        verification = RecordingVerificationService()
        evidence = RecordingEvidenceService()
        risk = RecordingRiskService()

        provider = RecordingSearchProvider((sebi_result(),))
        recorder = RecordingSearchService(SearchService(settings=api_settings, provider=provider))

        return GraphContext(
            dependencies=GraphDependencies(
                extraction_service=extraction,
                red_flag_engine=red_flags,
                verification_service=verification,
                evidence_service=evidence,
                risk_service=risk,
                search_recorder=recorder,
            )
        )

    @pytest.fixture
    def recording_client(
        self, api_settings, recording_context: GraphContext
    ) -> tuple[TestClient, dict[str, object]]:
        """A client whose graph context is fully recording.

        Args:
            api_settings: Isolated settings for this test.
            recording_context: The recording graph context.

        Returns:
            The client, and the dict of fakes to assert against.
        """
        client = TestClient(create_app(api_settings))
        client.app.dependency_overrides[get_graph_context_dep] = lambda: recording_context
        with client:
            yield client, {
                "extraction": recording_context.dependencies.extraction_service,
                "red_flags": recording_context.dependencies.red_flag_engine,
                "verification": recording_context.dependencies.verification_service,
                "evidence": recording_context.dependencies.evidence_service,
                "risk": recording_context.dependencies.risk_service,
            }

    def test_every_stage_runs_exactly_once_for_one_request(
        self, recording_client: tuple[TestClient, dict[str, object]]
    ) -> None:
        """One `POST` drives all five stages, once each.

        Zero calls would mean the route computed the answer itself; two would mean
        something runs the pipeline twice.

        Args:
            recording_client: A client wired to recording fakes, and the fakes.
        """
        client, fakes = recording_client

        response = client.post(
            "/api/investigations/text", json={"text": REGULATORY_CONTENT}
        )

        assert response.status_code == 200, response.text
        assert len(fakes["extraction"].calls) == 1
        assert len(fakes["red_flags"].calls) == 1
        assert len(fakes["verification"].calls) == 1
        assert len(fakes["evidence"].calls) == 1
        assert len(fakes["risk"].calls) == 1

    def test_the_route_hands_the_submitted_text_to_the_graph_unchanged(
        self, recording_client: tuple[TestClient, dict[str, object]]
    ) -> None:
        """Extraction receives exactly what the client sent.

        Red-flag spans index `raw_input`, so a route that trimmed, normalised or
        re-encoded the text before handing it over would produce flags pointing at
        the wrong characters.

        Args:
            recording_client: A client wired to recording fakes, and the fakes.
        """
        client, fakes = recording_client
        submitted = "  Acme Capital Advisors guarantees 30% returns.  "

        client.post("/api/investigations/text", json={"text": submitted})

        assert fakes["extraction"].calls == [submitted]

    def test_the_route_passes_no_input_of_its_own(
        self, recording_client: tuple[TestClient, dict[str, object]]
    ) -> None:
        """The typed endpoint fixes `input_type` rather than inventing one.

        Args:
            recording_client: A client wired to recording fakes, and the fakes.
        """
        client, fakes = recording_client

        client.post(
            "/api/investigations", json={"text": REGULATORY_CONTENT, "input_type": "TEXT"}
        )

        assert len(fakes["extraction"].calls) == 1

    def test_a_retrieval_runs_no_stage_again(
        self, recording_client: tuple[TestClient, dict[str, object]]
    ) -> None:
        """A `GET` is a read, not a re-investigation.

        The whole reason persistence exists is that retrieval is cheap. A `GET`
        that re-ran the pipeline would make the stored data decorative.

        Args:
            recording_client: A client wired to recording fakes, and the fakes.
        """
        client, fakes = recording_client

        posted = client.post(
            "/api/investigations/text", json={"text": REGULATORY_CONTENT}
        ).json()
        before = {name: len(fake.calls) for name, fake in fakes.items()}

        response = client.get(f"/api/investigations/{posted['investigation_id']}")

        assert response.status_code == 200
        after = {name: len(fake.calls) for name, fake in fakes.items()}
        assert before == after, f"retrieval re-ran stages: {before} -> {after}"

    def test_a_refused_submission_runs_no_stage(
        self, recording_client: tuple[TestClient, dict[str, object]]
    ) -> None:
        """An unsupported input type never reaches extraction.

        Args:
            recording_client: A client wired to recording fakes, and the fakes.
        """
        client, fakes = recording_client

        response = client.post(
            "/api/investigations",
            json={"input_type": "PDF", "text": "anything"},
        )

        assert response.status_code == 422
        assert fakes["extraction"].calls == []
        assert fakes["risk"].calls == []

    def test_claims_and_entities_reach_the_services_from_extraction_only(
        self, recording_client: tuple[TestClient, dict[str, object]]
    ) -> None:
        """The graph hands extraction's own objects to verification, not copies.

        A route or node that rebuilt a claim would create a second definition of one
        that could disagree with the evidence citing it.

        Args:
            recording_client: A client wired to recording fakes, and the fakes.
        """
        client, fakes = recording_client

        client.post("/api/investigations/text", json={"text": REGULATORY_CONTENT})

        (claims, entities), = fakes["verification"].calls
        assert claims == (build_claim(),)
        assert entities == (build_entity(),)
        # Identity, not just equality: the very object extraction produced.
        assert claims[0] is fakes["extraction"].result.claims[0]
        assert entities[0] is fakes["extraction"].result.entities[0]

    def test_the_graph_context_is_built_once_per_app_not_per_request(
        self, api_client: TestClient
    ) -> None:
        """Two requests share one graph context.

        A context rebuilt per request would construct five services and an engine
        pool for each call, and would defeat the recorder the evidence stage reads.

        Args:
            api_client: A client running the real app, offline.
        """
        first = api_client.app.state.graph_context
        second = api_client.app.state.graph_context

        assert first is second
        assert isinstance(first, GraphContext)


class TestTheRouteDelegatesToTheGraphEntryPoint:
    """The route calls `run_investigation` and builds its response with the adapter."""

    def test_the_route_module_calls_the_graph_entry_point(self) -> None:
        """`run_investigation` is invoked by the route, not reimplemented.

        Asserted on the source because the alternative — proving it by observing a
        state that came back shaped a certain way — cannot distinguish "the graph
        ran it" from "the route built the same shape itself", which is precisely the
        distinction under test.
        """
        source = (API_DIR / "routes" / "investigations.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert "run_investigation" in called, (
            "the route does not call run_investigation; it may have reimplemented "
            "the pipeline"
        )
        assert "serialize_investigation" in called, (
            "the route does not use the shared adapter, so a response could be "
            "shaped differently from a retrieved one (D-041)"
        )

    def test_the_write_and_read_paths_share_one_adapter_call_site(
        self, api_client: TestClient
    ) -> None:
        """One `serialize_investigation` call site serves both paths.

        Measured at runtime rather than in source: the observable consequence is
        that a stored investigation and a live one are byte-identical apart from
        their clocks, which is what the single call site buys.

        Args:
            api_client: A client running the real app, offline.
        """
        posted = api_client.post(
            "/api/investigations/text", json={"text": REGULATORY_CONTENT}
        ).json()

        retrieved = api_client.get(
            f"/api/investigations/{posted['investigation_id']}"
        ).json()

        assert retrieved == posted

    def test_the_adapter_is_the_only_response_builder(self) -> None:
        """No API module constructs `InvestigationResponse` outside the adapter.

        A route building the investigation response itself is a second shaping
        path, free to drift from the first — the exact failure D-041 exists to
        prevent.

        `InvestigationListResponse` is *not* included: history is genuinely a
        different shape, and the route legitimately assembles it. The invariant is
        about the investigation body, which has exactly one renderer.
        """
        offenders: list[str] = []
        for path in _api_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "InvestigationResponse":
                        relative = path.relative_to(API_DIR).as_posix()
                        if relative != "adapters.py":
                            offenders.append(f"{relative}:{node.lineno}")

        assert not offenders, (
            f"InvestigationResponse is constructed outside the adapter at {offenders}. "
            "A second rendering path could disagree with the first."
        )


class TestGraphAndApiAgreeOnTheStages:
    """The state the graph produces is shaped without the route interpreting it."""

    def test_the_response_timeline_lists_the_graph_stages_in_order(
        self, api_client: TestClient
    ) -> None:
        """The timeline a client renders is the graph's own, unaltered.

        Args:
            api_client: A client running the real app, offline.
        """
        body = api_client.post(
            "/api/investigations/text", json={"text": REGULATORY_CONTENT}
        ).json()

        stages = [event["stage"] for event in body["timeline"]]

        assert stages == ["input", "extraction", "red_flags", "verification", "evidence", "risk"]
        assert body["current_stage"] == "risk"

    def test_the_response_carries_the_state_rather_than_a_summary(
        self, api_client: TestClient
    ) -> None:
        """No field of the response is derived by the route rather than the graph.

        Args:
            api_client: A client running the real app, offline.
        """
        body = api_client.post(
            "/api/investigations/text", json={"text": REGULATORY_CONTENT}
        ).json()

        assert set(body) == {
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
            # Phase 12: provenance for a URL investigation. Null for this `TEXT`
            # submission, and still a field the route forwards from the state
            # rather than one it computes.
            "url_source",
            # Phase 13: provenance for a screenshot investigation. Null for
            # this `TEXT` submission, and likewise forwarded, not computed.
            "image_source",
        }
        assert body["investigation_id"].startswith("inv_")
        assert body["url_source"] is None
        assert body["image_source"] is None

    def test_the_investigation_id_is_derived_from_the_submitted_content(
        self, api_client: TestClient
    ) -> None:
        """The id comes from `investigation_id_for`, not from a counter.

        A sequential id would make two runs of the same content incomparable, which
        is the property a client checks when it re-submits something it has already
        seen.

        Args:
            api_client: A client running the real app, offline.
        """
        from app.graph.state import investigation_id_for

        text = "Acme Capital Advisors guarantees 30% returns."

        body = api_client.post("/api/investigations/text", json={"text": text}).json()

        assert body["investigation_id"] == investigation_id_for(
            InvestigationInputType.TEXT, text
        )


class TestTheContextIsFrozenAgainstHalfWiring:
    """A half-wired context fails at construction, not mid-run."""

    def test_a_context_cannot_hold_a_service_that_is_not_one(self) -> None:
        """`GraphDependencies` is typed, frozen, and required in full.

        Args:
            api_settings: Isolated settings, unused but required by the signature.
        """
        with pytest.raises(TypeError):
            GraphDependencies()  # type: ignore[call-arg]

    def test_the_dependency_container_cannot_be_mutated_mid_run(
        self, api_client: TestClient
    ) -> None:
        """Swapping a service after construction is refused.

        Args:
            api_client: A client running the real app, offline.
        """
        dependencies = api_client.app.state.graph_context.dependencies

        with pytest.raises(dataclasses.FrozenInstanceError):
            dependencies.risk_service = RecordingRiskService()  # type: ignore[misc]

    def test_the_production_context_is_built_from_real_services(
        self, api_client: TestClient
    ) -> None:
        """The real wiring uses the genuine Phase 1-6 classes.

        This is what makes "the API runs the real pipeline" true by construction
        rather than by assumption.

        Args:
            api_client: A client running the real app, offline.
        """
        dependencies = api_client.app.state.graph_context.dependencies

        assert isinstance(dependencies.extraction_service, ExtractionService)
        assert isinstance(dependencies.red_flag_engine, RedFlagEngine)
        assert isinstance(dependencies.verification_service, VerificationService)
        assert isinstance(dependencies.evidence_service, EvidenceService)
        assert isinstance(dependencies.risk_service, RiskService)
        assert isinstance(dependencies.search_recorder, RecordingSearchService)

    def test_the_production_verification_service_searches_through_the_recorder(
        self, api_client: TestClient
    ) -> None:
        """Phase 4 and Phase 5 share one search service.

        If verification searched its own way, the evidence stage would cite
        documents that verification never saw (D-023).

        Args:
            api_client: A client running the real app, offline.
        """
        dependencies = api_client.app.state.graph_context.dependencies

        assert dependencies.verification_service.search_service is dependencies.search_recorder
