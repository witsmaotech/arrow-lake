"""Tests for arrow_lake/quality/gravitino_policies.py.

Targets uncovered lines: 47-49, 65-66, 77-92, 103-110, 122-128, 132-134.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.config.gravitino import GravitinoConfig
from arrow_lake.quality.gravitino_policies import (
    GravitinoPolicyService,
    json_list,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(enabled: bool = True) -> GravitinoConfig:
    return GravitinoConfig(enabled=enabled, uri="http://fake:8090", metalake="test_ml")


@pytest.fixture()
def config_disabled() -> GravitinoConfig:
    return _make_config(enabled=False)


@pytest.fixture()
def config_enabled() -> GravitinoConfig:
    return _make_config(enabled=True)


# ---------------------------------------------------------------------------
# json_list helper
# ---------------------------------------------------------------------------


class TestJsonList:
    def test_serializes_list_of_strings(self) -> None:
        result: str = json_list(["a", "b", "c"])
        assert result == '["a", "b", "c"]'

    def test_empty_list(self) -> None:
        result: str = json_list([])
        assert result == "[]"


# ---------------------------------------------------------------------------
# GravitinoPolicyService — disabled config (no client)
# ---------------------------------------------------------------------------


class TestPolicyServiceDisabled:
    def test_client_is_none_when_disabled(self, config_disabled: GravitinoConfig) -> None:
        svc = GravitinoPolicyService(config_disabled)
        assert svc._client is None

    def test_create_retention_returns_early(self, config_disabled: GravitinoConfig) -> None:
        svc = GravitinoPolicyService(config_disabled)
        svc.create_retention_policy("ret-1", days=30)
        assert svc._client is None  # nothing to call

    def test_create_masking_returns_early(self, config_disabled: GravitinoConfig) -> None:
        svc = GravitinoPolicyService(config_disabled)
        svc.create_masking_policy("mask-1", columns=["ssn"])
        assert svc._client is None

    def test_apply_policy_returns_early(self, config_disabled: GravitinoConfig) -> None:
        svc = GravitinoPolicyService(config_disabled)
        svc.apply_policy("pol-1", "table_a")
        assert svc._client is None

    def test_list_policies_returns_empty(self, config_disabled: GravitinoConfig) -> None:
        svc = GravitinoPolicyService(config_disabled)
        result = svc.list_policies()
        assert result == []


# ---------------------------------------------------------------------------
# GravitinoPolicyService — enabled config with mocked client
# ---------------------------------------------------------------------------


class TestPolicyServiceEnabled:
    @pytest.fixture()
    def service(self, config_enabled: GravitinoConfig) -> GravitinoPolicyService:
        with patch(
            "arrow_lake.quality.gravitino_policies.GravitinoClient",
            create=True,
        ):
            svc = GravitinoPolicyService(config_enabled)
        # Override client with a mock so we control behavior
        svc._client = MagicMock()
        return svc

    @pytest.fixture()
    def metalake(self, service: GravitinoPolicyService) -> MagicMock:
        ml = MagicMock()
        service._client.load_metalake.return_value = ml
        return ml

    # -- _get_metalake failure path (lines 47-49) --

    def test_get_metalake_returns_none_on_exception(
        self, config_enabled: GravitinoConfig
    ) -> None:
        with patch(
            "arrow_lake.quality.gravitino_policies.GravitinoClient",
            create=True,
        ):
            svc = GravitinoPolicyService(config_enabled)
        svc._client = MagicMock()
        svc._client.load_metalake.side_effect = RuntimeError("boom")

        result = svc._get_metalake()
        assert result is None

    # -- create_retention_policy (lines 65-66) --

    def test_create_retention_policy_success(
        self, service: GravitinoPolicyService, metalake: MagicMock
    ) -> None:
        service.create_retention_policy("ret-1", days=90)
        metalake.create_policy.assert_called_once_with(
            name="ret-1",
            policy_type="retention",
            comment="Retain data for 90 days",
            properties={"retention.days": "90"},
        )

    def test_create_retention_policy_exception(
        self, service: GravitinoPolicyService, metalake: MagicMock
    ) -> None:
        metalake.create_policy.side_effect = RuntimeError("policy error")
        service.create_retention_policy("ret-2", days=60)  # should not raise
        metalake.create_policy.assert_called_once()

    # -- create_masking_policy (lines 77-92) --

    def test_create_masking_policy_success(
        self, service: GravitinoPolicyService, metalake: MagicMock
    ) -> None:
        service.create_masking_policy("mask-1", columns=["email", "phone"])
        metalake.create_policy.assert_called_once()
        call_kwargs = metalake.create_policy.call_args[1]
        assert call_kwargs["policy_type"] == "masking"
        assert call_kwargs["properties"]["masking.function"] == "redact"

    def test_create_masking_policy_function_passthrough(
        self, service: GravitinoPolicyService, metalake: MagicMock
    ) -> None:
        # P0-6: function param flows through to the policy property (was hardcoded redact).
        service.create_masking_policy("mask-h", columns=["ssn"], function="hash")
        call_kwargs = metalake.create_policy.call_args[1]
        assert call_kwargs["properties"]["masking.function"] == "hash"
        assert "hash" in call_kwargs["comment"]

    def test_create_masking_policy_exception(
        self, service: GravitinoPolicyService, metalake: MagicMock
    ) -> None:
        metalake.create_policy.side_effect = RuntimeError("mask fail")
        service.create_masking_policy("mask-2", columns=["ssn"])  # should not raise
        metalake.create_policy.assert_called_once()

    # -- apply_policy (lines 103-110) --

    def test_apply_policy_success(
        self, service: GravitinoPolicyService, metalake: MagicMock
    ) -> None:
        catalog = MagicMock()
        table_catalog = MagicMock()
        table_obj = MagicMock()
        policies_support = MagicMock()

        service._client.load_catalog.return_value = catalog
        catalog.as_table_catalog.return_value = table_catalog
        table_catalog.load_table.return_value = table_obj
        table_obj.supports_policies.return_value = policies_support

        service.apply_policy("pol-1", "users")

        service._client.load_catalog.assert_called_once_with("arrow_lake_lance")
        policies_support.associate_policies.assert_called_once_with(["pol-1"])

    def test_apply_policy_exception(
        self, service: GravitinoPolicyService, metalake: MagicMock
    ) -> None:
        service._client.load_catalog.side_effect = RuntimeError("catalog fail")
        service.apply_policy("pol-1", "users")  # should not raise

    # -- list_policies (lines 122-128) --

    def test_list_policies_success(
        self, service: GravitinoPolicyService, metalake: MagicMock
    ) -> None:
        p1, p2 = MagicMock(), MagicMock()
        p1.name.return_value = "pol-a"
        p2.name.return_value = "pol-b"
        metalake.list_policies.return_value = [p1, p2]

        result = service.list_policies()
        assert result == ["pol-a", "pol-b"]

    def test_list_policies_none_return(
        self, service: GravitinoPolicyService, metalake: MagicMock
    ) -> None:
        metalake.list_policies.return_value = None

        result = service.list_policies()
        assert result == []

    def test_list_policies_exception(
        self, service: GravitinoPolicyService, metalake: MagicMock
    ) -> None:
        metalake.list_policies.side_effect = RuntimeError("list fail")

        result = service.list_policies()
        assert result == []


# ---------------------------------------------------------------------------
# GravitinoPolicyService — init client failure
# ---------------------------------------------------------------------------


class TestPolicyServiceInitFailure:
    def test_init_client_exception_sets_client_none(
        self, config_enabled: GravitinoConfig
    ) -> None:
        with patch(
            "arrow_lake.quality.gravitino_policies.GravitinoClient",
            create=True,
            side_effect=ImportError("no gravitino"),
        ):
            svc = GravitinoPolicyService(config_enabled)
        assert svc._client is None

    def test_methods_return_safely_when_client_none(
        self, config_enabled: GravitinoConfig
    ) -> None:
        with patch(
            "arrow_lake.quality.gravitino_policies.GravitinoClient",
            create=True,
            side_effect=ImportError("no gravitino"),
        ):
            svc = GravitinoPolicyService(config_enabled)
        assert svc.list_policies() == []
        svc.create_retention_policy("r", 10)
        svc.create_masking_policy("m", ["col"])
        svc.apply_policy("p", "t")
