"""Coverage for arrow_lake.ingest.transforms — AI ops, deduplicate, dtype resolution, filter ops."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import daft
import pytest

from arrow_lake.ingest.transforms import (
    _build_add_constant,
    _build_cast,
    _build_classify_image,
    _build_classify_text,
    _build_deduplicate,
    _build_filter,
    _build_llm_generate,
    _build_prompt,
    _build_rename,
    _build_select,
    _resolve_dtype,
    apply_transforms,
    build_transforms,
)


# ---------------------------------------------------------------------------
# build_transforms — classification / AI ops construction
# ---------------------------------------------------------------------------


class TestClassifyTextBuild:
    def test_missing_column_raises(self) -> None:
        with pytest.raises(ValueError, match="classify_text requires"):
            build_transforms([{"op": "classify_text"}])

    def test_default_output_column(self) -> None:
        transforms = build_transforms([{"op": "classify_text", "column": "text"}])
        assert len(transforms) == 1

    def test_custom_output_column(self) -> None:
        transforms = build_transforms([{
            "op": "classify_text",
            "column": "desc",
            "output_column": "label",
            "provider": "openai",
            "model": "gpt-4",
        }])
        assert len(transforms) == 1


class TestClassifyImageBuild:
    def test_missing_column_raises(self) -> None:
        with pytest.raises(ValueError, match="classify_image requires"):
            build_transforms([{"op": "classify_image"}])

    def test_basic_build(self) -> None:
        transforms = build_transforms([{"op": "classify_image", "column": "img"}])
        assert len(transforms) == 1

    def test_custom_provider(self) -> None:
        transforms = build_transforms([{
            "op": "classify_image",
            "column": "img",
            "provider": "openai",
            "model": "gpt-4-vision",
            "output_column": "tags",
        }])
        assert len(transforms) == 1


class TestLlmGenerateBuild:
    def test_missing_column_raises(self) -> None:
        with pytest.raises(ValueError, match="llm_generate requires"):
            build_transforms([{"op": "llm_generate"}])

    def test_basic_build(self) -> None:
        transforms = build_transforms([{"op": "llm_generate", "column": "prompt"}])
        assert len(transforms) == 1

    def test_with_prompt_template(self) -> None:
        transforms = build_transforms([{
            "op": "llm_generate",
            "column": "input",
            "output_column": "response",
            "provider": "openai",
            "model": "gpt-4",
            "prompt_template": "Summarize: {text}",
        }])
        assert len(transforms) == 1


class TestPromptBuild:
    def test_missing_column_raises(self) -> None:
        with pytest.raises(ValueError, match="prompt requires"):
            build_transforms([{"op": "prompt"}])

    def test_basic_build(self) -> None:
        transforms = build_transforms([{"op": "prompt", "column": "question"}])
        assert len(transforms) == 1


class TestDeduplicateBuild:
    def test_missing_columns_raises(self) -> None:
        with pytest.raises(ValueError, match="deduplicate requires"):
            build_transforms([{"op": "deduplicate"}])

    def test_basic_build(self) -> None:
        transforms = build_transforms([{"op": "deduplicate", "columns": ["id"]}])
        assert len(transforms) == 1

    def test_with_order_by(self) -> None:
        transforms = build_transforms([{
            "op": "deduplicate",
            "columns": ["email"],
            "order_by": "created_at",
            "desc": True,
        }])
        assert len(transforms) == 1


# ---------------------------------------------------------------------------
# _resolve_dtype — comprehensive dtype mapping
# ---------------------------------------------------------------------------


class TestResolveDtype:
    @pytest.mark.parametrize("dtype_str", [
        "int8", "int16", "int32", "int64",
        "uint8", "uint16", "uint32", "uint64",
        "float32", "float64", "bool", "string", "utf8",
        "date", "timestamp",
    ])
    def test_known_dtypes(self, dtype_str: str) -> None:
        result = _resolve_dtype(dtype_str)
        assert result is not None

    def test_case_insensitive(self) -> None:
        assert _resolve_dtype("INT64") == _resolve_dtype("int64")
        assert _resolve_dtype("String") == _resolve_dtype("string")

    def test_unknown_dtype_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown dtype"):
            _resolve_dtype("tensor")


# ---------------------------------------------------------------------------
# Filter ops — is_null / is_not_null
# ---------------------------------------------------------------------------


class TestFilterNullOps:
    def test_is_null_build(self) -> None:
        transforms = build_transforms([{
            "op": "filter", "column": "val", "op_name": "is_null",
        }])
        assert len(transforms) == 1

    def test_is_not_null_build(self) -> None:
        transforms = build_transforms([{
            "op": "filter", "column": "val", "op_name": "is_not_null",
        }])
        assert len(transforms) == 1


class TestFilterComparisonOps:
    @pytest.mark.parametrize("op_name", [">", ">=", "<", "<=", "==", "!="])
    def test_all_comparison_ops_build(self, op_name: str) -> None:
        transforms = build_transforms([{
            "op": "filter", "column": "score", "op_name": op_name, "value": 50,
        }])
        assert len(transforms) == 1


# ---------------------------------------------------------------------------
# Apply transforms with AI ops (mocked)
# ---------------------------------------------------------------------------


class TestClassifyTextApply:
    def test_apply_classify_text(self) -> None:
        mock_df = MagicMock()

        transforms = build_transforms([{
            "op": "classify_text",
            "column": "text",
            "output_column": "label",
            "provider": "huggingface",
            "model": "bert-base",
        }])

        mock_functions = MagicMock()
        with patch.object(daft, "functions", mock_functions):
            transforms[0](mock_df)
            mock_functions.classify_text.assert_called_once()


class TestClassifyImageApply:
    def test_apply_classify_image(self) -> None:
        mock_df = MagicMock()

        transforms = build_transforms([{
            "op": "classify_image",
            "column": "image",
            "output_column": "tags",
        }])

        mock_functions = MagicMock()
        with patch.object(daft, "functions", mock_functions):
            transforms[0](mock_df)
            mock_functions.classify_image.assert_called_once()


class TestLlmGenerateApply:
    def test_apply_llm_generate(self) -> None:
        mock_df = MagicMock()

        transforms = build_transforms([{
            "op": "llm_generate",
            "column": "input",
            "output_column": "output",
            "prompt_template": "Summarize: {text}",
        }])

        mock_functions = MagicMock()
        with patch.object(daft, "functions", mock_functions):
            transforms[0](mock_df)
            mock_functions.llm_generate.assert_called_once()


class TestPromptApply:
    def test_apply_prompt(self) -> None:
        mock_df = MagicMock()

        transforms = build_transforms([{
            "op": "prompt",
            "column": "question",
            "output_column": "answer",
        }])

        mock_functions = MagicMock()
        with patch.object(daft, "functions", mock_functions):
            transforms[0](mock_df)
            mock_functions.prompt.assert_called_once()


class TestDeduplicateApply:
    def test_apply_with_order_by(self) -> None:
        df = daft.from_pydict({
            "id": [1, 2, 1, 3],
            "ts": [10, 20, 30, 40],
        })
        transforms = build_transforms([{
            "op": "deduplicate",
            "columns": ["id"],
            "order_by": "ts",
            "desc": True,
        }])
        result = apply_transforms(df, transforms)
        rows = result.count().to_arrow().column(0)[0].as_py()
        assert rows == 3  # id=1 kept once (ts=30, desc), id=2, id=3

    def test_apply_without_order_by(self) -> None:
        df = daft.from_pydict({
            "id": [1, 1, 2],
            "val": ["a", "b", "c"],
        })
        transforms = build_transforms([{
            "op": "deduplicate",
            "columns": ["id"],
        }])
        result = apply_transforms(df, transforms)
        rows = result.count().to_arrow().column(0)[0].as_py()
        assert rows == 2


# ---------------------------------------------------------------------------
# Extra kwargs forwarded for AI ops
# ---------------------------------------------------------------------------


class TestAiOpsExtraKwargs:
    def test_classify_text_forwards_kwargs(self) -> None:
        mock_df = MagicMock()

        transforms = build_transforms([{
            "op": "classify_text",
            "column": "text",
            "extra_param": "value1",
            "another": 42,
        }])

        mock_functions = MagicMock()
        with patch.object(daft, "functions", mock_functions):
            transforms[0](mock_df)
            call_kwargs = mock_functions.classify_text.call_args
            assert call_kwargs[1]["extra_param"] == "value1"
            assert call_kwargs[1]["another"] == 42

    def test_llm_generate_forwards_kwargs(self) -> None:
        mock_df = MagicMock()

        transforms = build_transforms([{
            "op": "llm_generate",
            "column": "input",
            "temperature": 0.7,
            "max_tokens": 100,
        }])

        mock_functions = MagicMock()
        with patch.object(daft, "functions", mock_functions):
            transforms[0](mock_df)
            call_kwargs = mock_functions.llm_generate.call_args
            assert call_kwargs[1]["temperature"] == 0.7
            assert call_kwargs[1]["max_tokens"] == 100


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_build_transforms_empty_list(self) -> None:
        assert build_transforms([]) == []

    def test_apply_transforms_identity(self) -> None:
        df = daft.from_pydict({"x": [1]})
        result = apply_transforms(df, [])
        assert result.count().to_arrow().column(0)[0].as_py() == 1

    def test_unknown_op(self) -> None:
        with pytest.raises(ValueError, match="Unknown transform op"):
            build_transforms([{"op": "nonexistent"}])

    def test_add_constant_with_dtype(self) -> None:
        df = daft.from_pydict({"x": [1]})
        transforms = build_transforms([{
            "op": "add_constant",
            "column": "flag",
            "value": 1,
            "dtype": "int64",
        }])
        result = apply_transforms(df, transforms)
        assert "flag" in result.column_names
