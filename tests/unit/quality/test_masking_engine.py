"""Tests for quality/masking_engine.py — MaskRule, MaskingEngine."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake.config.gravitino import GravitinoConfig
from arrow_lake.quality.masking_engine import MaskRule, MaskingEngine


def _cfg() -> GravitinoConfig:
    return GravitinoConfig(enabled=True, uri="http://g:8090", metalake="ml")


def _table() -> pa.Table:
    return pa.table({"email": ["a@b.com", "c@d.com"], "phone": ["1234567890", "0987654321"]})


class TestMaskRule:
    def test_creation(self) -> None:
        rule = MaskRule(column="email", function="hash")
        assert rule.column == "email"
        assert rule.function == "hash"

    def test_frozen(self) -> None:
        rule = MaskRule(column="x", function="nullify")
        with pytest.raises(AttributeError):
            rule.column = "y"  # type: ignore[misc]


class TestMaskingEngineInit:
    def test_creates_engine(self) -> None:
        engine = MaskingEngine(_cfg())
        assert engine is not None


class TestMaskingEngineApplyMasking:
    def test_no_rules_returns_original(self) -> None:
        engine = MaskingEngine(_cfg())
        table = _table()
        with patch.object(engine, "_get_rules", return_value=[]):
            result = engine.apply_masking(table, dataset="ds", role="viewer")
        assert result.num_rows == 2

    def test_hash_masking(self) -> None:
        with patch.dict(os.environ, {"ARROW_LAKE__MASKING__HMAC_KEY": "test-key!!!"}):
            engine = MaskingEngine(_cfg())
            rules = [MaskRule(column="email", function="hash")]
            with patch.object(engine, "_get_rules", return_value=rules):
                result = engine.apply_masking(_table(), dataset="ds", role="viewer")
            hashed = result.column("email")[0].as_py()
            assert hashed != "a@b.com"
            assert hashed is not None

    def test_nullify_masking(self) -> None:
        engine = MaskingEngine(_cfg())
        rules = [MaskRule(column="email", function="nullify")]
        with patch.object(engine, "_get_rules", return_value=rules):
            result = engine.apply_masking(_table(), dataset="ds", role="viewer")
        assert result.column("email")[0].as_py() is None

    def test_partial_masking(self) -> None:
        engine = MaskingEngine(_cfg())
        rules = [MaskRule(column="phone", function="partial")]
        with patch.object(engine, "_get_rules", return_value=rules):
            result = engine.apply_masking(_table(), dataset="ds", role="viewer")
        val = result.column("phone")[0].as_py()
        assert val is not None
        assert "****" in val or "*" in val

    def test_unknown_function_passes_through(self) -> None:
        engine = MaskingEngine(_cfg())
        rules = [MaskRule(column="email", function="unknown_func")]
        with patch.object(engine, "_get_rules", return_value=rules):
            result = engine.apply_masking(_table(), dataset="ds", role="viewer")
        # Should not crash, column may pass through
        assert result.num_rows == 2

    def test_column_not_in_table(self) -> None:
        engine = MaskingEngine(_cfg())
        rules = [MaskRule(column="nonexistent", function="hash")]
        with patch.object(engine, "_get_rules", return_value=rules):
            result = engine.apply_masking(_table(), dataset="ds", role="viewer")
        assert result.num_columns == 2

    def test_admin_role_not_masked(self) -> None:
        engine = MaskingEngine(_cfg())
        rules = [MaskRule(column="email", function="nullify")]
        with patch.object(engine, "_get_rules", return_value=rules):
            result = engine.apply_masking(_table(), dataset="ds", role="admin")
        assert result.column("email")[0].as_py() == "a@b.com"


class TestMaskingEngineRefreshCache:
    def test_refresh_returns_rules(self) -> None:
        engine = MaskingEngine(_cfg())
        rules = [MaskRule(column="x", function="hash")]
        with patch.object(engine, "_fetch_rules_from_gravitino", return_value=rules):
            result = engine.refresh_cache("ds")
        assert len(result) == 1


class TestMaskingEngineInternalMethods:
    def test_hash_column(self) -> None:
        with patch.dict(os.environ, {"ARROW_LAKE__MASKING__HMAC_KEY": "test-key!!!"}):
            engine = MaskingEngine(_cfg())
            col = pa.chunked_array([pa.array(["hello", "world"])])
            result = engine._mask_column(col, "hash")
            assert result[0].as_py() != "hello"

    def test_hash_column_no_key_fallback_nullify(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            # Remove key if present
            os.environ.pop("ARROW_LAKE__MASKING__HMAC_KEY", None)
            engine = MaskingEngine(_cfg())
            col = pa.chunked_array([pa.array(["hello"])])
            result = engine._mask_column(col, "hash")
            assert result[0].as_py() is None

    def test_partial_column(self) -> None:
        engine = MaskingEngine(_cfg())
        col = pa.chunked_array([pa.array(["abcdefghij"])])
        result = engine._mask_column(col, "partial")
        val = result[0].as_py()
        assert "*" in val

    def test_partial_column_short(self) -> None:
        engine = MaskingEngine(_cfg())
        col = pa.chunked_array([pa.array(["ab"])])
        result = engine._mask_column(col, "partial")
        assert result[0].as_py() == "****"

    def test_nullify_column(self) -> None:
        engine = MaskingEngine(_cfg())
        col = pa.chunked_array([pa.array(["hello", "world"])])
        result = engine._mask_column(col, "nullify")
        assert result[0].as_py() is None

    def test_redact_column(self) -> None:
        engine = MaskingEngine(_cfg())
        col = pa.chunked_array([pa.array(["hello"])])
        result = engine._mask_column(col, "redact")
        assert result[0].as_py() == "*****"

    def test_unknown_function_noop(self) -> None:
        engine = MaskingEngine(_cfg())
        col = pa.chunked_array([pa.array(["hello"])])
        result = engine._mask_column(col, "bogus")
        assert result[0].as_py() == "hello"
