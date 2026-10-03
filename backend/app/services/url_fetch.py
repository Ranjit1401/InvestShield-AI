"""Safe HTTP fetching for URL investigation (Phase 12 §5–§11).

This is the only module in the project that opens a connection to an address a
user named, so it carries the SSRF policy in full. It sits **below** the graph:
a route calls the graph, the graph's input stage calls this service, and this
service calls the network. No node performs HTTP itself (Phase 7's
one-node-one-service rule), and no route constructs a fetcher.

## The order of checks, and why it is this order

1. **Syntax** — scheme, host, length, no embedded credentials
   (:func:`app.services.url_guards.split_and_validate`). No network.
2. **Name policy** — metadata hostnames refused before resolution.
3. **Resolution** — every address the host resolves to is collected.
4. **Address policy** — *every* resolved address must be globally routable
   (:func:`app.services.url_guards.classify_address`). Refusing when *any* answer
   is internal is deliberate: a host with one public and one loopback answer is
   trying to be both.
5. **Connect**, pinned to a validated address.
6. **Read**, bounded in bytes and in time.
7. **Content type**, checked before the body is interpreted as HTML.

## Pinning, and why it is not optional

Resolving a name and then connecting *by name* leaves a window in which DNS can
answer differently, which is exactly DNS rebinding: the check sees
`93.184.216.34`, the connection reaches `127.0.0.1`. Every connection here
therefore dials an address this module has already validated, and the hostname
is used only for the ``Host`` header and TLS SNI, so certificate validation
still checks the name the user actually submitted.

## Redirects are re-validated from scratch

A redirect is a fresh request to a fresh address and is put through the whole
sequence again, including resolution and address policy. The dangerous shape —
a public host redirecting to `127.0.0.1` — is refused at the hop, not only at
the start. The count is bounded, so a chain cannot be used to exhaust the
request budget either.

## No circumvention

Nothing here tries to defeat a login, a CAPTCHA, a paywall, a robots policy or
any other access control, and there is no code path that could. A site that
refuses a plain fetch produces a typed refusal the caller can read, which is
the honest outcome.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.url import UrlSource
from app.services.url_guards import (
    InvalidUrl,
    SYNTAX_MESSAGES,
    is_blocked_address,
    is_blocked_hostname,
    split_and_validate,
)

logger = get_logger(__name__)

__all__ = [
    "FETCH_MESSAGES",
    "FetchedPage",
    "URLFetchService",
    "URL_MESSAGES",
    "UrlFetchError",
]

#: Wording for every retrieval failure, keyed by code.
#:
#: One message per code, owned here rather than at the raising site, so the same
#: failure always reads the same way. `app.graph.nodes` re-exports this table as
#: part of its error vocabulary, which is what keeps the graph's promise that
#: every code it can record has fixed wording.
#:
#: `{status}` is filled with the site's HTTP status. That is a fact about the
#: submitted page, already visible to whoever submitted it, and it is the one
#: detail that makes a `502` actionable rather than merely accurate.
FETCH_MESSAGES: dict[str, str] = {
    "URL_FETCH_DISABLED": (
        "Website investigation is disabled in this deployment, so the submitted "
        "URL was not retrieved."
    ),
    "URL_DNS_FAILED": (
        "The submitted host could not be resolved."
    ),
    "URL_FETCH_FAILED": (
        "The submitted page could not be retrieved."
    ),
    "URL_TIMEOUT": (
        "The submitted page did not respond within the time allowed."
    ),
    "URL_HTTP_ERROR": (
        "The submitted page returned HTTP {status} and was not investigated."
    ),
    "URL_TOO_MANY_REDIRECTS": (
        "The submitted page redirected more times than this version follows."
    ),
    "URL_CONTENT_TOO_LARGE": (
        "The submitted page is larger than this version will download."
    ),
    "URL_CONTENT_TYPE_UNSUPPORTED": (
        "The submitted URL did not return a web page, so it was not "
        "investigated as a website."
    ),
}

#: Every refusal this module can raise, including the ones raised on behalf of
#: the address policy. Merged rather than separate because the fetch service does
#: re-raise an `AddressPolicy` refusal, and a code whose wording lived in one
#: table while the exception that carries it consults another would be a split
#: waiting to happen.
URL_MESSAGES: dict[str, str] = {**SYNTAX_MESSAGES, **FETCH_MESSAGES}

#: Status codes that are a successful retrieval of a page. A `204` or a `205`
#: carries no body to investigate, so it is treated with the rest of the
#: unsuccessful responses rather than as an empty page.
_OK_STATUS = frozenset({200})


class UrlFetchError(Exception):
    """A fetch that could not be completed.

    Every reason a URL cannot be retrieved is one of these, with a stable code,
    so the graph can record a typed warning or error without inspecting
    exception text. No `message` from a socket, a TLS stack or an HTTP server is
    ever carried: the wording comes from `URL_MESSAGES`, and no call site can
    supply its own.

    Attributes:
        code: Stable machine-readable reason.
        message: Fixed wording safe to return to a caller.
    """

    def __init__(self, code: str, **fields: object) -> None:
        message = URL_MESSAGES[code]
        if fields:
            message = message.format(**fields)
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FetchedPage:
    """A retrieved HTML page.

    Attributes:
        submitted_url: The URL exactly as submitted.
        normalized_url: Its canonical spelling.
        final_url: What was actually fetched, after redirects.
        hostname: Host contacted.
        addresses: Addresses the host resolved to, all of which were checked.
        redirect_count: Redirects followed.
        status: Final HTTP status code.
        content_type: Media type, without parameters.
        charset: Encoding the page declared.
        body: The bytes read, already bounded.
        truncated: Whether the body was cut at the byte limit.
        fetched_at: When the response finished arriving.
    """

    submitted_url: str
    normalized_url: str
    final_url: str
    hostname: str
    addresses: tuple[str, ...]
    redirect_count: int
    status: int
    content_type: str | None
    charset: str | None
    body: bytes
    truncated: bool
    fetched_at: datetime

    def source(self, *, page_title: str | None, meta_description: str | None) -> UrlSource:
        """Build the persisted/returned record for this fetch."""
        scheme = (urlsplit(self.final_url).scheme or "").lower()
        return UrlSource(
            submitted_url=self.submitted_url,
            normalized_url=self.normalized_url,
            final_url=self.final_url,
            hostname=self.hostname,
            resolved_addresses=self.addresses,
            redirected=self.redirect_count > 0,
            redirect_count=self.redirect_count,
            page_title=page_title,
            meta_description=meta_description,
            http_status=self.status,
            content_type=self.content_type,
            is_https=scheme == "https",
            charset=self.charset,
            byte_size=len(self.body),
            fetched_at=self.fetched_at,
        )


# -- pinned connections -----------------------------------------------------


class _PinnedMixin:
    """Dials a pre-validated address instead of resolving the name again."""

    _pinned_address: str

    def _connect_socket(self, port: int) -> socket.socket:
        """Open a TCP connection to the pinned address.

        Args:
            port: Destination port.

        Returns:
            A connected socket.
        """
        return socket.create_connection((self._pinned_address, port), self.timeout)


class _PinnedHTTPConnection(_PinnedMixin, http.client.HTTPConnection):
    """Plain HTTP to a pinned address."""

    def connect(self) -> None:  # noqa: D102 - documented on the mixin
        self.sock = self._connect_socket(self.port)


class _PinnedHTTPSConnection(_PinnedMixin, http.client.HTTPSConnection):
    """HTTPS to a pinned address, still validating the hostname.

    The certificate is checked against `self.host` — the name the user
    submitted — not against the pinned address, so pinning a connection does not
    weaken certificate validation.
    """

    def connect(self) -> None:  # noqa: D102 - documented on the mixin
        raw = self._connect_socket(self.port)
        context = self._context or ssl.create_default_context()
        self.sock = context.wrap_socket(raw, server_hostname=self.host)


def _connection_for(
    scheme: str,
    host: str,
    port: int,
    address: str,
    timeout: float,
    context: ssl.SSLContext | None,
) -> http.client.HTTPConnection:
    """Build a connection that dials `address` while speaking to `host`.

    Args:
        scheme: `http` or `https`.
        host: Hostname for the `Host` header and TLS SNI.
        port: Destination port.
        address: The validated address to dial.
        timeout: Socket timeout in seconds.
        context: TLS context, for HTTPS.

    Returns:
        A connection instance.
    """
    if scheme == "https":
        connection = _PinnedHTTPSConnection(host, port=port, timeout=timeout, context=context)
    else:
        connection = _PinnedHTTPConnection(host, port=port, timeout=timeout)
    connection._pinned_address = address
    return connection


# -- the service ------------------------------------------------------------


@dataclass
class _Resolution:
    """Outcome of resolving and checking one host.

    Attributes:
        addresses: Validated, globally routable addresses.
    """

    addresses: tuple[str, ...]


class URLFetchService:
    """Retrieves an HTML page for investigation, defensively.

    Constructed once per settings so the limits are read from configuration at
    construction rather than passed to every call, and so a test can build a
    service with tiny limits and no real network.

    Attributes:
        settings: The configuration providing schemes, limits and timeouts.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._schemes = tuple(self._settings.url_fetch_scheme_list)
        self._content_types = frozenset(self._settings.url_fetch_content_type_list)

    # -- policy ---------------------------------------------------------

    def _resolve(self, hostname: str) -> _Resolution:
        """Resolve a host and refuse it unless every address is public.

        Args:
            hostname: Normalised hostname.

        Returns:
            The validated addresses.

        Raises:
            UrlFetchError: When the host is refused by name, does not resolve,
                or resolves to an address that is not publicly routable.
        """
        blocked = is_blocked_hostname(hostname)
        if blocked is not None:
            raise UrlFetchError(blocked.code)

        literal = is_blocked_address(hostname)
        if literal is not None:
            raise UrlFetchError(literal.code)

        try:
            infos = socket.getaddrinfo(
                hostname,
                None,
                proto=socket.IPPROTO_TCP,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            logger.info(
                "Hostname did not resolve",
                extra={"hostname": hostname, "errno": exc.errno},
            )
            raise UrlFetchError("URL_DNS_FAILED") from exc

        addresses: list[str] = []
        for info in infos:
            sockaddr = info[4]
            raw = str(sockaddr[0])
            policy = is_blocked_address(raw)
            if policy is not None:
                logger.warning(
                    "Refused a resolved address that is not publicly routable",
                    extra={"hostname": hostname, "reason": policy.code},
                )
                raise UrlFetchError(policy.code)
            if raw not in addresses:
                addresses.append(raw)

        if not addresses:
            raise UrlFetchError("URL_DNS_FAILED")

        return _Resolution(addresses=tuple(addresses))

    # -- retrieval ------------------------------------------------------

    def _read_bounded(
        self,
        response: http.client.HTTPResponse,
        max_bytes: int,
    ) -> tuple[bytes, bool]:
        """Read a response body, refusing to exceed the byte budget.

        The limit is enforced *while* reading rather than after, so an oversized
        page is abandoned before it is fully downloaded.

        Args:
            response: An open response with a body pending.
            max_bytes: Byte budget.

        Returns:
            ``(body, truncated)``.

        Raises:
            UrlFetchError: When the response exceeds the budget outright.
        """
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                if total - len(chunk) == 0:
                    # Nothing usable arrived before the limit; refuse outright.
                    raise UrlFetchError("URL_CONTENT_TOO_LARGE")
                # Some content already fits the budget: keep it and say so.
                return b"".join(chunks), True
            chunks.append(chunk)

        return b"".join(chunks), False

    def fetch(self, raw_url: str) -> FetchedPage:
        """Retrieve a page, refusing anything outside the fetch policy.

        Args:
            raw_url: The submitted URL.

        Returns:
            The retrieved page.

        Raises:
            UrlFetchError: When the URL cannot be fetched for any reason the
                product reports. Every cause carries a stable code.
        """
        settings = self._settings
        if not settings.url_fetch_enabled:
            raise UrlFetchError("URL_FETCH_DISABLED")

        try:
            normalized, _, _ = split_and_validate(
                raw_url,
                allowed_schemes=self._schemes,
                max_length=self._settings_url_max_length(),
            )
        except InvalidUrl as exc:
            raise UrlFetchError(exc.code) from exc

        submitted = raw_url.strip()
        started = time.monotonic()
        deadline = started + settings.url_fetch_total_timeout_seconds
        current = normalized
        redirects = 0

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UrlFetchError("URL_TIMEOUT")

            outcome = self._request_once(current, remaining)

            if outcome.redirect_target is None:
                return outcome.completed_page(
                    submitted_url=submitted,
                    normalized_url=normalized,
                    redirect_count=redirects,
                    fetched_at=datetime.now(timezone.utc),
                )

            redirects += 1
            if redirects > settings.url_fetch_max_redirects:
                raise UrlFetchError("URL_TOO_MANY_REDIRECTS")
            # A redirect target is re-validated in full on the next iteration:
            # new scheme, new host, new resolution, new address policy.
            current = outcome.redirect_target

    def _settings_url_max_length(self) -> int:
        """URL length ceiling, taken from the request schema."""
        from app.schemas.url import MAX_URL_LENGTH

        return MAX_URL_LENGTH

    def _request_once(self, url: str, budget: float) -> "_Outcome":
        """Perform one validated HTTP request.

        Args:
            url: The absolute URL to request.
            budget: Seconds still available for this attempt.

        Returns:
            Either a completed page or a redirect to follow.

        Raises:
            UrlFetchError: When the request cannot be completed.
        """
        settings = self._settings
        # Re-validated in full on every hop: a redirect can change the scheme,
        # the host, or both, so nothing from the previous hop is trusted.
        normalized, scheme, hostname = split_and_validate(
            url, allowed_schemes=self._schemes, max_length=self._settings_url_max_length()
        )
        parts = urlsplit(normalized)
        port = parts.port or (443 if scheme == "https" else 80)

        resolution = self._resolve(hostname)

        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"

        context: ssl.SSLContext | None = None
        if scheme == "https":
            context = ssl.create_default_context()

        timeout = min(
            budget,
            settings.url_fetch_connect_timeout_seconds + settings.url_fetch_read_timeout_seconds,
        )

        last_error: UrlFetchError | None = None
        for address in resolution.addresses:
            connection = _connection_for(
                scheme, hostname, port, address, timeout, context
            )
            try:
                connection.request(
                    "GET",
                    path,
                    headers={
                        "User-Agent": settings.url_fetch_user_agent,
                        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                        "Accept-Encoding": "identity",
                        # Compression is refused rather than negotiated, so the
                        # byte budget bounds what the *server* sends and the
                        # limit cannot be side-stepped by asking for gzip.
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
            except (socket.timeout, TimeoutError) as exc:
                connection.close()
                last_error = UrlFetchError("URL_TIMEOUT")
                logger.info("Timed out fetching a page", extra={"reason": last_error.code})
                continue
            except (ssl.SSLError, ConnectionError, OSError, http.client.HTTPException) as exc:
                connection.close()
                last_error = UrlFetchError("URL_FETCH_FAILED")
                logger.info(
                    "Failed to fetch a page",
                    extra={"reason": type(exc).__name__},
                )
                continue

            try:
                status = response.status
                location = response.getheader("Location")
                content_type_header = response.getheader("Content-Type") or ""
                media_type, _, charset_hint = content_type_header.partition(";")

                if 300 <= status < 400 and location:
                    response.read(0)
                    resolved_target = urljoin(normalized, location.strip())
                    return _Outcome(redirect_target=resolved_target)

                if status not in _OK_STATUS:
                    response.read(0)
                    raise UrlFetchError("URL_HTTP_ERROR", status=status)

                media_type = media_type.strip().lower()
                if media_type not in self._content_types:
                    response.read(0)
                    raise UrlFetchError("URL_CONTENT_TYPE_UNSUPPORTED")

                body, truncated = self._read_bounded(
                    response, settings.url_fetch_max_bytes
                )
            finally:
                connection.close()

            charset = self._charset(charset_hint)
            return _Outcome(
                fetched=FetchedPage(
                    submitted_url=normalized,
                    normalized_url=normalized,
                    final_url=normalized,
                    hostname=hostname,
                    addresses=resolution.addresses,
                    redirect_count=0,
                    status=status,
                    content_type=media_type or None,
                    charset=charset,
                    body=body,
                    truncated=truncated,
                    fetched_at=datetime.now(timezone.utc),
                )
            )

        if last_error is not None:
            raise last_error
        raise UrlFetchError("URL_FETCH_FAILED")

    @staticmethod
    def _charset(declared: str) -> str | None:
        """Read a `charset` out of a Content-Type parameter list.

        Args:
            declared: The text after the media type.

        Returns:
            The declared charset, lowercased, or ``None``.
        """
        for part in declared.split(";"):
            key, _, value = part.strip().partition("=")
            if key.strip().lower() == "charset":
                return value.strip().strip('"').lower() or None
        return None


@dataclass
class _Outcome:
    """Result of one request: either a page, or a redirect to follow.

    Exactly one field is set.

    Attributes:
        fetched: The retrieved page, when the request completed.
        redirect_target: Absolute URL of the next hop, when it did not.
    """

    fetched: FetchedPage | None = None
    redirect_target: str | None = None

    def completed_page(
        self,
        *,
        submitted_url: str,
        normalized_url: str,
        redirect_count: int,
        fetched_at: datetime,
    ) -> FetchedPage:
        """Return the completed page with its submission-level fields filled in.

        The per-hop page knows the URL it requested but not the URL the caller
        submitted; those are different values once a redirect has been followed,
        so they are combined here rather than overwritten in place.
        """
        assert self.fetched is not None
        return FetchedPage(
            submitted_url=submitted_url,
            normalized_url=normalized_url,
            final_url=self.fetched.final_url,
            hostname=self.fetched.hostname,
            addresses=self.fetched.addresses,
            redirect_count=redirect_count,
            status=self.fetched.status,
            content_type=self.fetched.content_type,
            charset=self.fetched.charset,
            body=self.fetched.body,
            truncated=self.fetched.truncated,
            fetched_at=fetched_at,
        )