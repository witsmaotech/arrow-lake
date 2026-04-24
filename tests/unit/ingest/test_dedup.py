"""Tests for arrow_lake.quality.dedup — Story 4.7 Content Deduplication."""

from __future__ import annotations

import hashlib

import pyarrow as pa
import pytest
from arrow_lake.exceptions import ErrorCode, QualityError
from arrow_lake.quality.dedup import ContentDeduplicator, DedupResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_image_table(rows: list[tuple[str, bytes]]) -> pa.Table:
    """Create a table with id + image_data columns."""
    ids = [r[0] for r in rows]
    data = [r[1] for r in rows]
    return pa.table({"id": ids, "image_data": data})


def _make_text_table(rows: list[tuple[str, str]]) -> pa.Table:
    """Create a table with id + text_content columns."""
    ids = [r[0] for r in rows]
    text = [r[1] for r in rows]
    return pa.table({"id": ids, "text_content": text})


# ---------------------------------------------------------------------------
# DedupResult
# ---------------------------------------------------------------------------


class TestDedupResult:
    """Test DedupResult frozen dataclass."""

    def test_is_frozen(self) -> None:
        table = pa.table({"id": [1]})
        result = DedupResult(
            total_rows=5,
            unique_rows=3,
            duplicates_found=2,
            strategy="exact",
            action="flag",
            table=table,
        )
        with pytest.raises(AttributeError):
            result.total_rows = 10  # type: ignore[misc]

    def test_fields(self) -> None:
        table = pa.table({"id": [1]})
        result = DedupResult(
            total_rows=5,
            unique_rows=3,
            duplicates_found=2,
            strategy="exact",
            action="remove",
            table=table,
        )
        assert result.total_rows == 5
        assert result.unique_rows == 3
        assert result.duplicates_found == 2
        assert result.strategy == "exact"
        assert result.action == "remove"
        assert result.table.num_rows == 1


# ---------------------------------------------------------------------------
# SHA-256 hashing
# ---------------------------------------------------------------------------


class TestSHA256:
    """Test SHA-256 hash computation."""

    def test_empty_bytes_returns_empty_string(self) -> None:
        d = ContentDeduplicator()
        assert d._compute_sha256(b"") == hashlib.sha256(b"").hexdigest()

    def test_none_returns_empty_string(self) -> None:
        d = ContentDeduplicator()
        assert d._compute_sha256(None) == ""

    def test_same_content_same_hash(self) -> None:
        d = ContentDeduplicator()
        data = b"hello world"
        assert d._compute_sha256(data) == d._compute_sha256(data)

    def test_different_content_different_hash(self) -> None:
        d = ContentDeduplicator()
        assert d._compute_sha256(b"abc") != d._compute_sha256(b"def")

    def test_string_encoded_to_utf8(self) -> None:
        d = ContentDeduplicator()
        assert d._compute_sha256("hello") == d._compute_sha256(b"hello")

    def test_non_bytes_non_string_returns_empty(self) -> None:
        d = ContentDeduplicator()
        assert d._compute_sha256(12345) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# pHash computation
# ---------------------------------------------------------------------------


class TestPHash:
    """Test perceptual hash computation."""

    def test_none_returns_zero(self) -> None:
        d = ContentDeduplicator()
        assert d._compute_phash(None) == 0

    @pytest.mark.skip(reason="imagehash not installed in CI")
    def test_invalid_bytes_raises_quality_error(self) -> None:
        d = ContentDeduplicator()
        # Non-image bytes should raise QualityError when imagehash is available
        with pytest.raises(QualityError) as exc_info:
            d._compute_phash(b"not an image")
        assert exc_info.value.error_code == ErrorCode.DEDUP_HASH_COMPUTATION_FAILED

    def test_hamming_distance_identical(self) -> None:
        assert ContentDeduplicator._hamming_distance(0b101010, 0b101010) == 0

    def test_hamming_distance_different(self) -> None:
        # 0b101010 vs 0b010101 → all 6 bits differ
        assert ContentDeduplicator._hamming_distance(0b101010, 0b010101) == 6

    def test_hamming_distance_zero(self) -> None:
        assert ContentDeduplicator._hamming_distance(0, 0) == 0


# ---------------------------------------------------------------------------
# Exact strategy
# ---------------------------------------------------------------------------


class TestExactStrategy:
    """Test SHA-256 exact match dedup."""

    def test_detects_duplicate_rows(self) -> None:
        data = b"same-image-bytes"
        table = _make_image_table(
            [
                ("a", data),
                ("b", data),
                ("c", b"different-bytes"),
            ]
        )
        d = ContentDeduplicator(strategy="exact", action="remove")
        result = d.deduplicate(table)

        assert result.total_rows == 3
        assert result.unique_rows == 2
        assert result.duplicates_found == 1
        assert result.strategy == "exact"

    def test_flag_action_adds_column(self) -> None:
        data = b"same-image-bytes"
        table = _make_image_table(
            [
                ("a", data),
                ("b", data),
            ]
        )
        d = ContentDeduplicator(strategy="exact", action="flag")
        result = d.deduplicate(table)

        assert result.total_rows == 2
        assert "is_duplicate" in result.table.column_names
        # First row is unique, second is duplicate
        flags = result.table.column("is_duplicate").to_pylist()
        assert flags == [False, True]

    def test_no_duplicates(self) -> None:
        table = _make_image_table(
            [
                ("a", b"img-a"),
                ("b", b"img-b"),
                ("c", b"img-c"),
            ]
        )
        d = ContentDeduplicator(strategy="exact", action="remove")
        result = d.deduplicate(table)

        assert result.duplicates_found == 0
        assert result.unique_rows == 3

    def test_empty_table(self) -> None:
        table = pa.table({"id": [], "image_data": []}).cast(
            pa.schema([("id", pa.string()), ("image_data", pa.binary())])
        )
        d = ContentDeduplicator(strategy="exact")
        result = d.deduplicate(table)

        assert result.total_rows == 0
        assert result.duplicates_found == 0


# ---------------------------------------------------------------------------
# Perceptual strategy
# ---------------------------------------------------------------------------


class TestPerceptualStrategy:
    """Test pHash perceptual dedup."""

    def test_perceptual_strategy_name(self) -> None:
        d = ContentDeduplicator(strategy="perceptual")
        assert d.name == "dedup_perceptual"

    def test_perceptual_no_image_column(self) -> None:
        """When no image column exists, all rows are considered unique (hash=0)."""
        table = pa.table({"id": ["a", "b", "c"], "text_content": ["x", "y", "z"]})
        d = ContentDeduplicator(strategy="perceptual", action="remove")
        result = d.deduplicate(table)

        # All get hash=0 → all kept as unique
        assert result.unique_rows == 3
        assert result.duplicates_found == 0


# ---------------------------------------------------------------------------
# Both strategy
# ---------------------------------------------------------------------------


class TestBothStrategy:
    """Test combined exact + perceptual dedup."""

    def test_both_strategy_name(self) -> None:
        d = ContentDeduplicator(strategy="both")
        assert d.name == "dedup_both"

    def test_exact_then_perceptual(self) -> None:
        from io import BytesIO

        from PIL import Image

        def _small_png() -> bytes:
            buf = BytesIO()
            Image.new("RGB", (4, 4), color=(0, 0, 0)).save(buf, format="PNG")
            return buf.getvalue()

        def _small_png_red() -> bytes:
            buf = BytesIO()
            Image.new("RGB", (4, 4), color=(255, 0, 0)).save(buf, format="PNG")
            return buf.getvalue()

        data = _small_png()
        table = _make_image_table(
            [
                ("a", data),
                ("b", data),
                ("c", _small_png_red()),
            ]
        )
        d = ContentDeduplicator(strategy="both", action="remove")
        result = d.deduplicate(table)

        assert result.total_rows == 3
        # After exact dedup: 2 unique rows remain
        # Perceptual won't match them since they're different images
        assert result.unique_rows == 2
        assert result.duplicates_found == 1


# ---------------------------------------------------------------------------
# Incremental dedup
# ---------------------------------------------------------------------------


class TestIncremental:
    """Test incremental (cross-batch) dedup."""

    def test_incremental_first_batch(self) -> None:
        table = _make_image_table([("a", b"img-a"), ("b", b"img-b")])
        d = ContentDeduplicator(strategy="exact", action="remove")
        result, seen = d.deduplicate_incremental(table, existing_sha256=None)

        assert result.unique_rows == 2
        assert result.duplicates_found == 0
        assert len(seen) == 2

    def test_incremental_second_batch_detects_dups(self) -> None:
        batch1 = _make_image_table([("a", b"img-a"), ("b", b"img-b")])
        d = ContentDeduplicator(strategy="exact", action="remove")

        _, seen = d.deduplicate_incremental(batch1)

        batch2 = _make_image_table(
            [
                ("c", b"img-a"),  # dup of batch1 row a
                ("d", b"img-c"),  # new
            ]
        )
        result, seen2 = d.deduplicate_incremental(batch2, existing_sha256=seen)

        assert result.unique_rows == 1
        assert result.duplicates_found == 1
        # seen map should now have 3 entries
        assert len(seen2) == 3

    def test_incremental_empty_table(self) -> None:
        table = pa.table({"id": [], "image_data": []}).cast(
            pa.schema([("id", pa.string()), ("image_data", pa.binary())])
        )
        d = ContentDeduplicator(strategy="exact")
        result, seen = d.deduplicate_incremental(table)
        assert result.total_rows == 0
        assert seen == {}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    """Test constructor validation."""

    def test_invalid_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="strategy"):
            ContentDeduplicator(strategy="invalid")

    def test_invalid_action_raises(self) -> None:
        with pytest.raises(ValueError, match="action"):
            ContentDeduplicator(action="delete")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestDedupConfig:
    """Test QualityConfig dedup fields."""

    def test_default_dedup_config(self) -> None:
        from arrow_lake.config import QualityConfig

        cfg = QualityConfig()
        assert cfg.dedup_enabled is False
        assert cfg.dedup_strategy == "exact"
        assert cfg.dedup_action == "flag"
        assert cfg.dedup_perceptual_threshold == 10

    def test_custom_dedup_config(self) -> None:
        from arrow_lake.config import QualityConfig

        cfg = QualityConfig(
            dedup_enabled=True,
            dedup_strategy="both",
            dedup_action="remove",
            dedup_perceptual_threshold=5,
        )
        assert cfg.dedup_enabled is True
        assert cfg.dedup_strategy == "both"
        assert cfg.dedup_action == "remove"
        assert cfg.dedup_perceptual_threshold == 5

    def test_default_export_config(self) -> None:
        from arrow_lake.config import ExportConfig

        cfg = ExportConfig()
        assert cfg.default_format == "parquet"
        assert cfg.parquet_compression == "snappy"
        assert cfg.csv_delimiter == ","
        assert cfg.allow_overwrite is False

    def test_export_config_invalid_format(self) -> None:
        from arrow_lake.config import ExportConfig

        with pytest.raises(ValueError, match="format"):
            ExportConfig(default_format="json")
