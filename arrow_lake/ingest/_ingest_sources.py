"""External source ingestion methods for Ingestor.

Handles SQL, Kafka, Iceberg, Delta Lake, and HTTP ingestion.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any


class _SourceIngestMixin:
    """Mixin providing external source ingestion methods.

    Requires the host class to have:
    - ``_manager`` (LanceStorageManager)
    - ``_build_report()`` static method
    - ``_detect_file_type()`` class method
    - ``_read_bytes()`` static method
    - ``_write_table()`` method
    - ``_quality_gate`` (IngestionQualityGate | None — the Ingestor's gate;
      P0-2/遗留-1 review 2026-08-26: every DataFrame-source write passes it)
    """

    def _gated_write_from_dataframe(
        self, dataset_name: str, df: Any, *, target_table: str | None = None,
    ) -> int:
        """Materialize a Daft DataFrame, run the quality gate, write via
        ``create_dataset``; returns the row count actually written.

        P0-2 + 遗留-1 (review 2026-08-26): SQL/ClickHouse/Kafka/Iceberg/
        DeltaLake sources used to write the Daft DataFrame directly via
        ``write_lance_from_dataframe``, bypassing the quality gate (contract
        + quality + dead-lettering) entirely. All of them now flow through
        this single gated path.
        """
        batch = df.to_arrow()
        if self._quality_gate is not None:
            gated, result = self._quality_gate.check(
                batch, dataset_name=dataset_name, table_name=target_table,
            )
            if result.rejected > 0:
                import structlog

                structlog.get_logger(__name__).info(
                    "quality_gate.rejections",
                    dataset=dataset_name,
                    mode=getattr(self._quality_gate, "mode", "enforce"),
                    rejected=result.rejected,
                    reasons=list(result.rejection_reasons),
                )
            # Shadow counts/logs but never drops rows; only enforce swaps
            # the gated (filtered) batch in (same contract as _write_table).
            if getattr(self._quality_gate, "mode", "enforce") == "enforce":
                batch = gated
        self._manager.create_dataset(dataset_name, batch, table=target_table)
        return batch.num_rows

    def ingest_sql(
        self,
        dataset_name: str,
        *,
        sql: str,
        connection_url: str,
        partition_col: str | None = None,
        num_partitions: int | None = None,
        transforms: list[Any] | None = None,
        target_table: str | None = None,
    ) -> Any:
        """Ingest data from a SQL database query (optionally into a container table).

        P0-2 (review 2026-08-26): the batch is materialized to Arrow and run
        through the quality gate (contract + quality + dead-letter) BEFORE the
        write — this path previously wrote the Daft DataFrame directly and
        bypassed gating entirely.
        """
        from arrow_lake.ingest.connectors_sql import SqlConnector
        from arrow_lake.ingest.ingestor import IngestionSource

        connector = SqlConnector(
            connection_url,
            partition_col=partition_col,
            num_partitions=num_partitions,
        )
        df = connector.read(sql)
        if transforms:
            for t in transforms:
                df = t(df)
        row_count = self._gated_write_from_dataframe(
            dataset_name, df, target_table=target_table,
        )

        # Best-effort column-comment capture (MySQL/PG catalog). Daft wrote the
        # DataFrame directly (no Arrow interception), so we apply comments to
        # the persisted Lance schema after the write via update_field_metadata.
        try:
            comments = connector.fetch_column_comments(sql)
            if comments:
                self._manager.update_field_comments(dataset_name, comments, table=target_table)
        except Exception:
            pass

        sources = [IngestionSource(
            path=f"sql:{connection_url.split('@')[-1] if '@' in connection_url else connection_url}",
            row_count=row_count,
            file_count=1,
        )]
        return self._build_report(sources)

    def ingest_kafka(
        self,
        dataset_name: str,
        *,
        bootstrap_servers: str,
        topics: list[str] | str,
        start: str = "earliest",
        end: str = "latest",
        json_decode: bool = True,
        transforms: list[Any] | None = None,
    ) -> Any:
        """Ingest messages from Kafka topics into a Lance dataset."""
        import daft

        from arrow_lake.ingest.connectors_kafka import KafkaConnector
        from arrow_lake.ingest.ingestor import IngestionSource

        connector = KafkaConnector(bootstrap_servers)
        df = connector.read(topics=topics, start=start, end=end)

        if json_decode:
            df = df.with_column("value", daft.functions.json_decode(daft.col("value")))
            value_type = df.schema()["value"].dtype
            if hasattr(value_type, "fields"):
                for field_name in value_type.fields:
                    df = df.with_column(
                        field_name, daft.col("value")[field_name],
                    )
                df = df.exclude("value")

        if transforms:
            for t in transforms:
                df = t(df)

        row_count = self._gated_write_from_dataframe(dataset_name, df)

        topic_str = topics if isinstance(topics, str) else ",".join(topics)
        sources = [IngestionSource(
            path=f"kafka:{topic_str}",
            row_count=row_count,
            file_count=1,
        )]
        return self._build_report(sources)

    def ingest_iceberg(
        self,
        dataset_name: str,
        *,
        table_uri: str,
        transforms: list[Any] | None = None,
    ) -> Any:
        """Ingest data from an Apache Iceberg table."""
        from arrow_lake.ingest.connectors_lakehouse import IcebergConnector
        from arrow_lake.ingest.ingestor import IngestionSource

        connector = IcebergConnector(table_uri)
        df = connector.read()
        if transforms:
            for t in transforms:
                df = t(df)
        row_count = self._gated_write_from_dataframe(dataset_name, df)
        return self._build_report([IngestionSource(
            path=f"iceberg:{table_uri}", row_count=row_count, file_count=1,
        )])

    def ingest_deltalake(
        self,
        dataset_name: str,
        *,
        table_uri: str,
        version: int | None = None,
        transforms: list[Any] | None = None,
    ) -> Any:
        """Ingest data from a Delta Lake table."""
        from arrow_lake.ingest.connectors_lakehouse import DeltaConnector
        from arrow_lake.ingest.ingestor import IngestionSource

        connector = DeltaConnector(table_uri, version=version)
        df = connector.read()
        if transforms:
            for t in transforms:
                df = t(df)
        row_count = self._gated_write_from_dataframe(dataset_name, df)
        return self._build_report([IngestionSource(
            path=f"delta:{table_uri}", row_count=row_count, file_count=1,
        )])

    def ingest_http(
        self,
        dataset_name: str,
        urls: list[str],
    ) -> Any:
        """Ingest files from HTTP(S) URLs."""
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector()
        sources: list[Any] = []

        def _fetch_and_read(url: str) -> tuple[str, Any]:
            result = connector.fetch(url)
            ft = self._detect_file_type(url)
            table = self._read_bytes(result.content, ft)
            return result.url, table

        workers = min(len(urls), 4)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_list = [pool.submit(_fetch_and_read, url) for url in urls]
            for future in future_list:
                resolved_url, table = future.result()
                self._write_table(dataset_name, table, sources, resolved_url)

        return self._build_report(sources)
