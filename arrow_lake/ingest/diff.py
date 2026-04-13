"""Version diff — Story 2.4.

Compare two dataset versions and report structural differences:
added/removed rows and schema changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa


@dataclass(frozen=True)
class VersionDiff:
    """Immutable result of comparing two dataset versions."""

    added_rows: int = 0
    removed_rows: int = 0
    schema_changes: list[dict[str, Any]] = field(default_factory=list)


class VersionDiffer:
    """Compare two versions of a Lance dataset.

    Args:
        manager: LanceStorageManager instance for dataset access.
    """

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def diff(
        self,
        name: str,
        left: int | str,
        right: int | str,
    ) -> VersionDiff:
        """Compare two versions of a dataset.

        Args:
            name: Dataset name.
            left: Left version number or tag name.
            right: Right version number or tag name.

        Returns:
            VersionDiff with added_rows, removed_rows, schema_changes.
        """
        left_data = self._read_version(name, left)
        right_data = self._read_version(name, right)

        row_diff = right_data.num_rows - left_data.num_rows
        added_rows = max(0, row_diff)
        removed_rows = max(0, -row_diff)

        schema_changes = self._compare_schemas(left_data.schema, right_data.schema)

        return VersionDiff(
            added_rows=added_rows,
            removed_rows=removed_rows,
            schema_changes=schema_changes,
        )

    def _read_version(self, name: str, version: int | str) -> pa.Table:
        """Read a specific version or tag from a dataset."""
        if isinstance(version, int):
            return self._manager.read_dataset(name, version=version)
        return self._manager.read_at_tag(name, version)

    @staticmethod
    def _compare_schemas(left: pa.Schema, right: pa.Schema) -> list[dict[str, Any]]:
        """Compare two schemas and return list of changes."""
        changes: list[dict[str, Any]] = []

        left_names = set(left.names)
        right_names = set(right.names)

        for col in sorted(right_names - left_names):
            changes.append({"type": "column_added", "column": col})

        for col in sorted(left_names - right_names):
            changes.append({"type": "column_removed", "column": col})

        for col in sorted(left_names & right_names):
            left_type = left.field(col).type
            right_type = right.field(col).type
            if not left_type.equals(right_type):
                changes.append(
                    {
                        "type": "column_type_changed",
                        "column": col,
                        "old_type": str(left_type),
                        "new_type": str(right_type),
                    }
                )

        return changes
