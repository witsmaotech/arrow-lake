"""Tests for RetentionEnforcer — background retention policy enforcement."""

from __future__ import annotations

import threading
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.config.gravitino import GravitinoConfig
from arrow_lake.quality.retention_enforcer import RetentionEnforcer


@pytest.fixture
def gravitino_config() -> GravitinoConfig:
    return GravitinoConfig(
        uri="http://localhost:8090",
        metalake="test",
        lance_catalog_name="cat",
        lance_schema_name="schema",
    )


@pytest.fixture
def storage() -> MagicMock:
    s = MagicMock()
    s.cleanup_versions.return_value = 3
    return s


@pytest.fixture
def enforcer(gravitino_config: GravitinoConfig, storage: MagicMock) -> RetentionEnforcer:
    return RetentionEnforcer(config=gravitino_config, storage=storage)


# ── enforce ──


class TestEnforce:
    def test_no_policies_returns_zero(self, enforcer: RetentionEnforcer) -> None:
        with patch.object(enforcer, "_fetch_retention_policies", return_value={}):
            assert enforcer.enforce() == 0

    def test_enforces_all_policies(self, enforcer: RetentionEnforcer, storage: MagicMock) -> None:
        with patch.object(enforcer, "_fetch_retention_policies", return_value={"ds1": 30, "ds2": 7}):
            total = enforcer.enforce()
        assert total == 6  # 3 per table
        assert storage.cleanup_versions.call_count == 2

    def test_dry_run(self, enforcer: RetentionEnforcer, storage: MagicMock) -> None:
        with patch.object(enforcer, "_fetch_retention_policies", return_value={"ds1": 7}):
            enforcer.enforce(dry_run=True)
        storage.cleanup_versions.assert_called_once_with("ds1", older_than=timedelta(days=7), dry_run=True)

    def test_table_failure_continues(self, enforcer: RetentionEnforcer, storage: MagicMock) -> None:
        storage.cleanup_versions.side_effect = [RuntimeError("fail"), 2]
        with patch.object(enforcer, "_fetch_retention_policies", return_value={"ds1": 7, "ds2": 30}):
            total = enforcer.enforce()
        assert total == 2


# ── enforce_table ──


class TestEnforceTable:
    def test_table_found(self, enforcer: RetentionEnforcer, storage: MagicMock) -> None:
        with patch.object(enforcer, "_fetch_retention_policies", return_value={"ds1": 7}):
            result = enforcer.enforce_table("ds1")
        assert result == 3

    def test_table_not_in_policies(self, enforcer: RetentionEnforcer) -> None:
        with patch.object(enforcer, "_fetch_retention_policies", return_value={"other": 7}):
            result = enforcer.enforce_table("ds1")
        assert result == 0


# ── _enforce_table (internal) ──


class TestEnforceTableInternal:
    def test_zero_days_skipped(self, enforcer: RetentionEnforcer) -> None:
        assert enforcer._enforce_table("ds1", 0) == 0

    def test_negative_days_skipped(self, enforcer: RetentionEnforcer) -> None:
        assert enforcer._enforce_table("ds1", -1) == 0

    def test_cleanup_exception_returns_zero(self, enforcer: RetentionEnforcer, storage: MagicMock) -> None:
        storage.cleanup_versions.side_effect = RuntimeError("disk error")
        assert enforcer._enforce_table("ds1", 7) == 0


# ── lifecycle ──


class TestLifecycle:
    def test_start_creates_thread(self, enforcer: RetentionEnforcer) -> None:
        enforcer.start()
        try:
            assert enforcer._thread is not None
            assert enforcer._thread.daemon is True
        finally:
            enforcer.stop()

    def test_start_idempotent(self, enforcer: RetentionEnforcer) -> None:
        enforcer.start()
        t1 = enforcer._thread
        enforcer.start()
        assert enforcer._thread is t1
        enforcer.stop()

    def test_stop_sets_event(self, enforcer: RetentionEnforcer) -> None:
        enforcer.start()
        enforcer.stop()
        assert enforcer._stop.is_set()
        assert enforcer._thread is None

    def test_stop_without_start(self, enforcer: RetentionEnforcer) -> None:
        enforcer.stop()  # Should not raise


# ── enforce loop exception handling (L82-83) ──


class TestEnforceLoopException:
    """Cover L82-83: except Exception in enforce() loop body."""

    def test_enforce_catches_direct_exception(self, enforcer: RetentionEnforcer) -> None:
        """When _enforce_table itself raises, enforce() catches and continues."""
        with patch.object(
            enforcer, "_enforce_table", side_effect=RuntimeError("unexpected")
        ), \
             patch.object(
            enforcer, "_fetch_retention_policies", return_value={"ds1": 7, "ds2": 30}
        ):
            # Both tables raise — enforce should catch each and return 0
            total = enforcer.enforce()
        assert total == 0
