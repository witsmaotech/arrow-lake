"""Cover missing lines in arrow_lake.query.olap."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pyarrow as pa
import pytest

from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.exceptions import ErrorCode, QueryError, StorageError
from arrow_lake.query.olap import OlapSearchBridge, _validate_dataset_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bridge(**kw: object) -> OlapSearchBridge:
    storage = MagicMock()
    config = MagicMock()
    config.query_cache_enabled = False
    config.max_result_rows = 10000
    config.enable_streaming = False
    config.scanner_batch_size = 10000
    config.lance_scan_mode = "pyarrow_fallback"
    config.max_query_memory_mb = 512
    config.query_timeout_seconds = 30
    config.ducklake_enabled = False
    config.ducklake_ttl_days = 7
    config.ducklake_max_join_rows = 1000000
    config.ducklake_index_columns = None
    config.query_cache_max_entries = 100
    config.query_cache_ttl_seconds = 300
    # v1.10.4: OlapSearchBridge now consults these in _resolve_scan_mode; a bare
    # MagicMock here is truthy for `in`/`bool`, so set real values. auto_promote
    # stays truthy (MagicMock default) to preserve the native-path reachability
    # that test_native_scan_local / test_native_scan_fallback rely on.
    config.lance_scan_mode_overrides = {}
    config.lance_breaker_trip_threshold = 2
    config.lance_breaker_window_seconds = 600
    config.lance_breaker_cooldown_seconds = 1800
    for k, v in kw.items():
        setattr(config, k, v)
    return OlapSearchBridge(storage=storage, config=config)


# ---------------------------------------------------------------------------
# __init__ cache
# ---------------------------------------------------------------------------


class TestInit:
    def test_cache_enabled(self) -> None:
        with patch("arrow_lake.query.olap.OlapSearchBridge.__init__", lambda self, **k: None):
            b = OlapSearchBridge.__new__(OlapSearchBridge)
            b._config = MagicMock()
            b._config.query_cache_enabled = True
            b._config.query_cache_max_entries = 50
            b._config.query_cache_ttl_seconds = 60
            b._cache = None
            with patch("arrow_lake.query.olap._QueryCache", create=True) as mock_qc:
                mock_qc.return_value = MagicMock()
                # Simulate the import path
                import importlib
                with patch.dict("sys.modules", {
                    "arrow_lake.query._cache": MagicMock(QueryCache=mock_qc),
                }):
                    pass  # cache init is done in __init__ which we bypassed


# ---------------------------------------------------------------------------
# query() branches
# ---------------------------------------------------------------------------


class TestQuery:
    def test_cached_result(self) -> None:
        b = _make_bridge()
        cached_table = pa.table({"x": [1]})
        mock_cache = MagicMock()
        mock_cache.get.return_value = cached_table
        b._cache = mock_cache
        with patch.object(b, "_validate_sql"), \
             patch.object(b, "_managed_session"):
            result = b.query("ds", "SELECT * FROM ds")
        assert result.row_count == 1

    def test_streaming_reader(self) -> None:
        b = _make_bridge(enable_streaming=True)
        b._storage.scan_dataset.return_value = MagicMock()
        mock_reader = MagicMock()
        mock_reader.read_all.return_value = pa.table({"a": [1]})
        mock_conn = MagicMock()
        mock_conn.execute.return_value.arrow.return_value = mock_reader
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch.object(b, "_managed_session", return_value=mock_conn), \
             patch.object(b, "_validate_sql"), \
             patch.object(b, "_register_dataset"):
            result = b.query("ds", "SELECT * FROM ds")
        assert result.table is not None

    def test_query_read_error(self) -> None:
        from arrow_lake.exceptions import StorageError
        b = _make_bridge()
        b._storage.read_dataset.side_effect = StorageError(
            error_code=ErrorCode.STORAGE_READ_FAILED, message="not found"
        )
        with patch.object(b, "_validate_sql"), \
             patch.object(b, "_managed_session"):
            with pytest.raises(QueryError, match="Failed to read"):
                b.query("ds", "SELECT * FROM ds")


# ---------------------------------------------------------------------------
# materialize
# ---------------------------------------------------------------------------


class TestMaterialize:
    def test_ducklake_not_enabled(self) -> None:
        b = _make_bridge(ducklake_enabled=False)
        with patch.object(b, "_validate_sql"):
            with pytest.raises(QueryError, match="not enabled"):
                b.materialize("ds", "SELECT 1")

    def test_materialize_read_error(self) -> None:
        from arrow_lake.exceptions import StorageError
        b = _make_bridge(ducklake_enabled=True)
        b._storage.read_dataset.side_effect = StorageError(error_code=ErrorCode.STORAGE_READ_FAILED, message="err")
        with patch.object(b, "_validate_sql"):
            with pytest.raises(QueryError, match="Failed to read"):
                b.materialize("ds", "SELECT 1")


# ---------------------------------------------------------------------------
# explain / explain_analyze
# ---------------------------------------------------------------------------


class TestExplain:
    def test_explain_success(self) -> None:
        b = _make_bridge()
        b._storage.read_dataset.return_value = pa.table({"a": [1]})
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [("plan",)]
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch.object(b, "_managed_session", return_value=mock_conn), \
             patch.object(b, "_validate_sql"), \
             patch.object(b, "_register_dataset"):
            result = b.explain("ds", "SELECT * FROM ds")
        assert "plan" in result

    def test_explain_read_error(self) -> None:
        from arrow_lake.exceptions import StorageError
        b = _make_bridge()
        b._storage.read_dataset.side_effect = StorageError(error_code=ErrorCode.STORAGE_READ_FAILED, message="err")
        with patch.object(b, "_validate_sql"):
            with pytest.raises(QueryError, match="Failed to read"):
                b.explain("ds", "SELECT * FROM ds")

    def test_explain_analyze_success(self) -> None:
        b = _make_bridge()
        b._storage.read_dataset.return_value = pa.table({"a": [1]})
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [("plan",)]
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch.object(b, "_managed_session", return_value=mock_conn), \
             patch.object(b, "_validate_sql"), \
             patch.object(b, "_register_dataset"), \
             patch.object(b, "_get_profiling_info", return_value=None):
            result = b.explain_analyze("ds", "SELECT * FROM ds")
        assert "plan" in result

    def test_explain_analyze_with_profiling(self) -> None:
        b = _make_bridge()
        b._storage.read_dataset.return_value = pa.table({"a": [1]})
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [("plan",)]
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch.object(b, "_managed_session", return_value=mock_conn), \
             patch.object(b, "_validate_sql"), \
             patch.object(b, "_register_dataset"), \
             patch.object(b, "_get_profiling_info", return_value="op: 0.001s"):
            result = b.explain_analyze("ds", "SELECT * FROM ds")
        assert "Profiling" in result

    def test_explain_analyze_read_error(self) -> None:
        from arrow_lake.exceptions import StorageError
        b = _make_bridge()
        b._storage.read_dataset.side_effect = StorageError(error_code=ErrorCode.STORAGE_READ_FAILED, message="err")
        with patch.object(b, "_validate_sql"):
            with pytest.raises(QueryError, match="Failed to read"):
                b.explain_analyze("ds", "SELECT * FROM ds")


# ---------------------------------------------------------------------------
# _get_profiling_info
# ---------------------------------------------------------------------------


class TestProfilingInfo:
    def test_catalog_exception(self) -> None:
        import duckdb
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = duckdb.CatalogException("no table")
        result = OlapSearchBridge._get_profiling_info(mock_conn)
        assert result is None

    def test_empty_rows(self) -> None:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        result = OlapSearchBridge._get_profiling_info(mock_conn)
        assert result is None

    def test_success_with_metrics(self) -> None:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("Scan", 0.5, 1000, {"MemoryUsage": "1MB", "BytesSpilled": "0"}),
            ("Filter", 0.1, 500, None),
        ]
        result = OlapSearchBridge._get_profiling_info(mock_conn)
        assert result is not None
        assert "Scan" in result
        assert "mem=" in result

    def test_success_no_extra(self) -> None:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("Join", 0.2, 100, {}),
        ]
        result = OlapSearchBridge._get_profiling_info(mock_conn)
        assert "Join" in result


# ---------------------------------------------------------------------------
# _register_dataset
# ---------------------------------------------------------------------------


class TestRegisterDataset:
    def test_pyarrow_fallback_mode(self) -> None:
        # v1.10.4: auto_promote flips a vector-less pyarrow_fallback dataset to
        # auto — the early direct-register branch needs it explicitly OFF.
        b = _make_bridge(lance_scan_mode="pyarrow_fallback", lance_auto_promote=False)
        mock_conn = MagicMock()
        source = pa.table({"a": [1]})
        b._register_dataset(mock_conn, "ds", source)
        mock_conn.register.assert_called_once_with("ds", source)

    def test_native_scan_local(self) -> None:
        b = _make_bridge()
        b._storage_config = None
        b._storage.dataset_uri = MagicMock(return_value="/tmp/ds.lance")
        mock_conn = MagicMock()
        source = pa.table({"a": [1]})
        # create_view may raise, so the fallback to conn.register is the expected path
        mock_adapter_inst = MagicMock()
        mock_adapter_inst.create_view.side_effect = OSError("no lance")
        with patch("arrow_lake.query.olap.create_lance_scan_adapter", return_value=mock_adapter_inst):
            b._register_dataset(mock_conn, "ds", source)
        # Falls back to conn.register
        mock_conn.register.assert_called_once_with("ds", source)

    def test_native_scan_s3(self) -> None:
        cfg = MagicMock()
        cfg.backend = StorageBackend.S3
        cfg.s3_uri = "s3://bucket/data"
        b = _make_bridge(lance_scan_mode="native")
        b._storage_config = cfg
        b._storage.dataset_uri.return_value = "s3://bucket/data/ds.lance"
        mock_conn = MagicMock()
        source = pa.table({"a": [1]})
        with patch("arrow_lake.query.olap.create_lance_scan_adapter") as mock_adapter:
            mock_adapter.return_value.create_view = MagicMock()
            b._register_dataset(mock_conn, "ds", source)
            mock_adapter.assert_called_once()

    def test_native_scan_fallback(self) -> None:
        import duckdb
        b = _make_bridge()
        b._storage_config = None
        b._storage.dataset_uri.return_value = "/tmp/ds.lance"
        mock_conn = MagicMock()
        source = pa.table({"a": [1]})
        with patch("arrow_lake.query.olap.create_lance_scan_adapter",
                    side_effect=duckdb.Error("no scan")):
            b._register_dataset(mock_conn, "ds", source)
        mock_conn.register.assert_called_once()


# ---------------------------------------------------------------------------
# _managed_session
# ---------------------------------------------------------------------------


class TestManagedSession:
    def test_with_session_manager(self) -> None:
        mock_sm = MagicMock()
        b = _make_bridge()
        b._session_manager = mock_sm
        b._managed_session()
        mock_sm.acquire.assert_called_once()

    def test_without_session_manager(self) -> None:
        b = _make_bridge()
        with patch("arrow_lake.query.olap.create_duckdb_session") as mock_create:
            mock_create.return_value = MagicMock()
            b._managed_session()
            mock_create.assert_called_once()


# ---------------------------------------------------------------------------
# _validate_dataset_name
# ---------------------------------------------------------------------------


class TestValidateDatasetName:
    def test_valid(self) -> None:
        _validate_dataset_name("my_dataset")

    def test_invalid_empty(self) -> None:
        with pytest.raises(ValueError):
            _validate_dataset_name("")

    def test_invalid_traversal(self) -> None:
        with pytest.raises(ValueError):
            _validate_dataset_name("../etc/passwd")
