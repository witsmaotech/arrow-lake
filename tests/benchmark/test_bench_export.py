"""Data export benchmarks — Story 5.9."""



from __future__ import annotations

import pytest

pytestmark = pytest.mark.benchmark

import numpy as np
import pyarrow as pa
import pytest
from pyarrow import FixedSizeListArray

from tests.benchmark.benchmark_report import BenchmarkReport


def _make_dataset_table(
    n: int = 10_000,
    dim: int = 128,
    with_binary: bool = False,
) -> pa.Table:
    """Create a test table with text, vector, and optional binary columns."""
    rng = np.random.RandomState(42)
    vectors = rng.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    vectors = vectors / norms

    cols: dict[str, list | pa.Array] = {
        "id": [f"doc_{i:06d}" for i in range(n)],
        "text_content": [f"Document number {i} about machine learning" for i in range(n)],
        "category": [f"cat_{i % 10}" for i in range(n)],
        "score": rng.rand(n).tolist(),
        "vector": FixedSizeListArray.from_arrays(vectors.ravel(), dim),
    }

    if with_binary:
        cols["image_data"] = [f"binary_blob_{i}".encode() for i in range(n)]

    return pa.table(cols)


@pytest.mark.benchmark
class TestExportBenchmark:
    """Benchmark data export performance."""

    def test_export_parquet_10k(self, tmp_path: object) -> None:
        """Benchmark: export 10K rows to Parquet."""
        from arrow_lake.query.export import ExportBridge

        table = _make_dataset_table(n=10_000)
        bridge = ExportBridge(storage=None)
        output = str(tmp_path / "bench_export.parquet")

        report = BenchmarkReport("export_parquet_10k")
        elapsed = report.measure(
            "Export to Parquet (10K rows, 128d vectors)",
            lambda: bridge.export_table(table, output, overwrite=True),
            rows=table.num_rows,
            repeats=5,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_export_parquet_50k(self, tmp_path: object) -> None:
        """Benchmark: export 50K rows to Parquet."""
        from arrow_lake.query.export import ExportBridge

        table = _make_dataset_table(n=50_000)
        bridge = ExportBridge(storage=None)
        output = str(tmp_path / "bench_export_50k.parquet")

        report = BenchmarkReport("export_parquet_50k")
        elapsed = report.measure(
            "Export to Parquet (50K rows, 128d vectors)",
            lambda: bridge.export_table(table, output, overwrite=True),
            rows=table.num_rows,
            repeats=3,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_export_parquet_compression(self, tmp_path: object) -> None:
        """Benchmark: Parquet export with different compression codecs."""
        from arrow_lake.query.export import ExportBridge

        table = _make_dataset_table(n=10_000)
        bridge = ExportBridge(storage=None)

        report = BenchmarkReport("export_parquet_compression")
        for codec in ("snappy", "gzip", "zstd"):
            output = str(tmp_path / f"bench_{codec}.parquet")
            report.measure(
                f"Export to Parquet ({codec})",
                lambda c=codec, o=output: bridge.export_table(
                    table, o, overwrite=True, compression=c
                ),
                rows=table.num_rows,
                repeats=3,
                warmup=1,
            )
        report.print_summary()
        print(report.to_json())

    def test_export_csv_10k(self, tmp_path: object) -> None:
        """Benchmark: export 10K rows to CSV (binary and vector columns excluded)."""
        from arrow_lake.query.export import ExportBridge

        # CSV doesn't support vector (fixed_size_list) or binary columns
        table = pa.table(
            {
                "id": [f"doc_{i:06d}" for i in range(10_000)],
                "text_content": [f"Document {i}" for i in range(10_000)],
                "category": [f"cat_{i % 10}" for i in range(10_000)],
                "score": np.random.RandomState(42).rand(10_000).tolist(),
            }
        )
        bridge = ExportBridge(storage=None)
        output = str(tmp_path / "bench_export.csv")

        report = BenchmarkReport("export_csv_10k")
        elapsed = report.measure(
            "Export to CSV (10K rows, binary excluded)",
            lambda: bridge.export_table(table, output, overwrite=True),
            rows=table.num_rows,
            repeats=5,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_export_column_selection(self, tmp_path: object) -> None:
        """Benchmark: export with column selection vs full export."""
        from arrow_lake.query.export import ExportBridge

        table = _make_dataset_table(n=10_000)
        bridge = ExportBridge(storage=None)

        report = BenchmarkReport("export_column_selection")
        output_full = str(tmp_path / "bench_full.parquet")
        output_subset = str(tmp_path / "bench_subset.parquet")

        report.measure(
            "Full export (5 columns)",
            lambda: bridge.export_table(table, output_full, overwrite=True),
            rows=table.num_rows,
            repeats=5,
            warmup=1,
        )
        report.measure(
            "Subset export (2 columns: id, text_content)",
            lambda: bridge.export_table(
                table, output_subset, overwrite=True, columns=["id", "text_content"]
            ),
            rows=table.num_rows,
            repeats=5,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())

    def test_export_from_lance_dataset(self, tmp_path: object) -> None:
        """Benchmark: end-to-end export from Lance dataset."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.export import ExportBridge

        storage = LanceStorageManager(base_uri=str(tmp_path / "lance"))
        table = _make_dataset_table(n=10_000)
        storage.create_dataset("bench_export_ds", table)

        bridge = ExportBridge(storage=storage)
        output = str(tmp_path / "bench_from_lance.parquet")

        report = BenchmarkReport("export_from_lance_10k")
        elapsed = report.measure(
            "Export Lance dataset to Parquet (10K rows)",
            lambda: bridge.export("bench_export_ds", output, overwrite=True),
            rows=table.num_rows,
            repeats=5,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0
