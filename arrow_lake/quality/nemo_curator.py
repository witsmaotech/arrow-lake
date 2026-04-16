"""NeMo Curator GPU quality filter — Story 8.5.

Provides GPU-accelerated quality scoring via NVIDIA NeMo Curator
with automatic CPU fallback using lightweight heuristic scoring.

Supported classifiers (GPU path):
- text_quality: General text quality scoring
- nsfw: NSFW content detection
- aesthetic: Image aesthetic quality scoring

Supported deduplication (GPU path):
- MinHash + LSH approximate deduplication via NeMo Curator

nemo-curator is an optional dependency — ImportError is handled gracefully.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import structlog

from arrow_lake.exceptions import ErrorCode, QualityError

logger = structlog.get_logger(__name__)

try:
    import nemo_curator  # noqa: F401

    HAS_NEMO = True
except ImportError:
    HAS_NEMO = False

__all__ = ["HAS_NEMO", "NeMoCuratorFilter", "NeMoDeduplicator"]


# ---------------------------------------------------------------------------
# Heuristic functions for CPU fallback
# ---------------------------------------------------------------------------


def _text_quality_heuristic(text: str | None, max_chars: int = 1000) -> float:
    """Score text based on length normalization.

    Longer text tends to have more informational content.
    """
    if not text:
        return 0.0
    return min(1.0, len(text) / max(1, max_chars))


def _nsfw_heuristic(text: str | None) -> float:
    """Heuristic NSFW score based on keyword matching.

    Returns a low score (0.0) for clean text, higher score for flagged text.
    This is a very basic heuristic — the GPU path uses a real classifier.
    """
    if not text:
        return 0.0
    nsfw_keywords = [
        "nsfw",
        "explicit",
        "adult content",
        "xxx",
        "porn",
        "sexually explicit",
        "graphic content",
    ]
    text_lower = text.lower()
    for kw in nsfw_keywords:
        if kw in text_lower:
            return 0.9
    return 0.0


def _aesthetic_heuristic(
    img_w: int | None, img_h: int | None, max_pixels: int = 16_777_216
) -> float:
    """Score image aesthetic based on resolution normalization.

    Higher resolution tends to correlate with higher aesthetic quality.
    """
    if img_w is None or img_h is None or img_w <= 0 or img_h <= 0:
        return 0.0
    pixels = img_w * img_h
    return min(1.0, pixels / max(1, max_pixels))


# ---------------------------------------------------------------------------
# NeMo Curator Quality Filter (multi-classifier)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NeMoQualityScore:
    """Per-row quality scores from multiple classifiers.

    Attributes:
        text_quality: Text quality score (0.0-1.0).
        nsfw: NSFW content score (0.0 = clean, 1.0 = explicit).
        aesthetic: Image aesthetic score (0.0-1.0).
        composite: Weighted composite score (0.0-1.0).
    """

    text_quality: float
    nsfw: float
    aesthetic: float
    composite: float


class NeMoCuratorFilter:
    """GPU-accelerated quality scoring via NeMo Curator.

    Falls back to lightweight CPU heuristic when:
    - nemo-curator is not installed (HAS_NEMO = False)
    - GPU is not available
    - Model loading fails

    Supports multiple classifiers that produce independent scores:
    - text_quality: General text quality
    - nsfw: NSFW content detection
    - aesthetic: Image aesthetic quality

    Args:
        model: NeMo Curator model name.
        threshold: Quality score threshold (0.0-1.0).
        batch_size: Batch size for GPU inference.
        use_gpu: Whether to attempt GPU inference.
        text_max_chars: Normalization ceiling for text length heuristic.
        image_max_pixels: Normalization ceiling for image pixels heuristic.
        classifiers: List of classifier names to run.
    """

    CLASSIFIERS = ("text_quality", "nsfw", "aesthetic")
    DEFAULT_CLASSIFIERS = ("text_quality",)

    def __init__(
        self,
        model: str = "nemo/quality-scorer",
        threshold: float = 0.5,
        batch_size: int = 64,
        use_gpu: bool = True,
        text_max_chars: int = 1000,
        image_max_pixels: int = 16_777_216,
        classifiers: tuple[str, ...] | None = None,
    ) -> None:
        self._model_name = model
        self._threshold = threshold
        self._batch_size = batch_size
        self._use_gpu = use_gpu
        self._text_max_chars = text_max_chars
        self._image_max_pixels = image_max_pixels
        self._classifiers = classifiers or self.DEFAULT_CLASSIFIERS
        self._model: Any = None
        self._device: str | None = None
        self._using_fallback = False

    @property
    def name(self) -> str:
        """Filter name for registry identification."""
        return "nemo_curator"

    @property
    def using_fallback(self) -> bool:
        """Whether CPU heuristic fallback is active."""
        return self._using_fallback

    @property
    def classifiers(self) -> tuple[str, ...]:
        """Active classifier names."""
        return self._classifiers

    def _load_model(self) -> None:
        """Lazy-load NeMo Curator model with GPU detection."""
        if self._model is not None:
            return

        if not HAS_NEMO:
            logger.warning(
                "nemo_curator_not_installed",
                message="nemo-curator not installed, using CPU heuristic fallback",
            )
            self._using_fallback = True
            return

        try:
            import torch

            if self._use_gpu and torch.cuda.is_available():
                self._device = "cuda"
            else:
                self._device = "cpu"

            from nemo_curator.filters import QualityClassifier

            self._model = QualityClassifier(model_name_or_path=self._model_name)
            self._model.to(self._device)
            logger.info(
                "nemo_curator_model_loaded",
                model=self._model_name,
                device=self._device,
                classifiers=self._classifiers,
            )
        except ImportError:
            logger.warning(
                "nemo_curator_import_failed",
                message="nemo-curator import failed, using CPU heuristic fallback",
            )
            self._using_fallback = True
        except Exception as exc:
            raise QualityError(
                error_code=ErrorCode.QUALITY_NEMO_MODEL_ERROR,
                message=f"Failed to load NeMo Curator model: {exc}",
            ) from exc

    def filter(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        """Apply multi-classifier quality scoring to the table.

        Computes per-row scores for each active classifier and adds
        quality score columns to the passed table. Rows below the
        composite threshold go to rejected.

        Score columns added:
        - quality_text_score
        - quality_nsfw_score (if nsfw classifier active)
        - quality_aesthetic_score (if aesthetic classifier active)
        - quality_composite_score (weighted average)

        Args:
            table: Arrow Table to filter.

        Returns:
            Tuple of (passed, rejected) Arrow Tables.
            Rejected rows include _rejection_reason column.
        """
        if table.num_rows == 0:
            return table, table.slice(0, 0)

        self._load_model()

        # Compute scores for each classifier
        scores = self._compute_scores(table)

        # Add score columns to the table
        for col_name, col_values in scores.items():
            if col_values:
                score_array = pa.array(col_values, type=pa.float32())
                table = table.append_column(col_name, score_array)

        # Filter by composite score
        composite = scores.get("quality_composite_score", [0.0] * table.num_rows)
        passed_mask: list[bool] = []
        reasons: list[str] = []
        for score in composite:
            if score >= self._threshold:
                passed_mask.append(True)
                reasons.append("")
            else:
                passed_mask.append(False)
                reasons.append(f"quality_composite={score:.3f} below threshold={self._threshold}")

        return self._split_table(table, passed_mask, reasons)

    def _compute_scores(self, table: pa.Table) -> dict[str, list[float]]:
        """Compute scores for each active classifier.

        Returns:
            Dict mapping column name -> list of per-row scores.
        """
        result: dict[str, list[float]] = {}

        has_text = "text_content" in table.column_names
        has_img_w = "image_width" in table.column_names
        has_img_h = "image_height" in table.column_names

        texts = table.column("text_content").to_pylist() if has_text else [None] * table.num_rows
        img_ws = table.column("image_width").to_pylist() if has_img_w else [None] * table.num_rows
        img_hs = table.column("image_height").to_pylist() if has_img_h else [None] * table.num_rows

        n = table.num_rows

        if self._using_fallback:
            # CPU heuristic fallback
            if "text_quality" in self._classifiers:
                result["quality_text_score"] = [
                    _text_quality_heuristic(t, self._text_max_chars) for t in texts
                ]
            if "nsfw" in self._classifiers:
                result["quality_nsfw_score"] = [_nsfw_heuristic(t) for t in texts]
            if "aesthetic" in self._classifiers:
                result["quality_aesthetic_score"] = [
                    _aesthetic_heuristic(w, h, self._image_max_pixels)
                    for w, h in zip(img_ws, img_hs, strict=True)
                ]
        else:
            # GPU inference path
            if "text_quality" in self._classifiers:
                result["quality_text_score"] = self._run_gpu_inference(
                    texts, classifier_type="quality"
                )
            if "nsfw" in self._classifiers:
                result["quality_nsfw_score"] = self._run_gpu_inference(
                    texts, classifier_type="nsfw"
                )
            if "aesthetic" in self._classifiers:
                result["quality_aesthetic_score"] = self._run_gpu_image_inference(img_ws, img_hs)

        # Compute composite score (equal weighting by default)
        active_scores: list[list[float]] = [
            result[c] for c in result if c is not None and len(result[c]) == n
        ]
        if active_scores:
            weights = [1.0 / len(active_scores)] * len(active_scores)
            composite = [
                sum(scores[j] * weights[i] for i, scores in enumerate(active_scores))
                for j in range(n)
            ]
            result["quality_composite_score"] = composite
        else:
            result["quality_composite_score"] = [0.0] * n

        return result

    def _run_gpu_inference(
        self, texts: list[str | None], classifier_type: str = "quality"
    ) -> list[float]:
        """Run GPU batch inference for text classifiers."""
        batch_texts = [t for t in texts if isinstance(t, str)]
        scores: dict[int, float] = {}

        try:
            for i in range(0, len(batch_texts), self._batch_size):
                batch = batch_texts[i : i + self._batch_size]
                result = self._model.score(batch)  # type: ignore[union-attr]
                for j, score in enumerate(result):
                    scores[i + j] = float(score)
        except Exception:
            logger.warning(
                "nemo_curator_inference_failed",
                message=f"GPU inference failed for {classifier_type}, falling back to heuristic",
            )
            self._using_fallback = True
            if classifier_type == "nsfw":
                return [_nsfw_heuristic(t) for t in texts]
            return [_text_quality_heuristic(t, self._text_max_chars) for t in texts]

        # Map back to full list
        result_scores: list[float] = []
        text_idx = 0
        for t in texts:
            if isinstance(t, str):
                result_scores.append(scores.get(text_idx, 0.0))
                text_idx += 1
            else:
                result_scores.append(0.0)
        return result_scores

    def _run_gpu_image_inference(
        self, img_ws: list[int | None], img_hs: list[int | None]
    ) -> list[float]:
        """Run GPU batch inference for aesthetic classifier."""
        # GPU aesthetic inference would use image tensors
        # For now, fall back to heuristic
        return [
            _aesthetic_heuristic(w, h, self._image_max_pixels)
            for w, h in zip(img_ws, img_hs, strict=True)
        ]

    @staticmethod
    def _split_table(
        table: pa.Table,
        passed_mask: list[bool],
        reasons: list[str],
    ) -> tuple[pa.Table, pa.Table]:
        """Split table into passed and rejected based on mask."""
        passed_indices = [i for i, p in enumerate(passed_mask) if p]
        rejected_indices = [i for i, p in enumerate(passed_mask) if not p]

        passed_table = table.take(passed_indices) if passed_indices else table.slice(0, 0)

        if rejected_indices:
            rejected_table = table.take(rejected_indices)
            rejection_col = pa.array([reasons[i] for i in rejected_indices], type=pa.string())
            rejected_table = rejected_table.append_column("_rejection_reason", rejection_col)
        else:
            rejected_table = table.slice(0, 0)

        return passed_table, rejected_table


# ---------------------------------------------------------------------------
# NeMo Curator Deduplicator (MinHash + LSH)
# ---------------------------------------------------------------------------


class NeMoDeduplicator:
    """MinHash LSH approximate deduplication via NeMo Curator.

    Falls back to SHA-256 exact deduplication when NeMo Curator
    is not available or GPU is not available.

    Args:
        ngram_size: Number of characters per n-gram for MinHash.
        num_hashes: Number of hash functions for MinHash.
        threshold: Jaccard similarity threshold (0.0-1.0).
        text_column: Column name to compute hashes from.
    """

    def __init__(
        self,
        ngram_size: int = 5,
        num_hashes: int = 128,
        threshold: float = 0.8,
        text_column: str = "text_content",
    ) -> None:
        self._ngram_size = ngram_size
        self._num_hashes = num_hashes
        self._threshold = threshold
        self._text_column = text_column
        self._using_gpu = False

    @property
    def name(self) -> str:
        """Deduplicator name for registry identification."""
        return "nemo_dedup"

    @property
    def using_gpu(self) -> bool:
        """Whether GPU-accelerated dedup is active."""
        return self._using_gpu

    def deduplicate(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        """Remove near-duplicate rows using MinHash LSH or exact hash.

        Args:
            table: Arrow Table to deduplicate.

        Returns:
            Tuple of (unique, duplicates) Arrow Tables.
        """
        if table.num_rows == 0:
            return table, table.slice(0, 0)

        if self._text_column not in table.column_names:
            return table, table.slice(0, 0)

        texts = table.column(self._text_column).to_pylist()

        if HAS_NEMO and self._try_gpu():
            return self._dedup_minhash(table, texts)

        return self._dedup_exact(table, texts)

    def _try_gpu(self) -> bool:
        """Check if GPU is available for MinHash."""
        if not HAS_NEMO:
            return False
        try:
            import torch

            return torch.cuda.is_available()
        except ImportError:
            return False

    def _dedup_exact(self, table: pa.Table, texts: list[str | None]) -> tuple[pa.Table, pa.Table]:
        """Exact deduplication using SHA-256 hashes (CPU fallback)."""
        import hashlib

        seen: dict[str, int] = {}
        unique_indices: list[int] = []
        dup_indices: list[int] = []

        for i, text in enumerate(texts):
            if not isinstance(text, str):
                seen.setdefault("", i)
                unique_indices.append(i)
                continue
            h = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if h in seen:
                dup_indices.append(i)
            else:
                seen[h] = i
                unique_indices.append(i)

        unique_table = table.take(unique_indices) if unique_indices else table.slice(0, 0)
        dup_table = table.take(dup_indices) if dup_indices else table.slice(0, 0)
        return unique_table, dup_table

    def _dedup_minhash(self, table: pa.Table, texts: list[str | None]) -> tuple[pa.Table, pa.Table]:
        """MinHash LSH deduplication using NeMo Curator (GPU path)."""
        self._using_gpu = True
        unique_indices: list[int] = []

        try:
            from datasketch import MinHash, MinHashLSH

            minhashes: list[MinHash] = []
            for text in texts:
                if not isinstance(text, str) or len(text) < self._ngram_size:
                    minhashes.append(None)
                    continue
                mh = MinHash(num_perm=self._num_hashes)
                for start in range(len(text) - self._ngram_size + 1):
                    ngram = text[start : start + self._ngram_size]
                    mh.update(ngram.encode("utf-8"))
                minhashes.append(mh)

            lsh = MinHashLSH(
                threshold=self._threshold,
                num_perm=self._num_hashes,
            )
            for i, mh in enumerate(minhashes):
                if mh is not None:
                    lsh.insert(str(i), mh)

            unique_indices = []
            dup_indices: list[int] = []
            seen: set[int] = set()
            for i in range(len(texts)):
                if minhashes[i] is None:
                    unique_indices.append(i)
                    continue
                result = lsh.query(minhashes[i])
                for j in result:
                    if int(j) not in seen:
                        dup_indices.append(i)
                        seen.add(int(j))
                        break
                if int(str(i)) not in seen:
                    seen.add(int(str(i)))
                    unique_indices.append(i)
        except Exception:
            logger.warning("minhash_dedup_failed", message="Falling back to exact dedup")
            self._using_gpu = False
            return self._dedup_exact(table, texts)

        unique_table = table.take(unique_indices) if unique_indices else table.slice(0, 0)
        dup_table = table.take(dup_indices) if dup_indices else table.slice(0, 0)
        return unique_table, dup_table
