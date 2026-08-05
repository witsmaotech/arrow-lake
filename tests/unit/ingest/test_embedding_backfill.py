"""M1 P1 embedding null-backfill tests (v1.10.2 §5.3).

Covers ``Lake._backfill_embedding_nulls`` without the heavy Lake/storage
wiring: a FakeLake provides ``_get_storage`` (read/drop/add on a synthetic
Arrow table) + ``_encode_texts`` (records what got encoded, returns fixed vecs).
This pins the core invariant — only NULL rows are encoded, non-null rows are
preserved at their original indices.
"""

from __future__ import annotations

import numpy as np
import pytest
import pyarrow as pa


def _fake_storage(table: pa.Table):
    class _FakeStorage:
        def read_dataset(self, name: str, columns=None) -> pa.Table:
            return table

        def drop_column(self, name: str, col: str) -> None:
            self.dropped = col  # type: ignore[attr-defined]

        def add_columns_table(self, name: str, cols: pa.Table) -> None:
            self.added = cols  # type: ignore[attr-defined]

    return _FakeStorage()


def test_backfill_encodes_only_null_rows_and_preserves_rest() -> None:
    from arrow_lake import Lake

    dim = 4
    emb = pa.array([[1., 1, 1, 1], None, [3., 3, 3, 3]], type=pa.list_(pa.float32(), dim))
    tbl = pa.table({"text_content": pa.array(["a", "b", "c"]), "text_embedding": emb})
    storage = _fake_storage(tbl)
    encoded: dict = {}

    class FakeLake:
        def _get_storage(self):
            return storage

        def _encode_texts(self, texts, cfg, bs=None):
            encoded["texts"] = list(texts)
            return np.array([[9., 9, 9, 9]], dtype=np.float32), dim

    n = Lake._backfill_embedding_nulls(
        FakeLake(), "ds", "text_content", "text_embedding", None
    )
    assert n == 1
    assert encoded["texts"] == ["b"]  # ONLY the null row was encoded
    result = storage.added.column("text_embedding").to_pylist()
    assert result == [[1., 1, 1, 1], [9., 9, 9, 9], [3., 3, 3, 3]]  # non-null kept


def test_backfill_no_null_is_noop() -> None:
    from arrow_lake import Lake

    dim = 4
    emb = pa.array([[1., 1, 1, 1], [2., 2, 2, 2]], type=pa.list_(pa.float32(), dim))
    tbl = pa.table({"text_content": pa.array(["a", "b"]), "text_embedding": emb})
    storage = _fake_storage(tbl)

    class FakeLake:
        def _get_storage(self):
            return storage

        def _encode_texts(self, *a, **k):
            raise AssertionError("must not encode when there are no null rows")

    n = Lake._backfill_embedding_nulls(
        FakeLake(), "ds", "text_content", "text_embedding", None
    )
    assert n == 0


def test_backfill_all_null_encodes_all() -> None:
    from arrow_lake import Lake

    dim = 4
    emb = pa.array([None, None], type=pa.list_(pa.float32(), dim))
    tbl = pa.table({"text_content": pa.array(["a", "b"]), "text_embedding": emb})
    storage = _fake_storage(tbl)

    class FakeLake:
        def _get_storage(self):
            return storage

        def _encode_texts(self, texts, cfg, bs=None):
            return np.array([[1., 1, 1, 1], [2., 2, 2, 2]], dtype=np.float32), dim

    n = Lake._backfill_embedding_nulls(
        FakeLake(), "ds", "text_content", "text_embedding", None
    )
    assert n == 2
    result = storage.added.column("text_embedding").to_pylist()
    assert result == [[1., 1, 1, 1], [2., 2, 2, 2]]


def test_backfill_aborts_on_concurrent_append() -> None:
    """P1.3 TOCTOU: row count changed mid-build (concurrent append) → abort."""
    from arrow_lake import Lake
    from arrow_lake.exceptions import StorageError

    dim = 4
    emb = pa.array([[1., 1, 1, 1], None, [3., 3, 3, 3]], type=pa.list_(pa.float32(), dim))
    tbl = pa.table({"text_content": pa.array(["a", "b", "c"]), "text_embedding": emb})
    tbl_after_append = pa.table({"text_content": pa.array(["a", "b", "c", "d"])})
    calls = {"n": 0}

    class FakeStorage:
        def read_dataset(self, name: str, columns=None):
            calls["n"] += 1
            return tbl if calls["n"] == 1 else tbl_after_append  # 2nd read = post-append

        def drop_column(self, name: str, col: str) -> None:
            raise AssertionError("must NOT drop when TOCTOU check fails")

        def add_columns_table(self, name: str, cols: pa.Table) -> None:
            raise AssertionError("must NOT add when TOCTOU check fails")

    class FakeLake:
        def _get_storage(self):
            return FakeStorage()

        def _encode_texts(self, texts, cfg, bs=None):
            return np.array([[9., 9, 9, 9]], dtype=np.float32), dim

    with pytest.raises(StorageError, match="changed mid-build"):
        Lake._backfill_embedding_nulls(
            FakeLake(), "ds", "text_content", "text_embedding", None
        )
