"""Unit tests for schema comment exposure (model + _schema_field_dicts + storage)."""

from __future__ import annotations

import pyarrow as pa

from arrow_lake.api.models.dataset import SchemaAnnotateRequest, SchemaField
from arrow_lake.api.routers.datasets import _schema_field_dicts


class TestSchemaFieldModel:
    def test_comment_defaults_empty(self):
        f = SchemaField(name="x", type="int64")
        assert f.comment == ""
        assert f.nullable is True

    def test_comment_round_trip(self):
        f = SchemaField(name="x", type="int64", nullable=False, comment="primary key")
        assert f.comment == "primary key"
        assert f.nullable is False

    def test_annotate_request(self):
        req = SchemaAnnotateRequest(field="x", comment="hello")
        assert req.field == "x"
        assert req.comment == "hello"
        # empty comment allowed (clears the annotation)
        assert SchemaAnnotateRequest(field="x").comment == ""


class TestSchemaFieldDicts:
    def test_reads_comment_metadata(self):
        schema = pa.schema([pa.field("user_id", pa.int64(), metadata={b"comment": b"primary key"})])
        rows = _schema_field_dicts(schema)
        assert rows == [
            {"name": "user_id", "type": "int64", "nullable": True, "comment": "primary key"}
        ]

    def test_description_fallback(self):
        schema = pa.schema([pa.field("amount", pa.float64(), metadata={b"description": b"total"})])
        assert _schema_field_dicts(schema)[0]["comment"] == "total"

    def test_no_metadata_empty_comment(self):
        schema = pa.schema([pa.field("c", pa.string())])
        row = _schema_field_dicts(schema)[0]
        assert row["comment"] == ""
        assert row["nullable"] is True

    def test_comment_takes_precedence_over_description(self):
        schema = pa.schema(
            [pa.field("c", pa.int64(), metadata={b"comment": b"win", b"description": b"lose"})]
        )
        assert _schema_field_dicts(schema)[0]["comment"] == "win"


class TestUpdateFieldCommentsStorage:
    def test_persists_and_reads_back(self, tmp_path):
        from arrow_lake.config import StorageBackend, StorageConfig
        from arrow_lake.ingest.storage import LanceStorageManager

        mgr = LanceStorageManager(StorageConfig(backend=StorageBackend.LOCAL, base_uri=str(tmp_path)))
        mgr.create_dataset("t", pa.table({"user_id": [1], "name": ["a"]}))
        mgr.update_field_comments("t", {"user_id": "primary key", "name": "full name"})

        rows = _schema_field_dicts(mgr.open_dataset("t").schema)
        by_name = {r["name"]: r["comment"] for r in rows}
        assert by_name == {"user_id": "primary key", "name": "full name"}

    def test_missing_dataset_raises(self, tmp_path):
        from arrow_lake.config import StorageBackend, StorageConfig
        from arrow_lake.ingest.storage import LanceStorageManager

        mgr = LanceStorageManager(StorageConfig(backend=StorageBackend.LOCAL, base_uri=str(tmp_path)))
        from arrow_lake.exceptions import StorageError

        try:
            mgr.update_field_comments("nope", {"a": "b"})
        except StorageError:
            return
        raise AssertionError("expected StorageError for missing dataset")

    def test_empty_payload_noop(self, tmp_path):
        from arrow_lake.config import StorageBackend, StorageConfig
        from arrow_lake.ingest.storage import LanceStorageManager

        mgr = LanceStorageManager(StorageConfig(backend=StorageBackend.LOCAL, base_uri=str(tmp_path)))
        mgr.create_dataset("t", pa.table({"a": [1]}))
        mgr.update_field_comments("t", {})  # must not raise
        assert _schema_field_dicts(mgr.open_dataset("t").schema)[0]["comment"] == ""
