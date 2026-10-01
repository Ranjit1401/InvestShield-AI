"""Deterministic URL and domain utilities (Phase 3, §7).

These are the only places in the project allowed to interpret a URL string.
Everything downstream — classification, deduplication, the manual test — uses
`extract_domain()` and `canonicalize_url()` so that two answers to "what host is
this?" can never disagree.

Deliberately absent: DNS resolution, WHOIS, reputation lookups, and any attempt
to decide whether a domain is malicious. Those are investigation questions
belonging to later phases, and conflating them with "which organisation published
this?" would turn a transport utility into an accusation engine.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

#: Prefixes removed from a hostname when deriving the source domain. A page at
#: ``www.example.com`` and the same page at ``example.com`` are the same source
#: for classification purposes.
_STRIPPED_HOST_PREFIXES = ("www.",)

#: Ports that carry no meaning for source identity.
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def extract_domain(url: str) -> str | None:
    """Return the lower-cased registrable host of `url`, or ``None``.

    Handles scheme-less input, default ports, trailing dots, mixed case, user
    info and credentials in the authority section. The full subdomain chain is
    preserved (``nsearchives.nseindia.com`` stays intact) because a subdomain can
    legitimately indicate a different official host.

    Args:
        url: An absolute or scheme-less URL, or a bare hostname.

    Returns:
        The normalised hostname, or ``None`` when no host can be determined.
    """
    if not url or not url.strip():
        return None

    candidate = url.strip()

    parts = urlsplit(candidate)
    if not parts.netloc:
        # Bare host or scheme-less URL such as "sebi.gov.in/some/path".
        parts = urlsplit(f"//{candidate}")

    netloc = parts.netloc
    if not netloc:
        return None

    # Discard any userinfo ("user:pass@host"), which must never become a domain.
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]

    # An IPv6 literal is bracketed; keep it as-is rather than splitting on ":".
    if netloc.startswith("["):
        host = netloc
    else:
        host = netloc.rsplit(":", 1)[0] if ":" in netloc else netloc

    host = host.strip().strip(".").lower()
    if not host:
        return None

    for prefix in _STRIPPED_HOST_PREFIXES:
        if host.startswith(prefix) and len(host) > len(prefix):
            host = host[len(prefix) :]
            break

    return host or None


def canonicalize_url(url: str) -> str | None:
    """Return a stable deduplication key for `url`, or ``None``.

    Normalisation is limited to identity-bearing differences: case in the scheme
    and host, a default port, a leading ``www.``, a trailing slash on an empty
    path, and the fragment. The path and query are otherwise preserved, because
    two different pages on one site are two different results.
    """

    if not url or not url.strip():
        return None

    candidate = url.strip()
    parts = urlsplit(candidate)
    if not parts.scheme:
        parts = urlsplit(f"//{candidate}")

    scheme = parts.scheme.lower()
    if not parts.netloc:
        return None

    netloc = parts.netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    netloc = netloc.strip().strip(".").lower()

    if scheme in _DEFAULT_PORTS and netloc.endswith(f":{_DEFAULT_PORTS[scheme]}"):
        netloc = netloc[: -(len(_DEFAULT_PORTS[scheme]) + 1)]

    for prefix in _STRIPPED_HOST_PREFIXES:
        if netloc.startswith(prefix) and len(netloc) > len(prefix):
            netloc = netloc[len(prefix) :]
            break

    path = parts.path or ""
    if path in ("", "/"):
        path = ""
    elif path != "/":
        path = path.rstrip("/") or ""

    query = parts.query or ""
    # The fragment never identifies a different document.
    return urlunsplit((scheme, netloc, path, query, ""))


__all__ = ["canonicalize_url", "extract_domain"]