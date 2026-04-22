"""Tests for arrow_lake.quality.builtin — Story 4.9 Built-in Filters."""

from __future__ import annotations

import pyarrow as pa
from arrow_lake.quality.builtin import ImageResolutionFilter, TextLengthFilter


class TestTextLengthFilter:
    """Test TextLengthFilter."""

    def test_name(self) -> None:
        f = TextLengthFilter(min_chars=1, max_chars=None)
        assert f.name == "text_length"

    def test_all_pass_default_min(self) -> None:
        f = TextLengthFilter(min_chars=1, max_chars=None)
        table = pa.table({"text_content": ["hello", "world", "foo"]})
        passed, rejected = f.filter(table)
        assert passed.num_rows == 3
        assert rejected.num_rows == 0

    def test_reject_short_text(self) -> None:
        f = TextLengthFilter(min_chars=3, max_chars=None)
        table = pa.table({"text_content": ["ab", "hello", "", "xyz", "hi"]})
        passed, rejected = f.filter(table)
        assert passed.num_rows == 2  # "hello", "xyz"
        assert rejected.num_rows == 3  # "ab", "", "hi"

    def test_reject_long_text(self) -> None:
        f = TextLengthFilter(min_chars=1, max_chars=5)
        table = pa.table({"text_content": ["hello", "world!", "hi", "a"]})
        passed, rejected = f.filter(table)
        # "hello"=5 ok, "world!"=6 rejected, "hi"=2 ok, "a"=1 ok
        assert passed.num_rows == 3
        assert rejected.num_rows == 1

    def test_null_text_passes(self) -> None:
        f = TextLengthFilter(min_chars=5, max_chars=None)
        table = pa.table({"text_content": ["hello", None, "world"]})
        passed, _rejected = f.filter(table)
        assert passed.num_rows == 3  # NULLs always pass

    def test_missing_column_noop(self) -> None:
        f = TextLengthFilter(min_chars=1, max_chars=None)
        table = pa.table({"other_column": ["a", "b"]})
        passed, rejected = f.filter(table)
        assert passed.num_rows == 2
        assert rejected.num_rows == 0

    def test_empty_table(self) -> None:
        f = TextLengthFilter(min_chars=1, max_chars=None)
        table = pa.table({"text_content": []}).cast(pa.table({"text_content": []}).schema)
        passed, rejected = f.filter(table)
        assert passed.num_rows == 0
        assert rejected.num_rows == 0

    def test_zero_min_chars(self) -> None:
        f = TextLengthFilter(min_chars=0, max_chars=None)
        table = pa.table({"text_content": ["", "a", "ab"]})
        passed, _rejected = f.filter(table)
        assert passed.num_rows == 3

    def test_rejection_reason_in_rejected_rows(self) -> None:
        f = TextLengthFilter(min_chars=3, max_chars=None)
        table = pa.table({"text_content": ["ab", "hello"]})
        _passed, rejected = f.filter(table)
        assert "_rejection_reason" in rejected.column_names
        reasons = rejected.column("_rejection_reason").to_pylist()
        assert all("text_length" in r for r in reasons)


class TestImageResolutionFilter:
    """Test ImageResolutionFilter."""

    def test_name(self) -> None:
        f = ImageResolutionFilter(min_width=64, min_height=64)
        assert f.name == "image_resolution"

    def test_all_pass(self) -> None:
        f = ImageResolutionFilter(min_width=64, min_height=64)
        table = pa.table(
            {
                "image_width": [100, 200, 300],
                "image_height": [100, 200, 300],
            }
        )
        passed, rejected = f.filter(table)
        assert passed.num_rows == 3
        assert rejected.num_rows == 0

    def test_reject_small_images(self) -> None:
        f = ImageResolutionFilter(min_width=64, min_height=64)
        table = pa.table(
            {
                "image_width": [32, 100, 50],
                "image_height": [100, 64, 30],
            }
        )
        passed, rejected = f.filter(table)
        # Row 0: w=32<64 → reject. Row 1: w=100>=64, h=64>=64 → pass. Row 2: w=50<64 → reject
        assert passed.num_rows == 1
        assert rejected.num_rows == 2

    def test_null_dimensions_pass(self) -> None:
        f = ImageResolutionFilter(min_width=64, min_height=64)
        table = pa.table(
            {
                "image_width": [None, 100, 200],
                "image_height": [None, 50, 200],
            }
        )
        passed, rejected = f.filter(table)
        # Row 0: NULL → pass. Row 1: w=100 ok, h=50<64 → reject. Row 2: both ok → pass
        assert passed.num_rows == 2
        assert rejected.num_rows == 1

    def test_missing_width_column_noop(self) -> None:
        f = ImageResolutionFilter(min_width=64, min_height=64)
        table = pa.table({"image_height": [100, 200]})
        passed, rejected = f.filter(table)
        assert passed.num_rows == 2
        assert rejected.num_rows == 0

    def test_missing_height_column_noop(self) -> None:
        f = ImageResolutionFilter(min_width=64, min_height=64)
        table = pa.table({"image_width": [100, 200]})
        passed, rejected = f.filter(table)
        assert passed.num_rows == 2
        assert rejected.num_rows == 0

    def test_empty_table(self) -> None:
        f = ImageResolutionFilter(min_width=64, min_height=64)
        table = pa.table({"image_width": [], "image_height": []})
        passed, rejected = f.filter(table)
        assert passed.num_rows == 0
        assert rejected.num_rows == 0

    def test_rejection_reason_in_rejected_rows(self) -> None:
        f = ImageResolutionFilter(min_width=64, min_height=64)
        table = pa.table(
            {
                "image_width": [10, 100],
                "image_height": [10, 100],
            }
        )
        _passed, rejected = f.filter(table)
        assert "_rejection_reason" in rejected.column_names
        reasons = rejected.column("_rejection_reason").to_pylist()
        assert all("image_resolution" in r for r in reasons)
