"""Tests for the authoritative source registry (Phase 4, §2).

The registry is the layer that stops a scammer's own website from being presented
as a regulator. These tests pin that behaviour down with the exact lookalike
hosts that motivated it (D-018).
"""

from __future__ import annotations

import pytest

from app.schemas.claims import ClaimType
from app.schemas.search import SourceType
from app.schemas.verification import TIER_PRIORITY, SourceTier
from app.services.verification.authority_registry import (
    AUTHORITY_BY_KEY,
    AUTHORITY_SOURCES,
    AuthoritySource,
    authorities_for_claim_type,
    authority_for_domain,
    authority_for_url,
    resolve_authority,
    source_type_for_tier,
    tier_for_domain,
    tier_for_source_type,
)


class TestRegistryContents:
    """The registry is explicit, keyed and internally consistent."""

    def test_expected_authorities_are_present(self) -> None:
        assert {authority.key for authority in AUTHORITY_SOURCES} == {
            "sebi",
            "rbi",
            "irdai",
            "nfra",
            "sfio",
            "mca",
            "nse",
            "bse",
        }

    def test_keys_are_unique_and_indexed(self) -> None:
        keys = [authority.key for authority in AUTHORITY_SOURCES]
        assert len(keys) == len(set(keys))
        assert set(AUTHORITY_BY_KEY) == set(keys)

    def test_every_authority_has_a_name_and_topics(self) -> None:
        for authority in AUTHORITY_SOURCES:
            assert authority.name.strip()
            assert authority.topics
            assert all(topic.strip() for topic in authority.topics)

    def test_domains_are_lowercase_and_unprefixed(self) -> None:
        for authority in AUTHORITY_SOURCES:
            for domain in authority.domains:
                assert domain == domain.lower()
                assert not domain.startswith("www.")
                assert "." in domain

    def test_no_domain_is_shared_between_authorities(self) -> None:
        seen: set[str] = set()
        for authority in AUTHORITY_SOURCES:
            for domain in authority.domains:
                assert domain not in seen, f"{domain} claimed twice"
                seen.add(domain)

    def test_registry_tier_matches_phase3_classification(self) -> None:
        """The two phases must never disagree about a host's tier."""
        for authority in AUTHORITY_SOURCES:
            assert tier_for_domain(authority.domain) is authority.tier

    def test_is_relevant_to_reflects_the_declared_set(self) -> None:
        sebi = AUTHORITY_BY_KEY["sebi"]
        assert sebi.is_relevant_to(ClaimType.REGULATORY_STATUS) is True
        assert sebi.is_relevant_to(ClaimType.RETURN_PROMISE) is False


class TestAuthorityLookup:
    """Hostname identity, never a suggestive word."""

    @pytest.mark.parametrize(
        ("domain", "key"),
        [
            ("sebi.gov.in", "sebi"),
            ("investor.sebi.gov.in", "sebi"),
            ("www.sebi.gov.in", "sebi"),
            ("rbi.org.in", "rbi"),
            ("mca.gov.in", "mca"),
            ("nseindia.com", "nse"),
            ("nsearchives.nseindia.com", "nse"),
            ("bseindia.com", "bse"),
            ("irdai.gov.in", "irdai"),
            ("nfra.gov.in", "nfra"),
            ("sfio.nic.in", "sfio"),
        ],
    )
    def test_registered_hosts_resolve(self, domain: str, key: str) -> None:
        assert authority_for_domain(domain).key == key

    @pytest.mark.parametrize(
        "domain",
        [
            "fake-sebi-example.com",
            "sebi-registration-example.com",
            "sebi-help.example.com",
            "sebi.example",
            "sebigovin.evil.net",
            "sebi.gov.in.attacker.net",
            "notsebi.gov.in",
            "mysebi.gov.in",
            "xnseindia.com",
            "sebi.gov.in.co",
            "example.com",
            "sebi-help.example.org",
        ],
    )
    def test_lookalike_hosts_resolve_to_nobody(self, domain: str) -> None:
        """A host that merely contains an authority's name is not that authority."""
        assert authority_for_domain(domain) is None

    @pytest.mark.parametrize("domain", [None, "", "   ", ".", None])
    def test_empty_input_resolves_to_nobody(self, domain: str | None) -> None:
        assert authority_for_domain(domain) is None

    def test_case_and_trailing_dot_are_normalised(self) -> None:
        assert authority_for_domain("SEBI.GOV.IN.").key == "sebi"

    def test_lookalike_is_never_authoritative_either(self) -> None:
        tier, authority = resolve_authority("https://fake-sebi-example.com/sebi")
        assert authority is None
        assert tier not in {
            SourceTier.TIER_1_PRIMARY_REGULATOR,
            SourceTier.TIER_2_GOVERNMENT,
            SourceTier.TIER_3_EXCHANGE,
        }

    def test_authority_for_url_extracts_the_host_first(self) -> None:
        assert authority_for_url("https://www.sebi.gov.in/legal/x.htm").key == "sebi"

    def test_authority_for_url_handles_junk(self) -> None:
        assert authority_for_url(None) is None
        assert authority_for_url("") is None
        assert authority_for_url("   ") is None

    def test_unlisted_government_host_is_authoritative_but_uncitable(self) -> None:
        """A real government host we have not enumerated is reported, not cited."""
        tier, authority = resolve_authority("some-unlisted-department.gov.in")
        assert tier is SourceTier.TIER_2_GOVERNMENT
        assert authority is None

    def test_resolve_authority_accepts_a_bare_hostname(self) -> None:
        tier, authority = resolve_authority("sebi.gov.in/orders")
        assert (tier, authority.key if authority else None) == (
            SourceTier.TIER_1_PRIMARY_REGULATOR,
            "sebi",
        )


class TestTierMapping:
    """Tier derivation and its inverse."""

    @pytest.mark.parametrize(
        ("source_type", "tier"),
        [
            (SourceType.REGULATOR, SourceTier.TIER_1_PRIMARY_REGULATOR),
            (SourceType.GOVERNMENT, SourceTier.TIER_2_GOVERNMENT),
            (SourceType.EXCHANGE, SourceTier.TIER_3_EXCHANGE),
            (SourceType.OFFICIAL_ENTITY, SourceTier.TIER_4_OFFICIAL_ENTITY),
            (SourceType.TRUSTED_SECONDARY, SourceTier.TIER_5_TRUSTED_SECONDARY),
            (SourceType.GENERAL_WEB, SourceTier.TIER_6_GENERAL_WEB),
            (SourceType.UNKNOWN, SourceTier.UNKNOWN),
        ],
    )
    def test_tier_round_trips(self, source_type: SourceType, tier: SourceTier) -> None:
        assert tier_for_source_type(source_type) is tier
        assert source_type_for_tier(tier) is source_type

    def test_tier_for_domain_of_unknown_host(self) -> None:
        assert tier_for_domain(None) is SourceTier.UNKNOWN
        assert tier_for_domain("") is SourceTier.UNKNOWN

    def test_every_registry_tier_has_a_priority(self) -> None:
        for authority in AUTHORITY_SOURCES:
            assert TIER_PRIORITY[authority.tier] <= TIER_PRIORITY[SourceTier.TIER_3_EXCHANGE]


class TestClaimRelevance:
    """Relevance is contextual: a regulator cannot verify a promised return."""

    @pytest.mark.parametrize(
        "claim_type",
        [ClaimType.REGULATORY_STATUS, ClaimType.CREDENTIAL_CLAIM, ClaimType.COMPANY_CLAIM],
    )
    def test_registry_claims_list_relevant_authorities(self, claim_type: ClaimType) -> None:
        authorities = authorities_for_claim_type(claim_type)
        assert authorities
        assert "sebi" in {authority.key for authority in authorities}

    @pytest.mark.parametrize(
        "claim_type",
        [
            ClaimType.RETURN_PROMISE,
            ClaimType.PROFIT_PROMISE,
            ClaimType.PAYMENT_INSTRUCTION,
            ClaimType.PRODUCT_CLAIM,
            ClaimType.INVESTMENT_OPPORTUNITY,
            ClaimType.GUARANTEE_CLAIM,
        ],
    )
    def test_no_official_record_can_settle_these(
        self, claim_type: ClaimType
    ) -> None:
        """A return, a payment destination or a product feature is not in a register."""
        assert authorities_for_claim_type(claim_type) == ()

    def test_results_are_ordered_best_tier_first(self) -> None:
        authorities = authorities_for_claim_type(ClaimType.REGULATORY_STATUS)
        priorities = [TIER_PRIORITY[authority.tier] for authority in authorities]
        assert priorities == sorted(priorities)

    def test_ordering_is_deterministic(self) -> None:
        first = authorities_for_claim_type(ClaimType.OWNERSHIP_CLAIM)
        second = authorities_for_claim_type(ClaimType.OWNERSHIP_CLAIM)
        assert [item.key for item in first] == [item.key for item in second]

    def test_exchanges_are_not_relevant_to_credentials(self) -> None:
        keys = {
            authority.key
            for authority in authorities_for_claim_type(ClaimType.CREDENTIAL_CLAIM)
        }
        assert "nse" not in keys
        assert "bse" not in keys


class TestAuthoritySourceHelpers:
    """The dataclass surface used by query construction."""

    def test_domain_property_returns_the_primary_host(self) -> None:
        authority = AuthoritySource(
            key="x",
            name="X",
            domains=("x.gov.in", "y.gov.in"),
            tier=SourceTier.TIER_2_GOVERNMENT,
            relevant_claim_types=frozenset({ClaimType.COMPANY_CLAIM}),
            topics=("topic",),
        )
        assert authority.domain == "x.gov.in"
        assert authority.is_relevant_to(ClaimType.COMPANY_CLAIM) is True
        assert authority.is_relevant_to(ClaimType.RETURN_PROMISE) is False