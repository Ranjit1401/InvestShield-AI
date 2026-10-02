"""Tests for the `app.services.risk` public surface.

Consumers import from the package, never from a module inside it. These tests hold
that line: every name in `__all__` must resolve, every submodule's public symbols
must be re-exported, and no exported name may read like a verdict or a piece of
advice.

The naming check is not decoration. A symbol called `fraud_score` or
`is_safe_to_invest` in the public API is a claim the product cannot support, and
by the time it reaches a report the name has already been made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas import risk as schemas
from app.services import risk as package

PACKAGE_DIR = Path(package.__file__).parent

#: Symbols that would mean a verdict, an accusation or advice had crept in.
FORBIDDEN_SYMBOL_TERMS = (
    "scam",
    "fraud",
    "fraudulent",
    "safe",
    "legit",
    "verdict",
    "guilty",
    "recommend",
    "advice",
    "buy",
    "sell",
)


class TestPublicSurface:
    def test_every_exported_name_resolves(self) -> None:
        for name in package.__all__:
            assert hasattr(package, name), name

    def test_the_dunder_all_has_no_duplicates(self) -> None:
        assert len(package.__all__) == len(set(package.__all__))

    @pytest.mark.parametrize("name", package.__all__)
    def test_no_exported_symbol_is_named_like_a_verdict(self, name: str) -> None:
        lowered = name.lower()
        assert not any(term in lowered for term in FORBIDDEN_SYMBOL_TERMS), name

    @pytest.mark.parametrize("name", schemas.__all__)
    def test_no_exported_schema_is_named_like_a_verdict(self, name: str) -> None:
        lowered = name.lower()
        assert not any(term in lowered for term in FORBIDDEN_SYMBOL_TERMS), name

    def test_the_service_and_builder_are_exported(self) -> None:
        assert package.RiskService is not None
        assert package.build_risk_service is not None

    def test_the_building_blocks_are_exported(self) -> None:
        """A consumer may need a stage directly, e.g. to explain a score."""
        for name in (
            "factors_from_red_flags",
            "factors_from_verification",
            "absorb_duplicate_signals",
            "score_from_contributions",
            "band_for",
            "weights_from_settings",
            "thresholds_from_settings",
        ):
            assert name in package.__all__, name

    def test_the_warnings_are_exported(self) -> None:
        """A report layer needs the same fixed wording the engine used."""
        for name in (
            "SCORE_NOT_A_PROBABILITY",
            "SEARCH_DATA_UNAVAILABLE",
            "CLAIMS_NOT_CONFIRMED",
            "CLAIMS_CONTRADICTED",
            "SIGNALS_DEDUPLICATED",
        ):
            assert name in package.__all__, name

    def test_the_reason_code_sets_are_exported(self) -> None:
        """Phase 7 will need to tell "could not look" from "looked and found none"."""
        for name in (
            "UNVERIFIED_REASON_CODES",
            "SEARCH_INCOMPLETE_REASON_CODES",
            "REGISTER_EMPTY_REASON_CODES",
        ):
            assert name in package.__all__, name


class TestSchemaReExport:
    @pytest.mark.parametrize("name", schemas.__all__)
    def test_every_schema_symbol_is_re_exported_by_the_package(self, name: str) -> None:
        """Consumers import from the package only, so nothing may be hidden."""
        assert name in package.__all__, name
        assert getattr(package, name) is getattr(schemas, name)

    def test_the_caveat_constant_has_one_definition(self) -> None:
        """Held in the schema, re-exported by the package and service alike.

        Three names, one string. If the service and the schema could hold
        different wordings, a report built from one would disclaim something
        different from the other.
        """
        from app.services.risk import risk_service

        assert package.SCORE_NOT_A_PROBABILITY is schemas.SCORE_NOT_A_PROBABILITY
        assert (
            risk_service.SCORE_NOT_A_PROBABILITY is schemas.SCORE_NOT_A_PROBABILITY
        )


class TestNoVerdictInModuleContents:
    def test_no_module_defines_a_verdict_helper(self) -> None:
        """Belt-and-braces over `__all__`: check what is actually defined."""
        import importlib

        for module_name in (
            "risk_aggregation",
            "risk_factors",
            "risk_scoring",
            "risk_service",
        ):
            module = importlib.import_module(f"app.services.risk.{module_name}")
            for name in vars(module):
                if name.startswith("_"):
                    continue
                lowered = name.lower()
                assert not any(
                    term in lowered for term in FORBIDDEN_SYMBOL_TERMS
                ), f"{module_name}.{name}"

    def test_the_package_docstring_states_the_boundary(self) -> None:
        """The refusal is documented where a reader will meet it."""
        docstring = (package.__doc__ or "").lower()
        assert "no double counting" in docstring
        assert "probability" in docstring
