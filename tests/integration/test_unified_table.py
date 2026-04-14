"""Tests for unified multimodal table — Story 3.5 (integration).

Tests UnifiedTableManager:
- Create table with UNIFIED_SCHEMA
- Append text rows
- Append image rows
- Append video rows
- Query by modality (column projection)
- Mixed modality in single table
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.ingest.schema import UnifiedTableManager
from arrow_lake.ingest.storage import LanceStorageManager


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path / "lance_data"))


@pytest.fixture()
def manager(storage: LanceStorageManager) -> UnifiedTableManager:
    return UnifiedTableManager(storage)


class TestUnifiedSchema:
    """Test UNIFIED_SCHEMA definition."""

    def test_schema_has_required_columns(self) -> None:
        from arrow_lake.ingest.schema import UNIFIED_SCHEMA

        names = set(UNIFIED_SCHEMA.names)
        assert "id" in names
        assert "modality" in names
        assert "source" in names
        assert "created_at" in names
        assert "text_content" in names
        assert "image_data" in names
        assert "image_thumbnail" in names
        assert "video_data" in names

    def test_id_is_string(self) -> None:
        from arrow_lake.ingest.schema import UNIFIED_SCHEMA

        assert UNIFIED_SCHEMA.field("id").type == pa.string()

    def test_modality_is_string(self) -> None:
        from arrow_lake.ingest.schema import UNIFIED_SCHEMA

        assert UNIFIED_SCHEMA.field("modality").type == pa.string()

    def test_text_embedding_type(self) -> None:
        from arrow_lake.ingest.schema import UNIFIED_SCHEMA

        field = UNIFIED_SCHEMA.field("text_embedding")
        assert pa.types.is_fixed_size_list(field.type)
        assert pa.types.is_float32(field.type.value_type)


class TestUnifiedTableManager:
    """Test UnifiedTableManager CRUD operations."""

    def test_create_table(self, manager: UnifiedTableManager) -> None:
        manager.create("test_table")
        assert manager._storage.dataset_exists("test_table")

    def test_create_idempotent_raises(self, manager: UnifiedTableManager) -> None:
        manager.create("test_table")
        from arrow_lake.exceptions import StorageError

        with pytest.raises(StorageError):
            manager.create("test_table")

    def test_append_text_rows(self, manager: UnifiedTableManager) -> None:
        manager.create("docs")
        manager.append_text_rows(
            "docs",
            [
                {"text_content": "Hello world", "source": "web"},
                {"text_content": "Another doc", "source": "api"},
            ],
        )
        table = manager._storage.read_dataset("docs")
        assert table.num_rows == 2

    def test_append_image_rows(self, manager: UnifiedTableManager) -> None:
        manager.create("images")
        manager.append_image_rows(
            "images",
            [
                {
                    "image_data": b"\xff\xd8\xff\xe0",
                    "image_thumbnail": b"thumb1",
                    "image_width": 640,
                    "image_height": 480,
                },
            ],
        )
        table = manager._storage.read_dataset("docs" if False else "images")
        assert table.num_rows == 1

    def test_append_video_rows(self, manager: UnifiedTableManager) -> None:
        manager.create("videos")
        manager.append_video_rows(
            "videos",
            [
                {
                    "video_data": b"fake_video",
                    "keyframe_count": 3,
                    "video_duration_ms": 10000,
                },
            ],
        )
        table = manager._storage.read_dataset("videos")
        assert table.num_rows == 1

    def test_query_by_modality_text(self, manager: UnifiedTableManager) -> None:
        manager.create("mixed")
        manager.append_text_rows(
            "mixed",
            [
                {"text_content": "Text doc 1", "source": "web"},
            ],
        )
        manager.append_image_rows(
            "mixed",
            [
                {
                    "image_data": b"img1",
                    "image_thumbnail": b"t1",
                    "image_width": 100,
                    "image_height": 100,
                },
            ],
        )

        result = manager.query_by_modality("mixed", "text")
        assert result.num_rows == 1

    def test_query_by_modality_image(self, manager: UnifiedTableManager) -> None:
        manager.create("mixed2")
        manager.append_text_rows(
            "mixed2",
            [
                {"text_content": "Text doc", "source": "web"},
            ],
        )
        manager.append_image_rows(
            "mixed2",
            [
                {
                    "image_data": b"img1",
                    "image_thumbnail": b"t1",
                    "image_width": 100,
                    "image_height": 100,
                },
            ],
        )

        result = manager.query_by_modality("mixed2", "image")
        assert result.num_rows == 1

    def test_query_by_modality_video(self, manager: UnifiedTableManager) -> None:
        manager.create("vids")
        manager.append_video_rows(
            "vids",
            [
                {
                    "video_data": b"vid1",
                    "keyframe_count": 2,
                    "video_duration_ms": 5000,
                },
            ],
        )

        result = manager.query_by_modality("vids", "video")
        assert result.num_rows == 1

    def test_text_row_has_correct_modality(self, manager: UnifiedTableManager) -> None:
        manager.create("mod_test")
        manager.append_text_rows(
            "mod_test",
            [
                {"text_content": "test", "source": "file"},
            ],
        )
        table = manager._storage.read_dataset("mod_test")
        modalities = table.column("modality").to_pylist()
        assert modalities == ["text"]
