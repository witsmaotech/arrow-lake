"""Tests for multi-fidelity blob storage — Story 3.6 (unit).

Tests column projection for zero blob I/O:
- Reading metadata-only columns loads no binary data
- Reading thumbnail-only loads only thumbnails
- should_retain_original lifecycle policy
"""

from __future__ import annotations

from pathlib import Path

import pytest
from arrow_lake.ingest.schema import UnifiedTableManager
from arrow_lake.ingest.storage import LanceStorageManager


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path / "lance_data"))


@pytest.fixture()
def manager(storage: LanceStorageManager) -> UnifiedTableManager:
    return UnifiedTableManager(storage)


class TestColumnProjection:
    """Test that column projection avoids loading binary data."""

    def _setup_image_table(self, manager: UnifiedTableManager, table_name: str) -> None:
        manager.create(table_name)
        manager.append_image_rows(
            table_name,
            [
                {
                    "image_data": b"x" * 10000,
                    "image_thumbnail": b"small",
                    "image_preview": b"medium",
                    "image_width": 100,
                    "image_height": 100,
                },
                {
                    "image_data": b"y" * 10000,
                    "image_thumbnail": b"small2",
                    "image_preview": b"medium2",
                    "image_width": 200,
                    "image_height": 200,
                },
            ],
        )

    def test_metadata_columns_no_blobs(self, manager: UnifiedTableManager) -> None:
        """Reading only id + modality should load no binary data."""
        self._setup_image_table(manager, "test_meta")
        table = manager._storage.read_dataset("test_meta", columns=["id", "modality"])
        assert table.num_rows == 2
        assert table.num_columns == 2
        assert "image_data" not in table.column_names

    def test_thumbnail_only_projection(self, manager: UnifiedTableManager) -> None:
        """Reading thumbnail column should not load original image_data."""
        self._setup_image_table(manager, "test_thumb")
        table = manager._storage.read_dataset("test_thumb", columns=["id", "image_thumbnail"])
        assert table.num_rows == 2
        assert "image_data" not in table.column_names
        assert "image_thumbnail" in table.column_names

    def test_full_image_loads_all_columns(self, manager: UnifiedTableManager) -> None:
        """Reading without column projection loads all columns."""
        self._setup_image_table(manager, "test_full")
        table = manager._storage.read_dataset("test_full")
        assert "image_data" in table.column_names
        assert "image_thumbnail" in table.column_names
        assert "image_preview" in table.column_names


class TestShouldRetainOriginal:
    """Test image retention lifecycle policy."""

    def test_retain_within_threshold(self) -> None:
        from arrow_lake.ingest.media import should_retain_original

        # 30 days old, 90 day retention → should retain
        assert should_retain_original(days_old=30, retention_days=90) is True

    def test_not_retain_beyond_threshold(self) -> None:
        from arrow_lake.ingest.media import should_retain_original

        # 100 days old, 90 day retention → should not retain
        assert should_retain_original(days_old=100, retention_days=90) is False

    def test_retain_at_boundary(self) -> None:
        from arrow_lake.ingest.media import should_retain_original

        assert should_retain_original(days_old=90, retention_days=90) is True
        assert should_retain_original(days_old=91, retention_days=90) is False

    def test_retain_zero_retention(self) -> None:
        from arrow_lake.ingest.media import should_retain_original

        assert should_retain_original(days_old=0, retention_days=0) is True
        assert should_retain_original(days_old=1, retention_days=0) is False
