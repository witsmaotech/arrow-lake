"""Tests for v1.4.2 deep governance: RetentionEnforcer, MaskingEngine, TagACLResolver,
StatsInjector, RegistryModelResolver, FederatedQueryEngine."""

from __future__ import annotations

import json
import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake.config.gravitino import GravitinoConfig


def _cfg(**overrides: object) -> GravitinoConfig:
    defaults = {
        "enabled": True,
        "uri": "http://gravitino:8090",
        "metalake": "test",
        "lance_rest_uri": "http://lance-rest:9002",
    }
    defaults.update(overrides)
    return GravitinoConfig(**defaults)


# ────────────────────────────────────────────────────────────
# RetentionEnforcer
# ────────────────────────────────────────────────────────────

class TestRetentionEnforcer:
    def test_enforce_no_policies(self) -> None:
        from arrow_lake.quality.retention_enforcer import RetentionEnforcer

        config = _cfg()
        storage = MagicMock()
        enforcer = RetentionEnforcer(config, storage)
        with patch.object(enforcer, "_fetch_retention_policies", return_value={}):
            assert enforcer.enforce() == 0

    def test_enforce_table_calls_cleanup(self) -> None:
        from arrow_lake.quality.retention_enforcer import RetentionEnforcer

        config = _cfg(retention_enforce_interval_seconds=9999)
        storage = MagicMock()
        storage.cleanup_versions.return_value = 3
        enforcer = RetentionEnforcer(config, storage)
        with patch.object(
            enforcer, "_fetch_retention_policies", return_value={"logs": 30}
        ):
            result = enforcer.enforce()
            assert result == 3
            storage.cleanup_versions.assert_called_once_with(
                "logs", older_than=timedelta(days=30), dry_run=False
            )

    def test_enforce_dry_run(self) -> None:
        from arrow_lake.quality.retention_enforcer import RetentionEnforcer

        config = _cfg()
        storage = MagicMock()
        storage.cleanup_versions.return_value = 5
        enforcer = RetentionEnforcer(config, storage)
        with patch.object(
            enforcer, "_fetch_retention_policies", return_value={"events": 7}
        ):
            assert enforcer.enforce(dry_run=True) == 5
            storage.cleanup_versions.assert_called_once_with(
                "events", older_than=timedelta(days=7), dry_run=True
            )


# ────────────────────────────────────────────────────────────
# MaskingEngine
# ────────────────────────────────────────────────────────────

class TestMaskingEngine:
    @pytest.fixture(autouse=True)
    def _hmac_key_env(self):
        """P0-6: default HMAC key so MaskingEngine construction succeeds."""
        import os
        with patch.dict(os.environ, {"ARROW_LAKE__MASKING__HMAC_KEY": "unit-test-key"}):
            yield

    @pytest.fixture()
    def table(self) -> pa.Table:
        return pa.table({
            "id": [1, 2, 3],
            "email": ["a@b.com", "c@d.com", "e@f.com"],
            "phone": ["1234567890", "0987654321", "1112223333"],
            "name": ["Alice", "Bob", "Charlie"],
        })

    def test_admin_skips_masking(self, table: pa.Table) -> None:
        from arrow_lake.quality.masking_engine import MaskingEngine

        engine = MaskingEngine(_cfg())
        result = engine.apply_masking(table, dataset="users", role="admin")
        assert result == table

    def test_redact_masking(self, table: pa.Table) -> None:
        from arrow_lake.quality.masking_engine import MaskingEngine, MaskRule

        engine = MaskingEngine(_cfg())
        rules = [MaskRule(column="email", function="redact")]
        with patch.object(engine, "_get_rules", return_value=rules):
            result = engine.apply_masking(table, dataset="users", role="viewer")
        assert result.column("email")[0].as_py() != "a@b.com"
        # name should be unchanged
        assert result.column("name")[0].as_py() == "Alice"

    def test_nullify_masking(self, table: pa.Table) -> None:
        from arrow_lake.quality.masking_engine import MaskingEngine, MaskRule

        engine = MaskingEngine(_cfg())
        rules = [MaskRule(column="phone", function="nullify")]
        with patch.object(engine, "_get_rules", return_value=rules):
            result = engine.apply_masking(table, dataset="users", role="viewer")
        assert result.column("phone")[0].as_py() is None

    def test_hash_masking(self, table: pa.Table) -> None:
        from arrow_lake.quality.masking_engine import MaskingEngine, MaskRule

        with patch.dict("os.environ", {"ARROW_LAKE__MASKING__HMAC_KEY": "test-hmac-key-for-unit-tests-!!"}):
            engine = MaskingEngine(_cfg())
        rules = [MaskRule(column="email", function="hash")]
        with patch.object(engine, "_get_rules", return_value=rules):
            result = engine.apply_masking(table, dataset="users", role="viewer")
        hashed = result.column("email")[0].as_py()
        assert hashed != "a@b.com"
        assert len(hashed) == 32  # truncated HMAC-SHA256 (hexdigest[:32])

    def test_partial_masking(self, table: pa.Table) -> None:
        from arrow_lake.quality.masking_engine import MaskingEngine, MaskRule

        engine = MaskingEngine(_cfg())
        rules = [MaskRule(column="phone", function="partial")]
        with patch.object(engine, "_get_rules", return_value=rules):
            result = engine.apply_masking(table, dataset="users", role="viewer")
        masked = result.column("phone")[0].as_py()
        assert masked.startswith("12")
        assert masked.endswith("90")
        assert "*" in masked

    def test_no_rules_no_change(self, table: pa.Table) -> None:
        from arrow_lake.quality.masking_engine import MaskingEngine

        engine = MaskingEngine(_cfg())
        with patch.object(engine, "_get_rules", return_value=[]):
            result = engine.apply_masking(table, dataset="users", role="viewer")
        assert result == table


# ────────────────────────────────────────────────────────────
# StatsInjector
# ────────────────────────────────────────────────────────────

class TestStatsInjector:
    def test_get_hints_default(self) -> None:
        from arrow_lake.query.stats_injector import StatsInjector

        injector = StatsInjector(_cfg())
        with patch.object(injector, "_fetch_hints") as mock:
            from arrow_lake.query.stats_injector import QueryHints

            mock.return_value = QueryHints(estimated_rows=500000, column_count=8, size_mb=50.0)
            hints = injector.get_hints("articles")
            assert hints.estimated_rows == 500000

    def test_should_use_olap(self) -> None:
        from arrow_lake.query.stats_injector import StatsInjector

        injector = StatsInjector(_cfg(stats_auto_route_threshold=10000))
        with patch.object(injector, "_fetch_hints") as mock:
            from arrow_lake.query.stats_injector import QueryHints

            mock.return_value = QueryHints(estimated_rows=50000)
            assert injector.should_use_olap("big_table") is True

    def test_suggest_limit_with_explicit(self) -> None:
        from arrow_lake.query.stats_injector import StatsInjector

        injector = StatsInjector(_cfg())
        assert injector.suggest_limit("articles", requested_limit=100) == 100


# ────────────────────────────────────────────────────────────
# RegistryModelResolver
# ────────────────────────────────────────────────────────────

class TestRegistryModelResolver:
    def test_resolve_returns_uri(self) -> None:
        from arrow_lake.catalog.gravitino_models import ModelVersionInfo
        from arrow_lake.embed.registry_resolver import RegistryModelResolver

        resolver = RegistryModelResolver(_cfg())
        version = ModelVersionInfo(
            name="embed", version=2, uri="/models/v2", aliases=("production",)
        )
        with patch.object(
            resolver, "_fetch_production_uri", return_value="/models/v2"
        ):
            assert resolver.resolve_model_path("embed") == "/models/v2"

    def test_resolve_returns_none_when_not_found(self) -> None:
        from arrow_lake.embed.registry_resolver import RegistryModelResolver

        resolver = RegistryModelResolver(_cfg())
        with patch.object(resolver, "_fetch_production_uri", return_value=None):
            assert resolver.resolve_model_path("nonexistent") is None

    def test_invalidate_clears_cache(self) -> None:
        from arrow_lake.embed.registry_resolver import RegistryModelResolver

        resolver = RegistryModelResolver(_cfg(model_resolver_cache_ttl_seconds=9999))
        with patch.object(resolver, "_fetch_production_uri", return_value="/v1"):
            resolver.resolve_model_path("test")
        resolver.invalidate("test")
        with patch.object(resolver, "_fetch_production_uri", return_value="/v2") as mock:
            result = resolver.resolve_model_path("test")
            assert result == "/v2"
            mock.assert_called_once()


# ────────────────────────────────────────────────────────────
# FederatedQueryEngine
# ────────────────────────────────────────────────────────────

class TestFederatedQueryEngine:
    def test_resolve_table_invalid_fqn(self) -> None:
        from arrow_lake.query.federated_engine import FederatedQueryEngine

        engine = FederatedQueryEngine(_cfg())
        result = engine.resolve_table("a.b.c.d")
        assert result is None

    def test_resolve_table_success(self) -> None:
        from arrow_lake.query.federated_engine import FederatedQueryEngine

        engine = FederatedQueryEngine(_cfg())
        mock_response = json.dumps({
            "table": {
                "name": "articles",
                "properties": {"format": "parquet", "location": "s3://data/articles"},
                "columns": [{"name": "id", "type": "long"}],
            }
        }).encode()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = mock_response
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = engine.resolve_table("my-catalog.my-schema.articles")
            assert result is not None
            assert result.format == "parquet"
            assert result.location == "s3://data/articles"

    def test_resolve_single_name(self) -> None:
        from arrow_lake.query.federated_engine import FederatedQueryEngine

        engine = FederatedQueryEngine(_cfg())
        mock_response = json.dumps({
            "table": {
                "name": "articles",
                "properties": {"location": "s3://data/articles"},
                "columns": [],
            }
        }).encode()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = mock_response
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = engine.resolve_table("articles")
            assert result is not None
            assert result.catalog == "lance-catalog"


# ────────────────────────────────────────────────────────────
# TagAwareACLResolver
# ────────────────────────────────────────────────────────────

class TestTagAwareACLResolver:
    def test_sync_no_rules(self) -> None:
        from arrow_lake.catalog.tag_acl_resolver import TagAwareACLResolver

        config = _cfg(tag_access_rules={})
        checker = MagicMock()
        resolver = TagAwareACLResolver(config, checker)
        assert resolver.sync_tags_to_acls() == 0

    def test_sync_with_tagged_columns(self) -> None:
        from arrow_lake.catalog.tag_acl_resolver import TagAwareACLResolver

        config = _cfg(tag_access_rules={"pii": {"visible_to": ["admin"]}})
        checker = MagicMock()
        resolver = TagAwareACLResolver(config, checker)

        with patch.object(resolver, "_list_gravitino_tables", return_value=["users"]):
            with patch.object(
                resolver,
                "_fetch_column_tags",
                return_value={"email": ["pii"], "phone": ["pii"]},
            ):
                with patch.object(
                    resolver,
                    "_get_table_schema",
                    return_value=[
                        {"name": "id"},
                        {"name": "email"},
                        {"name": "phone"},
                        {"name": "name"},
                    ],
                ):
                    count = resolver.sync_tags_to_acls()
                    assert count > 0
                    checker.set_acl.assert_called()


# ────────────────────────────────────────────────────────────
# Config: new fields
# ────────────────────────────────────────────────────────────

class TestConfigV142:
    def test_new_fields_have_defaults(self) -> None:
        cfg = GravitinoConfig()
        assert cfg.retention_enforce_interval_seconds == 3600
        assert cfg.masking_policy_cache_ttl_seconds == 60
        # tag_acl_sync_interval_seconds was removed in the v1.10.x 配置精简.
        assert "pii" in cfg.tag_access_rules
        assert cfg.stats_cache_ttl_seconds == 300
        assert cfg.stats_auto_route_threshold == 1_000_000
        assert cfg.model_resolver_cache_ttl_seconds == 600
        # lineage_sync_to_gravitino was removed in the v1.10.x 配置精简 (dead field).
        assert cfg.federated_query_max_rows == 100_000

    def test_custom_values(self) -> None:
        cfg = _cfg(
            retention_enforce_interval_seconds=600,
            federated_query_max_rows=50000,
        )
        assert cfg.retention_enforce_interval_seconds == 600
        assert cfg.federated_query_max_rows == 50000
