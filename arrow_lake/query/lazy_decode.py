"""Lazy image decoding — Story 3.8.

Provides LazyImageHandle and LazyDecodeManager for on-demand
image decoding with column projection for fidelity selection.
"""

from __future__ import annotations

from typing import ClassVar

from PIL import Image

from arrow_lake.ingest.storage import LanceStorageManager


class LazyImageHandle:
    """Lazy image handle — decodes pixels only on first .pixels() call.

    Args:
        raw_bytes: Raw image bytes (JPEG, PNG, etc.).
        fidelity: Fidelity level used for this handle.
    """

    def __init__(self, raw_bytes: bytes, fidelity: str = "full") -> None:
        self._raw_bytes = raw_bytes
        self._fidelity = fidelity
        self._pixels_cache: Image.Image | None = None

    def pixels(self) -> Image.Image:
        """Decode and return the PIL Image.

        First call decodes the image; subsequent calls return cached result.

        Returns:
            Decoded PIL Image.
        """
        if self._pixels_cache is not None:
            return self._pixels_cache

        import io

        self._pixels_cache = Image.open(io.BytesIO(self._raw_bytes))
        self._pixels_cache.load()
        return self._pixels_cache

    @property
    def fidelity(self) -> str:
        return self._fidelity

    @property
    def size_bytes(self) -> int:
        return len(self._raw_bytes)


class LazyDecodeManager:
    """Manages lazy image decoding from Lance datasets.

    Uses column projection to read only the requested fidelity column,
    avoiding unnecessary I/O for original image data.

    Args:
        storage: LanceStorageManager instance.
        default_quality: Default fidelity level ("thumbnail", "preview", "full").
    """

    _FIDELITY_COLUMNS: ClassVar[dict[str, str]] = {
        "thumbnail": "image_thumbnail",
        "preview": "image_preview",
        "full": "image_data",
    }

    def __init__(
        self,
        storage: LanceStorageManager,
        default_quality: str = "full",
    ) -> None:
        self._storage = storage
        self.default_quality = default_quality

    def get_image(
        self,
        dataset_name: str,
        row_id: int,
        fidelity: str | None = None,
    ) -> LazyImageHandle:
        """Get a lazy image handle for a specific row.

        Args:
            dataset_name: Name of the Lance dataset.
            row_id: Row index (0-based).
            fidelity: Fidelity level. Uses default if None.

        Returns:
            LazyImageHandle that decodes on .pixels() call.
        """
        quality = fidelity or self.default_quality
        col_name = self._FIDELITY_COLUMNS[quality]

        table = self._storage.read_dataset(
            dataset_name,
            columns=["id", col_name],
        )

        if row_id >= table.num_rows:
            raise IndexError(f"Row {row_id} out of range (0-{table.num_rows - 1})")

        raw_bytes = table.column(col_name)[row_id].as_py()
        if raw_bytes is None:
            raise ValueError(f"No {quality} data for row {row_id} in '{dataset_name}'")

        return LazyImageHandle(raw_bytes=raw_bytes, fidelity=quality)

    def get_images_batch(
        self,
        dataset_name: str,
        row_ids: list[int],
        fidelity: str | None = None,
    ) -> list[LazyImageHandle]:
        """Get lazy image handles for a batch of rows.

        Args:
            dataset_name: Name of the Lance dataset.
            row_ids: List of row indices.
            fidelity: Fidelity level. Uses default if None.

        Returns:
            List of LazyImageHandle objects.
        """
        quality = fidelity or self.default_quality
        col_name = self._FIDELITY_COLUMNS[quality]

        table = self._storage.read_dataset(
            dataset_name,
            columns=["id", col_name],
        )

        handles: list[LazyImageHandle] = []
        for row_id in row_ids:
            if row_id >= table.num_rows:
                raise IndexError(f"Row {row_id} out of range (0-{table.num_rows - 1})")

            raw_bytes = table.column(col_name)[row_id].as_py()
            if raw_bytes is None:
                raise ValueError(f"No {quality} data for row {row_id} in '{dataset_name}'")

            handles.append(LazyImageHandle(raw_bytes=raw_bytes, fidelity=quality))

        return handles
