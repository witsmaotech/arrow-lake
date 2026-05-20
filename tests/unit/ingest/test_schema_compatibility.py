"""Tests for SchemaCompatibilityChecker — schema migration safety validation."""

from __future__ import annotations

import pyarrow as pa
import pytest

from arrow_lake.ingest.schema import (
    SchemaCompatibilityChecker,
    SchemaMigrationError,
    UNIFIED_SCHEMA,
)


class TestCheckAddColumn:
    """Validation for adding new columns."""

    def test_add_new_column_is_safe(self) -> None:
        checker = SchemaCompatibilityChecker(UNIFIED_SCHEMA)
        issues = checker.check_add_column("new_col", pa.string())
        assert issues == []

    def test_add_existing_column_fails(self) -> None:
        checker = SchemaCompatibilityChecker(UNIFIED_SCHEMA)
        issues = checker.check_add_column("text_content", pa.string())
        assert len(issues) == 1
        assert "already exists" in issues[0]

    def test_add_column_with_compatible_default(self) -> None:
        checker = SchemaCompatibilityChecker(UNIFIED_SCHEMA)
        issues = checker.check_add_column("score", pa.float64(), default_value=0.0)
        assert issues == []

    def test_add_column_with_incompatible_default(self) -> None:
        checker = SchemaCompatibilityChecker(UNIFIED_SCHEMA)
        issues = checker.check_add_column("score", pa.int32(), default_value="not_a_number")
        assert len(issues) == 1
        assert "incompatible" in issues[0].lower()

    def test_add_column_with_none_default_is_safe(self) -> None:
        checker = SchemaCompatibilityChecker(UNIFIED_SCHEMA)
        issues = checker.check_add_column("tag", pa.string(), default_value=None)
        assert issues == []


class TestCheckAlterColumn:
    """Validation for altering column types."""

    def test_same_type_is_safe(self) -> None:
        schema = pa.schema([pa.field("age", pa.int64())])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_alter_column("age", pa.int64())
        assert issues == []

    def test_safe_widening_no_issues(self) -> None:
        schema = pa.schema([pa.field("age", pa.int32())])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_alter_column("age", pa.int64())
        assert issues == []

    def test_narrowing_int64_to_int32_warns(self) -> None:
        schema = pa.schema([pa.field("value", pa.int64())])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_alter_column("value", pa.int32())
        assert len(issues) == 1
        assert "narrowing" in issues[0].lower() or "truncate" in issues[0].lower()

    def test_narrowing_float64_to_float32_warns(self) -> None:
        schema = pa.schema([pa.field("price", pa.float64())])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_alter_column("price", pa.float32())
        assert len(issues) == 1

    def test_nonexistent_column_fails(self) -> None:
        schema = pa.schema([pa.field("age", pa.int64())])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_alter_column("unknown", pa.int32())
        assert len(issues) == 1
        assert "does not exist" in issues[0]

    def test_vector_dimension_mismatch(self) -> None:
        schema = pa.schema([pa.field("emb", pa.list_(pa.float32(), 128))])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_alter_column("emb", pa.list_(pa.float32(), 256))
        assert len(issues) == 1
        assert "dimension" in issues[0].lower()

    def test_vector_same_dimension_is_safe(self) -> None:
        schema = pa.schema([pa.field("emb", pa.list_(pa.float32(), 128))])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_alter_column("emb", pa.list_(pa.float32(), 128))
        assert issues == []


class TestCheckDropColumn:
    """Validation for dropping columns."""

    def test_drop_regular_column_is_safe(self) -> None:
        schema = pa.schema([pa.field("name", pa.string()), pa.field("age", pa.int64())])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_drop_column("age")
        assert issues == []

    def test_drop_nonexistent_column_fails(self) -> None:
        schema = pa.schema([pa.field("name", pa.string())])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_drop_column("unknown")
        assert len(issues) == 1
        assert "does not exist" in issues[0]

    def test_drop_indexed_column_warns(self) -> None:
        schema = pa.schema([pa.field("text_embedding", pa.list_(pa.float32(), 384))])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_drop_column(
            "text_embedding",
            indexed_columns=frozenset({"text_embedding"}),
        )
        assert len(issues) == 1
        assert "index" in issues[0].lower()

    def test_drop_unindexed_embedding_is_safe(self) -> None:
        schema = pa.schema([pa.field("text_embedding", pa.list_(pa.float32(), 384))])
        checker = SchemaCompatibilityChecker(schema)
        issues = checker.check_drop_column("text_embedding", indexed_columns=frozenset())
        assert issues == []


class TestSchemaMigrationError:
    """Verify the exception type works correctly."""

    def test_exception_message(self) -> None:
        with pytest.raises(SchemaMigrationError, match="data loss"):
            raise SchemaMigrationError("Narrowing may cause data loss")

    def test_exception_is_exception(self) -> None:
        assert issubclass(SchemaMigrationError, Exception)
