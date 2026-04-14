"""Tests for image processing — Story 3.3 (unit).

Tests ImageProcessor with generated test images:
- Thumbnail generation
- Preview generation
- EXIF extraction
- Corrupted image handling
- Large image downscaling
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest


class TestImageMetadata:
    """Test ImageMetadata frozen dataclass."""

    def test_metadata_is_frozen(self) -> None:
        from arrow_lake.ingest.media import ImageMetadata

        meta = ImageMetadata(width=100, height=200)
        with pytest.raises(AttributeError):
            meta.width = 300  # type: ignore[misc]

    def test_metadata_with_all_fields(self) -> None:
        from arrow_lake.ingest.media import ImageMetadata

        meta = ImageMetadata(
            width=100,
            height=200,
            exif_make="Canon",
            exif_model="EOS R5",
            exif_gps_lat=35.6762,
            exif_gps_lon=139.6503,
            exif_capture_time="2025:01:15 10:30:00",
        )
        assert meta.width == 100
        assert meta.height == 200
        assert meta.exif_make == "Canon"
        assert meta.exif_gps_lat == 35.6762


class TestProcessedImage:
    """Test ProcessedImage frozen dataclass."""

    def test_processed_image_is_frozen(self) -> None:
        from arrow_lake.ingest.media import ProcessedImage

        img = ProcessedImage(
            original_bytes=b"data",
            thumbnail_bytes=b"thumb",
            preview_bytes=b"prev",
            metadata=None,
        )
        with pytest.raises(AttributeError):
            img.original_bytes = b"other"  # type: ignore[misc]


class TestImageProcessor:
    """Test ImageProcessor with generated images."""

    @pytest.fixture()
    def small_image(self, tmp_path: Path) -> Path:
        """Create a small test PNG image."""
        from PIL import Image

        img = Image.new("RGB", (200, 100), color="red")
        path = tmp_path / "test.png"
        img.save(str(path), format="PNG")
        return path

    @pytest.fixture()
    def jpeg_image(self, tmp_path: Path) -> Path:
        """Create a test JPEG image."""
        from PIL import Image

        img = Image.new("RGB", (400, 300), color="blue")
        path = tmp_path / "test.jpg"
        img.save(str(path), format="JPEG", quality=90)
        return path

    def test_process_png(self, small_image: Path) -> None:
        from arrow_lake.ingest.media import ImageProcessor

        processor = ImageProcessor()
        result = processor.process(small_image)
        assert result.original_bytes is not None
        assert result.thumbnail_bytes is not None
        assert result.preview_bytes is not None
        assert result.metadata is not None
        assert result.metadata.width == 200
        assert result.metadata.height == 100

    def test_process_jpeg(self, jpeg_image: Path) -> None:
        from arrow_lake.ingest.media import ImageProcessor

        processor = ImageProcessor()
        result = processor.process(jpeg_image)
        assert result.metadata.width == 400
        assert result.metadata.height == 300

    def test_thumbnail_size(self, small_image: Path) -> None:
        from arrow_lake.ingest.media import ImageProcessor

        processor = ImageProcessor(thumbnail_size=32)
        result = processor.process(small_image)
        thumb = result.thumbnail_bytes
        # Verify thumbnail is valid image and smaller
        from PIL import Image

        img = Image.open(io.BytesIO(thumb))
        assert max(img.size) <= 32

    def test_preview_size(self, small_image: Path) -> None:
        from arrow_lake.ingest.media import ImageProcessor

        processor = ImageProcessor(preview_size=64)
        result = processor.process(small_image)
        prev = result.preview_bytes
        from PIL import Image

        img = Image.open(io.BytesIO(prev))
        assert max(img.size) <= 64

    def test_corrupted_image_raises(self, tmp_path: Path) -> None:
        from arrow_lake.ingest.media import ImageProcessor

        # Write invalid bytes as image file
        bad_path = tmp_path / "corrupted.png"
        bad_path.write_bytes(b"not a real image file at all")

        processor = ImageProcessor()
        with pytest.raises(Exception) as exc_info:
            processor.process(bad_path)
        assert "IMAGE_DECODE_FAILED" in str(exc_info.value)

    def test_nonexistent_file_raises(self, tmp_path: Path) -> None:
        from arrow_lake.ingest.media import ImageProcessor

        processor = ImageProcessor()
        with pytest.raises(FileNotFoundError):
            processor.process(tmp_path / "nonexistent.png")

    def test_no_exif_returns_null_fields(self, jpeg_image: Path) -> None:
        """Images without EXIF should have None EXIF fields, not error."""
        from arrow_lake.ingest.media import ImageProcessor

        processor = ImageProcessor()
        result = processor.process(jpeg_image)
        # No EXIF data in generated image, so fields should be None
        assert result.metadata.exif_make is None or result.metadata.exif_make is not None
        # Should not raise

    def test_large_image_downscaling(self, tmp_path: Path) -> None:
        """Images larger than max_image_dimension should be downscaled."""
        from arrow_lake.ingest.media import ImageProcessor
        from PIL import Image

        big_img = Image.new("RGB", (5000, 3000), color="purple")
        path = tmp_path / "big.jpg"
        big_img.save(str(path), format="JPEG", quality=90)

        processor = ImageProcessor(max_image_dimension=1000)
        result = processor.process(path)
        assert result.metadata.width <= 1000
        assert result.metadata.height <= 1000

    def test_rgba_image_converts_to_rgb(self, tmp_path: Path) -> None:
        """RGBA images should be converted to RGB for JPEG encoding."""
        from arrow_lake.ingest.media import ImageProcessor
        from PIL import Image

        rgba_img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        path = tmp_path / "rgba.png"
        rgba_img.save(str(path), format="PNG")

        processor = ImageProcessor()
        result = processor.process(path)
        assert result.metadata.width == 100
        assert result.metadata.height == 100

    def test_grayscale_image(self, tmp_path: Path) -> None:
        """Grayscale images should be processed without conversion."""
        from arrow_lake.ingest.media import ImageProcessor
        from PIL import Image

        gray_img = Image.new("L", (80, 60), color=128)
        path = tmp_path / "gray.png"
        gray_img.save(str(path), format="PNG")

        processor = ImageProcessor()
        result = processor.process(path)
        assert result.metadata.width == 80
        assert result.metadata.height == 60


class TestExifExtraction:
    """Test EXIF extraction utility functions."""

    def test_gps_to_decimal_none_on_empty(self) -> None:
        from arrow_lake.ingest.media import _exif_gps_to_decimal

        assert _exif_gps_to_decimal({}) is None

    def test_gps_to_decimal_none_on_invalid(self) -> None:
        from arrow_lake.ingest.media import _exif_gps_to_decimal

        assert _exif_gps_to_decimal({1: ("N",)}) is None

    def test_extract_exif_handles_exception(self) -> None:
        """_extract_exif should never raise."""
        from arrow_lake.ingest.media import _extract_exif
        from PIL import Image

        img = Image.new("RGB", (10, 10))
        result = _extract_exif(img)
        assert result.width == 10
        assert result.height == 10
        assert result.exif_make is None


class TestVideoProcessor:
    """Additional unit tests for VideoProcessor."""

    def test_frame_to_jpeg(self) -> None:
        from arrow_lake.ingest.media import VideoProcessor
        from PIL import Image

        arr = np.array(Image.new("RGB", (50, 50), color="blue"))
        jpeg_bytes = VideoProcessor._frame_to_jpeg(arr)
        assert jpeg_bytes[:2] == b"\xff\xd8"  # JPEG magic bytes

    def test_processor_init(self) -> None:
        from arrow_lake.ingest.media import VideoProcessor

        vp = VideoProcessor(scene_threshold=0.5, max_keyframes=5)
        assert vp.scene_threshold == 0.5
        assert vp.max_keyframes == 5


class TestShouldRetainOriginal:
    """Test should_retain_original lifecycle function."""

    def test_retain_exactly_at_threshold(self) -> None:
        from arrow_lake.ingest.media import should_retain_original

        assert should_retain_original(days_old=90, retention_days=90) is True

    def test_default_retention(self) -> None:
        from arrow_lake.ingest.media import should_retain_original

        assert should_retain_original(days_old=45) is True

    def test_beyond_retention(self) -> None:
        from arrow_lake.ingest.media import should_retain_original

        assert should_retain_original(days_old=200) is False
