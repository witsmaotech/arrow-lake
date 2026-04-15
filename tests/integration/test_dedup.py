"""Integration tests for content deduplication — Story 4.7."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.quality.dedup import ContentDeduplicator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path / "lance_data"))


def _create_dataset(
    storage: LanceStorageManager,
    name: str,
    rows: list[tuple[str, bytes]],
) -> None:
    """Create a Lance dataset with id + image_data columns."""
    table = pa.table(
        {
            "id": [r[0] for r in rows],
            "image_data": [r[1] for r in rows],
        }
    )
    storage.create_dataset(name, table)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDedupIntegration:
    """Integration: Content dedup on real Arrow tables."""

    def test_exact_dedup_on_real_table(self) -> None:
        """SHA-256 exact dedup identifies duplicate binary content."""
        img_a = b"image-content-a"
        img_b = b"image-content-b"
        table = pa.table(
            {
                "id": ["r1", "r2", "r3", "r4"],
                "image_data": [img_a, img_a, img_b, img_a],  # 2 dups of img_a
            }
        )

        d = ContentDeduplicator(strategy="exact", action="remove")
        result = d.deduplicate(table)

        assert result.total_rows == 4
        assert result.unique_rows == 2  # img_a (1), img_b (1)
        assert result.duplicates_found == 2

    def test_flag_mode_preserves_all_rows(self) -> None:
        """Flag mode keeps all rows and adds is_duplicate column."""
        img_a = b"image-content-a"
        img_b = b"image-content-b"
        table = pa.table(
            {
                "id": ["r1", "r2", "r3"],
                "image_data": [img_a, img_a, img_b],
            }
        )

        d = ContentDeduplicator(strategy="exact", action="flag")
        result = d.deduplicate(table)

        assert result.table.num_rows == 3
        assert "is_duplicate" in result.table.column_names
        flags = result.table.column("is_duplicate").to_pylist()
        # r1 is first occurrence → not duplicate
        assert flags[0] is False
        # r2 is dup of r1 → duplicate
        assert flags[1] is True
        # r3 is unique → not duplicate
        assert flags[2] is False

    def test_incremental_across_batches(self) -> None:
        """Incremental dedup accumulates seen hashes across batches."""
        d = ContentDeduplicator(strategy="exact", action="remove")

        # Batch 1
        batch1 = pa.table(
            {
                "id": ["a", "b"],
                "image_data": [b"img-a", b"img-b"],
            }
        )
        result1, seen = d.deduplicate_incremental(batch1)
        assert result1.unique_rows == 2

        # Batch 2 with overlap
        batch2 = pa.table(
            {
                "id": ["c", "d", "e"],
                "image_data": [b"img-a", b"img-c", b"img-b"],  # a=dup, b=dup
            }
        )
        result2, _seen2 = d.deduplicate_incremental(batch2, existing_sha256=seen)
        assert result2.unique_rows == 1  # only img-c is new
        assert result2.duplicates_found == 2

    def test_lake_deduplicate_sdk(self, storage: LanceStorageManager) -> None:
        """Lake.deduplicate() end-to-end with real Lance storage."""
        from arrow_lake import Lake

        # Create dataset
        img_a = b"image-content-a"
        img_b = b"image-content-b"
        table = pa.table(
            {
                "id": ["r1", "r2", "r3"],
                "image_data": [img_a, img_a, img_b],
            }
        )
        storage.create_dataset("dedup_test", table)

        lake = Lake(base_uri=storage.base_uri)
        result = lake.deduplicate("dedup_test", strategy="exact", action="remove")

        assert result.unique_rows == 2
        assert result.duplicates_found == 1
