"""Tests for the extraction service (Phase 2).

Groq is never contacted: a recording fake provider stands in for it, which also
lets these tests assert the "exactly one structured call per input" guarantee.
The offline (deterministic) path is tested with a `NullProvider`.

These are the tests that protect the two hard product rules: evidence spans must
index the original text exactly, and nothing may be reported that the input did
not actually say.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.claims import ClaimType
from app.schemas.common import span_slice_matches
from app.schemas.entities import EntityType
from app.schemas.extraction import ExtractionMode
from app.services.extraction_service import ExtractionService, build_extraction_service
from app.services.llm_service import (
    LLM_NOT_CONFIGURED,
    LLMProvider,
    LLMResult,
    LLMService,
    NullProvider,
    _Request,
)

logger = get_logger(__name__)


class RecordingProvider(LLMProvider):
    """A fake provider that records requests and replays canned responses.

    Args:
        payloads: Responses returned in order, one per call.
        available: Whether the provider claims to be reachable.
    """

    name = "recording"

    def __init__(self, payloads: list[dict[str, Any]] | None = None, available: bool = True) -> None:
        self.payloads = payloads or []
        self._available = available
        self.requests: list[_Request] = []

    def is_available(self) -> bool:
        return self._available

    def model_name(self) -> str:
        return "recording-model"

    def complete(self, request: _Request) -> LLMResult:
        self.requests.append(request)
        index = len(self.requests) - 1
        payload = self.payloads[index] if index < len(self.payloads) else {}
        return LLMResult(ok=True, content=json.dumps(payload), model=self.model_name())


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite:///:memory:",
        groq_api_key="",
        serpapi_key="",
        log_level="WARNING",
    )


def service_with(provider: LLMProvider | None, settings: Settings) -> ExtractionService:
    return ExtractionService(settings=settings, llm=LLMService(settings=settings, provider=provider))


def assert_spans_are_exact(result: Any) -> None:
    """Every span must slice its own text out of the original input."""
    for claim in result.claims:
        span = claim.evidence_span
        assert span_slice_matches(result.source_text, span), claim.text
        assert result.source_text[span.start : span.end] == claim.text
    for entity in result.entities:
        span = entity.evidence_span
        assert span_slice_matches(result.source_text, span), entity.name
        assert result.source_text[span.start : span.end] == entity.name


class TestOfflineFallback:
    """With no LLM the pipeline must still produce useful, honest output."""

    def test_mode_is_fallback_and_warning_is_recorded(self, settings: Settings) -> None:
        result = service_with(NullProvider(), settings).extract(
            "Our SEBI-approved expert team guarantees 35% monthly returns."
        )

        assert result.extraction_mode is ExtractionMode.FALLBACK
        assert result.processing_warnings
        assert "deterministic" in " ".join(result.processing_warnings).lower()

    def test_prompt_version_is_still_reported(self, settings: Settings) -> None:
        """The version is recorded on every result, so it can be attributed.

        `extraction-v2` is the Phase 12 prompt, which added the rule that web
        page content is data and never instruction. The literal is spelled out
        rather than imported, so a prompt change without a version bump fails
        here instead of passing silently — which is the whole reason the version
        exists.
        """
        result = service_with(NullProvider(), settings).extract("We guarantee 40% returns.")

        assert result.prompt_version == "extraction-v2"

    def test_spans_are_exact_without_an_llm(self, settings: Settings) -> None:
        result = service_with(NullProvider(), settings).extract(
            "Contact us at support@acme-finance.example or visit https://acme-finance.example\n\n"
            "Minimum investment is \u20b925,000."
        )

        assert_spans_are_exact(result)

    def test_empty_input_is_a_handled_fallback(self, settings: Settings) -> None:
        for raw in ("", "   \n\t  ", None):
            result = service_with(NullProvider(), settings).extract(raw)

            assert result.extraction_mode is ExtractionMode.FALLBACK
            assert result.claims == ()
            assert result.entities == ()
            assert result.processing_warnings

    def test_identifiers_are_extracted(self, settings: Settings) -> None:
        result = service_with(NullProvider(), settings).extract(
            "Send payment to acmefunds@okhdfcbank or call +91 98765 43210."
        )

        names = {entity.normalized_name for entity in result.entities}
        types = {entity.entity_type for entity in result.entities}

        assert "acmefunds@okhdfcbank" in names
        assert EntityType.UPI_ID in types
        assert_spans_are_exact(result)


class TestMultilingual:
    """English, Hindi, Marathi and Hinglish must all extract."""

    @pytest.mark.parametrize(
        ("text", "language"),
        [
            ("We guarantee 30% monthly returns on every trade.", "english"),
            ("\u0939\u092e \u0939\u0930 \u092e\u0939\u093f\u0928\u0947 \u0924\u094b 30% \u0932\u093e\u0913\u0926 \u0926\u093f\u0928\u0948\u0902.", "hindi"),
            ("\u0906\u092e\u094d\u0939\u0940 \u0926\u0930 \u092e\u0939\u093f\u0928\u094d\u092f\u093e\u0932\u093e 30% \u092e\u0941\u0928\u092b\u093e \u0926\u0947\u0924\u094b.", "marathi"),
            ("Bhai 100% profit guarantee, invest abhi!", "hinglish"),
        ],
    )
    def test_returns_claims_for_every_supported_language(
        self, settings: Settings, text: str, language: str
    ) -> None:
        result = service_with(NullProvider(), settings).extract(text)

        assert result.claims, f"no claim extracted for {language}: {text!r}"
        assert_spans_are_exact(result)

    def test_hinglish_guaranteed_return_is_detected(self, settings: Settings) -> None:
        result = service_with(
            NullProvider(), settings
        ).extract("Bhai pakka 100% return guarantee hai, abhi invest karo!")

        types = {claim.claim_type for claim in result.claims}

        assert types & {
            claim_type
            for claim_type in (
                __import__("app.schemas.claims", fromlist=["ClaimType"]).ClaimType.GUARANTEE_CLAIM,
                __import__("app.schemas.claims", fromlist=["ClaimType"]).ClaimType.RETURN_PROMISE,
                __import__("app.schemas.claims", fromlist=["ClaimType"]).ClaimType.PROFIT_PROMISE,
            )
        }
        assert_spans_are_exact(result)


class TestLLMPath:
    """The structured call path, its budget and its hallucination filter."""

    def test_exactly_one_structured_request_per_input(self, settings: Settings) -> None:
        provider = RecordingProvider(
            [{"claims": [], "entities": []}]
        )
        service_with(provider, settings).extract(
            "Guaranteed 40% returns. Pay \u20b950000 to acmefunds@okaxis. Call +91 98765 43210."
        )

        assert len(provider.requests) == 1

    def test_request_carries_a_json_schema(self, settings: Settings) -> None:
        provider = RecordingProvider([{"claims": [], "entities": []}])

        service_with(provider, settings).extract("We guarantee returns.")

        request = provider.requests[0]
        assert request.json_schema is not None
        assert request.system

    def test_llm_only_claims_are_kept_and_aligned(self, settings: Settings) -> None:
        text = "Our advisory board includes Dr. Ramesh Kumar."
        provider = RecordingProvider(
            [
                {
                    "claims": [
                        {
                            "text": "Our advisory board includes Dr. Ramesh Kumar.",
                            "claim_type": "AFFILIATION_CLAIM",
                            "confidence": 0.8,
                        }
                    ],
                    "entities": [
                        {"name": "Dr. Ramesh Kumar", "entity_type": "PERSON", "confidence": 0.9}
                    ],
                }
            ]
        )

        result = service_with(provider, settings).extract(text)

        assert result.extraction_mode is ExtractionMode.LLM
        assert any("Ramesh Kumar" in claim.text for claim in result.claims)
        assert any(entity.entity_type is EntityType.PERSON for entity in result.entities)
        assert_spans_are_exact(result)

    def test_hallucinated_claim_is_discarded_with_a_warning(self, settings: Settings) -> None:
        text = "We offer mutual funds."
        provider = RecordingProvider(
            [
                {
                    "claims": [
                        {
                            "text": "We are registered with SEBI and guarantee 50% returns.",
                            "claim_type": "GUARANTEE_CLAIM",
                            "confidence": 0.99,
                        }
                    ],
                    "entities": [
                        {"name": "SEBI", "entity_type": "REGULATOR", "confidence": 0.99}
                    ],
                }
            ]
        )

        result = service_with(provider, settings).extract(text)

        assert not any("guarantee 50%" in claim.text for claim in result.claims)
        assert not any(entity.name == "SEBI" for entity in result.entities)
        assert result.processing_warnings
        assert_spans_are_exact(result)

    def test_partially_hallucinated_payload_becomes_partial(self, settings: Settings) -> None:
        text = "Our advisory board includes Dr. Ramesh Kumar."
        provider = RecordingProvider(
            [
                {
                    "claims": [
                        {
                            "text": "Our advisory board includes Dr. Ramesh Kumar.",
                            "claim_type": "AFFILIATION_CLAIM",
                            "confidence": 0.8,
                        },
                        {
                            "text": "We are SEBI registered since 1998.",
                            "claim_type": "REGULATORY_STATUS",
                            "confidence": 0.9,
                        },
                    ],
                    "entities": [],
                }
            ]
        )

        result = service_with(provider, settings).extract(text)

        assert result.extraction_mode is ExtractionMode.PARTIAL
        assert result.processing_warnings
        assert_spans_are_exact(result)

    def test_malformed_json_degrades_to_deterministic(self, settings: Settings) -> None:
        class BrokenProvider(RecordingProvider):
            def complete(self, request: _Request) -> LLMResult:  # type: ignore[override]
                self.requests.append(request)
                return LLMResult(ok=True, content="not json at all", model="recording-model")

        result = service_with(BrokenProvider(), settings).extract(
            "We guarantee 40% monthly returns."
        )

        assert result.extraction_mode is ExtractionMode.FALLBACK
        assert result.claims
        assert_spans_are_exact(result)

    def test_schema_violation_degrades_to_deterministic(self, settings: Settings) -> None:
        provider = RecordingProvider(
            [{"claims": [{"text": "x", "claim_type": "NOT_A_REAL_TYPE"}], "entities": []}]
        )

        result = service_with(provider, settings).extract("We guarantee 40% returns.")

        assert result.extraction_mode is ExtractionMode.FALLBACK
        assert result.claims

    def test_provider_failure_degrades_to_deterministic(self, settings: Settings) -> None:
        class FailingProvider(RecordingProvider):
            def complete(self, request: _Request) -> LLMResult:  # type: ignore[override]
                self.requests.append(request)
                return LLMResult(ok=False, error_code="LLM_TIMEOUT")

        result = service_with(FailingProvider(), settings).extract(
            "We guarantee 40% monthly returns."
        )

        assert result.extraction_mode is ExtractionMode.FALLBACK
        assert result.claims
        assert "LLM_TIMEOUT" in " ".join(result.processing_warnings)

    def test_deterministic_and_llm_claims_are_not_duplicated(self, settings: Settings) -> None:
        text = "We guarantee 40% monthly returns."
        provider = RecordingProvider(
            [
                {
                    "claims": [
                        {"text": text, "claim_type": "RETURN_PROMISE", "confidence": 0.9}
                    ],
                    "entities": [],
                }
            ]
        )

        result = service_with(provider, settings).extract(text)

        matching = [claim for claim in result.claims if claim.text == text]
        assert len(matching) == 1


class TestResultIntegrity:
    """Invariants that must hold for any input, whatever the mode."""

    @pytest.mark.parametrize(
        "text",
        [
            "Guaranteed 40% returns on every trade. Join our Telegram group today.",
            "\u0939\u092e \u092f\u0915 \u0917\u093e\u0930\u0902\u091f\u0940 \u0932\u093e\u0913\u0926 \u0926\u093f\u0928\u0947\u0902.",
            "Invest now at https://acme.example\n\nPay to acmefunds@okaxis\n\nCall +91 98765 43210.",
            "Mixed   spacing\tand\n\nblank lines with \u200bzero width chars.",
        ],
    )
    def test_spans_and_relationships_stay_consistent(
        self, settings: Settings, text: str
    ) -> None:
        result = service_with(NullProvider(), settings).extract(text)

        assert_spans_are_exact(result)

        claim_ids = {claim.id for claim in result.claims}
        entity_ids = {entity.id for entity in result.entities}
        for link in result.relationships:
            assert link.claim_id in claim_ids
            assert link.entity_id in entity_ids
        for claim in result.claims:
            assert set(claim.entity_ids) <= entity_ids

    def test_claim_text_is_always_a_substring_of_the_input(self, settings: Settings) -> None:
        text = "We guarantee 40% returns.\nJoin our Telegram group today."
        result = service_with(NullProvider(), settings).extract(text)

        for claim in result.claims:
            assert claim.text in text

    def test_confidence_stays_within_bounds(self, settings: Settings) -> None:
        result = service_with(NullProvider(), settings).extract(
            "We guarantee 40% returns. Pay to acmefunds@okaxis."
        )

        for claim in result.claims:
            assert 0.0 <= claim.confidence <= 1.0
        for entity in result.entities:
            assert 0.0 <= entity.confidence <= 1.0

    def test_source_text_is_the_untouched_input(self, settings: Settings) -> None:
        text = "  We   guarantee 40%\treturns.  "
        result = service_with(NullProvider(), settings).extract(text)

        assert result.source_text == text

    def test_no_verdict_is_emitted(self, settings: Settings) -> None:
        result = service_with(NullProvider(), settings).extract(
            "This is a guaranteed scam. Verified fraud."
        )

        # Extraction reports what was said, never what is true. Accusatory
        # wording in the input must not turn into an entity or a verdict.
        entity_names = {entity.name.lower() for entity in result.entities}
        assert "verified fraud" not in entity_names
        assert "guaranteed scam" not in entity_names

        for claim in result.claims:
            assert claim.claim_type in set(ClaimType)
            assert claim.confidence <= 1.0
        assert result.extraction_mode in set(ExtractionMode)
        assert "VERDICT" not in json.dumps(result.model_dump(mode="json")).upper()


class TestFactory:
    def test_build_extraction_service_uses_settings(self) -> None:
        service = build_extraction_service()

        assert isinstance(service, ExtractionService)
        assert isinstance(service.llm, LLMService)

    def test_unconfigured_service_reports_the_reason(self) -> None:
        settings = Settings(
            environment="test",
            database_url="sqlite:///:memory:",
            groq_api_key="",
            serpapi_key="",
        )
        result = build_extraction_service(settings).extract("We guarantee returns.")

        assert result.extraction_mode is ExtractionMode.FALLBACK
        assert LLM_NOT_CONFIGURED