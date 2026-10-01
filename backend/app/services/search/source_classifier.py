"""Source classification (Phase 3, §8).

Maps a hostname onto the `SourceType` that describes *who published a
document*. That is the only question this module answers.

The hard rule, and the reason this module is small and explicit: authority is
recognised by **hostname identity**, never by the presence of a suggestive word.

```
sebi.gov.in             -> REGULATOR
investor.sebi.gov.in    -> REGULATOR     (subdomain of a registered regulator)
fake-sebi-example.com   -> GENERAL_WEB   (contains "sebi"; is not SEBI)
sebi-registry-fake.in   -> GENERAL_WEB   (contains "sebi"; is not SEBI)
```

An over-claiming classifier here would be worse than no classifier at all: it
would let a later stage present a scammer's own website as an authoritative
regulator. Anything unrecognised therefore becomes `GENERAL_WEB`, never a
specific category.

No DNS lookup happens. Deciding whether a host is *legitimate* rather than
*official* is an investigation question for Phase 4, not a parsing question.
"""

from __future__ import annotations

from app.schemas.search import SourceType

#: Hostnames (and their subdomains) operated by Indian financial regulators.
#: Explicit rather than pattern-based on purpose.
#:
#: `mca.gov.in` is deliberately absent: the Ministry of Corporate Affairs is a
#: government department rather than a financial regulator, so it is classified
#: `GOVERNMENT` by the suffix rule below. It remains authoritative — the
#: distinction is about category accuracy, not importance.
REGULATOR_DOMAINS: frozenset[str] = frozenset(
    {
        "sebi.gov.in",
        "rbi.org.in",
        "irdai.gov.in",
        "nfra.gov.in",
        "pfrda.org.in",
        "sfio.nic.in",
    }
)

#: Hostnames operated by stock exchanges.
EXCHANGE_DOMAINS: frozenset[str] = frozenset(
    {
        "nseindia.com",
        "bseindia.com",
        "nseindia.com.cn",
    }
)

#: Government suffixes. Registrable only by the government, so a host under one
#: is treated as `GOVERNMENT` — unless it is already a known regulator.
GOVERNMENT_SUFFIXES: tuple[str, ...] = ("gov.in", "nic.in", "gov")

#: Suffixes treated as general web content.
GENERAL_WEB_SUFFIXES: tuple[str, ...] = (
    ".com",
    ".net",
    ".org",
    ".in",
    ".co.in",
    ".io",
    ".biz",
    ".info",
    ".app",
    ".xyz",
    ".online",
    ".site",
    ".live",
)


def _host_matches(host: str, registry: frozenset[str]) -> bool:
    """Return True when `host` is, or is a subdomain of, a registered domain.

    Matching is on label boundaries only, so ``notsebi.gov.in.example.com`` and
    ``mysebi.gov.in` do not match ``sebi.gov.in``.
    """

    return any(host == domain or host.endswith(f".{domain}") for domain in registry)


def classify_domain(domain: str | None) -> SourceType:
    """Classify a hostname into a publisher category.

    Args:
        domain: Hostname as produced by `extract_domain()`, or ``None``.

    Returns:
        The matching `SourceType`. Unrecognised hosts become `GENERAL_WEB` when
        they look like ordinary sites, and `UNKNOWN` when there is no usable host
        at all.
    """
    if not domain or not domain.strip():
        return SourceType.UNKNOWN

    host = domain.strip().strip(".").lower()
    if not host:
        return SourceType.UNKNOWN

    # Regulators first: sebi.gov.in is both a regulator and a .gov.in host, and
    # the more specific answer is the useful one.
    if _host_matches(host, REGULATOR_DOMAINS):
        return SourceType.REGULATOR

    if _host_matches(host, EXCHANGE_DOMAINS):
        return SourceType.EXCHANGE

    for suffix in GOVERNMENT_SUFFIXES:
        if host == suffix or host.endswith(f".{suffix}"):
            return SourceType.GOVERNMENT

    for suffix in GENERAL_WEB_SUFFIXES:
        if host.endswith(suffix):
            return SourceType.GENERAL_WEB

    # Any multi-label host whose last label is alphabetic looks like a real
    # public suffix we simply do not enumerate (".co.uk", ".com.br", ".jp").
    # Calling it ordinary web content is safe: the official categories above have
    # already been ruled out by exact, label-boundary matching.
    labels = host.split(".")
    if len(labels) >= 2 and labels[-1].isalpha() and len(labels[-1]) >= 2:
        return SourceType.GENERAL_WEB

    # A single-label host (an intranet name) or an IP literal is not something we
    # can place. UNKNOWN keeps a later stage from treating it as ordinary
    # published content it might cite.
    return SourceType.UNKNOWN


def classify_url(url: str | None) -> SourceType:
    """Classify the publisher of `url` by extracting its host first.

    Args:
        url: Any URL string.

    Returns:
        The matching `SourceType`, or `UNKNOWN` when no host can be read.
    """
    from app.services.search.domain import extract_domain

    return classify_domain(extract_domain(url or ""))


__all__ = [
    "EXCHANGE_DOMAINS",
    "GOVERNMENT_SUFFIXES",
    "GENERAL_WEB_SUFFIXES",
    "REGULATOR_DOMAINS",
    "classify_domain",
    "classify_url",
]