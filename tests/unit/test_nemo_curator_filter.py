"""Tests for arrow_lake.quality.nemo_curator — Story 8.5 NeMo Curator Filter.

Tests NeMoCuratorFilter (import guard, CPU heuristic fallback, multi-classifier
scoring, composite score), HAS_NEMO flag, and QualityConfig defaults.
No GPU, no real nemo-curator.
"""

from __future__ import annotations

import pyarrow as pa
from arrow_lake.config import QualityConfig
from arrow_lake.quality.nemo_curator import (
    HAS_NEMO,
    NeMoCuratorFilter,
    _aesthetic_heuristic,
    _nsfw_heuristic,
    _text_quality_heuristic,
)

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
# TestClassifiers
# ---------------------------------------------------------------------------


class TestClassifiers:
    """Test classifier configuration."""

    def test_default_classifiers(self) -> None:
        f = NeMoCuratorFilter()
        assert f.classifiers == ("text_quality",)

    def test_custom_classifiers(self) -> None:
        f = NeMoCuratorFilter(classifiers=("text_quality", "nsfw", "aesthetic"))
        assert f.classifiers == ("text_quality", "nsfw", "aesthetic")

    def test_all_valid_classifier_names(self) -> None:
        assert NeMoCuratorFilter.CLASSIFIERS == ("text_quality", "nsfw", "aesthetic")


# ---------------------------------------------------------------------------
# TestHeuristicFunctions
# ---------------------------------------------------------------------------


class TestHeuristicFunctions:
    """Test CPU fallback heuristic functions."""

    def test_text_quality_short_text(self) -> None:
        score = _text_quality_heuristic("hello", max_chars=1000)
        assert score == min(1.0, 5 / 1000)

    def test_text_quality_long_text(self) -> None:
        score = _text_quality_heuristic("a" * 2000, max_chars=1000)
        assert score == 1.0

    def test_text_quality_none_returns_zero(self) -> None:
        assert _text_quality_heuristic(None) == 0.0

    def test_text_quality_empty_returns_zero(self) -> None:
        assert _text_quality_heuristic("") == 0.0

    def test_nsfw_clean_text(self) -> None:
        assert _nsfw_heuristic("This is a clean sentence.") == 0.0

    def test_nsfw_flagged_text(self) -> None:
        assert _nsfw_heuristic("This is explicit content") == 0.9

    def test_nsfw_none_returns_zero(self) -> None:
        assert _nsfw_heuristic(None) == 0.0

    def test_aesthetic_normal_image(self) -> None:
        score = _aesthetic_heuristic(1920, 1080)
        assert 0.0 < score < 1.0

    def test_aesthetic_none_returns_zero(self) -> None:
        assert _aesthetic_heuristic(None, 1080) == 0.0

    def test_aesthetic_zero_dimensions(self) -> None:
        assert _aesthetic_heuristic(0, 1080) == 0.0


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
# TestMultiClassifierColumns
# ---------------------------------------------------------------------------


class TestMultiClassifierColumns:
    """Test that multi-classifier filter adds score columns."""

    def test_single_classifier_adds_text_and_composite(self) -> None:
        f = NeMoCuratorFilter(
            threshold=0.0,
            classifiers=("text_quality",),
        )
        table = pa.table({"text_content": ["hello world"]})
        passed, _ = f.filter(table)
        assert "quality_text_score" in passed.column_names
        assert "quality_composite_score" in passed.column_names

    def test_all_classifiers_add_all_score_columns(self) -> None:
        f = NeMoCuratorFilter(
            threshold=0.0,
            classifiers=("text_quality", "nsfw", "aesthetic"),
        )
        table = pa.table(
            {
                "text_content": ["hello"],
                "image_width": [1920],
                "image_height": [1080],
            }
        )
        passed, _ = f.filter(table)
        assert "quality_text_score" in passed.column_names
        assert "quality_nsfw_score" in passed.column_names
        assert "quality_aesthetic_score" in passed.column_names
        assert "quality_composite_score" in passed.column_names

    def test_nsfw_classifier_not_added_by_default(self) -> None:
        f = NeMoCuratorFilter(threshold=0.0)
        table = pa.table({"text_content": ["hello"]})
        passed, _ = f.filter(table)
        assert "quality_nsfw_score" not in passed.column_names

    def test_composite_score_equals_single_classifier(self) -> None:
        f = NeMoCuratorFilter(
            threshold=0.0,
            classifiers=("text_quality",),
            text_max_chars=100,
        )
        text = "a" * 50
        table = pa.table({"text_content": [text]})
        passed, _ = f.filter(table)
        text_score = passed.column("quality_text_score")[0].as_py()
        composite_score = passed.column("quality_composite_score")[0].as_py()
        assert abs(text_score - composite_score) < 0.001


# ---------------------------------------------------------------------------
# TestCompositeScore
# ---------------------------------------------------------------------------


class TestCompositeScore:
    """Test composite score calculation with multiple classifiers."""

    def test_composite_with_two_classifiers(self) -> None:
        f = NeMoCuratorFilter(
            threshold=0.0,
            classifiers=("text_quality", "nsfw"),
            text_max_chars=100,
        )
        # Clean text with score 0.5 -> text_quality=0.5, nsfw=0.0
        # composite = (0.5 + 0.0) / 2 = 0.25
        table = pa.table({"text_content": ["a" * 50]})
        passed, _ = f.filter(table)
        composite = passed.column("quality_composite_score")[0].as_py()
        assert abs(composite - 0.25) < 0.01

    def test_composite_with_three_classifiers(self) -> None:
        f = NeMoCuratorFilter(
            threshold=0.0,
            classifiers=("text_quality", "nsfw", "aesthetic"),
            text_max_chars=100,
        )
        table = pa.table(
            {
                "text_content": ["a" * 50],
                "image_width": [1920],
                "image_height": [1080],
            }
        )
        passed, _ = f.filter(table)
        text_score = passed.column("quality_text_score")[0].as_py()
        nsfw_score = passed.column("quality_nsfw_score")[0].as_py()
        aesthetic_score = passed.column("quality_aesthetic_score")[0].as_py()
        composite = passed.column("quality_composite_score")[0].as_py()
        expected = (text_score + nsfw_score + aesthetic_score) / 3
        assert abs(composite - expected) < 0.001


# ---------------------------------------------------------------------------
# TestFilterCPUFallback
# ---------------------------------------------------------------------------


class TestFilterCPUFallback:
    """Test CPU fallback filter behavior in detail."""

    def test_short_text_rejected(self) -> None:
        f = NeMoCuratorFilter(threshold=0.5, text_max_chars=1000)
        # text_len=2 -> score = 2/1000 = 0.002 < 0.5
        table = pa.table({"text_content": ["hi"]})
        passed, rejected = f.filter(table)
        assert passed.num_rows == 0
        assert rejected.num_rows == 1

    def test_long_text_passed(self) -> None:
        f = NeMoCuratorFilter(threshold=0.3, text_max_chars=1000)
        # text_len=1000 -> score = 1.0 > 0.3
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
