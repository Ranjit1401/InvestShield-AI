"""Evidence construction (Phase 5).

The builder is where the no-fabrication rule is enforced in code: every excerpt
is a verbatim slice of a `SearchResult` field, and every item carries enough
provenance for a reader to check it against the document it claims to come from.
These tests hold both halves of that guarantee.
"""

from __future__ import annotations

import pytest

from app.schemas.claims import ClaimType
from app.schemas.evidence import EvidenceRelation, EvidenceRelevance, EvidenceType
from app.schemas.search import SourceType
from app.schemas.verification import (
    AUTHORITATIVE_SOURCE_CONFIRMS,
    AUTHORITATIVE_SOURCE_CONTRADICTS,
    IdentityMatch,
    SourceTier,
    VerificationStatus,
)
from app.services.evidence.evidence_builder import (
    MAX_EXCERPT_CHARS,
    build_evidence_item,
    evidence_id_for,
    excerpt_for,
    group_by_query,
    status_supports_evidence,
)
from app.services.evidence.source_normalizer import normalize_source
from tests.evidence_factories import (
    Stance,
    make_assessed,
    make_result,
    make_verification,
    ok_response,
)

ACME = "https://www.sebi.gov.in/intermediaries/acme"
SNIPPET = "Acme Capital Advisors is registered as an investment adviser."


class TestExcerpt:
    """An excerpt is copied, never composed."""

    def test_the_snippet_is_preferred(self) -> None:
        result = make_result("Acme Capital Advisors", ACME, snippet=SNIPPET)
        excerpt, origin = excerpt_for(result)
        assert excerpt == SNIPPET
        assert origin == "snippet"

    def test_the_title_is_used_when_there_is_no_snippet(self) -> None:
        result = make_result("Acme Capital Advisors", ACME, snippet=None)
        excerpt, origin = excerpt_for(result)
        assert excerpt == "Acme Capital Advisors"
        assert origin == "title"

    def test_a_blank_snippet_falls_back_to_the_title(self) -> None:
        """A whitespace-only snippet is no snippet."""
        result = make_result("Acme Capital Advisors", ACME, snippet="   ")
        assert excerpt_for(result) == ("Acme Capital Advisors", "title")

    def test_title_and_snippet_are_never_stitched_together(self) -> None:
        """A joined excerpt reads as one sentence from one place, and is not."""
        result = make_result("Acme Capital Advisors", ACME, snippet=SNIPPET)
        excerpt, _origin = excerpt_for(result)
        assert "Acme Capital Advisors is registered" not in excerpt or excerpt == SNIPPET
        assert excerpt != "Acme Capital Advisors " + SNIPPET

    def test_the_excerpt_is_the_exact_snippet(self) -> None:
        result = make_result("T", ACME, snippet=SNIPPET)
        assert excerpt_for(result)[0] in (result.snippet or "")

    def test_unicode_is_preserved_exactly(self) -> None:
        """Truncation must not mangle a non-ASCII character."""
        text = "Registré chez SEBI — numéro 12345"
        result = make_result("T", ACME, snippet=text)
        assert excerpt_for(result)[0] == text

    def test_a_long_snippet_is_truncated_to_a_prefix(self) -> None:
        long_text = "word " * 200
        result = make_result("T", ACME, snippet=long_text.strip())
        excerpt, _origin = excerpt_for(result)
        assert len(excerpt) <= MAX_EXCERPT_CHARS + 3
        assert excerpt.endswith("...")
        assert excerpt[:-3] in long_text

    def test_a_short_snippet_is_untouched(self) -> None:
        result = make_result("T", ACME, snippet=SNIPPET)
        assert excerpt_for(result)[0] == SNIPPET

    def test_a_result_with_no_retrieved_text_at_all_is_an_error(self) -> None:
        """There is nothing to cite, so there is nothing to build."""
        result = make_result("T", ACME, snippet=None).model_copy(update={"title": "   "})
        with pytest.raises(ValueError, match="no retrieved text"):
            excerpt_for(result)


class TestEvidenceId:
    """Ids are derived, so rebuilding yields the same id."""

    def test_the_id_is_stable_across_calls(self) -> None:
        source = normalize_source(make_result("T", ACME, snippet=SNIPPET))
        first = evidence_id_for("claim_001", source, "snippet", "SUPPORTS", SNIPPET)
        second = evidence_id_for("claim_001", source, "snippet", "SUPPORTS", SNIPPET)
        assert first == second

    def test_the_id_carries_the_evidence_prefix(self) -> None:
        source = normalize_source(make_result("T", ACME, snippet=SNIPPET))
        assert evidence_id_for("claim_001", source, "snippet", "SUPPORTS", SNIPPET).startswith(
            "ev_"
        )

    @pytest.mark.parametrize(
        "change",
        [
            {"claim_id": "claim_002"},
            {"origin": "title"},
            {"relationship": "CONTRADICTS"},
            {"excerpt": "A different sentence entirely."},
        ],
    )
    def test_every_part_of_the_key_changes_the_id(self, change: dict[str, str]) -> None:
        source = normalize_source(make_result("T", ACME, snippet=SNIPPET))
        base = evidence_id_for("claim_001", source, "snippet", "SUPPORTS", SNIPPET)
        assert (
            evidence_id_for(
                change.get("claim_id", "claim_001"),
                source,
                change.get("origin", "snippet"),
                change.get("relationship", "SUPPORTS"),
                change.get("excerpt", SNIPPET),
            )
            != base
        )

    def test_two_different_documents_get_different_ids(self) -> None:
        first = normalize_source(make_result("T", ACME, snippet=SNIPPET))
        second = normalize_source(
            make_result("T", "https://www.sebi.gov.in/orders/acme", snippet=SNIPPET)
        )
        assert evidence_id_for("claim_001", first, "snippet", "SUPPORTS", SNIPPET) != (
            evidence_id_for("claim_001", second, "snippet", "SUPPORTS", SNIPPET)
        )

    def test_the_same_claim_built_twice_produces_the_same_item_id(self) -> None:
        """Determinism across rebuilds is what makes de-duplication meaningful."""
        result = make_result("T", ACME, snippet=SNIPPET)
        assessed = make_assessed(ACME)
        verification = make_verification()
        first = build_evidence_item(result, assessed, verification)
        second = build_evidence_item(result, assessed, verification)
        assert first.id == second.id


class TestBuildEvidenceItem:
    """One retrieved document becomes one traceable item."""

    def test_the_excerpt_is_copied_verbatim(self) -> None:
        item = build_evidence_item(
            make_result("T", ACME, snippet=SNIPPET),
            make_assessed(ACME),
            make_verification(),
        )
        assert item.excerpt == SNIPPET
        assert item.excerpt_origin == "snippet"

    def test_provenance_is_carried_from_the_result(self) -> None:
        item = build_evidence_item(
            make_result("Acme Capital Advisors", ACME, snippet=SNIPPET, position=2),
            make_assessed(ACME),
            make_verification(),
        )
        assert item.url == ACME
        assert item.source.position == 2
        assert item.source.title == "Acme Capital Advisors"

    def test_the_verification_status_is_copied_not_recomputed(self) -> None:
        verification = make_verification(status=VerificationStatus.VERIFIED)
        item = build_evidence_item(
            make_result("T", ACME, snippet=SNIPPET), make_assessed(ACME), verification
        )
        assert item.verification_status is VerificationStatus.VERIFIED

    def test_the_claim_type_defaults_to_the_verification_result(self) -> None:
        item = build_evidence_item(
            make_result("T", ACME, snippet=SNIPPET),
            make_assessed(ACME),
            make_verification(claim_type=ClaimType.CREDENTIAL_CLAIM),
        )
        assert item.claim_type is ClaimType.CREDENTIAL_CLAIM

    def test_an_explicit_claim_type_wins(self) -> None:
        item = build_evidence_item(
            make_result("T", ACME, snippet=SNIPPET),
            make_assessed(ACME),
            make_verification(claim_type=ClaimType.REGULATORY_STATUS),
            claim_type=ClaimType.CREDENTIAL_CLAIM,
        )
        assert item.claim_type is ClaimType.CREDENTIAL_CLAIM

    def test_the_matched_cue_is_kept_for_audit(self) -> None:
        """A reader can check *why* the document was read as supporting."""
        item = build_evidence_item(
            make_result("T", ACME, snippet=SNIPPET),
            make_assessed(ACME, matched_cue="registered as"),
            make_verification(),
        )
        assert item.matched_cue == "registered as"

    def test_a_contradicting_record_becomes_a_contradiction(self) -> None:
        verification = make_verification(
            status=VerificationStatus.CONTRADICTED,
            reason_code=AUTHORITATIVE_SOURCE_CONTRADICTS,
        )
        item = build_evidence_item(
            make_result("T", ACME, snippet=SNIPPET),
            make_assessed(ACME, stance=Stance.CONTRADICTS, matched_cue="cancelled"),
            verification,
        )
        assert item.relationship is EvidenceRelation.CONTRADICTS
        assert item.relevance is EvidenceRelevance.HIGH
        assert item.is_proof is True

    def test_a_lookalike_page_is_not_proof(self) -> None:
        item = build_evidence_item(
            make_result("T", "https://www.sebi.gov.in/intermediaries/acme-holdings",
                        snippet=SNIPPET),
            make_assessed(
                "https://www.sebi.gov.in/intermediaries/acme-holdings",
                identity=IdentityMatch.AMBIGUOUS,
                stance=Stance.NEUTRAL,
                matched_cue=None,
            ),
            make_verification(status=VerificationStatus.UNVERIFIED,
                              reason_code="IDENTITY_NOT_FOUND",
                              source_ids=(),
                              matched_result_ids=()),
        )
        assert item.is_proof is False

    def test_the_provider_query_is_recorded_when_known(self) -> None:
        item = build_evidence_item(
            make_result("T", ACME, snippet=SNIPPET),
            make_assessed(ACME),
            make_verification(),
            provider_query="Acme Capital Advisors SEBI registration",
        )
        assert item.provider_query == "Acme Capital Advisors SEBI registration"

    def test_the_provider_query_is_optional(self) -> None:
        item = build_evidence_item(
            make_result("T", ACME, snippet=SNIPPET), make_assessed(ACME), make_verification()
        )
        assert item.provider_query is None

    def test_a_general_web_page_is_a_plain_search_result(self) -> None:
        item = build_evidence_item(
            make_result("T", "https://random-blog.example/x", snippet=SNIPPET),
            make_assessed(
                "https://random-blog.example/x",
                tier=SourceTier.TIER_6_GENERAL_WEB,
                authority_key=None,
                claim_relevant=False,
                identity=IdentityMatch.ABSENT,
                stance=Stance.NEUTRAL,
                matched_cue=None,
            ),
            make_verification(status=VerificationStatus.UNVERIFIED,
                              reason_code="NO_CLAIM_RELEVANT_SOURCE",
                              source_ids=(),
                              matched_result_ids=()),
        )
        assert item.evidence_type is EvidenceType.SEARCH_RESULT
        assert item.is_proof is False


class TestGroupByQuery:
    """Query provenance: which query surfaced which document."""

    def test_results_are_indexed_by_their_query(self) -> None:
        responses = (
            ok_response("query one", (make_result("A", ACME, snippet=SNIPPET),)),
            ok_response("query two", ()),
        )
        indexed = group_by_query(responses)
        assert set(indexed) == {"query one"}
        assert indexed["query one"][0].url == ACME

    def test_an_empty_query_is_skipped(self) -> None:
        assert group_by_query((ok_response("", (make_result("A", ACME),)),)) == {}

    def test_a_response_with_no_results_is_skipped(self) -> None:
        assert group_by_query((ok_response("q", ()),)) == {}

    def test_the_same_query_returning_twice_is_merged_once(self) -> None:
        result = make_result("A", ACME, snippet=SNIPPET)
        indexed = group_by_query((ok_response("q", (result,)), ok_response("q", (result,))))
        assert len(indexed["q"]) == 1

    def test_distinct_results_under_one_query_are_all_kept(self) -> None:
        first = make_result("A", ACME, snippet=SNIPPET)
        second = make_result("B", "https://www.sebi.gov.in/orders/acme", snippet="B")
        indexed = group_by_query((ok_response("q", (first, second)),))
        assert len(indexed["q"]) == 2

    def test_input_order_is_preserved(self) -> None:
        indexed = group_by_query(
            (ok_response("b", (make_result("B", "https://example.com/b"),)),
             ok_response("a", (make_result("A", "https://example.com/a"),)))
        )
        assert list(indexed) == ["b", "a"]

    def test_no_responses_gives_an_empty_index(self) -> None:
        assert group_by_query(()) == {}


class TestStatusSupportsEvidence:
    """`NOT_APPLICABLE` can never have evidence, whatever the caller supplies."""

    def test_not_applicable_supports_no_evidence(self) -> None:
        assert not status_supports_evidence(VerificationStatus.NOT_APPLICABLE)

    @pytest.mark.parametrize(
        "status",
        [
            VerificationStatus.VERIFIED,
            VerificationStatus.UNVERIFIED,
            VerificationStatus.CONTRADICTED,
            VerificationStatus.INSUFFICIENT_EVIDENCE,
        ],
    )
    def test_every_other_status_may_have_evidence(self, status: VerificationStatus) -> None:
        assert status_supports_evidence(status)


def test_no_page_fetch_or_classification_happens_in_the_builder() -> None:
    """The builder's only inputs are the objects it was handed."""
    result = make_result("T", ACME, snippet=SNIPPET).model_copy(
        update={"source_type": SourceType.UNKNOWN}
    )
    item = build_evidence_item(result, make_assessed(ACME), make_verification())
    assert item.source.source_type is SourceType.UNKNOWN
    assert item.source.source_tier is SourceTier.TIER_1_PRIMARY_REGULATOR
