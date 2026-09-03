"""Field comment capture — extract column comments at ingest time.

Structured datasets (Parquet / DB / CSV-with-sidecar) carry human-readable
column comments. Daft's ``read_parquet().to_arrow()`` **discards** PyArrow
field metadata, so we read comments directly from the source and re-attach
them to the Arrow table's schema as field metadata (key ``b"comment"``) before
it is written to Lance. Lance persists Arrow field metadata, so the comment
travels with the data for the dataset's lifetime — no Gravitino dependency.

All extractors are best-effort: any failure returns an empty mapping so that
comment capture never blocks ingestion.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pyarrow as pa

logger = logging.getLogger(__name__)

# Arrow field-metadata key under which column comments are stored (bytes).
COMMENT_KEY = b"comment"
# Alternate keys written by various producers.
_ALT_KEYS = (b"description", b"comment", b"COMMENT")


def _decode_comment(metadata: dict[bytes, bytes] | None) -> str:
    """Pick the first non-empty comment-like value from field metadata."""
    if not metadata:
        return ""
    for key in _ALT_KEYS:
        raw = metadata.get(key)
        if raw:
            try:
                text = raw.decode("utf-8", "replace").strip()
            except Exception:
                continue
            if text:
                return text
    return ""


def extract_parquet_comments(path: str) -> dict[str, str]:
    """Read column comments from a Parquet file's field metadata.

    Hive/Spark write column comments into the parquet field metadata under the
    ``comment`` key. We read the schema via PyArrow directly (NOT via Daft,
    which strips field metadata on ``to_arrow()``).
    """
    try:
        import pyarrow.parquet as pq

        schema = pq.ParquetFile(path).schema_arrow
    except Exception:
        logger.debug("parquet_comment_read_failed", exc_info=True)
        return {}

    out: dict[str, str] = {}
    for field in schema:
        text = _decode_comment(field.metadata)
        if text:
            out[field.name] = text
    return out


def extract_csv_sidecar_comments(path: str) -> dict[str, str]:
    """Read column comments from a CSV sidecar file.

    Looks next to the CSV for, in order:
      - ``{stem}.columns.json``  → ``{"col_name": "comment", ...}``
      - ``{stem}.meta.yaml``     → ``columns: {col_name: comment}`` (or a flat
        mapping of col→comment)
    """
    p = Path(path)
    stem = p.stem
    directory = p.parent

    # 1) {stem}.columns.json
    json_path = directory / f"{stem}.columns.json"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("csv_sidecar_json_parse_failed path=%s", json_path, exc_info=True)
            data = None
        if isinstance(data, dict):
            return {
                str(k): str(v).strip()
                for k, v in data.items()
                if str(v).strip()
            }

    # 2) {stem}.meta.yaml
    yaml_path = directory / f"{stem}.meta.yaml"
    if yaml_path.is_file():
        try:
            import yaml  # type: ignore[import-untyped]

            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("csv_sidecar_yaml_parse_failed path=%s", yaml_path, exc_info=True)
            data = None
        cols = None
        if isinstance(data, dict):
            cols = data.get("columns") if isinstance(data.get("columns"), dict) else data
        if isinstance(cols, dict):
            return {
                str(k): str(v).strip()
                for k, v in cols.items()
                if str(v).strip()
            }

    return {}


def attach_field_comments(table: pa.Table, comments: dict[str, str]) -> pa.Table:
    """Return ``table`` with ``b"comment"`` field metadata set for named fields.

    Field metadata cannot be mutated in place via ``cast``; the schema is
    rebuilt with metadata and the table re-wrapped via ``from_arrays``.
    Fields without a comment keep any existing metadata untouched.
    """
    if not comments:
        return table

    col_names = set(table.schema.names)
    relevant = {k: v for k, v in comments.items() if k in col_names}
    if not relevant:
        return table

    new_fields = []
    changed = False
    for field in table.schema:
        if field.name in relevant:
            md = dict(field.metadata or {})
            md[COMMENT_KEY] = relevant[field.name].encode("utf-8")
            new_fields.append(pa.field(field.name, field.type, nullable=field.nullable, metadata=md))
            changed = True
        else:
            new_fields.append(field)

    if not changed:
        return table

    new_schema = pa.schema(new_fields, metadata=table.schema.metadata)
    return pa.Table.from_arrays(table.columns, schema=new_schema)


def capture_for_file(source_path: str, file_type: str, table: pa.Table) -> pa.Table:
    """Capture comments for ``source_path`` and attach them to ``table``.

    Dispatches by ``file_type`` ("parquet" / "csv"). Any error is swallowed
    and the original table is returned — comment capture must never block
    ingestion.
    """
    try:
        if file_type == "parquet":
            comments = extract_parquet_comments(source_path)
        elif file_type == "csv":
            comments = extract_csv_sidecar_comments(source_path)
        else:
            return table
        return attach_field_comments(table, comments)
    except Exception:
        logger.debug("field_comment_capture_failed path=%s", source_path, exc_info=True)
        return table


__all__: list[str] = [
    "COMMENT_KEY",
    "extract_parquet_comments",
    "extract_csv_sidecar_comments",
    "attach_field_comments",
    "capture_for_file",
]
