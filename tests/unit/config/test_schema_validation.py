"""Tests for arrow_lake.quality.schema_validation — Story 4.12 Schema Validation Gate."""

from __future__ import annotations

import pyarrow as pa
from arrow_lake.quality.schema_validation import SchemaValidationGate


def _target_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.int64()),
            pa.field("name", pa.string()),
            pa.field("score", pa.float64()),
        ]
    )


def _valid_rows() -> list[dict]:
    return [
        {"id": 1, "name": "alice", "score": 0.9},
        {"id": 2, "name": "bob", "score": 0.8},
    ]


class TestSchemaValidationGateStrict:
    """Test SchemaValidationGate in strict mode."""

    def test_valid_rows_all_pass(self) -> None:
        gate = SchemaValidationGate(mode="strict")
        target = _target_schema()
        valid, rejected = gate.validate(_valid_rows(), target)
        assert len(valid) == 2
        assert len(rejected) == 0

    def test_unknown_column_rejected(self) -> None:
        gate = SchemaValidationGate(mode="strict")
        target = _target_schema()
        rows = [{"id": 1, "name": "a", "score": 0.5, "extra": "bad"}]
        valid, rejected = gate.validate(rows, target)
        assert len(valid) == 0
        assert len(rejected) == 1
        assert "extra" in rejected[0]["_rejection_reason"]

    def test_type_mismatch_rejected(self) -> None:
        gate = SchemaValidationGate(mode="strict")
        target = _target_schema()
        rows = [{"id": "not_an_int", "name": "a", "score": 0.5}]
        valid, rejected = gate.validate(rows, target)
        assert len(valid) == 0
        assert len(rejected) == 1

    def test_missing_column_rejected(self) -> None:
        gate = SchemaValidationGate(mode="strict")
        target = pa.schema(
            [
                pa.field("id", pa.int64(), nullable=False),
                pa.field("name", pa.string()),
                pa.field("score", pa.float64(), nullable=False),
            ]
        )
        rows = [{"id": 1, "name": "a"}]  # missing score (non-nullable)
        valid, rejected = gate.validate(rows, target)
        assert len(valid) == 0
        assert len(rejected) == 1

    def test_mixed_valid_and_invalid(self) -> None:
        gate = SchemaValidationGate(mode="strict")
        target = _target_schema()
        rows = [
            {"id": 1, "name": "ok", "score": 0.5},
            {"id": "bad", "name": "a", "score": 0.3},
        ]
        valid, rejected = gate.validate(rows, target)
        assert len(valid) == 1
        assert len(rejected) == 1


class TestSchemaValidationGateLenient:
    """Test SchemaValidationGate in lenient mode."""

    def test_unknown_column_dropped(self) -> None:
        gate = SchemaValidationGate(mode="lenient")
        target = _target_schema()
        rows = [{"id": 1, "name": "a", "score": 0.5, "extra": "dropped"}]
        valid, rejected = gate.validate(rows, target)
        assert len(valid) == 1
        assert "extra" not in valid[0]
        assert len(rejected) == 0

    def test_safe_cast_compatible_types(self) -> None:
        gate = SchemaValidationGate(mode="lenient")
        target = _target_schema()
        # int32 → int64 is safe
        rows = [{"id": 1, "name": "a", "score": 0.5}]
        valid, rejected = gate.validate(rows, target)
        assert len(valid) == 1
        assert len(rejected) == 0

    def test_incompatible_type_rejected(self) -> None:
        gate = SchemaValidationGate(mode="lenient")
        target = _target_schema()
        rows = [{"id": "not_a_number", "name": "a", "score": 0.5}]
        valid, rejected = gate.validate(rows, target)
        assert len(valid) == 0
        assert len(rejected) == 1

    def test_missing_nullable_column_ok(self) -> None:
        gate = SchemaValidationGate(mode="lenient")
        # score is nullable → missing should be ok
        target = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("name", pa.string()),
                pa.field("score", pa.float32(), nullable=True),
            ]
        )
        rows = [{"id": 1, "name": "a"}]
        valid, rejected = gate.validate(rows, target)
        assert len(valid) == 1
        assert len(rejected) == 0


class TestSchemaValidationGateEmpty:
    """Test SchemaValidationGate with empty input."""

    def test_empty_rows(self) -> None:
        gate = SchemaValidationGate(mode="strict")
        target = _target_schema()
        valid, rejected = gate.validate([], target)
        assert len(valid) == 0
        assert len(rejected) == 0
