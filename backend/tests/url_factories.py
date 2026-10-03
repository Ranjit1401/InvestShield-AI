"""Fakes and builders for the URL investigation tests (Phase 12).

The suite must never open a socket, and `conftest.py`'s autouse guard enforces
that. What makes testing the fetch *policy* possible anyway is the seam this
module provides:

- :class:`FakeFetchService` replaces the network entirely, standing in for
  `URLFetchService` and producing either a canned page or a canned typed refusal.
- :func:`stub_resolution` replaces **only** `getaddrinfo`, so
  `URLFetchService` itself — its scheme checks, its redirect loop, its byte and
  timeout budgets, its content-type gate — is exercised for real. The connection
  layer is stubbed too, so a passing test still means the logic is right rather
  than that the machine has a network.

That split is deliberate. A test suite that only ever exercises a fake fetcher
proves that the graph calls its dependency; it cannot catch a reversed redirect
loop or an off-by-one in the byte budget. Only the *socket* is faked here, never
the policy.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from app.core.config import Settings
from app.graph.context import GraphContext, GraphDependencies
from app.schemas.url import UrlSource
from app.services.url_fetch import FetchedPage, UrlFetchError
from app.services.website_extractor import WebsiteContentExtractor

#: A fixed instant, matching the graph suite's, so page timestamps are stable.
FIXED_INSTANT = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: A public address. Anything in this space is globally routable, so a test using
#: it is exercising the accept path rather than tripping the SSRF policy.
PUBLIC_ADDRESS = "93.184.216.34"

#: A small, realistic page carrying several of the patterns Phase 1 already
#: detects, so a URL investigation can be asserted to reach the *existing* rules
#: rather than a URL-specific duplicate of them.
#:
#: Written to look like something these pages actually say rather than to satisfy
#: the rules conveniently: the WhatsApp and investment wording sit in one
#: sentence, because `WHATSAPP_INVESTMENT_GROUP` deliberately requires both and
#: a fixture that put them 200 characters apart would test the fixture.
SCAM_PAGE = b"""<html><head>
<title>Guaranteed 40% Monthly Returns - Invest With Us</title>
<meta name="description" content="Risk free investment. Capital guaranteed.">
</head><body>
<h1>Invest with us today</h1>
<p>We are a SEBI registered investment advisor.</p>
<p>We guarantee 40% monthly returns with no risk and no loss.</p>
<p>Our office is in Dubai and we are not regulated in India.</p>
<p>Join our WhatsApp investment group for daily trading signals.</p>
<p>Minimum investment is 25,000. No risk, no loss, capital guaranteed.</p>
<a href="https://t.me/quickreturns">Join our Telegram</a>
</body></html>"""

#: A page whose visible text is built by JavaScript, so a plain fetch sees none.
JS_ONLY_PAGE = b"""<html><body><div id="root"></div>
<script>window.app.render()</script></body></html>"""


def build_page(
    *,
    url: str = "https://example.com/invest",
    body: bytes = SCAM_PAGE,
    hostname: str = "example.com",
    status: int = 200,
    content_type: str | None = "text/html",
    charset: str | None = "utf-8",
    truncated: bool = False,
    redirect_count: int = 0,
    final_url: str | None = None,
    addresses: tuple[str, ...] = (PUBLIC_ADDRESS,),
) -> FetchedPage:
    """Build a `FetchedPage` as a successful retrieval would produce it.

    Args:
        url: The URL that was fetched.
        body: The response body.
        hostname: Host contacted.
        status: HTTP status returned.
        content_type: Media type of the response.
        charset: Encoding the response declared.
        truncated: Whether the body was cut at the byte budget.
        redirect_count: Redirects followed to reach this page.
        final_url: What was finally fetched. Defaults to `url`.
        addresses: Addresses the host resolved to.

    Returns:
        A populated `FetchedPage`.
    """
    return FetchedPage(
        submitted_url=url,
        normalized_url=url,
        final_url=final_url or url,
        hostname=hostname,
        addresses=addresses,
        redirect_count=redirect_count,
        status=status,
        content_type=content_type,
        charset=charset,
        body=body,
        truncated=truncated,
        fetched_at=FIXED_INSTANT,
    )


def build_source(**overrides: object) -> UrlSource:
    """Build a `UrlSource` for persistence and response tests.

    Args:
        overrides: Any field to replace.

    Returns:
        A `UrlSource` with test-friendly defaults.
    """
    defaults: dict[str, object] = {
        "submitted_url": "https://example.com/invest",
        "normalized_url": "https://example.com/invest",
        "final_url": "https://example.com/invest",
        "hostname": "example.com",
        "resolved_addresses": (PUBLIC_ADDRESS,),
        "redirected": False,
        "redirect_count": 0,
        "page_title": "Guaranteed 40% Monthly Returns",
        "meta_description": None,
        "http_status": 200,
        "content_type": "text/html",
        "is_https": True,
        "charset": "utf-8",
        "byte_size": 512,
        "fetched_at": FIXED_INSTANT,
    }
    defaults.update(overrides)
    return UrlSource(**defaults)  # type: ignore[arg-type]


class FakeFetchService:
    """Stands in for `URLFetchService`, producing a canned outcome.

    Attributes:
        calls: Every URL the graph asked for, in order.
    """

    def __init__(
        self,
        page: FetchedPage | None = None,
        *,
        error: UrlFetchError | None = None,
    ) -> None:
        self._page = page
        self._error = error
        self.calls: list[str] = []

    def fetch(self, raw_url: str) -> FetchedPage:
        """Return the canned page, or raise the canned refusal.

        Args:
            raw_url: The URL the graph submitted.

        Returns:
            The canned `FetchedPage`.

        Raises:
            UrlFetchError: The refusal this fake was built with, if any.
        """
        self.calls.append(raw_url)
        if self._error is not None:
            raise self._error
        assert self._page is not None
        return self._page


def offline_settings(**overrides: object) -> Settings:
    """Keyless settings that cannot reach the network.

    Args:
        overrides: Any settings field to replace.

    Returns:
        Settings with no credentials.
    """
    defaults: dict[str, object] = {
        "environment": "test",
        "groq_api_key": "",
        "serpapi_key": "",
        "log_level": "WARNING",
        "database_url": "sqlite:///:memory:",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def url_dependencies(
    base: GraphDependencies,
    fetcher: object,
    settings: Settings | None = None,
) -> GraphDependencies:
    """Replace only the URL pair on an existing dependency set.

    The Phase 1-6 services are left exactly as they were, which is what lets a
    test assert that a URL submission produces a *real* investigation through the
    *real* rules rather than a stub's opinion of one.

    Args:
        base: Dependencies to start from.
        fetcher: The stand-in for `URLFetchService`.
        settings: Settings for the extractor. Defaults to offline settings.

    Returns:
        New dependencies with the URL pair installed.
    """
    resolved = settings or offline_settings()
    return dataclasses.replace(
        base,
        url_fetch_service=fetcher,  # type: ignore[arg-type]
        website_extractor=WebsiteContentExtractor(resolved),
    )


def url_context(
    base: GraphDependencies,
    fetcher: object,
    settings: Settings | None = None,
) -> GraphContext:
    """Build a graph context with the URL pair replaced.

    Args:
        base: Dependencies to start from.
        fetcher: The stand-in for `URLFetchService`.
        settings: Settings for the extractor.

    Returns:
        A `GraphContext` ready to run a URL investigation.
    """
    from tests.graph.graph_factories import fixed_clock

    return GraphContext(
        dependencies=url_dependencies(base, fetcher, settings),
        clock=fixed_clock,
    )


def stub_resolution(monkeypatch, answers: tuple[str, ...] = (PUBLIC_ADDRESS,)) -> None:
    """Make name resolution answer `answers` instead of consulting DNS.

    `URLFetchService` itself still runs, so the policy it applies to the returned
    addresses is exercised for real — this is the substitution that lets a test
    assert a host resolving inward is refused, rather than merely asserting that a
    fake refused it.

    Args:
        monkeypatch: Pytest monkeypatch.
        answers: Addresses to return. Each must be spelled as a string.
    """
    import socket

    def _fake_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
            for address in answers
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


__all__ = [
    "FIXED_INSTANT",
    "JS_ONLY_PAGE",
    "PUBLIC_ADDRESS",
    "SCAM_PAGE",
    "FakeFetchService",
    "build_page",
    "build_source",
    "offline_settings",
    "stub_resolution",
    "url_context",
    "url_dependencies",
]