"""Schema validation gate (Story 4.12).

Validates incoming rows against a target PyArrow schema before they
enter the quality pipeline.  Two modes:

- **strict**: unknown columns → reject; type mismatch → reject.
- **lenient**: unknown columns → drop + warn; compatible types → safe cast;
  incompatible types → reject.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import structlog

logger = structlog.get_logger(__name__)


class SchemaValidationGate:
    """Gate that validates rows against a target schema.

    Args:
        mode: ``"strict"`` or ``"lenient"``.
    """

    def __init__(self, mode: str = "lenient") -> None:
        if mode not in ("strict", "lenient"):
            raise ValueError(f"mode must be 'strict' or 'lenient', got '{mode}'")
        self._mode = mode

    @property
    def mode(self) -> str:
        return self._mode

    def validate(
        self,
        rows: list[dict[str, Any]],
        target_schema: pa.Schema,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Validate *rows* against *target_schema*.

        Returns:
            (valid_rows, rejected_rows) where rejected rows include a
            ``_rejection_reason`` key.
        """
        if not rows:
            return [], []

        target_names = set(target_schema.names)
        target_map = {f.name: f.type for f in target_schema}

        valid: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for row in rows:
            errors: list[str] = []
            clean: dict[str, Any] = {}

            row_keys = set(row.keys())

            # Check for unknown columns
            unknown = row_keys - target_names
            if unknown:
                if self._mode == "strict":
                    errors.append(f"Unknown columns: {', '.join(sorted(unknown))}")
                else:
                    logger.warning(
                        "schema_validation_dropping_columns",
                        columns=sorted(unknown),
                    )

            # Check for missing columns
            missing = target_names - row_keys
            for col_name in sorted(missing):
                field = target_schema.field(col_name)
                if field.nullable:
                    clean[col_name] = None
                else:
                    errors.append(f"Missing required column: {col_name}")

            # Validate types for present columns
            for col_name, col_type in target_map.items():
                if col_name not in row:
                    continue
                value = row[col_name]
                if value is None:
                    clean[col_name] = None
                    continue

                value_type = _infer_arrow_type(value)
                if value_type is None:
                    errors.append(f"Column '{col_name}': cannot infer type for value {value!r}")
                    clean[col_name] = value
                    continue

                if value_type == col_type or (
                    self._mode == "lenient" and _can_safe_cast(value_type, col_type)
                ):
                    clean[col_name] = value
                elif self._mode == "strict":
                    errors.append(f"Column '{col_name}': expected {col_type}, got {value_type}")
                    clean[col_name] = value
                else:
                    errors.append(f"Column '{col_name}': expected {col_type}, got {value_type}")
                    clean[col_name] = value

            # Copy known columns that weren't type-checked above
            for col_name in row_keys & target_names:
                if col_name not in clean:
                    clean[col_name] = row[col_name]

            if errors:
                rejected.append({**clean, "_rejection_reason": "; ".join(errors)})
            else:
                valid.append(clean)

        logger.debug(
            "schema_validation_result",
            mode=self._mode,
            total=len(rows),
            valid=len(valid),
            rejected=len(rejected),
        )
        return valid, rejected


def _infer_arrow_type(value: Any) -> pa.DataType | None:
    """Infer the PyArrow type from a Python value."""
    if isinstance(value, bool):
        return pa.bool_()
    if isinstance(value, int):
        return pa.int64()
    if isinstance(value, float):
        return pa.float64()
    if isinstance(value, str):
        return pa.string()
    if isinstance(value, bytes):
        return pa.binary()
    if isinstance(value, list):
        return pa.list_(pa.string())  # approximate
    return None


def _can_safe_cast(source: pa.DataType, target: pa.DataType) -> bool:
    """Check if *source* can be safely cast to *target*.

    Safe casts include:
    - int32 → int64 (wider integer)
    - float32 → float64 (wider float)
    - int → float (int to wider float)
    - Same kind, wider width
    """
    if source == target:
        return True

    # int to wider int
    if pa.types.is_integer(source) and pa.types.is_integer(target):
        return source.bit_width <= target.bit_width

    # float to wider float
    if pa.types.is_floating(source) and pa.types.is_floating(target):
        return source.bit_width <= target.bit_width

    # int to float
    if pa.types.is_integer(source) and pa.types.is_floating(target):
        return source.bit_width <= target.bit_width

    return False
