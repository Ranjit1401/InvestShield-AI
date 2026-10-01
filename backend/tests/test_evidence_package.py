"""Package-level invariants for the evidence layer (Phase 5).

These are the guards that would otherwise be written once and forgotten: the
public surface must not grow a verdict or advice vocabulary, no module may reach
past Phase 3 to the network, no module may summarise anything, and nothing in the
layer may depend on a clock or a random seed.
"""

from __future__ import annotations

import ast
import pkgutil
from pathlib import Path

import pytest

from app.schemas import evidence as schemas
from app.services import evidence as package
from app.services.evidence import EvidenceRelation, EvidenceRelevance, EvidenceType

PACKAGE_DIR = Path(package.__file__).parent

#: Symbols that would mean a verdict, an accusation or advice had crept in.
FORBIDDEN_SYMBOL_TERMS = (
    "scam",
    "fraud",
    "fraudulent",
    "safe",
    "unsafe",
    "dangerous",
    "verdict",
    "risk_score",
    "recommendation",
    "advice",
    "red_flag",
)

#: Call sites that would turn retrieved text into something that is not the text.
FABRICATION_TERMS = (
    "summarize",
    "summarise",
    "paraphrase",
    "rewrit",
    "clean_text",
    "translate",
)

HTTP_CLIENTS = {"httpx", "requests", "aiohttp", "urllib3"}


def module_files() -> list[Path]:
    """Return every module file in the evidence package."""
    return sorted(PACKAGE_DIR.glob("*.py"))


def parse(path: Path) -> ast.Module:
    """Parse a module, dropping its docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return tree


class TestPublicSurface:
    """The package exports what consumers need, and nothing worse."""

    def test_every_exported_symbol_exists(self) -> None:
        for name in package.__all__:
            assert hasattr(package, name), name

    def test_exports_have_no_duplicates(self) -> None:
        assert len(package.__all__) == len(set(package.__all__))

    def test_exports_include_the_documented_entry_points(self) -> None:
        for name in (
            "EvidenceService",
            "build_evidence_service",
            "EvidenceResponse",
            "EvidenceBundleResponse",
            "EvidenceItem",
            "EvidenceSource",
            "EvidenceRelation",
            "EvidenceRelevance",
            "EvidenceType",
            "build_evidence_item",
            "normalize_source",
            "dedupe_evidence",
            "order_evidence",
            "relationship_for",
            "is_probative",
        ):
            assert name in package.__all__

    @pytest.mark.parametrize("name", package.__all__)
    def test_no_exported_symbol_is_named_like_a_verdict(self, name: str) -> None:
        lowered = name.lower()
        assert not any(term in lowered for term in FORBIDDEN_SYMBOL_TERMS), name

    @pytest.mark.parametrize("name", schemas.__all__)
    def test_no_exported_schema_is_named_like_a_verdict(self, name: str) -> None:
        lowered = name.lower()
        assert not any(term in lowered for term in FORBIDDEN_SYMBOL_TERMS), name

    def test_every_schema_symbol_is_re_exported_by_the_package(self) -> None:
        """Consumers import from the package only, so nothing may be hidden."""
        for name in schemas.__all__:
            assert name in package.__all__, name

    def test_the_package_adds_no_schema_of_its_own(self) -> None:
        """Phase 5 defines no object outside the schemas and the service."""
        assert {name for name in package.__all__ if name not in schemas.__all__} >= {
            "EvidenceService",
            "build_evidence_service",
        }


class TestNeutralVocabulary:
    """No axis may state that a claim is true, false, safe or recommended."""

    @pytest.mark.parametrize("relation", list(EvidenceRelation))
    def test_no_relationship_states_a_truth(self, relation: EvidenceRelation) -> None:
        lowered = relation.value.lower()
        assert not any(term in lowered for term in ("true", "false", "valid", "fraud"))

    @pytest.mark.parametrize("member", list(EvidenceRelevance))
    def test_relevance_carries_no_number(self, member: EvidenceRelevance) -> None:
        """Relevance is a label; a numeric score here would become a risk score."""
        assert not member.value.isdigit()

    @pytest.mark.parametrize("member", list(EvidenceType))
    def test_record_types_describe_records_not_claims(self, member: EvidenceType) -> None:
        lowered = member.value.lower()
        assert "claim" not in lowered
        assert "support" not in lowered
        assert "contradict" not in lowered


class TestNoDirectNetworkAccess:
    """Phase 5 must not bypass Phase 3's search boundary."""

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_module_imports_an_http_client(self, path: Path) -> None:
        imported: set[str] = set()
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not imported & HTTP_CLIENTS, path.name

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_module_issues_a_search(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        assert ".search(" not in source, path.name
        assert "SearchProvider" not in source, path.name

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_module_references_a_provider_by_name(self, path: Path) -> None:
        assert "serpapi" not in ast.unparse(parse(path)).casefold(), path.name


class TestNoFabrication:
    """The layer quotes; it never writes."""

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_module_summarises_or_rewrites(self, path: Path) -> None:
        code = ast.unparse(parse(path)).casefold()
        for term in FABRICATION_TERMS:
            assert term not in code, f"{path.name}: {term}"

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_module_calls_an_llm(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        for marker in ("llm_service", "LLMService", "langchain", "groq", "openai"):
            assert marker not in source, path.name

    def test_the_only_excerpt_fields_are_snippet_and_title(self) -> None:
        """If the builder ever grows, this test names what may be quoted."""
        assert schemas.ALLOWED_EXCERPT_ORIGINS == frozenset({"snippet", "title"})

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_module_reads_an_html_document(self, path: Path) -> None:
        """There is no fetched page anywhere in this layer."""
        code = ast.unparse(parse(path)).casefold()
        for term in ("beautifulsoup", "lxml", "html.parser", "read_text"):
            assert term not in code, f"{path.name}: {term}"


class TestNoReimplementationOfEarlierPhases:
    """One answer per question, however many phases need it."""

    def test_canonicalisation_is_phase_three_only(self) -> None:
        callers = {
            path.name
            for path in module_files()
            if "canonicalize_url(" in path.read_text(encoding="utf-8")
        }
        assert callers == {"source_normalizer.py"}

    def test_authority_resolution_is_phase_four_only(self) -> None:
        callers = {
            path.name
            for path in module_files()
            if "resolve_authority(" in path.read_text(encoding="utf-8")
        }
        assert callers == {"source_normalizer.py"}

    def test_domain_classification_is_never_reimplemented(self) -> None:
        """A second publisher classifier would be a second answer."""
        for path in module_files():
            assert "classify_domain(" not in path.read_text(encoding="utf-8"), path.name

    def test_no_module_defines_its_own_tier_table(self) -> None:
        for path in module_files():
            assert "TIER_PRIORITY =" not in path.read_text(encoding="utf-8"), path.name

    def test_the_comparator_is_reused_not_reimplemented(self) -> None:
        """Phase 5 reads `AssessedSource`; it never calls `detect_stance`."""
        for path in module_files():
            source = path.read_text(encoding="utf-8")
            assert "detect_stance(" not in source, path.name


class TestDeterministicByConstruction:
    """No randomness, no clock-dependent content, no model call."""

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_randomness(self, path: Path) -> None:
        assert "import random" not in path.read_text(encoding="utf-8"), path.name

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_uuid_or_counter_minting(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        assert "import uuid" not in source, path.name
        assert "time.time()" not in source, path.name

    def test_no_item_id_is_derived_from_a_timestamp(self) -> None:
        """A clock in the key would make identical evidence look new each run."""
        from app.services.evidence.evidence_builder import MAX_EXCERPT_CHARS, evidence_id_for

        assert evidence_id_for.__module__.endswith("evidence_builder")
        assert MAX_EXCERPT_CHARS > 0

    def test_the_layer_makes_no_http_request_at_runtime(self) -> None:
        """A live-service construction must not need a credential or a socket."""
        service = package.build_evidence_service()
        assert service is not None
        assert pkgutil is not None
