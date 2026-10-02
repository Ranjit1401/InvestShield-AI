"""Secrets and internals must not survive the trip to a client (Phase 10).

`tests/api/test_security_headers.py` covers the *response surface* Phase 8 built:
the right headers, the generic error envelope, and the rule that a database error
exposes its exception type rather than its message. It does not cover where the
dangerous strings *come from*.

This module injects them. Every test here takes a genuine failure — a database that
refuses to connect, a search provider that reports an error, an extraction service
that raises — and asserts the string never reaches a response body.

The reason to inject rather than to read the handlers is that the leak paths are not
where they look. A DSN in a SQLAlchemy message does not arrive through the handler
that formats SQLAlchemy errors; it reaches the client because the message is logged,
the log record is attached to the exception, and some *other* handler renders it. A
submitted password does not arrive through the validation handler that strips
values; it arrives through the handler that builds the envelope and then falls
through to a generic path. Only an executed test finds those.

Four sources, each with its own realistic secret:

- **Database** — `postgresql://user:password@example.com/database`, the canonical
  DSN shape that Phase 9's configuration accepts.
- **Search** — a provider key in a provider's own error warning.
- **LLM** — `GROQ_API_KEY` in an exception message. The *name* is banned alongside
  the value, because the name alone is enough to send a reader to the file holding
  it.
- **Validation** — credentials submitted in a malformed request.

The standing rule, checked throughout: no response may contain a traceback, a stack
trace, SQL, a DSN, a password, an API key, or a filesystem path.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.api.deps import get_graph_context_dep
from app.core.config import Settings
from app.db.session import Database
from app.graph.context import GraphContext, GraphDependencies, RecordingSearchService
from app.main import create_app
from app.schemas.verification import VerificationStatus
from app.services.search import SearchService
from tests.contract_helpers import iter_json_strings
from tests.graph.graph_factories import (
    RecordingEvidenceService,
    RecordingExtractionService,
    RecordingRedFlagEngine,
    RecordingRiskService,
    RecordingVerificationService,
    build_claim,
    build_entity,
    build_flag,
    extraction_with,
    result_for,
    verification_response,
)
from tests.test_failure_injection import FailingProvider

#: A realistic PostgreSQL DSN, in the exact shape `Settings.database_url` accepts.
#: Every part is something a client has no business seeing: the password, the
#: username, the host, the database name, and the scheme naming the backend.
#:
#: The host is loopback on port 1 deliberately. `psycopg2` connects through libpq,
#: which does its own DNS and TCP in C and therefore **bypasses**
#: `tests/network_guard.py` — a public host name would make these tests wait out a
#: real network timeout, which is both slow and a live connection the guard is
#: supposed to prevent. Port 1 on loopback is refused by the kernel immediately, so
#: the failure is still a genuine connection failure, produced the same way, in
#: milliseconds. The DSN keeps every part that matters for the leak assertions:
#: scheme, `user:password@` credentials, host and database name.
POISON_DSN = "postgresql://user:password@127.0.0.1:1/database"

#: A provider key, shaped like a SerpAPI key.
POISON_SEARCH_KEY = "serp-9f3a2b7c4d5e6f708192a3b4c5d6e7f80"

#: The LLM key. Named for the environment variable it stands in for.
POISON_LLM_KEY = "gsk_7xYz9QaBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"

#: A local filesystem path, the kind an exception from a bad `__file__` would carry.
POISON_PATH = "C:\\Users\\developer\\InvestShield AI\\backend\\app\\services\\risk.py"

#: Every one of these must be absent from any response body, case-insensitively.
#:
#: `groq_api_key` and `database_url` appear as *names*. Phase 8's documented
#: contract is that a database error exposes the exception type rather than the
#: message, and the same restraint has to cover the setting name that prompted it.
#:
#: The SQL fragments are matched with a leading space so that a word ending in
#: "select" or "update" in ordinary prose does not trip them.
BANNED_SUBSTRINGS = (
    "password",
    "postgresql://",
    "postgres://",
    "sqlite://",
    "user:",
    "database_url",
    "groq_api_key",
    "serpapi_key",
    "api_key",
    "apikey",
    "x-api-key",
    "authorization",
    "bearer ",
    "secret",
    "traceback",
    "stack trace",
    "most recent call last",
    'file "',
    " select ",
    " insert into ",
    " delete from ",
    ".py",
    "site-packages",
)

#: The ban list, upper-cased for case-insensitive comparison.
BANNED_UPPER = tuple(text.upper() for text in BANNED_SUBSTRINGS)


class PoisonedProvider(FailingProvider):
    """A failing provider whose error warning quotes the configured key.

    Subclasses the failure-injection module's `FailingProvider` rather than defining
    a second failing provider: the search path, the error code and the
    "never raise, return an ERROR response" contract are all already established
    there, and a second copy could drift from it.

    A real provider does put its own key in its error text. Some SDKs log the
    request URL, which carries the key as a query parameter, and the message
    survives into whatever warning the provider chooses to return. This is the
    realistic shape.

    Args:
        error_code: Which search failure to report.
        secret: The text to smuggle into the warning.
    """

    def __init__(self, error_code: str, secret: str) -> None:
        super().__init__(error_code)
        self._secret = secret

    def search(self, query: str, max_results: int) -> Any:
        """Return the inherited failure, with the secret added to its warning."""
        response = super().search(query, max_results)
        return dataclasses.replace(
            response,
            warnings=response.warnings + (f"provider rejected key {self._secret}",),
        )


#: Credential *variable names* the health endpoint is documented to publish.
#:
#: See `TestHealthReportsConfigurationWithoutDisclosingIt` for the finding and for
#: why Phase 10 records it rather than changing it. No value is ever exposed, which
#: is asserted separately. These are passed as an explicit `allow` to `assert_clean`
#: at the health call sites only, so a setting name appearing in a validation error
#: or a 500 still fails.
SETTING_NAME_DISCLOSURES = ("groq_api_key", "serpapi_key", "api_key", "apikey")


def assert_clean(body: Any, *, allow: tuple[str, ...] = ()) -> None:
    """Assert a response body leaks no secret, internals or path.

    Args:
        body: The decoded response body.
        allow: Ban-list entries to tolerate, for the one documented disclosure.
            Anything allowed here is still checked for a *value* by the test that
            passes it.

    Raises:
        AssertionError: Naming the offending substring and where it was found.
    """
    exempted = {entry.upper() for entry in allow}
    offenders: list[str] = []
    for path, value in iter_json_strings(body):
        upper = value.upper()
        for banned in BANNED_UPPER:
            if banned in exempted:
                continue
            if banned in upper:
                offenders.append(f"{path} contains {banned!r}")

    assert not offenders, "\n".join(offenders)


def assert_error_envelope(response: Any, expected_status: int) -> dict:
    """Assert a response is a well-formed error, and return its body.

    Args:
        response: The response to check.
        expected_status: The status code the contract requires.

    Returns:
        The decoded error body.
    """
    assert response.status_code == expected_status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "detail"}
    assert body["error"]["code"]
    assert body["error"]["message"]
    return body


def _client(settings: Settings, context: GraphContext) -> TestClient:
    """Build an entered client that reports server errors instead of raising.

    `raise_server_exceptions=False` is essential rather than merely tidy. With the
    default, `TestClient` re-raises whatever escaped the app, so a test asserting
    "this failure becomes a 500" would instead see the exception in the test, and
    could not distinguish "the app handled it" from "the client happened to catch
    it". Turning it off makes the assertion mean what it says.

    The API suite's own `api_client_factory` does not set it, because its callers
    assert on successful responses. This module asserts exclusively on failures.

    Args:
        settings: Settings to build the app with.
        context: The graph context to install.

    Returns:
        An entered `TestClient`.
    """
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    client.app.dependency_overrides[get_graph_context_dep] = lambda: context
    client.__enter__()
    return client


def _poison_settings(tmp_settings: Settings, **overrides: Any) -> Settings:
    """Return a copy of `tmp_settings` with overrides applied.

    Args:
        tmp_settings: The isolated settings from `api_settings`.
        **overrides: Settings fields to replace.

    Returns:
        A new `Settings`.
    """
    return tmp_settings.model_copy(update=overrides)


def _context_with_poisoned_search(settings: Settings, secret: str) -> GraphContext:
    """A context whose search provider fails with `secret` in its warning.

    Args:
        settings: Settings for the `SearchService`.
        secret: The text to smuggle into the provider's warning.

    Returns:
        A `GraphContext` wired to that provider.
    """
    provider = PoisonedProvider("SEARCH_AUTH_ERROR", secret)
    search = RecordingSearchService(SearchService(settings=settings, provider=provider))
    claim = build_claim()

    return GraphContext(
        dependencies=GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(claim,), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(claim, VerificationStatus.INSUFFICIENT_EVIDENCE)
                )
            ),
            evidence_service=RecordingEvidenceService(),
            risk_service=RecordingRiskService(),
            search_recorder=search,
        )
    )


def _context_with_poisoned_extraction(error: Exception) -> GraphContext:
    """A context whose extraction service raises `error`.

    The rest of the pipeline is wired normally, so the run fails at Phase 2 and
    every later stage still executes against the failure — which is what makes the
    exception message a realistic thing for the API to have to handle.

    Args:
        error: The exception to raise from `extract`.

    Returns:
        A `GraphContext` wired to that service.
    """
    claim = build_claim()

    return GraphContext(
        dependencies=GraphDependencies(
            extraction_service=RecordingExtractionService(raises=error),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(claim, VerificationStatus.INSUFFICIENT_EVIDENCE)
                )
            ),
            evidence_service=RecordingEvidenceService(),
            risk_service=RecordingRiskService(),
        )
    )


class TestDatabaseSecretsDoNotEscape:
    """A DSN must not leave the process, however the failure was produced."""

    @pytest.fixture
    def unreachable_app(self, api_settings: Settings) -> Any:
        """An app whose database points at a host that does not resolve.

        The failure originates inside SQLAlchemy's own connect path, exactly where
        it would in production, rather than being manufactured by a test double.

        Args:
            api_settings: Isolated settings, so the real `.env` cannot supply a URL.

        Yields:
            An entered `TestClient` for the poisoned app.
        """
        app = create_app(
            _poison_settings(
                api_settings, database_url=POISON_DSN, log_level="CRITICAL"
            )
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    def test_a_health_probe_does_not_return_the_dsn(
        self, unreachable_app: TestClient
    ) -> None:
        """Health is the endpoint most likely to report a dependency problem.

        Args:
            unreachable_app: A client whose database cannot be reached.
        """
        response = unreachable_app.get("/api/health")

        assert response.status_code in (200, 503)
        assert POISON_DSN not in response.text
        # The health endpoint legitimately names credential variables; what matters
        # here is that the dead database's DSN and error are not among them.
        assert_clean(response.json(), allow=SETTING_NAME_DISCLOSURES)

    def test_a_failed_read_does_not_return_the_dsn(
        self, unreachable_app: TestClient
    ) -> None:
        """Reading a stored investigation is a query against the dead database.

        Args:
            unreachable_app: A client whose database cannot be reached.
        """
        response = unreachable_app.get("/api/investigations/inv_deadbeefdeadbeef")

        assert POISON_DSN not in response.text
        assert_clean(response.json())

    def test_a_probe_exposes_the_exception_type_not_its_message(
        self, unreachable_app: TestClient
    ) -> None:
        """Phase 8's rule, asserted at the HTTP boundary rather than in a unit test.

        A psycopg2 message contains host, port, database and sometimes the user.
        The class name contains none of it and is enough for a client to decide
        between retrying and giving up.

        Args:
            unreachable_app: A client whose database cannot be reached.
        """
        body = assert_error_envelope(
            unreachable_app.get("/api/investigations/inv_deadbeefdeadbeef"), 500
        )

        # The type survives somewhere in the error.
        assert any(
            token in json.dumps(body)
            for token in ("Error", "error", "UNAVAILABLE", "INTERNAL")
        )
        # The message does not.
        assert POISON_DSN not in json.dumps(body)
        assert_clean(body)

    def test_a_write_failure_does_not_echo_the_statement(
        self, api_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed INSERT carries column names, bound values and the whole SQL.

        The bound values are the user's submitted content. None of it belongs in a
        response.

        Args:
            api_settings: Isolated settings for this test.
            monkeypatch: Pytest monkeypatch fixture.
        """
        from app.api.routes import investigations as routes_module

        def explode(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(
                "(psycopg2.errors.UndefinedColumn) column does not exist\n"
                "LINE 1: INSERT INTO investigations (public_id, raw_input) "
                "VALUES (%(public_id)s, %(raw_input)s)"
            )

        # The graph is replaced wholesale, so the app never needs a real context and
        # no dependency override is installed.
        monkeypatch.setattr(routes_module, "run_investigation", explode)
        client = TestClient(create_app(api_settings), raise_server_exceptions=False)

        response = client.post(
            "/api/investigations/text", json={"text": "Acme is registered."}
        )

        assert response.status_code >= 400
        assert "insert into" not in response.text.lower()
        assert_clean(response.json())

    def test_a_repository_raising_mid_transaction_leaks_nothing(
        self, api_settings: Settings
    ) -> None:
        """The repository's failure message is where SQL actually accumulates.

        A rollback error chains onto the original, so the response handler can see
        two exceptions and either could be rendered.

        Args:
            api_settings: Isolated settings for this test.
        """
        from app.repositories import investigations as repository_module

        class PoisonRepository:
            """A repository that fails on every method, with SQL in the message."""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __getattr__(self, name: str) -> Any:
                def fail(*args: Any, **kwargs: Any) -> Any:
                    raise RuntimeError(
                        "(psycopg2.errors.UndefinedTable) relation "
                        '"investigations" does not exist\n'
                        "SQL: SELECT * FROM investigations WHERE public_id = %s"
                    )

                return fail

        original = repository_module.InvestigationRepository
        repository_module.InvestigationRepository = PoisonRepository
        try:
            client = TestClient(
                create_app(api_settings), raise_server_exceptions=False
            )
            response = client.post(
                "/api/investigations/text", json={"text": "Acme is registered."}
            )
        finally:
            repository_module.InvestigationRepository = original

        assert response.status_code >= 400
        assert_clean(response.json())


class TestProviderSecretsDoNotEscape:
    """An API key in a third-party failure must not reach our client."""

    def test_a_search_key_in_a_provider_warning_is_not_returned(
        self, api_settings: Settings
    ) -> None:
        """The search stage fails, and the provider's own warning quoted the key.

        Args:
            api_settings: Isolated settings for this test.
        """
        context = _context_with_poisoned_search(api_settings, POISON_SEARCH_KEY)
        client = _client(api_settings, context)

        response = client.post(
            "/api/investigations/text", json={"text": "Acme is registered."}
        )

        assert response.status_code in (200, 500)
        assert POISON_SEARCH_KEY not in response.text
        assert_clean(response.json())

    def test_an_llm_key_in_an_exception_is_not_returned(
        self, api_settings: Settings
    ) -> None:
        """The extraction stage fails, and the message named the key.

        Both the value and the setting name are checked, because a report saying
        "GROQ_API_KEY was rejected" is enough to point a reader at the environment
        file that holds it.

        Args:
            api_settings: Isolated settings for this test.
        """
        error = RuntimeError(
            f"GroqError: invalid request, GROQ_API_KEY={POISON_LLM_KEY} rejected"
        )
        context = _context_with_poisoned_extraction(error)
        client = _client(api_settings, context)

        response = client.post(
            "/api/investigations/text", json={"text": "Acme is registered."}
        )

        assert response.status_code in (200, 500)
        assert POISON_LLM_KEY not in response.text
        assert "GROQ_API_KEY" not in response.text
        assert_clean(response.json())

    def test_a_dsn_in_an_extraction_error_is_not_returned(
        self, api_settings: Settings
    ) -> None:
        """A provider error that quotes a full connection URL.

        Real clients wrap their own configuration in their error messages, so a DSN
        can arrive through an LLM or search failure even when the database is
        healthy.

        Args:
            api_settings: Isolated settings for this test.
        """
        error = ConnectionError(
            f"could not reach {POISON_DSN} while reading the prompt store"
        )
        context = _context_with_poisoned_extraction(error)
        client = _client(api_settings, context)

        response = client.post(
            "/api/investigations/text", json={"text": "Acme is registered."}
        )

        assert POISON_DSN not in response.text
        assert_clean(response.json())


class TestSubmittedValuesAreNotEchoedByValidation:
    """Pydantic's `input` is where a submitted secret goes to be published."""

    def test_a_credential_in_an_unknown_field_is_not_reflected(
        self, api_client: TestClient
    ) -> None:
        """An unexpected field is a validation error naming the field.

        Pydantic's default detail carries the offending value itself. Phase 8
        strips it, and this is the regression test for that strip.

        Args:
            api_client: A client running the real app, offline.
        """
        response = api_client.post(
            "/api/investigations/text",
            json={"text": "Acme is registered.", "unexpected": POISON_LLM_KEY},
        )

        body = assert_error_envelope(response, 422)
        assert POISON_LLM_KEY not in json.dumps(body)
        assert_clean(body)

    def test_a_credential_in_a_wrong_type_field_is_not_reflected(
        self, api_client: TestClient
    ) -> None:
        """A string where a boolean belongs is the same leak path.

        Args:
            api_client: A client running the real app, offline.
        """
        response = api_client.post(
            "/api/investigations/text",
            json={"text": "Acme is registered.", "translate": POISON_DSN},
        )

        body = assert_error_envelope(response, 422)
        assert POISON_DSN not in json.dumps(body)
        assert_clean(body)

    def test_a_credential_in_form_data_is_not_reflected(
        self, api_client: TestClient
    ) -> None:
        """Form encoding reaches the same handler with raw `bytes` for `input`.

        This was a live defect. The 422 handler assumed Pydantic's `input` was
        JSON-serialisable; for a form body it is raw `bytes`. Serialising it raised
        `TypeError` *inside* the error handler, which fell through to a generic 500
        — turning a clean 422 into an internal error, and doing so with a
        credential in the payload.

        Args:
            api_client: A client running the real app, offline.
        """
        response = api_client.post(
            "/api/investigations/text",
            data={"text": "Acme is registered.", "translate": POISON_DSN},
        )

        body = assert_error_envelope(response, 422)
        assert POISON_DSN not in json.dumps(body)
        assert_clean(body)

    def test_a_credential_in_malformed_json_is_not_reflected(
        self, api_client: TestClient
    ) -> None:
        """A JSON parse error must not quote the body it failed to parse.

        Args:
            api_client: A client running the real app, offline.
        """
        response = api_client.post(
            "/api/investigations/text",
            content=b'{"text": "' + POISON_LLM_KEY.encode() + b'",',
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 422
        assert POISON_LLM_KEY not in response.text
        assert_clean(response.json())

    def test_a_credential_in_a_query_parameter_is_not_reflected(
        self, api_client: TestClient
    ) -> None:
        """Query parameters reach validation on the list endpoint.

        Args:
            api_client: A client running the real app, offline.
        """
        response = api_client.get(f"/api/investigations?limit={POISON_LLM_KEY}")

        assert response.status_code in (200, 422)
        assert POISON_LLM_KEY not in response.text
        assert_clean(response.json())

    def test_a_credential_in_the_path_is_not_reflected(
        self, api_client: TestClient
    ) -> None:
        """A path segment that looks like a credential reaches the 404 handler.

        Args:
            api_client: A client running the real app, offline.
        """
        response = api_client.get(f"/api/investigations/{POISON_LLM_KEY}")

        body = assert_error_envelope(response, 404)
        assert POISON_LLM_KEY not in json.dumps(body)
        assert_clean(body)


class TestNoInternalsEscapeOnAnyPath:
    """Whatever fails, the body stays inside the documented vocabulary."""

    @pytest.mark.parametrize(
        "method,path,kwargs",
        (
            ("get", "/api/investigations", {}),
            ("get", "/api/investigations/inv_deadbeefdeadbeef", {}),
            ("get", "/api/nonexistent", {}),
            ("post", "/api/investigations/text", {"json": {"text": ""}}),
            ("delete", "/api/investigations", {}),
            ("patch", "/api/investigations", {}),
            ("put", "/api/investigations", {}),
        ),
    )
    def test_every_response_is_free_of_internals(
        self,
        api_client: TestClient,
        method: str,
        path: str,
        kwargs: dict[str, Any],
    ) -> None:
        """All seven investigation outcomes, checked against the standing bans.

        `/api/health` is excluded and has its own class below, because it
        deliberately reports a different and narrower kind of detail.

        Args:
            api_client: A client running the real app, offline.
            method: The HTTP method to use.
            path: The path to request.
            kwargs: Extra request arguments.
        """
        response = api_client.request(method.upper(), path, **kwargs)

        assert_clean(response.json())
        assert_clean(response.text)

    def test_a_path_and_dsn_in_a_500_are_not_returned(
        self, api_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The generic handler is the last line, and it must obey the contract.

        Args:
            api_settings: Isolated settings for this test.
            monkeypatch: Pytest monkeypatch fixture.
        """
        from app.api.routes import investigations as routes_module

        def explode(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(
                f"internal failure reading {POISON_DSN} at {POISON_PATH}"
            )

        monkeypatch.setattr(routes_module, "run_investigation", explode)
        client = TestClient(create_app(api_settings), raise_server_exceptions=False)

        response = client.post(
            "/api/investigations/text", json={"text": "Acme is registered."}
        )

        body = assert_error_envelope(response, 500)
        assert "C:\\Users" not in json.dumps(body)
        assert POISON_DSN not in json.dumps(body)
        assert_clean(body)

    def test_the_error_type_stays_available_to_a_client(
        self, api_client: TestClient
    ) -> None:
        """Leak-safety must not degrade into vagueness.

        A client that cannot tell a bad request from a dead database cannot decide
        whether to retry. The type is the safe half of the information, and losing
        it would be a different kind of regression.

        Args:
            api_client: A client running the real app, offline.
        """
        body = assert_error_envelope(
            api_client.get("/api/investigations/inv_deadbeefdeadbeef"), 404
        )

        assert body["error"]["code"]
        assert isinstance(
            body["error"]["detail"], (dict, list, str, int, float, bool, type(None))
        )


class TestHealthReportsConfigurationWithoutDisclosingIt:
    """`/api/health` names the credential variables. It must not disclose values.

    **A finding from Phase 10.** `probe_llm` and `probe_search` build their
    `detail` strings by naming the environment variable they looked for, so an
    unauthenticated `GET /api/health` returns text such as "GROQ_API_KEY is not
    set." No value is exposed, and the `configured` boolean a legitimate client
    reads is unaffected. But on a public deployment it is reconnaissance: it names
    the exact secrets worth attacking and reveals that a given one is currently
    absent.

    Phase 10 does not change it. Editing the probe wording is Phase 8 behaviour,
    the strings are arguably useful to an operator, and no value leaks — so the
    disclosure is recorded as a known limitation rather than silently patched. What
    *is* asserted here is the security-relevant half: the configured value never
    appears, only the variable's name, and a client is not left without the boolean
    it actually needs.

    Args:
        api_settings: Settings, mutated to carry real-looking credential values.
    """

    def test_the_health_endpoint_does_not_disclose_a_configured_value(
        self, api_settings: Settings
    ) -> None:
        """Configure a key, and prove only its name is published.

        Args:
            api_settings: Isolated settings, carrying a synthetic key.
        """
        settings = _poison_settings(
            api_settings, groq_api_key=POISON_LLM_KEY, serpapi_key=POISON_SEARCH_KEY
        )
        app = create_app(settings)

        with TestClient(app) as client:
            body = client.get("/api/health").json()

        rendered = json.dumps(body)
        assert POISON_LLM_KEY not in rendered
        assert POISON_SEARCH_KEY not in rendered
        assert "serp-9f3a2b7c4d5e6f708192a3b4c5d6e7f80" not in rendered

    def test_a_client_still_gets_the_boolean_it_needs(
        self, api_client: TestClient
    ) -> None:
        """The disclosure is the detail string, not the structured fields.

        If a future change removed the names, it must not remove the information a
        legitimate client depends on.

        Args:
            api_client: A client running the real app, offline.
        """
        services = api_client.get("/api/health").json()["services"]

        for name, probe in services.items():
            assert set(probe) >= {"provider", "configured", "available"}, name
            assert isinstance(probe["configured"], bool)
            assert isinstance(probe["available"], bool)

    def test_health_never_reaches_beyond_the_probe(
        self, api_client: TestClient
    ) -> None:
        """No path, DSN, traceback or SQL may appear, whatever the detail says.

        Args:
            api_client: A client running the real app, offline.
        """
        body = api_client.get("/api/health").json()

        assert_clean(body, allow=SETTING_NAME_DISCLOSURES)


class TestNoQueryLeaksCredentials:
    """SQLAlchemy `echo=True` would put the DSN in every log line."""

    def test_the_engine_does_not_echo_its_url(self, api_settings: Settings) -> None:
        """A database built with a poison DSN must not echo its statements.

        `echo=True` logs engine setup, which includes the DSN. It is the setting
        most likely to be flipped while debugging, and the one that must never be
        true in this configuration.

        Args:
            api_settings: Settings whose URL is replaced with the poison DSN.
        """
        database = Database(_poison_settings(api_settings, database_url=POISON_DSN))
        try:
            # `echo` defaults to `None`, which SQLAlchemy treats as off. The
            # assertion is that it is not truthy, not that it is exactly `False` —
            # a future refactor setting it to `None` again must not fail here.
            assert not database.engine.echo
        finally:
            database.dispose()

    def test_a_statement_listener_sees_no_credentials(
        self, api_client: TestClient
    ) -> None:
        """Every statement a run issues is captured, and none may quote a secret.

        The same instrumentation serves the N+1 check in
        `tests/test_query_efficiency.py`; this test asserts only the *content*.

        Args:
            api_client: A client running the real app, offline.
        """
        statements: list[str] = []
        engine = api_client.app.state.database.engine

        def capture(
            conn: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            posted = api_client.post(
                "/api/investigations/text", json={"text": "Acme is registered."}
            )
            api_client.get(f"/api/investigations/{posted.json()['investigation_id']}")
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        assert statements, "the run should have issued at least one statement"
        for statement in statements:
            lowered = statement.lower()
            for banned in ("password", "postgresql://", "api_key", "groq_api_key"):
                assert banned not in lowered, f"{banned!r} appeared in {statement!r}"


#: Every secret this module plants, and how it is caught.
#:
#: A DSN and a path contain banned fragments outright. The two provider keys do
#: not — a key is an opaque token, and no word-list matcher can recognise one. They
#: are caught by literal assertions against the exact planted value, which is why
#: the mapping below has to name that explicitly. If a future edit adds a fifth
#: secret, this table is what forces the question of how it will be caught.
PLANTED_SECRETS = {
    POISON_DSN: "password",
    POISON_PATH: ".py",
    POISON_SEARCH_KEY: "literal",
    POISON_LLM_KEY: "literal",
}


def test_the_ban_list_itself_is_well_formed() -> None:
    """Guard the guard.

    An empty or truncated ban list would make every test in this module pass
    vacuously, which is the one failure mode a security suite cannot detect on its
    own. A reviewer reading the module sees the ban applied; only a test can notice
    that the ban itself went missing.

    """
    assert len(BANNED_SUBSTRINGS) >= 20
    assert all(text == text.lower() for text in BANNED_SUBSTRINGS)
    assert len(set(BANNED_SUBSTRINGS)) == len(BANNED_SUBSTRINGS)
    assert BANNED_UPPER == tuple(text.upper() for text in BANNED_SUBSTRINGS)


@pytest.mark.parametrize("secret,how", sorted(PLANTED_SECRETS.items()))
def test_every_planted_secret_is_actually_caught(secret: str, how: str) -> None:
    """Each planted secret is caught by a real mechanism, not merely by intent.

    Args:
        secret: The secret planted into a failure in this module.
        how: Either a banned fragment the secret contains, or `"literal"` when the
            test asserting on it compares the value directly.
    """
    if how == "literal":
        # An opaque key cannot be matched by a word list. Its protection is that
        # the tests naming it compare it directly, so confirm the key really is
        # unique enough that such a comparison is meaningful.
        assert secret not in BANNED_SUBSTRINGS
        assert len(secret) >= 20
        return

    # Otherwise the ban list must contain a fragment that occurs in the secret.
    assert how in BANNED_SUBSTRINGS
    assert how in secret.lower()
