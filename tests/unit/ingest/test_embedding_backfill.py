"""M1 embedding null-backfill tests (v1.10.2 §5.3) + P1.4 async backfill (§5.3 P1.4).

Covers ``Lake._backfill_embedding_nulls`` without the heavy Lake/storage wiring:
a FakeLake provides ``_get_storage`` (read/drop/add on a synthetic Arrow table)
+ ``_encode_texts`` (records what got encoded, returns fixed vecs). This pins
the core invariant — only NULL rows are encoded, non-null rows are preserved at
their original indices.

The P1.4 tests (``test_ingest_*`` / ``test_estimate_*``) drive the full
``ingest_documents_and_index`` post-step via an unbound ``Lake`` method on a
FakeLake, asserting the sync-vs-async gate and background status tracking.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
import pyarrow as pa


def _fake_storage(table: pa.Table):
    class _FakeLanceTable:
        """Stand-in for a lance table; count_rows(filter) emulates null pushdown."""

        def count_rows(self, filter: str | None = None) -> int:
            if filter and "IS NULL" in filter:
                for col in table.column_names:
                    if col in filter:
                        return int(table.column(col).is_null().to_numpy(zero_copy_only=False).sum())
            return table.num_rows

    class _FakeStorage:
        def has_column(self, name: str, column_name: str) -> bool:
            return column_name in table.column_names

        def read_dataset(self, name: str, columns=None) -> pa.Table:
            return table

        def open_dataset(self, name: str) -> _FakeLanceTable:
            return _FakeLanceTable()

        def drop_column(self, name: str, col: str) -> None:
            self.dropped = col  # type: ignore[attr-defined]

        def add_columns_table(self, name: str, cols: pa.Table) -> None:
            self.added = cols  # type: ignore[attr-defined]

    return _FakeStorage()


@pytest.fixture(autouse=True)
def _reset_embed_bg(monkeypatch):
    """Clear background-embed status + isolate from Redis between tests."""
    from arrow_lake._lake_ingest import _EMBED_BG_LOCK, _embed_bg

    # Default: no Redis in unit tests (individual tests mock it explicitly).
    monkeypatch.setattr("arrow_lake._lake_ingest._embed_redis_client", lambda: None)
    with _EMBED_BG_LOCK:
        _embed_bg.clear()
    yield
    # Drain any lingering background task before the next test starts.
    time.sleep(0.05)
    with _EMBED_BG_LOCK:
        _embed_bg.clear()


# ---------------------------------------------------------------------------
# P1.1–P1.3: _backfill_embedding_nulls core invariants (existing)
# ---------------------------------------------------------------------------


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
        def has_column(self, name: str, col: str) -> bool:
            return col in tbl.column_names

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


# ---------------------------------------------------------------------------
# P1.4: async embed+vector backfill gate + status tracking
# ---------------------------------------------------------------------------


def _make_async_lake(storage, *, threshold: int = 5, dim: int = 4,
                     encode_delay: float = 0.0, fail: bool = False):
    """Build a FakeLake driving ``ingest_documents_and_index``'s post-step.

    Binds real ``Lake`` unbound methods (embed_and_add / _estimate_null_embeddings
    / _run_embed_and_vector_bg / get_embed_backfill_status) so the post-step logic
    runs against the FakeLake's storage/encode mocks. Returns (lake, state).
    """
    from arrow_lake import Lake
    from arrow_lake.ingest.ingestor import IngestionReport

    class _EmbCfg:
        embed_async_threshold = threshold
        batch_size = 32

    class _Cfg:
        embedding = _EmbCfg()

    cfg = _Cfg()
    state = {"encode_calls": 0, "fts": 0, "vector": 0}

    class _FL:
        embed_and_add = Lake.embed_and_add
        _backfill_embedding_nulls = Lake._backfill_embedding_nulls
        _estimate_null_embeddings = Lake._estimate_null_embeddings
        _run_embed_and_vector_bg = Lake._run_embed_and_vector_bg
        get_embed_backfill_status = Lake.get_embed_backfill_status

        def _get_storage(self):
            return storage

        @property
        def _config(self):
            return cfg

        def ingest_documents(self, name, paths, *, doc_config=None,
                             doc_type=None, actor="system"):
            return IngestionReport()

        def _encode_texts(self, texts, emb_cfg, batch_size=None):
            state["encode_calls"] += 1
            if encode_delay:
                time.sleep(encode_delay)
            if fail:
                raise RuntimeError("encode boom")
            n = len(texts)
            return np.zeros((n, dim), dtype=np.float32), dim

        def create_fts_index(self, name):
            state["fts"] += 1

        def create_vector_index(self, name):
            state["vector"] += 1

    return _FL(), state


def _poll_status(lake, name: str, timeout: float = 5.0):
    """Poll get_embed_backfill_status until non-running or timeout."""
    deadline = time.time() + timeout
    st = None
    while time.time() < deadline:
        st = lake.get_embed_backfill_status(name)
        if st and st["status"] != "running":
            return st
        time.sleep(0.02)
    return st


def test_estimate_null_embeddings_counts_nulls() -> None:
    from arrow_lake import Lake

    dim = 4
    emb = pa.array([[1., 1, 1, 1], None, [3., 3, 3, 3]], type=pa.list_(pa.float32(), dim))
    tbl = pa.table({"text_content": pa.array(["a", "b", "c"]), "text_embedding": emb})
    lake, _ = _make_async_lake(_fake_storage(tbl), dim=dim)
    assert Lake._estimate_null_embeddings(lake, "ds") == 1


def test_estimate_null_embeddings_first_time_returns_row_count() -> None:
    from arrow_lake import Lake

    tbl = pa.table({"text_content": pa.array(["a", "b", "c", "d"])})  # no emb column
    lake, _ = _make_async_lake(_fake_storage(tbl), dim=4)
    assert Lake._estimate_null_embeddings(lake, "ds") == 4


def test_ingest_sync_when_small_null() -> None:
    """P1.4: null ≤ threshold → synchronous inline embed+vector (regression)."""
    from arrow_lake import Lake

    dim = 4
    emb = pa.array([[1., 1, 1, 1], None], type=pa.list_(pa.float32(), dim))  # 1 null < 5
    tbl = pa.table({"text_content": pa.array(["a", "b"]), "text_embedding": emb})
    lake, state = _make_async_lake(_fake_storage(tbl), threshold=5, dim=dim)

    report = Lake.ingest_documents_and_index(lake, "ds", [])

    assert report.embed_async is None  # synchronous — no async marker
    assert state["fts"] == 1
    assert state["encode_calls"] == 1  # embed ran inline
    assert state["vector"] == 1  # vector ran inline
    assert lake.get_embed_backfill_status("ds") is None  # no background task


def test_ingest_defers_when_large_null() -> None:
    """P1.4: null > threshold → fire-and-forget; request returns immediately."""
    from arrow_lake import Lake

    dim = 4
    n = 10  # > threshold 5
    emb = pa.array([None] * n, type=pa.list_(pa.float32(), dim))
    tbl = pa.table({"text_content": pa.array([f"t{i}" for i in range(n)]),
                    "text_embedding": emb})
    lake, state = _make_async_lake(_fake_storage(tbl), threshold=5, dim=dim)

    report = Lake.ingest_documents_and_index(lake, "ds", [])

    # Request returned immediately with the async marker.
    assert report.embed_async is not None
    assert report.embed_async["status"] == "running"
    assert report.embed_async["null_rows"] == n
    # FTS ran inline (doesn't depend on embedding).
    assert state["fts"] == 1
    # vector is built in the background after embed — verified post-poll below.

    st = _poll_status(lake, "ds")
    assert st is not None and st["status"] == "completed"
    # embed + vector ran in the background, in order.
    assert state["encode_calls"] == 1
    assert state["vector"] == 1


def test_ingest_async_failure_recorded() -> None:
    """P1.4: background encode failure → status 'failed' + error; ingest doesn't raise."""
    from arrow_lake import Lake

    dim = 4
    n = 10
    emb = pa.array([None] * n, type=pa.list_(pa.float32(), dim))
    tbl = pa.table({"text_content": pa.array([f"t{i}" for i in range(n)]),
                    "text_embedding": emb})
    lake, _ = _make_async_lake(_fake_storage(tbl), threshold=5, dim=dim, fail=True)

    report = Lake.ingest_documents_and_index(lake, "ds", [])
    assert report.embed_async is not None  # deferred (failure happens in bg)

    st = _poll_status(lake, "ds")
    assert st is not None and st["status"] == "failed"
    assert st["error"]  # non-empty error captured


def test_ingest_skips_embed_while_running() -> None:
    """P1.4: a 2nd ingest while backfill is running skips embed+vector (dedupe)."""
    from arrow_lake import Lake

    dim = 4
    n = 10
    emb = pa.array([None] * n, type=pa.list_(pa.float32(), dim))
    tbl = pa.table({"text_content": pa.array([f"t{i}" for i in range(n)]),
                    "text_embedding": emb})
    # Slow encode keeps the bg task in "running" while we trigger a 2nd ingest.
    lake, state = _make_async_lake(_fake_storage(tbl), threshold=5, dim=dim,
                                   encode_delay=0.5)

    report1 = Lake.ingest_documents_and_index(lake, "ds", [])
    assert report1.embed_async is not None  # first: deferred

    # 2nd ingest while bg still running → must skip (no 2nd async submission,
    # no inline embed racing the running task).
    report2 = Lake.ingest_documents_and_index(lake, "ds", [])
    assert report2.embed_async is None  # NOT deferred again
    assert state["encode_calls"] <= 1  # the 2nd ingest did not start another encode

    st = _poll_status(lake, "ds", timeout=10)
    assert st is not None and st["status"] in ("completed", "failed")
    # Still only one embed across both ingests — the 2nd was skipped.
    assert state["encode_calls"] == 1


def test_get_status_none_when_no_backfill() -> None:
    """P1.4: get_embed_backfill_status returns None when nothing ever ran."""
    lake, _ = _make_async_lake(_fake_storage(pa.table({"text_content": pa.array(["a"])})))
    assert lake.get_embed_backfill_status("ds") is None


# ---------------------------------------------------------------------------
# P1.4 G11: cross-worker Redis mirror
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal in-memory Redis stand-in for set/get."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key: str, val: str, ex: int | None = None) -> None:
        self.store[key] = val

    def get(self, key: str):
        return self.store.get(key)


def test_embed_status_redis_sync_and_read(monkeypatch) -> None:
    """G11: _sync_embed_status_redis round-trips through Redis."""
    from arrow_lake._lake_ingest import (
        _read_embed_status_redis,
        _sync_embed_status_redis,
    )

    fake = _FakeRedis()
    monkeypatch.setattr("arrow_lake._lake_ingest._embed_redis_client", lambda: fake)
    payload = {"status": "completed", "null_rows": 5, "error": None,
               "started_at": "t0", "finished_at": "t1"}
    _sync_embed_status_redis("ds", payload)
    assert _read_embed_status_redis("ds") == payload


def test_get_status_falls_back_to_redis(monkeypatch) -> None:
    """G11: when this worker has no in-memory record, fall back to Redis."""
    from arrow_lake._lake_ingest import _EMBED_REDIS_PREFIX

    fake = _FakeRedis()
    fake.store[f"{_EMBED_REDIS_PREFIX}:other_worker_ds"] = (
        '{"status": "running", "null_rows": 9}'
    )
    monkeypatch.setattr("arrow_lake._lake_ingest._embed_redis_client", lambda: fake)
    lake, _ = _make_async_lake(_fake_storage(pa.table({"text_content": pa.array(["a"])})))
    # In-memory _embed_bg has no "other_worker_ds" (cleared by fixture) → Redis.
    st = lake.get_embed_backfill_status("other_worker_ds")
    assert st == {"status": "running", "null_rows": 9}
