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
    """

    def ingest_sql(
        self,
        dataset_name: str,
        *,
        sql: str,
        connection_url: str,
        partition_col: str | None = None,
        num_partitions: int | None = None,
        transforms: list[Any] | None = None,
    ) -> Any:
        """Ingest data from a SQL database query."""
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
        row_count = df.count().to_arrow().column(0)[0].as_py()
        self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")

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

        row_count = df.count().to_arrow().column(0)[0].as_py()
        self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")

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
        row_count = df.count().to_arrow().column(0)[0].as_py()
        self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")
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
        row_count = df.count().to_arrow().column(0)[0].as_py()
        self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")
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
