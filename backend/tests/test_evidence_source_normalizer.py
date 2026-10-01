"""Source normalization for evidence (Phase 5).

Phase 5 must not answer "how trustworthy is this publisher?" a second time. These
tests pin the three rules it reuses — Phase 3's canonicalisation and domain
extraction, Phase 4's id minting and authority resolution — so a future change
cannot quietly introduce a second classification of the same URL.
"""

from __future__ import annotations

import pytest

from app.schemas.evidence import EvidenceType
from app.schemas.search import SourceType
from app.schemas.verification import SourceTier
from app.services.evidence.source_normalizer import (
    DEFAULT_EVIDENCE_TYPE,
    EVIDENCE_TYPE_BY_TIER,
    canonical_url_for,
    evidence_type_for_tier,
    normalize_source,
)
from app.services.search.domain import canonicalize_url, extract_domain
from app.services.verification.authority_registry import resolve_authority
from app.services.verification.comparator import result_id_for, source_id_for
from tests.evidence_factories import make_result


class TestCanonicalUrl:
    """URL identity is Phase 3's decision, reused verbatim."""

    def test_phase_three_canonical_form_is_preferred(self) -> None:
        """A result enriched by `SearchService` already carries the answer."""
        result = make_result(
            "Acme",
            "https://www.sebi.gov.in/intermediaries/acme?utm_source=x",
            snippet="Registered.",
        ).model_copy(update={"canonical_url": "https://www.sebi.gov.in/intermediaries/acme"})
        assert canonical_url_for(result) == "https://www.sebi.gov.in/intermediaries/acme"

    def test_a_hand_built_result_is_canonicalized_on_the_way_in(self) -> None:
        result = make_result("Acme", "https://www.sebi.gov.in/intermediaries/acme")
        assert canonical_url_for(result) == canonicalize_url(result.url)

    def test_trailing_slashes_collapse(self) -> None:
        """`…/page` and `…/page/` are one document, here as in Phase 3."""
        bare = canonical_url_for(make_result("A", "https://example.com/page"))
        slashed = canonical_url_for(make_result("A", "https://example.com/page/"))
        assert bare == slashed

    def test_a_result_with_no_usable_url_still_yields_something(self) -> None:
        """Never an empty canonical URL, which would break every key."""
        result = make_result("A", "https://example.com/page").model_copy(update={"url": "  "})
        assert canonical_url_for(result).strip()


class TestEvidenceTypeFromTier:
    """The record category follows the authority tier, and only the tier."""

    @pytest.mark.parametrize(
        ("tier", "expected"),
        [
            (SourceTier.TIER_1_PRIMARY_REGULATOR, EvidenceType.REGULATORY_RECORD),
            (SourceTier.TIER_2_GOVERNMENT, EvidenceType.GOVERNMENT_RECORD),
            (SourceTier.TIER_3_EXCHANGE, EvidenceType.EXCHANGE_RECORD),
            (SourceTier.TIER_4_OFFICIAL_ENTITY, EvidenceType.OFFICIAL_ENTITY_SOURCE),
            (SourceTier.TIER_5_TRUSTED_SECONDARY, EvidenceType.SEARCH_RESULT),
            (SourceTier.TIER_6_GENERAL_WEB, EvidenceType.SEARCH_RESULT),
            (SourceTier.UNKNOWN, EvidenceType.SEARCH_RESULT),
        ],
    )
    def test_tier_maps_to_record_type(
        self, tier: SourceTier, expected: EvidenceType
    ) -> None:
        assert evidence_type_for_tier(tier) is expected

    def test_every_tier_has_a_decision(self) -> None:
        """An unmapped tier would raise at runtime inside the builder."""
        for tier in SourceTier:
            assert evidence_type_for_tier(tier) in EvidenceType

    def test_only_tiers_one_to_three_are_records(self) -> None:
        record_types = {EvidenceType.REGULATORY_RECORD, EvidenceType.GOVERNMENT_RECORD,
                        EvidenceType.EXCHANGE_RECORD}
        for tier, evidence_type in EVIDENCE_TYPE_BY_TIER.items():
            assert (evidence_type in record_types) is (tier in {
                SourceTier.TIER_1_PRIMARY_REGULATOR,
                SourceTier.TIER_2_GOVERNMENT,
                SourceTier.TIER_3_EXCHANGE,
            })

    def test_everything_else_falls_back_to_a_plain_search_result(self) -> None:
        assert DEFAULT_EVIDENCE_TYPE is EvidenceType.SEARCH_RESULT


class TestNormalizeSource:
    """A source is a retrieved document promoted to a named, traceable object."""

    def test_copies_the_retrieved_fields_unchanged(self) -> None:
        result = make_result(
            "Acme Capital Advisors - SEBI",
            "https://www.sebi.gov.in/intermediaries/acme",
            snippet="Acme Capital Advisors is registered as an investment adviser.",
            position=3,
        )
        document = normalize_source(result)
        assert document.url == result.url
        assert document.title == result.title
        assert document.position == 3
        assert document.retrieved_at == result.retrieved_at

    def test_the_retrieval_time_is_never_re_stamped(self) -> None:
        """Re-stamping would record when Phase 5 ran, not when the fetch ran."""
        result = make_result("A", "https://example.com/a", snippet="S")
        document = normalize_source(result)
        assert document.retrieved_at == result.retrieved_at

    def test_ids_are_phase_four_ids(self) -> None:
        result = make_result("A", "https://www.sebi.gov.in/intermediaries/acme", snippet="S")
        document = normalize_source(result)
        assert document.source_id == source_id_for(document.canonical_url)
        assert document.result_id == result_id_for(document.canonical_url)

    def test_the_same_document_keeps_the_same_id_across_results(self) -> None:
        """`source_id` is about the document; `result_id` is about the result."""
        first = normalize_source(make_result("A", "https://example.com/x", snippet="one"))
        second = normalize_source(make_result("B", "https://example.com/x/", snippet="two"))
        assert first.source_id == second.source_id
        assert first.result_id == second.result_id

    def test_the_authority_tier_is_phase_four_registry_output(self) -> None:
        result = make_result(
            "A", "https://www.sebi.gov.in/intermediaries/acme", snippet="S"
        )
        document = normalize_source(result)
        tier, _authority = resolve_authority("www.sebi.gov.in")
        assert document.source_tier is tier

    def test_the_publisher_category_is_phase_three_output(self) -> None:
        """Classification is carried, never recomputed by Phase 5."""
        result = make_result("A", "https://example.com/x", snippet="S").model_copy(
            update={"source_type": SourceType.TRUSTED_SECONDARY}
        )
        assert normalize_source(result).source_type is SourceType.TRUSTED_SECONDARY

    def test_a_carry_over_category_is_not_overridden_by_the_tier(self) -> None:
        """Phase 3 and Phase 4 classify independently; Phase 5 changes neither."""
        result = make_result(
            "A", "https://www.sebi.gov.in/intermediaries/acme", snippet="S"
        ).model_copy(update={"source_type": SourceType.UNKNOWN})
        document = normalize_source(result)
        assert document.source_type is SourceType.UNKNOWN
        assert document.source_tier is SourceTier.TIER_1_PRIMARY_REGULATOR

    def test_the_domain_is_phase_three_extraction(self) -> None:
        result = make_result("A", "https://www.sebi.gov.in/intermediaries/acme", snippet="S")
        assert normalize_source(result).domain == extract_domain(result.url)

    def test_a_normalized_source_is_frozen(self) -> None:
        document = normalize_source(make_result("A", "https://example.com/a", snippet="S"))
        with pytest.raises(Exception):
            document.domain = "attacker.example"  # type: ignore[misc]

    def test_an_unofficial_publisher_is_never_authoritative(self) -> None:
        """A random site is not tiered by how convincing its snippet reads."""
        document = normalize_source(
            make_result("A", "https://random-blog.example/post", snippet="S")
        )
        assert document.source_tier is SourceTier.TIER_6_GENERAL_WEB
        assert document.is_authoritative is False
