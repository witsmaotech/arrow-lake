"""Tests for MaintenanceScheduler — background compaction and version cleanup."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.ingest.maintenance_scheduler import (
    MaintenanceReport,
    MaintenanceScheduler,
    MaintenanceStatus,
)


# ── Fakes ──


@dataclass(frozen=True)
class _FakeCompactionStats:
    fragments_before: int
    fragments_after: int


@dataclass(frozen=True)
class _FakeStorageConfig:
    maintenance_interval_seconds: int = 3600
    version_retention_days: int = 7
    compaction_fragment_threshold: int = 10


# ── Fixtures ──


@pytest.fixture
def storage() -> MagicMock:
    s = MagicMock()
    s.list_datasets.return_value = ["ds_a", "ds_b"]
    s.compact.return_value = _FakeCompactionStats(fragments_before=20, fragments_after=5)
    s.cleanup_versions.return_value = 3
    s.dataset_uri.return_value = "/data/ds_a.lance"
    s.storage_options = {}
    return s


@pytest.fixture
def config() -> _FakeStorageConfig:
    return _FakeStorageConfig()


@pytest.fixture
def scheduler(storage: MagicMock, config: _FakeStorageConfig) -> MaintenanceScheduler:
    return MaintenanceScheduler(storage=storage, config=config)


# ── MaintenanceReport ──


class TestMaintenanceReport:
    def test_fields(self) -> None:
        r = MaintenanceReport(
            datasets_compacted=2,
            datasets_cleaned=1,
            total_fragments_before=20,
            total_fragments_after=10,
            total_versions_removed=5,
            duration_seconds=1.23,
        )
        assert r.datasets_compacted == 2
        assert r.datasets_cleaned == 1
        assert r.duration_seconds == 1.23

    def test_frozen(self) -> None:
        r = MaintenanceReport(
            datasets_compacted=0,
            datasets_cleaned=0,
            total_fragments_before=0,
            total_fragments_after=0,
            total_versions_removed=0,
            duration_seconds=0.0,
        )
        with pytest.raises(AttributeError):
            r.datasets_compacted = 5  # type: ignore[misc]


# ── MaintenanceStatus ──


class TestMaintenanceStatus:
    def test_fields(self) -> None:
        s = MaintenanceStatus(
            enabled=True,
            last_run="2025-01-01T00:00:00+00:00",
            next_run="2025-01-01T01:00:00+00:00",
            interval_seconds=3600,
            last_report=None,
        )
        assert s.enabled is True
        assert s.interval_seconds == 3600
        assert s.last_report is None

    def test_frozen(self) -> None:
        s = MaintenanceStatus(
            enabled=False, last_run="", next_run="", interval_seconds=0, last_report=None,
        )
        with pytest.raises(AttributeError):
            s.enabled = True  # type: ignore[misc]


# ── run_once ──


class TestRunOnce:
    def test_basic_cycle(self, scheduler: MaintenanceScheduler, storage: MagicMock) -> None:
        with patch("arrow_lake.ingest.maintenance_scheduler.MaintenanceScheduler._count_fragments", return_value=15):
            report = scheduler.run_once()

        assert report.datasets_compacted == 2
        assert report.datasets_cleaned == 2
        assert report.total_fragments_before == 40  # 2 * 20
        assert report.total_fragments_after == 10  # 2 * 5
        assert report.total_versions_removed == 6  # 2 * 3
        assert report.duration_seconds >= 0
        assert scheduler._last_report is report

    def test_below_threshold_skips_compaction(
        self, scheduler: MaintenanceScheduler, storage: MagicMock,
    ) -> None:
        with patch.object(scheduler, "_count_fragments", return_value=5):
            report = scheduler.run_once()

        assert report.datasets_compacted == 0
        assert report.total_fragments_before == 10  # 2 * 5
        assert report.total_fragments_after == 10
        storage.compact.assert_not_called()

    def test_list_datasets_failure(
        self, scheduler: MaintenanceScheduler, storage: MagicMock,
    ) -> None:
        storage.list_datasets.side_effect = RuntimeError("connection lost")
        report = scheduler.run_once()

        assert report.datasets_compacted == 0
        assert report.datasets_cleaned == 0

    def test_compact_failure_does_not_abort_cycle(
        self, scheduler: MaintenanceScheduler, storage: MagicMock,
    ) -> None:
        storage.compact.side_effect = RuntimeError("compact error")
        with patch.object(scheduler, "_count_fragments", return_value=15):
            report = scheduler.run_once()

        # Should still process version cleanup
        assert report.datasets_cleaned == 2
        assert report.datasets_compacted == 0

    def test_cleanup_versions_zero_does_not_increment_cleaned(
        self, scheduler: MaintenanceScheduler, storage: MagicMock,
    ) -> None:
        storage.cleanup_versions.return_value = 0
        with patch.object(scheduler, "_count_fragments", return_value=5):
            report = scheduler.run_once()

        assert report.datasets_cleaned == 0
        assert report.total_versions_removed == 0

    def test_cleanup_versions_failure_does_not_abort(
        self, scheduler: MaintenanceScheduler, storage: MagicMock,
    ) -> None:
        storage.cleanup_versions.side_effect = RuntimeError("vacuum error")
        with patch.object(scheduler, "_count_fragments", return_value=5):
            report = scheduler.run_once()

        assert report.datasets_compacted == 0
        # Should not raise, just log

    def test_last_run_time_updated(self, scheduler: MaintenanceScheduler) -> None:
        assert scheduler._last_run_time == 0.0
        with patch.object(scheduler, "_count_fragments", return_value=5):
            scheduler.run_once()
        assert scheduler._last_run_time > 0

    def test_empty_dataset_list(self, scheduler: MaintenanceScheduler, storage: MagicMock) -> None:
        storage.list_datasets.return_value = []
        report = scheduler.run_once()
        assert report.datasets_compacted == 0
        assert report.total_versions_removed == 0


# ── _count_fragments ──


class TestCountFragments:
    def test_returns_fragment_count(self, scheduler: MaintenanceScheduler, storage: MagicMock) -> None:
        mock_ds = MagicMock()
        mock_ds.get_fragments.return_value = [MagicMock(), MagicMock(), MagicMock()]
        with patch.dict("sys.modules", {"lance": MagicMock(dataset=MagicMock(return_value=mock_ds))}):
            result = scheduler._count_fragments("ds_a")
        assert result == 3

    def test_returns_zero_on_exception(self, scheduler: MaintenanceScheduler, storage: MagicMock) -> None:
        with patch.dict("sys.modules", {"lance": MagicMock(side_effect=ImportError("no lance"))}):
            result = scheduler._count_fragments("ds_a")
        assert result == 0


# ── status ──


class TestStatus:
    def test_status_before_first_run(self, scheduler: MaintenanceScheduler) -> None:
        status = scheduler.status()
        assert status.enabled is True
        assert status.last_run == ""
        assert status.interval_seconds == 3600
        assert status.last_report is None

    def test_status_after_run(self, scheduler: MaintenanceScheduler, storage: MagicMock) -> None:
        with patch.object(scheduler, "_count_fragments", return_value=5):
            scheduler.run_once()
        status = scheduler.status()
        assert status.last_run != ""
        assert status.next_run != ""
        assert status.last_report is not None


# ── lifecycle (start/stop) ──


class TestLifecycle:
    def test_start_creates_thread(self, scheduler: MaintenanceScheduler) -> None:
        scheduler.start()
        try:
            assert scheduler._thread is not None
            assert scheduler._thread.daemon is True
        finally:
            scheduler.stop()

    def test_start_idempotent(self, scheduler: MaintenanceScheduler) -> None:
        scheduler.start()
        thread1 = scheduler._thread
        scheduler.start()  # Should not create a new thread
        assert scheduler._thread is thread1
        scheduler.stop()

    def test_stop_sets_event(self, scheduler: MaintenanceScheduler) -> None:
        scheduler.start()
        scheduler.stop()
        assert scheduler._stop.is_set()
        assert scheduler._thread is None

    def test_stop_without_start(self, scheduler: MaintenanceScheduler) -> None:
        # Should not raise
        scheduler.stop()
