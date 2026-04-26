"""Query mixin — metadata SQL, OLAP analytics, export, and Daft."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arrow_lake.query.daft_api import LazyDaftFrame
    from arrow_lake.query.metadata import MetadataQueryResult
    from arrow_lake.query.olap import OlapQueryResult
    from arrow_lake.query.export import ExportResult


class _LakeQueryMixin:
    """Provides metadata SQL queries, OLAP analytics, dataset export, and Daft."""

    def query(
        self,
        dataset_name: str,
        sql: str,
    ) -> MetadataQueryResult:
        """Query dataset metadata via SQL.

        Delegates to MetadataSearchBridge (Story 3.9).

        Args:
            dataset_name: Name of the Lance dataset.
            sql: SQL query (SELECT only).

        Returns:
            MetadataQueryResult with Arrow table.
        """
        from arrow_lake.core.metrics import _QueryTimer
        from arrow_lake.query.metadata import MetadataSearchBridge

        bridge = self._get_component(
            "metadata",
            lambda: MetadataSearchBridge(
                self._get_storage(),
                storage_config=self._config.storage,
                session_manager=self.get_session_manager(),
            ),
        )
        with _QueryTimer("metadata_query"):
            return bridge.query(dataset_name, sql)

    def olap_query(
        self,
        dataset_name: str,
        sql: str,
        *,
        max_rows: int | None = None,
        tables: dict[str, Any] | None = None,
    ) -> OlapQueryResult:
        """OLAP analytics query via DuckDB SQL (Story 5.4, 7.6).

        Supports GROUP BY, aggregation, window functions, HAVING, ORDER BY,
        LIMIT, and JOIN queries.

        Args:
            dataset_name: Name of the Lance dataset.
            sql: SQL query string (must be SELECT only).
            max_rows: Maximum result rows (None = use config default).
            tables: Additional Arrow tables for JOIN queries.

        Returns:
            OlapQueryResult with Arrow table and metadata.
        """
        from arrow_lake.query.olap import OlapSearchBridge

        bridge = self._get_component(
            "olap",
            lambda: OlapSearchBridge(
                self._get_storage(),
                config=self._config.olap,
                storage_config=self._config.storage,
                session_manager=self.get_session_manager(),
            ),
        )
        from arrow_lake.api.telemetry import get_tracer
        from arrow_lake.core.metrics import _QueryTimer, get_metrics_enabled, query_results_total

        tracer = get_tracer()
        with tracer.start_as_current_span("olap_query", attributes={"dataset": dataset_name}):
            with _QueryTimer("olap_query"):
                result = bridge.query(dataset_name, sql, max_rows=max_rows, tables=tables)
        if get_metrics_enabled():
            query_results_total.labels(query_type="olap_query").inc(result.table.num_rows)
        return result

    def sql_query(
        self,
        dataset_name: str,
        sql: str,
        *,
        max_rows: int | None = None,
        tables: dict[str, Any] | None = None,
    ) -> OlapQueryResult:
        """SQL query — semantic alias for olap_query() (Story 7.6).

        Args:
            dataset_name: Name of the Lance dataset.
            sql: SQL query string (must be SELECT only).
            max_rows: Maximum result rows (None = use config default).
            tables: Additional Arrow tables for JOIN queries.

        Returns:
            OlapQueryResult with Arrow table and metadata.
        """
        return self.olap_query(dataset_name, sql, max_rows=max_rows, tables=tables)

    def materialize(
        self,
        dataset_name: str,
        sql: str,
        *,
        view_name: str | None = None,
        ttl_days: int | None = None,
        max_join_rows: int | None = None,
    ) -> int:
        """Materialize a SQL query result as a DuckLake table.

        Creates a persistent materialized view managed by DuckLake with
        TTL-based lifecycle. Requires ducklake_enabled=True in config.

        Args:
            dataset_name: Name of the Lance dataset.
            sql: SQL query to materialize (SELECT only).
            view_name: Materialized table name (None = auto-generate).
            ttl_days: TTL in days (None = use config default).
            max_join_rows: Row budget (None = use config default).

        Returns:
            Number of rows materialized.
        """
        from arrow_lake.core.metrics import _QueryTimer
        from arrow_lake.query.olap import OlapSearchBridge

        bridge = self._get_component(
            "olap",
            lambda: OlapSearchBridge(
                self._get_storage(),
                config=self._config.olap,
                storage_config=self._config.storage,
                session_manager=self.get_session_manager(),
            ),
        )
        with _QueryTimer("materialize"):
            return bridge.materialize(
                dataset_name,
                sql,
                view_name=view_name,
                ttl_days=ttl_days,
                max_join_rows=max_join_rows,
            )

    def cleanup_materialized(self, ttl_days: int | None = None) -> list[str]:
        """Drop expired materialized DuckLake views.

        Args:
            ttl_days: Override TTL (None = use config default).

        Returns:
            List of dropped table names.
        """
        from arrow_lake.query.olap import OlapSearchBridge

        bridge = self._get_component(
            "olap",
            lambda: OlapSearchBridge(
                self._get_storage(),
                config=self._config.olap,
                storage_config=self._config.storage,
                session_manager=self.get_session_manager(),
            ),
        )
        return bridge.cleanup_materialized(ttl_days=ttl_days)

    def export(
        self,
        dataset_name: str,
        output_path: str,
        *,
        format: str | None = None,  # noqa: A002
        columns: list[str] | None = None,
        version: int | None = None,
        compression: str | None = None,
        overwrite: bool = False,
    ) -> ExportResult:
        """Export a dataset to Parquet or CSV (Story 5.9).

        Delegates to ExportBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            output_path: Output file path (.parquet or .csv).
            format: Export format (None = auto-detect from path suffix).
            columns: Optional column subset to export.
            version: Dataset version to export (None = latest).
            compression: Compression codec for Parquet.
            overwrite: Allow overwriting existing file.

        Returns:
            ExportResult with export metadata.
        """
        from arrow_lake.core.metrics import _QueryTimer
        from arrow_lake.query.export import ExportBridge

        bridge = self._get_component(
            "export",
            lambda: ExportBridge(self._get_storage(), config=self._config.export),
        )
        with _QueryTimer("export"):
            return bridge.export(
                dataset_name,
                output_path,
                format=format,
                columns=columns,
                version=version,
                compression=compression,
                overwrite=overwrite,
            )

    def daft_query(
        self,
        dataset_name: str,
        *,
        columns: list[str] | None = None,
    ) -> LazyDaftFrame:
        """Load a Lance dataset as a lazy Daft DataFrame (Story 3.7).

        Returns a LazyDaftFrame for chained operations: select, filter,
        sort, join, groupby. Call .collect() to materialize as Arrow Table.

        Args:
            dataset_name: Name of the Lance dataset.
            columns: Optional column subset to load.

        Returns:
            LazyDaftFrame for further lazy operations.
        """
        from arrow_lake.query.daft_api import DaftQueryEngine

        engine = self._get_component(
            "daft",
            lambda: DaftQueryEngine(self._base_uri),
        )
        return engine.load(dataset_name, columns=columns)
