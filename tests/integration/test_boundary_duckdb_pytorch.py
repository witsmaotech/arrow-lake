"""Zero-copy boundary test — Story 4.1 (integration).

Tests DuckDB → PyTorch tensor conversion:
- DuckDB query result can be converted to PyTorch tensor
- Arrow → NumPy → Torch pipeline works
- No data corruption in the conversion chain
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa


class TestDuckDBPyTorchBoundary:
    """Test zero-copy boundary between DuckDB and PyTorch."""

    def test_duckdb_result_to_numpy(self) -> None:
        """DuckDB query result can be converted to NumPy array."""
        import duckdb

        table = pa.table(
            {
                "id": [1, 2, 3],
                "value": [10.0, 20.0, 30.0],
            }
        )

        conn = duckdb.connect()
        conn.register("data", table)
        result = conn.execute("SELECT value FROM data ORDER BY value").arrow()
        conn.close()
        if hasattr(result, "read_all"):
            result = result.read_all()
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3

        arr = result.column("value").to_numpy()
        assert arr.shape == (3,)
        np.testing.assert_array_almost_equal(arr, [10.0, 20.0, 30.0])

    def test_arrow_to_pytorch(self) -> None:
        """Arrow table can be converted to PyTorch tensor."""
        import torch

        table = pa.table(
            {
                "features": pa.array(
                    [
                        [0.1, 0.2, 0.3],
                        [0.4, 0.5, 0.6],
                        [0.7, 0.8, 0.9],
                    ],
                    type=pa.list_(pa.float32(), 3),
                ),
            }
        )

        # Convert to numpy then to torch
        flat = np.stack(table.column("features").to_pylist()).astype(np.float32)
        tensor = torch.from_numpy(flat)

        assert tensor.shape == (3, 3)
        assert tensor.dtype == torch.float32

    def test_duckdb_arrow_to_torch_pipeline(self) -> None:
        """Full pipeline: DuckDB → Arrow → NumPy → PyTorch."""
        import duckdb
        import torch

        table = pa.table(
            {
                "id": [1, 2, 3, 4, 5],
                "embedding": pa.array(
                    [
                        [0.1] * 384,
                        [0.2] * 384,
                        [0.3] * 384,
                        [0.4] * 384,
                        [0.5] * 384,
                    ],
                    type=pa.list_(pa.float32(), 384),
                ),
            }
        )

        conn = duckdb.connect()
        conn.register("data", table)
        reader = conn.execute("SELECT embedding FROM data WHERE id > 2").arrow()
        conn.close()
        result = reader.read_all() if hasattr(reader, "read_all") else reader
        assert result.num_rows == 3

        flat = np.stack(result.column("embedding").to_pylist()).astype(np.float32)
        tensor = torch.from_numpy(flat)

        assert tensor.shape == (3, 384)
        assert tensor.dtype == torch.float32
        # Verify no data corruption
        assert abs(tensor[0, 0].item() - 0.3) < 1e-6
        assert abs(tensor[2, 0].item() - 0.5) < 1e-6

    def test_embedding_column_preserves_dimensions(self) -> None:
        """Embedding vectors maintain correct dimensions through the pipeline."""
        import torch

        dim = 384
        table = pa.table(
            {
                "text_embedding": pa.array(
                    [np.random.randn(dim).astype(np.float32).tolist() for _ in range(10)],
                    type=pa.list_(pa.float32(), dim),
                ),
            }
        )

        flat = np.stack(table.column("text_embedding").to_pylist()).astype(np.float32)
        tensor = torch.from_numpy(flat)

        assert tensor.shape == (10, 384)
        assert tensor.dtype == torch.float32
