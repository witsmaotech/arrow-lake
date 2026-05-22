"""Statistics-driven query optimization hints — reads Gravitino stats to influence query routing."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass

import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class QueryHints:
    """Optimization hints derived from table statistics."""

    estimated_rows: int = 0
    column_count: int = 0
    size_mb: float = 0.0

    @property
    def is_large(self) -> bool:
        """Whether this table is considered large enough for special routing."""
        return self.estimated_rows >= 1_000_000 or self.size_mb >= 1024.0


class StatsInjector:
    """Reads table statistics from Gravitino properties and provides query optimization hints.

    Usage::

        injector = StatsInjector(config)
        hints = injector.get_hints("articles")
        if hints.estimated_rows > config.stats_auto_route_threshold:
            # Route to DuckDB OLAP instead of Daft in-memory
    """

    def __init__(self, config: GravitinoConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[QueryHints, float]] = {}
        self._ttl = config.stats_cache_ttl_seconds

    def get_hints(self, dataset: str) -> QueryHints:
        """Get optimization hints for a dataset (cached with TTL)."""
        with self._lock:
            cached = self._cache.get(dataset)
            if cached is not None:
                hints, ts = cached
                if time.time() - ts < self._ttl:
                    return hints

        hints = self._fetch_hints(dataset)
        with self._lock:
            self._cache[dataset] = (hints, time.time())
        return hints

    def invalidate(self, dataset: str) -> None:
        """Force next get_hints() call to re-fetch from Gravitino."""
        with self._lock:
            self._cache.pop(dataset, None)

    def should_use_olap(self, dataset: str) -> bool:
        """Whether the dataset is large enough to prefer DuckDB OLAP over Daft in-memory."""
        hints = self.get_hints(dataset)
        return hints.estimated_rows >= self._config.stats_auto_route_threshold

    def suggest_limit(self, dataset: str, requested_limit: int | None = None) -> int | None:
        """Suggest a LIMIT for queries against this dataset.

        Returns requested_limit if provided, otherwise a sensible default based on table size.
        """
        if requested_limit is not None:
            return requested_limit
        hints = self.get_hints(dataset)
        if hints.estimated_rows <= 0:
            return None
        if hints.estimated_rows > 100_000:
            return 10_000
        return None

    # ── internal ──

    def _fetch_hints(self, dataset: str) -> QueryHints:
        """Read stats from Gravitino table properties."""
        try:
            from urllib.request import Request, urlopen

            if not _SAFE_ID.match(dataset):
                logger.warning("stats_injector.invalid_dataset", dataset=dataset)
                return QueryHints()

            url = (
                f"{self._config.uri}/api/metalakes/{self._config.metalake}"
                f"/catalogs/{self._config.lance_catalog_name}"
                f"/schemas/{self._config.lance_schema_name}/tables/{dataset}"
            )
            req = Request(url)
            req.add_header("Accept", "application/vnd.gravitino.v1+json")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            props = data.get("table", {}).get("properties", {})
            return QueryHints(
                estimated_rows=int(props.get("stats.row_count", 0)),
                column_count=int(props.get("stats.column_count", 0)),
                size_mb=float(props.get("stats.size_mb", 0.0)),
            )
        except Exception:
            logger.debug("stats_injector.fetch_failed", dataset=dataset, exc_info=True)
            return QueryHints()
