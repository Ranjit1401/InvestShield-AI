"""The URL destination policy: what may be connected to (Phase 12 §5).

These tests are the security core of Phase 12, so they are written to be
readable as an argument rather than as coverage. Each block states the shape of
attack it refuses and the reason that shape is closed.

Two properties are worth more than any individual address:

- The rule is an **allowlist**. An address is fetchable only if the standard
  library says it is globally routable, so a range nobody thought of is refused
  by default rather than permitted by omission.
- **Obfuscation is closed.** Decimal, octal and hexadecimal IPv4 spellings, and
  IPv6 forms that wrap a private IPv4 address, are all resolved to the address
  they really mean and checked on their own. A string that a resolver would
  accept and this policy would not is exactly the hole that gets found.
"""

from __future__ import annotations

import ipaddress

import pytest

from app.services.url_guards import (
    InvalidUrl,
    classify_address,
    is_blocked_address,
    is_blocked_hostname,
    looks_like_obfuscated_ipv4,
    normalize_hostname,
    split_and_validate,
)

#: Addresses that must never be contacted, with the reason each is closed.
#:
#: Grouped by *shape* rather than listed flat, because the shapes are what recur
#: in real submissions and what a future range would resemble.
BLOCKED_ADDRESSES: tuple[tuple[str, str], ...] = (
    # -- the obvious ones ---------------------------------------------------
    ("127.0.0.1", "loopback"),
    ("127.255.255.254", "loopback, the whole /8"),
    ("0.0.0.0", "unspecified, means 'this host'"),
    ("255.255.255.255", "reserved broadcast"),
    # -- RFC 1918 and its neighbours ---------------------------------------
    ("10.0.0.5", "RFC 1918 private"),
    ("172.16.0.1", "RFC 1918 private"),
    ("192.168.1.1", "RFC 1918 private"),
    ("169.254.1.1", "link-local"),
    # -- cloud metadata -----------------------------------------------------
    ("169.254.169.254", "AWS/Azure/GCP instance metadata"),
    ("169.254.170.2", "ECS task metadata"),
    ("100.100.100.200", "Alibaba instance metadata, in CGNAT space"),
    ("fd00:ec2::254", "AWS IPv6 instance metadata"),
    # -- carrier NAT, neither public nor RFC 1918 --------------------------
    ("100.64.0.1", "CGNAT, used for internal provider routing"),
    # -- IPv6 internal shapes ----------------------------------------------
    ("::1", "IPv6 loopback"),
    ("fc00::1", "IPv6 unique-local"),
    ("fd12:3456::1", "IPv6 unique-local"),
    ("fe80::1", "IPv6 link-local"),
    # -- IPv6 wrappers around a private IPv4 -------------------------------
    ("::ffff:127.0.0.1", "IPv4-mapped loopback"),
    ("::ffff:10.0.0.1", "IPv4-mapped RFC 1918 private"),
    ("2002:7f00:1::", "6to4 wrapping loopback"),
    ("2002:a00:1::", "6to4 wrapping RFC 1918 private"),
    # -- obfuscated IPv4 spellings -----------------------------------------
    ("2130706433", "decimal form of 127.0.0.1"),
    ("0177.0.0.1", "octal form of 127.0.0.1"),
    ("0x7f000001", "hexadecimal form of 127.0.0.1"),
    ("0x7f.0.0.1", "mixed hexadecimal form of 127.0.0.1"),
)

#: Addresses that must be permitted, so the allowlist is not simply refusing
#: everything and passing the refusal tests.
PUBLIC_ADDRESSES: tuple[tuple[str, str], ...] = (
    ("93.184.216.34", "an ordinary public IPv4 address"),
    ("8.8.8.8", "a public IPv4 address"),
    ("1.1.1.1", "a public IPv4 address"),
    ("2606:2800:220:1:248:1893:25c8:1946", "a public IPv6 address"),
    ("2001:4860:4860::8888", "a public IPv6 address"),
)


class TestBlockedAddresses:
    """Every non-public destination is refused."""

    @pytest.mark.parametrize(("address", "reason"), BLOCKED_ADDRESSES)
    def test_address_is_refused(self, address: str, reason: str) -> None:
        """A non-public or obfuscated address never yields an acceptable policy.

        Args:
            address: The literal a caller could submit.
            reason: Why it must be refused, for the reader.
        """
        assert is_blocked_address(address) is not None, (
            f"{address} ({reason}) was allowed"
        )

    def test_refusal_carries_a_stable_code(self) -> None:
        """Every address refusal uses one of two stable codes.

        Split by whether the destination is *infrastructure* rather than by
        address family. `URL_ADDRESS_BLOCKED` covers every non-public address, and
        `URL_METADATA_ADDRESS_BLOCKED` names the one category worth naming: a
        submission that reaches an instance-metadata service is a meaningfully
        different thing from one that reaches any other private address.
        """
        codes = {is_blocked_address(address).code for address, _ in BLOCKED_ADDRESSES}
        assert codes <= {"URL_ADDRESS_BLOCKED", "URL_METADATA_ADDRESS_BLOCKED"}

    def test_only_metadata_endpoints_use_the_metadata_code(self) -> None:
        """The distinction is drawn where it is drawn in the policy."""
        assert is_blocked_address("169.254.169.254").code == "URL_METADATA_ADDRESS_BLOCKED"
        assert is_blocked_address("10.0.0.5").code == "URL_ADDRESS_BLOCKED"
        assert is_blocked_address("127.0.0.1").code == "URL_ADDRESS_BLOCKED"

    def test_refusal_message_names_the_category_not_the_host(self) -> None:
        """The wording describes the category refused, not the target."""
        message = is_blocked_address("10.0.0.5").message
        assert "10.0.0.5" not in message
        assert "not a public internet address" in message

    def test_metadata_address_message_differs_from_the_general_one(self) -> None:
        """A metadata endpoint is named as such, since it is worth knowing.

        This is the one case where the specific category helps a reader: the
        submitted page was an instance-metadata service, not merely something
        private. It names no address and no provider secret.
        """
        message = is_blocked_address("169.254.169.254").message
        assert "metadata" in message.lower()


class TestPublicAddresses:
    """Public destinations are permitted, so the allowlist is not vacuous."""

    @pytest.mark.parametrize(("address", "reason"), PUBLIC_ADDRESSES)
    def test_address_is_allowed(self, address: str, reason: str) -> None:
        """A globally routable address is accepted.

        Args:
            address: The public literal.
            reason: What it is, for the reader.
        """
        assert is_blocked_address(address) is None, (
            f"{address} ({reason}) was wrongly refused"
        )

    def test_non_address_is_not_treated_as_blocked(self) -> None:
        """A hostname is not an address, so it passes to name resolution.

        Refusing every non-literal here would make `is_blocked_address` answer a
        question about names as well as addresses, and the fetch service would
        then have two policies to choose between.
        """
        assert is_blocked_address("example.com") is None


class TestMetadataHostnames:
    """Metadata services are refused by name, before any resolution."""

    def test_metadata_hostname_refused(self) -> None:
        """A name that exists to hand out credentials never resolves at all."""
        assert is_blocked_hostname("metadata.google.internal") is not None

    def test_ordinary_hostname_allowed(self) -> None:
        """An ordinary name is left to resolution and the address policy."""
        assert is_blocked_hostname("example.com") is None

    def test_name_refusal_precedes_resolution(self, monkeypatch) -> None:
        """Refusing by name means DNS is never consulted for it.

        Refusing only after resolution would let a rebinding record present the
        metadata service as an apparently-public address, which is the whole
        reason this list exists.
        """
        import socket

        from app.services.url_fetch import URLFetchService, UrlFetchError
        from tests.url_factories import offline_settings

        def _explode(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("DNS must not be consulted for a metadata host")

        monkeypatch.setattr(socket, "getaddrinfo", _explode)

        with pytest.raises(UrlFetchError) as raised:
            URLFetchService(offline_settings()).fetch(
                "http://metadata.google.internal/"
            )
        assert raised.value.code == "URL_METADATA_HOSTNAME_BLOCKED"


class TestEmbeddedAddresses:
    """A wrapped address is judged by what it really reaches."""

    @pytest.mark.parametrize(
        "wrapper",
        ["::ffff:10.0.0.1", "2002:0a00:0001::"],
    )
    def test_wrapper_inherits_the_wrapped_verdict(self, wrapper: str) -> None:
        """An IPv6 address embedding a private IPv4 address is not public.

        Args:
            wrapper: The IPv6 spelling being tested.
        """
        assert is_blocked_address(wrapper) is not None

    def test_public_v4_in_v6_is_still_public(self) -> None:
        """Wrapping a public address does not invent a restriction."""
        assert is_blocked_address("::ffff:93.184.216.34") is None


class TestHostnames:
    """Hostname normalisation happens once, so every comparison agrees."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("EXAMPLE.COM", "example.com"),
            ("example.com.", "example.com"),
            ("  example.com  ", "example.com"),
            ("[2606:2800::1]", "2606:2800::1"),
            ("fe80::1%25eth0", "fe80::1"),
            (None, ""),
            ("", ""),
        ],
    )
    def test_normalisation(self, raw: str | None, expected: str) -> None:
        """Spelling differences do not create different destinations.

        A trailing dot, an upper-case label and an IPv6 zone identifier all
        denote the same host to a resolver, so they must denote the same host to
        the policy.

        Args:
            raw: The hostname as parsed from a URL.
            expected: Its canonical spelling.
        """
        assert normalize_hostname(raw) == expected

    def test_zone_identifier_still_resolves_to_a_blocked_address(self) -> None:
        """Stripping a zone identifier must not make a link-local address usable."""
        stripped = normalize_hostname("fe80::1%25eth0")
        assert is_blocked_address(stripped) is not None


class TestObfuscationDetection:
    """The advisory check for non-dotted-decimal literals."""

    def test_decimal_form_is_detected(self) -> None:
        """`2130706433` is a decimal IPv4 literal, not a hostname."""
        assert looks_like_obfuscated_ipv4("2130706433") is True

    def test_dotted_decimal_is_not_flagged(self) -> None:
        """A normal address is handled by the address policy, not this check."""
        assert looks_like_obfuscated_ipv4("127.0.0.1") is False

    def test_ordinary_name_is_not_flagged(self) -> None:
        """A domain name is not an obfuscated literal."""
        assert looks_like_obfuscated_ipv4("example.com") is False


class TestClassifyAddress:
    """`classify_address` judges parsed addresses, not strings."""

    def test_public_v4_allowed(self) -> None:
        """The accept path of the classifier itself."""
        assert classify_address(ipaddress.ip_address("93.184.216.34")) is None

    def test_private_v4_refused(self) -> None:
        """The refuse path of the classifier itself."""
        assert classify_address(ipaddress.ip_address("192.168.0.1")) is not None


class TestSplitAndValidate:
    """Syntax checks: everything decidable without a network."""

    @pytest.mark.parametrize(
        ("raw", "expected_code"),
        [
            ("", "URL_EMPTY"),
            ("   \n  ", "URL_EMPTY"),
            ("example.com", "URL_SCHEME_UNSUPPORTED"),
            ("ftp://example.com", "URL_SCHEME_UNSUPPORTED"),
            ("file:///etc/passwd", "URL_SCHEME_UNSUPPORTED"),
            ("javascript:alert(1)", "URL_SCHEME_UNSUPPORTED"),
            ("data:text/html,<script>x</script>", "URL_SCHEME_UNSUPPORTED"),
            ("//example.com", "URL_SCHEME_UNSUPPORTED"),
            ("https://", "URL_HOST_MISSING"),
            ("http://", "URL_HOST_MISSING"),
            ("https:///path", "URL_HOST_MISSING"),
        ],
    )
    def test_malformed_submission_refused(self, raw: str, expected_code: str) -> None:
        """A URL that cannot be fetched is refused with a specific reason.

        Args:
            raw: The submitted string.
            expected_code: The stable reason the caller branches on.
        """
        with pytest.raises(InvalidUrl) as raised:
            split_and_validate(raw)
        assert raised.value.code == expected_code

    def test_embedded_credentials_refused(self) -> None:
        """A credential in the authority is refused rather than forwarded.

        Stripping it would be friendlier and wrong: the credentials would still
        have been transmitted to the remote host, and would reach our own logs
        on the way.
        """
        with pytest.raises(InvalidUrl) as raised:
            split_and_validate("https://user:secret@example.com/")
        assert raised.value.code == "URL_CREDENTIALS_NOT_ACCEPTED"
        assert "secret" not in raised.value.message

    @pytest.mark.parametrize(
        "raw",
        [
            "https://exa\nmple.com/",
            "https://example.com/a\rb",
            "https://example.com/a\tb",
            "https://example.com/\x00",
        ],
    )
    def test_embedded_control_characters_refused(self, raw: str) -> None:
        """Control characters *inside* a URL are refused.

        Surrounding whitespace is trimmed, because a user pasting a URL with a
        trailing newline is normal and trimming it is friendly. An embedded
        control character is not: it can hide a host from a reviewer reading the
        submission while the fetcher acts on a different one, so accepting it
        would mean the displayed value and the acted-on value differ.

        Args:
            raw: A URL with a control character inside it.
        """
        with pytest.raises(InvalidUrl) as raised:
            split_and_validate(raw)
        assert raised.value.code == "URL_CONTROL_CHARACTERS_NOT_ACCEPTED"

    def test_overlong_url_refused(self) -> None:
        """Length is bounded before anything is parsed."""
        with pytest.raises(InvalidUrl) as raised:
            split_and_validate("https://example.com/" + "a" * 5000, max_length=2048)
        assert raised.value.code == "URL_TOO_LONG"

    def test_implausibly_long_host_refused(self) -> None:
        """A 300-character hostname is refused even inside the URL budget."""
        with pytest.raises(InvalidUrl) as raised:
            split_and_validate(f"https://{'a' * 300}.com/")
        assert raised.value.code == "URL_HOST_TOO_LONG"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("HTTPS://Example.COM/Path", "https://example.com/Path"),
            ("https://example.com:443/Path", "https://example.com/Path"),
            ("http://example.com:80/Path", "http://example.com/Path"),
            ("https://example.com:8443/Path", "https://example.com:8443/Path"),
            ("https://example.com", "https://example.com"),
            ("https://example.com/", "https://example.com/"),
            ("  https://example.com/  ", "https://example.com/"),
            ("https://example.com/P?q=1", "https://example.com/P?q=1"),
        ],
    )
    def test_normalisation(self, raw: str, expected: str) -> None:
        """Two spellings of one address produce one canonical form.

        This is what makes the investigation id stable: without it,
        `https://Example.com` and `https://example.com/` would be two
        investigations of the same page.

        Args:
            raw: The submitted spelling.
            expected: The canonical one.
        """
        normalized, _scheme, _host = split_and_validate(raw)
        assert normalized == expected

    def test_ipv6_host_keeps_its_brackets(self) -> None:
        """A v6 URL round-trips in a form a client can use again."""
        normalized, scheme, host = split_and_validate("https://[2606:2800::1]:8443/x")
        assert scheme == "https"
        assert host == "2606:2800::1"
        assert normalized == "https://[2606:2800::1]:8443/x"

    def test_address_policy_is_not_applied_here(self) -> None:
        """A loopback URL is syntactically valid; the address policy is separate.

        Syntax and destination policy are decided in different places on
        purpose: this function needs no network, and the fetch service is the one
        place a resolution-dependent decision is made. Asserting the separation
        keeps a second, disagreeing SSRF check from creeping in here.
        """
        normalized, _scheme, host = split_and_validate("http://127.0.0.1/")
        assert normalized == "http://127.0.0.1/"
        assert host == "127.0.0.1"
        assert is_blocked_address(host) is not None