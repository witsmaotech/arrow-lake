"""Coverage for _SourceIngestMixin — SQL, Kafka, Iceberg, DeltaLake, HTTP ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest


@dataclass(frozen=True)
class _FakeSource:
    path: str
    row_count: int
    file_count: int = 1


@dataclass(frozen=True)
class _FakeReport:
    sources: tuple = ()
    total_rows: int = 0
    total_files: int = 0


class _FakeHost:
    """Minimal host class with required attributes for _SourceIngestMixin."""

    def __init__(self) -> None:
        self._manager = MagicMock()

    @staticmethod
    def _build_report(sources):
        total_rows = sum(s.row_count for s in sources)
        return _FakeReport(sources=tuple(sources), total_rows=total_rows, total_files=len(sources))

    @classmethod
    def _detect_file_type(cls, path: str) -> str:
        return "csv"

    @staticmethod
    def _read_bytes(data: bytes, file_type: str) -> pa.Table:
        return pa.table({"col": [1]})

    def _write_table(self, name, table, sources, path):
        sources.append(_FakeSource(path=path, row_count=table.num_rows))


from arrow_lake.ingest._ingest_sources import _SourceIngestMixin


class _HostWithMixin(_FakeHost, _SourceIngestMixin):
    pass


@pytest.fixture
def host() -> _HostWithMixin:
    return _HostWithMixin()


def _mock_df(count: int = 10) -> MagicMock:
    df = MagicMock()
    df.count.return_value.to_arrow.return_value = pa.table({"count": [count]})
    df.with_column.return_value = df
    return df


# ── ingest_sql ──


class TestIngestSql:
    def test_basic(self, host: _HostWithMixin) -> None:
        df = _mock_df(10)
        with patch("arrow_lake.ingest.connectors_sql.SqlConnector") as MC:
            MC.return_value.read.return_value = df
            report = host.ingest_sql("ds1", sql="SELECT 1", connection_url="sqlite:///test.db")
        assert report.total_rows == 10

    def test_with_transforms(self, host: _HostWithMixin) -> None:
        df = _mock_df(5)
        t = MagicMock(return_value=df)
        with patch("arrow_lake.ingest.connectors_sql.SqlConnector") as MC:
            MC.return_value.read.return_value = df
            host.ingest_sql("ds1", sql="SELECT 1", connection_url="sqlite:///db", transforms=[t])
        t.assert_called_once()


# ── ingest_iceberg ──


class TestIngestIceberg:
    def test_basic(self, host: _HostWithMixin) -> None:
        df = _mock_df(100)
        with patch("arrow_lake.ingest.connectors_lakehouse.IcebergConnector") as MC:
            MC.return_value.read.return_value = df
            report = host.ingest_iceberg("ds1", table_uri="s3://warehouse/t")
        assert report.total_rows == 100


# ── ingest_deltalake ──


class TestIngestDeltaLake:
    def test_basic(self, host: _HostWithMixin) -> None:
        df = _mock_df(50)
        with patch("arrow_lake.ingest.connectors_lakehouse.DeltaConnector") as MC:
            MC.return_value.read.return_value = df
            report = host.ingest_deltalake("ds1", table_uri="s3://warehouse/dt", version=3)
        assert report.total_rows == 50


# ── ingest_http ──


class TestIngestHttp:
    def test_basic(self, host: _HostWithMixin) -> None:
        mock_result = MagicMock()
        mock_result.url = "https://example.com/data.csv"
        mock_result.content = b"a,b\n1,2"
        with patch("arrow_lake.ingest.connectors_http.HttpConnector") as MC:
            MC.return_value.fetch.return_value = mock_result
            report = host.ingest_http("ds1", ["https://example.com/data.csv"])
        assert report.total_files == 1


# ── ingest_kafka ──


class TestIngestKafka:
    def test_basic(self, host: _HostWithMixin) -> None:
        df = _mock_df(200)
        with patch("arrow_lake.ingest.connectors_kafka.KafkaConnector") as MC:
            MC.return_value.read.return_value = df
            report = host.ingest_kafka(
                "ds1",
                bootstrap_servers="localhost:9092",
                topics="my-topic",
                json_decode=False,
            )
        assert report.total_rows == 200
