"""v0.2 migration validation — ensures new infrastructure reads v0.2 data correctly.

M0a Day 5 — validates that Lance datasets created by v0.2 patterns
are readable via:
1. __lance_scan() (DuckDB native)
2. CREATE VIEW + information_schema column discovery
3. PyArrowFallbackAdapter.scan()
4. NULL cross-format preservation
"""

from __future__ import annotations

import pyarrow as pa
import pytest


@pytest.fixture()
def v02_lance_dataset(tmp_path: object) -> str:
    """Create a Lance dataset using v0.2-compatible lance.write_dataset."""
    import lance

    table = pa.table(
        {
            "id": [f"doc_{i:03d}" for i in range(10)],
            "text_content": [f"Sample document {i} about ML" for i in range(10)],
            "modality": ["text"] * 10,
            "source": ["test"] * 10,
            "score": [float(i) for i in range(10)],
        }
    )
    ds_path = str(tmp_path / "v02_dataset")
    lance.write_dataset(table, ds_path)
    return ds_path


class TestV02MigrationLanceScan:
    """Verify v0.2 datasets are readable via __lance_scan()."""

    def test_lance_scan_reads_data(self, v02_lance_dataset: str) -> None:
        """__lance_scan should read v0.2-created datasets."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            result = conn.execute(
                f"SELECT count(*) FROM __lance_scan('{v02_lance_dataset}', "
                f"explain_verbose := false)"
            ).fetchone()
            assert result[0] == 10

    def test_lance_scan_preserves_columns(self, v02_lance_dataset: str) -> None:
        """Column names should be preserved through __lance_scan."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            conn.execute(
                f"CREATE VIEW v AS SELECT * FROM __lance_scan("
                f"'{v02_lance_dataset}', explain_verbose := false)"
            )
            cols = conn.execute("DESCRIBE v").fetchall()
            col_names = {row[0] for row in cols}
            assert "id" in col_names
            assert "text_content" in col_names
            assert "modality" in col_names

    def test_lance_scan_sql_filter(self, v02_lance_dataset: str) -> None:
        """SQL WHERE should work on __lance_scan results."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            result = conn.execute(
                f"SELECT count(*) FROM __lance_scan("
                f"'{v02_lance_dataset}', explain_verbose := false) "
                f"WHERE modality = 'text' AND score >= 5"
            ).fetchone()
            assert result[0] == 5

    def test_lance_scan_aggregation(self, v02_lance_dataset: str) -> None:
        """SQL aggregation should work on __lance_scan results."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            result = conn.execute(
                f"SELECT avg(score) FROM __lance_scan("
                f"'{v02_lance_dataset}', explain_verbose := false)"
            ).fetchone()
            assert result[0] == 4.5


class TestV02MigrationColumnDiscovery:
    """Verify column discovery via information_schema."""

    def test_information_schema_columns(self, v02_lance_dataset: str) -> None:
        """information_schema.columns should list all dataset columns."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            conn.execute(
                f"CREATE VIEW v AS SELECT * FROM __lance_scan("
                f"'{v02_lance_dataset}', explain_verbose := false)"
            )
            cols = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'v'"
            ).fetchall()
            col_names = {row[0] for row in cols}
            assert "id" in col_names
            assert "text_content" in col_names
            assert "modality" in col_names
            assert "source" in col_names
            assert "score" in col_names


class TestV02MigrationPyArrowFallback:
    """Verify v0.2 datasets are readable via PyArrowFallbackAdapter."""

    def test_pyarrow_adapter_reads_data(self, v02_lance_dataset: str) -> None:
        """PyArrowFallbackAdapter should read v0.2 data correctly."""
        import duckdb
        import lance

        from arrow_lake.query.lance_adapter import PyArrowFallbackAdapter

        dataset = lance.dataset(v02_lance_dataset)
        adapter = PyArrowFallbackAdapter(dataset=dataset)
        with adapter.scan(duckdb.connect(), v02_lance_dataset) as conn:
            result = conn.execute("SELECT count(*) FROM t").fetchone()[0]
            assert result == 10


class TestV02MigrationNullPreservation:
    """Verify NULL values are preserved across format conversions."""

    @pytest.fixture()
    def null_dataset(self, tmp_path: object) -> str:
        """Create a Lance dataset with NULL values."""
        import lance

        table = pa.table({
            "id": [1, 2, 3, 4],
            "text": ["hello", None, "world", None],
            "score": [3.14, None, 2.71, None],
        })
        ds_path = str(tmp_path / "null_dataset")
        lance.write_dataset(table, ds_path)
        return ds_path

    def test_null_in_string_column(self, null_dataset: str) -> None:
        """NULL values in string columns should survive the round-trip."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            result = conn.execute(
                f"SELECT text FROM __lance_scan('{null_dataset}', "
                f"explain_verbose := false) WHERE id IN (2, 4) ORDER BY id"
            ).fetchall()
            assert result[0][0] is None
            assert result[1][0] is None

    def test_null_in_numeric_column(self, null_dataset: str) -> None:
        """NULL values in numeric columns should survive the round-trip."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            result = conn.execute(
                f"SELECT score FROM __lance_scan('{null_dataset}', "
                f"explain_verbose := false) WHERE id = 2"
            ).fetchone()
            assert result[0] is None

    def test_null_count_via_sql(self, null_dataset: str) -> None:
        """SQL COUNT with NULL filtering should work correctly."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            result = conn.execute(
                f"SELECT count(*) FROM __lance_scan('{null_dataset}', "
                f"explain_verbose := false) WHERE score IS NULL"
            ).fetchone()
            assert result[0] == 2
