"""Tests for the SerpAPI provider (Phase 3, §4/§5/§6).

`httpx.Client` is replaced with a client backed by `httpx.MockTransport`, so no
test in this file performs any network I/O. That makes the failure paths that
are otherwise awkward to trigger — timeouts, rate limits, rejected keys — fully
testable without depending on a live account.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from app.core.config import Settings
from app.schemas.search import (
    SEARCH_AUTH_ERROR,
    SEARCH_INVALID_RESPONSE,
    SEARCH_NOT_CONFIGURED,
    SEARCH_RATE_LIMITED,
    SEARCH_SERVICE_ERROR,
    SEARCH_TIMEOUT,
    SearchStatus,
)
from app.services.search.serpapi_provider import SerpAPIProvider, clean_text

SECRET = "serp-test-key-do-not-log-1234567890"

Handler = Callable[[httpx.Request], httpx.Response]

#: Captured before any monkeypatching. The provider module and this test module
#: share the same ``httpx`` module object, so the factory installed below must
#: build clients with the *real* class or it would call itself forever.
_REAL_HTTPX_CLIENT = httpx.Client


def make_settings(**overrides: Any) -> Settings:
    """Build test settings, defaulting to a configured SerpAPI key."""
    defaults: dict[str, Any] = {
        "environment": "test",
        "database_url": "sqlite:///:memory:",
        "groq_api_key": "",
        "serpapi_key": SECRET,
        "serpapi_base_url": "https://serpapi.test/search",
        "log_level": "WARNING",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def json_handler(payload: Any, status: int = 200) -> Handler:
    """Build a handler returning `payload` as JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def status_handler(status: int, body: str = "") -> Handler:
    """Build a handler returning a bare status code."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    return handler


def raising_handler(exc: Exception) -> Handler:
    """Build a handler that raises a transport-level exception."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


class SearchHarness:
    """A configured provider with fully mocked HTTP.

    Attributes:
        provider: The `SerpAPIProvider` under test.
        calls: Every request the provider issued, in order.
    """

    def __init__(self, provider: SerpAPIProvider) -> None:
        self.provider = provider
        self.calls: list[httpx.Request] = []
        self.install: Callable[[Handler], None] = lambda handler: None

    def search(self, query: str = "query", max_results: int = 5):
        """Run a search through the provider."""
        return self.provider.search(query, max_results)

    @property
    def params(self) -> dict[str, str]:
        """Query parameters of the single request that was issued."""
        assert len(self.calls) == 1, f"expected exactly one request, got {len(self.calls)}"
        return dict(self.calls[0].url.params)


@pytest.fixture
def make_harness(monkeypatch: pytest.MonkeyPatch) -> Callable[..., SearchHarness]:
    """Return a factory building harnesses whose HTTP client is mocked.

    Replaces `httpx.Client` inside the provider module with a client backed by
    `httpx.MockTransport`, so no request ever leaves the process.
    """
    state: dict[str, Handler] = {"handler": json_handler({})}

    def factory(*args: Any, **kwargs: Any) -> httpx.Client:
        return _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(state["handler"]))

    monkeypatch.setattr("app.services.search.serpapi_provider.httpx.Client", factory)

    def build(**setting_overrides: Any) -> SearchHarness:
        instance = SearchHarness(SerpAPIProvider(make_settings(**setting_overrides)))

        def install(handler: Handler) -> None:
            instance.calls.clear()

            def wrapped(request: httpx.Request) -> httpx.Response:
                instance.calls.append(request)
                return handler(request)

            state["handler"] = wrapped

        instance.install = install
        return instance

    return build


@pytest.fixture
def harness(make_harness: Callable[..., SearchHarness]) -> SearchHarness:
    """A SerpAPI provider with a configured key."""
    return make_harness()


@pytest.fixture
def unavailable_harness(make_harness: Callable[..., SearchHarness]) -> SearchHarness:
    """A provider configured without an API key."""
    return make_harness(serpapi_key="")


class TestAvailability:
    """No key means no request is ever attempted."""

    def test_configured_provider_is_available(self) -> None:
        assert SerpAPIProvider(make_settings()).is_available() is True

    def test_missing_key_is_unavailable(self) -> None:
        assert SerpAPIProvider(make_settings(serpapi_key="")).is_available() is False

    def test_whitespace_key_counts_as_missing(self) -> None:
        assert SerpAPIProvider(make_settings(serpapi_key="   ")).is_available() is False

    def test_unconfigured_provider_returns_unavailable(self, unavailable_harness: SearchHarness) -> None:
        response = unavailable_harness.search()

        assert response.status is SearchStatus.UNAVAILABLE
        assert response.error_code == SEARCH_NOT_CONFIGURED
        assert response.results == ()
        assert response.provider_query_sent is False
        assert response.provider == "serpapi"


class TestRequestShape:
    """The provider builds SerpAPI's documented request."""

    def test_request_carries_the_documented_parameters(self, harness: SearchHarness) -> None:
        harness.install(json_handler({"organic_results": []}))

        harness.search("SEBI registration", 5)

        params = harness.params
        assert params["q"] == "SEBI registration"
        assert params["engine"] == "google"
        assert params["num"] == "5"
        assert params["api_key"] == SECRET

    def test_engine_is_configurable(self, make_harness: Callable[..., SearchHarness]) -> None:
        harness = make_harness(serpapi_engine="bing")
        harness.install(json_handler({"organic_results": []}))

        harness.search()

        assert harness.params["engine"] == "bing"

    def test_num_is_clamped_to_the_provider_ceiling(self, harness: SearchHarness) -> None:
        harness.install(json_handler({"organic_results": []}))

        harness.search("query", 5000)

        assert int(harness.params["num"]) <= 100

    def test_num_is_at_least_one(self, harness: SearchHarness) -> None:
        harness.install(json_handler({"organic_results": []}))

        harness.search("query", 0)

        assert int(harness.params["num"]) >= 1

    def test_base_url_is_configurable(self, make_harness: Callable[..., SearchHarness]) -> None:
        harness = make_harness(serpapi_base_url="https://custom.test/lookup")
        harness.install(json_handler({"organic_results": []}))

        harness.search()

        assert str(harness.calls[0].url).startswith("https://custom.test/lookup")


class TestSuccessfulResponses:
    """Results are normalized; nothing SerpAPI-shaped escapes."""

    def test_results_are_normalized(self, harness: SearchHarness) -> None:
        harness.install(
            json_handler(
                {
                    "organic_results": [
                        {
                            "position": 1,
                            "title": "SEBI <b>registration</b> lookup",
                            "link": "https://www.sebi.gov.in/x",
                            "snippet": "Check <b>registration</b> status",
                        }
                    ]
                }
            )
        )

        response = harness.search()
        result = response.results[0]

        assert response.status is SearchStatus.OK
        assert response.success is True
        assert response.provider == "serpapi"
        assert result.title == "SEBI registration lookup"
        assert result.snippet == "Check registration status"
        assert result.url == "https://www.sebi.gov.in/x"
        assert result.source_domain == "sebi.gov.in"
        assert result.position == 1

    def test_missing_snippet_is_allowed(self, harness: SearchHarness) -> None:
        harness.install(
            json_handler({"organic_results": [{"title": "t", "link": "https://example.com/a"}]})
        )

        assert harness.search().results[0].snippet is None

    def test_alternative_url_key_is_accepted(self, harness: SearchHarness) -> None:
        harness.install(
            json_handler({"organic_results": [{"title": "t", "url": "https://example.com/a"}]})
        )

        assert harness.search().results[0].url == "https://example.com/a"

    def test_serpapi_shape_does_not_leak(self, harness: SearchHarness) -> None:
        harness.install(
            json_handler(
                {
                    "search_metadata": {"google_results_count": "1234"},
                    "search_parameters": {"engine": "google"},
                    "organic_results": [{"title": "t", "link": "https://example.com/a"}],
                }
            )
        )

        dumped = harness.search().model_dump()

        assert "organic_results" not in dumped
        assert "search_metadata" not in dumped
        assert "search_parameters" not in dumped

    def test_zero_results_is_a_successful_search(self, harness: SearchHarness) -> None:
        harness.install(json_handler({"organic_results": []}))

        response = harness.search()

        assert response.status is SearchStatus.OK
        assert response.success is True
        assert response.results == ()
        assert response.error_code is None

    def test_missing_organic_results_key_is_treated_as_empty(self, harness: SearchHarness) -> None:
        harness.install(json_handler({"search_metadata": {}}))

        response = harness.search()

        assert response.status is SearchStatus.OK
        assert response.results == ()

    def test_result_count_is_capped_by_max_results(self, harness: SearchHarness) -> None:
        harness.install(
            json_handler(
                {
                    "organic_results": [
                        {"title": f"t{i}", "link": f"https://example.com/{i}"}
                        for i in range(20)
                    ]
                }
            )
        )

        assert len(harness.search(max_results=3).results) == 3


class TestMalformedEntries:
    """One bad result must never discard a whole search."""

    @pytest.mark.parametrize(
        "entry",
        [
            "a string, not an object",
            42,
            None,
            ["a", "list"],
            {"title": "no link"},
            {"title": "   ", "link": "https://example.com/a"},
            {"title": "t", "link": "javascript:alert(1)"},
            {"title": "t", "link": 12345},
        ],
    )
    def test_unusable_entries_are_skipped(self, harness: SearchHarness, entry: Any) -> None:
        harness.install(json_handler({"organic_results": [entry]}))

        response = harness.search()

        assert response.status is SearchStatus.OK
        assert response.results == ()
        assert response.warnings

    def test_good_entries_survive_alongside_bad_ones(self, harness: SearchHarness) -> None:
        harness.install(
            json_handler(
                {
                    "organic_results": [
                        "junk",
                        {"title": "good", "link": "https://sebi.gov.in/a"},
                        {"title": "bad", "link": "javascript:alert(1)"},
                        {"title": "also good", "link": "https://example.com/b"},
                    ]
                }
            )
        )

        response = harness.search()

        assert [result.title for result in response.results] == ["good", "also good"]
        assert any("skipped" in warning for warning in response.warnings)

    def test_organic_results_of_wrong_type_is_an_invalid_response(
        self, harness: SearchHarness
    ) -> None:
        harness.install(json_handler({"organic_results": "not a list"}))

        response = harness.search()

        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_INVALID_RESPONSE


class TestFailureModes:
    """Every failure becomes a typed code, never an empty success."""

    def test_timeout(self, harness: SearchHarness) -> None:
        harness.install(raising_handler(httpx.ReadTimeout("timed out")))

        response = harness.search()

        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_TIMEOUT

    def test_transport_error(self, harness: SearchHarness) -> None:
        harness.install(raising_handler(httpx.ConnectError("connection refused")))

        response = harness.search()

        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_SERVICE_ERROR

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_authentication_failure(self, harness: SearchHarness, status_code: int) -> None:
        harness.install(status_handler(status_code))

        response = harness.search()

        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_AUTH_ERROR

    def test_rate_limit(self, harness: SearchHarness) -> None:
        harness.install(status_handler(429))

        response = harness.search()

        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_RATE_LIMITED

    @pytest.mark.parametrize("status_code", [400, 500, 503])
    def test_http_error_status(self, harness: SearchHarness, status_code: int) -> None:
        harness.install(status_handler(status_code))

        response = harness.search()

        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_SERVICE_ERROR

    def test_non_json_body(self, harness: SearchHarness) -> None:
        harness.install(status_handler(200, "<html>not json</html>"))

        response = harness.search()

        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_INVALID_RESPONSE

    def test_json_that_is_not_an_object(self, harness: SearchHarness) -> None:
        harness.install(json_handler([1, 2]))

        response = harness.search()

        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_INVALID_RESPONSE

    def test_provider_error_field_with_http_200(self, harness: SearchHarness) -> None:
        # SerpAPI reports some failures with HTTP 200; a status check alone
        # would read this as a successful empty search.
        harness.install(json_handler({"error": "Invalid API key"}))

        response = harness.search()

        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_AUTH_ERROR

    def test_provider_error_field_that_is_not_auth(self, harness: SearchHarness) -> None:
        harness.install(json_handler({"error": "Google hasn't returned results"}))

        response = harness.search()

        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_SERVICE_ERROR

    def test_failure_never_becomes_an_empty_success(self, harness: SearchHarness) -> None:
        harness.install(status_handler(429))

        response = harness.search()

        assert response.success is False
        assert response.results == ()
        assert response.error_code is not None
        assert response.provider_query_sent is True

    def test_failure_response_carries_a_warning(self, harness: SearchHarness) -> None:
        harness.install(status_handler(500))

        assert harness.search().warnings


class TestNoSecretLeakage:
    """The API key must not appear anywhere a user or log could see it."""

    def test_key_is_absent_from_the_response(self, harness: SearchHarness) -> None:
        harness.install(
            json_handler({"organic_results": [{"title": "t", "link": "https://example.com/a"}]})
        )

        response = harness.search()

        assert SECRET not in response.model_dump_json()
        assert all(SECRET not in warning for warning in response.warnings)

    def test_key_is_absent_from_failure_responses(
        self, harness: SearchHarness, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.clear()
        harness.install(raising_handler(httpx.ConnectError(f"failed to reach {SECRET}")))

        response = harness.search()

        assert SECRET not in response.model_dump_json()
        assert all(SECRET not in warning for warning in response.warnings)
        assert SECRET not in caplog.text

    def test_key_is_absent_from_auth_failure_logs(
        self, harness: SearchHarness, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.clear()
        harness.install(status_handler(401))

        harness.search()

        assert SECRET not in caplog.text

    def test_provider_error_text_is_not_echoed_back(self, harness: SearchHarness) -> None:
        harness.install(json_handler({"error": f"Invalid API key: {SECRET}"}))

        assert SECRET not in harness.search().model_dump_json()

    def test_error_body_is_never_returned(self, harness: SearchHarness) -> None:
        harness.install(status_handler(500, f"key {SECRET} rejected"))

        response = harness.search()

        assert SECRET not in response.model_dump_json()
        assert "Traceback" not in " ".join(response.warnings)


class TestCleanText:
    """Markup left by the provider must not reach a report verbatim."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("plain text", "plain text"),
            ("SEBI <b>registration</b>", "SEBI registration"),
            ("a &amp; b", "a & b"),
            ("  spaced   out  ", "spaced out"),
            ("<b></b>", None),
            ("", None),
        ],
    )
    def test_markup_is_stripped(self, raw: str, expected: str | None) -> None:
        assert clean_text(raw) == expected

    @pytest.mark.parametrize("raw", [None, 42, ["list"], {"a": 1}])
    def test_non_strings_yield_none(self, raw: Any) -> None:
        assert clean_text(raw) is None