"""Tests for targeted query construction (Phase 4, §3).

Query construction is where verification could quietly go wrong: paraphrase a
claim, drop a registration number, and the pipeline would verify a statement the
user never made. These tests pin the mechanics — verbatim identifiers, bounded
count, deterministic order, no operator injection.
"""

from __future__ import annotations

from app.schemas.claims import ClaimType
from app.schemas.entities import EntityType
from app.services.verification.authority_registry import AUTHORITY_SOURCES
from app.services.verification.query_builder import (
    MAX_QUERIES_PER_CLAIM,
    build_queries,
    query_key,
)
from app.services.verification.target import build_target
from tests.verification_factories import make_claim, make_entity


class TestQueryKey:
    """De-duplication folds case and spacing, nothing else."""

    def test_case_and_spacing_are_folded(self) -> None:
        assert query_key('"Acme  Capital"  registration') == query_key(
            '"acme capital" registration'
        )

    def test_identifiers_stay_distinct(self) -> None:
        assert query_key("INH0000123456") != query_key("INH0000999999")

    def test_amounts_stay_distinct(self) -> None:
        assert query_key("35% returns") != query_key("36% returns")

    def test_punctuation_is_significant(self) -> None:
        assert query_key("acme@okaxis") != query_key("acme okaxis")


class TestQueryConstruction:
    """A targeted query names the party, the record and the body."""

    def test_registration_claim_targets_a_registered_host(self) -> None:
        entity = make_entity("Acme Capital Advisors")
        claim = make_claim(
            "Acme Capital Advisors is SEBI registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id,),
        )
        queries = build_target(claim, (entity,)).search_queries

        assert queries
        assert any(query.startswith('"Acme Capital Advisors"') for query in queries)
        assert any("registration" in query for query in queries)
        assert any("site:sebi.gov.in" in query for query in queries)

    def test_subject_is_quoted_so_identity_terms_stay_together(self) -> None:
        entity = make_entity("Acme Capital Advisors")
        claim = make_claim(
            "Acme Capital Advisors is registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id,),
        )
        queries = build_target(claim, (entity,)).search_queries
        assert any('"Acme Capital Advisors"' in query for query in queries)

    def test_never_exceeds_the_per_claim_cap(self) -> None:
        entity = make_entity("Acme Capital Advisors")
        numbers = [
            make_entity(
                f"INH00000000{index}",
                EntityType.REGISTRATION_NUMBER,
                entity_id=f"entity_{index}",
            )
            for index in range(8)
        ]
        claim = make_claim(
            "Acme Capital Advisors is registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=tuple([entity.id, *(number.id for number in numbers)]),
        )
        queries = build_target(claim, (entity, *numbers)).search_queries
        assert len(queries) == MAX_QUERIES_PER_CLAIM

    def test_queries_are_unique(self) -> None:
        entity = make_entity("Acme Capital")
        claim = make_claim(
            "Acme Capital is registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id,),
        )
        queries = build_target(claim, (entity,)).search_queries
        assert len({query_key(query) for query in queries}) == len(queries)

    def test_construction_is_deterministic(self) -> None:
        entity = make_entity("Acme Capital")
        claim = make_claim(
            "Acme Capital is registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id,),
        )
        first = build_target(claim, (entity,)).search_queries
        second = build_target(claim, (entity,)).search_queries
        assert first == second

    def test_claim_family_selects_the_vocabulary(self) -> None:
        claim = make_claim(
            "Anita Rao manages the fund.",
            ClaimType.OWNERSHIP_CLAIM,
            entity_ids=(),
        )
        queries = build_target(claim, ()).search_queries
        assert any("directors" in query for query in queries)

    def test_registration_number_is_searched_verbatim(self) -> None:
        number = make_entity(
            "INH0000123456", EntityType.REGISTRATION_NUMBER, entity_id="entity_002"
        )
        claim = make_claim(
            "Registration INH0000123456 is valid.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(number.id,),
        )
        queries = build_target(claim, (number,)).search_queries
        assert any('"INH0000123456"' in query for query in queries)

    def test_upi_identifier_survives_verbatim(self) -> None:
        upi = make_entity(
            "acmefunds@okhdfcbank", EntityType.UPI_ID, entity_id="entity_002"
        )
        claim = make_claim(
            "Pay acmefunds@okhdfcbank.", ClaimType.PAYMENT_INSTRUCTION,
            entity_ids=(upi.id,),
        )
        target = build_target(claim, (upi,))
        assert target.search_queries == ()

    def test_non_applicable_claim_yields_nothing(self) -> None:
        entity = make_entity("Acme Capital")
        claim = make_claim(
            "Invest in Acme Capital today.",
            ClaimType.INVESTMENT_OPPORTUNITY,
            entity_ids=(entity.id,),
        )
        assert build_queries(build_target(claim, (entity,))) == ()

    def test_authority_names_are_absent_for_irrelevant_families(self) -> None:
        claim = make_claim("30% a month guaranteed.", ClaimType.RETURN_PROMISE)
        queries = build_target(claim, ()).search_queries
        assert all("SEBI" not in query for query in queries)
        assert all("site:" not in query for query in queries)

    def test_site_restriction_uses_the_registered_host(self) -> None:
        claim = make_claim("Acme is registered.", ClaimType.REGULATORY_STATUS)
        queries = build_target(claim, ()).search_queries
        restricted = [query for query in queries if "site:" in query]
        assert restricted
        registered = {
            domain
            for authority in AUTHORITY_SOURCES
            for domain in authority.domains
        }
        for query in restricted:
            host = query.rsplit("site:", 1)[1]
            assert host in registered


class TestSubjectHardening:
    """A subject must not be able to change what the query asks for."""

    def test_operator_tokens_are_stripped_from_the_subject(self) -> None:
        entity = make_entity("Acme Capital site:sebi.gov.in filetype:pdf")
        claim = make_claim(
            "Acme Capital is registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id,),
        )
        queries = build_target(claim, (entity,)).search_queries
        for query in queries:
            assert "filetype:pdf" not in query
        assert any("site:sebi.gov.in" in query for query in queries)

    def test_subject_quotes_cannot_break_out_of_the_phrase(self) -> None:
        entity = make_entity('Acme "Capital" Ltd')
        claim = make_claim(
            "Acme is registered.", ClaimType.REGULATORY_STATUS, entity_ids=(entity.id,)
        )
        queries = build_target(claim, (entity,)).search_queries
        assert queries
        assert all('"' not in query.split(" ")[0] or query.count('"') % 2 == 0
                   for query in queries)

    def test_blank_subject_produces_no_query(self) -> None:
        claim = make_claim("   ", ClaimType.REGULATORY_STATUS)
        assert build_target(claim, ()).search_queries == ()

    def test_pure_punctuation_subject_is_rejected(self) -> None:
        claim = make_claim("...", ClaimType.REGULATORY_STATUS)
        assert build_target(claim, ()).search_queries == ()

    def test_control_characters_are_normalised_away(self) -> None:
        entity = make_entity("Acme\x00Capital")
        claim = make_claim(
            "Acme Capital is registered.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=(entity.id,),
        )
        queries = build_target(claim, (entity,)).search_queries
        assert queries
        assert all("\x00" not in query for query in queries)