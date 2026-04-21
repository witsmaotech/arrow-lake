"""Tests for OlapSearchBridge.materialize() and DuckLake integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.config import OlapConfig


class TestOlapMaterialize:
    """Tests for DuckLake materialization via OlapSearchBridge."""

    def _make_bridge(
        self,
        *,
        ducklake_enabled: bool = True,
        ducklake_ttl_days: int = 7,
        ducklake_max_join_rows: int = 1_000_000,
    ) -> Any:
        from arrow_lake.query.olap import OlapSearchBridge

        config = OlapConfig(
            ducklake_enabled=ducklake_enabled,
            ducklake_ttl_days=ducklake_ttl_days,
            ducklake_max_join_rows=ducklake_max_join_rows,
        )
        storage = MagicMock()
        return OlapSearchBridge(storage, config=config)

    def test_materialize_disabled_raises(self) -> None:
        """When ducklake_enabled=False, materialize should raise QueryError."""
        from arrow_lake.exceptions import ErrorCode, QueryError

        bridge = self._make_bridge(ducklake_enabled=False)
        storage_mock = MagicMock()
        storage_mock.read_dataset.return_value = MagicMock()

        with pytest.raises(QueryError) as exc_info:
            bridge.materialize(
                "test_ds",
                "SELECT count(*) FROM test_ds",
            )
        assert exc_info.value.error_code == ErrorCode.OLAP_QUERY_FAILED
        assert "not enabled" in exc_info.value.message

    def test_materialize_passes_config_to_workspace(self) -> None:
        """Materialize should pass TTL and max_join_rows to DuckLakeWorkspace."""
        bridge = self._make_bridge(
            ducklake_ttl_days=3,
            ducklake_max_join_rows=500_000,
        )

        sample_table = MagicMock()
        sample_table.num_rows = 0
        bridge._storage.read_dataset.return_value = sample_table

        with patch(
            "arrow_lake.query.olap.create_duckdb_session",
        ) as mock_session:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_session.return_value = mock_conn

            with patch(
                "arrow_lake.query.ducklake_workspace.DuckLakeWorkspace"
            ) as mock_workspace_cls:
                mock_workspace = MagicMock()
                mock_workspace.materialize.return_value = 42
                mock_workspace_cls.return_value = mock_workspace

                result = bridge.materialize(
                    "test_ds",
                    "SELECT * FROM test_ds",
                    view_name="my_view",
                    ttl_days=5,
                    max_join_rows=100_000,
                )

                assert result == 42
                # Check DuckLakeWorkspace was created with overridden params
                mock_workspace_cls.assert_called_once_with(
                    ttl_days=5,
                    max_join_rows=100_000,
                )
                # Check materialize was called on workspace
                mock_workspace.materialize.assert_called_once_with(
                    mock_conn,
                    "SELECT * FROM test_ds",
                    "my_view",
                )

    def test_materialize_auto_generates_view_name(self) -> None:
        """When view_name is None, auto-generate from dataset name."""
        bridge = self._make_bridge()
        sample_table = MagicMock()
        bridge._storage.read_dataset.return_value = sample_table

        with patch("arrow_lake.query.olap.create_duckdb_session") as mock_session:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_session.return_value = mock_conn

            with patch(
                "arrow_lake.query.ducklake_workspace.DuckLakeWorkspace"
            ) as mock_workspace_cls:
                mock_workspace = MagicMock()
                mock_workspace.materialize.return_value = 10
                mock_workspace_cls.return_value = mock_workspace

                bridge.materialize(
                    "my_dataset",
                    "SELECT count(*) FROM my_dataset",
                )

                call_args = mock_workspace.materialize.call_args
                assert call_args[0][2] == "_materialized_my_dataset"

    def test_cleanup_materialized(self) -> None:
        """Cleanup should delegate to DuckLakeWorkspace.cleanup_expired."""
        bridge = self._make_bridge(ducklake_ttl_days=14)

        with patch("arrow_lake.query.olap.create_duckdb_session") as mock_session:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_session.return_value = mock_conn

            with patch(
                "arrow_lake.query.ducklake_workspace.DuckLakeWorkspace"
            ) as mock_workspace_cls:
                mock_workspace = MagicMock()
                mock_workspace.cleanup_expired.return_value = ["old_view"]
                mock_workspace_cls.return_value = mock_workspace

                result = bridge.cleanup_materialized(ttl_days=30)

                assert result == ["old_view"]
                mock_workspace_cls.assert_called_once_with(ttl_days=30)
                mock_workspace.cleanup_expired.assert_called_once_with(mock_conn)
