"""Cover missing lines in quality/masking_engine.py — cache TTL, Gravitino fetch, edge cases."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake.config.gravitino import GravitinoConfig
from arrow_lake.quality.masking_engine import MaskRule, MaskingEngine


@pytest.fixture(autouse=True)
def _hmac_key_env():
    """P0-6: default HMAC key so MaskingEngine construction succeeds."""
    with patch.dict(os.environ, {"ARROW_LAKE__MASKING__HMAC_KEY": "unit-test-key"}):
        yield


def _cfg(**overrides: object) -> GravitinoConfig:
    defaults = {"enabled": True, "uri": "http://g:8090", "metalake": "ml"}
    defaults.update(overrides)
    return GravitinoConfig(**defaults)


def _mock_resp(data: dict) -> MagicMock:
    """Create a mock HTTP response that works as a context manager."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── _get_rules cache logic (lines 91-99) ──


class TestGetRulesCache:
    def test_cached_rules_returned_within_ttl(self) -> None:
        engine = MaskingEngine(_cfg(masking_policy_cache_ttl_seconds=600))
        rules = [MaskRule(column="x", function="redact")]
        with engine._lock:
            engine._cache["ds"] = (rules, time.time())
        assert engine._get_rules("ds") is rules

    def test_expired_cache_triggers_refresh(self) -> None:
        engine = MaskingEngine(_cfg(masking_policy_cache_ttl_seconds=10))
        old_rules = [MaskRule(column="x", function="redact")]
        new_rules = [MaskRule(column="y", function="nullify")]
        with engine._lock:
            engine._cache["ds"] = (old_rules, time.time() - 100)
        with patch.object(engine, "_fetch_rules_from_gravitino", return_value=new_rules):
            result = engine._get_rules("ds")
        assert result is new_rules


# ── _fetch_rules_from_gravitino (lines 101-147) ──


class TestFetchRulesFromGravitino:
    def test_fetch_with_matching_masking_policy(self) -> None:
        engine = MaskingEngine(_cfg())

        list_data = {"identifiers": [{"name": "mask_pii"}]}
        detail_data = {
            "policy": {
                "properties": {
                    "applied_tables": json.dumps(["users"]),
                    "masking.columns": json.dumps(["email", "ssn"]),
                    "masking.function": "partial",
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [_mock_resp(list_data), _mock_resp(detail_data)]
            rules = engine._fetch_rules_from_gravitino("users")

        assert len(rules) == 2
        assert rules[0].column == "email"
        assert rules[0].function == "partial"
        assert rules[1].column == "ssn"

    def test_fetch_policy_name_without_mask_returns_empty(self) -> None:
        engine = MaskingEngine(_cfg())

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.return_value = _mock_resp({"identifiers": [{"name": "retention_30d"}]})
            rules = engine._fetch_rules_from_gravitino("any_table")

        assert rules == []

    def test_fetch_policy_table_not_in_applied_tables(self) -> None:
        engine = MaskingEngine(_cfg())

        list_data = {"identifiers": [{"name": "mask_secret"}]}
        detail_data = {
            "policy": {
                "properties": {
                    "applied_tables": json.dumps(["other_table"]),
                    "masking.columns": json.dumps(["secret"]),
                    "masking.function": "hash",
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [_mock_resp(list_data), _mock_resp(detail_data)]
            rules = engine._fetch_rules_from_gravitino("my_table")

        assert rules == []

    def test_fetch_list_api_failure_returns_empty(self) -> None:
        engine = MaskingEngine(_cfg())

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen", side_effect=RuntimeError("network")):
            MockReq.return_value = MagicMock()
            rules = engine._fetch_rules_from_gravitino("ds")

        assert rules == []

    def test_fetch_detail_api_failure_continues(self) -> None:
        engine = MaskingEngine(_cfg())

        list_data = {"identifiers": [{"name": "mask_pii"}]}

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [_mock_resp(list_data), RuntimeError("detail failed")]
            rules = engine._fetch_rules_from_gravitino("ds")

        assert rules == []

    def test_fetch_applied_tables_not_list_skips(self) -> None:
        engine = MaskingEngine(_cfg())

        detail_data = {
            "policy": {
                "properties": {
                    "applied_tables": json.dumps("not-a-list"),
                    "masking.columns": json.dumps(["col1"]),
                    "masking.function": "redact",
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "mask_x"}]}),
                _mock_resp(detail_data),
            ]
            rules = engine._fetch_rules_from_gravitino("ds")

        assert rules == []

    def test_fetch_columns_not_list_skips(self) -> None:
        engine = MaskingEngine(_cfg())

        detail_data = {
            "policy": {
                "properties": {
                    "applied_tables": json.dumps(["ds"]),
                    "masking.columns": json.dumps("not-a-list"),
                    "masking.function": "redact",
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "mask_x"}]}),
                _mock_resp(detail_data),
            ]
            rules = engine._fetch_rules_from_gravitino("ds")

        assert rules == []

    def test_fetch_columns_fallback_to_columns_key(self) -> None:
        """When masking.columns is missing, fall back to 'columns' key."""
        engine = MaskingEngine(_cfg())

        detail_data = {
            "policy": {
                "properties": {
                    "applied_tables": json.dumps(["ds"]),
                    "columns": json.dumps(["phone"]),
                    "masking.function": "hash",
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "mask_x"}]}),
                _mock_resp(detail_data),
            ]
            rules = engine._fetch_rules_from_gravitino("ds")

        assert len(rules) == 1
        assert rules[0].column == "phone"

    def test_fetch_empty_applied_tables_skips(self) -> None:
        """Empty applied_tables string means the whole block is skipped — no rules."""
        engine = MaskingEngine(_cfg())

        detail_data = {
            "policy": {
                "properties": {
                    "applied_tables": "",
                    "masking.columns": json.dumps(["col1"]),
                    "masking.function": "redact",
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "mask_x"}]}),
                _mock_resp(detail_data),
            ]
            rules = engine._fetch_rules_from_gravitino("ds")

        assert rules == []

    def test_fetch_non_string_column_entries_filtered(self) -> None:
        """Non-string entries in columns list are filtered out."""
        engine = MaskingEngine(_cfg())

        detail_data = {
            "policy": {
                "properties": {
                    "applied_tables": json.dumps(["ds"]),
                    "masking.columns": json.dumps(["email", 123, None]),
                    "masking.function": "redact",
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "mask_x"}]}),
                _mock_resp(detail_data),
            ]
            rules = engine._fetch_rules_from_gravitino("ds")

        assert len(rules) == 1
        assert rules[0].column == "email"

    def test_fetch_default_function_is_redact(self) -> None:
        """When masking.function is missing, default to 'redact'."""
        engine = MaskingEngine(_cfg())

        detail_data = {
            "policy": {
                "properties": {
                    "applied_tables": json.dumps(["ds"]),
                    "masking.columns": json.dumps(["col1"]),
                    # no masking.function key
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "mask_x"}]}),
                _mock_resp(detail_data),
            ]
            rules = engine._fetch_rules_from_gravitino("ds")

        assert len(rules) == 1
        assert rules[0].function == "redact"


# ── _mask_column edge cases (lines 167, 177) ──


class TestMaskColumnEdgeCases:
    def test_hash_with_null_values(self) -> None:
        with patch.dict(os.environ, {"ARROW_LAKE__MASKING__HMAC_KEY": "test-key!!!"}):
            engine = MaskingEngine(_cfg())
            col = pa.chunked_array([pa.array(["hello", None, "world"])])
            result = engine._mask_column(col, "hash")
            assert result[0].as_py() is not None
            assert result[1].as_py() is None
            assert result[2].as_py() is not None

    def test_partial_with_null_values(self) -> None:
        engine = MaskingEngine(_cfg())
        col = pa.chunked_array([pa.array([None, "abcd", "abcdefgh"])])
        result = engine._mask_column(col, "partial")
        assert result[0].as_py() is None
        assert result[1].as_py() == "****"  # len <= 4
        val = result[2].as_py()
        assert val is not None
        assert val.startswith("ab")
        assert val.endswith("gh")

    def test_partial_exactly_5_chars(self) -> None:
        engine = MaskingEngine(_cfg())
        col = pa.chunked_array([pa.array(["abcde"])])
        result = engine._mask_column(col, "partial")
        val = result[0].as_py()
        assert val is not None
        assert val == "ab*de"

    def test_nullify_with_null_values(self) -> None:
        engine = MaskingEngine(_cfg())
        col = pa.chunked_array([pa.array(["hello", None])])
        result = engine._mask_column(col, "nullify")
        assert result[0].as_py() is None
        assert result[1].as_py() is None

    def test_redact_replaces_all_chars(self) -> None:
        engine = MaskingEngine(_cfg())
        col = pa.chunked_array([pa.array(["test123"])])
        result = engine._mask_column(col, "redact")
        assert result[0].as_py() == "*******"
