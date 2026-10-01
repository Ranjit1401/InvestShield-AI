"""Tests for claim ↔ source comparison (Phase 4, §4).

Three gates guard every document: identity, authority, relevance. These tests
attack each gate with the cases that would otherwise let one entity's record
confirm another entity's claim, or let a marketing page settle a regulatory
question (D-006, D-018).
"""

from __future__ import annotations

import pytest

from app.schemas.claims import ClaimType
from app.schemas.search import SourceType
from app.schemas.verification import IdentityMatch, SourceTier
from app.services.search import classify_domain
from app.services.verification.comparator import (
    CONTRADICTION_CUES,
    NAME_CONTINUATION_TOKENS,
    SUPPORT_CUES,
    AssessedSource,
    Stance,
    assess_result,
    assess_results,
    detect_stance,
    match_identity,
    match_identity_for_target,
    normalize_for_match,
    result_id_for,
    source_id_for,
)
from app.services.verification.target import build_target
from tests.verification_factories import make_claim, make_entity, make_result


def build(
    text: str = "Acme Capital is registered.",
    claim_type: ClaimType = ClaimType.REGULATORY_STATUS,
    entities: tuple = (),
):
    """Build a target for a claim, with optional linked entities."""
    claim = make_claim(text, claim_type, entity_ids=tuple(e.id for e in entities))
    return build_target(claim, entities)


class TestNormalisation:
    """Matching text is normalised without losing identifier punctuation."""

    def test_case_and_whitespace_are_folded(self) -> None:
        assert normalize_for_match("  Acme   CAPITAL  ") == "acme capital"

    def test_identifier_punctuation_survives(self) -> None:
        assert normalize_for_match("acmefunds@okhdfcbank") == "acmefunds@okhdfcbank"
        assert normalize_for_match("INH0000123456") == "inh0000123456"

    def test_sentence_punctuation_is_dropped(self) -> None:
        assert normalize_for_match("Acme Capital, registered.") == "acme capital registered"

    def test_empty_input(self) -> None:
        assert normalize_for_match("") == ""
        assert normalize_for_match(None) == ""


class TestIdentityMatching:
    """A name inside a longer name is a different entity."""

    @pytest.mark.parametrize(
        ("text", "subject", "expected"),
        [
            ("Acme Capital is registered.", "Acme Capital", IdentityMatch.MATCHED),
            ("Acme Capital Advisors", "Acme Capital", IdentityMatch.AMBIGUOUS),
            ("Acme Capital Limited", "Acme Capital", IdentityMatch.AMBIGUOUS),
            ("Acme Capital Holdings", "Acme Capital", IdentityMatch.AMBIGUOUS),
            ("Something else entirely", "Acme Capital", IdentityMatch.ABSENT),
            ("acmecapital.com", "Acme Capital", IdentityMatch.ABSENT),
            ("", "Acme Capital", IdentityMatch.ABSENT),
        ],
    )
    def test_verdicts(self, text: str, subject: str, expected: IdentityMatch) -> None:
        assert match_identity(text, subject) is expected

    def test_a_plain_occurrence_wins_over_an_extended_one(self) -> None:
        text = "Acme Capital Advisors and also Acme Capital itself"
        assert match_identity(text, "Acme Capital") is IdentityMatch.MATCHED

    def test_trailing_punctuation_is_not_a_continuation(self) -> None:
        assert match_identity("Acme Capital.", "Acme Capital") is IdentityMatch.MATCHED

    def test_matching_is_case_and_whitespace_insensitive(self) -> None:
        assert match_identity("ACME   capital", "Acme Capital") is IdentityMatch.MATCHED

    def test_multi_word_subject_requires_all_words(self) -> None:
        assert match_identity("Acme only", "Acme Capital") is IdentityMatch.ABSENT

    def test_legal_suffix_is_a_different_legal_entity(self) -> None:
        """Conservative: a claim about `Acme Capital` is not confirmed by `Acme Capital Ltd`."""
        assert (
            match_identity("Acme Capital Ltd registration", "Acme Capital")
            is IdentityMatch.AMBIGUOUS
        )

    def test_blank_subject_is_absent(self) -> None:
        assert match_identity("anything at all", "  ") is IdentityMatch.ABSENT

    def test_continuation_set_covers_the_obvious_decorations(self) -> None:
        assert {"ltd", "limited", "advisors", "holdings", "pvt"} <= NAME_CONTINUATION_TOKENS

    def test_target_matching_uses_the_first_named_party(self) -> None:
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        assert match_identity_for_target("Acme Capital profile", target) is (
            IdentityMatch.MATCHED
        )

    def test_target_matching_falls_back_to_the_claim_text(self) -> None:
        target = build(text="The scheme is registered with SEBI.")
        assert match_identity_for_target("unrelated page", target) is IdentityMatch.ABSENT

    def test_target_matching_reports_ambiguity(self) -> None:
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        assert match_identity_for_target("Acme Capital Advisors", target) is (
            IdentityMatch.AMBIGUOUS
        )


class TestStanceDetection:
    """Contradiction is read before support."""

    def test_support_cue(self) -> None:
        stance, cue = detect_stance(
            "Acme Capital registration number INH0000123456", ClaimType.REGULATORY_STATUS
        )
        assert stance == Stance.SUPPORTS
        assert cue

    def test_contradiction_cue(self) -> None:
        stance, cue = detect_stance(
            "The registration of Acme Capital was cancelled.", ClaimType.REGULATORY_STATUS
        )
        assert stance == Stance.CONTRADICTS
        assert cue in CONTRADICTION_CUES

    def test_negation_wins_over_the_word_registered(self) -> None:
        stance, _ = detect_stance(
            "Acme Capital is not registered.", ClaimType.REGULATORY_STATUS
        )
        assert stance == Stance.CONTRADICTS

    def test_longest_cue_wins(self) -> None:
        _, cue = detect_stance(
            "registration cancelled", ClaimType.REGULATORY_STATUS
        )
        assert cue == "registration cancelled"

    def test_neutral_when_nothing_speaks_to_the_claim(self) -> None:
        stance, cue = detect_stance("Acme Capital overview page", ClaimType.REGULATORY_STATUS)
        assert stance == Stance.NEUTRAL
        assert cue is None

    def test_empty_document(self) -> None:
        assert detect_stance("", ClaimType.REGULATORY_STATUS) == (Stance.NEUTRAL, None)

    def test_cue_sets_are_non_empty_for_registry_claims(self) -> None:
        for claim_type in (ClaimType.REGULATORY_STATUS, ClaimType.CREDENTIAL_CLAIM):
            assert SUPPORT_CUES[claim_type]


class TestStableIdentifiers:
    """Source and result ids are derived from the canonical URL only."""

    def test_ids_are_stable(self) -> None:
        url = "https://www.sebi.gov.in/legal/x.htm"
        assert source_id_for(url) == source_id_for(url)

    def test_ids_are_distinct_per_kind(self) -> None:
        url = "https://sebi.gov.in/a"
        assert source_id_for(url) != result_id_for(url)

    def test_id_shape(self) -> None:
        assert source_id_for("https://sebi.gov.in/a").startswith("src_")
        assert result_id_for("https://sebi.gov.in/a").startswith("res_")
        assert len(source_id_for("https://sebi.gov.in/a")) == len("src_") + 12

    def test_different_urls_differ(self) -> None:
        assert source_id_for("https://sebi.gov.in/a") != source_id_for("https://sebi.gov.in/b")


class TestAssessedSource:
    """Only a document that passes all three gates can take a stance."""

    def test_authoritative_relevant_source_supports(self) -> None:
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        result = make_result(
            "Acme Capital Advisors",  # deliberately a different entity
            "https://www.sebi.gov.in/other",
            snippet="nothing relevant",
        )
        assessed = assess_result(result, target)
        assert assessed.tier is SourceTier.TIER_1_PRIMARY_REGULATOR
        assert assessed.authority_key == "sebi"
        assert assessed.claim_relevant is True
        assert assessed.identity is IdentityMatch.AMBIGUOUS
        assert assessed.stance == Stance.NEUTRAL

    def test_lookalike_host_is_never_authoritative(self) -> None:
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        result = make_result(
            "Acme Capital registration",
            "https://fake-sebi-example.com/acme",
            snippet="Acme Capital is registered with SEBI.",
        )
        assessed = assess_result(result, target)
        assert assessed.authority_key is None
        assert assessed.claim_relevant is False
        assert assessed.stance == Stance.NEUTRAL
        assert assessed.is_authoritative is False

    def test_general_web_cannot_settle_a_claim(self) -> None:
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        result = make_result(
            "Acme Capital registered entity",
            "https://news.example.com/acme",
            snippet="Acme Capital is registered as an investment adviser.",
        )
        assessed = assess_result(result, target)
        assert assessed.tier is SourceTier.TIER_6_GENERAL_WEB
        assert assessed.claim_relevant is False
        assert assessed.stance == Stance.NEUTRAL

    def test_regulator_cannot_settle_a_return_promise(self) -> None:
        claim = make_claim("35% monthly returns", ClaimType.RETURN_PROMISE)
        target = build_target(claim, ())
        result = make_result(
            "Acme Capital returns",
            "https://sebi.gov.in/returns",
            snippet="Acme Capital returns 35% monthly. Registered entity.",
        )
        assessed = assess_result(result, target)
        assert assessed.tier is SourceTier.TIER_1_PRIMARY_REGULATOR
        assert assessed.claim_relevant is False
        assert assessed.stance == Stance.NEUTRAL

    def test_matching_identity_and_support_gives_support(self) -> None:
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        result = make_result(
            "Acme Capital registration",
            "https://sebi.gov.in/intermediaries/acme",
            snippet="Acme Capital is registered as an investment adviser.",
        )
        assessed = assess_result(result, target)
        assert assessed.identity is IdentityMatch.MATCHED
        assert assessed.claim_relevant is True
        assert assessed.stance == Stance.SUPPORTS
        assert assessed.matched_cue

    def test_matching_identity_and_contradiction_gives_contradiction(self) -> None:
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        result = make_result(
            "Acme Capital registration",
            "https://sebi.gov.in/orders/acme",
            snippet="The registration of Acme Capital was cancelled.",
        )
        assessed = assess_result(result, target)
        assert assessed.stance == Stance.CONTRADICTS

    def test_unregistered_government_host_cannot_settle_a_claim(self) -> None:
        """An authoritative tier without a registry entry is uncitable."""
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        result = make_result(
            "Acme Capital registration",
            "https://unlisted-department.gov.in/acme",
            snippet="Acme Capital is registered as an investment adviser.",
        )
        assessed = assess_result(result, target)
        assert assessed.tier is SourceTier.TIER_2_GOVERNMENT
        assert assessed.authority_key is None
        assert assessed.claim_relevant is False

    def test_rank_orders_by_tier_then_position(self) -> None:
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        first = assess_result(
            make_result(
                "Acme Capital", "https://nseindia.com/a", snippet="Acme Capital.",
                position=1,
            ),
            target,
        )
        second = assess_result(
            make_result(
                "Acme Capital", "https://sebi.gov.in/b", snippet="Acme Capital.",
                position=5,
            ),
            target,
        )
        assert second.rank < first.rank

    def test_source_type_is_classified_consistently(self) -> None:
        """Assessment and Phase 3 classification never disagree about a host."""
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        assessed = assess_result(
            make_result("Acme", "https://sebi.gov.in/x"), target
        )
        assert assessed.tier is SourceTier.TIER_1_PRIMARY_REGULATOR
        assert classify_domain("sebi.gov.in") is SourceType.REGULATOR


class TestAssessResults:
    """Batch assessment preserves order."""

    def test_one_assessment_per_result(self) -> None:
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        results = (
            make_result("One", "https://sebi.gov.in/1"),
            make_result("Two", "https://example.com/2"),
        )
        assessed = assess_results(results, target)
        assert [item.title for item in assessed] == ["One", "Two"]
        assert all(isinstance(item, AssessedSource) for item in assessed)

    def test_empty_input(self) -> None:
        entity = make_entity("Acme Capital")
        target = build(entities=(entity,))
        assert assess_results((), target) == ()