
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
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import UNPROCESSABLE_CONTENT, ApiError
from app.api.routes import health_router, investigations_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import Database
from app.graph.context import build_default_context

logger = get_logger(__name__)

#: Keys copied from a pydantic validation error into the response `detail`.
#:
#: `input` is deliberately **not** among them. It holds the value the client sent,
#: which is why two of the three exclusions matter: echoing it reflects the whole
#: request body back to whoever sent it, and for a client that mistakenly puts a
#: credential in a field this API forbids, that reflection hands the credential
#: straight back in the error. `ctx` can hold the same class of value, and `url`
#: is documentation metadata.
_ALLOWED_VALIDATION_KEYS: tuple[str, ...] = ("type", "loc", "msg")

#: Stable codes for the failures the router itself raises, so a client can branch
#: on `error.code` rather than on the status number.
_HTTP_ERROR_CODES: dict[int, str] = {
    404: "ROUTE_NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}

#: Fixed wording per framework status. Says what happened to the request; never
#: anything about the deployment, which is the usual source of detail in a
#: framework's default body.
_HTTP_ERROR_MESSAGES: dict[int, str] = {
    404: "No endpoint matches that path and method.",
    405: "That endpoint does not accept this method.",
    401: "Authentication is required for that endpoint.",
    403: "That endpoint is not available.",
}


def _http_message(status_code: int) -> str:
    """Return fixed wording for a framework-raised HTTP failure.

    Args:
        status_code: The status the router raised with.

    Returns:
        Wording safe to surface, generic when the status has none of its own.
    """
    return _HTTP_ERROR_MESSAGES.get(
        status_code, "The request could not be handled as sent."
    )


def _validation_detail(exc: RequestValidationError) -> list[dict[str, object]]:
    """Reduce validation errors to the keys a client may see.

    Pydantic's `errors()` cannot be handed straight to `JSONResponse`. Its `input`
    is the submitted value, and for a request whose body is not a JSON object at
    all — a client that posted form data to a JSON endpoint, which is the most
    common shape of that mistake — that value is raw `bytes`. `json.dumps` then
    raises `TypeError` *inside the error handler*, which escapes as a `500`
    `INTERNAL_ERROR`: a malformed request reported as a server fault.

    Copying only `type`, `loc` and `msg` fixes both halves at once. The detail
    stays JSON-serialisable by construction rather than by luck, and no submitted
    value is reflected back to the sender.

    Args:
        exc: The validation error FastAPI raised.

    Returns:
        One dict per validation error, containing only the allowed keys.
    """
    return [
        {key: error[key] for key in _ALLOWED_VALIDATION_KEYS if key in error}
        for error in exc.errors()
    ]

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
                    "detail": _validation_detail(exc),
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

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Render a framework-raised HTTP failure in the documented envelope.

        An unmatched path and a wrong method are raised by the router, below every
        application handler, so without this they escape as Starlette's default
        `{"detail": ...}`. That is the one failure shape a client would meet that
        its error handling does not cover — and the likeliest one a client meets,
        because a mistyped URL is the most common mistake there is.
        """
        logger.info(
            "Framework HTTP error",
            extra={"status_code": exc.status_code},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR"),
                    "message": _http_message(exc.status_code),
                    "detail": None,
                }
            },
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
