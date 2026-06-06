"""Common Pydantic models shared across all API endpoints."""

from __future__ import annotations

import base64
import re
from typing import Any, Literal

import pyarrow as pa
import pyarrow.ipc
from pydantic import BaseModel

# Dataset name pattern: alphanumeric, underscore, hyphen; 1-128 chars
_NAME_PATTERN = r"^[a-zA-Z0-9_-]{1,128}$"

# Dangerous SQL statement prefixes (only at statement start)
_BLOCKED_SQL_PREFIXES = re.compile(
    r"^\s*(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|GRANT|REVOKE|TRUNCATE|EXEC|EXECUTE|COMMENT|RENAME|MERGE)\b",
    re.IGNORECASE | re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    """Standard error payload returned when an ArrowLakeError is raised."""

    success: bool = False
    error: str
    message: str
    context: dict[str, Any] | None = None


class MessageResponse(BaseModel):
    """Simple one-message response (e.g. delete confirmation)."""

    success: bool = True
    message: str


# ---------------------------------------------------------------------------
# Arrow IPC serialization helpers
# ---------------------------------------------------------------------------

def arrow_table_to_ipc_base64(table: pa.Table) -> str:
    """Serialize a PyArrow Table to base64-encoded Arrow IPC Stream."""
    sink = pa.BufferOutputStream()
    writer = pa.ipc.new_stream(sink, table.schema)
    writer.write_table(table)
    writer.close()
    return base64.b64encode(sink.getvalue().to_pybytes()).decode()


def arrow_table_to_response(
    table: pa.Table,
    fmt: Literal["arrow_ipc", "json"],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a PyArrow Table to a JSON-serializable response dict.

    Args:
        table: The result table.
        fmt: Serialization format. "arrow_ipc" uses base64-encoded IPC stream.
        meta: Optional metadata dict (distances, facets, etc.).

    Returns:
        Dict suitable for inclusion in a Pydantic response model.
    """
    base: dict[str, Any] = {
        "format": fmt,
        "row_count": table.num_rows,
        "column_count": table.num_columns,
    }
    if meta:
        base["meta"] = meta
    if fmt == "arrow_ipc":
        base["data"] = arrow_table_to_ipc_base64(table)
    else:
        base["rows"] = table.to_pylist()
    return base
