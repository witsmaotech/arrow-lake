"""NeMo Curator GPU quality filter — Story 8.5.

Provides GPU-accelerated quality scoring via NVIDIA NeMo Curator
with automatic CPU fallback using lightweight heuristic scoring.

nemo-curator is an optional dependency — ImportError is handled gracefully.
"""

from __future__ import annotations

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

__all__ = ["HAS_NEMO", "NeMoCuratorFilter"]


class NeMoCuratorFilter:
    """GPU-accelerated quality scoring via NeMo Curator.

    Falls back to lightweight CPU heuristic when:
    - nemo-curator is not installed (HAS_NEMO = False)
    - GPU is not available
    - Model loading fails

    Args:
        model: NeMo Curator model name.
        threshold: Quality score threshold (0.0-1.0).
        batch_size: Batch size for GPU inference.
        use_gpu: Whether to attempt GPU inference.
        text_max_chars: Normalization ceiling for text length heuristic.
        image_max_pixels: Normalization ceiling for image pixels heuristic.
    """

    def __init__(
        self,
        model: str = "nemo/quality-scorer",
        threshold: float = 0.5,
        batch_size: int = 64,
        use_gpu: bool = True,
        text_max_chars: int = 1000,
        image_max_pixels: int = 16777216,  # 4096 * 4096
    ) -> None:
        self._model_name = model
        self._threshold = threshold
        self._batch_size = batch_size
        self._use_gpu = use_gpu
        self._text_max_chars = text_max_chars
        self._image_max_pixels = image_max_pixels
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

    def _cpu_heuristic_score(
        self, text_len: int | None, img_w: int | None, img_h: int | None
    ) -> float:
        """Lightweight CPU fallback heuristic.

        Combines text length and image resolution normalization:
        score = 0.5 * min(1.0, text_len / text_max_chars)
              + 0.5 * min(1.0, pixels / image_max_pixels)
        """
        score = 0.0
        if text_len is not None and text_len > 0:
            score += 0.5 * min(1.0, text_len / max(1, self._text_max_chars))
        if img_w is not None and img_h is not None and img_w > 0 and img_h > 0:
            pixels = img_w * img_h
            score += 0.5 * min(1.0, pixels / max(1, self._image_max_pixels))
        return score

    def filter(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        """Apply quality scoring to the table.

        Args:
            table: Arrow Table to filter.

        Returns:
            Tuple of (passed, rejected) Arrow Tables.
            Rejected rows include _rejection_reason column.
        """
        if table.num_rows == 0:
            return table, table.slice(0, 0)

        self._load_model()

        if self._using_fallback:
            return self._filter_cpu_fallback(table)

        return self._filter_gpu(table)

    def _filter_cpu_fallback(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        """Filter using CPU heuristic scoring."""
        passed_mask: list[bool] = []
        reasons: list[str] = []

        has_text = "text_content" in table.column_names
        has_img_w = "image_width" in table.column_names
        has_img_h = "image_height" in table.column_names

        text_col = table.column("text_content").to_pylist() if has_text else [None] * table.num_rows
        img_w_col = (
            table.column("image_width").to_pylist() if has_img_w else [None] * table.num_rows
        )
        img_h_col = (
            table.column("image_height").to_pylist() if has_img_h else [None] * table.num_rows
        )

        for i in range(table.num_rows):
            text_val = text_col[i]
            text_len = len(text_val) if isinstance(text_val, str) else None
            img_w = img_w_col[i]
            img_h = img_h_col[i]

            score = self._cpu_heuristic_score(text_len, img_w, img_h)
            if score >= self._threshold:
                passed_mask.append(True)
                reasons.append("")
            else:
                passed_mask.append(False)
                reasons.append(f"quality_score={score:.3f} below threshold={self._threshold}")

        return self._split_table(table, passed_mask, reasons)

    def _filter_gpu(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        """Filter using NeMo Curator GPU inference."""
        passed_mask: list[bool] = []
        reasons: list[str] = []

        has_text = "text_content" in table.column_names
        if not has_text:
            return table, table.slice(0, 0)

        texts = table.column("text_content").to_pylist()

        # Batch inference
        scores = self._run_inference(texts)

        for score in scores:
            if score >= self._threshold:
                passed_mask.append(True)
                reasons.append("")
            else:
                passed_mask.append(False)
                reasons.append(f"quality_score={score:.3f} below threshold={self._threshold}")

        return self._split_table(table, passed_mask, reasons)

    def _run_inference(self, texts: list[str | None]) -> list[float]:
        """Run batch inference on texts."""
        # NeMo Curator ScoreFilter-style inference
        batch_texts: list[str] = [t for t in texts if isinstance(t, str)]
        scores: dict[int, float] = {}

        try:
            for i in range(0, len(batch_texts), self._batch_size):
                batch = batch_texts[i : i + self._batch_size]
                # Use NeMo Curator's quality classifier
                result = self._model.score(batch)
                for j, score in enumerate(result):
                    scores[i + j] = float(score)
        except Exception:
            # Fallback to heuristic if inference fails
            logger.warning("nemo_curator_inference_failed", message="Falling back to CPU heuristic")
            self._using_fallback = True
            return [
                self._cpu_heuristic_score(len(t) if isinstance(t, str) else None, None, None)
                for t in texts
            ]

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
