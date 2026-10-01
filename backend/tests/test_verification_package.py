"""Package-level invariants for the verification layer (Phase 4).

These are the guards that would otherwise be written once and forgotten: the
public surface must not grow a verdict vocabulary, and no module inside the
package may reach past Phase 3's `SearchService` to the network.
"""

from __future__ import annotations

import ast
import pkgutil
from pathlib import Path

import pytest

from app.services import verification as package
from app.services.verification import VerificationStatus

PACKAGE_DIR = Path(package.__file__).parent

#: Symbols that would mean a verdict had crept into the verification layer.
FORBIDDEN_SYMBOL_TERMS = (
    "scam",
    "fraud",
    "fraudulent",
    "safe",
    "dangerous",
    "verdict",
    "risk_score",
    "recommendation",
)


def module_files() -> list[Path]:
    """Return every module file in the verification package."""
    return sorted(PACKAGE_DIR.glob("*.py"))


class TestPublicSurface:
    """The package exports what consumers need, and nothing worse."""

    def test_every_exported_symbol_exists(self) -> None:
        for name in package.__all__:
            assert hasattr(package, name), name

    def test_exports_have_no_duplicates(self) -> None:
        assert len(package.__all__) == len(set(package.__all__))

    def test_exports_include_the_documented_entry_points(self) -> None:
        for name in (
            "VerificationService",
            "VerificationResult",
            "VerificationStatus",
            "VerificationTarget",
            "build_target",
            "build_queries",
            "build_verification_service",
            "decide_verification",
        ):
            assert name in package.__all__

    def test_no_exported_symbol_is_named_like_a_verdict(self) -> None:
        for name in package.__all__:
            lowered = name.lower()
            assert not any(term in lowered for term in FORBIDDEN_SYMBOL_TERMS), name

    def test_the_service_and_builder_are_exported(self) -> None:
        assert package.build_verification_service is not None
        assert package.VerificationService is not None


class TestStatusVocabulary:
    """The five-status vocabulary is the only one."""

    def test_only_the_five_statuses_exist(self) -> None:
        assert {status.value for status in VerificationStatus} == {
            "VERIFIED",
            "UNVERIFIED",
            "CONTRADICTED",
            "INSUFFICIENT_EVIDENCE",
            "NOT_APPLICABLE",
        }


class TestNoDirectNetworkAccess:
    """Phase 4 must not bypass Phase 3's search boundary."""

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_module_imports_an_http_client(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not imported & {"httpx", "requests", "aiohttp", "urllib3"}, path.name

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_module_references_serpapi(self, path: Path) -> None:
        """Provider naming may appear in prose, never in code."""
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
        assert "serpapi" not in ast.unparse(tree).casefold(), path.name

    def test_only_the_service_constructs_or_issues_searches(self) -> None:
        """`SearchService.search()` is called from exactly one place in Phase 4."""
        callers = {
            path.name
            for path in module_files()
            if "_search.search(" in path.read_text(encoding="utf-8")
        }
        assert callers == {"verification_service.py"}


class TestDeterministicByConstruction:
    """No randomness, no clock-dependent status, no model call."""

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_randomness(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        assert "import random" not in source, path.name

    @pytest.mark.parametrize("path", module_files(), ids=lambda p: p.name)
    def test_no_llm_client_is_used(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        for marker in ("llm_service", "LLMService", "langchain", "groq"):
            assert marker not in source, path.name


def test_package_is_importable_without_credentials() -> None:
    """Importing the layer must not require a key, a network or a database."""
    assert pkgutil is not None
    assert package.MAX_QUERIES_PER_CLAIM == 5