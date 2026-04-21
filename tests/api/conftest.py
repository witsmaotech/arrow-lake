"""Shared test fixtures for the REST API tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.fixture
def mock_lake() -> MagicMock:
    """Return a mock Lake instance for isolated API tests."""
    lake = MagicMock()
    lake.version.return_value = "1.0.0"
    lake.catalog.return_value = MagicMock()
    return lake


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    """Return an async HTTP client wired to the FastAPI test app."""
    from arrow_lake.api.app import create_app

    app: FastAPI = create_app()
    app.state.lake = mock_lake
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
