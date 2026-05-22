"""Background metadata sync scheduler."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from arrow_lake.catalog.gravitino_bridge import GravitinoBridge

logger = structlog.get_logger(__name__)


def _load_local_entries(lake: Any) -> list[dict[str, Any]]:
    """Read current catalog entries via Lake facade."""
    try:
        names = lake.list_datasets()
        return [{"name": n, "location": ""} for n in names]
    except Exception:
        return []


class GravitinoSyncScheduler:
    """Periodic background sync between local catalog and Gravitino.

    Args:
        bridge: GravitinoBridge instance.
        session_manager: DuckDBSessionManager for reading catalog_tables.
        interval: Sync interval in seconds (minimum 5).
    """

    def __init__(
        self,
        bridge: GravitinoBridge,
        lake: Any,
        interval: int = 30,
    ) -> None:
        self._bridge = bridge
        self._lake = lake
        self._interval = max(interval, 5)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="gravitino-sync",
            daemon=True,
        )
        self._thread.start()
        logger.info("gravitino_sync_started", interval=self._interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 5)
            self._thread = None
        logger.info("gravitino_sync_stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                entries = _load_local_entries(self._lake)
                outbound = self._bridge.sync_outbound(entries)
                inbound = self._bridge.sync_inbound()
                logger.debug(
                    "gravitino_sync_cycle",
                    outbound=outbound,
                    inbound=len(inbound),
                )
            except Exception as exc:
                logger.warning("gravitino_sync_cycle_failed", error=str(exc))
            self._stop_event.wait(timeout=self._interval)
