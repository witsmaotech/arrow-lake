"""Tests for quality/schema_validation.py — SchemaValidationGate."""

from __future__ import annotations

import pyarrow as pa
import pytest

from arrow_lake.quality.schema_validation import (
    SchemaValidationGate,
    _can_safe_cast,
    _infer_arrow_type,
)


def _schema() -> pa.Schema:
    return pa.schema([
        ("id", pa.int64()),
        ("name", pa.string()),
        ("score", pa.float64()),
        ("active", pa.bool_()),
    ])


# ===========================================================================
# SchemaValidationGate init
# ===========================================================================


class TestInit:
    def test_default_lenient(self) -> None:
        gate = SchemaValidationGate()
        assert gate.mode == "lenient"

    def test_strict_mode(self) -> None:
        assert SchemaValidationGate("strict").mode == "strict"

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="mode must be"):
            SchemaValidationGate("invalid")


# ===========================================================================
# validate — strict mode
# ===========================================================================


class TestStrictMode:
    def test_valid_rows_pass(self) -> None:
        gate = SchemaValidationGate("strict")
        valid, rejected = gate.validate(
            [{"id": 1, "name": "a", "score": 0.5, "active": True}],
            _schema(),
        )
        assert len(valid) == 1
        assert len(rejected) == 0

    def test_unknown_columns_rejected(self) -> None:
        gate = SchemaValidationGate("strict")
        valid, rejected = gate.validate(
            [{"id": 1, "name": "a", "extra": "x"}],
            _schema(),
        )
        assert len(rejected) == 1
        assert "Unknown" in rejected[0]["_rejection_reason"]

    def test_missing_required_column_rejected(self) -> None:
        gate = SchemaValidationGate("strict")
        schema = pa.schema([
            pa.field("id", pa.int64(), nullable=False),
            pa.field("name", pa.string(), nullable=False),
        ])
        valid, rejected = gate.validate([{"id": 1}], schema)
        assert len(rejected) == 1

    def test_missing_nullable_column_fills_null(self) -> None:
        gate = SchemaValidationGate("strict")
        schema = pa.schema([("id", pa.int64()), ("name", pa.string())])
        valid, rejected = gate.validate([{"id": 1}], schema)
        assert valid[0]["name"] is None

    def test_type_mismatch_rejected(self) -> None:
        gate = SchemaValidationGate("strict")
        valid, rejected = gate.validate(
            [{"id": "not_an_int", "name": "a"}],
            _schema(),
        )
        assert len(rejected) == 1


# ===========================================================================
# validate — lenient mode
# ===========================================================================


class TestLenientMode:
    def test_unknown_columns_dropped(self) -> None:
        gate = SchemaValidationGate("lenient")
        valid, rejected = gate.validate(
            [{"id": 1, "name": "a", "extra": "x"}],
            _schema(),
        )
        assert len(valid) == 1
        assert "extra" not in valid[0]

    def test_null_values_pass(self) -> None:
        gate = SchemaValidationGate("lenient")
        valid, rejected = gate.validate(
            [{"id": None, "name": None}],
            _schema(),
        )
        assert len(valid) == 1
        assert valid[0]["id"] is None

    def test_empty_rows(self) -> None:
        gate = SchemaValidationGate("lenient")
        valid, rejected = gate.validate([], _schema())
        assert valid == []
        assert rejected == []

    def test_safe_cast_int_to_float(self) -> None:
        gate = SchemaValidationGate("lenient")
        schema = pa.schema([("val", pa.float64())])
        valid, rejected = gate.validate([{"val": 42}], schema)
        assert len(valid) == 1


# ===========================================================================
# _infer_arrow_type
# ===========================================================================


class TestInferArrowType:
    def test_bool(self) -> None:
        assert _infer_arrow_type(True) == pa.bool_()

    def test_int(self) -> None:
        assert _infer_arrow_type(42) == pa.int64()

    def test_float(self) -> None:
        assert _infer_arrow_type(3.14) == pa.float64()

    def test_str(self) -> None:
        assert _infer_arrow_type("hello") == pa.string()

    def test_bytes(self) -> None:
        assert _infer_arrow_type(b"data") == pa.binary()

    def test_list(self) -> None:
        assert _infer_arrow_type(["a", "b"]) is not None

    def test_unknown_returns_none(self) -> None:
        assert _infer_arrow_type(object()) is None


# ===========================================================================
# _can_safe_cast
# ===========================================================================


class TestCanSafeCast:
    def test_same_type(self) -> None:
        assert _can_safe_cast(pa.int64(), pa.int64())

    def test_int32_to_int64(self) -> None:
        assert _can_safe_cast(pa.int32(), pa.int64())

    def test_int64_to_int32_fails(self) -> None:
        assert not _can_safe_cast(pa.int64(), pa.int32())

    def test_float32_to_float64(self) -> None:
        assert _can_safe_cast(pa.float32(), pa.float64())

    def test_int_to_float(self) -> None:
        assert _can_safe_cast(pa.int32(), pa.float64())

    def test_string_to_int_fails(self) -> None:
        assert not _can_safe_cast(pa.string(), pa.int64())
