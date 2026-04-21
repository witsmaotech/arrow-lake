"""Tests for LanceScanAdapter — abstract layer for Lance → DuckDB bridging.

M0a Day 2 — TDD RED phase.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# ABC contract
# ---------------------------------------------------------------------------


class TestLanceScanAdapterABC:
    """Test that LanceScanAdapter is an abstract base class."""

    def test_cannot_instantiate_directly(self) -> None:
        """ABC should not allow direct instantiation."""
        from arrow_lake.query.lance_adapter import LanceScanAdapter

        with pytest.raises(TypeError):
            LanceScanAdapter()

    def test_subclass_must_implement_scan(self) -> None:
        """Subclass without scan() should not be instantiatable."""
        from arrow_lake.query.lance_adapter import LanceScanAdapter

        class IncompleteAdapter(LanceScanAdapter):
            def create_view(self, conn, uri, view_name):  # type: ignore[override]
                pass

            def is_available(self):  # type: ignore[override]
                return True

        with pytest.raises(TypeError):
            IncompleteAdapter()

    def test_subclass_must_implement_create_view(self) -> None:
        """Subclass without create_view() should not be instantiatable."""
        from arrow_lake.query.lance_adapter import LanceScanAdapter

        class IncompleteAdapter(LanceScanAdapter):
            def scan(self, conn, uri):  # type: ignore[override]
                pass

            def is_available(self):  # type: ignore[override]
                return True

        with pytest.raises(TypeError):
            IncompleteAdapter()

    def test_subclass_must_implement_is_available(self) -> None:
        """Subclass without is_available() should not be instantiatable."""
        from arrow_lake.query.lance_adapter import LanceScanAdapter

        class IncompleteAdapter(LanceScanAdapter):
            def scan(self, conn, uri):  # type: ignore[override]
                pass

            def create_view(self, conn, uri, view_name):  # type: ignore[override]
                pass

        with pytest.raises(TypeError):
            IncompleteAdapter()


# ---------------------------------------------------------------------------
# PyArrowFallbackAdapter
# ---------------------------------------------------------------------------


class TestPyArrowFallbackAdapter:
    """Test the PyArrow fallback adapter that uses scanner().to_reader()."""

    def test_scan_returns_record_batch_reader(self) -> None:
        """scan() should return data registered as a DuckDB table."""
        import duckdb

        from arrow_lake.query.lance_adapter import PyArrowFallbackAdapter

        table = pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        mock_ds = MagicMock()
        mock_scanner = MagicMock()
        mock_reader = table.to_reader()
        mock_scanner.to_reader.return_value = mock_reader
        mock_ds.scanner.return_value = mock_scanner

        adapter = PyArrowFallbackAdapter(dataset=mock_ds)
        with adapter.scan(duckdb.connect(), "./data") as conn:
            result = conn.execute("SELECT count(*) FROM t").fetchone()[0]
            assert result == 3

    def test_scan_uses_to_reader_not_to_table(self) -> None:
        """scan() must use scanner().to_reader() for streaming, not to_table()."""
        from arrow_lake.query.lance_adapter import PyArrowFallbackAdapter

        mock_ds = MagicMock()
        mock_scanner = MagicMock()
        mock_scanner.to_reader.return_value = pa.table({"a": [1]}).to_reader()
        mock_ds.scanner.return_value = mock_scanner

        adapter = PyArrowFallbackAdapter(dataset=mock_ds)
        with adapter.scan(MagicMock(), "./data"):
            mock_scanner.to_reader.assert_called_once()
            # to_table must NOT be called (prevents OOM on large datasets)
            mock_ds.to_table.assert_not_called()

    def test_is_available_always_true(self) -> None:
        """PyArrowFallbackAdapter is always available."""
        from arrow_lake.query.lance_adapter import PyArrowFallbackAdapter

        adapter = PyArrowFallbackAdapter(dataset=MagicMock())
        assert adapter.is_available() is True

    def test_create_view_registers_reader(self) -> None:
        """create_view() should register a view in DuckDB from Lance dataset."""
        from arrow_lake.query.lance_adapter import PyArrowFallbackAdapter

        table = pa.table({"id": [10, 20], "val": ["a", "b"]})
        mock_ds = MagicMock()
        mock_scanner = MagicMock()
        mock_scanner.to_reader.return_value = table.to_reader()
        mock_ds.scanner.return_value = mock_scanner

        adapter = PyArrowFallbackAdapter(dataset=mock_ds)
        conn = MagicMock()
        adapter.create_view(conn, "./data", "my_view")
        conn.register.assert_called_once()
        call_args = conn.register.call_args
        assert call_args[0][0] == "my_view"

    def test_scan_with_columns_filter(self) -> None:
        """scan() should pass columns filter to scanner."""
        from arrow_lake.query.lance_adapter import PyArrowFallbackAdapter

        mock_ds = MagicMock()
        mock_scanner = MagicMock()
        mock_scanner.to_reader.return_value = pa.table({"a": [1]}).to_reader()
        mock_ds.scanner.return_value = mock_scanner

        adapter = PyArrowFallbackAdapter(dataset=mock_ds)
        with adapter.scan(MagicMock(), "./data", columns=["a", "b"]):
            mock_ds.scanner.assert_called_once()
            call_kwargs = mock_ds.scanner.call_args
            # columns should be passed to scanner
            assert call_kwargs[1].get("columns") == ["a", "b"] or (
                len(call_kwargs[0]) > 0 and call_kwargs[0][0] == ["a", "b"]
            )


# ---------------------------------------------------------------------------
# NativeLanceScanAdapter
# ---------------------------------------------------------------------------


class TestNativeLanceScanAdapter:
    """Test the DuckDB native lance extension adapter."""

    def test_scan_uses_lance_scan_function(self) -> None:
        """scan() should use __lance_scan SQL function."""
        from arrow_lake.query.lance_adapter import NativeLanceScanAdapter

        adapter = NativeLanceScanAdapter()
        mock_conn = MagicMock()
        with patch.object(adapter, "_load_lance_scan", return_value=True):
            with adapter.scan(mock_conn, "./data"):
                pass

    def test_is_available_probes_extension(self) -> None:
        """is_available() should return True when lance extension is loaded."""
        from arrow_lake.query.lance_adapter import NativeLanceScanAdapter

        adapter = NativeLanceScanAdapter()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (1,)

        with patch.object(adapter, "_load_lance_scan", return_value=True):
            result = adapter.is_available(mock_conn)
            assert result is True

    def test_is_available_returns_false_on_failure(self) -> None:
        """is_available() should return False when __lance_scan is not available."""
        from arrow_lake.query.lance_adapter import NativeLanceScanAdapter

        adapter = NativeLanceScanAdapter()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("not available")

        with patch.object(adapter, "_load_lance_scan", return_value=False):
            result = adapter.is_available(mock_conn)
            assert result is False

    def test_create_view_creates_duckdb_view(self) -> None:
        """create_view() should CREATE VIEW using __lance_scan."""
        from arrow_lake.query.lance_adapter import NativeLanceScanAdapter

        adapter = NativeLanceScanAdapter()
        mock_conn = MagicMock()
        adapter.create_view(mock_conn, "./data", "my_view")
        mock_conn.execute.assert_called_once()
        sql = mock_conn.execute.call_args.args[0]
        assert "VIEW" in sql
        assert "my_view" in sql
        assert "__lance_scan" in sql


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestCreateLanceScanAdapter:
    """Test the create_lance_scan_adapter factory function."""

    def test_auto_returns_native_when_available(self) -> None:
        """auto mode should return NativeLanceScanAdapter when available."""
        from arrow_lake.query.lance_adapter import NativeLanceScanAdapter
        from arrow_lake.query.lance_adapter import create_lance_scan_adapter

        mock_conn = MagicMock()
        with patch(
            "arrow_lake.query.lance_adapter.NativeLanceScanAdapter._load_lance_scan",
            return_value=True,
        ):
            adapter = create_lance_scan_adapter(mock_conn, mode="auto")
            assert isinstance(adapter, NativeLanceScanAdapter)

    def test_native_mode_returns_native(self) -> None:
        """native mode should return NativeLanceScanAdapter."""
        from arrow_lake.query.lance_adapter import NativeLanceScanAdapter
        from arrow_lake.query.lance_adapter import create_lance_scan_adapter

        mock_conn = MagicMock()
        with patch(
            "arrow_lake.query.lance_adapter.NativeLanceScanAdapter._load_lance_scan",
            return_value=True,
        ):
            adapter = create_lance_scan_adapter(mock_conn, mode="native")
            assert isinstance(adapter, NativeLanceScanAdapter)

    def test_pyarrow_fallback_mode(self) -> None:
        """pyarrow_fallback mode should return PyArrowFallbackAdapter."""
        from arrow_lake.query.lance_adapter import PyArrowFallbackAdapter
        from arrow_lake.query.lance_adapter import create_lance_scan_adapter

        mock_ds = MagicMock()
        adapter = create_lance_scan_adapter(
            MagicMock(), mode="pyarrow_fallback", dataset=mock_ds
        )
        assert isinstance(adapter, PyArrowFallbackAdapter)

    def test_auto_falls_back_to_pyarrow(self) -> None:
        """auto mode should fall back to PyArrowFallbackAdapter when native fails."""
        from arrow_lake.query.lance_adapter import PyArrowFallbackAdapter
        from arrow_lake.query.lance_adapter import create_lance_scan_adapter

        mock_conn = MagicMock()
        mock_ds = MagicMock()
        with patch(
            "arrow_lake.query.lance_adapter.NativeLanceScanAdapter._load_lance_scan",
            return_value=False,
        ):
            adapter = create_lance_scan_adapter(
                mock_conn, mode="auto", dataset=mock_ds
            )
            assert isinstance(adapter, PyArrowFallbackAdapter)
