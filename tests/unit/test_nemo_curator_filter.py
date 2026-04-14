"""Tests for arrow_lake.quality.nemo_curator — Story 8.5 NeMo Curator Filter.

Tests NeMoCuratorFilter (import guard, CPU heuristic fallback, filter),
HAS_NEMO flag, and QualityConfig defaults. No GPU, no real nemo-curator.
"""

from __future__ import annotations

import pyarrow as pa
from arrow_lake.config import QualityConfig
from arrow_lake.quality.nemo_curator import HAS_NEMO, NeMoCuratorFilter

# ---------------------------------------------------------------------------
# TestImportGuard
# ---------------------------------------------------------------------------


class TestImportGuard:
    """Test HAS_NEMO flag in CI environment."""

    def test_has_nemo_is_false(self) -> None:
        # CI doesn't have nemo-curator installed
        assert HAS_NEMO is False


# ---------------------------------------------------------------------------
# TestName
# ---------------------------------------------------------------------------


class TestName:
    """Test NeMoCuratorFilter.name property."""

    def test_name(self) -> None:
        f = NeMoCuratorFilter()
        assert f.name == "nemo_curator"


# ---------------------------------------------------------------------------
# TestUsingFallback
# ---------------------------------------------------------------------------


class TestUsingFallback:
    """Test using_fallback property."""

    def test_initially_false(self) -> None:
        f = NeMoCuratorFilter()
        assert f.using_fallback is False

    def test_becomes_true_after_load_model(self) -> None:
        f = NeMoCuratorFilter()
        f._load_model()
        assert f.using_fallback is True


# ---------------------------------------------------------------------------
# TestCPUHeuristicScore
# ---------------------------------------------------------------------------


class TestCPUHeuristicScore:
    """Test _cpu_heuristic_score CPU fallback heuristic."""

    def test_text_only(self) -> None:
        f = NeMoCuratorFilter(text_max_chars=1000, image_max_pixels=16777216)
        score = f._cpu_heuristic_score(text_len=500, img_w=None, img_h=None)
        # score = 0.5 * min(1.0, 500/1000) = 0.5 * 0.5 = 0.25
        assert abs(score - 0.25) < 0.01

    def test_image_only(self) -> None:
        f = NeMoCuratorFilter(text_max_chars=1000, image_max_pixels=16777216)
        score = f._cpu_heuristic_score(text_len=None, img_w=1920, img_h=1080)
        # score = 0.5 * min(1.0, 2073600/16777216) ~ 0.5 * 0.1236 ~ 0.0618
        expected = 0.5 * (1920 * 1080) / 16777216
        assert abs(score - expected) < 0.01

    def test_both_text_and_image(self) -> None:
        f = NeMoCuratorFilter(text_max_chars=1000, image_max_pixels=16777216)
        score = f._cpu_heuristic_score(text_len=1000, img_w=1920, img_h=1080)
        # score = 0.5 * 1.0 + 0.5 * (1920*1080)/16777216
        expected = 0.5 * 1.0 + 0.5 * (1920 * 1080) / 16777216
        assert abs(score - expected) < 0.01

    def test_zero_text_and_image_returns_zero(self) -> None:
        f = NeMoCuratorFilter(text_max_chars=1000, image_max_pixels=16777216)
        score = f._cpu_heuristic_score(text_len=0, img_w=None, img_h=None)
        assert score == 0.0


# ---------------------------------------------------------------------------
# TestFilter
# ---------------------------------------------------------------------------


class TestFilter:
    """Test NeMoCuratorFilter.filter with CPU fallback."""

    def test_empty_table_returns_empty_tuple(self) -> None:
        f = NeMoCuratorFilter()
        table = pa.table({"text_content": []})
        passed, rejected = f.filter(table)
        assert passed.num_rows == 0
        assert rejected.num_rows == 0

    def test_calls_load_model_on_first_use(self) -> None:
        f = NeMoCuratorFilter()
        assert f._model is None
        f._load_model()
        # With HAS_NEMO=False, _model stays None but _using_fallback is True
        assert f.using_fallback is True

    def test_returns_passed_rejected_tuple(self) -> None:
        f = NeMoCuratorFilter(threshold=0.5, text_max_chars=100)
        table = pa.table(
            {
                "text_content": ["short", "a" * 200],
            }
        )
        passed, rejected = f.filter(table)
        total = passed.num_rows + rejected.num_rows
        assert total == 2


# ---------------------------------------------------------------------------
# TestFilterCPUFallback
# ---------------------------------------------------------------------------


class TestFilterCPUFallback:
    """Test CPU fallback filter behavior in detail."""

    def test_short_text_rejected(self) -> None:
        f = NeMoCuratorFilter(threshold=0.5, text_max_chars=1000)
        # text_len=10 -> score = 0.5 * 10/1000 = 0.005 < 0.5
        table = pa.table({"text_content": ["hi"]})
        passed, rejected = f.filter(table)
        assert passed.num_rows == 0
        assert rejected.num_rows == 1

    def test_long_text_passed(self) -> None:
        f = NeMoCuratorFilter(threshold=0.3, text_max_chars=1000)
        # text_len=1000 -> score = 0.5 * 1.0 = 0.5 > 0.3
        table = pa.table({"text_content": ["a" * 1000]})
        passed, rejected = f.filter(table)
        assert passed.num_rows == 1
        assert rejected.num_rows == 0

    def test_rejected_has_rejection_reason(self) -> None:
        f = NeMoCuratorFilter(threshold=0.5, text_max_chars=1000)
        table = pa.table({"text_content": ["hi"]})
        _, rejected = f.filter(table)
        assert "_rejection_reason" in rejected.column_names
        reason = rejected.column("_rejection_reason")[0].as_py()
        assert "below threshold" in reason


# ---------------------------------------------------------------------------
# TestConfig
# ---------------------------------------------------------------------------


class TestConfig:
    """Test QualityConfig NeMo Curator defaults."""

    def test_defaults(self) -> None:
        cfg = QualityConfig()
        assert cfg.nemo_curator_enabled is False
        assert cfg.nemo_curator_model == "nemo/quality-scorer"
        assert cfg.nemo_curator_threshold == 0.5
        assert cfg.nemo_curator_batch_size == 64
