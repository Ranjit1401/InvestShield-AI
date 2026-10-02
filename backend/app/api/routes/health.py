"""Health and service-capability endpoint."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_database_dep, get_settings_dep
from app.core.config import Settings
from app.db.session import Database
from app.services.capabilities import (
    probe_database,
    probe_embeddings,
    probe_llm,
    probe_ocr,
    probe_pdf,
    probe_search,
)

router = APIRouter(tags=["health"])


class ServiceProbe(BaseModel):
    """Availability of one optional service (no secrets exposed)."""

    provider: str
    configured: bool = Field(description="Credentials/settings present for this provider.")
    available: bool = Field(description="Underlying package or binary is installed.")
    healthy: bool | None = Field(default=None, description="Active probe result, if performed.")
    detail: str | None = None


class DatabaseProbe(BaseModel):
    """Database reachability."""

    connected: bool
    dialect: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    """Response body for `GET /api/health`."""

    status: str
    app: str
    version: str
    environment: str
    database: DatabaseProbe
    services: dict[str, ServiceProbe]
    time: datetime


@router.get("/health", response_model=HealthResponse, summary="Service health")
def health(
    settings: Settings = Depends(get_settings_dep),
    database: Database = Depends(get_database_dep),
) -> HealthResponse:
    """Report application health and which optional services are usable.

    An unconfigured service is a healthy state: InvestShield degrades coverage
    and states the limitation rather than failing (D-009).
    """
    connected, detail = probe_database(database.session_factory)
    dialect = database.engine.dialect.name
    database_probe = (
        DatabaseProbe(connected=True, dialect=dialect)
        if connected
        else DatabaseProbe(connected=False, dialect=dialect, error=detail)
    )

    services = {
        "llm": ServiceProbe(**probe_llm(settings).to_dict()),
        "search": ServiceProbe(**probe_search(settings).to_dict()),
        "ocr": ServiceProbe(**probe_ocr(settings).to_dict()),
        "pdf": ServiceProbe(**probe_pdf(settings).to_dict()),
        "embeddings": ServiceProbe(**probe_embeddings(settings).to_dict()),
    }

    overall = "ok" if database_probe.connected else "degraded"

    return HealthResponse(
        status=overall,
        app=settings.app_name,
        version=settings.version,
        environment=settings.environment,
        database=database_probe,
        services=services,
        time=datetime.now(timezone.utc),
    )


__all__ = ["DatabaseProbe", "HealthResponse", "ServiceProbe", "router"]
