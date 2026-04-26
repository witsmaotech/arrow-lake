"""Tests for health monitoring on Lake admin mixin."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from arrow_lake._lake_admin import _LakeAdminMixin
from arrow_lake.config import ArrowLakeConfig


def _make_lake(base_uri="./data"):
    config = ArrowLakeConfig()
    config.storage.base_uri = base_uri
    obj = _LakeAdminMixin()
    obj._config = config
    obj._base_uri = base_uri
    obj._components = {}
    obj._start_time = time.monotonic()
    obj._get_storage = MagicMock()
    obj._get_component = MagicMock()
    return obj


class TestHealth:
    def test_ok_for_local_storage(self, tmp_path):
        lake = _make_lake(str(tmp_path))
        lake._components = {}
        result = lake.health()
        assert result.status == "ok"
        assert result.storage_ok is True
        assert result.uptime_seconds >= 0

    def test_degraded_for_missing_storage(self):
        lake = _make_lake("/nonexistent/path/that/does/not/exist")
        lake._components = {}
        result = lake.health()
        assert result.status == "degraded"
        assert result.storage_ok is False

    def test_includes_version(self, tmp_path):
        lake = _make_lake(str(tmp_path))
        result = lake.health()
        assert isinstance(result.version, str)
        assert len(result.version) > 0

    def test_session_pool_none_when_not_initialized(self, tmp_path):
        lake = _make_lake(str(tmp_path))
        lake._components = {}
        result = lake.health()
        assert result.session_pool is None

    def test_session_pool_populated_when_initialized(self, tmp_path):
        lake = _make_lake(str(tmp_path))
        mock_sm = MagicMock()
        mock_stats = MagicMock(
            pool_size=5,
            active_sessions=2,
            queued_requests=0,
            total_queries=100,
            total_errors=1,
            total_timeouts=0,
            avg_wait_seconds=0.05,
            slow_query_count=2,
        )
        mock_sm.get_stats.return_value = mock_stats
        lake._components = {"session_manager": mock_sm}
        result = lake.health()
        assert result.session_pool is not None
        assert result.session_pool["pool_size"] == 5
        assert result.session_pool["active_sessions"] == 2
