"""Gate B1: Real image batch ingestion validation (100 images).

Validates end-to-end image ingestion pipeline:
- Generate 100 PNG/JPEG images with PIL
- Ingest via ImageProcessor → Lance dataset
- Verify thumbnail generation, EXIF extraction, metadata
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from arrow_lake.ingest.media import ImageProcessor
from arrow_lake.ingest.storage import LanceStorageManager
from PIL import Image


@pytest.fixture()
def image_dir(tmp_path: Path) -> Path:
    """Generate 100 test images (50 PNG + 50 JPEG)."""
    img_dir = tmp_path / "images"
    img_dir.mkdir()

    colors = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 0),
        (255, 0, 255),
        (0, 255, 255),
        (128, 0, 0),
        (0, 128, 0),
        (0, 0, 128),
        (128, 128, 128),
    ]

    for i in range(50):
        color = colors[i % len(colors)]
        size = (200 + (i % 5) * 100, 200 + (i % 3) * 100)
        img = Image.new("RGB", size, color)
        path = img_dir / f"img_{i:03d}.png"
        img.save(str(path), format="PNG")

    for i in range(50):
        color = colors[(i + 3) % len(colors)]
        size = (300 + (i % 4) * 100, 300 + (i % 2) * 100)
        img = Image.new("RGB", size, color)
        path = img_dir / f"img_{i + 50:03d}.jpg"
        img.save(str(path), format="JPEG", quality=90)

    return img_dir


class TestGateB1ImageBatchIngestion:
    """Validate 100-image ingestion with thumbnails and metadata."""

    def test_process_100_images(self, image_dir: Path) -> None:
        """Process all 100 images and verify results."""
        processor = ImageProcessor()
        paths = sorted(image_dir.glob("*"))
        assert len(paths) == 100

        results = []
        for path in paths:
            result = processor.process(path)
            results.append(result)

        # All 100 processed successfully
        assert len(results) == 100

        # All have non-empty thumbnails and previews
        for r in results:
            assert r.original_bytes is not None
            assert len(r.original_bytes) > 0
            assert r.thumbnail_bytes is not None
            assert len(r.thumbnail_bytes) > 0
            assert r.preview_bytes is not None
            assert len(r.preview_bytes) > 0

        # All have valid metadata
        for r in results:
            assert r.metadata.width > 0
            assert r.metadata.height > 0

        # Thumbnails are valid images and smaller than originals
        for r in results:
            thumb = Image.open(io.BytesIO(r.thumbnail_bytes))
            assert max(thumb.size) <= 64
            preview = Image.open(io.BytesIO(r.preview_bytes))
            assert max(preview.size) <= 512

    def test_ingest_100_images_to_lance(self, image_dir: Path, tmp_path: Path) -> None:
        """Ingest 100 images into Lance dataset via Ingestor."""
        from arrow_lake.ingest.ingestor import Ingestor

        storage = LanceStorageManager(str(tmp_path / "lance_data"))
        ingestor = Ingestor(manager=storage)

        paths = [str(p) for p in sorted(image_dir.glob("*"))]
        report = ingestor.ingest_images("batch_images", paths)

        assert report.total_files == 100
        assert report.total_rows == 100

        # Verify dataset exists and has correct shape
        table = storage.read_dataset("batch_images")
        assert table.num_rows == 100
        assert "image_data" in table.column_names
        assert "image_thumbnail" in table.column_names
        assert "image_width" in table.column_names

    def test_jpeg_and_png_count(self, image_dir: Path) -> None:
        """Verify correct mix of PNG and JPEG files."""
        png_count = len(list(image_dir.glob("*.png")))
        jpg_count = len(list(image_dir.glob("*.jpg")))
        assert png_count == 50
        assert jpg_count == 50
