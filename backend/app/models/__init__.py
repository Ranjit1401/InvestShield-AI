"""SQLAlchemy ORM models (Phase 9).

Tables are declared here and registered on ``Base.metadata`` so that
``create_all()`` picks them up.

Importing this package imports every table module. That import is deliberate and
is what `Database.create_all()` relies on: a model that is never imported is
never registered, and a table that is never registered is silently missing from
the schema. `app/db/session.py` imports this package for exactly that reason.
"""

from app.models.investigation import (
    ClaimEntityLinkRow,
    ClaimRow,
    EntityRow,
    EvidenceItemRow,
    EvidenceResponseRow,
    InvestigationErrorRow,
    InvestigationRow,
    InvestigationWarningRow,
    RedFlagRow,
    RiskAssessmentRow,
    RiskFactorRow,
    SourceRow,
    TimelineEventRow,
    VerificationResultRow,
)

__all__ = [
    "ClaimEntityLinkRow",
    "ClaimRow",
    "EntityRow",
    "EvidenceItemRow",
    "EvidenceResponseRow",
    "InvestigationErrorRow",
    "InvestigationRow",
    "InvestigationWarningRow",
    "RedFlagRow",
    "RiskAssessmentRow",
    "RiskFactorRow",
    "SourceRow",
    "TimelineEventRow",
    "VerificationResultRow",
]