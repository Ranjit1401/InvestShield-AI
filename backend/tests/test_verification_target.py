"""Tests for verification targets (Phase 4, §3).

A target records what verification *would* look for. These tests assert that
resolution is pure — no network, no provider — and that it refuses to pretend a
claim is checkable when it is not.
"""

from __future__ import annotations

from app.schemas.claims import ClaimType
from app.schemas.entities import EntityType
from app.services.verification.target import (
    NON_VERIFIABLE_CLAIM_TYPES,
    REGISTRATION_STYLE_CLAIM_TYPES,
    build_target,
    identifiers_for,
    identity_entities_for,
    normalized_subject,
)
from tests.verification_factories import make_claim, make_entity


class TestBuildTarget:
    """Resolving a claim into a look-up plan."""

    def test_regulatory_claim_resolves_authorities_and_queries(self) -> None:
        entity = make_entity("Acme Capital Advisors")
        claim = make_claim(
            "Acme Capital Advisors is SEBI registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id,),
        )
        target = build_target(claim, (entity,))

        assert target.is_applicable is True
        assert target.has_identity is True
        assert target.subject == "Acme Capital Advisors"
        assert target.preferred_authorities
        assert target.search_queries
        assert len(target.search_queries) <= 5

    def test_non_applicable_claim_produces_no_queries(self) -> None:
        for claim_type in sorted(NON_VERIFIABLE_CLAIM_TYPES, key=lambda item: item.value):
            claim = make_claim("Invest now and double your money.", claim_type)
            target = build_target(claim, ())
            assert target.is_applicable is False
            assert target.search_queries == ()
            assert target.preferred_authorities == ()

    def test_return_promise_is_applicable_but_has_no_authorities(self) -> None:
        """The claim is searched, but no official record can settle it.

        A promised return is not a registration fact, so the registry offers
        nothing relevant: the query set is generic and contains no `site:`
        restriction, which is why such a claim can only ever come back as
        `INSUFFICIENT_EVIDENCE`.
        """
        claim = make_claim(
            "We guarantee 35% monthly returns.", ClaimType.RETURN_PROMISE
        )
        target = build_target(claim, ())
        assert target.is_applicable is True
        assert target.preferred_authorities == ()
        assert target.search_queries
        assert all("site:" not in query for query in target.search_queries)
        assert all("SEBI" not in query for query in target.search_queries)

    def test_subject_falls_back_to_claim_text(self) -> None:
        claim = make_claim(
            "The scheme is registered with SEBI.", ClaimType.REGULATORY_STATUS
        )
        target = build_target(claim, ())
        assert target.has_identity is False
        assert target.subject == "The scheme is registered with SEBI."

    def test_preferred_source_types_mirror_the_authorities_tiers(self) -> None:
        claim = make_claim("Acme is registered.", ClaimType.REGULATORY_STATUS)
        target = build_target(claim, ())
        assert target.preferred_source_types
        assert all(
            source_type.value for source_type in target.preferred_source_types
        )

    def test_target_is_deterministic(self) -> None:
        entity = make_entity("Acme Capital")
        claim = make_claim(
            "Acme Capital is registered.", ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id,),
        )
        first = build_target(claim, (entity,))
        second = build_target(claim, (entity,))
        assert first == second

    def test_registration_style_flag_matches_the_declared_set(self) -> None:
        assert REGISTRATION_STYLE_CLAIM_TYPES == frozenset(
            {
                ClaimType.REGULATORY_STATUS,
                ClaimType.CREDENTIAL_CLAIM,
                ClaimType.OWNERSHIP_CLAIM,
            }
        )
        claim = make_claim("Acme is registered.", ClaimType.REGULATORY_STATUS)
        assert build_target(claim, ()).is_registration_style is True

        promise = make_claim("30% a month.", ClaimType.RETURN_PROMISE)
        assert build_target(promise, ()).is_registration_style is False

    def test_entity_ids_are_preserved_verbatim(self) -> None:
        entity = make_entity("Acme Capital")
        claim = make_claim(
            "Acme Capital is registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id,),
        )
        target = build_target(claim, (entity,))
        assert target.entity_ids == ("entity_001",)

    def test_normalized_subject_uses_entity_normalisation(self) -> None:
        entity = make_entity("Acme Capital Pvt. Ltd.")
        claim = make_claim(
            "Acme Capital Pvt. Ltd. is registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id,),
        )
        assert normalized_subject(build_target(claim, (entity,))) == "acme capital private limited"

    def test_normalized_subject_of_text_only_target(self) -> None:
        claim = make_claim("Acme Capital is registered.", ClaimType.REGULATORY_STATUS)
        assert normalized_subject(build_target(claim, ())) == "acme capital is registered"


class TestIdentitySelection:
    """Only linked entities that can name a party drive identity matching."""

    def test_non_identity_entities_are_ignored(self) -> None:
        upi = make_entity("acmefunds@okhdfcbank", EntityType.UPI_ID, entity_id="entity_002")
        claim = make_claim(
            "Pay acmefunds@okhdfcbank today.", ClaimType.PAYMENT_INSTRUCTION,
            entity_ids=(upi.id,),
        )
        assert identity_entities_for(claim, (upi,)) == ()
        assert build_target(claim, (upi,)).has_identity is False

    def test_identity_entities_follow_link_order(self) -> None:
        first = make_entity("Acme Capital", entity_id="entity_001")
        second = make_entity("Acme Holdings", entity_id="entity_002")
        claim = make_claim(
            "Acme Capital and Acme Holdings are registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(second.id, first.id),
        )
        chosen = identity_entities_for(claim, (first, second))
        assert [entity.id for entity in chosen] == ["entity_002", "entity_001"]

    def test_duplicate_mentions_are_collapsed(self) -> None:
        entity = make_entity("Acme Capital")
        claim = make_claim(
            "Acme Capital and Acme Capital are registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id, entity.id),
        )
        assert len(identity_entities_for(claim, (entity,))) == 1

    def test_unknown_entity_ids_are_skipped(self) -> None:
        claim = make_claim(
            "Acme is registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=("entity_missing",),
        )
        assert identity_entities_for(claim, ()) == ()

    def test_person_entities_are_identity(self) -> None:
        person = make_entity("Anita Rao", EntityType.PERSON)
        claim = make_claim(
            "Anita Rao is a SEBI registered advisor.",
            ClaimType.CREDENTIAL_CLAIM,
            entity_ids=(person.id,),
        )
        assert len(identity_entities_for(claim, (person,))) == 1


class TestIdentifiers:
    """Identifiers travel verbatim; a modified identifier is a different one."""

    def test_registration_number_is_collected_verbatim(self) -> None:
        number = make_entity(
            "INH0000123456", EntityType.REGISTRATION_NUMBER, entity_id="entity_002"
        )
        claim = make_claim(
            "Registration INH0000123456 is valid.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(number.id,),
        )
        assert identifiers_for(claim, (number,)) == ("INH0000123456",)

    def test_identifiers_are_deduplicated_exactly(self) -> None:
        first = make_entity("INH0000123456", EntityType.REGISTRATION_NUMBER, entity_id="e1")
        second = make_entity("INH0000123456", EntityType.REGISTRATION_NUMBER, entity_id="e2")
        claim = make_claim(
            "INH0000123456 twice.", ClaimType.REGULATORY_STATUS, entity_ids=("e1", "e2")
        )
        assert identifiers_for(claim, (first, second)) == ("INH0000123456",)

    def test_case_differences_are_distinct_identifiers(self) -> None:
        upper = make_entity("INH0000123456", EntityType.REGISTRATION_NUMBER, entity_id="e1")
        lower = make_entity("inh0000123456", EntityType.REGISTRATION_NUMBER, entity_id="e2")
        claim = make_claim(
            "both ids", ClaimType.REGULATORY_STATUS, entity_ids=("e1", "e2")
        )
        assert identifiers_for(claim, (upper, lower)) == (
            "INH0000123456",
            "inh0000123456",
        )

    def test_no_entities_means_no_identifiers(self) -> None:
        claim = make_claim("Nothing to cite.", ClaimType.REGULATORY_STATUS)
        assert identifiers_for(claim, ()) == ()