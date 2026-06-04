"""Coverage for ingest/media.py — image/video processing utilities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from arrow_lake.ingest.media import (
    ExtractedKeyframe,
    ImageMetadata,
    ImageProcessor,
    ProcessedImage,
    VideoIngestResult,
    VideoProcessor,
    _exif_gps_to_decimal,
    _extract_exif,
    _resize_and_encode,
    should_retain_original,
)


# ── Dataclasses ──


class TestDataclasses:
    def test_image_metadata(self) -> None:
        m = ImageMetadata(width=100, height=100)
        assert m.width == 100

    def test_processed_image(self) -> None:
        p = ProcessedImage(
            original_bytes=b"\xff\xd8",
            thumbnail_bytes=b"",
            preview_bytes=b"",
            metadata=ImageMetadata(width=10, height=10),
        )
        assert p.original_bytes == b"\xff\xd8"

    def test_extracted_keyframe(self) -> None:
        k = ExtractedKeyframe(jpeg_bytes=b"\xff\xd8", timestamp_ms=1000)
        assert k.timestamp_ms == 1000

    def test_video_ingest_result(self) -> None:
        r = VideoIngestResult(keyframes=[], duration_ms=10000, scene_detection_used=False)
        assert r.duration_ms == 10000


# ── _exif_gps_to_decimal ──


class TestExifGps:
    def test_no_gps(self) -> None:
        assert _exif_gps_to_decimal({}) is None

    def test_incomplete_gps(self) -> None:
        assert _exif_gps_to_decimal({1: "N"}) is None


# ── _extract_exif ──


class TestExtractExif:
    def test_no_exif(self) -> None:
        img = Image.new("RGB", (100, 100))
        meta = _extract_exif(img)
        assert isinstance(meta, ImageMetadata)
        assert meta.width == 100

    def test_with_exif(self) -> None:
        img = Image.new("RGB", (100, 100))
        exif = img.getexif()
        exif[271] = "TestCamera"  # Make
        meta = _extract_exif(img)
        assert meta.exif_make == "TestCamera"


# ── _resize_and_encode ──


class TestResizeAndEncode:
    def test_resize(self) -> None:
        img = Image.new("RGB", (200, 200))
        data = _resize_and_encode(img, 50)
        assert data[:2] == b"\xff\xd8"  # JPEG magic

    def test_small_image(self) -> None:
        img = Image.new("RGB", (10, 10))
        data = _resize_and_encode(img, 100)
        assert len(data) > 0


# ── ImageProcessor ──


class TestImageProcessor:
    def test_process(self, tmp_path: Path) -> None:
        img_path = tmp_path / "test.jpg"
        img = Image.new("RGB", (100, 100))
        img.save(img_path, "JPEG")

        proc = ImageProcessor(thumbnail_size=50)
        result = proc.process(str(img_path))
        assert isinstance(result, ProcessedImage)
        assert result.metadata.width > 0

    def test_process_nonexistent(self) -> None:
        proc = ImageProcessor()
        with pytest.raises(FileNotFoundError):
            proc.process("/nonexistent/image.jpg")


# ── VideoProcessor ──


class TestVideoProcessor:
    def test_frame_to_jpeg(self) -> None:
        arr = np.zeros((100, 100, 3), dtype=np.uint8)
        data = VideoProcessor._frame_to_jpeg(arr)
        assert data[:2] == b"\xff\xd8"

    def test_extract_nonexistent(self) -> None:
        vp = VideoProcessor()
        with pytest.raises(FileNotFoundError):
            vp.extract_keyframes("/nonexistent/video.mp4")


# ── should_retain_original ──


class TestShouldRetainOriginal:
    def test_new_image_retained(self) -> None:
        assert should_retain_original(10) is True

    def test_old_image_not_retained(self) -> None:
        assert should_retain_original(200, retention_days=90) is False

    def test_exactly_at_threshold(self) -> None:
        assert should_retain_original(90, retention_days=90) is True
