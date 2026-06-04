"""Tests for StatsInjector — statistics-driven query optimization hints."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.config.gravitino import GravitinoConfig
from arrow_lake.query.stats_injector import QueryHints, StatsInjector


@pytest.fixture
def gravitino_config() -> GravitinoConfig:
    return GravitinoConfig(
        uri="http://localhost:8090",
        metalake="test_metalake",
        lance_catalog_name="lake_catalog",
        lance_schema_name="default",
        stats_cache_ttl_seconds=60,
        stats_auto_route_threshold=100_000,
    )


@pytest.fixture
def injector(gravitino_config: GravitinoConfig) -> StatsInjector:
    return StatsInjector(gravitino_config)


# ── QueryHints ──


class TestQueryHints:
    def test_defaults(self) -> None:
        h = QueryHints()
        assert h.estimated_rows == 0
        assert h.column_count == 0
        assert h.size_mb == 0.0
        assert h.is_large is False

    def test_large_by_rows(self) -> None:
        h = QueryHints(estimated_rows=2_000_000)
        assert h.is_large is True

    def test_large_by_size(self) -> None:
        h = QueryHints(size_mb=2048.0)
        assert h.is_large is True

    def test_not_large(self) -> None:
        h = QueryHints(estimated_rows=500_000, size_mb=512.0)
        assert h.is_large is False

    def test_frozen(self) -> None:
        h = QueryHints()
        with pytest.raises(AttributeError):
            h.estimated_rows = 42  # type: ignore[misc]


# ── get_hints / caching ──


class TestGetHints:
    def test_fetches_from_gravitino(self, injector: StatsInjector) -> None:
        with patch.object(injector, "_fetch_hints", return_value=QueryHints(estimated_rows=50000, column_count=12, size_mb=128.5)):
            hints = injector.get_hints("articles")
        assert hints.estimated_rows == 50000
        assert hints.column_count == 12
        assert hints.size_mb == 128.5

    def test_caches_result(self, injector: StatsInjector) -> None:
        with patch.object(injector, "_fetch_hints", return_value=QueryHints(estimated_rows=100)) as mock_fetch:
            injector.get_hints("ds1")
            injector.get_hints("ds1")  # Should use cache
            assert mock_fetch.call_count == 1

    def test_cache_expiry(self, injector: StatsInjector) -> None:
        injector._ttl = 0  # Immediate expiry
        with patch.object(injector, "_fetch_hints", return_value=QueryHints(estimated_rows=10)) as mock_fetch:
            injector.get_hints("ds1")
            time.sleep(0.01)
            injector.get_hints("ds1")  # TTL expired, should re-fetch
            assert mock_fetch.call_count == 2

    def test_fetch_failure_returns_empty(self, injector: StatsInjector) -> None:
        with patch.object(injector, "_fetch_hints", return_value=QueryHints()):
            hints = injector.get_hints("articles")
        assert hints.estimated_rows == 0

    def test_invalid_dataset_name_returns_empty(self, injector: StatsInjector) -> None:
        with patch.object(injector, "_fetch_hints", return_value=QueryHints()):
            hints = injector.get_hints("../../evil")
        assert hints.estimated_rows == 0


# ── invalidate ──


class TestInvalidate:
    def test_invalidate_clears_cache(self, injector: StatsInjector) -> None:
        injector._cache["ds1"] = (QueryHints(estimated_rows=100), time.time())
        injector.invalidate("ds1")
        assert "ds1" not in injector._cache


# ── should_use_olap ──


class TestShouldUseOlap:
    def test_above_threshold(self, injector: StatsInjector) -> None:
        with patch.object(injector, "get_hints", return_value=QueryHints(estimated_rows=200_000)):
            assert injector.should_use_olap("big_ds") is True

    def test_below_threshold(self, injector: StatsInjector) -> None:
        with patch.object(injector, "get_hints", return_value=QueryHints(estimated_rows=50_000)):
            assert injector.should_use_olap("small_ds") is False


# ── suggest_limit ──


class TestSuggestLimit:
    def test_respects_requested_limit(self, injector: StatsInjector) -> None:
        result = injector.suggest_limit("ds", requested_limit=500)
        assert result == 500

    def test_large_dataset_suggests_limit(self, injector: StatsInjector) -> None:
        with patch.object(injector, "get_hints", return_value=QueryHints(estimated_rows=500_000)):
            result = injector.suggest_limit("ds")
        assert result == 10_000

    def test_small_dataset_no_suggestion(self, injector: StatsInjector) -> None:
        with patch.object(injector, "get_hints", return_value=QueryHints(estimated_rows=1_000)):
            result = injector.suggest_limit("ds")
        assert result is None

    def test_zero_rows_no_suggestion(self, injector: StatsInjector) -> None:
        with patch.object(injector, "get_hints", return_value=QueryHints(estimated_rows=0)):
            result = injector.suggest_limit("ds")
        assert result is None


# ── _fetch_hints (direct HTTP path) ──


def _make_urlopen_response(body: dict) -> MagicMock:
    """Build a fake urlopen response that supports context-manager and read()."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestFetchHints:
    """Direct tests for StatsInjector._fetch_hints covering the HTTP path."""

    def test_valid_dataset_returns_parsed_hints(
        self, injector: StatsInjector,
    ) -> None:
        body = {
            "table": {
                "properties": {
                    "stats.row_count": "50000",
                    "stats.column_count": "12",
                    "stats.size_mb": "128.5",
                }
            }
        }
        fake_resp = _make_urlopen_response(body)
        with patch(
            "urllib.request.urlopen",
            return_value=fake_resp,
        ) as mock_urlopen:
            hints = injector._fetch_hints("articles")

        assert mock_urlopen.called
        assert hints.estimated_rows == 50000
        assert hints.column_count == 12
        assert hints.size_mb == 128.5

    def test_valid_dataset_url_and_headers(
        self, injector: StatsInjector,
    ) -> None:
        fake_resp = _make_urlopen_response({"table": {"properties": {}}})
        with patch(
            "urllib.request.urlopen",
            return_value=fake_resp,
        ) as mock_urlopen:
            injector._fetch_hints("my_table")

        call_args = mock_urlopen.call_args
        req = call_args.args[0]
        assert "tables/my_table" in req.full_url
        assert "test_metalake" in req.full_url
        assert "lake_catalog" in req.full_url
        assert "default" in req.full_url

    def test_invalid_dataset_with_path_traversal(
        self, injector: StatsInjector,
    ) -> None:
        hints = injector._fetch_hints("../evil")
        assert hints.estimated_rows == 0
        assert hints.column_count == 0
        assert hints.size_mb == 0.0

    def test_invalid_dataset_with_special_chars(
        self, injector: StatsInjector,
    ) -> None:
        hints = injector._fetch_hints("bad;drop table")
        assert hints.estimated_rows == 0

    def test_http_error_returns_empty_hints(
        self, injector: StatsInjector,
    ) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=Exception("connection refused"),
        ):
            hints = injector._fetch_hints("articles")
        assert hints.estimated_rows == 0
        assert hints.column_count == 0
        assert hints.size_mb == 0.0

    def test_response_missing_table_key(
        self, injector: StatsInjector,
    ) -> None:
        fake_resp = _make_urlopen_response({"other_key": {}})
        with patch(
            "urllib.request.urlopen",
            return_value=fake_resp,
        ):
            hints = injector._fetch_hints("articles")
        assert hints.estimated_rows == 0
        assert hints.column_count == 0
        assert hints.size_mb == 0.0

    def test_response_missing_properties_key(
        self, injector: StatsInjector,
    ) -> None:
        fake_resp = _make_urlopen_response({"table": {"name": "articles"}})
        with patch(
            "urllib.request.urlopen",
            return_value=fake_resp,
        ):
            hints = injector._fetch_hints("articles")
        assert hints.estimated_rows == 0
        assert hints.column_count == 0
        assert hints.size_mb == 0.0

    def test_response_empty_properties_returns_defaults(
        self, injector: StatsInjector,
    ) -> None:
        fake_resp = _make_urlopen_response({"table": {"properties": {}}})
        with patch(
            "urllib.request.urlopen",
            return_value=fake_resp,
        ):
            hints = injector._fetch_hints("articles")
        assert hints.estimated_rows == 0
        assert hints.column_count == 0
        assert hints.size_mb == 0.0

    def test_response_with_numeric_stats_converts_correctly(
        self, injector: StatsInjector,
    ) -> None:
        body = {
            "table": {
                "properties": {
                    "stats.row_count": 12345,
                    "stats.column_count": 8,
                    "stats.size_mb": 99.9,
                }
            }
        }
        fake_resp = _make_urlopen_response(body)
        with patch(
            "urllib.request.urlopen",
            return_value=fake_resp,
        ):
            hints = injector._fetch_hints("events")
        assert hints.estimated_rows == 12345
        assert hints.column_count == 8
        assert hints.size_mb == 99.9

    def test_json_decode_error_returns_empty_hints(
        self, injector: StatsInjector,
    ) -> None:
        bad_resp = MagicMock()
        bad_resp.read.return_value = b"not-json!!!"
        bad_resp.__enter__ = MagicMock(return_value=bad_resp)
        bad_resp.__exit__ = MagicMock(return_value=False)
        with patch(
            "urllib.request.urlopen",
            return_value=bad_resp,
        ):
            hints = injector._fetch_hints("articles")
        assert hints.estimated_rows == 0
        assert hints.column_count == 0
        assert hints.size_mb == 0.0
