"""Application configuration.

All runtime configuration is loaded from environment variables / `.env` via
pydantic-settings. Nothing in this project may hard-code secrets, endpoints or
provider credentials (D-012).

Lookup order for `.env`: `./.env` first (so `backend/.env` wins when running
from `backend/`), then `../.env` (repository root).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- Application ----------
    app_name: str = "investshield-ai"
    version: str = "0.1.0"
    environment: str = "local"
    debug: bool = False
    log_level: str = "INFO"
    api_prefix: str = "/api"

    # ---------- Database ----------
    database_url: str = "sqlite:///./data/investshield.db"

    # ---------- CORS ----------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---------- LLM ----------
    llm_provider: str = "groq"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: int = 30
    groq_max_retries: int = 2

    # ---------- Search ----------
    search_provider: str = "serpapi"
    serpapi_key: str = ""
    serpapi_base_url: str = "https://serpapi.com/search"
    search_timeout_seconds: int = 15
    search_max_results: int = 10

    # ---------- Embeddings ----------
    embeddings_enabled: bool = True
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # ---------- OCR ----------
    tesseract_cmd: str = ""
    ocr_languages: str = "eng"

    # ---------- PDF ----------
    pdf_max_pages: int = 100

    # ---------- Uploads ----------
    max_upload_bytes: int = 10 * 1024 * 1024
    allowed_upload_types: str = "image/png,image/jpeg,image/webp,application/pdf"

    # ---------- Risk engine ----------
    risk_weight_guaranteed_return: int = 20
    risk_weight_unrealistic_return: int = 25
    risk_weight_urgency_pressure: int = 15
    risk_weight_fake_regulatory_claim: int = 25
    risk_weight_unverified_adviser: int = 20
    risk_weight_suspicious_url: int = 10
    risk_weight_third_party_payment: int = 15
    risk_weight_apk_download: int = 25
    risk_weight_messaging_investment_group: int = 10
    risk_weight_borrow_to_invest: int = 20
    risk_weight_withdrawal_fee: int = 25
    risk_weight_account_activation_fee: int = 20
    risk_weight_fake_profit_screenshot: int = 15
    risk_weight_impersonation: int = 20
    risk_band_medium_max: int = 20
    risk_band_high_max: int = 50
    risk_band_critical_max: int = 90

    # ---------- Derived helpers ----------
    @property
    def cors_origin_list(self) -> list[str]:
        """Allowed CORS origins parsed from the comma-separated setting."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_upload_type_list(self) -> list[str]:
        """Allowed upload MIME types parsed from the comma-separated setting."""
        return [item.strip().lower() for item in self.allowed_upload_types.split(",") if item.strip()]

    @property
    def is_sqlite(self) -> bool:
        """True when the configured database is SQLite."""
        return self.database_url.startswith("sqlite")

    @property
    def llm_configured(self) -> bool:
        """True when the selected LLM provider has the credentials it needs."""
        if self.llm_provider == "groq":
            return bool(self.groq_api_key.strip())
        return False

    @property
    def search_configured(self) -> bool:
        """True when the selected search provider has the credentials it needs."""
        if self.search_provider == "serpapi":
            return bool(self.serpapi_key.strip())
        return False

    @property
    def data_dir(self) -> Path:
        """Absolute path to the repository-level `data/` directory."""
        return REPO_ROOT / "data"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings.

    Cached so environment parsing happens once per process. Tests may call
    `get_settings.cache_clear()` after mutating the environment.
    """
    return Settings()


__all__ = ["BACKEND_DIR", "REPO_ROOT", "Settings", "get_settings"]
