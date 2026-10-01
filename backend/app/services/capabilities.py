"""Runtime capability probes.

The product must degrade rather than crash (D-009), so the first thing we need
is an honest picture of which optional dependencies and credentials are actually
available in the current process. `/api/health` reports exactly this — note
that it reports *configuration presence*, never any secret value.

Probes here are cheap and side-effect free: no network calls, no model
downloads.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import Settings, get_settings

#: Windows install locations checked when `TESSERACT_CMD` is not set and the
#: binary is not on ``PATH``.
_WINDOWS_TESSERACT_CANDIDATES = (
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
)


@dataclass(frozen=True)
class ServiceStatus:
    """Availability of one optional service.

    Attributes:
        provider: Implementation name, e.g. ``groq`` or ``tesseract``.
        configured: Whether the service has everything it needs to run.
        available: Whether the underlying package/binary is installed.
        healthy: Result of an active probe, or ``None`` when not probed.
        detail: Human-readable explanation; never contains credentials.
    """

    provider: str
    configured: bool
    available: bool
    healthy: bool | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialise for the API response."""
        return asdict(self)


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime.

    Timestamps are stored naive-UTC and serialised with an explicit ``Z``
    suffix, per ``DATABASE_SCHEMA.md``.

    Returns:
        Timezone-aware UTC datetime.
    """
    return datetime.now(timezone.utc)


def module_available(module_name: str) -> bool:
    """Return True when a module can be imported without importing it.

    Args:
        module_name: Importable module name, e.g. ``fitz``.

    Returns:
        Whether the module is installed in this interpreter.
    """
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def resolve_tesseract_cmd(settings: Settings | None = None) -> str | None:
    """Locate the Tesseract executable.

    Resolution order:

    1. ``TESSERACT_CMD`` from settings.
    2. ``tesseract`` on ``PATH``.
    3. Well-known Windows install locations.

    ``OCRService`` (Phase 3) reuses this function so that the binary is
    resolved in exactly one place.

    Args:
        settings: Optional settings override.

    Returns:
        Path to the executable, or ``None`` when it cannot be found.
    """
    resolved = settings or get_settings()

    if resolved.tesseract_cmd.strip():
        candidate = Path(resolved.tesseract_cmd.strip())
        return str(candidate) if candidate.exists() else None

    on_path = shutil.which("tesseract")
    if on_path:
        return on_path

    for candidate in _WINDOWS_TESSERACT_CANDIDATES:
        if candidate.exists():
            return str(candidate)

    return None


def probe_llm(settings: Settings | None = None) -> ServiceStatus:
    """Report LLM availability without contacting the provider.

    Args:
        settings: Optional settings override.

    Returns:
        A :class:`ServiceStatus` describing the LLM service.
    """
    resolved = settings or get_settings()
    available = module_available("langchain_groq") or module_available("groq")

    if resolved.llm_provider not in {"groq"}:
        return ServiceStatus(
            provider=resolved.llm_provider,
            configured=False,
            available=available,
            detail=f"Unknown LLM provider '{resolved.llm_provider}'; extraction falls back to deterministic rules.",
        )

    if not resolved.llm_configured:
        return ServiceStatus(
            provider="groq",
            configured=False,
            available=available,
            detail="GROQ_API_KEY is not set; claim/entity extraction falls back to deterministic rules.",
        )

    if not available:
        return ServiceStatus(
            provider="groq",
            configured=True,
            available=False,
            detail="GROQ_API_KEY is set but no Groq client package is installed.",
        )

    return ServiceStatus(
        provider="groq",
        configured=True,
        available=True,
        healthy=None,
        detail="Configured. Health is verified on first use.",
    )


def probe_search(settings: Settings | None = None) -> ServiceStatus:
    """Report web-search availability without spending quota.

    Args:
        settings: Optional settings override.

    Returns:
        A :class:`ServiceStatus` describing the search service.
    """
    resolved = settings or get_settings()
    available = module_available("httpx")

    if not resolved.search_configured:
        return ServiceStatus(
            provider=resolved.search_provider,
            configured=False,
            available=available,
            detail="SERPAPI_KEY is not set; external verification is skipped and stated as a limitation.",
        )

    return ServiceStatus(
        provider="serpapi",
        configured=True,
        available=available,
        healthy=None,
        detail="Configured. Health is verified on first use.",
    )


def probe_ocr(settings: Settings | None = None) -> ServiceStatus:
    """Report OCR availability (package present and binary found).

    Args:
        settings: Optional settings override.

    Returns:
        A :class:`ServiceStatus` describing the OCR service.
    """
    resolved = settings or get_settings()
    package_ok = module_available("pytesseract") and module_available("PIL")
    binary = resolve_tesseract_cmd(resolved)

    if not package_ok:
        return ServiceStatus(
            provider="tesseract",
            configured=bool(binary),
            available=False,
            detail="pytesseract/Pillow are not installed; screenshot analysis is unavailable.",
        )

    if binary is None:
        return ServiceStatus(
            provider="tesseract",
            configured=False,
            available=False,
            detail="Tesseract binary not found. Set TESSERACT_CMD to its full path.",
        )

    return ServiceStatus(
        provider="tesseract",
        configured=True,
        available=True,
        detail=f"Binary resolved at {binary}",
    )


def probe_pdf(settings: Settings | None = None) -> ServiceStatus:
    """Report PDF text-extraction availability.

    Args:
        settings: Optional settings override.

    Returns:
        A :class:`ServiceStatus` describing the PDF service.
    """
    available = module_available("fitz")
    return ServiceStatus(
        provider="pymupdf",
        configured=available,
        available=available,
        detail="PyMuPDF is installed." if available else "PyMuPDF is not installed; PDF analysis is unavailable.",
    )


def probe_embeddings(settings: Settings | None = None) -> ServiceStatus:
    """Report embedding availability.

    Args:
        settings: Optional settings override.

    Returns:
        A :class:`ServiceStatus` describing the embedding service.
    """
    resolved = settings or get_settings()
    if not resolved.embeddings_enabled:
        return ServiceStatus(
            provider="sentence-transformers",
            configured=False,
            available=False,
            detail="EMBEDDINGS_ENABLED is false; evidence retrieval falls back to lexical similarity.",
        )

    available = module_available("sentence_transformers")
    return ServiceStatus(
        provider="sentence-transformers",
        configured=available,
        available=available,
        detail=(
            "sentence-transformers is installed."
            if available
            else "sentence-transformers is not installed; lexical similarity fallback is used."
        ),
    )


def probe_database(session_factory: object | None = None) -> tuple[bool, str | None]:
    """Check that the configured database is reachable.

    Args:
        session_factory: Optional SQLAlchemy ``sessionmaker`` to use. When
            omitted the application-wide factory is used.

    Returns:
        ``(connected, dialect_or_error)``.
    """
    from sqlalchemy import text

    from app.db.session import SessionLocal

    factory = session_factory or SessionLocal
    try:
        session = factory()  # type: ignore[operator]
        try:
            dialect = session.get_bind().dialect.name
            session.execute(text("SELECT 1"))
            return True, dialect
        finally:
            session.close()
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"{type(exc).__name__}: {exc}"


__all__ = [
    "ServiceStatus",
    "module_available",
    "probe_database",
    "probe_embeddings",
    "probe_llm",
    "probe_ocr",
    "probe_pdf",
    "probe_search",
    "resolve_tesseract_cmd",
    "utc_now",
]
