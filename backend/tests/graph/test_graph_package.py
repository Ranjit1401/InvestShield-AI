"""Tests for the `app.graph` public surface and its boundaries.

The graph's package is the seam between the pipeline and everything that will
consume it — a CLI now, the HTTP layer in Phase 8, the report in Phase 10. These
tests hold three lines:

- Consumers import from `app.graph`, never from a module inside it.
- The graph orchestrates; it does not own domain logic. No module under
  `app/graph/` may compute a score, classify a source, decide a verification
  status or perform a search.
- No exported name reads like a verdict, a probability or advice.

The boundary check is the substantive one. An orchestration layer that grows a
regex or a weight lookup is no longer an orchestration layer, and by the time the
damage shows it will be spread across nodes that were each individually
plausible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import graph as package

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
    "confidence",
    "probability",
    "score",
)

#: Modules the graph must never import, because they own a domain decision the
#: graph is supposed to be delegating.
FORBIDDEN_IMPORTS = (
    "app.services.red_flag_rules",
    "app.services.verification.decision_engine",
    "app.services.verification.comparator",
    "app.services.verification.query_builder",
    "app.services.search.source_classifier",
    "app.services.evidence.evidence_builder",
    "app.services.risk.risk_scoring",
)


class TestPublicSurface:
    """The package re-exports what a consumer needs."""

    def test_every_exported_name_resolves(self) -> None:
        for name in package.__all__:
            assert hasattr(package, name), name

    def test_the_dunder_all_has_no_duplicates(self) -> None:
        assert len(package.__all__) == len(set(package.__all__))

    @pytest.mark.parametrize("name", package.__all__)
    def test_no_exported_symbol_is_named_like_a_verdict(self, name: str) -> None:
        lowered = name.lower()
        assert not any(term in lowered for term in FORBIDDEN_SYMBOL_TERMS), name

    def test_the_entry_points_are_exported(self) -> None:
        assert package.build_investigation_graph is not None
        assert package.run_investigation is not None
        assert package.build_default_context is not None

    def test_the_context_types_are_exported(self) -> None:
        """Tests inject these, so they are part of the contract."""
        assert package.GraphContext is not None
        assert package.GraphDependencies is not None
        assert package.RecordingSearchService is not None

    def test_the_state_types_are_exported(self) -> None:
        for name in (
            "InvestigationState",
            "GraphWarning",
            "GraphError",
            "GraphStage",
            "TimelineEvent",
            "TimelineStatus",
            "InvestigationInputType",
        ):
            assert getattr(package, name) is not None, name


class TestPackageBoundary:
    """The graph orchestrates; it does not own domain decisions."""

    def test_the_expected_modules_exist(self) -> None:
        modules = {path.stem for path in PACKAGE_DIR.glob("*.py")}
        assert {"__init__", "state", "context", "nodes", "edges", "investigation_graph"} <= modules

    def test_no_graph_module_imports_a_domain_decision_module(self) -> None:
        """The single most important boundary test in this file."""
        offenders: list[str] = []
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_IMPORTS:
                if forbidden in source:
                    offenders.append(f"{path.name} imports {forbidden}")
        assert offenders == []

    def test_no_graph_module_calls_a_search_provider(self) -> None:
        """All external access stays inside `SearchService`."""
        offenders = [
            path.name
            for path in PACKAGE_DIR.glob("*.py")
            if "SerpAPIProvider(" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_no_graph_module_reads_a_risk_weight_setting(self) -> None:
        """Weights are resolved by Phase 1's rule table and Phase 6's service."""
        offenders = [
            path.name
            for path in PACKAGE_DIR.glob("*.py")
            if "risk_weight_" in path.read_text(encoding="utf-8")
            or "risk_band_" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_no_graph_module_constructs_a_search_query(self) -> None:
        """Query construction belongs to Phase 4's query builder."""
        from app.services.verification.query_builder import build_queries

        source = "\n".join(
            path.read_text(encoding="utf-8") for path in PACKAGE_DIR.glob("*.py")
        )
        assert build_queries.__name__ not in source

    def test_the_graph_never_issues_a_search_itself(self) -> None:
        """It delegates to Phase 4 and records what Phase 4 retrieved."""
        offenders = [
            path.name
            for path in PACKAGE_DIR.glob("*.py")
            if ".search(" in path.read_text(encoding="utf-8")
            and path.name != "context.py"
        ]
        assert offenders == []


class TestNoGlobalGraphInstance:
    """No module-level compiled graph, so tests cannot leak wiring into each other."""

    def test_no_module_compiles_a_graph_at_import_time(self) -> None:
        """`build_investigation_graph()` must be called, not cached at module level."""
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            if path.name == "investigation_graph.py":
                continue
            assert "= build_investigation_graph()" not in path.read_text(
                encoding="utf-8"
            ), path.name

    def test_the_factory_builds_a_new_graph_each_time(self) -> None:
        assert package.build_investigation_graph() is not package.build_investigation_graph()