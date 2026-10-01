"""Tests for configuration loading and derived settings.

Every test isolates from real OS environment variables so the suite behaves the
same on a developer machine (which may have a real `GROQ_API_KEY` exported) as
in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings, get_settings

KEY_ENV_VARS = ("GROQ_API_KEY", "SERPAPI_KEY", "DATABASE_URL", "CORS_ORIGINS")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_safe_without_env_file() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "investshield-ai"
    assert settings.database_url == "sqlite:///./data/investshield.db"
    assert settings.is_sqlite is True
    assert settings.groq_api_key == ""
    assert settings.serpapi_key == ""


def test_llm_and_search_are_unconfigured_without_keys() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_configured is False
    assert settings.search_configured is False


def test_llm_configured_when_key_present() -> None:
    settings = Settings(_env_file=None, groq_api_key="test-key")

    assert settings.llm_configured is True


def test_search_configured_when_key_present() -> None:
    settings = Settings(_env_file=None, serpapi_key="test-key")

    assert settings.search_configured is True


def test_llm_not_configured_for_unknown_provider() -> None:
    settings = Settings(_env_file=None, llm_provider="mystery", groq_api_key="test-key")

    assert settings.llm_configured is False


def test_non_sqlite_database_url_is_detected() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db")

    assert settings.is_sqlite is False


def test_env_vars_populate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    monkeypatch.setenv("SERPAPI_KEY", "from-env-too")

    settings = Settings(_env_file=None)

    assert settings.groq_api_key == "from-env"
    assert settings.llm_configured is True
    assert settings.search_configured is True


def test_cors_origins_parsed_from_comma_separated_string() -> None:
    settings = Settings(_env_file=None, cors_origins="http://a.test, http://b.test ,")

    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_allowed_upload_types_parsed_and_lowercased() -> None:
    settings = Settings(_env_file=None, allowed_upload_types="IMAGE/PNG, application/pdf")

    assert settings.allowed_upload_type_list == ["image/png", "application/pdf"]


def test_data_dir_is_anchored_to_repo_root() -> None:
    settings = Settings(_env_file=None)

    assert settings.data_dir.name == "data"
    assert settings.data_dir.is_absolute()


def test_risk_weights_have_heuristic_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.risk_weight_guaranteed_return == 20
    assert settings.risk_weight_fake_regulatory_claim == 25
    assert settings.risk_band_medium_max < settings.risk_band_high_max < settings.risk_band_critical_max


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


@pytest.mark.parametrize(
    "url",
    ["postgresql+psycopg://user:pw@host/db", "sqlite:///:memory:"],
)
def test_non_relative_urls_pass_through_unchanged(url: str) -> None:
    from app.db.session import resolve_database_url

    assert resolve_database_url(url) == url


def test_absolute_sqlite_url_passed_through(tmp_path: Path) -> None:
    from app.db.session import resolve_database_url

    absolute = (tmp_path / "already.db").as_posix()

    assert resolve_database_url(f"sqlite:///{absolute}") == f"sqlite:///{absolute}"


def test_relative_sqlite_url_is_anchored_to_root(tmp_path: Path) -> None:
    from app.db.session import resolve_database_url

    resolved = resolve_database_url("sqlite:///./data/x.db", repo_root=tmp_path)

    assert resolved.startswith("sqlite:///")
    assert (tmp_path / "data" / "x.db").as_posix() in resolved
