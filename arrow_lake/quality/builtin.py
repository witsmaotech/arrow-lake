"""Built-in quality filters (Story 4.9).

TextLengthFilter: rejects rows whose text_content is too short or too long.
ImageResolutionFilter: rejects rows whose image dimensions are too small.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc
import structlog

logger = structlog.get_logger(__name__)

_TEXT_COLUMN = "text_content"
_IMAGE_WIDTH_COLUMN = "image_width"
_IMAGE_HEIGHT_COLUMN = "image_height"
_REJECTION_REASON_COL = "_rejection_reason"


def _split_table(
    table: pa.Table,
    mask: pa.BooleanArray,
    reason: str,
) -> tuple[pa.Table, pa.Table]:
    """Split a table using a boolean mask into (passed, rejected).

    Rows where mask is True are *passed*; rows where mask is False are
    *rejected*.  The rejected table gains a ``_rejection_reason`` column.

    Args:
        table: Source table.
        mask: Boolean array with one entry per row.
        reason: Human-readable reason string for rejected rows.

    Returns:
        (passed_table, rejected_table_with_reason)
    """
    if table.num_rows == 0:
        return table, table.slice(0, 0)

    passed = table.filter(mask)
    rejected_mask = pc.invert(mask)
    rejected = table.filter(rejected_mask)

    if rejected.num_rows > 0:
        reasons = pa.array([reason] * rejected.num_rows, type=pa.string())
        rejected = rejected.append_column(_REJECTION_REASON_COL, reasons)

    return passed, rejected


class TextLengthFilter:
    """Filter rows by text content length.

    Rejects rows whose ``text_content`` column value has fewer than
    *min_chars* or more than *max_chars* UTF-8 characters.  NULL text
    always passes.  If the column is missing the filter is a no-op.
    """

    def __init__(
        self,
        min_chars: int = 1,
        max_chars: int | None = None,
    ) -> None:
        self._min_chars = min_chars
        self._max_chars = max_chars

    @property
    def name(self) -> str:
        return "text_length"

    def filter(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        if table.num_rows == 0:
            return table, table.slice(0, 0)

        if _TEXT_COLUMN not in table.column_names:
            logger.debug("text_length_filter_noop", reason="column_missing")
            return table, table.slice(0, 0)

        col = table.column(_TEXT_COLUMN)
        lengths = pc.utf8_length(col)
        null_mask = pc.is_null(col)

        mask = pc.greater_equal(lengths, self._min_chars)

        if self._max_chars is not None:
            mask = pc.and_(mask, pc.less_equal(lengths, self._max_chars))

        # NULLs always pass
        mask = pc.if_else(null_mask, pa.scalar(True, type=pa.bool_()), mask)

        reason_parts = []
        if self._min_chars > 0:
            reason_parts.append(f"min_chars={self._min_chars}")
        if self._max_chars is not None:
            reason_parts.append(f"max_chars={self._max_chars}")
        reason = f"text_length({', '.join(reason_parts)})"

        passed, rejected = _split_table(table, mask, reason)
        logger.debug(
            "text_length_filter_result",
            passed=passed.num_rows,
            rejected=rejected.num_rows,
        )
        return passed, rejected


class ImageResolutionFilter:
    """Filter rows by image resolution.

    Rejects rows whose ``image_width`` or ``image_height`` is below the
    configured minimum.  NULL dimensions always pass.  If either column
    is missing the filter is a no-op.
    """

    def __init__(
        self,
        min_width: int = 64,
        min_height: int = 64,
    ) -> None:
        self._min_width = min_width
        self._min_height = min_height

    @property
    def name(self) -> str:
        return "image_resolution"

    def filter(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        if table.num_rows == 0:
            return table, table.slice(0, 0)

        has_width = _IMAGE_WIDTH_COLUMN in table.column_names
        has_height = _IMAGE_HEIGHT_COLUMN in table.column_names

        if not has_width or not has_height:
            missing = []
            if not has_width:
                missing.append(_IMAGE_WIDTH_COLUMN)
            if not has_height:
                missing.append(_IMAGE_HEIGHT_COLUMN)
            logger.debug(
                "image_resolution_filter_noop",
                reason="columns_missing",
                missing=missing,
            )
            return table, table.slice(0, 0)

        width_col = table.column(_IMAGE_WIDTH_COLUMN)
        height_col = table.column(_IMAGE_HEIGHT_COLUMN)

        width_ok = pc.greater_equal(width_col, self._min_width)
        height_ok = pc.greater_equal(height_col, self._min_height)

        # NULLs in either dimension always pass
        width_null = pc.is_null(width_col)
        height_null = pc.is_null(height_col)
        width_ok = pc.if_else(width_null, pa.scalar(True, type=pa.bool_()), width_ok)
        height_ok = pc.if_else(height_null, pa.scalar(True, type=pa.bool_()), height_ok)

        mask = pc.and_(width_ok, height_ok)

        reason = f"image_resolution(min_width={self._min_width}, min_height={self._min_height})"
        passed, rejected = _split_table(table, mask, reason)
        logger.debug(
            "image_resolution_filter_result",
            passed=passed.num_rows,
            rejected=rejected.num_rows,
        )
        return passed, rejected
