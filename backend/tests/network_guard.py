"""Network isolation for the default test suite (Phase 10).

The suite is supposed to be offline. That was, until now, a property of
*discipline*: every settings factory passed `_env_file=None` and blank keys, and
every search provider was a fixture. Discipline is exactly the kind of guarantee
that decays — one new test constructs `Settings()` the ordinary way, the
developer running it has a real `GROQ_API_KEY` or `SERPAPI_KEY` in `.env`, and the
test makes a live call. It will usually still **pass**, because a live provider
returns plausible data. That is precisely the failure this module exists to make
impossible: a test that reaches the network should fail loudly, not quietly
succeed on someone else's API key.

## What is blocked

Outbound socket connections: `socket.socket.connect`, `socket.socket.connect_ex`,
and `socket.create_connection`. DNS resolution goes through
`socket.getaddrinfo`, which is blocked too — a name lookup is network activity
even when nothing is sent, and it is the first half of every credential leak.

## What is not blocked

- **`TestClient`**, because it speaks ASGI in-process and opens no socket. Every
  API test therefore keeps working with no changes.
- **SQLite**, which is a library, not a server.
- **Tests explicitly marked `@pytest.mark.integration`**, which are deselected by
  default anyway. The guard is only installed when a test runs, so a deliberately
  opted-in live test simply never sees it.

## Why an exception rather than a skip

A blocked call raises :class:`NetworkAccessBlocked`. That makes the offending
test fail with the host and port it tried to reach, which is the information
needed to fix it. A warning would be missed; a skip would hide a test that
silently stopped running.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

__all__ = [
    "NetworkAccessBlocked",
    "allow_network",
    "is_integration",
    "no_network",
]


class NetworkAccessBlocked(RuntimeError):
    """Raised when the default suite tries to open a network connection.

    Deliberately a `RuntimeError` subclass rather than a bare `AssertionError` so
    the failure is recognisable even if it escapes a service's own `except`.
    """


#: Hosts a connection to which is allowed while the guard is installed.
#:
#: Empty by default. `allow_network` is the only way to widen it, and nothing in
#: the default suite calls it.
_ALLOWED_TARGETS: set[tuple[str, int | None]] = set()


#: Loopback literals, which the guard lets through.
#:
#: **This exemption is not optional, and it is not a loophole in the guarantee the
#: guard provides.** `TestClient(app)` used as a context manager starts an anyio
#: blocking portal, and the portal needs a socket pair. On POSIX that is an
#: `AF_UNIX` pair; on **Windows**, which has no `socketpair`, CPython emulates it
#: with a real TCP connection to `127.0.0.1` on an OS-assigned ephemeral port. Every
#: API test in this suite uses `with TestClient(app)`, so without this exemption the
#: guard fails roughly a thousand tests on Windows for a reason that has nothing to
#: do with network isolation.
#:
#: The guarantee the guard gives is therefore: **no traffic leaves the machine.**
#: Loopback never does. The threat this guard exists for is a test quietly spending
#: a developer's real `GROQ_API_KEY` or `SERPAPI_KEY` against a provider, and every
#: such destination is off-host.
#:
#: Only literal loopback addresses are exempt, not the name `localhost` — a
#: hostname still has to resolve, and resolution is still blocked, so nothing hides
#: behind it. `tests/test_network_guard.py` pins this boundary: the exemption is
#: asserted in both directions, so it cannot quietly widen to a routable address.
#: An IPv4 literal in `127.0.0.0/8`. Anchored on digits and dots, so a *hostname*
#: that merely begins with `127.` — `127.0.0.1.example.invalid` — does not match.
#: That case is not hypothetical: a prefix test on the string is the obvious way to
#: write this check and it silently exempts any domain an attacker chooses to
#: register under a loopback-looking name.
_LOOPBACK_IPV4 = re.compile(r"^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

#: IPv6 loopback, in the forms a socket address can carry it.
_LOOPBACK_IPV6 = {"::1", "0:0:0:0:0:0:0:1"}


def _is_loopback_literal(host: Any) -> bool:
    """Report whether `host` is a literal loopback address.

    Args:
        host: The host component of an address.

    Returns:
        `True` for `127.0.0.0/8` literals and IPv6 loopback.
    """
    text = str(host)
    if text in _LOOPBACK_IPV6:
        return True
    if _LOOPBACK_IPV4.match(text):
        # Every octet must actually be a number, so `127.999.1.1` is not loopback.
        return all(0 <= int(octet) <= 255 for octet in text.split("."))
    return False


def _is_allowed(address: Any) -> bool:
    """Report whether `address` was explicitly opted in, or is loopback.

    An entry of `(host, None)` is a wildcard for that host and matches any port.
    That matters for `getaddrinfo`, which is handed a concrete port — an
    `allow_network("example.invalid")` call says "this host is fine", and matching
    only on an exact `(host, port)` pair would silently refuse it.

    Args:
        address: Whatever `connect` was given — an `(host, port)` tuple for the
            AF_INET families, or a path for AF_UNIX.

    Returns:
        `True` when the guard should stand aside.
    """
    if not isinstance(address, tuple) or len(address) != 2:
        # AF_UNIX addresses are filesystem paths, not network destinations. Nothing
        # in this project uses one, but letting them through keeps the guard from
        # breaking an unrelated standard-library path rather than failing a test
        # for a reason that has nothing to do with isolation.
        return True
    host, port = address
    if _is_loopback_literal(host):
        return True
    key = (str(host), port)
    if key in _ALLOWED_TARGETS:
        return True
    # A wildcard entry for this host, registered by `allow_network(host)`.
    return (str(host), None) in _ALLOWED_TARGETS


@contextmanager
def allow_network(host: str, port: int | None = None) -> Iterator[None]:
    """Permit connections to one host for the duration of the block.

    Args:
        host: Hostname or address to permit.
        port: Port to permit. `None` permits **any** port on that host, which
            includes the concrete port a `getaddrinfo` call is given.

    Yields:
        `None`, with the guard temporarily widened for this host only.
    """
    target = (str(host), port)
    _ALLOWED_TARGETS.add(target)
    try:
        yield
    finally:
        _ALLOWED_TARGETS.discard(target)


@contextmanager
def no_network() -> Iterator[None]:
    """Block outbound connections for the duration of the block.

    Yields:
        `None`, with the guard installed.
    """
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo

    def _blocked_connect(self: socket.socket, address: Any) -> Any:
        if _is_allowed(address):
            return original_connect(self, address)
        raise NetworkAccessBlocked(
            f"The test suite attempted a network connection to {address!r}. "
            "The default suite must be offline. Inject a fake provider or service "
            "instead of calling a real one. A live test belongs behind "
            "@pytest.mark.integration, which pytest deselects by default."
        )

    def _blocked_connect_ex(self: socket.socket, address: Any) -> Any:
        if _is_allowed(address):
            return original_connect_ex(self, address)
        raise NetworkAccessBlocked(
            f"The test suite attempted a network connection to {address!r}. "
            "See no_network() for why this raises rather than warns."
        )

    def _blocked_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_allowed(address):
            return original_create_connection(address, *args, **kwargs)
        raise NetworkAccessBlocked(
            f"The test suite attempted a network connection to {address!r}. "
            "The default suite must be offline."
        )

    def _blocked_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_allowed((host, port)):
            return original_getaddrinfo(host, port, *args, **kwargs)
        raise NetworkAccessBlocked(
            f"The test suite attempted a DNS lookup for {host!r}. "
            "Name resolution is network activity and leaks the hostname a test "
            "was reaching for even when nothing is sent."
        )

    socket.socket.connect = _blocked_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _blocked_connect_ex  # type: ignore[method-assign]
    socket.create_connection = _blocked_create_connection  # type: ignore[assignment]
    socket.getaddrinfo = _blocked_getaddrinfo  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
        socket.create_connection = original_create_connection  # type: ignore[assignment]
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _offline_by_default(request: pytest.FixtureRequest) -> Iterator[None]:
    """Install the network guard around every test that did not opt out.

    An integration test is deliberately allowed to reach the network, so the guard
    is skipped for it rather than left to fail — otherwise the marker would mean
    "run and immediately fail", which is not opt-in at all.

    **This fixture lives in `conftest.py`, not here, and that placement is load
    bearing.** An `autouse` fixture is only collected from a file pytest treats as a
    plugin or a conftest. This module holds the implementation, but importing it
    does not register its fixtures — so a fixture defined here would be dead code
    while the suite stayed green. `tests/conftest.py` defines the autouse fixture
    and calls `no_network()`; this module stays a plain library.

    Args:
        request: The pytest request, used to read the test's markers.

    Yields:
        `None` for the duration of the test.
    """
    if is_integration(request.node):
        yield
        return

    with no_network():
        yield


def is_integration(node: Any) -> bool:
    """Report whether a pytest node opted out of network isolation.

    A plain function rather than fixture logic, so the decision can be tested
    directly — a pytest fixture may not be called from a test, and a rule that can
    only be exercised by deselecting the test that checks it is a rule nobody
    checks.

    Args:
        node: A pytest node, or anything with `get_closest_marker`.

    Returns:
        `True` when the node is marked `integration`.
    """
    return node.get_closest_marker("integration") is not None
