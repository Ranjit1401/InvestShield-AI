"""Evidence de-duplication and deterministic ordering (Phase 5).

De-duplication here is a safety feature, not housekeeping: a URL reachable from
three of a claim's five queries would otherwise contribute three identical
"supporting" items, and a report that says "confirmed by 3 sources" when there is
one is wrong in the direction that matters most.
"""

from __future__ import annotations

import pytest

from app.schemas.evidence import (
    EvidenceItem,
    EvidenceRelation,
    EvidenceRelevance,
    EvidenceSource,
    EvidenceType,
)
from app.schemas.search import SourceType
from app.schemas.verification import SourceTier
from app.services.evidence.evidence_builder import evidence_id_for
from app.services.evidence.evidence_dedupe import (
    dedupe_evidence,
    distinct_sources,
    evidence_key,
    excerpt_key,
    order_evidence,
    order_key,
)
from app.services.evidence.source_normalizer import normalize_source
from tests.evidence_factories import make_result

ACME = "https://www.sebi.gov.in/intermediaries/acme"
ORDERS = "https://www.sebi.gov.in/orders/acme"


def make_item(
    url: str = ACME,
    *,
    excerpt: str = "Acme Capital Advisors is registered as an investment adviser.",
    origin: str = "snippet",
    relationship: EvidenceRelation = EvidenceRelation.SUPPORTS,
    relevance: EvidenceRelevance = EvidenceRelevance.HIGH,
    tier: SourceTier = SourceTier.TIER_1_PRIMARY_REGULATOR,
    position: int = 1,
    claim_id: str = "claim_001",
) -> EvidenceItem:
    """Build an `EvidenceItem` for ordering and de-duplication tests."""
    source = normalize_source(make_result("T", url, snippet=excerpt, position=position))
    source = source.model_copy(update={"source_tier": tier})
    return EvidenceItem(
        id=evidence_id_for(claim_id, source, origin, relationship.value, excerpt),
        claim_id=claim_id,
        evidence_type=EvidenceType.SEARCH_RESULT,
        relationship=relationship,
        relevance=relevance,
        excerpt=excerpt,
        excerpt_origin=origin,
        source=source,
    )


class TestExcerptKey:
    """Only case and whitespace are folded; wording is never touched."""

    def test_case_is_folded(self) -> None:
        assert excerpt_key("Registered as an adviser") == excerpt_key(
            "registered as an adviser"
        )

    def test_whitespace_runs_collapse(self) -> None:
        assert excerpt_key("registered   as\n\tan adviser") == excerpt_key(
            "registered as an adviser"
        )

    def test_leading_and_trailing_space_is_ignored(self) -> None:
        assert excerpt_key("  registered as an adviser  ") == excerpt_key(
            "registered as an adviser"
        )

    def test_a_single_changed_word_makes_a_different_key(self) -> None:
        """Two findings that differ by one word are two findings."""
        assert excerpt_key("registered as an adviser") != excerpt_key(
            "registered as a dealer"
        )

    def test_punctuation_is_significant(self) -> None:
        assert excerpt_key("registered as adviser.") != excerpt_key("registered as adviser")

    def test_an_empty_excerpt_has_an_empty_key(self) -> None:
        assert excerpt_key("") == excerpt_key("   ")


class TestEvidenceKey:
    """The key covers everything that makes an item a distinct finding."""

    def test_identical_items_share_a_key(self) -> None:
        assert evidence_key(make_item()) == evidence_key(make_item())

    def test_a_different_excerpt_changes_the_key(self) -> None:
        assert evidence_key(make_item()) != evidence_key(
            make_item(excerpt="The registration was cancelled.")
        )

    def test_a_different_origin_changes_the_key(self) -> None:
        assert evidence_key(make_item()) != evidence_key(make_item(origin="title"))

    def test_a_different_document_changes_the_key(self) -> None:
        assert evidence_key(make_item()) != evidence_key(make_item(url=ORDERS))

    def test_a_different_claim_changes_the_key(self) -> None:
        assert evidence_key(make_item()) != evidence_key(make_item(claim_id="claim_002"))

    def test_a_different_relationship_changes_the_key(self) -> None:
        """Support and conflict from one page are two findings, never merged."""
        assert evidence_key(make_item()) != evidence_key(
            make_item(relationship=EvidenceRelation.CONTRADICTS)
        )

    def test_two_pages_on_one_regulator_are_not_merged(self) -> None:
        """Collapsing them would erase the distinction the reader must check."""
        registration = make_item(url=ACME, excerpt="Registered as an adviser.")
        cancellation = make_item(url=ORDERS, excerpt="The registration was cancelled.")
        assert evidence_key(registration) != evidence_key(cancellation)

    def test_the_key_is_hashable(self) -> None:
        assert isinstance(hash(evidence_key(make_item())), int)


class TestDedupe:
    """The first item wins, so the survivor is the earliest position."""

    def test_identical_items_collapse_to_one(self) -> None:
        items = (make_item(), make_item(), make_item())
        assert len(dedupe_evidence(items)) == 1

    def test_different_excerpts_are_all_kept(self) -> None:
        items = (
            make_item(excerpt="Registered as an adviser."),
            make_item(excerpt="Registered as a portfolio manager."),
        )
        assert len(dedupe_evidence(items)) == 2

    def test_the_same_document_with_the_same_text_is_collapsed(self) -> None:
        """One document reachable from three queries is still one document."""
        items = tuple(make_item() for _ in range(3))
        assert len(dedupe_evidence(items)) == 1

    def test_differently_spaced_duplicates_are_collapsed(self) -> None:
        items = (
            make_item(excerpt="Registered as an adviser."),
            make_item(excerpt="Registered   as an adviser."),
        )
        assert len(dedupe_evidence(items)) == 1

    def test_the_first_item_survives(self) -> None:
        first = make_item(position=1, excerpt="Registered as an adviser.")
        second = make_item(position=7, excerpt="Registered   as an adviser.")
        survivor = dedupe_evidence((first, second))
        assert survivor[0].source.position == 1

    def test_two_documents_with_the_same_text_are_both_kept(self) -> None:
        items = (make_item(url=ACME), make_item(url=ORDERS))
        assert len(dedupe_evidence(items)) == 2

    def test_both_sides_of_a_conflict_are_kept(self) -> None:
        items = (
            make_item(url=ACME, relationship=EvidenceRelation.SUPPORTS),
            make_item(url=ACME, relationship=EvidenceRelation.CONTRADICTS),
        )
        assert len(dedupe_evidence(items)) == 2

    def test_the_same_finding_for_two_claims_is_kept_twice(self) -> None:
        items = (make_item(claim_id="claim_001"), make_item(claim_id="claim_002"))
        assert len(dedupe_evidence(items)) == 2

    def test_an_empty_input_gives_an_empty_output(self) -> None:
        assert dedupe_evidence(()) == ()

    def test_output_is_in_first_seen_order(self) -> None:
        first = make_item(url=ACME, excerpt="one")
        second = make_item(url=ORDERS, excerpt="two")
        third = make_item(url=ACME, excerpt="three")
        survivors = dedupe_evidence((first, second, third))
        assert [item.excerpt for item in survivors] == ["one", "two", "three"]


class TestOrdering:
    """Ordering is deterministic and reuses existing priority data."""

    def test_a_regulator_outranks_a_general_web_page(self) -> None:
        official = make_item(url=ACME, tier=SourceTier.TIER_1_PRIMARY_REGULATOR)
        blog = make_item(
            url="https://random-blog.example/x", tier=SourceTier.TIER_6_GENERAL_WEB
        )
        assert order_evidence((blog, official))[0] is official

    def test_higher_relevance_ranks_first_within_a_tier(self) -> None:
        low = make_item(relevance=EvidenceRelevance.LOW,
                        relationship=EvidenceRelation.MENTIONS)
        high = make_item(excerpt="different text", relevance=EvidenceRelevance.HIGH)
        assert order_evidence((low, high))[0] is high

    def test_support_ranks_before_conflict_within_a_relevance(self) -> None:
        conflict = make_item(relationship=EvidenceRelation.CONTRADICTS)
        support = make_item(relationship=EvidenceRelation.SUPPORTS)
        assert order_evidence((conflict, support)) == (support, conflict)

    def test_provider_position_breaks_a_tie(self) -> None:
        later = make_item(position=7, excerpt="later text")
        earlier = make_item(position=2, excerpt="earlier text")
        assert order_evidence((later, earlier))[0] is earlier

    def test_the_id_breaks_a_remaining_tie(self) -> None:
        """A total order, so the same inputs always print the same way."""
        items = (make_item(excerpt="bbb"), make_item(excerpt="aaa"))
        assert order_evidence(items) == tuple(sorted(items, key=order_key))

    def test_ordering_is_stable_across_repeated_calls(self) -> None:
        items = tuple(
            make_item(url=url, excerpt=text, position=index)
            for index, (url, text) in enumerate(
                (
                    (ACME, "registered as an adviser"),
                    (ORDERS, "the registration was cancelled"),
                    ("https://www.sebi.gov.in/circulars/returns", "caution against returns"),
                ),
                start=1,
            )
        )
        first = [item.id for item in order_evidence(items)]
        second = [item.id for item in order_evidence(tuple(reversed(items)))]
        assert first == second

    def test_an_empty_input_sorts_to_an_empty_output(self) -> None:
        assert order_evidence(()) == ()

    def test_the_sort_key_ends_with_the_stable_id(self) -> None:
        """Nothing may depend on dict or set iteration order."""
        document = make_item()
        assert order_key(document)[-1] == document.id


class TestDistinctSources:
    """The source list reads in the same order as the evidence citing it."""

    def test_one_document_yields_one_source(self) -> None:
        items = (make_item(), make_item(relationship=EvidenceRelation.CONTRADICTS))
        assert len(distinct_sources(items)) == 1

    def test_two_documents_yield_two_sources(self) -> None:
        items = (make_item(url=ACME), make_item(url=ORDERS))
        assert len(distinct_sources(items)) == 2

    def test_source_order_follows_the_evidence_order(self) -> None:
        ordered = order_evidence((make_item(url=ACME), make_item(url=ORDERS)))
        sources = distinct_sources(ordered)
        assert [source.canonical_url for source in sources] == [
            item.source.canonical_url for item in ordered
        ]

    def test_a_source_that_cited_nothing_is_not_listed(self) -> None:
        assert distinct_sources(()) == ()

    def test_the_returned_sources_are_real_evidence_sources(self) -> None:
        assert all(isinstance(source, EvidenceSource) for source in distinct_sources((make_item(),)))


@pytest.mark.parametrize("source_type", list(SourceType))
def test_every_publisher_category_can_be_normalized(source_type: SourceType) -> None:
    """No `SourceType` leaves the normalizer unable to build a source."""
    result = make_result("T", ACME, snippet="text").model_copy(update={"source_type": source_type})
    assert normalize_source(result).source_type is source_type
