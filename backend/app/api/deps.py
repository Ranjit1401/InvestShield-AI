"""Shared FastAPI dependencies."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Request

from app.core.config import Settings, get_settings
from app.db.session import get_db

__all__ = ["get_db", "get_settings_dep", "get_settings_cached"]


@lru_cache(maxsize=1)
def get_settings_cached() -> Settings:
    """Return the process-wide cached settings."""
    return get_settings()


def get_settings_dep(request: Request) -> Settings:
    """FastAPI dependency exposing the settings the app was built with.

    Reads ``app.state.settings`` (set by the application factory) so tests and
    alternate deployments get the settings that actually belong to the running
    app, not the module-level global.

    Args:
        request: Incoming request, used to reach the app instance.

    Returns:
        The :class:`~app.core.config.Settings` for this application.
    """
    return getattr(request.app.state, "settings", None) or get_settings_cached()
