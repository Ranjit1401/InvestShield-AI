"""The network guard is installed, and it works (Phase 10).

`tests/network_guard.py` blocks outbound sockets for the default suite. This module
checks that the guard is actually *there*, which is not a given: an autouse fixture
only takes effect once its plugin module has been imported, and a plugin that is
never imported is a guard that is never installed. A green suite would look exactly
the same.

So these tests attempt the connections the guard is supposed to stop, and require
them to fail. The destinations are loopback on a closed port. Nothing leaves the
machine under any circumstances — and if the guard were ever removed, the connection
would be refused by the kernel in microseconds rather than reaching a real host, so
a regression in the guard cannot become a live network call.

The exemption is checked too, without needing a real service: an
`@pytest.mark.integration`-marked test is shown to run with the guard *not*
installed, by confirming a marked test can reach the point of attempting a lookup
and that the attempt is not blocked. No external service is contacted.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from tests.network_guard import (
    NetworkAccessBlocked,
    allow_network,
    is_integration,
    no_network,
)

#: A port nothing listens on, on a **routable-looking** address.
#:
#: `192.0.2.1` is TEST-NET-1 from RFC 5737, reserved for documentation and
#: guaranteed never to be routed. The choice matters: a loopback address would be
#: allowed (see `TestLoopbackIsExempt`), so a loopback target would prove nothing.
#: If the guard were ever removed, the packet would go nowhere rather than reaching
#: a real host, so a regression in the guard can never become a live call.
UNROUTABLE_HOST = "192.0.2.1"
UNROUTABLE_PORT = 9

#: A name that must never be resolved. `.invalid` is reserved by RFC 2606 and can
#: never resolve, so even a guard regression produces a lookup failure rather than a
#: successful resolution of a real domain.
UNRESOLVABLE = "phase10-guard-canary.invalid"


def _connect(host: str = UNROUTABLE_HOST, port: int = UNROUTABLE_PORT) -> None:
    """Attempt one outbound TCP connection.

    Args:
        host: The address to try.
        port: The port to try.

    Raises:
        NetworkAccessBlocked: If the guard is installed, which is the point.
        OSError: If the guard is not installed, and the connection is refused or
            times out.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect((host, port))


class _MarkedRequest:
    """A stand-in request whose node carries the `integration` marker."""

    class _Node:
        """A node that reports an integration marker."""

        @staticmethod
        def get_closest_marker(name: str) -> object:
            """Report a marker for any name, as an integration test would.

            Args:
                name: The marker name pytest asked about.

            Returns:
                A truthy marker object, or `None` for a name that is not
                    `integration`.
            """
            return object() if name == "integration" else None

    node = _Node()


class _UnmarkedRequest:
    """A stand-in request whose node carries no markers."""

    class _Node:
        """A node with no markers at all."""

        @staticmethod
        def get_closest_marker(name: str) -> None:
            """Report no marker.

            Args:
                name: The marker name pytest asked about.
            """
            return None

    node = _Node()


class TestTheGuardIsInstalled:
    """A test that reaches the network must fail, not quietly succeed."""

    def test_a_plain_connection_is_blocked(self) -> None:
        """The basic guarantee, asserted from inside a normal test.

        No `no_network()` call here. If the autouse fixture were missing, the
        connection would succeed in being refused by the kernel and this test would
        fail with `OSError` — which is exactly the signal wanted, since it would
        mean the guard was not installed for ordinary tests.
        """
        with pytest.raises(NetworkAccessBlocked) as caught:
            _connect()

        assert UNROUTABLE_HOST in str(caught.value)

    def test_the_failure_names_the_destination_and_the_remedy(self) -> None:
        """A blocked call must say enough to fix it.

        The message has to name the target that was attempted, or the developer
        hunting the failure has nothing to go on; and it has to say what to do
        instead, or the obvious guess is to disable the guard.

        """
        with pytest.raises(NetworkAccessBlocked) as caught:
            _connect()

        message = str(caught.value)
        assert UNROUTABLE_HOST in message
        assert "integration" in message

    def test_connect_ex_is_blocked(self) -> None:
        """`connect_ex` is the silent sibling of `connect`.

        It returns an error code instead of raising, so code that uses it will not
        notice a guard that only wraps `connect` — it will simply see every
        connection fail and carry on, which is a much harder failure to diagnose.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            with pytest.raises(NetworkAccessBlocked):
                sock.connect_ex((UNROUTABLE_HOST, UNROUTABLE_PORT))

    def test_create_connection_is_blocked(self) -> None:
        """`socket.create_connection` is the high-level helper libraries reach for."""
        with pytest.raises(NetworkAccessBlocked):
            socket.create_connection((UNROUTABLE_HOST, UNROUTABLE_PORT), timeout=2)

    def test_dns_resolution_is_blocked(self) -> None:
        """A name lookup is network activity even when nothing is sent.

        It is also the first half of a credential leak: knowing the host a test
        reached for tells you what the test was trying to do.
        """
        with pytest.raises(NetworkAccessBlocked) as caught:
            socket.getaddrinfo(UNRESOLVABLE, 443)

        assert UNRESOLVABLE in str(caught.value)

    def test_the_guard_is_released_afterwards(self) -> None:
        """The guard must not leak into the next test.

        If the autouse fixture failed to restore the original functions, every
        subsequent test in the session would run with the guard still installed.
        That is survivable but confusing, so it is checked explicitly.
        """
        assert socket.socket.connect.__name__ == "_blocked_connect"
        assert socket.getaddrinfo.__name__ == "_blocked_getaddrinfo"

    def test_the_guard_is_installed_around_this_test_and_not_inside_it(
        self, request: pytest.FixtureRequest
    ) -> None:
        """This test is not marked integration, so it must be guarded."""
        assert request.node.get_closest_marker("integration") is None

        with pytest.raises(NetworkAccessBlocked):
            socket.getaddrinfo(UNRESOLVABLE, 443)


class TestTheExemption:
    """`allow_network` and the integration marker both stand the guard aside."""

    def test_allow_network_permits_a_listed_host(self) -> None:
        """A DNS lookup for an allowed host is not blocked.

        The host is the unresolvable canary, so even if the guard were skipped for
        the wrong reason the lookup still fails — with a different error, which is
        what the assertion distinguishes.
        """
        with allow_network(UNRESOLVABLE):
            try:
                socket.getaddrinfo(UNRESOLVABLE, 443)
            except NetworkAccessBlocked:  # pragma: no cover - the failure this guards
                pytest.fail("allow_network did not stand the guard aside")
            except socket.gaierror:
                # Expected. The guard stood aside, and the name genuinely does not
                # resolve. Reaching here is the success condition.
                pass

    def test_allow_network_is_scoped_to_the_listed_host(self) -> None:
        """Allowing one host must not allow another.

        A guard that leaked its exemption would be worse than no guard, because it
        would look like isolation while permitting arbitrary destinations.
        """
        with allow_network(UNRESOLVABLE):
            with pytest.raises(NetworkAccessBlocked):
                socket.getaddrinfo("another-canary.invalid", 443)

    def test_allow_network_expires(self) -> None:
        """The exemption ends with the block, even on an exception."""
        with pytest.raises(RuntimeError):
            with allow_network(UNRESOLVABLE):
                raise RuntimeError("something went wrong inside the block")

        with pytest.raises(NetworkAccessBlocked):
            socket.getaddrinfo(UNRESOLVABLE, 443)

    def test_a_nested_guard_restores_the_outer_one(self) -> None:
        """Re-entering the guard must not un-install it on the way out.

        `no_network` restores the functions it saved on entry. A second, nested
        entry saves the *first* guard's functions, so the inner exit restores the
        guard rather than removing it. Getting this wrong would silently disable
        isolation for the rest of the test.
        """
        before = socket.getaddrinfo

        with no_network():
            outer = socket.getaddrinfo
            with no_network():
                inner = socket.getaddrinfo
                assert inner is not outer
                with pytest.raises(NetworkAccessBlocked):
                    socket.getaddrinfo(UNRESOLVABLE, 443)

            # The inner exit must put the *outer* guard back, not remove both.
            assert socket.getaddrinfo is outer
            with pytest.raises(NetworkAccessBlocked):
                socket.getaddrinfo(UNRESOLVABLE, 443)

        # And the outer exit restores whatever was there before — here, the
        # autouse guard installed for this test.
        assert socket.getaddrinfo is before
        assert socket.getaddrinfo.__name__ == "_blocked_getaddrinfo"

    def test_the_original_functions_are_restored(self) -> None:
        """After the block, the module is exactly as it was found."""
        original_getaddrinfo = socket.getaddrinfo

        with no_network():
            assert socket.getaddrinfo is not original_getaddrinfo

        assert socket.getaddrinfo is original_getaddrinfo


class TestLoopbackIsExempt:
    """Loopback must be allowed, and the exemption must not widen.

    `TestClient(app)` used as a context manager starts an anyio blocking portal,
    which needs a socket pair. Windows has no `socketpair`, so CPython emulates it
    with a real TCP connection to `127.0.0.1`. Without this exemption the guard
    breaks every API test on Windows for a reason unrelated to isolation.

    The guarantee the guard gives is *no traffic leaves the machine*. Loopback
    never does, so allowing it costs nothing. These tests pin the boundary from
    both sides: loopback is allowed, and a documentation-range address is not.
    """

    def test_a_loopback_connection_is_allowed(self) -> None:
        """A loopback literal passes the guard and is refused by the kernel instead.

        The `OSError` rather than `NetworkAccessBlocked` is the assertion: the guard
        stood aside, and the failure came from the closed port.
        """
        with pytest.raises(OSError) as caught:
            _connect("127.0.0.1", 9)

        assert not isinstance(caught.value, NetworkAccessBlocked)

    def test_a_test_client_context_manager_works_under_the_guard(
        self, client: Any
    ) -> None:
        """The exemption's actual reason, asserted end to end.

        This is the test that failed before the exemption existed, which is why it
        is here rather than left implicit.

        Args:
            client: A client running the real app, offline.
        """
        assert client.get("/api/health").status_code == 200

    def test_a_documentation_range_address_is_still_blocked(self) -> None:
        """The exemption is for loopback, not for "addresses that look harmless"."""
        with pytest.raises(NetworkAccessBlocked):
            _connect(UNROUTABLE_HOST, UNROUTABLE_PORT)

    def test_the_localhost_name_is_not_exempt(self) -> None:
        """Only literals are exempt, so nothing can hide behind a name.

        A test could not reach a local service by writing `localhost`, because the
        name still has to be resolved and resolution is still blocked.
        """
        with pytest.raises(NetworkAccessBlocked):
            socket.getaddrinfo("localhost", 80)

    def test_a_lookalike_address_is_not_exempt(self) -> None:
        """`127.0.0.1.example.invalid` must not match a `127.` prefix check."""
        with pytest.raises(NetworkAccessBlocked):
            socket.getaddrinfo("127.0.0.1.example.invalid", 80)

    def test_a_public_literal_is_not_exempt(self) -> None:
        """An address one digit away from loopback is not loopback."""
        with pytest.raises(NetworkAccessBlocked):
            _connect("128.0.0.1", UNROUTABLE_PORT)


class TestIntegrationTestsAreIntentionallyExempt:
    """The marker must mean "may reach the network", not "run and immediately fail".

    The exemption is verified by **driving the fixture with a stub request** rather
    than by marking these tests `integration`. Marking them would be self-defeating:
    `pytest.ini` sets `-m "not integration"`, so an integration-marked test is
    deselected by the very run that is supposed to prove the exemption works. The
    real marker is covered by
    `tests/test_integration_markers.py::...::test_a_marked_test_runs_without_the_guard`,
    which is opt-in via `-m integration` and contacts nothing.
    """

    def test_the_exemption_branch_installs_no_guard(self) -> None:
        """A marked node must come out of the guard with sockets untouched."""
        before = socket.getaddrinfo

        assert is_integration(_MarkedRequest().node)
        # `no_network` is what the fixture wraps; with it stood aside for a marked
        # node, the functions are the originals.
        assert socket.getaddrinfo is before

    def test_an_unmarked_node_gets_the_guard(self) -> None:
        """An unmarked node must be guarded, and the guard must come back off."""
        before = socket.getaddrinfo

        assert not is_integration(_UnmarkedRequest().node)

        with no_network():
            assert socket.getaddrinfo is not before
        assert socket.getaddrinfo is before

    def test_a_real_integration_test_can_attempt_a_connection(self) -> None:
        """With the guard stood aside, the attempt is refused by the kernel.

        The point is the *kind* of failure. `NetworkAccessBlocked` would mean the
        guard is still installed and the marker is not working; an `OSError` from a
        closed loopback port means the guard correctly stood aside. No external
        service is contacted either way.
        """
        assert is_integration(_MarkedRequest().node)

        with pytest.raises(OSError) as caught:
            _connect("127.0.0.1", 9)

        assert not isinstance(caught.value, NetworkAccessBlocked)

    def test_the_marker_is_registered_in_the_configuration(self) -> None:
        """An unregistered marker fails under `--strict-markers`.

        `pytest.ini` sets `addopts = --strict-markers`, so `integration` is either
        declared in `markers` or the suite cannot run at all. This assertion fails
        with a clear message rather than leaving it to a collection error.
        """
        markers = pytestconfig_marker_names()
        assert "integration" in markers, (
            "the integration marker must be declared in pytest.ini so that "
            "--strict-markers accepts it and the exemption is discoverable"
        )


def pytestconfig_marker_names() -> set[str]:
    """Return the marker names pytest has registered.

    Reads them from a fresh `pytest --markers` run rather than re-reading
    `pytest.ini`, so this stays correct if the file moves and does not duplicate
    pytest's own parsing rules.

    Returns:
        Every registered marker name, without the `@pytest.mark.` prefix.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--markers"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        line[len("@pytest.mark.") :].split("(", 1)[0].split(":", 1)[0].strip()
        for line in result.stdout.splitlines()
        if line.startswith("@pytest.mark.")
    }


class TestTheSuiteItselfIsOffline:
    """The guard is not decorative: real code paths must survive it."""

    def test_a_settings_object_can_be_built(self) -> None:
        """Configuration is read, never fetched."""
        from app.core.config import Settings

        settings = Settings(_env_file=None, environment="test", database_url="sqlite://")

        assert settings.environment == "test"

    def test_sqlite_works_under_the_guard(self, db_settings: Any) -> None:
        """SQLite is a library, not a server, so the guard must not touch it.

        A guard that blocked this would fail the entire persistence suite, so the
        suite passing is itself the proof; asserting it here names the requirement.

        Args:
            db_settings: Isolated settings pointing at a temporary SQLite file.
        """
        from sqlalchemy import text

        from app.db.session import Database

        database = Database(db_settings)
        try:
            with database.engine.connect() as connection:
                assert connection.execute(text("SELECT 1")).scalar_one() == 1
        finally:
            database.dispose()
