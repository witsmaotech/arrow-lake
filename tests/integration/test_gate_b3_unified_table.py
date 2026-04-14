"""Gate B3: Unified multimodal table end-to-end validation.

Validates text + image + video rows coexist in unified Lance table:
- Create unified table
- Append rows of all modalities
- Verify modality column correctness
- Verify query_by_modality filters correctly
- Verify column projection (zero blob I/O for metadata-only queries)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from arrow_lake.ingest.schema import UnifiedTableManager
from arrow_lake.ingest.storage import LanceStorageManager
from PIL import Image


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path / "lance_data"))


@pytest.fixture()
def manager(storage: LanceStorageManager) -> UnifiedTableManager:
    return UnifiedTableManager(storage)


@pytest.fixture()
def image_dir(tmp_path: Path) -> Path:
    """Generate a few test images for ingestion."""
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    for i in range(5):
        img = Image.new("RGB", (200, 100), color=(i * 50, i * 50, i * 50))
        img.save(str(img_dir / f"img_{i}.png"), format="PNG")
    return img_dir


class TestGateB3UnifiedTable:
    """Validate unified multimodal table end-to-end."""

    def test_all_modalities_coexist(
        self,
        manager: UnifiedTableManager,
        image_dir: Path,
    ) -> None:
        """Text, image, and video rows all in one table."""
        from arrow_lake.ingest.media import ImageProcessor

        manager.create("multimodal")

        # Text rows
        manager.append_text_rows(
            "multimodal",
            [
                {"text_content": "Document one", "source": "web"},
                {"text_content": "Document two", "source": "api"},
            ],
        )

        # Image rows (real processing)
        processor = ImageProcessor()
        for img_path in sorted(image_dir.glob("*.png")):
            result = processor.process(img_path)
            manager.append_image_rows(
                "multimodal",
                [
                    {
                        "image_data": result.original_bytes,
                        "image_thumbnail": result.thumbnail_bytes,
                        "image_width": result.metadata.width,
                        "image_height": result.metadata.height,
                    },
                ],
            )

        # Video rows (simulated)
        manager.append_video_rows(
            "multimodal",
            [
                {
                    "video_data": b"fake_video_1",
                    "keyframe_count": 3,
                    "video_duration_ms": 10000,
                },
            ],
        )

        # Verify total row count
        table = manager._storage.read_dataset("multimodal")
        assert table.num_rows == 8  # 2 text + 5 image + 1 video

    def test_modality_column_correctness(
        self,
        manager: UnifiedTableManager,
        image_dir: Path,
    ) -> None:
        """Modality column has correct values per row type."""

        manager.create("mod_test")

        manager.append_text_rows(
            "mod_test",
            [
                {"text_content": "text row", "source": "test"},
            ],
        )
        manager.append_image_rows(
            "mod_test",
            [
                {
                    "image_data": b"img",
                    "image_thumbnail": b"th",
                    "image_width": 100,
                    "image_height": 100,
                },
            ],
        )
        manager.append_video_rows(
            "mod_test",
            [
                {
                    "video_data": b"vid",
                    "keyframe_count": 1,
                    "video_duration_ms": 5000,
                },
            ],
        )

        table = manager._storage.read_dataset("mod_test")
        modalities = table.column("modality").to_pylist()
        assert "text" in modalities
        assert "image" in modalities
        assert "video" in modalities

    def test_query_by_modality_filters(
        self,
        manager: UnifiedTableManager,
    ) -> None:
        """query_by_modality returns only matching rows."""
        manager.create("filter_test")

        manager.append_text_rows(
            "filter_test",
            [
                {"text_content": "t1", "source": "s"},
                {"text_content": "t2", "source": "s"},
            ],
        )
        manager.append_image_rows(
            "filter_test",
            [
                {
                    "image_data": b"a",
                    "image_thumbnail": b"t",
                    "image_width": 10,
                    "image_height": 10,
                },
            ],
        )
        manager.append_video_rows(
            "filter_test",
            [
                {"video_data": b"v", "keyframe_count": 1, "video_duration_ms": 1000},
            ],
        )

        text_result = manager.query_by_modality("filter_test", "text")
        assert text_result.num_rows == 2

        image_result = manager.query_by_modality("filter_test", "image")
        assert image_result.num_rows == 1

        video_result = manager.query_by_modality("filter_test", "video")
        assert video_result.num_rows == 1

    def test_column_projection_zero_blob_io(
        self,
        manager: UnifiedTableManager,
    ) -> None:
        """Reading only metadata columns doesn't load blob data."""
        manager.create("projection_test")

        manager.append_image_rows(
            "projection_test",
            [
                {
                    "image_data": b"x" * 1000000,  # 1MB blob
                    "image_thumbnail": b"th",
                    "image_width": 100,
                    "image_height": 100,
                },
            ],
        )

        # Read only metadata columns
        table = manager._storage.read_dataset(
            "projection_test",
            columns=["id", "modality", "image_width", "image_height"],
        )
        assert table.num_rows == 1
        assert "image_data" not in table.column_names
        assert "image_thumbnail" not in table.column_names
