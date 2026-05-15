"""Tests for arrow_lake.query.lazy_decode — Story 3.8."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from arrow_lake.query.lazy_decode import LazyDecodeManager, LazyImageHandle
from PIL import Image

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(width: int = 4, height: int = 4) -> bytes:
    """Return a minimal valid PNG byte string."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _mock_pyarrow_table(num_rows: int, column_name: str, data: list) -> MagicMock:
    """Build a mock pyarrow Table that behaves like the real one.

    Args:
        num_rows: Value returned by ``table.num_rows``.
        column_name: Name of the fidelity column to stub.
        data: List of values for the column cells. Length should match *num_rows*.
    """
    table = MagicMock()
    table.num_rows = num_rows

    def _column_side_effect(name: str) -> MagicMock:
        col = MagicMock()
        if name == column_name:
            col.__getitem__ = MagicMock(side_effect=lambda idx: MagicMock(as_py=lambda: data[idx]))
        return col

    table.column = MagicMock(side_effect=_column_side_effect)
    return table


# ---------------------------------------------------------------------------
# LazyImageHandle
# ---------------------------------------------------------------------------


class TestLazyImageHandle:
    """Tests for the LazyImageHandle value-object."""

    def test_fidelity_defaults_to_full(self) -> None:
        handle = LazyImageHandle(raw_bytes=b"")
        assert handle.fidelity == "full"

    def test_fidelity_is_stored(self) -> None:
        handle = LazyImageHandle(raw_bytes=b"", fidelity="thumbnail")
        assert handle.fidelity == "thumbnail"

    def test_size_bytes(self) -> None:
        raw = b"\x89PNG\r\n"
        handle = LazyImageHandle(raw_bytes=raw)
        assert handle.size_bytes == len(raw)

    def test_pixels_decodes_real_image(self) -> None:
        raw = _make_png_bytes(8, 8)
        handle = LazyImageHandle(raw_bytes=raw, fidelity="full")
        img = handle.pixels()
        assert isinstance(img, Image.Image)
        assert img.size == (8, 8)

    def test_pixels_caches_result(self) -> None:
        raw = _make_png_bytes()
        handle = LazyImageHandle(raw_bytes=raw)
        first = handle.pixels()
        second = handle.pixels()
        assert first is second  # same cached object

    def test_pixels_is_not_called_on_init(self) -> None:
        """Verify that construction does NOT eagerly decode."""
        raw = _make_png_bytes()
        handle = LazyImageHandle(raw_bytes=raw)
        assert handle._pixels_cache is None

    def test_pixels_invalid_bytes_raises(self) -> None:
        """Corrupt bytes should raise when .pixels() is called."""
        handle = LazyImageHandle(raw_bytes=b"not-an-image")
        with pytest.raises(Exception):  # PIL.UnidentifiedImageError
            handle.pixels()


# ---------------------------------------------------------------------------
# LazyDecodeManager
# ---------------------------------------------------------------------------


class TestLazyDecodeManagerInit:
    """Tests for LazyDecodeManager construction and class-level constants."""

    def test_default_quality(self) -> None:
        mgr = LazyDecodeManager(storage=MagicMock())
        assert mgr.default_quality == "full"

    def test_custom_default_quality(self) -> None:
        mgr = LazyDecodeManager(storage=MagicMock(), default_quality="thumbnail")
        assert mgr.default_quality == "thumbnail"

    def test_fidelity_columns_mapping(self) -> None:
        expected = {
            "thumbnail": "image_thumbnail",
            "preview": "image_preview",
            "full": "image_data",
        }
        assert expected == LazyDecodeManager._FIDELITY_COLUMNS


class TestLazyDecodeManagerGetImage:
    """Tests for LazyDecodeManager.get_image."""

    def test_returns_lazy_handle(self) -> None:
        """get_image returns a LazyImageHandle without decoding."""
        raw = _make_png_bytes()
        table = _mock_pyarrow_table(3, "image_data", [None, raw, None])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage)
        handle = mgr.get_image("my_ds", row_id=1, fidelity="full")

        assert isinstance(handle, LazyImageHandle)
        assert handle.fidelity == "full"
        assert handle.size_bytes == len(raw)
        # Verify column projection used the correct column name
        storage.read_dataset.assert_called_once_with("my_ds", columns=["id", "image_data"])

    def test_uses_default_fidelity_when_none(self) -> None:
        """When fidelity=None, the manager's default_quality is used."""
        raw = _make_png_bytes()
        table = _mock_pyarrow_table(1, "image_preview", [raw])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage, default_quality="preview")
        handle = mgr.get_image("ds", row_id=0)

        assert handle.fidelity == "preview"
        storage.read_dataset.assert_called_once_with("ds", columns=["id", "image_preview"])

    def test_index_error_on_out_of_range(self) -> None:
        table = _mock_pyarrow_table(2, "image_data", [b"a", b"b"])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage)
        with pytest.raises(IndexError, match="Row 5 out of range"):
            mgr.get_image("ds", row_id=5)

    def test_value_error_on_null_data(self) -> None:
        table = _mock_pyarrow_table(3, "image_data", [b"a", None, b"c"])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage)
        with pytest.raises(ValueError, match="No full data for row 1"):
            mgr.get_image("ds", row_id=1)

    def test_invalid_fidelity_raises_key_error(self) -> None:
        storage = MagicMock()
        mgr = LazyDecodeManager(storage=storage)
        with pytest.raises(KeyError):
            mgr.get_image("ds", row_id=0, fidelity="invalid_quality")


class TestLazyDecodeManagerGetImagesBatch:
    """Tests for LazyDecodeManager.get_images_batch."""

    def test_returns_handles_for_valid_rows(self) -> None:
        """Batch returns one handle per requested row_id."""
        raw0 = _make_png_bytes(4, 4)
        raw1 = _make_png_bytes(8, 8)
        table = _mock_pyarrow_table(3, "image_data", [raw0, raw1, b"c"])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage)
        handles = mgr.get_images_batch("ds", row_ids=[0, 2], fidelity="full")

        assert len(handles) == 2
        assert all(isinstance(h, LazyImageHandle) for h in handles)
        assert handles[0].size_bytes == len(raw0)
        assert handles[1].size_bytes == len(b"c")

    def test_batch_single_read_dataset_call(self) -> None:
        """Batch should read the dataset only once, not per row."""
        table = _mock_pyarrow_table(2, "image_thumbnail", [b"a", b"b"])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage)
        mgr.get_images_batch("ds", row_ids=[0, 1], fidelity="thumbnail")

        # Exactly one call to read_dataset
        assert storage.read_dataset.call_count == 1
        storage.read_dataset.assert_called_once_with(
            "ds", columns=["id", "image_thumbnail"]
        )

    def test_batch_uses_default_fidelity(self) -> None:
        raw = _make_png_bytes()
        table = _mock_pyarrow_table(1, "image_preview", [raw])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage, default_quality="preview")
        handles = mgr.get_images_batch("ds", row_ids=[0])

        assert handles[0].fidelity == "preview"

    def test_batch_index_error_on_out_of_range(self) -> None:
        """If any row_id is out of range, an IndexError is raised."""
        table = _mock_pyarrow_table(1, "image_data", [b"x"])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage)
        with pytest.raises(IndexError, match="Row 9 out of range"):
            mgr.get_images_batch("ds", row_ids=[0, 9])

    def test_batch_value_error_on_null(self) -> None:
        table = _mock_pyarrow_table(2, "image_data", [None, b"ok"])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage)
        with pytest.raises(ValueError, match="No full data for row 0"):
            mgr.get_images_batch("ds", row_ids=[0, 1])

    def test_batch_empty_row_ids(self) -> None:
        """Empty row_ids list returns empty handles list."""
        table = _mock_pyarrow_table(0, "image_data", [])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage)
        handles = mgr.get_images_batch("ds", row_ids=[])

        assert handles == []

    def test_batch_handles_can_decode(self) -> None:
        """Verify batch-returned handles actually decode to valid images."""
        raw = _make_png_bytes(16, 16)
        table = _mock_pyarrow_table(1, "image_data", [raw])

        storage = MagicMock()
        storage.read_dataset.return_value = table

        mgr = LazyDecodeManager(storage=storage)
        handles = mgr.get_images_batch("ds", row_ids=[0])

        img = handles[0].pixels()
        assert isinstance(img, Image.Image)
        assert img.size == (16, 16)
