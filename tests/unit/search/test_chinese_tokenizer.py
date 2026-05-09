"""Tests for arrow_lake.query._chinese_tokenizer."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

pytest.importorskip("jieba")

from arrow_lake.query._chinese_tokenizer import has_cjk, segment_query, segment_text


class TestHasCjk:
    def test_chinese(self) -> None:
        assert has_cjk("机器学习") is True

    def test_english(self) -> None:
        assert has_cjk("hello world") is False

    def test_mixed(self) -> None:
        assert has_cjk("hello 机器学习") is True

    def test_empty(self) -> None:
        assert has_cjk("") is False

    def test_numbers_and_punctuation(self) -> None:
        assert has_cjk("123!@#") is False


class TestSegmentText:
    def test_no_cjk_passthrough(self) -> None:
        """English text should pass through unchanged."""
        assert segment_text("hello world") == "hello world"

    def test_empty_string(self) -> None:
        assert segment_text("") == ""

    def test_none_like_empty(self) -> None:
        assert segment_text("") == ""

    def test_chinese_segmented(self) -> None:
        """Chinese text should be segmented by jieba."""
        result = segment_text("机器学习基础教程")
        # jieba should produce space-separated tokens
        assert " " in result
        # Original characters should all be preserved
        for ch in "机器学习基础教程":
            assert ch in result

    def test_mixed_chinese_english(self) -> None:
        """Mixed text: Chinese segmented, English preserved."""
        result = segment_text("深度学习 deep learning 模型")
        assert " " in result
        assert "deep" in result
        assert "learning" in result

    def test_falls_back_without_jieba(self) -> None:
        """Without jieba, returns original text unchanged."""
        with patch("arrow_lake.query._chinese_tokenizer._JIEBA_AVAILABLE", False):
            assert segment_text("机器学习") == "机器学习"

    def test_exception_returns_original_text(self) -> None:
        """jieba.lcut() failure should return original text, not crash."""
        with patch("arrow_lake.query._chinese_tokenizer.jieba") as mock_jieba:
            mock_jieba.lcut.side_effect = RuntimeError("jieba internal error")
            result = segment_text("机器学习")
            assert result == "机器学习"


class TestSegmentQuery:
    def test_query_segmented(self) -> None:
        result = segment_query("机器学习")
        assert " " in result

    def test_english_query_passthrough(self) -> None:
        assert segment_query("hello world") == "hello world"


class TestBlobKeySanitization:
    """Verify ASCII-only blob key generation for S3/MinIO compatibility."""

    def test_chinese_filename_ascii(self) -> None:
        """Chinese characters must be replaced, not preserved."""
        stem = "平凡的世界"
        ascii_stem = stem.encode("ascii", "replace").decode("ascii")
        safe = re.sub(r"[^\w.\-]", "_", ascii_stem)
        assert safe.isascii()
        assert "?" not in safe
        assert "平" not in safe

    def test_ascii_filename_unchanged(self) -> None:
        stem = "sample_report_v2"
        ascii_stem = stem.encode("ascii", "replace").decode("ascii")
        safe = re.sub(r"[^\w.\-]", "_", ascii_stem)
        assert safe == "sample_report_v2"

    def test_mixed_filename(self) -> None:
        stem = "report_2024_v1.0_final"
        ascii_stem = stem.encode("ascii", "replace").decode("ascii")
        safe = re.sub(r"[^\w.\-]", "_", ascii_stem)
        assert safe.isascii()
