
"""InvestShield AI — FastAPI application factory.

Run locally with:

    uvicorn app.main:app --reload

Layering rule: routes call services, services call agents/tools, and nothing
below a route is allowed to depend on HTTP.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import UNPROCESSABLE_CONTENT, ApiError
from app.api.routes import health_router, investigations_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import Database
from app.graph.context import build_default_context

logger = get_logger(__name__)

DESCRIPTION = """
**Investigate Before You Invest.**

InvestShield AI investigates the *claims* inside suspicious investment content
instead of simply labelling it. It extracts claims and entities, detects
red-flag patterns deterministically, verifies claims against authoritative
sources, connects every claim to traceable evidence, and produces an explainable
investor-safety report.

This API provides information, evidence and risk indicators. **It is not
financial advice.**
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application.

    Args:
        settings: Optional settings override; defaults to cached settings.

    Returns:
        A configured :class:`~fastapi.FastAPI` instance.
    """
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    database = Database(resolved)
    graph_context = build_default_context(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "InvestShield starting",
            extra={"environment": resolved.environment, "version": resolved.version},
        )
        # Schema creation is idempotent (`checkfirst=True` by default) and runs at
        # startup rather than per request, so a request never pays for DDL and a
        # fresh deployment is usable without a separate migration step. It is
        # wrapped because a database that is reachable but not writable must not
        # stop the read-only endpoints from starting — the health check reports
        # the failure, and investigation is a degraded service rather than none.
        try:
            database.create_all()
        except SQLAlchemyError:
            logger.exception("Database schema could not be created; running degraded")
        yield
        database.dispose()
        logger.info("InvestShield shutting down")

    app = FastAPI(
        title="InvestShield AI",
        version=resolved.version,
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.database = database
    app.state.graph_context = graph_context

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix=resolved.api_prefix)
    app.include_router(investigations_router, prefix=resolved.api_prefix)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        """Point clients at the health endpoint and API docs."""
        return {
            "app": resolved.app_name,
            "version": resolved.version,
            "health": f"{resolved.api_prefix}/health",
            "docs": "/docs",
        }

    _register_error_handlers(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    """Attach handlers that return the documented error envelope.

    Stack traces are never returned to clients; details are logged instead.
    """

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.info("Request validation failed", extra={"errors": exc.errors()})
        return JSONResponse(
            status_code=UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "The request could not be validated.",
                    "detail": exc.errors(),
                }
            },
        )

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        logger.info(
            "API request failed",
            extra={"code": exc.code, "status_code": exc.status_code},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred while processing the request.",
                    "detail": None,
                }
            },
        )


app = create_app()

__all__ = ["app", "create_app"]
