"""The fetch service's own behaviour: budgets, redirects, and pinning (Phase 12 §6–§11).

`tests/test_url_guards.py` covers *which destinations* are permitted. This module
covers what happens once a destination is permitted: how many redirects are
followed, how large a body may be, how long it may take, which content types are
read, and — most importantly — that the address which was **validated** is the
address which is **dialled**.

That last point is the reason these tests stub `socket` rather than stubbing the
service. A test that replaced `URLFetchService` with a fake could not detect a
regression where the code resolved a name, checked it, and then connected *by
name* again — which is the entire DNS-rebinding attack. Here the connection layer
is replaced with something that reports which address it was handed, so pinning
is observable rather than assumed.
"""

from __future__ import annotations

import http.client
import socket
import ssl

import pytest

from app.services.url_fetch import URLFetchService, UrlFetchError
from tests.url_factories import (
    PUBLIC_ADDRESS,
    SCAM_PAGE,
    offline_settings,
    stub_resolution,
)

OTHER_PUBLIC_ADDRESS = "93.184.216.35"


class _FakeResponse:
    """A minimal stand-in for `http.client.HTTPResponse`.

    Args:
        status: Status line code.
        headers: Response headers, lowercased keys.
        body: Response body bytes.
    """

    def __init__(
        self,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status = status
        self._headers = headers or {}
        self._body = body
        self._offset = 0

    def getheader(self, name: str) -> str | None:
        """Return a header value, or ``None`` when absent.

        Args:
            name: Header name, matched case-insensitively.
        """
        for key, value in self._headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def read(self, amount: int = -1) -> bytes:
        """Read up to `amount` bytes from the body.

        Args:
            amount: Bytes to read, or ``-1`` for all of them.
        """
        if amount is None or amount < 0:
            chunk = self._body[self._offset :]
            self._offset = len(self._body)
            return chunk
        chunk = self._body[self._offset : self._offset + amount]
        self._offset += len(chunk)
        return chunk


class _RecordingConnectionFactory:
    """Builds fake connections and records the address each was dialled.

    This is the seam that makes pinning testable: a test asserts on
    ``dialed``, which is the address `socket.create_connection` was asked for.

    Attributes:
        responses: One response per connection opened, in order.
        dialed: `(host, port, address)` per connection opened.
    """

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.dialed: list[tuple[str, int, str]] = []

    def __call__(self, address: tuple[str, int], timeout: float | None = None) -> object:
        """Stand in for `socket.create_connection`.

        Args:
            address: `(host, port)` of the pinned destination.
            timeout: Socket timeout, unused.

        Returns:
            A placeholder socket object.
        """
        host, port = address
        self.dialed.append((host, port, host))
        return object()


def _install_connections(
    monkeypatch,
    responses: list[_FakeResponse],
    *,
    connect: object = None,
) -> _RecordingConnectionFactory:
    """Route every connection through a recording factory.

    The fake connection classes still perform the real `connect()` sequence —
    they call `socket.create_connection` with the pinned address — so `dialed`
    reports the address the service would really have dialled. Only the socket
    itself is replaced; the pinning logic around it is the code under test.

    Args:
        monkeypatch: Pytest monkeypatch.
        responses: Responses to serve, one per connection.
        connect: Replacement for `socket.create_connection`. Defaults to the
            recording factory, so that it both records and succeeds.

    Returns:
        The factory, so a test can inspect what was dialled.
    """
    factory = _RecordingConnectionFactory(responses)
    dial = connect if connect is not None else factory
    monkeypatch.setattr(socket, "create_connection", dial)

    class _FakeConnection:
        """Base for the fake HTTP connections."""

        def __init__(self, host, port=None, timeout=None, context=None):
            self.host = host
            self.port = port or (443 if host else 80)
            self.timeout = timeout
            self._context = context
            self._pinned_address = host
            self.sock = None
            self.response: _FakeResponse | None = None
            self.request_headers: dict[str, str] = {}
            self.closed = False

        def connect(self):
            """Dial the pinned address, exactly as the real mixin does."""
            self.sock = socket.create_connection(
                (self._pinned_address, self.port), self.timeout
            )

        def request(self, method, path, headers=None):  # noqa: ANN001, ANN202
            """Record the request and attach the next canned response."""
            if self.sock is None:
                self.connect()
            self.request_headers = headers or {}
            self.response = factory.responses.pop(0) if factory.responses else _FakeResponse()

        def getresponse(self):
            """Return the response the request attached."""
            assert self.response is not None
            return self.response

        def close(self):
            """Mark the connection closed."""
            self.closed = True

    from app.services import url_fetch

    monkeypatch.setattr(url_fetch, "_PinnedHTTPConnection", _FakeConnection)
    monkeypatch.setattr(url_fetch, "_PinnedHTTPSConnection", _FakeConnection)
    return factory


def _ok(body: bytes = SCAM_PAGE, **headers: str) -> _FakeResponse:
    """Build a successful HTML response."""
    base = {"Content-Type": "text/html; charset=utf-8"}
    base.update(headers)
    return _FakeResponse(200, base, body)


class TestPinnedConnections:
    """The address checked is the address dialled."""

    def test_connection_targets_the_validated_address(self, monkeypatch) -> None:
        """A resolved, checked public address is the one connected to.

        This is the DNS-rebinding defence. If the code resolved the name, checked
        the answer, and then opened the socket *by name*, an attacker controlling
        DNS could answer `93.184.216.34` for the check and `127.0.0.1` for the
        connection. Dialing the checked literal removes the window entirely.
        """
        stub_resolution(monkeypatch, (OTHER_PUBLIC_ADDRESS,))
        factory = _install_connections(monkeypatch, [_ok()])

        URLFetchService(offline_settings()).fetch("https://example.com/page")

        assert len(factory.dialed) == 1
        assert factory.dialed[0][2] == OTHER_PUBLIC_ADDRESS
        assert factory.dialed[0][2] != "example.com"

    def test_hostname_is_still_sent_in_the_request(self, monkeypatch) -> None:
        """The `Host` header and SNI carry the real name, not the pinned address.

        Dialing an address must not mean talking to it as though it were the
        address: virtual hosting, and TLS certificate validation, both depend on
        the name the user submitted.
        """
        import app.services.url_fetch as url_fetch

        captured: list[dict[str, str]] = []
        stub_resolution(monkeypatch, (OTHER_PUBLIC_ADDRESS,))
        factory = _install_connections(monkeypatch, [_ok()])

        original = url_fetch._connection_for

        def _spy(scheme, host, port, address, timeout, context):  # noqa: ANN001, ANN202
            captured.append({"host": host, "address": address, "port": str(port)})
            return original(scheme, host, port, address, timeout, context)

        monkeypatch.setattr(url_fetch, "_connection_for", _spy)

        URLFetchService(offline_settings()).fetch("https://example.com:8443/page")

        assert captured[0]["host"] == "example.com"
        assert captured[0]["address"] == OTHER_PUBLIC_ADDRESS
        assert captured[0]["port"] == "8443"
        assert factory.dialed  # a connection really was attempted

    def test_a_host_resolving_inward_is_refused_before_connecting(
        self, monkeypatch
    ) -> None:
        """A name that resolves to loopback never reaches the socket layer."""
        stub_resolution(monkeypatch, ("127.0.0.1",))
        factory = _install_connections(monkeypatch, [_ok()])

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch("https://rebind.example/")

        assert raised.value.code == "URL_ADDRESS_BLOCKED"
        assert factory.dialed == []

    def test_one_internal_answer_refuses_the_whole_host(self, monkeypatch) -> None:
        """A host with one public and one loopback answer is refused.

        Refusing only when *every* answer is internal would leave a host that
        answers with both a public and a private address perfectly usable, and
        which address a connection happens to use would be up to the resolver.
        A host that answers both is a host trying to be both.
        """
        stub_resolution(monkeypatch, (PUBLIC_ADDRESS, "10.0.0.7"))
        factory = _install_connections(monkeypatch, [_ok()])

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch("https://mixed.example/")

        assert raised.value.code == "URL_ADDRESS_BLOCKED"
        assert factory.dialed == []


class TestRedirects:
    """A redirect is re-validated in full, not trusted."""

    def test_redirect_is_followed(self, monkeypatch) -> None:
        """A `302` to a public page is followed and reported."""
        stub_resolution(monkeypatch)
        _install_connections(
            monkeypatch,
            [
                _FakeResponse(302, {"Location": "/final"}, b""),
                _ok(),
            ],
        )

        page = URLFetchService(offline_settings()).fetch("https://example.com/start")

        assert page.redirect_count == 1
        assert page.final_url == "https://example.com/final"

    def test_absolute_redirect_target_is_honoured(self, monkeypatch) -> None:
        """A `Location` may be absolute, and is resolved against the current URL."""
        stub_resolution(monkeypatch)
        _install_connections(
            monkeypatch,
            [
                _FakeResponse(
                    301, {"Location": "https://other.example/dest"}, b""
                ),
                _ok(),
            ],
        )

        page = URLFetchService(offline_settings()).fetch("https://example.com/start")

        assert page.final_url == "https://other.example/dest"

    @pytest.mark.parametrize(
        "target",
        [
            # The dangerous shape — a public host redirecting inward —
            # is refused at the hop rather than only at the start. If
            # redirects were followed without being re-checked, every
            # SSRF control above would be bypassable by one hop.
            #
            # These are all *literal* destinations, so the address policy
            # refuses them before any resolution, and the one shared
            # resolution stub cannot interfere.
            "http://169.254.169.254/latest/",
            "http://127.0.0.1/admin",
            "http://10.0.0.1/secret",
            "http://[::1]/",
        ],
    )
    def test_redirect_to_an_internal_address_is_refused(
        self, monkeypatch, target: str
    ) -> None:
        """A redirect to any non-public literal is refused at the hop.

        Args:
            monkeypatch: Pytest monkeypatch.
            target: Where the public host redirects to.
        """
        stub_resolution(monkeypatch)
        _install_connections(
            monkeypatch,
            [_FakeResponse(302, {"Location": target}, b"")],
        )

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch("https://example.com/start")

        assert raised.value.code in {
            "URL_ADDRESS_BLOCKED",
            "URL_METADATA_ADDRESS_BLOCKED",
        }

    @pytest.mark.parametrize(
        ("name", "expected_code"),
        [
            # A redirect to a *name* is only as safe as what that name
            # resolves to. Real DNS answers `localhost` with `127.0.0.1`,
            # so a redirect there must be refused exactly as a literal
            # loopback is. The resolution stub below is host-aware for
            # this reason: `example.com` must still resolve publicly for
            # the first hop to happen, while the redirected name resolves
            # inward and is refused at the second.
            ("localhost", "URL_ADDRESS_BLOCKED"),
            ("ip-127-0-0-1.sslip.io", "URL_ADDRESS_BLOCKED"),
        ],
    )
    def test_redirect_to_a_name_that_resolves_inward_is_refused(
        self, monkeypatch, name: str, expected_code: str
    ) -> None:
        """A redirect whose target name resolves to a private address is refused.

        This is the rebinding shape applied to redirects: the first hop is a
        public host, the second is a name that answers with an internal
        address. Only the hop's own resolution decides, so the first hop's
        public answer cannot carry the second hop through.

        Args:
            monkeypatch: Pytest monkeypatch.
            name: The redirected-to hostname.
            expected_code: The refusal the redirect hop must produce.
        """
        import socket

        def _host_aware_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            # The original host resolves publicly so the first request is
            # made; the redirected-to name resolves to loopback.
            answers = (PUBLIC_ADDRESS,) if host != name else ("127.0.0.1",)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
                for address in answers
            ]

        monkeypatch.setattr(socket, "getaddrinfo", _host_aware_getaddrinfo)
        _install_connections(
            monkeypatch,
            [_FakeResponse(302, {"Location": f"http://{name}/"}, b""), _ok()],
        )

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch("https://example.com/start")

        assert raised.value.code == expected_code

    def test_redirect_loop_is_bounded(self, monkeypatch) -> None:
        """A chain longer than the configured limit is refused."""
        stub_resolution(monkeypatch)
        settings = offline_settings(url_fetch_max_redirects=2)
        _install_connections(
            monkeypatch,
            [_FakeResponse(302, {"Location": "/next"}, b"") for _ in range(6)],
        )

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(settings).fetch("https://example.com/loop")

        assert raised.value.code == "URL_TOO_MANY_REDIRECTS"

    def test_a_3xx_without_a_location_is_treated_as_an_error(self, monkeypatch) -> None:
        """A redirect with nowhere to go is not a page.

        `200` is the only status treated as a successful retrieval; a bare `302`
        with no `Location` has nothing to fetch and must not be reported as one.
        """
        stub_resolution(monkeypatch)
        _install_connections(monkeypatch, [_FakeResponse(302, {}, b"")])

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch("https://example.com/")

        assert raised.value.code == "URL_HTTP_ERROR"


class TestResponseGates:
    """Only a bounded HTML response becomes a page."""

    @pytest.mark.parametrize("status", [204, 304, 400, 403, 404, 500, 503])
    def test_non_200_status_is_refused(self, monkeypatch, status: int) -> None:
        """Any status other than `200` is a failure, not an empty page.

        `204` and `304` are worth naming: both are "success" to an HTTP client
        and both carry no body, so reporting either as a retrieved page would
        present an empty investigation as a real analysis of a real page.

        Args:
            status: The status code the server returned.
        """
        stub_resolution(monkeypatch)
        _install_connections(monkeypatch, [_FakeResponse(status, {}, b"")])

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch("https://example.com/")

        assert raised.value.code == "URL_HTTP_ERROR"
        assert str(status) in raised.value.message

    @pytest.mark.parametrize(
        "content_type",
        [
            "application/json",
            "application/pdf",
            "image/png",
            "text/plain",
            "application/octet-stream",
            "",
        ],
    )
    def test_non_html_content_type_is_refused(self, monkeypatch, content_type: str) -> None:
        """A URL that does not return a web page is not investigated as one.

        Args:
            content_type: The declared media type.
        """
        stub_resolution(monkeypatch)
        _install_connections(
            monkeypatch, [_FakeResponse(200, {"Content-Type": content_type}, b"data")]
        )

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch("https://example.com/file")

        assert raised.value.code == "URL_CONTENT_TYPE_UNSUPPORTED"

    @pytest.mark.parametrize(
        "content_type",
        [
            "text/html",
            "text/html; charset=utf-8",
            "TEXT/HTML",
            "application/xhtml+xml",
            "application/xhtml+xml;charset=ISO-8859-1",
        ],
    )
    def test_accepted_content_types(self, monkeypatch, content_type: str) -> None:
        """The two HTML media types are accepted, with or without parameters.

        Args:
            content_type: The declared media type.
        """
        stub_resolution(monkeypatch)
        _install_connections(
            monkeypatch, [_FakeResponse(200, {"Content-Type": content_type}, SCAM_PAGE)]
        )

        page = URLFetchService(offline_settings()).fetch("https://example.com/")

        assert page.status == 200

    def test_declared_charset_is_read(self, monkeypatch) -> None:
        """A `charset` parameter is carried through to the extractor."""
        stub_resolution(monkeypatch)
        _install_connections(
            monkeypatch,
            [_FakeResponse(200, {"Content-Type": "text/html; charset=ISO-8859-1"}, b"<p>x</p>")],
        )

        page = URLFetchService(offline_settings()).fetch("https://example.com/")

        assert page.charset == "iso-8859-1"

    def test_oversized_body_is_refused(self, monkeypatch) -> None:
        """A page larger than the whole budget is refused rather than truncated.

        Keeping *nothing* is the honest outcome: a body that exceeded the limit
        before any content arrived is not a page whose first bytes happen to be
        interesting.
        """
        stub_resolution(monkeypatch)
        _install_connections(
            monkeypatch,
            [_ok(body=b"<p>x</p>" * 10_000)],
        )

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings(url_fetch_max_bytes=1_024)).fetch(
                "https://example.com/big"
            )

        assert raised.value.code == "URL_CONTENT_TOO_LARGE"

    def test_body_within_budget_is_kept_whole(self, monkeypatch) -> None:
        """A page inside the budget is returned untruncated."""
        stub_resolution(monkeypatch)
        _install_connections(monkeypatch, [_ok(body=b"<p>small</p>")])

        page = URLFetchService(offline_settings(url_fetch_max_bytes=1_000_000)).fetch(
            "https://example.com/small"
        )

        assert page.body == b"<p>small</p>"
        assert page.truncated is False

    def test_no_compression_is_requested(self, monkeypatch) -> None:
        """Compression is refused rather than negotiated.

        Asking for `identity` means the byte budget bounds what the *server*
        sends. If compressed content were accepted, the budget would bound only
        the decompressed size, and a small download could expand into an
        arbitrarily large response.
        """
        stub_resolution(monkeypatch)

        seen: dict[str, str] = {}
        from app.services import url_fetch

        original = url_fetch._connection_for

        def _spy(scheme, host, port, address, timeout, context):
            conn = original(scheme, host, port, address, timeout, context)
            request = conn.request

            def _request(method, path, headers=None):  # noqa: ANN001, ANN202
                seen.update(headers or {})
                return request(method, path, headers=headers)

            conn.request = _request  # type: ignore[method-assign]
            return conn

        monkeypatch.setattr(url_fetch, "_connection_for", _spy)
        _install_connections(monkeypatch, [_ok()])

        URLFetchService(offline_settings()).fetch("https://example.com/")

        assert seen["Accept-Encoding"] == "identity"
        assert seen["User-Agent"] == "InvestShieldAI/1.0"


class TestFailureClassification:
    """Every failure is typed, and none carries provider or socket detail."""

    def test_dns_failure_is_typed(self, monkeypatch) -> None:
        """A host that does not resolve is a site fault, not a crash."""
        import socket as socket_module

        def _nxdomain(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise socket_module.gaierror(-2, "Name or service not known")

        monkeypatch.setattr(socket_module, "getaddrinfo", _nxdomain)

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch("https://nope.example/")

        assert raised.value.code == "URL_DNS_FAILED"

    def test_timeout_is_typed_and_carries_no_host_detail(self, monkeypatch) -> None:
        """A timeout message says nothing about the socket that produced it."""
        def _timeout(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise socket.timeout("timed out")

        monkeypatch.setattr(socket, "create_connection", _timeout)
        stub_resolution(monkeypatch)

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch("https://slow.example/")

        assert raised.value.code == "URL_TIMEOUT"
        assert "timed out" not in raised.value.message
        assert "slow.example" not in raised.value.message

    def test_tls_failure_is_a_fetch_failure_not_a_crash(self, monkeypatch) -> None:
        """A certificate problem is reported as a retrieval failure.

        The wording must not quote the TLS stack: a certificate error names the
        host and can carry a verification path, neither of which belongs in a
        body a caller may display.
        """
        def _tls_error(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise ssl.SSLError("certificate verify failed: self signed")

        monkeypatch.setattr(socket, "create_connection", _tls_error)
        stub_resolution(monkeypatch)

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch("https://badtls.example/")

        assert raised.value.code == "URL_FETCH_FAILED"
        assert "self signed" not in raised.value.message

    def test_a_second_address_is_tried_after_a_connection_failure(
        self, monkeypatch
    ) -> None:
        """A host with several addresses is not abandoned after the first failure."""
        attempts: list[str] = []

        def _sometimes_fail(address, timeout=None):  # noqa: ANN001, ANN202
            attempts.append(str(address[0]))
            if len(attempts) == 1:
                raise ConnectionRefusedError("refused")
            return object()

        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [
                (2, 1, 6, "", (PUBLIC_ADDRESS, 0)),
                (2, 1, 6, "", ("93.184.216.36", 0)),
            ],
        )
        _install_connections(monkeypatch, [_ok()], connect=_sometimes_fail)

        page = URLFetchService(offline_settings()).fetch("https://example.com/")

        assert len(attempts) == 2
        assert attempts[0] == PUBLIC_ADDRESS
        assert page.status == 200


class TestFetchDisabled:
    """URL analysis can be switched off, and says so."""

    def test_disabled_fetch_refused(self) -> None:
        """A deployment with URL analysis off refuses rather than half-trying."""
        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings(url_fetch_enabled=False)).fetch(
                "https://example.com/"
            )

        assert raised.value.code == "URL_FETCH_DISABLED"

    def test_disabled_fetch_opens_no_socket(self, monkeypatch) -> None:
        """The switch is checked before any connection is attempted."""
        def _explode(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("no socket may be opened when fetching is disabled")

        monkeypatch.setattr(socket, "create_connection", _explode)

        with pytest.raises(UrlFetchError):
            URLFetchService(offline_settings(url_fetch_enabled=False)).fetch(
                "https://example.com/"
            )


class TestSyntaxBeforeNetwork:
    """Nothing is resolved or connected for a syntactically invalid URL."""

    @pytest.mark.parametrize(
        ("raw", "code"),
        [
            ("ftp://example.com", "URL_SCHEME_UNSUPPORTED"),
            ("file:///etc/passwd", "URL_SCHEME_UNSUPPORTED"),
            ("https://", "URL_HOST_MISSING"),
            ("https://user:pw@example.com/", "URL_CREDENTIALS_NOT_ACCEPTED"),
        ],
    )
    def test_invalid_url_refused_without_resolution(
        self, monkeypatch, raw: str, code: str
    ) -> None:
        """Syntax is checked first, so a bad URL never reaches DNS.

        Args:
            raw: The malformed submission.
            code: The expected refusal code.
        """
        def _explode(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("DNS must not be consulted for an invalid URL")

        monkeypatch.setattr(socket, "getaddrinfo", _explode)

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch(raw)

        assert raised.value.code == code


class TestFetchedPageSource:
    """A fetched page renders its own provenance."""

    def test_source_records_what_was_contacted(self, monkeypatch) -> None:
        """Provenance names the submitted URL, the final URL and the addresses.

        This is what makes a website investigation checkable: a reader can see
        which host the analysis came from rather than having to trust that the
        submitted address was the one actually contacted.
        """
        stub_resolution(monkeypatch, (PUBLIC_ADDRESS,))
        _install_connections(
            monkeypatch,
            [_FakeResponse(302, {"Location": "https://www.example.com/final"}, b""), _ok()],
        )

        page = URLFetchService(offline_settings()).fetch("http://example.com/start")
        source = page.source(page_title="Title", meta_description="Desc")

        assert source.submitted_url == "http://example.com/start"
        assert source.normalized_url == "http://example.com/start"
        assert source.final_url == "https://www.example.com/final"
        assert source.hostname == "www.example.com"
        assert source.resolved_addresses == (PUBLIC_ADDRESS,)
        assert source.redirected is True
        assert source.redirect_count == 1
        assert source.is_https is True
        assert source.http_status == 200
        assert source.page_title == "Title"
        assert source.meta_description == "Desc"
        assert source.byte_size == len(page.body)