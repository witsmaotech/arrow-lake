"""Arrow Lake data assertion helpers — Story 2.7.

Provides pytest-compatible assertions for Arrow Tables and datasets.
All helpers raise AssertionError with descriptive messages on failure.
"""

from __future__ import annotations

from typing import Any


def assert_table_has_schema(table: Any, expected_schema: Any) -> None:
    """Assert that an Arrow table matches the expected schema.

    Args:
        table: Arrow Table to check.
        expected_schema: Expected Arrow Schema.

    Raises:
        AssertionError: If schemas differ.
    """
    actual = table.schema
    if actual.equals(expected_schema):
        return

    actual_fields = sorted(actual.names)
    expected_fields = sorted(expected_schema.names)
    differences: list[str] = []

    if actual_fields != expected_fields:
        differences.append(f"fields differ: got {actual_fields}, expected {expected_fields}")

    for name in expected_fields:
        if name not in actual.names:
            differences.append(f"missing field: {name}")
            continue
        actual_type = actual.field(name).type
        expected_type = expected_schema.field(name).type
        if not actual_type.equals(expected_type):
            differences.append(
                f"field '{name}' type differs: got {actual_type}, expected {expected_type}"
            )

    for name in actual_fields:
        if name not in sorted(expected_schema.names):
            differences.append(f"extra field: {name}")

    raise AssertionError(f"Schema mismatch: {'; '.join(differences)}")


def assert_row_count(table: Any, expected: int) -> None:
    """Assert that a table has exactly the expected number of rows.

    Args:
        table: Arrow Table to check.
        expected: Expected row count.

    Raises:
        AssertionError: If row count differs.
    """
    actual = table.num_rows
    if actual == expected:
        return
    raise AssertionError(f"Row count: expected {expected}, got {actual}")


def assert_column_values_unique(table: Any, column: str) -> None:
    """Assert that all values in a column are unique (no duplicates).

    Args:
        table: Arrow Table to check.
        column: Column name to validate.

    Raises:
        AssertionError: If column has duplicates or doesn't exist.
    """
    if column not in table.schema.names:
        raise AssertionError(f"Column '{column}' not found in schema: {table.schema.names}")

    values = table.column(column).to_pylist()
    if len(values) == len(set(values)):
        return

    from collections import Counter

    counts = Counter(values)
    duplicates = {v: c for v, c in counts.items() if c > 1}
    raise AssertionError(
        f"Column '{column}' has {len(duplicates)} duplicate value(s): {dict(list(duplicates.items())[:5])}"
    )


def assert_column_within_range(table: Any, column: str, min_val: float, max_val: float) -> None:
    """Assert that all values in a numeric column fall within [min_val, max_val].

    Args:
        table: Arrow Table to check.
        column: Column name to validate.
        min_val: Minimum allowed value (inclusive).
        max_val: Maximum allowed value (inclusive).

    Raises:
        AssertionError: If any value is out of range.
    """
    if column not in table.schema.names:
        raise AssertionError(f"Column '{column}' not found in schema: {table.schema.names}")

    values = table.column(column).to_pylist()
    out_of_range = [v for v in values if v < min_val or v > max_val]
    if not out_of_range:
        return

    examples = out_of_range[:5]
    raise AssertionError(
        f"Column '{column}' has {len(out_of_range)} value(s) outside [{min_val}, {max_val}]: {examples}"
    )


def assert_dataset_version(dataset: Any, expected_version: int) -> None:
    """Assert that a Lance dataset is at the expected version.

    Args:
        dataset: Lance dataset or LanceStorageManager result with a .version attribute.
        expected_version: Expected version number.

    Raises:
        AssertionError: If version differs.
    """
    actual = dataset.version
    if actual == expected_version:
        return
    raise AssertionError(f"Dataset version: expected {expected_version}, got {actual}")
