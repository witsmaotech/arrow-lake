"""Arrow Lake data validation utilities — Story 1.7.

ArrowCopyDetector: detects whether Arrow data was zero-copied or
deep-copied at Lance/Daft/DuckDB boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CopyDetectionResult:
    """Result of a zero-copy check.

    Attributes:
        is_zero_copy: Whether data was zero-copied (same buffers).
        original_address: Memory address of the original buffer.
        result_address: Memory address of the result buffer.
    """

    is_zero_copy: bool
    original_address: int
    result_address: int


class ArrowCopyDetector:
    """Detects whether Arrow arrays share the same memory buffers.

    Used to verify zero-copy data transfer at Lance→Daft→DuckDB boundaries.
    """

    def check(self, original: Any, result: Any) -> CopyDetectionResult:
        """Compare two Arrow arrays for zero-copy status.

        Args:
            original: The original Arrow array or chunked array.
            result: The result Arrow array or chunked array.

        Returns:
            CopyDetectionResult with zero-copy status.
        """

        orig_buf = self._get_buffer_address(original)
        result_buf = self._get_buffer_address(result)

        return CopyDetectionResult(
            is_zero_copy=(orig_buf == result_buf and orig_buf != 0),
            original_address=orig_buf,
            result_address=result_buf,
        )

    @staticmethod
    def _get_buffer_address(arr: Any) -> int:
        """Extract the data buffer address from an Arrow array."""
        import pyarrow as pa

        if isinstance(arr, pa.ChunkedArray):
            chunks = arr.chunks
            if len(chunks) == 0:
                return 0
            arr = chunks[0]

        buffers = arr.buffers()
        if len(buffers) < 2 or buffers[1] is None:
            return 0

        addr = buffers[1].address
        return int(addr)
