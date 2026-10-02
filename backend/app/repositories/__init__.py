"""Persistence layer (Phase 9).

Repositories are the only place that maps a Phase 1-7 object to a database row.
Routes and services above them deal in domain models, so a schema change is
confined to `app/models/` and this package.

The session is always supplied by the caller — a FastAPI dependency in
production, a test fixture elsewhere. No repository builds its own engine or
reaches for a module-level global, because a global engine is resolved from
whatever `DATABASE_URL` happened to be set at import time and would silently
ignore the settings the running application was actually built with.
"""

from app.repositories.investigations import (
    InvestigationRepository,
    InvestigationSummary,
    NotFound,
)

__all__ = ["InvestigationRepository", "InvestigationSummary", "NotFound"]
