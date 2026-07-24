"""Unit tests for arrow_lake.ingest.field_comments (column-comment capture)."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from arrow_lake.ingest.field_comments import (
    attach_field_comments,
    capture_for_file,
    extract_csv_sidecar_comments,
    extract_parquet_comments,
)


def _write_parquet(path, *, comments):
    """Write a one-row parquet file whose schema carries ``comment`` metadata."""
    def _value(typ):
        if typ == pa.int64():
            return 0
        if typ == pa.float64():
            return 1.0
        return "x"

    fields = []
    data = {}
    for name, comment, typ in comments:
        md = {b"comment": comment.encode()} if comment is not None else None
        fields.append(pa.field(name, typ, metadata=md))
        data[name] = [_value(typ)]
    schema = pa.schema(fields)
    table = pa.Table.from_pydict(data, schema=schema)
    pq.write_table(table, str(path))
    return table


class TestExtractParquetComments:
    def test_reads_comment_metadata(self, tmp_path):
        p = tmp_path / "orders.parquet"
        _write_parquet(
            p,
            comments=[("user_id", "primary key", pa.int64()), ("note", "a note", pa.string())],
        )
        got = extract_parquet_comments(str(p))
        assert got == {"user_id": "primary key", "note": "a note"}

    def test_description_key_fallback(self, tmp_path):
        # Spark/other producers may write under b"description".
        fields = [pa.field("amount", pa.float64(), metadata={b"description": b"order total"})]
        schema = pa.schema(fields)
        pq.write_table(pa.Table.from_pydict({"amount": [1.0]}, schema=schema), str(tmp_path / "a.parquet"))
        assert extract_parquet_comments(str(tmp_path / "a.parquet")) == {"amount": "order total"}

    def test_no_metadata_returns_empty(self, tmp_path):
        p = tmp_path / "plain.parquet"
        pq.write_table(pa.table({"c": [1, 2]}), str(p))
        assert extract_parquet_comments(str(p)) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        assert extract_parquet_comments(str(tmp_path / "nope.parquet")) == {}


class TestExtractCsvSidecar:
    def test_json_sidecar(self, tmp_path):
        (tmp_path / "data.columns.json").write_text('{"a": "alpha", "b": "beta"}', encoding="utf-8")
        assert extract_csv_sidecar_comments(str(tmp_path / "data.csv")) == {"a": "alpha", "b": "beta"}

    def test_yaml_sidecar(self, tmp_path):
        (tmp_path / "data.meta.yaml").write_text("columns:\n  a: alpha\n  b: beta\n", encoding="utf-8")
        assert extract_csv_sidecar_comments(str(tmp_path / "data.csv")) == {"a": "alpha", "b": "beta"}

    def test_no_sidecar_returns_empty(self, tmp_path):
        assert extract_csv_sidecar_comments(str(tmp_path / "data.csv")) == {}

    def test_blank_values_dropped(self, tmp_path):
        (tmp_path / "data.columns.json").write_text('{"a": "alpha", "b": "  "}', encoding="utf-8")
        assert extract_csv_sidecar_comments(str(tmp_path / "data.csv")) == {"a": "alpha"}


class TestAttachFieldComments:
    def test_attaches_comment_metadata(self):
        table = pa.table({"user_id": [1], "name": ["a"]})
        out = attach_field_comments(table, {"user_id": "primary key"})
        assert out.schema.field("user_id").metadata[b"comment"] == b"primary key"
        assert out.schema.field("name").metadata in (None, {})

    def test_unknown_field_ignored(self):
        table = pa.table({"a": [1]})
        out = attach_field_comments(table, {"missing": "x"})
        assert out.schema.field("a").metadata in (None, {})

    def test_empty_comments_returns_same_table(self):
        table = pa.table({"a": [1]})
        assert attach_field_comments(table, {}) is table

    def test_preserves_existing_metadata(self):
        f = pa.field("a", pa.int64(), metadata={b"keep": b"yes"})
        table = pa.Table.from_arrays([pa.array([1])], schema=pa.schema([f]))
        out = attach_field_comments(table, {"a": "new"})
        md = out.schema.field("a").metadata
        assert md[b"keep"] == b"yes"
        assert md[b"comment"] == b"new"


class TestCaptureForFile:
    def test_parquet_dispatch(self, tmp_path):
        p = tmp_path / "orders.parquet"
        _write_parquet(p, comments=[("user_id", "pk", pa.int64())])
        table = pa.table({"user_id": [1]})  # Daft-stripped (no metadata)
        out = capture_for_file(str(p), "parquet", table)
        assert out.schema.field("user_id").metadata[b"comment"] == b"pk"

    def test_unsupported_type_returns_table_unchanged(self):
        table = pa.table({"a": [1]})
        assert capture_for_file("x.json", "json", table) is table

    def test_invalid_path_returns_table_unchanged(self):
        table = pa.table({"a": [1]})
        # parquet path that does not exist → best-effort returns original table
        assert capture_for_file("/nonexistent/x.parquet", "parquet", table) is table


class TestIngestHookRoundTrip:
    def test_ingest_preserves_parquet_comments(self, tmp_path):
        # Full path: parquet with comments → Ingestor.ingest → Lance schema
        # carries the comments (Daft strips them, the _write_table hook
        # re-attaches). Requires a real local LanceStorageManager.
        from arrow_lake.config import StorageBackend, StorageConfig
        from arrow_lake.ingest.ingestor import Ingestor
        from arrow_lake.ingest.storage import LanceStorageManager

        src = tmp_path / "src"
        src.mkdir()
        lake_dir = tmp_path / "lake"
        _write_parquet(
            src / "orders.parquet",
            comments=[("user_id", "primary key", pa.int64()), ("amount", "order total", pa.float64())],
        )
        mgr = LanceStorageManager(StorageConfig(backend=StorageBackend.LOCAL, base_uri=str(lake_dir)))
        ing = Ingestor(mgr)
        report = ing.ingest("orders_ds", [str(src / "orders.parquet")])
        assert report.total_rows == 1

        schema = mgr.open_dataset("orders_ds").schema
        assert schema.field("user_id").metadata[b"comment"] == b"primary key"
        assert schema.field("amount").metadata[b"comment"] == b"order total"
