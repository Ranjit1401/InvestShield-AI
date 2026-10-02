"""The generated OpenAPI document is part of the contract (Phase 10).

Phase 11 consumes this document, so it is a **published artefact** and needs its
own tests rather than being assumed correct because the endpoints work. A route can
return exactly the right body while its OpenAPI entry says something else, and the
only way to notice is to read the document.

Four properties are asserted here.

**Presence.** Every documented endpoint exists, with the documented methods, request
schemas and response schemas.

**Honesty.** A client reading the document learns what actually happens: the
`422`/`500` envelopes, the paging bounds on history, the `extra="forbid"` request
models, the fact that retrieval can `404`.

**Containment.** The document exposes no internal type. The Phase 1-6 domain models
are intentionally embedded in the response — that is D-038 — but the *request*
schemas, the persistence layer, the SQLAlchemy models and the repository are not
part of the contract, and their appearance would mean the boundary has leaked.

**No secrets, and no withdrawn design.** No credential may appear anywhere in the
document, including as a default or an example. And the `202 Accepted` plus
`GET`-for-polling shape that D-036 withdrew must be absent: a client generated
from a document that still advertises a job handle would poll an endpoint that does
not exist.

Nothing here rewrites the API to satisfy a test. Where the document and the code
disagree, the code is the truth and the test is what moves.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from tests.contract_helpers import CREDENTIALS, iter_json_strings, looks_like_a_secret

#: Every endpoint the product documents, with the methods it must answer.
EXPECTED_ENDPOINTS: dict[str, set[str]] = {
    "/api/health": {"get"},
    "/api/investigations": {"get", "post"},
    "/api/investigations/text": {"post"},
    "/api/investigations/limits": {"get"},
    "/api/investigations/{investigation_id}": {"get"},
}


@pytest.fixture
def openapi(api_client: TestClient) -> dict[str, object]:
    """Return the generated OpenAPI document.

    Args:
        api_client: A client running the real app, offline.

    Returns:
        The decoded document.
    """
    response = api_client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


class TestDocumentedSurface:
    """The document advertises exactly the endpoints that exist."""

    def test_every_documented_endpoint_is_present(
        self, openapi: dict[str, object]
    ) -> None:
        """No documented endpoint is missing, and none has an undocumented method.

        Args:
            openapi: The generated document.
        """
        paths = openapi["paths"]

        for path, methods in EXPECTED_ENDPOINTS.items():
            assert path in paths, f"{path} is missing from the document"
            assert methods <= set(paths[path]), (
                f"{path} documents {sorted(paths[path])}, expected at least "
                f"{sorted(methods)}"
            )

    def test_no_endpoint_beyond_the_documented_set_is_advertised(
        self, openapi: dict[str, object]
    ) -> None:
        """The document does not grow a route nobody decided to expose.

        `/` exists and is deliberately `include_in_schema=False`, so it must not
        appear. A future route added without thinking about the contract would
        otherwise be published to every client generator by default.

        Args:
            openapi: The generated document.
        """
        paths = set(openapi["paths"])

        assert paths == set(EXPECTED_ENDPOINTS)

    def test_the_root_pointer_route_is_not_published(self, openapi: dict[str, object]) -> None:
        """`/` is hidden from the schema, so a client generator ignores it.

        Args:
            openapi: The generated document.
        """
        assert "/" not in openapi["paths"]


class TestRequestAndResponseSchemas:
    """Both directions of every documented call are described."""

    @pytest.mark.parametrize(
        ("path", "method", "expected_status"),
        [
            ("/api/investigations/text", "post", "200"),
            ("/api/investigations", "post", "200"),
            ("/api/health", "get", "200"),
            ("/api/investigations", "get", "200"),
            ("/api/investigations/{investigation_id}", "get", "200"),
        ],
    )
    def test_a_response_is_described_by_a_named_model(
        self,
        openapi: dict[str, object],
        path: str,
        method: str,
        expected_status: str,
    ) -> None:
        """The named status carries a resolvable reference to a declared model.

        Only the endpoints that return a pydantic model are checked. `/limits`
        returns a plain `dict`, which is documented inline — see
        `test_the_limits_endpoint_is_documented_as_an_untyped_object`.

        Args:
            openapi: The generated document.
            path: The endpoint path.
            method: The HTTP method.
            expected_status: The status code that must be documented.
        """
        responses = openapi["paths"][path][method]["responses"]

        assert expected_status in responses, (
            f"{method.upper()} {path} does not document {expected_status}: "
            f"{sorted(responses)}"
        )
        schema = responses[expected_status]["content"]["application/json"]["schema"]
        assert "$ref" in schema, f"{expected_status} schema is inline, not a named model"
        assert schema["$ref"].rsplit("/", 1)[-1] in openapi["components"]["schemas"]

    def test_the_limits_endpoint_is_documented_as_an_untyped_object(
        self, openapi: dict[str, object]
    ) -> None:
        """`/limits` has no response model, which is pinned as a known limitation.

        The route returns `dict[str, object]`, so FastAPI publishes an inline
        `additionalProperties: true` and a Phase 11 generator types the response as
        `any`. That is a real gap, recorded rather than fixed here: adding a
        response model is a contract change, and Phase 10's mandate is to test the
        contract that exists. Pinning it means it cannot be forgotten.

        Args:
            openapi: The generated document.
        """
        responses = openapi["paths"]["/api/investigations/limits"]["get"]["responses"]
        schema = responses["200"]["content"]["application/json"]["schema"]

        assert schema["type"] == "object"
        assert schema["additionalProperties"] is True
        assert "$ref" not in schema
        # It takes no parameters, so it has nothing to validate and documents no
        # 422. Asserted so a future parameter addition is noticed here.
        assert "parameters" not in openapi["paths"]["/api/investigations/limits"]["get"]
        assert "422" not in responses

    @pytest.mark.parametrize(
        ("path", "method", "schema_name"),
        [
            ("/api/investigations/text", "post", "TextInvestigationRequest"),
            ("/api/investigations", "post", "InvestigationCreateRequest"),
        ],
    )
    def test_a_post_body_is_described_by_its_named_model(
        self, openapi: dict[str, object], path: str, method: str, schema_name: str
    ) -> None:
        """A request body names the model a client should generate.

        Args:
            openapi: The generated document.
            path: The endpoint path.
            method: The HTTP method.
            schema_name: The expected component name.
        """
        body = openapi["paths"][path][method]["requestBody"]

        assert body["required"] is True
        schema = body["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith(f"/{schema_name}")
        assert schema_name in openapi["components"]["schemas"]

    def test_a_request_model_forbids_unknown_fields(
        self, openapi: dict[str, object]
    ) -> None:
        """`extra="forbid"` is published, so a generated client cannot send extras.

        A client generated without this would not know the API rejects them, and
        would keep sending a field it invented. Publishing the constraint is what
        makes it usable.

        Args:
            openapi: The generated document.
        """
        for name in ("TextInvestigationRequest", "InvestigationCreateRequest"):
            schema = openapi["components"]["schemas"][name]
            assert schema["additionalProperties"] is False, f"{name} permits extras"

    def test_the_text_field_documents_its_length_bounds(
        self, openapi: dict[str, object]
    ) -> None:
        """The published bounds match the enforced ones.

        Args:
            openapi: The generated document.
        """
        text = openapi["components"]["schemas"]["TextInvestigationRequest"]["properties"]["text"]

        assert text["minLength"] == 1
        assert text["maxLength"] == 20_000

    def test_the_input_type_enum_lists_every_declared_kind(
        self, openapi: dict[str, object]
    ) -> None:
        """All four kinds are published even though only one is analysed.

        `PROJECT_CONTEXT.md` §9 commits the product to four inputs. Publishing only
        `TEXT` would make a generated client unable to send the others at all, and
        would quietly redefine the product as a text-only tool.

        Args:
            openapi: The generated document.
        """
        ref = openapi["components"]["schemas"]["InvestigationCreateRequest"]["properties"][
            "input_type"
        ]["$ref"]
        enum_name = ref.rsplit("/", 1)[-1]
        enum = openapi["components"]["schemas"][enum_name]

        assert set(enum["enum"]) == {"TEXT", "URL", "IMAGE", "PDF"}

    def test_the_status_enum_has_exactly_the_three_documented_values(
        self, openapi: dict[str, object]
    ) -> None:
        """`COMPLETED`, `PARTIAL`, `FAILED` — and nothing else.

        The withdrawn `PENDING`/`PROCESSING` values must not reappear in a
        generated client, because there is no such state in a synchronous API
        (D-036).

        Args:
            openapi: The generated document.
        """
        response = openapi["components"]["schemas"]["InvestigationResponse"]
        ref = response["properties"]["status"]["$ref"]
        enum = openapi["components"]["schemas"][ref.rsplit("/", 1)[-1]]

        assert set(enum["enum"]) == {"COMPLETED", "PARTIAL", "FAILED"}

    def test_history_paging_bounds_are_published(
        self, openapi: dict[str, object]
    ) -> None:
        """A client can discover the paging bounds without guessing.

        Args:
            openapi: The generated document.
        """
        parameters = openapi["paths"]["/api/investigations"]["get"]["parameters"]
        by_name = {parameter["name"]: parameter for parameter in parameters}

        assert by_name["limit"]["schema"]["minimum"] == 1
        assert by_name["limit"]["schema"]["maximum"] == 100
        assert by_name["limit"]["schema"]["default"] == 20
        assert by_name["offset"]["schema"]["minimum"] == 0
        assert by_name["offset"]["schema"]["default"] == 0

    def test_the_history_envelope_names_its_collection(
        self, openapi: dict[str, object]
    ) -> None:
        """The generated client learns the collection is called `investigations`.

        `items` in the Phase 8 draft would have produced a client reading a field
        that is not there.

        Args:
            openapi: The generated document.
        """
        schema = openapi["components"]["schemas"]["InvestigationListResponse"]

        assert set(schema["properties"]) == {"investigations", "total", "limit", "offset"}

    def test_retrieval_documents_the_404_envelope(
        self, openapi: dict[str, object]
    ) -> None:
        """A retrieval miss is part of the documented contract.

        Args:
            openapi: The generated document.
        """
        responses = openapi["paths"]["/api/investigations/{investigation_id}"]["get"][
            "responses"
        ]

        assert "404" in responses
        ref = responses["404"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/ErrorEnvelope")

    @pytest.mark.parametrize(
        ("path", "method", "status_code"),
        [
            ("/api/investigations/text", "post", "422"),
            ("/api/investigations/text", "post", "500"),
            ("/api/investigations", "post", "422"),
            ("/api/investigations", "post", "500"),
            ("/api/investigations/{investigation_id}", "get", "404"),
            ("/api/investigations/{investigation_id}", "get", "500"),
        ],
    )
    def test_every_declared_failure_status_is_present(
        self,
        openapi: dict[str, object],
        path: str,
        method: str,
        status_code: str,
    ) -> None:
        """A client can generate a failure branch for each documented status.

        Args:
            openapi: The generated document.
            path: The endpoint path.
            method: The HTTP method.
            status_code: The status code that must be documented.
        """
        responses = openapi["paths"][path][method]["responses"]

        assert status_code in responses, (
            f"{method.upper()} {path} does not document {status_code}: {sorted(responses)}"
        )


class TestDescriptions:
    """Every operation is described, because the document is documentation."""

    @pytest.mark.parametrize("path", sorted(EXPECTED_ENDPOINTS))
    def test_every_operation_has_a_summary_and_description(
        self, openapi: dict[str, object], path: str
    ) -> None:
        """No operation is published as a bare endpoint.

        A client developer reading only the generated docs learns nothing from a
        missing summary — which is how a `POST` that stores becomes a `POST` that
        does not, in someone's mental model.

        Args:
            openapi: The generated document.
            path: The endpoint path.
        """
        for method, operation in openapi["paths"][path].items():
            assert operation.get("summary"), f"{method.upper()} {path} has no summary"
            assert operation.get("description"), (
                f"{method.upper()} {path} has no description"
            )

    def test_the_document_says_the_api_is_not_advice(self, openapi: dict[str, object]) -> None:
        """The not-advice disclaimer survives into the published description.

        The product's central constraint has to be visible at the point a client
        developer reads the API, not only in the repository's own documentation.

        Args:
            openapi: The generated document.
        """
        description = openapi["info"]["description"]

        assert "not" in description.lower()
        assert re.search(r"not\s+financial\s+advice", description, re.IGNORECASE)

    def test_the_write_endpoints_state_that_the_run_is_stored(
        self, openapi: dict[str, object]
    ) -> None:
        """A `POST` that stores says so in the operation description.

        Args:
            openapi: The generated document.
        """
        for path in ("/api/investigations", "/api/investigations/text"):
            description = openapi["paths"][path]["post"]["description"].lower()
            assert "stored" in description or "store" in description, (
                f"POST {path} does not say its run is stored"
            )


class TestNoInternalSchemasAreExposed:
    """The document describes the HTTP boundary and nothing behind it."""

    def test_no_persistence_type_is_published(self, openapi: dict[str, object]) -> None:
        """No SQLAlchemy table, session or repository type reaches the document.

        `InvestigationSummary` is a repository type that happens to be shaped like
        a response; the ORM classes themselves must never appear, or a client
        generator would produce a model bound to our storage layout.

        Args:
            openapi: The generated document.
        """
        names = set(openapi["components"]["schemas"])

        forbidden = {"Base", "DeclarativeBase", "Session", "Database", "Engine"}
        assert not (names & forbidden)

        for name in names:
            assert not name.endswith("Row"), f"{name} is an ORM type"
            assert not name.endswith("Model"), f"{name} is an ORM type"
            assert not name.endswith("Repository"), f"{name} is a persistence type"

    def test_no_engineering_internals_are_published(
        self, openapi: dict[str, object]
    ) -> None:
        """Graph, adapter and configuration internals are not part of the contract.

        `GraphStage` and `TimelineStatus` are deliberately *not* forbidden: they are
        response field types — `timeline[].stage`, `warnings[].stage` — and Phase 11
        needs the vocabulary. What must not appear is the machinery that produces
        them, or the state object that carries them between stages.

        Args:
            openapi: The generated document.
        """
        names = set(openapi["components"]["schemas"])

        forbidden = {
            "GraphContext",
            "GraphDependencies",
            "GraphWarning",
            "GraphError",
            "InvestigationState",
            "Settings",
            "RecordingSearchService",
        }
        assert not (names & forbidden), f"internals published: {sorted(names & forbidden)}"

    def test_the_published_stage_vocabulary_is_the_documented_one(
        self, openapi: dict[str, object]
    ) -> None:
        """The stage enum a client switches on has exactly the seven documented values.

        Args:
            openapi: The generated document.
        """
        enum = openapi["components"]["schemas"]["GraphStage"]

        assert set(enum["enum"]) == {
            "input",
            "extraction",
            "red_flags",
            "verification",
            "evidence",
            "risk",
            "completed",
        }

    def test_the_published_timeline_vocabulary_has_all_five_states(
        self, openapi: dict[str, object]
    ) -> None:
        """`PARTIAL`, `FAILED` and `SKIPPED` are all documented to a client.

        These three are how "checked less than we wanted" stays distinguishable from
        "checked and found nothing" (D-006). A client that cannot see them cannot
        render the distinction either.

        Args:
            openapi: The generated document.
        """
        enum = set(openapi["components"]["schemas"]["TimelineStatus"]["enum"])

        assert enum == {"STARTED", "COMPLETED", "PARTIAL", "FAILED", "SKIPPED"}

    def test_the_search_bookkeeping_shape_is_not_a_separate_top_level_field(
        self, openapi: dict[str, object]
    ) -> None:
        """Search stays inside `evidence[].source`, as D-038 decided.

        A flattened top-level `sources` would be a second copy able to disagree
        with the per-claim evidence.

        Args:
            openapi: The generated document.
        """
        properties = set(openapi["components"]["schemas"]["InvestigationResponse"]["properties"])

        assert "sources" not in properties
        assert "search_results" not in properties


class TestNoSecretsInTheDocument:
    """A published document is public, so it may not carry a credential."""

    def test_no_credential_value_appears_anywhere_in_the_document(
        self, api_client: TestClient
    ) -> None:
        """Every string in the document is checked, at any depth.

        Args:
            api_client: A client running the real app, offline.
        """
        document = api_client.get("/openapi.json").json()

        for path, value in iter_json_strings(document):
            for name, secret in CREDENTIALS.items():
                assert secret not in value, f"{name} leaked at {path}"

    def test_no_string_in_the_document_looks_like_a_credential(
        self, api_client: TestClient
    ) -> None:
        """Shape, not just value: a DSN or key pattern anywhere is a failure.

        Args:
            api_client: A client running the real app, offline.
        """
        document = api_client.get("/openapi.json").json()

        offenders = [
            path for path, value in iter_json_strings(document) if looks_like_a_secret(value)
        ]
        assert not offenders, f"credential-shaped strings at {offenders}"

    def test_no_environment_variable_name_is_published(self, openapi: dict[str, object]) -> None:
        """The document does not name the environment variables it reads.

        Publishing `GROQ_API_KEY` in a description is harmless on its own, but a
        client developer who sees it may wire it into a frontend — where it would
        be exposed to every browser.

        Args:
            openapi: The generated document.
        """
        document = openapi
        for _, value in iter_json_strings(document):
            for name in ("GROQ_API_KEY", "SERPAPI_KEY", "DATABASE_URL"):
                assert name not in value, f"{name} is named in the published document"

    def test_the_documented_app_version_is_not_a_secret_bearing_field(
        self, openapi: dict[str, object]
    ) -> None:
        """The app's own metadata carries a version, not configuration.

        Args:
            openapi: The generated document.
        """
        info = openapi["info"]

        assert isinstance(info["version"], str)
        assert not looks_like_a_secret(info["version"])
        assert set(info) >= {"title", "version", "description"}


class TestWithdrawnDesignsAreAbsent:
    """A withdrawn design must not survive in the published document."""

    def test_no_endpoint_advertises_a_202(self, openapi: dict[str, object]) -> None:
        """No endpoint returns `202 Accepted`.

        D-036 withdrew the asynchronous shape. A `202` in the document would send
        a Phase 11 client looking for a job handle, and it would poll an endpoint
        that does not exist.

        Args:
            openapi: The generated document.
        """
        found: list[str] = []
        for path, operations in openapi["paths"].items():
            for method, operation in operations.items():
                if "202" in operation.get("responses", {}):
                    found.append(f"{method.upper()} {path}")

        assert not found, f"an endpoint still advertises 202: {found}"

    def test_the_run_statuses_are_not_the_withdrawn_polling_pair(
        self, openapi: dict[str, object]
    ) -> None:
        """`PENDING` and `PROCESSING` are not statuses of this API.

        Args:
            openapi: The generated document.
        """
        response = openapi["components"]["schemas"]["InvestigationResponse"]
        ref = response["properties"]["status"]["$ref"]
        enum = set(openapi["components"]["schemas"][ref.rsplit("/", 1)[-1]]["enum"])

        assert not (enum & {"PENDING", "PROCESSING", "QUEUED", "RUNNING"})

    def test_no_polling_endpoint_exists(self, openapi: dict[str, object]) -> None:
        """No `/status` or `/jobs` route is published or present.

        Args:
            openapi: The generated document.
        """
        paths = set(openapi["paths"])

        for candidate in ("/api/jobs/{job_id}", "/api/status", "/api/investigations/status"):
            assert candidate not in paths, f"{candidate} is a polling endpoint"


class TestDocumentIsWellFormed:
    """The document is valid OpenAPI 3.1, or tooling in Phase 11 will reject it."""

    def test_the_document_declares_a_supported_openapi_version(
        self, openapi: dict[str, object]
    ) -> None:
        """Args:
            openapi: The generated document.
        """
        assert openapi["openapi"].startswith("3.")

    def test_every_ref_resolves_to_a_declared_component(
        self, openapi: dict[str, object]
    ) -> None:
        """No `$ref` in the document points at a schema that is not there.

        A dangling reference is the most common way a generated client ends up with
        an `any`-typed field, which defeats the purpose of publishing models at all.

        Args:
            openapi: The generated document.
        """
        declared = set(openapi["components"]["schemas"])
        dangling: list[str] = []

        for path, value in iter_json_strings(openapi):
            del path  # The refs are collected separately below.
            del value

        def walk(node: object, where: str) -> None:
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                    if ref.rsplit("/", 1)[-1] not in declared:
                        dangling.append(f"{where} -> {ref}")
                for key, item in node.items():
                    walk(item, f"{where}.{key}")
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    walk(item, f"{where}[{index}]")

        walk(openapi, "$")

        assert not dangling, f"dangling $ref(s): {sorted(set(dangling))}"

    def test_the_document_is_served_as_json_with_a_version(self, api_client: TestClient) -> None:
        """The document endpoint answers with a usable content type.

        Args:
            api_client: A client running the real app, offline.
        """
        response = api_client.get("/openapi.json")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
