"""Tests for local file ingestion — Story 3.1 (integration).

Tests Ingestor with real local files:
- Ingest CSV files into Lance dataset
- Ingest Parquet files into Lance dataset
- Ingest JSONL files into Lance dataset
- Mixed file list ingests correctly
- Ingested data matches source data
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from arrow_lake.ingest.ingestor import Ingestor
from arrow_lake.ingest.storage import LanceStorageManager


@pytest.fixture
def seed_dir(tmp_path: Path) -> Path:
    """Create seed data files for ingestion tests."""
    data_dir = tmp_path / "seed"
    data_dir.mkdir()

    # CSV
    csv_path = data_dir / "users.csv"
    csv_path.write_text("id,name,age\n1,Alice,30\n2,Bob,25\n3,Carol,35\n")

    # Parquet
    pq.write_table(
        pa.table({"id": [4, 5], "name": ["Dave", "Eve"], "age": [40, 28]}),
        str(data_dir / "more_users.parquet"),
    )

    # JSONL
    jsonl_path = data_dir / "extra.jsonl"
    with open(jsonl_path, "w") as f:
        for i, name in enumerate(["Frank", "Grace"], start=6):
            json.dump({"id": i, "name": name, "age": 30 + i}, f)
            f.write("\n")

    return data_dir


class TestLocalIngestion:
    """Test ingestion from local files."""

    def test_ingest_csv(self, tmp_path: Path, seed_dir: Path) -> None:
        """CSV files are ingested correctly."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        ingestor = Ingestor(manager)

        report = ingestor.ingest("users", [str(seed_dir / "users.csv")])

        assert report.total_rows == 3
        assert report.total_files == 1

        data = manager.read_dataset("users")
        assert data.num_rows == 3
        assert sorted(data.column("name").to_pylist()) == ["Alice", "Bob", "Carol"]

    def test_ingest_parquet(self, tmp_path: Path, seed_dir: Path) -> None:
        """Parquet files are ingested correctly."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        ingestor = Ingestor(manager)

        report = ingestor.ingest("pq_users", [str(seed_dir / "more_users.parquet")])

        assert report.total_rows == 2
        data = manager.read_dataset("pq_users")
        assert data.num_rows == 2

    def test_ingest_jsonl(self, tmp_path: Path, seed_dir: Path) -> None:
        """JSONL files are ingested correctly."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        ingestor = Ingestor(manager)

        report = ingestor.ingest("json_users", [str(seed_dir / "extra.jsonl")])

        assert report.total_rows == 2
        data = manager.read_dataset("json_users")
        assert data.num_rows == 2

    def test_ingest_multiple_files(self, tmp_path: Path, seed_dir: Path) -> None:
        """Multiple files are merged into one dataset."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        ingestor = Ingestor(manager)

        files = [
            str(seed_dir / "users.csv"),
            str(seed_dir / "more_users.parquet"),
            str(seed_dir / "extra.jsonl"),
        ]
        report = ingestor.ingest("all_users", files)

        assert report.total_rows == 7
        assert report.total_files == 3

        data = manager.read_dataset("all_users")
        assert data.num_rows == 7
