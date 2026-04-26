"""E2E tests for full data pipeline via Lake SDK."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from arrow_lake import Lake


@pytest.fixture()
def lake(tmp_path: Path) -> Lake:
    """Create a Lake instance with fresh temp storage."""
    return Lake(base_uri=str(tmp_path / "lake_data"))


@pytest.fixture()
def sample_table() -> pa.Table:
    """Create a sample dataset with text, vector, and metadata columns."""
    n, dim = 500, 128
    rng = np.random.RandomState(42)
    vectors = rng.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    vectors = vectors / norms

    return pa.table(
        {
            "id": [f"doc_{i:04d}" for i in range(n)],
            "text_content": [
                f"Sample document {i} about machine learning and data processing" for i in range(n)
            ],
            "category": [f"cat_{i % 5}" for i in range(n)],
            "score": rng.rand(n).tolist(),
            "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
        }
    )


@pytest.fixture()
def populated_lake(lake: Lake, sample_table: pa.Table, tmp_path: Path) -> Lake:
    """Create a lake with ingested data, vector index, and FTS index."""
    lake._get_storage().create_dataset("documents", sample_table)
    lake.create_vector_index("documents", vector_column="text_embedding", num_sub_vectors=8)
    lake.create_fts_index("documents", fts_column="text_content")
    return lake


class TestIngestToSearchPipeline:
    """E2E: Ingest → Create Index → Vector Search → FTS → Hybrid."""

    def test_ingest_and_list(self, lake: Lake, sample_table: pa.Table) -> None:
        """Ingest data and verify it appears in dataset list."""
        storage = lake._get_storage()
        assert not storage.dataset_exists("documents")

        storage.create_dataset("documents", sample_table)

        assert storage.dataset_exists("documents")
        datasets = storage.list_datasets()
        assert "documents" in datasets

    def test_full_vector_search_flow(self, populated_lake: Lake, sample_table: pa.Table) -> None:
        """Vector search returns results from ingested data."""
        query_vec = sample_table.column("text_embedding")[0].as_py()
        result = populated_lake.search(
            "documents",
            query_vec,
            top_k=5,
            vector_column="text_embedding",
        )

        assert result.table.num_rows <= 5
        assert result.table.num_rows > 0
        assert "_distance" in result.table.column_names

    def test_full_fts_flow(self, populated_lake: Lake) -> None:
        """Full-text search returns results matching query."""
        result = populated_lake.text_search(
            "documents",
            "machine learning",
            top_k=5,
        )

        assert result.table.num_rows > 0
        assert "_score" in result.table.column_names

    def test_full_hybrid_search_flow(self, populated_lake: Lake, sample_table: pa.Table) -> None:
        """Hybrid search returns fused results from vector + FTS."""
        query_vec = sample_table.column("text_embedding")[0].as_py()
        result = populated_lake.hybrid_search(
            "documents",
            query_vec,
            "data processing",
            top_k=5,
            vector_column="text_embedding",
            fts_column="text_content",
        )

        assert result.table.num_rows > 0
        assert "_hybrid_score" in result.table.column_names or "_rrf_score" in result.table.column_names


class TestDeduplicationPipeline:
    """E2E: Ingest → Deduplicate (exact + flag mode)."""

    def test_deduplicate_exact_remove(self, lake: Lake) -> None:
        """Exact dedup removes duplicate binary content."""
        img_a = b"image-content-alpha"
        img_b = b"image-content-beta"
        table = pa.table(
            {
                "id": ["r1", "r2", "r3", "r4", "r5"],
                "image_data": [img_a, img_a, img_b, img_a, img_b],
            }
        )
        lake._get_storage().create_dataset("dedup_ds", table)

        result = lake.deduplicate("dedup_ds", strategy="exact", action="remove")

        assert result.total_rows == 5
        assert result.unique_rows == 2
        assert result.duplicates_found == 3

    def test_deduplicate_exact_flag(self, lake: Lake) -> None:
        """Exact dedup flag mode marks duplicates without removing."""
        img_a = b"image-x"
        img_b = b"image-y"
        table = pa.table(
            {
                "id": ["r1", "r2", "r3"],
                "image_data": [img_a, img_a, img_b],
            }
        )
        lake._get_storage().create_dataset("dedup_flag_ds", table)

        result = lake.deduplicate("dedup_flag_ds", strategy="exact", action="flag")

        assert result.table.num_rows == 3
        assert "is_duplicate" in result.table.column_names
        flags = result.table.column("is_duplicate").to_pylist()
        assert flags[0] is False  # first occurrence
        assert flags[1] is True  # duplicate
        assert flags[2] is False  # unique


class TestExportPipeline:
    """E2E: Ingest → Export to Parquet and CSV."""

    def test_export_to_parquet(self, lake: Lake, sample_table: pa.Table, tmp_path: Path) -> None:
        """Export dataset to Parquet and verify file."""
        lake._get_storage().create_dataset("export_ds", sample_table)

        output = str(tmp_path / "output.parquet")
        result = lake.export("export_ds", output, overwrite=True)

        assert result.format == "parquet"
        assert result.row_count == 500
        assert Path(output).exists()
        assert Path(output).stat().st_size > 0

    def test_export_to_csv_excludes_binary(self, lake: Lake, tmp_path: Path) -> None:
        """Export to CSV excludes binary columns."""
        table = pa.table(
            {
                "id": ["a", "b", "c"],
                "text_content": ["doc a", "doc b", "doc c"],
                "image_data": [b"x", b"y", b"z"],
            }
        )
        lake._get_storage().create_dataset("csv_ds", table)

        output = str(tmp_path / "output.csv")
        result = lake.export("csv_ds", output, overwrite=True)

        assert result.format == "csv"
        assert result.row_count == 3
        # Binary columns excluded from CSV — column_count reflects non-binary only
        assert result.column_count == 2  # id, text_content

    def test_export_column_selection(
        self, lake: Lake, sample_table: pa.Table, tmp_path: Path
    ) -> None:
        """Export with column selection returns only specified columns."""
        lake._get_storage().create_dataset("col_ds", sample_table)

        output = str(tmp_path / "subset.parquet")
        result = lake.export("col_ds", output, columns=["id", "category"], overwrite=True)

        assert result.column_count == 2


class TestAuditPipeline:
    """E2E: Record audit entry → Query → Verify HMAC."""

    def test_audit_record_and_query(self, lake: Lake) -> None:
        """Record an audit entry and query it back."""
        lake._get_storage().create_dataset("audit_ds", pa.table({"id": ["1"], "val": [10]}))

        audit_id = lake.audit_record(
            event_type="data_ingest",
            dataset_name="audit_ds",
            actor="e2e_test",
        )

        assert isinstance(audit_id, str)
        assert len(audit_id) > 0

    def test_audit_verify_integrity(self, lake: Lake) -> None:
        """Verify HMAC integrity of a recorded audit entry."""
        lake._get_storage().create_dataset("audit_v", pa.table({"id": ["1"], "val": [10]}))

        audit_id = lake.audit_record(
            event_type="create",
            dataset_name="audit_v",
        )

        is_valid = lake.audit_verify(audit_id)
        assert is_valid is True

    def test_audit_verify_nonexistent(self, lake: Lake) -> None:
        """Verify returns False for nonexistent audit ID."""
        is_valid = lake.audit_verify("nonexistent-id")
        assert is_valid is False


class TestLineagePipeline:
    """E2E: Record lineage → Query history → SQL query."""

    def test_record_and_retrieve_lineage(self, lake: Lake) -> None:
        """Record a lineage event and retrieve dataset history."""
        lake._get_storage().create_dataset("lineage_ds", pa.table({"id": ["1"], "val": [10]}))

        lake.lineage_record_event(
            "lineage_ds",
            operation="create",
            source_datasets=["raw_data"],
            transform_type="etl",
        )

        history = lake.lineage_history("lineage_ds")
        assert len(history) == 1
        assert history[0].operation == "create"
        assert history[0].dataset_name == "lineage_ds"

    def test_lineage_sql_query(self, lake: Lake) -> None:
        """SQL query over lineage events returns expected results."""
        lake._get_storage().create_dataset("lineage_sql", pa.table({"id": ["1"], "val": [10]}))

        lake.lineage_record_event("lineage_sql", operation="create")
        lake.lineage_record_event("lineage_sql", operation="append")

        result = lake.lineage_query("SELECT * FROM _lineage_events WHERE operation = 'append'")
        assert result.num_rows == 1


class TestOLAPQueryPipeline:
    """E2E: Ingest → OLAP SQL query."""

    def test_olap_group_by(self, lake: Lake, sample_table: pa.Table) -> None:
        """OLAP GROUP BY query returns aggregated results."""
        lake._get_storage().create_dataset("olap_ds", sample_table)

        result = lake.olap_query(
            "olap_ds",
            "SELECT category, COUNT(*) as cnt FROM olap_ds GROUP BY category ORDER BY category",
        )

        assert result.table.num_rows == 5  # 5 categories
        assert "category" in result.table.column_names
        assert "cnt" in result.table.column_names
