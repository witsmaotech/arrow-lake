"""Typed placeholder columns (v1.11.6) — storage-level behavior.

Covers the mode-B add path that Lance SQL expressions cannot produce
(vector / blob / timestamp / date32) plus the blob version wall:
blob columns require data files at version >= 2.2 while lancedb writes
2.1 by default (live-probed 2026-09-08).
"""

from __future__ import annotations

from pathlib import Path

import lance
import pyarrow as pa
import pytest

from arrow_lake.ingest.schema import SchemaMigrationError, resolve_lance_type
from arrow_lake.ingest.storage import LanceStorageManager


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path))


def _make_table(storage: LanceStorageManager, name: str, n: int = 3) -> None:
    storage.create_dataset(
        name,
        pa.table({"id": pa.array(list(range(n)), pa.int64()), "s": ["a"] * n}),
    )


def _schema_of(storage: LanceStorageManager, name: str) -> pa.Schema:
    return storage.open_dataset(name).schema


class TestPlaceholderScalars:
    """Non-expression types land as all-NULL columns of the right type."""

    @pytest.mark.parametrize("spec,expected", [
        ("binary", pa.binary()),
        ("large_binary", pa.large_binary()),
        ("timestamp", pa.timestamp("us")),
        ("date32", pa.date32()),
        ("bool", pa.bool_()),
    ])
    def test_scalar_placeholder_type_lands(self, storage, spec, expected) -> None:
        _make_table(storage, "ds")
        data_type, is_blob = resolve_lance_type(spec)
        assert is_blob is False
        storage.add_null_column("ds", "col", data_type)
        schema = _schema_of(storage, "ds")
        assert schema.field("col").type == expected
        # all-NULL: the column exists but carries no values yet
        table = storage.read_dataset("ds", columns=["col"])
        assert table.num_rows == 3
        assert all(v is None for v in table.column("col").to_pylist())

    def test_existing_column_rejected(self, storage) -> None:
        _make_table(storage, "ds")
        data_type, _ = resolve_lance_type("binary")
        with pytest.raises(Exception, match="Failed to add placeholder column"):
            storage.add_null_column("ds", "s", data_type)


class TestPlaceholderVector:
    """vector:<dim> placeholders — the pre-backfill step of the embed path."""

    def test_vector_placeholder_lands_fixed_size_list(self, storage) -> None:
        _make_table(storage, "ds", n=4)
        data_type, is_blob = resolve_lance_type("vector:384")
        assert is_blob is False
        assert data_type == pa.list_(pa.float32(), 384)
        storage.add_null_column("ds", "text_embedding", data_type)
        schema = _schema_of(storage, "ds")
        emb = schema.field("text_embedding").type
        assert pa.types.is_fixed_size_list(emb)
        assert emb.list_size == 384
        # backfill contract: the column reads back all-NULL, matching the
        # has_column → _backfill_embedding_nulls branch in embed_and_add
        col = storage.read_dataset("ds", columns=["text_embedding"]).column("text_embedding")
        assert all(v is None for v in col.to_pylist())


class TestBlobVersionWall:
    """blob columns need data files >= 2.2; lancedb defaults to 2.1."""

    def test_blob_on_default_table_hits_version_wall(self, storage) -> None:
        _make_table(storage, "ds")  # lancedb write → data files 2.1
        data_type, is_blob = resolve_lance_type("blob")
        assert is_blob is True
        with pytest.raises(SchemaMigrationError, match="2.2"):
            storage.add_null_column("ds", "photo", data_type, blob=True)

    def test_blob_on_2_2_table_lands(self, storage) -> None:
        _make_table(storage, "ds")
        # Rewrite the table's data files at 2.2 (the documented escape path)
        data = storage.read_dataset("ds")
        lance.write_dataset(
            data, storage.dataset_uri("ds"),
            mode="overwrite", data_storage_version="2.2",
        )
        data_type, is_blob = resolve_lance_type("blob")
        storage.add_null_column("ds", "photo", data_type, blob=True)
        schema = _schema_of(storage, "ds")
        field = schema.field("photo")
        # lance.blob.v2 is an extension type; assert it is not a plain scalar
        assert not pa.types.is_binary(field.type)
        assert "blob" in str(field.type).lower()
