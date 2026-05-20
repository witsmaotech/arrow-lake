"""Unified multimodal table schema — Story 3.5.

Defines UNIFIED_SCHEMA for flat Lance table storage and
UnifiedTableManager for creating/append/querying multimodal rows.

Flat column layout enables Lance predicate pushdown for efficient queries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa

from arrow_lake.ingest.storage import LanceStorageManager

__all__ = [
    "UNIFIED_SCHEMA",
    "SchemaCompatibilityChecker",
    "SchemaMigrationError",
    "UnifiedTableManager",
]


def _now_timestamp_us() -> int:
    """Return current time in microseconds since epoch."""
    return int(datetime.now(UTC).timestamp() * 1_000_000)


UNIFIED_SCHEMA = pa.schema(
    [
        # Identity
        pa.field("id", pa.string(), nullable=False),
        pa.field("modality", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=True),
        pa.field("created_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("filename", pa.string(), nullable=True),
        # Text
        pa.field("text_content", pa.string(), nullable=True),
        # Image
        pa.field("image_data", pa.binary(), nullable=True),
        pa.field("image_thumbnail", pa.binary(), nullable=True),
        pa.field("image_preview", pa.binary(), nullable=True),
        pa.field("image_width", pa.int32(), nullable=True),
        pa.field("image_height", pa.int32(), nullable=True),
        # EXIF
        pa.field("exif_make", pa.string(), nullable=True),
        pa.field("exif_model", pa.string(), nullable=True),
        pa.field("exif_gps_lat", pa.float64(), nullable=True),
        pa.field("exif_gps_lon", pa.float64(), nullable=True),
        pa.field("exif_capture_time", pa.string(), nullable=True),
        # Video
        pa.field("video_data", pa.binary(), nullable=True),
        pa.field("keyframe_count", pa.int32(), nullable=True),
        pa.field("video_duration_ms", pa.int64(), nullable=True),
        # Embedding
        pa.field("text_embedding", pa.list_(pa.float32(), 384), nullable=True),
    ]
)


# Column groups for each modality — used for efficient column projection
_TEXT_COLUMNS = [
    "id",
    "modality",
    "source",
    "created_at",
    "filename",
    "text_content",
    "text_embedding",
]

_IMAGE_COLUMNS = [
    "id",
    "modality",
    "source",
    "created_at",
    "filename",
    "image_data",
    "image_thumbnail",
    "image_preview",
    "image_width",
    "image_height",
    "exif_make",
    "exif_model",
    "exif_gps_lat",
    "exif_gps_lon",
    "exif_capture_time",
]

_VIDEO_COLUMNS = [
    "id",
    "modality",
    "source",
    "created_at",
    "filename",
    "video_data",
    "keyframe_count",
    "video_duration_ms",
]

_METADATA_COLUMNS = [
    "id",
    "modality",
    "source",
    "created_at",
    "filename",
]


class SchemaMigrationError(Exception):
    """Raised when a schema migration is incompatible with existing data."""


# Narrowing pairs: (check_source, check_target) → warning
_NARROWING_CHECKS: list[tuple[t.Any, t.Any]] = [
    # (is_source_type, is_target_type)
]


def _is_narrowing(old_type: pa.DataType, new_type: pa.DataType) -> bool:
    """Return True if changing old_type → new_type is a narrowing conversion."""
    if pa.types.is_int64(old_type) and (
        pa.types.is_int32(new_type) or pa.types.is_int16(new_type) or pa.types.is_int8(new_type)
    ):
        return True
    if pa.types.is_int32(old_type) and (pa.types.is_int16(new_type) or pa.types.is_int8(new_type)):
        return True
    if pa.types.is_float64(old_type) and pa.types.is_float32(new_type):
        return True
    return False


class SchemaCompatibilityChecker:
    """Validates schema migration operations for safety.

    Checks:
    - Column type narrowing: warns if narrowing may lose data (int64 → int32).
    - Column deletion with index conflict: warns if deleted column has a vector index.
    - New column default value compatibility.
    - Vector dimension matching for embedding columns.

    Usage::

        checker = SchemaCompatibilityChecker(current_schema)
        issues = checker.check_alter_column("age", pa.int32())
        if issues:
            raise SchemaMigrationError("; ".join(issues))
    """

    def __init__(self, current_schema: pa.Schema) -> None:
        self._schema = current_schema

    @property
    def schema(self) -> pa.Schema:
        return self._schema

    def check_add_column(
        self,
        column_name: str,
        column_type: pa.DataType,
        default_value: Any = None,
    ) -> list[str]:
        """Check if adding a column is safe.

        Returns:
            List of warning/error messages (empty = safe).
        """
        issues: list[str] = []

        if column_name in self._schema.names:
            issues.append(f"Column '{column_name}' already exists")

        if default_value is not None:
            try:
                pa.scalar(default_value, type=column_type)
            except (TypeError, ValueError, pa.ArrowInvalid) as exc:
                issues.append(
                    f"Default value {default_value!r} incompatible with {column_type}: {exc}"
                )

        return issues

    def check_alter_column(
        self,
        column_name: str,
        new_type: pa.DataType,
    ) -> list[str]:
        """Check if altering a column type is safe.

        Returns:
            List of warning/error messages (empty = safe).
        """
        issues: list[str] = []

        idx = self._schema.get_field_index(column_name)
        if idx < 0:
            issues.append(f"Column '{column_name}' does not exist")
            return issues

        old_type = self._schema.field(idx).type
        if old_type == new_type:
            return issues

        # Check type narrowing
        if _is_narrowing(old_type, new_type):
            issues.append(
                f"Narrowing {old_type} → {new_type} may truncate existing data"
            )

        # Check vector dimension mismatch
        if pa.types.is_fixed_size_list(old_type) and pa.types.is_fixed_size_list(new_type):
            if old_type.list_size != new_type.list_size:
                issues.append(
                    f"Vector dimension mismatch: {old_type.list_size} → {new_type.list_size}"
                )

        return issues

    def check_drop_column(
        self,
        column_name: str,
        indexed_columns: frozenset[str] = frozenset(),
    ) -> list[str]:
        """Check if dropping a column is safe.

        Args:
            column_name: Column to drop.
            indexed_columns: Set of columns with vector indexes.

        Returns:
            List of warning/error messages (empty = safe).
        """
        issues: list[str] = []

        idx = self._schema.get_field_index(column_name)
        if idx < 0:
            issues.append(f"Column '{column_name}' does not exist")
            return issues

        if column_name in indexed_columns:
            issues.append(
                f"Column '{column_name}' has a vector index — drop the index first"
            )

        return issues


class UnifiedTableManager:
    """Manages the unified multimodal Lance table.

    Provides typed append methods for each modality and
    efficient modality-based querying via column projection.

    Args:
        storage: LanceStorageManager instance.
    """

    def __init__(self, storage: LanceStorageManager) -> None:
        self._storage = storage

    def create(self, name: str) -> None:
        """Create a new unified table with empty schema.

        Args:
            name: Table name.

        Raises:
            StorageError: If table already exists.
        """
        # Create an empty table with the full schema
        empty = pa.table({field.name: pa.array([], type=field.type) for field in UNIFIED_SCHEMA})
        self._storage.create_dataset(name, empty)

    def append_text_rows(
        self,
        name: str,
        rows: list[dict[str, Any]],
    ) -> None:
        """Append text modality rows.

        Each row dict should have at least 'text_content'.
        Other fields (source, filename) are optional.

        Args:
            name: Table name.
            rows: List of row dicts.
        """
        now = pa.scalar(datetime.now(UTC), type=pa.timestamp("us", tz="UTC"))

        data: dict[str, list[Any]] = {field.name: [None] * len(rows) for field in UNIFIED_SCHEMA}
        # Override non-null defaults
        data["modality"] = ["text"] * len(rows)
        data["created_at"] = [now.as_py()] * len(rows)

        for i, row in enumerate(rows):
            data["id"][i] = str(uuid.uuid4())
            data["text_content"][i] = row.get("text_content")
            data["source"][i] = row.get("source")
            data["filename"][i] = row.get("filename")

        table = pa.table(data, schema=UNIFIED_SCHEMA)
        self._storage.append_dataset(name, table)

    def append_image_rows(
        self,
        name: str,
        rows: list[dict[str, Any]],
    ) -> None:
        """Append image modality rows.

        Each row dict should have at least 'image_data'.
        Optional: image_thumbnail, image_preview, image_width, image_height,
        exif_make, exif_model, exif_gps_lat, exif_gps_lon.

        Args:
            name: Table name.
            rows: List of row dicts.
        """
        now = pa.scalar(datetime.now(UTC), type=pa.timestamp("us", tz="UTC"))

        data: dict[str, list[Any]] = {field.name: [None] * len(rows) for field in UNIFIED_SCHEMA}
        data["modality"] = ["image"] * len(rows)
        data["created_at"] = [now.as_py()] * len(rows)

        for i, row in enumerate(rows):
            data["id"][i] = str(uuid.uuid4())
            data["source"][i] = row.get("source")
            data["filename"][i] = row.get("filename")
            data["image_data"][i] = row.get("image_data")
            data["image_thumbnail"][i] = row.get("image_thumbnail")
            data["image_preview"][i] = row.get("image_preview")
            data["image_width"][i] = row.get("image_width")
            data["image_height"][i] = row.get("image_height")
            data["exif_make"][i] = row.get("exif_make")
            data["exif_model"][i] = row.get("exif_model")
            data["exif_gps_lat"][i] = row.get("exif_gps_lat")
            data["exif_gps_lon"][i] = row.get("exif_gps_lon")
            data["exif_capture_time"][i] = row.get("exif_capture_time")

        table = pa.table(data, schema=UNIFIED_SCHEMA)
        self._storage.append_dataset(name, table)

    def append_video_rows(
        self,
        name: str,
        rows: list[dict[str, Any]],
    ) -> None:
        """Append video modality rows.

        Each row dict should have at least 'video_data'.
        Optional: keyframe_count, video_duration_ms.

        Args:
            name: Table name.
            rows: List of row dicts.
        """
        now = pa.scalar(datetime.now(UTC), type=pa.timestamp("us", tz="UTC"))

        data: dict[str, list[Any]] = {field.name: [None] * len(rows) for field in UNIFIED_SCHEMA}
        data["modality"] = ["video"] * len(rows)
        data["created_at"] = [now.as_py()] * len(rows)

        for i, row in enumerate(rows):
            data["id"][i] = str(uuid.uuid4())
            data["source"][i] = row.get("source")
            data["filename"][i] = row.get("filename")
            data["video_data"][i] = row.get("video_data")
            data["keyframe_count"][i] = row.get("keyframe_count")
            data["video_duration_ms"][i] = row.get("video_duration_ms")

        table = pa.table(data, schema=UNIFIED_SCHEMA)
        self._storage.append_dataset(name, table)

    def query_by_modality(
        self,
        name: str,
        modality: str,
    ) -> pa.Table:
        """Query rows by modality using column projection.

        Reads only the columns relevant to the specified modality,
        avoiding unnecessary binary data I/O.

        Args:
            name: Table name.
            modality: One of "text", "image", "video".

        Returns:
            Arrow Table with matching rows and modality-specific columns.

        Raises:
            ValueError: If modality is not recognized.
        """
        columns_map: dict[str, list[str]] = {
            "text": _TEXT_COLUMNS,
            "image": _IMAGE_COLUMNS,
            "video": _VIDEO_COLUMNS,
        }

        if modality not in columns_map:
            raise ValueError(f"Unknown modality '{modality}': must be one of {list(columns_map)}")

        columns = columns_map[modality]
        full_table = self._storage.read_dataset(name, columns=columns)

        # Filter by modality (predicated pushdown where possible)
        mask = pa.compute.equal(full_table.column("modality"), modality)
        return full_table.filter(mask)
