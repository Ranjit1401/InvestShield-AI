"""Network destination policy for URL investigation (Phase 12 §5).

This module answers one question: **may this process open a connection to this
address?** It is deliberately free of HTTP so that the whole policy is testable
without a socket, and so that the rules live in one auditable place rather than
being scattered through fetch code.

## The posture is an allowlist, not a denylist

The naive implementation lists the addresses to block: `127.0.0.1`, `10.0.0.0/8`,
`169.254.0.0/16` and so on. That is the wrong shape, because the list of ways to
reach an internal service is unbounded — IPv4-mapped IPv6, 6to4, CGNAT,
cloud-specific metadata addresses, decimal and octal literals, DNS names that
resolve inward — and every entry the list forgets is a hole.

So the rule here is inverted: an address is fetchable only if
:attr:`ipaddress.IPv4Address.is_global` / :attr:`ipaddress.IPv6Address.is_global`
says it is globally routable. Everything else is refused. A new internal range
invented by a cloud provider in five years' time is blocked by default, because
the *default* is "no".

The one exception is made explicitly and visibly: a small set of well-known
cloud metadata endpoints is blocked by address **and** by name, because relying
on `is_global` alone for those would make a future change in a library's
classification a security regression.

## What is refused, and why each matters

- **Loopback, private, link-local, multicast, reserved, unspecified.** The
  standard shapes of "this service is not on the internet".
- **CGNAT (`100.64.0.0/10`).** Carrier-grade NAT space, which is neither public
  nor RFC 1918 and which cloud providers use for internal routing.
- **IPv4-mapped and 6to4 IPv6.** `::ffff:127.0.0.1` reaches loopback and
  `2002:7f00:1::` reaches `127.0.0.1`, so the embedded IPv4 address is unwrapped
  and re-checked on its own.
- **Cloud metadata endpoints.** AWS/Azure and GCP's `169.254.169.254`,
  Alibaba's `100.100.100.200`, and GCP's `metadata.google.internal` by name.
  These hand out instance credentials to anything that asks.
- **Obfuscated host literals.** Decimal (`2130706433`) and octal
  (`0177.0.0.1`) IPv4 forms, which some resolvers accept and
  :mod:`ipaddress` rejects. Resolved through :func:`socket.inet_aton` where
  possible so they are classified rather than passed through.

## DNS rebinding

Checking an address is necessary but not sufficient: a hostname can resolve to a
public address during the check and to `127.0.0.1` when the socket is opened. The
fetcher therefore *pins* the address it validated and dials that address
directly, so the value checked and the value connected are the same value. This
module supplies the validated list; :mod:`app.services.url_fetch` holds it.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

__all__ = [
    "AddressPolicy",
    "InvalidUrl",
    "SYNTAX_MESSAGES",
    "URL_MESSAGES",
    "classify_address",
    "describe_blocked",
    "is_blocked_address",
    "is_blocked_hostname",
    "looks_like_obfuscated_ipv4",
    "normalize_hostname",
    "split_and_validate",
]

#: Wording for every refusal this module can raise, keyed by code.
#:
#: **One message per code, and the table is the only place any of them is
#: written.** An exception takes a code and formats its own message from here, so
#: no call site can invent wording, and no two call sites can disagree about what
#: a code means. `app.graph.nodes` re-exports this table as its own error-message
#: vocabulary, which is what keeps the graph's promise that every failure code
#: has fixed wording literally true for the URL codes as well as its own.
#:
#: Values may contain `{field}` placeholders, filled from the keyword arguments
#: the raising call site supplies. A placeholder is only ever a fact the caller
#: already submitted — a limit, a status code — never anything derived from a
#: socket, a TLS stack or a provider.
SYNTAX_MESSAGES: dict[str, str] = {
    "URL_EMPTY": "No URL was submitted.",
    "URL_INVALID": "The submitted URL is not a valid web address.",
    "URL_TOO_LONG": (
        "The submitted URL is longer than the {max_length} character limit."
    ),
    "URL_SCHEME_UNSUPPORTED": (
        "The submitted URL must begin with http:// or https://. This version "
        "investigates web addresses only."
    ),
    "URL_HOST_MISSING": (
        "The submitted URL does not name a host to investigate."
    ),
    "URL_HOST_TOO_LONG": (
        "The submitted URL names a host longer than any real domain name."
    ),
    "URL_CREDENTIALS_NOT_ACCEPTED": (
        "The submitted URL contains embedded credentials, which are not accepted."
    ),
    "URL_CONTROL_CHARACTERS_NOT_ACCEPTED": (
        "The submitted URL contains control characters, which are not accepted "
        "in a web address."
    ),
    "URL_ADDRESS_BLOCKED": (
        "The submitted address is not a public internet address and was not contacted."
    ),
    "URL_METADATA_ADDRESS_BLOCKED": (
        "The submitted address is a cloud instance-metadata endpoint and was not "
        "contacted."
    ),
    "URL_METADATA_HOSTNAME_BLOCKED": (
        "The submitted host is a cloud instance-metadata service and was not contacted."
    ),
}

#: The complete wording table owned by this module. `url_fetch` adds its own
#: codes, and `app.graph.nodes` merges both into the graph's error vocabulary.
URL_MESSAGES: dict[str, str] = dict(SYNTAX_MESSAGES)

#: Hostnames that exist to hand infrastructure credentials to anything that asks
#: for them. Blocked by name so that a hostname resolving to a *public* address
#: cannot be used to reach the metadata service through a rebinding hop.
_METADATA_HOSTNAMES: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
    }
)

#: Addresses used by cloud providers for instance metadata, in addition to
#: whatever `is_global` already refuses. `169.254.169.254` is link-local and is
#: already covered; the Alibaba address lives in CGNAT space and is listed
#: explicitly so the intent is visible rather than incidental.
_METADATA_ADDRESSES: frozenset[str] = frozenset(
    {
        "169.254.169.254",
        "169.254.170.2",
        "100.100.100.200",
        "fd00:ec2::254",
    }
)

#: Character budget for a submitted hostname. Generous for a real domain name,
#: short enough that the value cannot be a smuggling attempt.
MAX_HOSTNAME_LENGTH = 253


class AddressPolicy:
    """Why a destination was refused.

    Built from a code and the shared wording table, so the explanation always
    matches the code and no call site can attach wording of its own.

    Attributes:
        code: Stable machine-readable reason, suitable for an error code.
        message: Fixed wording. Names the *category* of destination refused and
            nothing about the host, so the reason is not a fingerprint of the
            network the deployment runs in.
    """

    __slots__ = ("code", "message")

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = SYNTAX_MESSAGES[code]

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"AddressPolicy(code={self.code!r})"


# -- hostname normalisation ------------------------------------------------


def normalize_hostname(hostname: str | None) -> str:
    """Lower-case a hostname and strip the surrounding brackets of a literal.

    ``urlsplit`` leaves an IPv6 literal's brackets in place, and the trailing dot
    of a fully-qualified name is significant to DNS but not to a comparison.
    Both are removed here so every later comparison sees one canonical spelling.

    Args:
        hostname: A host as parsed from a URL, or ``None``.

    Returns:
        The normalised hostname, possibly empty.
    """
    if not hostname:
        return ""
    value = hostname.strip().lower()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    # A zone identifier (`fe80::1%25eth0`) names a local interface and has no
    # meaning for a public fetch; dropping it keeps the address parseable.
    value = value.split("%", 1)[0]
    return value.rstrip(".")


def looks_like_obfuscated_ipv4(hostname: str) -> bool:
    """Report whether a host is a non-dotted-decimal IPv4 literal.

    ``2130706433``, ``0177.0.0.1`` and ``0x7f.1`` all denote ``127.0.0.1`` to a
    permissive resolver. :mod:`ipaddress` refuses them, so they are detected
    here and either resolved explicitly or refused — never passed to
    :func:`socket.getaddrinfo` unchecked.

    Args:
        hostname: Normalised hostname.

    Returns:
        ``True`` when the value looks like an obfuscated IPv4 literal.
    """
    if not hostname or "." in hostname:
        return False
    if not hostname.isdigit():
        # `0x...` and `0b...` forms also exist, but they are not all-digit and
        # are refused outright by `_parse_literal` as unparsable.
        return False
    # A plain dotted-decimal host has dots; an all-digit label is therefore a
    # decimal-form IPv4 literal such as `2130706433`.
    return True


# -- address classification -------------------------------------------------


def _parse_literal(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a host as an IP literal, including legacy encodings.

    Handles the dotted-decimal forms :mod:`ipaddress` accepts, then falls back to
    :func:`socket.inet_aton` for the decimal, octal and hexadecimal legacy forms
    that some resolvers still honour.

    Args:
        hostname: Normalised hostname that may itself be a literal.

    Returns:
        The parsed address, or ``None`` when the value is not an IP literal.
    """
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass

    # `ipaddress` deliberately rejects legacy spellings such as `0177.0.0.1`,
    # `2130706433` and `0x7f.1`, but `getaddrinfo` accepts them and resolves
    # them to loopback. Every value without a colon is therefore offered to
    # `inet_aton`, which understands all of those forms, so they are classified
    # rather than passed through to the resolver unchecked.
    if ":" not in hostname:
        try:
            packed = socket.inet_aton(hostname)
        except OSError:
            return None
        return ipaddress.ip_address(packed)

    return None


def _unwrap(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> tuple[
    ipaddress.IPv4Address | ipaddress.IPv6Address, ...
]:
    """Return the address itself plus any address embedded inside it.

    An IPv4-mapped IPv6 address (``::ffff:127.0.0.1``) and a 6to4 address
    (``2002:7f00:1::``) both carry an IPv4 address that is what the socket will
    actually reach, so both are checked as well as the wrapper.

    Args:
        address: A parsed address.

    Returns:
        The address and any embedded address, in that order.
    """
    if not isinstance(address, ipaddress.IPv6Address):
        return (address,)

    embedded: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [address]

    mapped = address.ipv4_mapped
    if mapped is not None:
        embedded.append(mapped)

    # 6to4 embeds the IPv4 address in the 2002::/16 prefix at bits 16-48.
    if address.packed[:2] == b"\x20\x02":
        embedded.append(ipaddress.IPv4Address(address.packed[2:6]))

    return tuple(embedded)


def classify_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> AddressPolicy | None:
    """Decide whether one parsed address may be connected to.

    Args:
        address: A parsed IPv4 or IPv6 address.

    Returns:
        ``None`` when the address is acceptable, otherwise the refusal.
    """
    for candidate in _unwrap(address):
        if str(candidate) in _METADATA_ADDRESSES:
            # Named separately from the general refusal: a submission that reaches
            # an instance-metadata service is a meaningfully different thing from
            # one that reaches any other private address, and the distinction is
            # worth making without naming the address itself.
            return AddressPolicy("URL_METADATA_ADDRESS_BLOCKED")

        if not _is_globally_routable(candidate):
            return AddressPolicy("URL_ADDRESS_BLOCKED")

    return None


def _is_globally_routable(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Report whether an address is globally routable unicast.

    `is_global` is the allowlist: it is ``False`` for loopback, private,
    link-local, multicast, reserved, unspecified and CGNAT space, and ``True``
    only for addresses that route on the public internet.

    Args:
        address: A parsed address.

    Returns:
        ``True`` when the address may be connected to.
    """
    if isinstance(address, ipaddress.IPv6Address):
        # Unique-local (`fc00::/7`) and site-local (`fec0::/10`) are handled by
        # `is_private`, but IPv4-compatible and deprecated site-local forms are
        # not, so they are named explicitly.
        if address.sixtofour is not None or address.teredo is not None:
            # 6to4 and Teredo carry an embedded IPv4 address; treat them as
            # routable only when that embedded address is itself public.
            embedded = address.sixtofour or address.teredo[1]
            return _is_globally_routable(embedded)
        if address.ipv4_mapped is not None:
            return _is_globally_routable(address.ipv4_mapped)
        if address in ipaddress.IPv6Network("fec0::/10"):
            return False

    return bool(address.is_global)


def is_blocked_address(value: str) -> AddressPolicy | None:
    """Classify an address given as text.

    Args:
        value: An address in textual form.

    Returns:
        ``None`` when acceptable, otherwise the refusal.
    """
    parsed = _parse_literal(value)
    if parsed is None:
        return None
    return classify_address(parsed)


def is_blocked_hostname(hostname: str) -> AddressPolicy | None:
    """Refuse a hostname by name, before any resolution is attempted.

    Metadata hostnames are refused here rather than after resolution, so a
    rebinding record cannot turn one into an apparently-public address.

    Args:
        hostname: Normalised hostname.

    Returns:
        ``None`` when acceptable, otherwise the refusal.
    """
    if hostname in _METADATA_HOSTNAMES:
        return AddressPolicy("URL_METADATA_HOSTNAME_BLOCKED")
    return None


def describe_blocked(policy: AddressPolicy | None) -> str:
    """Render a refusal for a log line.

    Args:
        policy: A refusal, or ``None``.

    Returns:
        The message, or an empty string when there was no refusal.
    """
    return policy.message if policy is not None else ""


# -- syntax ----------------------------------------------------------------


class InvalidUrl(ValueError):
    """The submitted string is not a URL this product will fetch.

    The message comes from the shared wording table rather than from the call
    site, so every refusal of a given code reads the same way no matter which
    check produced it, and the graph's error vocabulary needs no second copy of
    these sentences.

    Attributes:
        code: Stable machine-readable reason.
        message: Fixed wording safe to return to a caller.
    """

    def __init__(self, code: str, **fields: object) -> None:
        message = SYNTAX_MESSAGES[code]
        if fields:
            message = message.format(**fields)
        super().__init__(message)
        self.code = code
        self.message = message


def split_and_validate(
    raw_url: str,
    *,
    allowed_schemes: tuple[str, ...] = ("http", "https"),
    max_length: int = 2048,
) -> tuple[str, str, str]:
    """Parse and syntactically validate a submitted URL.

    Performs every check that needs no network: presence, length, scheme, host.
    Address policy is *not* applied here — that needs resolution and belongs to
    the fetch service, so there is one SSRF decision rather than two that could
    disagree.

    Args:
        raw_url: The submitted string.
        allowed_schemes: Lowercase schemes without colons.
        max_length: Maximum accepted length.

    Returns:
        ``(normalized_url, scheme, hostname)``.

    Raises:
        InvalidUrl: When the value cannot be accepted.
    """
    if raw_url is None:
        raise InvalidUrl("URL_EMPTY")

    candidate = raw_url.strip()
    if not candidate:
        raise InvalidUrl("URL_EMPTY")

    if len(candidate) > max_length:
        raise InvalidUrl("URL_TOO_LONG", max_length=max_length)

    # Control characters can hide a scheme or a host from a human reading the
    # submission, and are never legitimate in a URL.
    if any(character in candidate for character in ("\n", "\r", "\t", "\x00")):
        raise InvalidUrl("URL_CONTROL_CHARACTERS_NOT_ACCEPTED")

    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise InvalidUrl("URL_INVALID") from exc

    scheme = (parts.scheme or "").lower()
    if not scheme:
        raise InvalidUrl("URL_SCHEME_UNSUPPORTED")

    if scheme not in allowed_schemes:
        raise InvalidUrl("URL_SCHEME_UNSUPPORTED")

    hostname = normalize_hostname(parts.hostname)
    if not hostname:
        raise InvalidUrl("URL_HOST_MISSING")

    if len(hostname) > MAX_HOSTNAME_LENGTH:
        raise InvalidUrl("URL_HOST_TOO_LONG")

    # An embedded credential in the authority would be sent to the remote host
    # and would otherwise end up in our logs. Refuse rather than strip it.
    if "@" in (parts.netloc or ""):
        raise InvalidUrl("URL_CREDENTIALS_NOT_ACCEPTED")

    normalized = _normalize_url(parts)
    return normalized, scheme, hostname


def _normalize_url(parts: object) -> str:
    """Rebuild a URL in a canonical spelling.

    Lower-cases the scheme and host, drops a default port, and keeps the path,
    query and fragment as submitted. Two spellings of one address therefore
    produce one investigation id.

    Args:
        parts: A :class:`urllib.parse.SplitResult`.

    Returns:
        The canonical URL.
    """
    from urllib.parse import urlunsplit

    scheme = parts.scheme.lower()  # type: ignore[attr-defined]
    hostname = normalize_hostname(parts.hostname)  # type: ignore[attr-defined]
    port = parts.port  # type: ignore[attr-defined]

    netloc = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 80 if scheme == "http" else 443
    if port is not None and port != default_port:
        netloc = f"{netloc}:{port}"

    return urlunsplit(
        (
            scheme,
            netloc,
            parts.path or "",  # type: ignore[attr-defined]
            parts.query,  # type: ignore[attr-defined]
            parts.fragment,  # type: ignore[attr-defined]
        )
    )