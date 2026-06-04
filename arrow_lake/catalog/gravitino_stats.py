"""Gravitino statistics collector — collect from DuckDB, register to Gravitino."""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen

import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)


class GravitinoStatsCollector:
    """Collect table statistics from DuckDB and register with Gravitino.

    Args:
        config: Gravitino connection config.
    """

    _LANCE_CATALOG = "lance-catalog"
    _DEFAULT_SCHEMA = "arrow_lake"

    def __init__(self, config: GravitinoConfig) -> None:
        self._config = config
        self._headers = {
            "Accept": "application/vnd.gravitino.v1+json",
            "Content-Type": "application/json",
        }

    def collect_table_stats(self, name: str, conn: Any) -> dict[str, Any]:
        """Collect basic table statistics from DuckDB.

        Args:
            name: Table name.
            conn: DuckDB connection.

        Returns:
            Dict with row_count, column_count, etc.
        """
        stats: dict[str, Any] = {
            "name": name,
            "row_count": 0,
            "column_count": 0,
            "size_mb": 0.0,
            "columns": [],
        }
        try:
            # Column metadata from information_schema (parameterized)
            cols = conn.execute(
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_name = ?",
                [name],
            ).fetchall()
            stats["column_count"] = len(cols)
            stats["columns"] = [{"name": c[0], "type": c[1]} for c in cols]

            # Row count from the Lance dataset (may fail for external tables)
            try:
                from arrow_lake.validation import validate_identifier
                validate_identifier(name)
                row = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()  # nosec B608: name validated above
                if row:
                    stats["row_count"] = row[0]
            except Exception:
                pass

            # Size estimate from Parquet metadata if available
            try:
                row = conn.execute(
                    "SELECT sum(file_size) / 1024.0 / 1024.0 "
                    "FROM parquet_metadata(?||'/**/*.parquet')",
                    [name],
                ).fetchone()
                if row and row[0]:
                    stats["size_mb"] = round(row[0], 2)
            except Exception:
                pass

        except Exception as exc:
            logger.warning("gravitino_collect_stats_failed", name=name, error=str(exc))
        return stats

    def register_stats(self, name: str, stats: dict[str, Any]) -> None:
        """Register collected statistics with Gravitino as table properties."""
        if not self._config.enabled:
            return
        try:
            props: dict[str, str] = {
                "stats.row_count": str(stats.get("row_count", 0)),
                "stats.column_count": str(stats.get("column_count", 0)),
                "stats.size_mb": f"{stats.get('size_mb', 0.0):.2f}",
            }
            for col_info in stats.get("columns", []):
                col_name = col_info.get("name", "")
                col_type = col_info.get("type", "")
                if col_name:
                    props[f"stats.col.{col_name}.type"] = col_type

            metalake = self._config.metalake
            url = (
                f"{self._config.uri}/api/metalakes/{metalake}"
                f"/catalogs/{self._LANCE_CATALOG}"
                f"/schemas/{self._DEFAULT_SCHEMA}/tables/{name}"
            )
            body = json.dumps({
                "updates": [
                    {"@type": "setProperty", "property": k, "value": v}
                    for k, v in props.items()
                ],
            }).encode()
            req = Request(url, data=body, headers=self._headers, method="PATCH")
            with urlopen(req, timeout=10):
                pass
            logger.info(
                "gravitino_stats_registered",
                name=name,
                row_count=stats.get("row_count"),
                properties=len(props),
            )
        except Exception as exc:
            logger.warning("gravitino_register_stats_failed", name=name, error=str(exc))
