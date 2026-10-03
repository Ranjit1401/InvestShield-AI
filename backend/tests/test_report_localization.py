"""Tests for the deterministic report localizer (Phase 15).

The investigation engine is language-independent, and these tests
are what prove the localizer keeps it that way. Three properties
are pinned:

**Only the presentation moves.** A report localized into Hindi
carries the same claims, entities, red flags, verification
verdicts, evidence and risk assessment as the English one — the
same objects, not recomputed copies. A test that fails here means
localization changed a canonical fact.

**Every language degrades to English.** The resource merge is the
one degradation path, so a partial or missing translation reads as
English wherever it has nothing of its own, never as an empty
title or a missing label.

**Nothing is invented.** A label lookup that finds nothing falls
back to the canonical value itself, so a localized report can never
render an empty string where a reader needs a word.

No network, no LLM, no credentials: every string comes from
`app/locales`.
"""

from __future__ import annotations

import pytest

from app.api.adapters import serialize_investigation
from app.locales import deep_merge, resources_for
from app.schemas.api import Language, LocalizedReport
from app.schemas.red_flags import RedFlagCode
from app.schemas.verification import VerificationStatus
from app.services.report_localization_service import (
    ReportLocalizationService,
    report_localization_service,
)
from app.services.risk import RiskService
from tests.risk_factories import make_flag, make_placed_claim, not_applicable, unverified, verified

#: The languages this version renders, for the parametrized cases.
LOCALIZED_LANGUAGES = [Language.HI, Language.MR]


def _state_with_findings() -> dict:
    """A finished-run state carrying one of every canonical collection."""
    return {
        "investigation_id": "inv_localization_test",
        "red_flags": (
            make_flag(),
            make_flag(RedFlagCode.UNREALISTIC_RETURN),
        ),
        "claims": (
            make_placed_claim("First claim."),
            make_placed_claim("Second claim."),
            make_placed_claim("Third claim."),
        ),
        "verification_results": (
            verified(),
            unverified(),
            not_applicable(),
        ),
        "risk_assessment": RiskService().assess(),
    }


def _response(language: Language = Language.EN) -> object:
    """Serialize the fixture state in a language."""
    return serialize_investigation(_state_with_findings(), language=language)


def _responses_for_state(state: dict) -> dict[Language, object]:
    """Serialize one state in every language, so only the language differs.

    Building the state once matters: a risk assessment stamps the
    time it was produced, so two separately built states would differ
    in `assessed_at` and the comparison would test the clock instead
    of the localizer.
    """
    return {
        language: serialize_investigation(state, language=language)
        for language in Language
    }


# -- the presentation layer is localized ------------------------------


@pytest.mark.parametrize("language", LOCALIZED_LANGUAGES)
def test_section_titles_are_translated(language: Language) -> None:
    """A non-English report carries non-English section titles."""
    report = report_localization_service.localize(_response(), language)

    assert report.sections["claims"] != "Claims"
    assert report.sections["claims"] == resources_for(language)["sections"]["claims"]


@pytest.mark.parametrize("language", LOCALIZED_LANGUAGES)
def test_controlled_vocabulary_labels_are_translated(language: Language) -> None:
    """The human-readable form of every controlled value is translated."""
    report = report_localization_service.localize(_response(), language)

    assert report.labels["risk_level"]["HIGH"] != "High"
    assert report.labels["verification_status"]["VERIFIED"] != "Verified"


def test_english_report_carries_the_english_resources() -> None:
    """The base language reads as itself, from the same dictionaries."""
    report = report_localization_service.localize(_response(), Language.EN)

    assert report.sections["risk_assessment"] == "Risk Assessment"
    assert report.labels["risk_level"]["CRITICAL"] == "Critical"
    assert report.language is Language.EN


@pytest.mark.parametrize("language", [Language.EN, *LOCALIZED_LANGUAGES])
def test_fixed_report_prose_is_translated(language: Language) -> None:
    """The fixed prose — guidance, caveat, disclaimer — is localized."""
    report = report_localization_service.localize(_response(), language)

    assert report.safety_guidance
    assert report.risk_caveat
    assert report.disclaimer
    if language is not Language.EN:
        assert report.disclaimer != "InvestShield AI is an investigation tool."


# -- canonical facts never move ----------------------------------------


@pytest.mark.parametrize("language", LOCALIZED_LANGUAGES)
def test_canonical_facts_are_identical_in_every_language(language: Language) -> None:
    """Localizing a report changes no canonical field.

    The claims, entities, red flags, verification verdicts, evidence
    and risk assessment are compared value-for-value against the
    English serialization of the same state.
    """
    responses = _responses_for_state(_state_with_findings())
    english = responses[Language.EN]
    localized = responses[language]

    assert localized.claims == english.claims
    assert localized.entities == english.entities
    assert localized.red_flags == english.red_flags
    assert localized.verification_results == english.verification_results
    assert localized.evidence == english.evidence
    assert localized.risk_assessment == english.risk_assessment
    assert localized.risk_assessment.risk_score == english.risk_assessment.risk_score
    assert localized.risk_assessment.risk_level is english.risk_assessment.risk_level


def test_the_localizer_never_mutates_the_response() -> None:
    """`localize` reads the response; it does not write to it.

    The adapter attaches the English report when the response is
    built, so the assertion is that a Hindi localization leaves the
    response — including its attached English report — untouched.
    """
    response = _response()
    before = response.model_dump()
    attached = response.report

    report_localization_service.localize(response, Language.HI)

    assert response.model_dump() == before
    assert response.report == attached


def test_localizing_twice_produces_the_same_report() -> None:
    """Localization is deterministic: no clock, no provider, no randomness."""
    response = _response()

    first = report_localization_service.localize(response, Language.HI)
    second = report_localization_service.localize(response, Language.HI)

    assert first == second


# -- the summary carries canonical numbers -----------------------------


def test_the_summary_interpolates_the_canonical_counts() -> None:
    """The summary's numbers are read from the response, not invented."""
    report = report_localization_service.localize(_response(), Language.EN)

    # The fixture carries 2 red flags, 3 claims, 1 verified result.
    assert "2" in report.summary
    assert "3" in report.summary


def test_the_summary_names_the_translated_risk_level() -> None:
    """The risk level is interpolated as its label, not its code."""
    hindi = report_localization_service.localize(_response(), Language.HI)
    level = _response().risk_assessment.risk_level
    expected = resources_for(Language.HI)["labels"]["risk_level"][level.value]

    assert expected in hindi.summary
    assert level.value not in hindi.summary


def test_a_run_without_risk_assessment_is_summarized_honestly() -> None:
    """A run that never reached the risk stage says so, in the language."""
    response = serialize_investigation({"investigation_id": "inv_partial"})

    report = report_localization_service.localize(response, Language.HI)

    assert "जांच पूर्ण नहीं हुई" in report.summary


# -- the English fallback ----------------------------------------------


def test_a_partial_translation_falls_back_to_english() -> None:
    """A language with one translated section reads English elsewhere."""
    partial = {"sections": {"claims": "दावे"}}

    report = report_localization_service.localize(
        _response(),
        Language.HI,
        resources=partial,
    )

    assert report.sections["claims"] == "दावे"
    assert report.sections["entities"] == "Entities"
    assert report.labels["risk_level"]["HIGH"] == "High"
    assert report.disclaimer == resources_for(Language.EN)["fixed_text"]["disclaimer"]


def test_missing_resources_read_as_english() -> None:
    """A language with no resources at all is the English report."""
    english = report_localization_service.localize(_response(), Language.EN)
    fallback = report_localization_service.localize(
        _response(),
        Language.HI,
        resources={},
    )

    assert fallback.sections == english.sections
    assert fallback.labels == english.labels
    assert fallback.summary == english.summary


def test_deep_merge_does_not_mutate_its_inputs() -> None:
    """The merge is the fallback rule, so it must be pure."""
    base = {"sections": {"claims": "Claims"}, "labels": {"a": {"B": "b"}}}
    override = {"sections": {"claims": "दावे"}}

    merged = deep_merge(base, override)

    assert merged["sections"]["claims"] == "दावे"
    assert merged["sections"].get("entities") is None
    assert base["sections"]["claims"] == "Claims"
    assert override == {"sections": {"claims": "दावे"}}


def test_a_label_lookup_that_finds_nothing_returns_the_canonical_value() -> None:
    """An unknown controlled value renders as itself, never as empty."""
    report = report_localization_service.localize(_response(), Language.EN)

    assert report.labels["risk_level"].get("NO_SUCH_LEVEL") is None

    service = ReportLocalizationService()
    merged = resources_for(Language.EN)

    assert service._label(merged, "risk_level", "NO_SUCH_LEVEL") == "NO_SUCH_LEVEL"


# -- the adapter attaches the report -----------------------------------


@pytest.mark.parametrize(
    ("language", "code"),
    [(Language.EN, "en"), (Language.HI, "hi"), (Language.MR, "mr")],
)
def test_serialization_attaches_a_report_in_the_requested_language(
    language: Language,
    code: str,
) -> None:
    """Every serialized investigation carries its localized report."""
    response = serialize_investigation(_state_with_findings(), language=language)

    assert isinstance(response.report, LocalizedReport)
    assert response.report.language is language
    assert response.report.language.value == code
    assert response.report.investigation_id == "inv_localization_test"


def test_the_report_is_the_only_field_that_differs_by_language() -> None:
    """The serialized response differs from its English twin in `language`
    and `report` alone — and `language` is only the request echo.
    """
    responses = _responses_for_state(_state_with_findings())
    english = responses[Language.EN]
    hindi = responses[Language.HI]

    english_fields = english.model_dump(exclude={"report", "language"})
    hindi_fields = hindi.model_dump(exclude={"report", "language"})

    assert hindi_fields == english_fields
    assert hindi.report.sections != english.report.sections
