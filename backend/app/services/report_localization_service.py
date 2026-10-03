"""Deterministic report localization (Phase 15).

The investigation is language-independent and this service keeps it
that way. It reads a **canonical, language-neutral**
:class:`~app.schemas.api.InvestigationResponse` — the object every
client already receives — and produces the human-facing layer alone:
the section titles, the controlled-vocabulary labels and the fixed
report prose, in the requested language.

It is deliberately a *presentation* transformer and nothing more.
It does not investigate, search, verify, score, classify or translate
a single canonical fact. The risk score, the risk level, every claim,
entity, red flag, verification verdict, evidence URL and timeline
event are read only to *count* them for the summary; they are never
recomputed, reweighted, reclassified, reworded or translated, and
they are never written back. A localized report is the same
investigation wearing different words, which is the whole safety
guarantee Phase 15 exists to provide.

Localization is **deterministic**. Every string comes from the
dictionaries in `app/locales`; no large-language model is involved,
so no provider outage can change a verdict or fail a report. The one
degradation path is a missing translation, and that degrades to
English by construction: `app.locales.resources_for` deep-merges the
requested language over the English base, so an unfinished
translation reads as English wherever it has nothing of its own.
"""

from __future__ import annotations

from app.locales import deep_merge, resources_for
from app.schemas.api import (
    InvestigationResponse,
    Language,
    LocalizedReport,
)
from app.schemas.verification import VerificationStatus

__all__ = ["ReportLocalizationService", "report_localization_service"]


class ReportLocalizationService:
    """Build the localized presentation layer for a finished investigation.

    The service is stateless. The English base it falls back to is
    injectable so the fallback path is testable, but it defaults to
    the English module in `app/locales`.
    """

    def __init__(self, english_resources: dict | None = None) -> None:
        """Create a localizer.

        Args:
            english_resources: The English base to merge over. Defaults
                to the English module. Injectable only so a test can
                prove the fallback; production always uses the real one.
        """
        self._english = english_resources or resources_for(Language.EN)

    def localize(
        self,
        response: InvestigationResponse,
        language: Language,
        *,
        resources: dict | None = None,
    ) -> LocalizedReport:
        """Localize the presentation layer of one investigation.

        Args:
            response: The canonical, language-neutral response. It is
                read, never mutated.
            language: The language to render in.
            resources: The requested language's resources. Defaults to
                `app.locales.resources_for(language)`. Injectable so a
                test can prove that a missing translation falls back to
                English.

        Returns:
            A :class:`~app.schemas.api.LocalizedReport` carrying the
            translated titles, labels and fixed prose. Every canonical
            fact is left exactly as the caller supplied it.
        """
        requested = resources if resources is not None else resources_for(language)
        merged = deep_merge(self._english, requested)

        return LocalizedReport(
            language=language,
            investigation_id=response.investigation_id,
            sections=dict(merged.get("sections", {})),
            labels={
                vocabulary: dict(values)
                for vocabulary, values in merged.get("labels", {}).items()
            },
            summary=self._summary(response, merged),
            safety_guidance=str(merged.get("fixed_text", {}).get("safety_guidance", "")),
            risk_caveat=str(merged.get("fixed_text", {}).get("risk_caveat", "")),
            disclaimer=str(merged.get("fixed_text", {}).get("disclaimer", "")),
        )

    def _summary(
        self,
        response: InvestigationResponse,
        merged: dict,
    ) -> str:
        """Build the translated one-paragraph summary.

        The summary is the only localized string that carries numbers,
        and the numbers are canonical: the red-flag, claim and verified
        counts and the risk score are read from the response and
        interpolated into the translated template. The risk *level* is
        interpolated as its translated label, not its code, so the
        summary reads naturally in the target language while the
        underlying level stays the canonical enum.

        Args:
            response: The canonical response to count from.
            merged: The English-filled presentation resources.

        Returns:
            The translated summary. When the run produced no risk
            assessment, the "did not complete" template is used
            instead, so a failed run is summarized honestly.
        """
        fixed_text = merged.get("fixed_text", {})
        red_flag_count = len(response.red_flags)
        claim_count = len(response.claims)
        verified_count = sum(
            1
            for result in response.verification_results
            if result.status is VerificationStatus.VERIFIED
        )

        assessment = response.risk_assessment
        if assessment is not None:
            level_label = self._label(merged, "risk_level", assessment.risk_level.value)
            template = str(fixed_text.get("summary_with_risk", ""))
            return template.format(
                risk_level=level_label,
                risk_score=assessment.risk_score,
                red_flag_count=red_flag_count,
                claim_count=claim_count,
                verified_count=verified_count,
            )

        template = str(fixed_text.get("summary_without_risk", ""))
        return template.format(
            red_flag_count=red_flag_count,
            claim_count=claim_count,
        )

    @staticmethod
    def _label(
        merged: dict,
        vocabulary: str,
        value: str,
    ) -> str:
        """Return the translated label for one controlled value.

        Falls back to the canonical value itself when neither the
        requested language nor English carries a label, so a lookup can
        never produce an empty string.

        Args:
            merged: The English-filled presentation resources.
            vocabulary: The vocabulary name (e.g. ``"risk_level"``).
            value: The canonical value (e.g. ``"HIGH"``).

        Returns:
            The translated label, or the canonical value if unlabeled.
        """
        labels = merged.get("labels", {})
        vocabulary_labels = labels.get(vocabulary, {})
        return str(vocabulary_labels.get(value, value))


#: The shared, stateless localizer the API layer uses. Stateless, so a
#: single instance is safe to share across requests.
report_localization_service = ReportLocalizationService()
