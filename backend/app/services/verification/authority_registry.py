"""Authoritative source registry and tiering (Phase 4, §2).

This registry answers one question: **which organisations may settle a claim,
and about which kinds of claim?**

Two rules give it its value.

1. **Hostname identity, never a keyword.** A domain belongs to an authority
   only through exact, label-boundary matching, reusing Phase 3's
   `extract_domain()` and `classify_domain()` (D-018). So
   ``sebi.gov.in`` is SEBI, while ``fake-sebi-example.com``,
   ``sebi-registration-example.com``, ``sebi-help.example.com`` and
   ``sebi.gov.in.attacker.net`` are all `GENERAL_WEB` and belong to nobody here.
2. **Relevance is contextual.** A regulator's records can establish *who is
   registered*. They cannot establish *what a scheme returns*, *where to send
   money*, or *what a product does*. `relevant_claim_types` encodes that, so a
   SEBI page can never verify a 35% monthly return promise just because SEBI is
   the most authoritative source on the web.

Deliberately absent: any judgement about whether an authority has done its job,
whether a specific listing exists, or whether a domain in the registry is
genuinely operated by that body. That last question needs DNS and registration
records — an investigation, not a table lookup.

The registry is small and explicit on purpose. An unlisted official body is
classified and reported, but it cannot settle a claim: an unlisted host is
treated as uncitable rather than guessed at (D-018 rationale).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.claims import ClaimType
from app.schemas.search import SourceType
from app.schemas.verification import TIER_PRIORITY, SourceTier
from app.services.search.source_classifier import classify_domain

#: Claim types for which a regulator or government record is the natural place
#: to look for confirmation. Everything else is deliberately outside this set:
#: promised returns, product behaviour, payment destinations and withdrawal
#: experience are not established by a regulator's register.
REGULATORY_RECORD_CLAIM_TYPES: frozenset[ClaimType] = frozenset(
    {
        ClaimType.REGULATORY_STATUS,
        ClaimType.CREDENTIAL_CLAIM,
        ClaimType.COMPANY_CLAIM,
        ClaimType.OWNERSHIP_CLAIM,
        ClaimType.AFFILIATION_CLAIM,
    }
)


@dataclass(frozen=True)
class AuthoritySource:
    """One official body whose records can settle particular claim types.

    Attributes:
        key: Stable machine identifier, e.g. ``sebi``.
        name: Human-readable body name, used in explanations.
        domains: Hostnames operated by this body. Matched exactly or as a
            label-boundary suffix, never by keyword.
        tier: Authority tier. Always equal to the tier Phase 3 derives for the
            domain, so the two layers cannot drift apart; a test enforces it.
        relevant_claim_types: Claim families this body's records can speak to.
        topics: Search topic terms appended to a targeted query, chosen from
            the vocabulary that body actually publishes in.
    """

    key: str
    name: str
    domains: tuple[str, ...]
    tier: SourceTier
    relevant_claim_types: frozenset[ClaimType]
    topics: tuple[str, ...]

    @property
    def domain(self) -> str:
        """The primary hostname for this body."""
        return self.domains[0]

    def is_relevant_to(self, claim_type: ClaimType) -> bool:
        """Whether this body's records can speak to `claim_type`.

        Args:
            claim_type: The claim family under consideration.

        Returns:
            True when a record from this body could establish the claim.
        """
        return claim_type in self.relevant_claim_types


#: Indian financial regulators and government bodies consulted for registration
#: and corporate facts. Explicit list, no wildcards, no keyword matching.
AUTHORITY_SOURCES: tuple[AuthoritySource, ...] = (
    AuthoritySource(
        key="sebi",
        name="Securities and Exchange Board of India (SEBI)",
        domains=("sebi.gov.in",),
        tier=SourceTier.TIER_1_PRIMARY_REGULATOR,
        relevant_claim_types=REGULATORY_RECORD_CLAIM_TYPES,
        topics=("registration", "registered entity", "intermediary registration"),
    ),
    AuthoritySource(
        key="rbi",
        name="Reserve Bank of India (RBI)",
        domains=("rbi.org.in",),
        tier=SourceTier.TIER_1_PRIMARY_REGULATOR,
        relevant_claim_types=frozenset(
            {
                ClaimType.REGULATORY_STATUS,
                ClaimType.CREDENTIAL_CLAIM,
                ClaimType.COMPANY_CLAIM,
            }
        ),
        topics=("registration", "license", "authorised institution"),
    ),
    AuthoritySource(
        key="irdai",
        name="Insurance Regulatory and Development Authority of India (IRDAI)",
        domains=("irdai.gov.in",),
        tier=SourceTier.TIER_1_PRIMARY_REGULATOR,
        relevant_claim_types=frozenset(
            {
                ClaimType.REGULATORY_STATUS,
                ClaimType.CREDENTIAL_CLAIM,
                ClaimType.COMPANY_CLAIM,
            }
        ),
        topics=("insurer registration", "license", "registered insurer"),
    ),
    AuthoritySource(
        key="nfra",
        name="National Financial Reporting Authority (NFRA)",
        domains=("nfra.gov.in",),
        tier=SourceTier.TIER_1_PRIMARY_REGULATOR,
        relevant_claim_types=frozenset(
            {
                ClaimType.COMPANY_CLAIM,
                ClaimType.OWNERSHIP_CLAIM,
            }
        ),
        topics=("auditor registration", "audit firm", "enforcement action"),
    ),
    AuthoritySource(
        key="sfio",
        name="Securities Fraud Investigation Office (SFIO)",
        domains=("sfio.nic.in",),
        tier=SourceTier.TIER_1_PRIMARY_REGULATOR,
        relevant_claim_types=frozenset(
            {
                ClaimType.REGULATORY_STATUS,
                ClaimType.COMPANY_CLAIM,
                ClaimType.OWNERSHIP_CLAIM,
            }
        ),
        topics=("investigation", "prosecution", "show cause notice"),
    ),
    AuthoritySource(
        key="mca",
        name="Ministry of Corporate Affairs (MCA)",
        domains=("mca.gov.in",),
        tier=SourceTier.TIER_2_GOVERNMENT,
        relevant_claim_types=frozenset(
            {
                ClaimType.COMPANY_CLAIM,
                ClaimType.OWNERSHIP_CLAIM,
                ClaimType.REGULATORY_STATUS,
            }
        ),
        topics=("company master data", "director", "corporate identity number"),
    ),
    AuthoritySource(
        key="nse",
        name="National Stock Exchange of India (NSE)",
        domains=("nseindia.com",),
        tier=SourceTier.TIER_3_EXCHANGE,
        relevant_claim_types=frozenset(
            {
                ClaimType.COMPANY_CLAIM,
                ClaimType.REGULATORY_STATUS,
            }
        ),
        topics=("listed company", "listing", "company profile"),
    ),
    AuthoritySource(
        key="bse",
        name="BSE Limited (BSE)",
        domains=("bseindia.com",),
        tier=SourceTier.TIER_3_EXCHANGE,
        relevant_claim_types=frozenset(
            {
                ClaimType.COMPANY_CLAIM,
                ClaimType.REGULATORY_STATUS,
            }
        ),
        topics=("listed company", "listing", "company profile"),
    ),
)

#: Fast key lookup. Built once; the registry itself is immutable.
AUTHORITY_BY_KEY: dict[str, AuthoritySource] = {
    authority.key: authority for authority in AUTHORITY_SOURCES
}


def tier_for_source_type(source_type: SourceType) -> SourceTier:
    """Map a Phase 3 publisher category onto a Phase 4 authority tier.

    Args:
        source_type: Publisher category already determined by
            `classify_domain()`.

    Returns:
        The corresponding `SourceTier`. Unknown publishers become
        `SourceTier.UNKNOWN`, which is never authoritative.
    """
    match source_type:
        case SourceType.REGULATOR:
            return SourceTier.TIER_1_PRIMARY_REGULATOR
        case SourceType.GOVERNMENT:
            return SourceTier.TIER_2_GOVERNMENT
        case SourceType.EXCHANGE:
            return SourceTier.TIER_3_EXCHANGE
        case SourceType.OFFICIAL_ENTITY:
            return SourceTier.TIER_4_OFFICIAL_ENTITY
        case SourceType.TRUSTED_SECONDARY:
            return SourceTier.TIER_5_TRUSTED_SECONDARY
        case SourceType.GENERAL_WEB:
            return SourceTier.TIER_6_GENERAL_WEB
        case SourceType.UNKNOWN:
            return SourceTier.UNKNOWN


def authority_for_domain(domain: str | None) -> AuthoritySource | None:
    """Return the authority that operates `domain`, if any.

    Matching is exact-host or label-boundary-suffix only. A host that merely
    contains an authority's name is not that authority:

    ```
    sebi.gov.in                 -> SEBI
    investor.sebi.gov.in        -> SEBI
    fake-sebi-example.com       -> None
    sebi-registration-example.com -> None
    sebi-help.example.com       -> None
    sebi.gov.in.example.net     -> None
    ```

    Args:
        domain: Hostname as produced by `extract_domain()`, or ``None``.

    Returns:
        The matching `AuthoritySource`, or ``None`` when the host is not a
        registered authority host.
    """
    if not domain or not domain.strip():
        return None

    host = domain.strip().strip(".").lower()
    if not host:
        return None

    for authority in AUTHORITY_SOURCES:
        for registered in authority.domains:
            if host == registered or host.endswith(f".{registered}"):
                return authority
    return None


def authority_for_url(url: str | None) -> AuthoritySource | None:
    """Return the authority operating the host of `url`, if any.

    Args:
        url: Any URL string.

    Returns:
        The matching `AuthoritySource`, or ``None``.
    """
    from app.services.search.domain import extract_domain

    return authority_for_domain(extract_domain(url or ""))


def source_type_for_tier(tier: SourceTier) -> SourceType:
    """Map an authority tier back onto its publisher category.

    The inverse of `tier_for_source_type()`, used when query construction wants
    the *category* of source it is looking for rather than its rank.

    Args:
        tier: Authority tier.

    Returns:
        The corresponding `SourceType`.
    """
    match tier:
        case SourceTier.TIER_1_PRIMARY_REGULATOR:
            return SourceType.REGULATOR
        case SourceTier.TIER_2_GOVERNMENT:
            return SourceType.GOVERNMENT
        case SourceTier.TIER_3_EXCHANGE:
            return SourceType.EXCHANGE
        case SourceTier.TIER_4_OFFICIAL_ENTITY:
            return SourceType.OFFICIAL_ENTITY
        case SourceTier.TIER_5_TRUSTED_SECONDARY:
            return SourceType.TRUSTED_SECONDARY
        case SourceTier.TIER_6_GENERAL_WEB:
            return SourceType.GENERAL_WEB
        case SourceTier.UNKNOWN:
            return SourceType.UNKNOWN


def tier_for_domain(domain: str | None) -> SourceTier:
    """Return the authority tier of `domain`.

    Delegates classification to Phase 3's `classify_domain()` so a host can
    never be tiered differently by the two phases, then maps the publisher
    category onto a tier.

    Args:
        domain: Hostname, or ``None``.

    Returns:
        The `SourceTier` for that host.
    """
    return tier_for_source_type(classify_domain(domain))


def authorities_for_claim_type(claim_type: ClaimType) -> tuple[AuthoritySource, ...]:
    """Return the authorities whose records are relevant to `claim_type`.

    Ordered most to least authoritative, then by registry order, so query
    construction is deterministic.

    Args:
        claim_type: The claim family under consideration.

    Returns:
        Relevant authorities, best tier first. Empty for claim families no
        official record can settle, such as a promised return.
    """
    relevant = [
        authority for authority in AUTHORITY_SOURCES if authority.is_relevant_to(claim_type)
    ]
    return tuple(
        sorted(relevant, key=lambda item: (TIER_PRIORITY[item.tier], item.key))
    )


def resolve_authority(url_or_domain: str | None) -> tuple[SourceTier, AuthoritySource | None]:
    """Return both the tier and the registry entry for a host.

    Args:
        url_or_domain: A URL or a bare hostname.

    Returns:
        ``(tier, authority)``; `authority` is ``None`` when the host is not in
        the registry, even if its tier is authoritative. An authoritative but
        unregistered host is reported, never cited.
    """
    from app.services.search.domain import extract_domain

    host = extract_domain(url_or_domain or "")
    return tier_for_domain(host), authority_for_domain(host)


__all__ = [
    "AUTHORITY_BY_KEY",
    "AUTHORITY_SOURCES",
    "REGULATORY_RECORD_CLAIM_TYPES",
    "AuthoritySource",
    "authorities_for_claim_type",
    "authority_for_domain",
    "authority_for_url",
    "resolve_authority",
    "source_type_for_tier",
    "tier_for_domain",
    "tier_for_source_type",
]