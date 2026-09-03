"""Tests for arrow_lake.query.metadata — MetadataSearchBridge and MetadataQueryResult."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake.exceptions import ErrorCode, QueryError, StorageError
from arrow_lake.query.metadata import MetadataQueryResult, MetadataSearchBridge

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def storage():
    mock = MagicMock()
    batch = pa.record_batch(
        [pa.array([1, 2], type=pa.int64()), pa.array(["alice", "bob"], type=pa.string())],
        names=["id", "name"],
    )
    mock.scan_dataset.return_value = batch
    return mock


@pytest.fixture()
def result_table():
    return pa.table({"id": [1, 2], "name": ["alice", "bob"]})


# ---------------------------------------------------------------------------
# MetadataQueryResult tests
# ---------------------------------------------------------------------------

class TestMetadataQueryResult:
    def test_to_arrow_returns_table(self):
        table = pa.table({"x": [1, 2, 3]})
        result = MetadataQueryResult(
            table=table, row_count=3, column_count=1, sql="SELECT x"
        )
        assert result.to_arrow() is table

    def test_frozen_dataclass(self):
        table = pa.table({"x": [1]})
        result = MetadataQueryResult(
            table=table, row_count=1, column_count=1, sql="SELECT x"
        )
        with pytest.raises(AttributeError):
            result.row_count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MetadataSearchBridge — validation tests
# ---------------------------------------------------------------------------

class TestMetadataSearchBridgeValidation:
    @pytest.fixture()
    def bridge(self, storage):
        return MetadataSearchBridge(storage)

    def test_invalid_dataset_name_rejects_special_chars(self, bridge):
        with pytest.raises(ValueError, match="Invalid dataset name"):
            bridge.query("bad;name", "SELECT 1")

    def test_invalid_dataset_name_rejects_leading_digit(self, bridge):
        with pytest.raises(ValueError, match="Invalid dataset name"):
            bridge.query("1dataset", "SELECT 1")

    def test_non_select_sql_raises(self, bridge):
        with pytest.raises(QueryError, match="Only SELECT") as exc_info:
            bridge.query("my_ds", "INSERT INTO t VALUES (1)")
        assert exc_info.value.error_code == ErrorCode.QUERY_SYNTAX_ERROR

    def test_dangerous_keyword_blocked(self, bridge):
        with pytest.raises(QueryError, match="Keyword 'DROP' is not allowed") as exc_info:
            bridge.query("my_ds", "SELECT DROP table")
        assert exc_info.value.error_code == ErrorCode.QUERY_SYNTAX_ERROR

    def test_semicolon_blocked(self, bridge):
        with pytest.raises(QueryError, match="Semicolons are not allowed") as exc_info:
            bridge.query("my_ds", "SELECT 1;")
        assert exc_info.value.error_code == ErrorCode.QUERY_SYNTAX_ERROR

    def test_union_of_selects_allowed(self, bridge):
        """v1.10.8+: AST validation — UNION of SELECTs is read-only, allowed."""
        bridge.query("my_ds", "SELECT 1 UNION SELECT 2")

    def test_extra_table_invalid_name_raises(self, bridge):
        with pytest.raises(ValueError):
            bridge.query(
                "my_ds",
                "SELECT * FROM my_ds JOIN bad_name ON my_ds.id = bad_name.id",
                tables={"bad;name": pa.table({"id": [1]})},
            )

    def test_storage_error_wrapped_as_query_error(self, bridge, storage):
        storage.scan_dataset.side_effect = StorageError(
            error_code=ErrorCode.STORAGE_READ_FAILED,
            message="Dataset not found",
        )
        with pytest.raises(QueryError, match="Failed to read dataset") as exc_info:
            bridge.query("my_ds", "SELECT * FROM my_ds")
        assert exc_info.value.error_code == ErrorCode.QUERY_NO_RESULTS


# ---------------------------------------------------------------------------
# MetadataSearchBridge — successful query (no session_manager)
# ---------------------------------------------------------------------------

class TestMetadataSearchBridgeQuery:
    @pytest.fixture()
    def bridge(self, storage):
        return MetadataSearchBridge(storage)

    def test_query_returns_result(self, bridge, storage, result_table):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.arrow.return_value = result_table
        # create_duckdb_session is a context manager — __enter__ returns the conn
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch(
            "arrow_lake.query.metadata.create_duckdb_session",
            return_value=mock_conn,
        ), patch.object(bridge, "_register_dataset"):
            result = bridge.query("my_ds", "SELECT * FROM my_ds")

        assert isinstance(result, MetadataQueryResult)
        assert result.row_count == 2
        assert result.column_count == 2
        assert result.sql == "SELECT * FROM my_ds"
        assert result.table.equals(result_table)

    def test_query_with_record_batch_reader(self, bridge, storage):
        """When .arrow() returns a RecordBatchReader, read_all() is called."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        reader = MagicMock()
        result_table = pa.table({"x": [1]})
        reader.read_all.return_value = result_table
        mock_conn.execute.return_value.arrow.return_value = reader

        with patch(
            "arrow_lake.query.metadata.create_duckdb_session",
            return_value=mock_conn,
        ), patch.object(bridge, "_register_dataset"):
            result = bridge.query("my_ds", "SELECT x FROM my_ds")

        reader.read_all.assert_called_once()
        assert result.table.equals(result_table)

    def test_query_registers_extra_tables(self, bridge, storage, result_table):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.arrow.return_value = result_table
        extra = pa.table({"tag": ["a", "b"]})

        with patch(
            "arrow_lake.query.metadata.create_duckdb_session",
            return_value=mock_conn,
        ), patch.object(bridge, "_register_dataset"):
            bridge.query(
                "my_ds",
                "SELECT * FROM my_ds JOIN tags ON my_ds.id = tags.id",
                tables={"tags": extra},
            )

        # extra table should be registered via conn.register()
        mock_conn.register.assert_any_call("tags", extra)

    def test_query_scans_dataset(self, bridge, storage):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.arrow.return_value = pa.table({"x": [1]})

        with patch(
            "arrow_lake.query.metadata.create_duckdb_session",
            return_value=mock_conn,
        ), patch.object(bridge, "_register_dataset"):
            bridge.query("my_ds", "SELECT x FROM my_ds")

        storage.scan_dataset.assert_called_once_with("my_ds")


# ---------------------------------------------------------------------------
# MetadataSearchBridge — with session_manager
# ---------------------------------------------------------------------------

class TestMetadataSearchBridgeSessionManager:
    @pytest.fixture()
    def bridge(self, storage):
        return MetadataSearchBridge(storage, session_manager=MagicMock())

    def test_query_uses_session_manager(self, bridge, storage, result_table):
        managed = MagicMock()
        managed.conn.execute.return_value.arrow.return_value = result_table
        bridge._session_manager.acquire.return_value = managed

        with patch.object(bridge, "_register_dataset"):
            bridge.query("my_ds", "SELECT * FROM my_ds")

        bridge._session_manager.acquire.assert_called_once()
        managed.release.assert_called_once()
        managed.conn.execute.assert_called_once_with("SELECT * FROM my_ds")

    def test_session_manager_released_on_query_failure(self, bridge, storage):
        managed = MagicMock()
        managed.conn.execute.side_effect = RuntimeError("boom")
        bridge._session_manager.acquire.return_value = managed

        with pytest.raises(RuntimeError, match="boom"):
            bridge.query("my_ds", "SELECT * FROM my_ds")

        managed.release.assert_called_once()


# ---------------------------------------------------------------------------
# _register_dataset tests
# ---------------------------------------------------------------------------

class TestRegisterDataset:
    def test_native_scan_success(self, storage):
        bridge = MetadataSearchBridge(storage)
        conn = MagicMock()
        storage.dataset_uri.return_value = "/data/my_ds.lance"
        mock_adapter = MagicMock()

        with patch(
            "arrow_lake.query.metadata.create_lance_scan_adapter",
            return_value=mock_adapter,
        ):
            bridge._register_dataset(conn, "my_ds", "source")

        mock_adapter.create_view.assert_called_once_with(conn, "/data/my_ds.lance", "my_ds")
        # conn.register should NOT be called when native scan succeeds
        conn.register.assert_not_called()

    def test_fallback_to_pyarrow_on_duckdb_error(self, storage):
        bridge = MetadataSearchBridge(storage)
        conn = MagicMock()
        storage.dataset_uri.return_value = "/data/my_ds.lance"

        import duckdb

        mock_adapter = MagicMock()
        mock_adapter.create_view.side_effect = duckdb.Error("no lance extension")

        with patch(
            "arrow_lake.query.metadata.create_lance_scan_adapter",
            return_value=mock_adapter,
        ):
            bridge._register_dataset(conn, "my_ds", "source")

        conn.register.assert_called_once_with("my_ds", "source")

    def test_fallback_to_pyarrow_on_os_error(self, storage):
        bridge = MetadataSearchBridge(storage)
        conn = MagicMock()
        storage.dataset_uri.return_value = "/data/my_ds.lance"
        mock_adapter = MagicMock()
        mock_adapter.create_view.side_effect = OSError("file not found")

        with patch(
            "arrow_lake.query.metadata.create_lance_scan_adapter",
            return_value=mock_adapter,
        ):
            bridge._register_dataset(conn, "my_ds", "source")

        conn.register.assert_called_once_with("my_ds", "source")

    def test_fallback_when_storage_has_no_dataset_uri(self, storage):
        """When storage lacks dataset_uri, should skip native scan and register directly."""
        bridge = MetadataSearchBridge(storage)
        conn = MagicMock()
        # Remove dataset_uri from the mock
        del storage.dataset_uri
        source = MagicMock()

        bridge._register_dataset(conn, "my_ds", source)

        conn.register.assert_called_once_with("my_ds", source)

    def test_duckdb_error_caught_specifically(self, storage):
        """duckdb.Error specifically triggers fallback."""
        bridge = MetadataSearchBridge(storage)
        conn = MagicMock()
        storage.dataset_uri.return_value = "/data/my_ds.lance"

        import duckdb

        mock_adapter = MagicMock()
        mock_adapter.create_view.side_effect = duckdb.Error("extension error")

        with patch(
            "arrow_lake.query.metadata.create_lance_scan_adapter",
            return_value=mock_adapter,
        ):
            bridge._register_dataset(conn, "my_ds", "source")

        conn.register.assert_called_once_with("my_ds", "source")
