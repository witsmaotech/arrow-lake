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
        self._quality_gate = None

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
    df.to_arrow.return_value = pa.table({"x": list(range(count))})
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


class TestIngestSqlGateWiring:
    """P0-2 (review 2026-08-26): SQL-source batches must pass the quality
    gate (contract + dead-letter) before the write — the old direct
    write_lance_from_dataframe call bypassed gating entirely."""

    @staticmethod
    def _gate_mock(mode: str, keep: int) -> MagicMock:
        from types import SimpleNamespace

        gate = MagicMock()
        gate.mode = mode
        gate.check.return_value = (
            pa.table({"x": list(range(keep))}),
            SimpleNamespace(rejected=max(10 - keep, 0), rejection_reasons=["contract:1"]),
        )
        return gate

    def test_gate_runs_on_materialized_batch(self, host: _HostWithMixin) -> None:
        host._quality_gate = self._gate_mock("shadow", 10)
        df = _mock_df(10)
        with patch("arrow_lake.ingest.connectors_sql.SqlConnector") as MC:
            MC.return_value.read.return_value = df
            host.ingest_sql("ds1", sql="SELECT 1", connection_url="sqlite:///db", target_table="t1")
        gate_args = host._quality_gate.check.call_args
        assert gate_args.kwargs.get("dataset_name") == "ds1"
        assert gate_args.kwargs.get("table_name") == "t1"
        assert gate_args.args[0].num_rows == 10  # the materialized batch

    def test_enforce_writes_gated_rows(self, host: _HostWithMixin) -> None:
        host._quality_gate = self._gate_mock("enforce", 6)
        with patch("arrow_lake.ingest.connectors_sql.SqlConnector") as MC:
            MC.return_value.read.return_value = _mock_df(10)
            report = host.ingest_sql("ds1", sql="SELECT 1", connection_url="sqlite:///db")
        written = host._manager.create_dataset.call_args
        assert written.args[0] == "ds1"
        assert written.args[1].num_rows == 6  # gated table, not the raw batch
        assert report.total_rows == 6

    def test_shadow_writes_full_batch(self, host: _HostWithMixin) -> None:
        host._quality_gate = self._gate_mock("shadow", 6)
        with patch("arrow_lake.ingest.connectors_sql.SqlConnector") as MC:
            MC.return_value.read.return_value = _mock_df(10)
            report = host.ingest_sql("ds1", sql="SELECT 1", connection_url="sqlite:///db")
        assert host._manager.create_dataset.call_args.args[1].num_rows == 10
        assert report.total_rows == 10

    def test_no_gate_still_writes(self, host: _HostWithMixin) -> None:
        host._quality_gate = None
        with patch("arrow_lake.ingest.connectors_sql.SqlConnector") as MC:
            MC.return_value.read.return_value = _mock_df(10)
            report = host.ingest_sql("ds1", sql="SELECT 1", connection_url="sqlite:///db")
        assert host._manager.create_dataset.call_args.args[1].num_rows == 10
        assert report.total_rows == 10


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


# ── 遗留-1 (review 2026-08-26): Kafka/Iceberg/Delta DataFrame 源统一走门禁 ──


class TestDataframeSourcesGated:
    """SQL got the gate in P0-2; the other DataFrame sources (kafka/iceberg/
    delta) had the identical bypass — all now flow through
    ``_gated_write_from_dataframe``."""

    @pytest.mark.parametrize("call", [
        lambda host: host.ingest_kafka(
            "ds1", bootstrap_servers="b:9092", topics="t", json_decode=False,
        ),
        lambda host: host.ingest_iceberg("ds1", table_uri="s3://w/t"),
        lambda host: host.ingest_deltalake("ds1", table_uri="s3://w/dt"),
    ], ids=["kafka", "iceberg", "deltalake"])
    def test_enforce_writes_gated_rows(self, host: _HostWithMixin, call) -> None:
        from types import SimpleNamespace

        gate = MagicMock()
        gate.mode = "enforce"
        gate.check.return_value = (
            pa.table({"x": list(range(6))}),
            SimpleNamespace(rejected=4, rejection_reasons=["contract:4"]),
        )
        host._quality_gate = gate
        df = _mock_df(10)
        with patch("arrow_lake.ingest.connectors_kafka.KafkaConnector") as mk, \
             patch("arrow_lake.ingest.connectors_lakehouse.IcebergConnector") as mi, \
             patch("arrow_lake.ingest.connectors_lakehouse.DeltaConnector") as md:
            mk.return_value.read.return_value = df
            mi.return_value.read.return_value = df
            md.return_value.read.return_value = df
            call(host)
        written = host._manager.create_dataset.call_args.args[1]
        assert written.num_rows == 6  # gated batch, not the raw 10

    def test_no_gate_still_writes(self, host: _HostWithMixin) -> None:
        host._quality_gate = None
        with patch("arrow_lake.ingest.connectors_kafka.KafkaConnector") as MC:
            MC.return_value.read.return_value = _mock_df(10)
            report = host.ingest_kafka(
                "ds1", bootstrap_servers="b:9092", topics="t", json_decode=False,
            )
        assert host._manager.create_dataset.call_args.args[1].num_rows == 10
        assert report.total_rows == 10


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
