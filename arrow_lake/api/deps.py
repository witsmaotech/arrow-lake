"""Dependency injection for the REST API.

Provides FastAPI Depends-callable factories for Lake instance and config.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Request

from arrow_lake.config import ArrowLakeConfig


@lru_cache(maxsize=1)
def get_config() -> ArrowLakeConfig:
    """Return cached application config (singleton per process)."""
    return ArrowLakeConfig()


def get_lake(request: Request):
    """Return the Lake instance bound to this app (set in lifespan)."""
    return request.app.state.lake
