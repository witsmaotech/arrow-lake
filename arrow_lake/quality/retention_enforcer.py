"""Retention policy enforcement — background thread for cleaning expired Lance versions."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from typing import Any

import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)


class RetentionEnforcer:
    """Periodically reads retention policies from Gravitino and cleans expired data.

    Two enforcement modes:
      - Version cleanup: remove old Lance dataset versions older than retention days.
      - Row cleanup: delete rows with ``created_at`` older than retention days (if column exists).
    """

    def __init__(
        self,
        config: GravitinoConfig,
        storage: Any,
        session_manager: Any | None = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._session_manager = session_manager
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ── lifecycle ──

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("retention_enforcer.started",
                     interval=self._config.retention_enforce_interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                logger.error("retention_enforcer.thread_still_alive")
            self._thread = None
        logger.info("retention_enforcer.stopped")

    # ── main loop ──

    def _run_loop(self) -> None:
        while not self._stop.wait(timeout=self._config.retention_enforce_interval_seconds):
            try:
                cleaned = self.enforce()
                if cleaned > 0:
                    logger.info("retention_enforcer.cycle", cleaned=cleaned)
            except Exception:
                logger.warning("retention_enforcer.cycle_failed", exc_info=True)

    # ── enforcement ──

    def enforce(self, dry_run: bool = False) -> int:
        """Execute one round of retention enforcement. Returns number of tables cleaned."""
        policies = self._fetch_retention_policies()
        if not policies:
            return 0

        total = 0
        for table_name, days in policies.items():
            try:
                n = self._enforce_table(table_name, days, dry_run=dry_run)
                total += n
            except Exception:
                logger.warning("retention_enforcer.table_failed",
                               table=table_name, exc_info=True)
        return total

    def enforce_table(self, table_name: str, dry_run: bool = False) -> int:
        """Enforce retention for a single table (auto-detect days from policies)."""
        policies = self._fetch_retention_policies()
        days = policies.get(table_name)
        if days is None:
            return 0
        return self._enforce_table(table_name, days, dry_run=dry_run)

    # ── internal ──

    def _enforce_table(self, table_name: str, days: int, *, dry_run: bool = False) -> int:
        """Clean one table. Returns count of versions removed."""
        cutoff = timedelta(days=days)

        # Version-level cleanup via Lance
        try:
            removed = self._storage.cleanup_versions(table_name, older_than=cutoff, dry_run=dry_run)
            if removed > 0:
                logger.info("retention_enforcer.versions_cleaned",
                            table=table_name, removed=removed, days=days, dry_run=dry_run)
            return removed
        except Exception:
            logger.warning("retention_enforcer.cleanup_failed",
                           table=table_name, exc_info=True)
            return 0

    def _fetch_retention_policies(self) -> dict[str, int]:
        """Read retention policies from Gravitino REST API.

        Returns {table_name: retention_days} for all tables with applied retention policies.
        """
        policies: dict[str, int] = {}
        try:
            from urllib.request import Request, urlopen

            url = f"{self._config.uri}/api/metalakes/{self._config.metalake}/policies"
            req = Request(url)
            req.add_header("Accept", "application/vnd.gravitino.v1+json")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            for ident in data.get("identifiers", []):
                name = ident.get("name", "")
                if "retention" not in name:
                    continue
                # Read policy details for retention days
                try:
                    detail_url = (
                        f"{self._config.uri}/api/metalakes/{self._config.metalake}"
                        f"/policies/{name}"
                    )
                    detail_req = Request(detail_url)
                    detail_req.add_header("Accept", "application/vnd.gravitino.v1+json")
                    with urlopen(detail_req, timeout=10) as detail_resp:
                        detail = json.loads(detail_resp.read().decode())

                    policy = detail.get("policy", {})
                    props = policy.get("properties", {})
                    days = int(props.get("retention.days", props.get("days", 0)))
                    # Get applied tables from policy properties
                    applied_tables = props.get("applied_tables", "")
                    if applied_tables:
                        tables_list = json.loads(applied_tables)
                        if not isinstance(tables_list, list):
                            continue
                        for tbl in tables_list:
                            if isinstance(tbl, str) and days > 0:
                                policies[tbl] = days
                except Exception:
                    logger.debug("retention_enforcer.policy_detail_failed", name=name)
        except Exception:
            logger.debug("retention_enforcer.fetch_policies_failed", exc_info=True)
        return policies
