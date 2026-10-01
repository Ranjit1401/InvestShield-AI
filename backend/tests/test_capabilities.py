"""Tests for runtime capability probes."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.services.capabilities import (
    module_available,
    probe_embeddings,
    probe_llm,
    probe_ocr,
    probe_pdf,
    probe_search,
    resolve_tesseract_cmd,
)


def test_module_available_detects_stdlib() -> None:
    assert module_available("json") is True


def test_module_available_rejects_unknown_module() -> None:
    assert module_available("definitely_not_a_real_module_xyz") is False


def test_tesseract_resolution_prefers_explicit_setting(tmp_path: Path) -> None:
    fake_binary = tmp_path / "tesseract.exe"
    fake_binary.write_text("stub", encoding="utf-8")

    settings = Settings(_env_file=None, tesseract_cmd=str(fake_binary))

    assert resolve_tesseract_cmd(settings) == str(fake_binary)


def test_tesseract_resolution_returns_none_for_missing_explicit_path() -> None:
    settings = Settings(_env_file=None, tesseract_cmd="C:/nope/tesseract.exe")

    assert resolve_tesseract_cmd(settings) is None


def test_probe_llm_explains_missing_key() -> None:
    status = probe_llm(Settings(_env_file=None, groq_api_key=""))

    assert status.provider == "groq"
    assert status.configured is False
    assert "GROQ_API_KEY" in (status.detail or "")


def test_probe_search_explains_missing_key() -> None:
    status = probe_search(Settings(_env_file=None, serpapi_key=""))

    assert status.provider == "serpapi"
    assert status.configured is False
    assert "SERPAPI_KEY" in (status.detail or "")


def test_probe_unknown_llm_provider_is_reported() -> None:
    status = probe_llm(Settings(_env_file=None, llm_provider="mystery"))

    assert status.configured is False
    assert "mystery" in (status.detail or "")


def test_probe_ocr_never_raises() -> None:
    status = probe_ocr(Settings(_env_file=None))

    assert status.provider == "tesseract"
    assert isinstance(status.available, bool)


def test_probe_pdf_reports_availability() -> None:
    status = probe_pdf(Settings(_env_file=None))

    assert status.provider == "pymupdf"
    assert isinstance(status.available, bool)


def test_probe_embeddings_respects_disable_flag() -> None:
    status = probe_embeddings(Settings(_env_file=None, embeddings_enabled=False))

    assert status.configured is False
    assert "EMBEDDINGS_ENABLED" in (status.detail or "")
