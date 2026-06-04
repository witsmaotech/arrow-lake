"""Cover missing lines in quality/retention_enforcer.py — run loop, thread lifecycle, Gravitino fetch."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.config.gravitino import GravitinoConfig
from arrow_lake.quality.retention_enforcer import RetentionEnforcer


def _cfg(**overrides: object) -> GravitinoConfig:
    defaults = {
        "enabled": True,
        "uri": "http://g:8090",
        "metalake": "ml",
        "retention_enforce_interval_seconds": 300,
    }
    defaults.update(overrides)
    return GravitinoConfig(**defaults)


def _mock_resp(data: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@pytest.fixture
def storage() -> MagicMock:
    s = MagicMock()
    s.cleanup_versions.return_value = 3
    return s


@pytest.fixture
def enforcer(storage: MagicMock) -> RetentionEnforcer:
    return RetentionEnforcer(config=_cfg(), storage=storage)


# ── stop: thread still alive (line 54) ──


class TestStopThreadStillAlive:
    def test_stop_logs_error_if_thread_survives_join(self, enforcer: RetentionEnforcer) -> None:
        enforcer._thread = MagicMock()
        enforcer._thread.is_alive.return_value = True
        enforcer._thread.join = MagicMock()
        with patch("arrow_lake.quality.retention_enforcer.logger") as mock_log:
            enforcer.stop()
            mock_log.error.assert_called_once()
        assert enforcer._thread is None


# ── _run_loop (lines 61-67) ──


class TestRunLoop:
    def test_run_loop_calls_enforce_once_then_stops(self, enforcer: RetentionEnforcer) -> None:
        """The _stop.wait returns False first time (interval expires), then True (stop set)."""
        call_count = 0

        def _wait_side_effect(timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return False  # interval expired — run enforce
            enforcer._stop.set()  # simulate stop
            return True  # stop requested

        enforcer._stop.wait = _wait_side_effect
        with patch.object(enforcer, "enforce", return_value=5) as mock_enforce:
            enforcer._run_loop()
        mock_enforce.assert_called_once()

    def test_run_loop_enforce_exception_logged(self, enforcer: RetentionEnforcer) -> None:
        call_count = 0

        def _wait_side_effect(timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return False
            enforcer._stop.set()
            return True

        enforcer._stop.wait = _wait_side_effect
        with patch.object(enforcer, "enforce", side_effect=RuntimeError("boom")):
            # Should not raise, just log warning
            enforcer._run_loop()

    def test_run_loop_zero_cleaned_no_log(self, enforcer: RetentionEnforcer) -> None:
        call_count = 0

        def _wait_side_effect(timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return False
            enforcer._stop.set()
            return True

        enforcer._stop.wait = _wait_side_effect
        with patch.object(enforcer, "enforce", return_value=0):
            enforcer._run_loop()


# ── enforce: table failure continues (lines 82-83) ──


class TestEnforceTableFailure:
    def test_single_table_exception_continues(self, enforcer: RetentionEnforcer, storage: MagicMock) -> None:
        storage.cleanup_versions.side_effect = [RuntimeError("fail"), 2]
        with patch.object(enforcer, "_fetch_retention_policies", return_value={"ds1": 7, "ds2": 30}):
            total = enforcer.enforce()
        assert total == 2


# ── _enforce_table branch (107->110) ──


class TestEnforceTableBranch:
    def test_cleanup_returns_zero_no_info_log(self, enforcer: RetentionEnforcer, storage: MagicMock) -> None:
        storage.cleanup_versions.return_value = 0
        result = enforcer._enforce_table("ds1", 7)
        assert result == 0


# ── _fetch_retention_policies (lines 121-162) ──


class TestFetchRetentionPolicies:
    def test_fetch_with_matching_retention_policy(self, enforcer: RetentionEnforcer) -> None:
        list_data = {"identifiers": [{"name": "retention_30d"}]}
        detail_data = {
            "policy": {
                "properties": {
                    "retention.days": "30",
                    "applied_tables": json.dumps(["orders", "logs"]),
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [_mock_resp(list_data), _mock_resp(detail_data)]
            policies = enforcer._fetch_retention_policies()

        assert policies == {"orders": 30, "logs": 30}

    def test_fetch_policy_name_without_retention_skips(self, enforcer: RetentionEnforcer) -> None:
        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.return_value = _mock_resp({"identifiers": [{"name": "mask_pii"}]})
            policies = enforcer._fetch_retention_policies()

        assert policies == {}

    def test_fetch_list_api_failure_returns_empty(self, enforcer: RetentionEnforcer) -> None:
        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen", side_effect=RuntimeError("network")):
            MockReq.return_value = MagicMock()
            policies = enforcer._fetch_retention_policies()

        assert policies == {}

    def test_fetch_detail_failure_continues(self, enforcer: RetentionEnforcer) -> None:
        list_data = {"identifiers": [{"name": "retention_30d"}]}

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [_mock_resp(list_data), RuntimeError("detail fail")]
            policies = enforcer._fetch_retention_policies()

        assert policies == {}

    def test_fetch_applied_tables_not_list_skips(self, enforcer: RetentionEnforcer) -> None:
        detail_data = {
            "policy": {
                "properties": {
                    "retention.days": "7",
                    "applied_tables": json.dumps("not-a-list"),
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "retention_7d"}]}),
                _mock_resp(detail_data),
            ]
            policies = enforcer._fetch_retention_policies()

        assert policies == {}

    def test_fetch_zero_days_skips(self, enforcer: RetentionEnforcer) -> None:
        detail_data = {
            "policy": {
                "properties": {
                    "retention.days": "0",
                    "applied_tables": json.dumps(["tbl"]),
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "retention_0"}]}),
                _mock_resp(detail_data),
            ]
            policies = enforcer._fetch_retention_policies()

        assert policies == {}

    def test_fetch_non_string_table_entry_skips(self, enforcer: RetentionEnforcer) -> None:
        detail_data = {
            "policy": {
                "properties": {
                    "retention.days": "30",
                    "applied_tables": json.dumps(["valid_table", 123, None]),
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "retention_30"}]}),
                _mock_resp(detail_data),
            ]
            policies = enforcer._fetch_retention_policies()

        assert policies == {"valid_table": 30}

    def test_fetch_fallback_to_days_key(self, enforcer: RetentionEnforcer) -> None:
        """When retention.days is missing, fall back to 'days' key."""
        detail_data = {
            "policy": {
                "properties": {
                    "days": "14",
                    "applied_tables": json.dumps(["tbl"]),
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "retention_14"}]}),
                _mock_resp(detail_data),
            ]
            policies = enforcer._fetch_retention_policies()

        assert policies == {"tbl": 14}

    def test_fetch_default_days_zero(self, enforcer: RetentionEnforcer) -> None:
        """When both retention.days and days are missing, default is 0 — skips."""
        detail_data = {
            "policy": {
                "properties": {
                    "applied_tables": json.dumps(["tbl"]),
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "retention_x"}]}),
                _mock_resp(detail_data),
            ]
            policies = enforcer._fetch_retention_policies()

        assert policies == {}

    def test_fetch_empty_applied_tables_no_tables(self, enforcer: RetentionEnforcer) -> None:
        detail_data = {
            "policy": {
                "properties": {
                    "retention.days": "7",
                    "applied_tables": "",
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp({"identifiers": [{"name": "retention_7"}]}),
                _mock_resp(detail_data),
            ]
            policies = enforcer._fetch_retention_policies()

        assert policies == {}

    def test_fetch_multiple_policies(self, enforcer: RetentionEnforcer) -> None:
        list_data = {
            "identifiers": [
                {"name": "retention_30d"},
                {"name": "retention_7d"},
            ]
        }
        detail_30 = {
            "policy": {
                "properties": {
                    "retention.days": "30",
                    "applied_tables": json.dumps(["orders"]),
                }
            }
        }
        detail_7 = {
            "policy": {
                "properties": {
                    "retention.days": "7",
                    "applied_tables": json.dumps(["logs"]),
                }
            }
        }

        with patch("urllib.request.Request") as MockReq, \
             patch("urllib.request.urlopen") as mock_open:
            MockReq.return_value = MagicMock()
            mock_open.side_effect = [
                _mock_resp(list_data),
                _mock_resp(detail_30),
                _mock_resp(detail_7),
            ]
            policies = enforcer._fetch_retention_policies()

        assert policies == {"orders": 30, "logs": 7}
