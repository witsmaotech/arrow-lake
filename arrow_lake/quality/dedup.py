"""Content deduplication — Story 4.7.

Provides SHA-256 exact-match deduplication and pHash perceptual deduplication
for multimodal data tables. Supports both "flag" (mark duplicates) and "remove"
(exclude duplicates) strategies.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import structlog

from arrow_lake.exceptions import ErrorCode, QualityError

logger = structlog.get_logger(__name__)

__all__ = ["ContentDeduplicator", "DedupResult"]

_BINARY_COLUMNS = {"image_data", "video_data", "image_thumbnail", "image_preview"}

try:
    from PIL import Image as PILImage

    try:
        import imagehash

        _HAS_IMAGEHASH = True
    except ImportError:
        _HAS_IMAGEHASH = False
except ImportError:
    _HAS_IMAGEHASH = False
    PILImage = None  # type: ignore[assignment]


@dataclass(frozen=True)
class DedupResult:
    """Result of a content deduplication pass.

    Attributes:
        total_rows: Total input rows.
        unique_rows: Rows identified as unique.
        duplicates_found: Rows identified as duplicates.
        strategy: Strategy used ("exact", "perceptual", "both").
        action: Action taken ("flag" or "remove").
        table: Result table (flag: includes is_duplicate col; remove: unique only).
    """

    total_rows: int
    unique_rows: int
    duplicates_found: int
    strategy: str
    action: str
    table: pa.Table


class ContentDeduplicator:
    """Content deduplication via SHA-256 exact match and pHash perceptual hash.

    Supports three strategies:
    - ``exact``: SHA-256 hash on binary content columns.
    - ``perceptual``: pHash on image data, with Hamming distance threshold.
    - ``both``: Exact first, then perceptual on remaining unique rows.

    Args:
        strategy: Dedup strategy ("exact", "perceptual", "both", "minhash").
        action: What to do with duplicates ("flag" keeps them with a marker,
               "remove" excludes them from the result).
        perceptual_threshold: Maximum Hamming distance for perceptual duplicates.
        text_column: Column to hash for ``minhash`` (required for that strategy).
        ngram_size: Character n-gram size for MinHash shingling.
        num_hashes: Number of MinHash permutations (``num_perm``).
        threshold: Jaccard similarity threshold (0.0–1.0) for near-duplicate.
    """

    def __init__(
        self,
        strategy: str = "exact",
        action: str = "flag",
        perceptual_threshold: int = 10,
        *,
        text_column: str | None = None,
        ngram_size: int = 5,
        num_hashes: int = 128,
        threshold: float = 0.8,
    ) -> None:
        if strategy not in ("exact", "perceptual", "both", "minhash"):
            raise ValueError(
                f"strategy must be 'exact', 'perceptual', 'both', or 'minhash', got {strategy!r}"
            )
        if strategy == "minhash" and not text_column:
            raise ValueError("strategy='minhash' requires a text_column")
        if action not in ("flag", "remove"):
            raise ValueError(f"action must be 'flag' or 'remove', got {action!r}")
        self._strategy = strategy
        self._action = action
        self._perceptual_threshold = perceptual_threshold
        self._text_column = text_column
        self._ngram_size = ngram_size
        self._num_hashes = num_hashes
        self._threshold = threshold

    @property
    def name(self) -> str:
        return f"dedup_{self._strategy}"

    def deduplicate(self, table: pa.Table) -> DedupResult:
        """Run deduplication on a single batch table.

        Args:
            table: Arrow table to deduplicate.

        Returns:
            DedupResult with outcome and processed table.
        """
        total = table.num_rows
        if total == 0:
            return DedupResult(
                total_rows=0,
                unique_rows=0,
                duplicates_found=0,
                strategy=self._strategy,
                action=self._action,
                table=table,
            )

        # MinHash LSH semantic dedup — isolated path (CPU datasketch, no GPU gate).
        if self._strategy == "minhash":
            return self._deduplicate_minhash(table)

        # Compute hashes
        sha256_col = self._compute_sha256_column(table)
        phash_col: list[int] | None = None

        if self._strategy == "exact":
            result_table = self._apply_exact_dedup(table, sha256_col)
        elif self._strategy == "perceptual":
            phash_col = self._compute_phash_column(table)
            result_table = self._apply_perceptual_dedup(table, phash_col)
        else:
            # both: exact first, then perceptual on unique
            unique_table = self._apply_exact_dedup(table, sha256_col)
            if unique_table.num_rows > 0:
                unique_phash = self._compute_phash_column(unique_table)
                result_table = self._apply_perceptual_dedup(unique_table, unique_phash)
            else:
                result_table = unique_table

        duplicates = total - result_table.num_rows

        # For "flag" action, keep duplicates but add is_duplicate column
        if self._action == "flag":
            if self._strategy in ("perceptual", "both"):
                # phash must match original table length for flag column
                if phash_col is None:
                    phash_col = self._compute_phash_column(table)
                result_table = self._add_flag_column_perceptual(table, phash_col)
            else:
                result_table = self._add_flag_column(table, sha256_col)

        unique_count = total - duplicates
        return DedupResult(
            total_rows=total,
            unique_rows=unique_count,
            duplicates_found=duplicates,
            strategy=self._strategy,
            action=self._action,
            table=result_table,
        )

    # ------------------------------------------------------------------
    # Internal: MinHash LSH (semantic / near-duplicate text dedup)
    # ------------------------------------------------------------------

    def _deduplicate_minhash(self, table: pa.Table) -> DedupResult:
        """Near-duplicate text dedup via MinHash + LSH (CPU datasketch).

        Keeps the first occurrence of each near-duplicate cluster. ``flag``
        adds an ``is_duplicate`` column; ``remove`` returns unique rows only.
        Rows whose text is missing or shorter than ``ngram_size`` are kept.
        """
        flags = self._compute_minhash_flags(table)
        total = table.num_rows
        duplicates = sum(1 for f in flags if f)
        unique_count = total - duplicates

        if self._action == "flag":
            result_table = table.append_column(
                pa.field("is_duplicate", pa.bool_(), nullable=False),
                pa.array(flags, type=pa.bool_()),
            )
        else:  # remove
            keep = [i for i, f in enumerate(flags) if not f]
            result_table = table.take(keep) if keep else table.slice(0, 0)

        return DedupResult(
            total_rows=total,
            unique_rows=unique_count,
            duplicates_found=duplicates,
            strategy="minhash",
            action=self._action,
            table=result_table,
        )

    def _compute_minhash_flags(self, table: pa.Table) -> list[bool]:
        """Return a per-row ``is_duplicate`` flag list using MinHash LSH.

        First occurrence wins: a row is flagged only if an earlier kept row
        exceeds the Jaccard ``threshold``. Missing text column → no flags.
        """
        if not self._text_column or self._text_column not in table.column_names:
            return [False] * table.num_rows

        try:
            from datasketch import MinHash, MinHashLSH
        except ImportError as exc:  # pragma: no cover - datasketch is a dep
            raise QualityError(
                error_code=ErrorCode.DEDUP_HASH_COMPUTATION_FAILED,
                message="datasketch is required for minhash dedup",
            ) from exc

        texts = table.column(self._text_column).to_pylist()
        lsh = MinHashLSH(threshold=self._threshold, num_perm=self._num_hashes)

        flags: list[bool] = []
        for text in texts:
            if not isinstance(text, str) or len(text) < self._ngram_size:
                flags.append(False)  # too short / null → keep as unique
                continue
            mh = MinHash(num_perm=self._num_hashes)
            for start in range(len(text) - self._ngram_size + 1):
                mh.update(text[start : start + self._ngram_size].encode("utf-8"))
            if lsh.query(mh):
                flags.append(True)
            else:
                lsh.insert(str(len(flags)), mh)
                flags.append(False)
        return flags

    def deduplicate_incremental(
        self,
        new_table: pa.Table,
        existing_sha256: dict[str, str] | None = None,
    ) -> tuple[DedupResult, dict[str, str]]:
        """Deduplicate new rows against previously seen hashes.

        Args:
            new_table: New batch of rows to deduplicate.
            existing_sha256: Map of sha256_hash -> first_row_id from prior batches.

        Returns:
            Tuple of (DedupResult, updated_sha256_map).
        """
        seen = dict(existing_sha256) if existing_sha256 is not None else {}
        total = new_table.num_rows
        if total == 0:
            return (
                DedupResult(
                    total_rows=0,
                    unique_rows=0,
                    duplicates_found=0,
                    strategy=self._strategy,
                    action=self._action,
                    table=new_table,
                ),
                seen,
            )

        sha256_col = self._compute_sha256_column(new_table)
        unique_indices: list[int] = []
        duplicate_count = 0

        for i in range(total):
            h = sha256_col[i]
            if h and h not in seen:
                seen[h] = (
                    str(new_table.column("id")[i].as_py())
                    if "id" in new_table.column_names
                    else str(i)
                )
                unique_indices.append(i)
            elif h:
                duplicate_count += 1
            else:
                # Null hash (empty content) — always keep
                unique_indices.append(i)

        unique_table = (unique_indices and new_table.take(unique_indices)) or new_table.slice(0, 0)

        if self._action == "flag":
            flag_col = [False] * total
            for i in range(total):
                if i not in unique_indices:
                    flag_col[i] = True
            result = new_table.append_column(
                pa.field("is_duplicate", pa.bool_(), nullable=False),
                pa.array(flag_col, type=pa.bool_()),
            )
        else:
            result = unique_table

        return (
            DedupResult(
                total_rows=total,
                unique_rows=total - duplicate_count,
                duplicates_found=duplicate_count,
                strategy=self._strategy,
                action=self._action,
                table=result,
            ),
            seen,
        )

    # ------------------------------------------------------------------
    # Internal: SHA-256
    # ------------------------------------------------------------------

    def _compute_sha256_column(self, table: pa.Table) -> list[str]:
        """Compute SHA-256 hash for each row.

        Hashes the first non-null binary column (image_data, video_data,
        image_thumbnail) or text_content string column.
        """
        hashes: list[str] = []
        col_names = table.column_names

        # Determine which column to hash
        hash_col = None
        for c in ("image_data", "image_thumbnail", "video_data", "text_content"):
            if c in col_names:
                hash_col = c
                break

        if hash_col is None:
            return [""] * table.num_rows

        col = table.column(hash_col)
        for i in range(table.num_rows):
            val = col[i].as_py()
            hashes.append(self._compute_sha256(val))

        return hashes

    @staticmethod
    def _compute_sha256(data: Any) -> str:
        """Compute SHA-256 hex digest of binary or string data."""
        if data is None:
            return ""
        if isinstance(data, str):
            data = data.encode("utf-8")
        elif isinstance(data, bytes):
            pass
        else:
            return ""
        return hashlib.sha256(data).hexdigest()

    # ------------------------------------------------------------------
    # Internal: pHash
    # ------------------------------------------------------------------

    def _compute_phash_column(self, table: pa.Table) -> list[int]:
        """Compute perceptual hash (pHash) for each image row.

        Uses Daft-native ``image_hash()`` when available for 5-10x speedup
        over per-image imagehash processing. Falls back to imagehash.

        Returns list of int hashes; rows without image data get 0.
        """
        hash_col = None
        for c in ("image_data", "image_thumbnail"):
            if c in table.column_names:
                hash_col = c
                break

        if hash_col is None:
            return [0] * table.num_rows

        # Try Daft-native batch hash computation
        try:
            return self._compute_phash_column_daft(table, hash_col)
        except Exception as exc:
            logger.debug("Daft image_hash failed, falling back to imagehash: %s", exc)

        # Fallback: per-image hash
        hashes: list[int] = []
        col = table.column(hash_col)
        for i in range(table.num_rows):
            val = col[i].as_py()
            hashes.append(self._compute_phash(val))
        return hashes

    def _compute_phash_column_daft(self, table: pa.Table, hash_col: str) -> list[int]:
        """Compute pHash using Daft-native image_hash for batch processing."""
        import daft

        col_data = table.column(hash_col).to_pylist()

        df = daft.from_pydict({"image_bytes": col_data})
        df = df.with_column(
            "image",
            daft.functions.decode_image(df["image_bytes"]),
        )
        df = df.with_column(
            "hash",
            daft.functions.image_hash(df["image"], method="phash"),
        )

        result = df.select("hash").to_pydict()
        hashes: list[int] = []
        for h in result["hash"]:
            if h is None:
                hashes.append(0)
            else:
                # Daft returns FixedSizeBinary; convert to int
                hashes.append(int.from_bytes(h, byteorder="big"))
        return hashes

    def _compute_phash(self, image_bytes: Any) -> int:
        """Compute perceptual hash of image bytes.

        Returns int hash value, or 0 if unavailable.
        """
        if image_bytes is None:
            return 0
        if not _HAS_IMAGEHASH:
            logger.warning("imagehash library not installed, perceptual dedup unavailable")
            return 0
        try:
            img = PILImage.open(io.BytesIO(image_bytes))
            return int(str(imagehash.phash(img)), 16)
        except (ValueError, OSError) as exc:
            raise QualityError(
                error_code=ErrorCode.DEDUP_HASH_COMPUTATION_FAILED,
                message=f"Failed to compute pHash: {exc}",
            ) from exc

    @staticmethod
    def _hamming_distance(h1: int, h2: int) -> int:
        """Compute Hamming distance between two integer hashes."""
        xor = h1 ^ h2
        return bin(xor).count("1")

    # ------------------------------------------------------------------
    # Internal: apply strategies
    # ------------------------------------------------------------------

    def _apply_exact_dedup(self, table: pa.Table, sha256_col: list[str]) -> pa.Table:
        """Remove exact duplicates based on SHA-256 hashes."""
        seen: set[str] = set()
        unique_indices: list[int] = []
        for _i, h in enumerate(sha256_col):
            if h and h not in seen:
                seen.add(h)
                unique_indices.append(_i)
            elif not h:
                unique_indices.append(_i)
        return (unique_indices and table.take(unique_indices)) or table.slice(0, 0)

    def _apply_perceptual_dedup(self, table: pa.Table, phash_col: list[int]) -> pa.Table:
        """Remove perceptual duplicates based on pHash Hamming distance.

        Uses a bucket-based approach: group hashes by their high bits to avoid
        O(n²) comparison. Only compares hashes within the same bucket.
        """
        threshold = self._perceptual_threshold
        # Bucket by high 16 bits — hashes that differ in high bits will always
        # have Hamming distance > threshold for reasonable thresholds (<16)
        bucket_bits = max(16, threshold + 1)
        bucket_mask = ((1 << bucket_bits) - 1) << (64 - bucket_bits)
        buckets: dict[int, list[tuple[int, int]]] = {}  # bucket_key -> [(index, hash)]
        unique_indices: list[int] = []

        for _i, h in enumerate(phash_col):
            if h == 0:
                unique_indices.append(_i)
                continue
            bucket_key = h & bucket_mask
            bucket = buckets.get(bucket_key, [])
            is_dup = any(self._hamming_distance(h, existing_h) <= threshold for _, existing_h in bucket)
            # Also check adjacent bucket for hashes near boundary
            if not is_dup and bucket_key != 0:
                adj_bucket = buckets.get(bucket_key - (1 << (64 - bucket_bits)), [])
                is_dup = any(self._hamming_distance(h, existing_h) <= threshold for _, existing_h in adj_bucket)
            if not is_dup:
                bucket.append((_i, h))
                buckets[bucket_key] = bucket
                unique_indices.append(_i)
        return (unique_indices and table.take(unique_indices)) or table.slice(0, 0)

    def _add_flag_column(
        self,
        original_table: pa.Table,
        sha256_col: list[str],
    ) -> pa.Table:
        """Add is_duplicate column to original table based on sha256 lookup."""
        flag_values: list[bool] = []
        seen: set[str] = set()
        for h in sha256_col:
            if h and h in seen:
                flag_values.append(True)
            else:
                flag_values.append(False)
                if h:
                    seen.add(h)

        return original_table.append_column(
            pa.field("is_duplicate", pa.bool_(), nullable=False),
            pa.array(flag_values, type=pa.bool_()),
        )

    def _add_flag_column_perceptual(
        self,
        original_table: pa.Table,
        phash_col: list[int],
    ) -> pa.Table:
        """Add is_duplicate column based on perceptual hash Hamming distance."""
        flag_values: list[bool] = []
        seen: list[int] = []
        threshold = self._perceptual_threshold
        for h in phash_col:
            if h == 0:
                flag_values.append(False)
                continue
            is_dup = False
            for existing_h in seen:
                if self._hamming_distance(h, existing_h) <= threshold:
                    is_dup = True
                    break
            if not is_dup:
                seen.append(h)
            flag_values.append(is_dup)

        return original_table.append_column(
            pa.field("is_duplicate", pa.bool_(), nullable=False),
            pa.array(flag_values, type=pa.bool_()),
        )
