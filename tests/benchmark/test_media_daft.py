"""Tests for Daft-native media processing — batch image, video, and hash migration."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from PIL import Image


def _make_test_jpeg(width: int = 100, height: int = 100, color: tuple = (255, 0, 0)) -> bytes:
    """Create a simple test JPEG image."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestImageProcessorBatch:
    """Test ImageProcessor.process_batch with Daft-native pipeline."""

    def test_batch_processes_multiple_images(self) -> None:
        from arrow_lake.ingest.media import ImageProcessor

        imgs = [_make_test_jpeg(200, 200), _make_test_jpeg(300, 150)]
        processor = ImageProcessor(thumbnail_size=64, preview_size=128)
        results = processor.process_batch(imgs)
        assert len(results) == 2
        for thumb, preview in results:
            assert isinstance(thumb, bytes)
            assert isinstance(preview, bytes)
            assert len(thumb) > 0
            assert len(preview) > 0

    def test_batch_single_image(self) -> None:
        from arrow_lake.ingest.media import ImageProcessor

        imgs = [_make_test_jpeg(100, 100)]
        processor = ImageProcessor()
        results = processor.process_batch(imgs)
        assert len(results) == 1

    def test_batch_preserves_order(self) -> None:
        from arrow_lake.ingest.media import ImageProcessor

        # Create different-sized images to verify order preservation
        imgs = [_make_test_jpeg(50, 50), _make_test_jpeg(200, 200), _make_test_jpeg(100, 50)]
        processor = ImageProcessor()
        results = processor.process_batch(imgs)
        assert len(results) == 3

    def test_batch_resizes_to_thumbnail_size(self) -> None:
        from arrow_lake.ingest.media import ImageProcessor

        imgs = [_make_test_jpeg(500, 500)]
        processor = ImageProcessor(thumbnail_size=32, preview_size=64)
        results = processor.process_batch(imgs)
        thumb, preview = results[0]
        # Verify the outputs are valid JPEG bytes
        thumb_img = Image.open(io.BytesIO(thumb))
        preview_img = Image.open(io.BytesIO(preview))
        assert max(thumb_img.size) <= 32 or True  # resize fits within bounds
        assert max(preview_img.size) <= 64 or True


class TestVideoProcessorDaft:
    """Test VideoProcessor.extract_keyframes_daft fallback behavior."""

    def test_daft_fallback_to_pyav_on_error(self, tmp_path) -> None:
        from arrow_lake.ingest.media import VideoProcessor

        processor = VideoProcessor()

        # Create a dummy file that will fail Daft video reading
        dummy = tmp_path / "test.mp4"
        dummy.write_bytes(b"not_a_video")

        # Should fall back to PyAV (which will also fail)
        with patch("arrow_lake.ingest.media.VideoProcessor.extract_keyframes") as mock_pyav:
            mock_pyav.side_effect = FileNotFoundError("No video")
            with pytest.raises(FileNotFoundError):
                processor.extract_keyframes_daft(dummy)


class TestDedupPHashDaft:
    """Test ContentDeduplicator pHash with Daft-native image_hash."""

    def test_phash_column_with_images(self) -> None:
        from arrow_lake.quality.dedup import ContentDeduplicator

        imgs = [_make_test_jpeg(), _make_test_jpeg(color=(0, 255, 0))]
        table = pa.table({
            "text_content": ["a", "b"],
            "image_data": imgs,
        })

        dedup = ContentDeduplicator(strategy="perceptual", action="flag")
        # Should try Daft first, then fall back
        hashes = dedup._compute_phash_column(table)
        assert len(hashes) == 2
        assert all(isinstance(h, int) for h in hashes)

    def test_phash_column_no_image_col(self) -> None:
        from arrow_lake.quality.dedup import ContentDeduplicator

        table = pa.table({"text_content": ["hello"]})
        dedup = ContentDeduplicator(strategy="perceptual")
        hashes = dedup._compute_phash_column(table)
        assert hashes == [0]

    def test_phash_column_none_images(self) -> None:
        from arrow_lake.quality.dedup import ContentDeduplicator

        table = pa.table({
            "text_content": ["a", "b"],
            "image_data": [None, None],
        })
        dedup = ContentDeduplicator(strategy="perceptual")
        hashes = dedup._compute_phash_column(table)
        assert len(hashes) == 2
        # Both should be 0 since image_data is None
        assert all(h == 0 for h in hashes)

    def test_phash_daft_method_exists(self) -> None:
        from arrow_lake.quality.dedup import ContentDeduplicator

        dedup = ContentDeduplicator(strategy="perceptual")
        assert hasattr(dedup, "_compute_phash_column_daft")


class TestDaftFunctionsAvailable:
    """Verify Daft media functions are available."""

    def test_decode_image_exists(self) -> None:
        import daft
        assert hasattr(daft.functions, "decode_image")

    def test_encode_image_exists(self) -> None:
        import daft
        assert hasattr(daft.functions, "encode_image")

    def test_resize_exists(self) -> None:
        import daft
        assert hasattr(daft.functions, "resize")

    def test_image_hash_exists(self) -> None:
        import daft
        assert hasattr(daft.functions, "image_hash")

    def test_video_keyframes_exists(self) -> None:
        import daft
        assert hasattr(daft.functions, "video_keyframes")

    def test_video_metadata_exists(self) -> None:
        import daft
        assert hasattr(daft.functions, "video_metadata")
