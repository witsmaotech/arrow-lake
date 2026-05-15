"""Kafka bounded-batch connector — Daft Phase 2, Sprint 6.

Wraps ``daft.read_kafka()`` for ingesting bounded message ranges from
Kafka topics into Lance datasets.
"""

from __future__ import annotations

from typing import Any

from arrow_lake.exceptions import ErrorCode, IngestError


class KafkaConnector:
    """Read messages from Kafka topics via Daft.

    Only supports **bounded batch** reads — no streaming/unbounded mode.

    Args:
        bootstrap_servers: Kafka broker addresses.
        group_id: Consumer group ID.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        *,
        group_id: str = "arrow-lake-kafka-reader",
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._group_id = group_id

    def read(
        self,
        *,
        topics: list[str] | str,
        start: str = "earliest",
        end: str = "latest",
    ) -> Any:
        """Read a bounded range of Kafka messages.

        Args:
            topics: Topic name(s) to read from.
            start: Start bound — "earliest", "latest", ISO-8601 timestamp, or offset dict.
            end: End bound — same types as start.

        Returns:
            Daft DataFrame with columns: key, value, partition, offset, timestamp.

        Raises:
            IngestError: If Kafka connection or read fails.
        """
        import daft

        try:
            return daft.read_kafka(
                bootstrap_servers=self._bootstrap_servers,
                topics=topics,
                start=start,
                end=end,
                group_id=self._group_id,
            )
        except Exception as exc:
            raise IngestError(
                error_code=ErrorCode.INGEST_FILE_NOT_FOUND,
                message=f"Kafka read failed: {exc}",
                context={
                    "bootstrap_servers": self._bootstrap_servers,
                    "topics": str(topics),
                },
            ) from exc
