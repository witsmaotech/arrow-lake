"""Background storage maintenance — auto-compaction and version cleanup."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from arrow_lake.config.storage import StorageConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MaintenanceReport:
    """Result of one maintenance cycle."""

    datasets_compacted: int
    datasets_cleaned: int
    total_fragments_before: int
    total_fragments_after: int
    total_versions_removed: int
    duration_seconds: float


@dataclass(frozen=True)
class MaintenanceStatus:
    """Current maintenance scheduler status."""

    enabled: bool
    last_run: str
    next_run: str
    interval_seconds: int
    last_report: MaintenanceReport | None


class MaintenanceScheduler:
    """Background storage maintenance: auto-compaction and version cleanup.

    Follows the same threading pattern as RetentionEnforcer and
    GravitinoSyncScheduler.
    """

    def __init__(self, storage: Any, config: StorageConfig) -> None:
        self._storage = storage
        self._config = config
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_report: MaintenanceReport | None = None
        self._last_run_time: float = 0.0

    # ── lifecycle ──

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            logger.info(
                "maintenance_scheduler.started",
                interval=self._config.maintenance_interval_seconds,
            )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                logger.error("maintenance_scheduler.thread_still_alive")
            self._thread = None
        logger.info("maintenance_scheduler.stopped")

    # ── main loop ──

    def _run_loop(self) -> None:
        while not self._stop.wait(timeout=self._config.maintenance_interval_seconds):
            try:
                report = self.run_once()
                logger.info(
                    "maintenance_scheduler.cycle",
                    compacted=report.datasets_compacted,
                    cleaned=report.datasets_cleaned,
                    duration=report.duration_seconds,
                )
            except Exception:
                logger.warning("maintenance_scheduler.cycle_failed", exc_info=True)

    # ── public API ──

    def run_once(self) -> MaintenanceReport:
        """Execute one maintenance cycle across all datasets."""
        from arrow_lake.core.metrics import (
            maintenance_compaction_fragments_delta,
            maintenance_compaction_runs_total,
            maintenance_cycle_duration_seconds,
            maintenance_last_run_timestamp,
            maintenance_vacuum_runs_total,
        )

        start = time.monotonic()
        fragments_before_total = 0
        fragments_after_total = 0
        compacted = 0
        cleaned = 0
        versions_removed = 0

        retention = timedelta(days=self._config.version_retention_days)
        threshold = self._config.compaction_fragment_threshold

        try:
            datasets = self._storage.list_datasets()
        except Exception:
            logger.warning("maintenance_scheduler.list_datasets_failed", exc_info=True)
            datasets = []

        for name in datasets:
            # ── compaction ──
            try:
                fragment_count = self._count_fragments(name)
                if fragment_count > threshold:
                    stats = self._storage.compact(name)
                    compacted += 1
                    fragments_before_total += stats.fragments_before
                    fragments_after_total += stats.fragments_after
                    delta = stats.fragments_before - stats.fragments_after
                    maintenance_compaction_runs_total.labels(dataset=name).inc()
                    maintenance_compaction_fragments_delta.labels(dataset=name).set(delta)
                    logger.info(
                        "maintenance_scheduler.compacted",
                        dataset=name,
                        fragments_before=stats.fragments_before,
                        fragments_after=stats.fragments_after,
                    )
                else:
                    fragments_before_total += fragment_count
                    fragments_after_total += fragment_count
            except Exception:
                logger.debug("maintenance_scheduler.compact_failed", dataset=name, exc_info=True)

            # ── version cleanup ──
            try:
                removed = self._storage.cleanup_versions(name, older_than=retention)
                if removed > 0:
                    cleaned += 1
                    versions_removed += removed
                    maintenance_vacuum_runs_total.labels(dataset=name).inc()
            except Exception:
                logger.debug("maintenance_scheduler.vacuum_failed", dataset=name, exc_info=True)

        elapsed = time.monotonic() - start
        report = MaintenanceReport(
            datasets_compacted=compacted,
            datasets_cleaned=cleaned,
            total_fragments_before=fragments_before_total,
            total_fragments_after=fragments_after_total,
            total_versions_removed=versions_removed,
            duration_seconds=round(elapsed, 3),
        )

        self._last_report = report
        self._last_run_time = time.time()
        maintenance_cycle_duration_seconds.set(elapsed)
        maintenance_last_run_timestamp.set(time.time())
        return report

    def status(self) -> MaintenanceStatus:
        """Return current scheduler status."""
        last_run_iso = (
            datetime.fromtimestamp(self._last_run_time, tz=UTC).isoformat()
            if self._last_run_time > 0
            else ""
        )
        next_ts = (
            self._last_run_time + self._config.maintenance_interval_seconds
            if self._last_run_time > 0
            else time.time() + self._config.maintenance_interval_seconds
        )
        next_run_iso = datetime.fromtimestamp(next_ts, tz=UTC).isoformat()
        return MaintenanceStatus(
            enabled=True,
            last_run=last_run_iso,
            next_run=next_run_iso,
            interval_seconds=self._config.maintenance_interval_seconds,
            last_report=self._last_report,
        )

    # ── internal ──

    def _count_fragments(self, name: str) -> int:
        """Count Lance dataset fragments for fragmentation check."""
        try:
            import lance as lance_lib

            uri = self._storage.dataset_uri(name)
            opts = self._storage.storage_options
            ds = lance_lib.dataset(uri, storage_options=opts)
            return len(ds.get_fragments())
        except Exception:
            return 0
