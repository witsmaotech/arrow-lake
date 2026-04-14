"""Tests for lazy image decode — Story 3.8 (integration).

Tests LazyImageHandle and LazyDecodeManager:
- Lazy decoding (pixels only decoded on .pixels() call)
- Batch retrieval
- Column projection for fidelity selection
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from arrow_lake.ingest.schema import UnifiedTableManager
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query.lazy_decode import LazyDecodeManager, LazyImageHandle
from PIL import Image


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path / "lance_data"))


@pytest.fixture()
def manager(storage: LanceStorageManager) -> UnifiedTableManager:
    return UnifiedTableManager(storage)


@pytest.fixture()
def decode_manager(storage: LanceStorageManager) -> LazyDecodeManager:
    return LazyDecodeManager(storage)


class TestLazyImageHandle:
    """Test LazyImageHandle lazy decoding."""

    def test_handle_is_lazy(self) -> None:
        """Handle should not decode on creation."""
        handle = LazyImageHandle(raw_bytes=b"fake", fidelity="full")
        assert handle._pixels_cache is None

    def test_pixels_decode(self) -> None:
        """pixels() should decode and cache the image."""
        img = Image.new("RGB", (50, 50), color="red")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        jpeg_bytes = buf.getvalue()

        handle = LazyImageHandle(raw_bytes=jpeg_bytes, fidelity="full")
        pixels = handle.pixels()
        assert isinstance(pixels, Image.Image)
        assert pixels.size == (50, 50)

    def test_pixels_caches_result(self) -> None:
        """pixels() should cache the decoded image."""
        img = Image.new("RGB", (10, 10), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        jpeg_bytes = buf.getvalue()

        handle = LazyImageHandle(raw_bytes=jpeg_bytes, fidelity="full")
        first = handle.pixels()
        second = handle.pixels()
        assert first is second  # Same cached object


class TestLazyDecodeManager:
    """Test LazyDecodeManager retrieval and fidelity."""

    def test_get_image_thumbnail(
        self, manager: UnifiedTableManager, decode_manager: LazyDecodeManager
    ) -> None:
        manager.create("lazy_test")
        manager.append_image_rows(
            "lazy_test",
            [
                {
                    "image_data": b"x" * 1000,
                    "image_thumbnail": self._make_jpeg(32, 32),
                    "image_preview": self._make_jpeg(128, 128),
                    "image_width": 100,
                    "image_height": 100,
                },
            ],
        )

        handle = decode_manager.get_image("lazy_test", row_id=0, fidelity="thumbnail")
        assert isinstance(handle, LazyImageHandle)
        pixels = handle.pixels()
        assert max(pixels.size) <= 32

    def test_get_image_preview(
        self, manager: UnifiedTableManager, decode_manager: LazyDecodeManager
    ) -> None:
        manager.create("lazy_preview")
        manager.append_image_rows(
            "lazy_preview",
            [
                {
                    "image_data": b"x" * 1000,
                    "image_thumbnail": self._make_jpeg(32, 32),
                    "image_preview": self._make_jpeg(128, 128),
                    "image_width": 100,
                    "image_height": 100,
                },
            ],
        )

        handle = decode_manager.get_image("lazy_preview", row_id=0, fidelity="preview")
        pixels = handle.pixels()
        assert max(pixels.size) <= 128

    def test_get_images_batch(
        self, manager: UnifiedTableManager, decode_manager: LazyDecodeManager
    ) -> None:
        manager.create("lazy_batch")
        for _ in range(3):
            manager.append_image_rows(
                "lazy_batch",
                [
                    {
                        "image_data": b"x" * 1000,
                        "image_thumbnail": self._make_jpeg(16, 16),
                        "image_preview": self._make_jpeg(64, 64),
                        "image_width": 50,
                        "image_height": 50,
                    },
                ],
            )

        handles = decode_manager.get_images_batch("lazy_batch", [0, 1, 2], fidelity="thumbnail")
        assert len(handles) == 3
        for h in handles:
            assert isinstance(h, LazyImageHandle)

    @staticmethod
    def _make_jpeg(w: int, h: int) -> bytes:
        img = Image.new("RGB", (w, h), color="green")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
