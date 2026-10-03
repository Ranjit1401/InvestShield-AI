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
    serpapi_engine: str = "google"
    search_timeout_seconds: int = 15
    search_max_results: int = 10

    # ---------- Embeddings ----------
    embeddings_enabled: bool = True
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # ---------- OCR ----------
    tesseract_cmd: str = ""
    ocr_languages: str = "eng"
    # Upper bound on the text handed to extraction. Longer recovered
    # text is truncated with a recorded limitation, so the pipeline
    # never claims to have read a screenshot it only saw part of.
    ocr_extraction_max_chars: int = 20_000
    # A bound on a single recognition run, so a pathological image
    # cannot hold the investigation open indefinitely.
    ocr_timeout_seconds: int = 30

    # ---------- PDF ----------
    pdf_max_pages: int = 100

    # ---------- Uploads ----------
    max_upload_bytes: int = 10 * 1024 * 1024
    allowed_upload_types: str = "image/png,image/jpeg,image/webp,application/pdf"

    # ---------- URL fetch (Phase 12) ----------
    # URL investigation is the first stage allowed to make an outbound request
    # to an address the user named, so every limit here is a security limit as
    # much as a resource one. They are configuration rather than constants so an
    # operator can tighten them without a code change, and so a test can drive
    # the boundaries without waiting for a real slow response.
    #
    # Timeouts are deliberately short. An investigation is a synchronous HTTP
    # request; a page that has not answered in a few seconds is a limitation to
    # record, not a client to hang on.
    url_fetch_enabled: bool = True
    url_fetch_connect_timeout_seconds: float = 5.0
    url_fetch_read_timeout_seconds: float = 10.0
    url_fetch_total_timeout_seconds: float = 20.0
    # Bounded so a redirect chain cannot be used to exhaust the request budget
    # or to hop through a checked host to reach an unchecked one.
    url_fetch_max_redirects: int = 3
    # Applied while the body is being read, not after it has been buffered, so
    # an oversized page is refused before it is fully downloaded.
    url_fetch_max_bytes: int = 2 * 1024 * 1024
    url_fetch_user_agent: str = "InvestShieldAI/1.0"
    # Only these schemes are fetched. `file:`, `ftp:`, `data:` and
    # `javascript:` are refused rather than normalised away.
    url_fetch_allowed_schemes: str = "http,https"
    url_fetch_allowed_content_types: str = "text/html,application/xhtml+xml"
    # Upper bound on the normalised text handed to extraction. Longer pages are
    # truncated with a recorded limitation, so the pipeline never claims to have
    # read a page it only saw part of.
    url_extraction_max_chars: int = 20_000

    # ---------- Risk engine ----------
    # Heuristic indicator weights (Phase 1). These are tuning knobs for the
    # explainable risk score. They are NOT scientifically validated
    # probabilities of fraud and must never be presented as such (D-007).
    risk_weight_guaranteed_return: int = 20
    risk_weight_unrealistic_return: int = 20
    risk_weight_urgency_pressure: int = 15
    risk_weight_fake_regulatory_claim: int = 25
    risk_weight_unverified_adviser: int = 20
    risk_weight_suspicious_url: int = 10
    risk_weight_third_party_payment: int = 15
    risk_weight_apk_download: int = 15
    risk_weight_telegram_investment_group: int = 10
    risk_weight_whatsapp_investment_group: int = 10
    risk_weight_borrow_to_invest: int = 15
    risk_weight_withdrawal_fee: int = 20
    risk_weight_account_activation_fee: int = 15
    risk_weight_fake_profit_screenshot: int = 10
    risk_weight_impersonation: int = 25

    risk_band_medium_max: int = 20
    risk_band_high_max: int = 50
    risk_band_critical_max: int = 90

    # ---------- Risk engine: verification-derived factors (Phase 6) ----------
    # A `CONTRADICTED` result is the strongest external finding the pipeline can
    # produce, so it carries a weight comparable to the strongest content
    # indicators. It only contributes at all when no Phase 1 red flag already
    # counts the same underlying signal.
    risk_weight_contradicted_claim: int = 25
    # A completed search that found no confirmation for a material claim. Small
    # on purpose: "we did not find a registration" is a gap in the record, not a
    # finding of wrongdoing (D-006).
    risk_weight_unverified_claim: int = 8
    # Verification could not be completed. This is **uncertainty**, not risk, so
    # the default weight is 0: absence of evidence must never raise the score.
    # Raise it only as an explicit product decision, and read the coverage and
    # completeness fields alongside it.
    risk_weight_insufficient_evidence: int = 0

    # The indicator score is capped here so a long document cannot run away with
    # the band scale. The cap is a ceiling, never a rescaling: a document far
    # past the cap scores the cap, not a proportionally larger number.
    risk_score_ceiling: int = 100

    # ---------- Red flag engine thresholds ----------
    # A promised monthly/annual return above these percentages is treated as
    # an unrealistic-return indicator. Thresholds are promotional/promissory
    # thresholds, not fraud determinations.
    unrealistic_monthly_return_threshold: float = 20.0
    unrealistic_annual_return_threshold: float = 50.0
    # "Double your money" is 2x; this threshold flags 3x-and-above claims.
    unrealistic_multiplier_threshold: float = 3.0
    # Claims of very large short-period profits (e.g. "100% profit in 7 days").
    unrealistic_short_period_pct_threshold: float = 100.0
    unrealistic_short_period_days_threshold: int = 30
    # Currency growth claims such as "₹10,000 becomes ₹1,00,000".
    unrealistic_currency_growth_multiple: float = 5.0
    # Character window used when a detector needs nearby context (urgency cues,
    # messaging-platform investment context).
    red_flag_context_window: int = 80

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
    def url_fetch_scheme_list(self) -> list[str]:
        """URL schemes the fetcher will follow, lowercased and without colons."""
        return [
            item.strip().lower().rstrip(":")
            for item in self.url_fetch_allowed_schemes.split(",")
            if item.strip()
        ]

    @property
    def url_fetch_content_type_list(self) -> list[str]:
        """Content types treated as HTML pages, lowercased and without parameters."""
        return [
            item.strip().lower().split(";", 1)[0].strip()
            for item in self.url_fetch_allowed_content_types.split(",")
            if item.strip()
        ]

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
