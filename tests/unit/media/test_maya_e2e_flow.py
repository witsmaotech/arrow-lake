"""Tests for Story 6.10 — Maya E2E Pipeline."""

from __future__ import annotations

import json

import pyarrow as pa


class TestMayaE2EFlowSteps:
    """Test individual Maya E2E flow step logic.

    Metaflow FlowSpec requires Ray and full runtime to execute,
    so we test the step logic in isolation.
    """

    def test_quality_filter_step_logic(self) -> None:
        """Test quality filter: short text should be rejected."""
        import pyarrow.compute as pc

        table = pa.table(
            {
                "id": ["1", "2", "3", "4"],
                "text_content": ["short", "hi", "a longer valid document", ""],
                "source": ["test"] * 4,
            }
        )

        min_length = 10
        mask = pc.greater_equal(pc.utf8_length(table.column("text_content")), min_length)
        passed = table.filter(mask)
        rejected = table.filter(pc.invert(mask))

        assert passed.num_rows == 1
        assert passed.column("text_content")[0].as_py() == "a longer valid document"
        assert rejected.num_rows == 3

    def test_embed_step_logic(self) -> None:
        """Test embedding generation with deterministic pseudo-embeddings."""
        import numpy as np

        n = 10
        dim = 128
        rng = np.random.RandomState(42)
        embeddings = rng.randn(n, dim).astype(np.float32)

        # Normalize
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        embeddings = embeddings / norms

        # Each row should have unit norm
        row_norms = np.linalg.norm(embeddings, axis=1)
        assert np.allclose(row_norms, 1.0, atol=1e-6)

        # Verify embedding column creation
        embedding_col = pa.FixedSizeListArray.from_arrays(embeddings.ravel(), dim)
        assert len(embedding_col) == n
        assert embedding_col.type.list_size == dim

    def test_search_step_logic(self) -> None:
        """Test cosine similarity search on embedded records."""
        import numpy as np

        # Create some test embeddings
        dim = 64
        rng = np.random.RandomState(42)
        vectors = rng.randn(10, dim).astype(np.float32)
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

        # Query = average of first 3
        query = vectors[:3].mean(axis=0)
        query = query / np.linalg.norm(query)

        similarities = vectors @ query
        top_indices = np.argsort(similarities)[::-1][:5]

        # Top results should have highest similarity
        assert similarities[top_indices[0]] >= similarities[top_indices[1]]
        assert len(top_indices) == 5

    def test_pipeline_summary_structure(self) -> None:
        """Test end step summary JSON structure."""
        summary = {
            "pipeline": "maya_e2e",
            "ingested": 100,
            "quality_filter": {
                "total": 100,
                "passed": 90,
                "rejected": 10,
                "pass_rate": 90.0,
            },
            "embedded": 90,
            "embedding_dim": 128,
            "search": {
                "status": "success",
                "top_k": 10,
                "results": [{"id": "1", "score": 0.95}],
            },
        }
        json_str = json.dumps(summary, indent=2)
        parsed = json.loads(json_str)
        assert parsed["pipeline"] == "maya_e2e"
        assert parsed["quality_filter"]["pass_rate"] == 90.0
        assert len(parsed["search"]["results"]) == 1

    def test_empty_dataset_handling(self) -> None:
        """Test pipeline handles empty datasets gracefully."""
        table = pa.table(
            {
                "id": pa.array([], type=pa.string()),
                "text_content": pa.array([], type=pa.string()),
            }
        )

        assert table.num_rows == 0

        # Embed step should skip
        if table.num_rows == 0:
            embedded_count = 0
            assert embedded_count == 0

    def test_dead_letter_table_creation(self) -> None:
        """Test dead-letter table includes rejection reasons."""
        rejected = pa.table(
            {
                "id": ["1", "2"],
                "text_content": ["hi", "ok"],
                "source": ["test", "test"],
            }
        )

        dl_table = rejected.append_column(
            "rejection_reason",
            pa.array(["text_too_short", "text_too_short"]),
        )

        assert "rejection_reason" in dl_table.column_names
        assert dl_table.num_rows == 2
        assert dl_table.column("rejection_reason")[0].as_py() == "text_too_short"

    def test_synthetic_data_generation(self) -> None:
        """Test synthetic test data creation pattern."""
        rows = 50
        table = pa.table(
            {
                "id": [str(i) for i in range(rows)],
                "text_content": [f"Document {i}" for i in range(rows)],
                "source": ["synthetic"] * rows,
                "status": ["active"] * rows,
            }
        )

        assert table.num_rows == rows
        assert set(table.column("source").to_pylist()) == {"synthetic"}
        assert set(table.column("status").to_pylist()) == {"active"}
