"""Cover missing lines in embed/registry_resolver.py — cache, fetch, invalidate."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.config.gravitino import GravitinoConfig
from arrow_lake.embed.registry_resolver import RegistryModelResolver


def _cfg(**overrides: object) -> GravitinoConfig:
    defaults = {"enabled": True, "uri": "http://g:8090", "metalake": "ml"}
    defaults.update(overrides)
    return GravitinoConfig(**defaults)


# ── resolve_model_path ──


class TestResolveModelPath:
    def test_cache_hit_within_ttl(self) -> None:
        resolver = RegistryModelResolver(_cfg(model_resolver_cache_ttl_seconds=600))
        with resolver._lock:
            resolver._path_cache["embed_v1"] = ("/models/embed_v1", time.time())

        result = resolver.resolve_model_path("embed_v1")
        assert result == "/models/embed_v1"

    def test_cache_miss_fetches(self) -> None:
        resolver = RegistryModelResolver(_cfg(model_resolver_cache_ttl_seconds=600))
        with patch.object(resolver, "_fetch_production_uri", return_value="/models/new"):
            result = resolver.resolve_model_path("new_model")
        assert result == "/models/new"

    def test_cache_expired_refetches(self) -> None:
        resolver = RegistryModelResolver(_cfg(model_resolver_cache_ttl_seconds=60))
        with resolver._lock:
            resolver._path_cache["m1"] = ("/old/path", time.time() - 100)
        with patch.object(resolver, "_fetch_production_uri", return_value="/new/path"):
            result = resolver.resolve_model_path("m1")
        assert result == "/new/path"


# ── resolve_model_config ──


class TestResolveModelConfig:
    def test_cache_hit_within_ttl(self) -> None:
        resolver = RegistryModelResolver(_cfg(model_resolver_cache_ttl_seconds=600))
        cfg_data = {"api_base": "http://llm:8000", "model": "gpt-4"}
        with resolver._lock:
            resolver._config_cache["m1"] = (cfg_data, time.time())

        result = resolver.resolve_model_config("m1")
        assert result == cfg_data

    def test_cache_miss_fetches(self) -> None:
        resolver = RegistryModelResolver(_cfg(model_resolver_cache_ttl_seconds=600))
        cfg_data = {"model": "test"}
        with patch.object(resolver, "_fetch_production_config", return_value=cfg_data):
            result = resolver.resolve_model_config("m1")
        assert result == cfg_data

    def test_cache_expired_refetches(self) -> None:
        resolver = RegistryModelResolver(_cfg(model_resolver_cache_ttl_seconds=60))
        old_cfg = {"model": "old"}
        new_cfg = {"model": "new"}
        with resolver._lock:
            resolver._config_cache["m1"] = (old_cfg, time.time() - 100)
        with patch.object(resolver, "_fetch_production_config", return_value=new_cfg):
            result = resolver.resolve_model_config("m1")
        assert result == new_cfg


# ── invalidate ──


class TestInvalidate:
    def test_invalidate_clears_both_caches(self) -> None:
        resolver = RegistryModelResolver(_cfg())
        with resolver._lock:
            resolver._path_cache["m1"] = ("/path", time.time())
            resolver._config_cache["m1"] = ({"k": "v"}, time.time())
        resolver.invalidate("m1")
        assert "m1" not in resolver._path_cache
        assert "m1" not in resolver._config_cache

    def test_invalidate_nonexistent_is_noop(self) -> None:
        resolver = RegistryModelResolver(_cfg())
        resolver.invalidate("no_such_model")  # Should not raise


# ── _fetch_production_uri ──


class TestFetchProductionUri:
    def test_fetch_returns_uri(self) -> None:
        resolver = RegistryModelResolver(_cfg())
        mock_version = MagicMock()
        mock_version.uri = "/models/embed_v1"
        mock_version.version = 3

        mock_registry = MagicMock()
        mock_registry.get_production_version.return_value = mock_version

        with patch("arrow_lake.catalog.gravitino_models.GravitinoModelRegistry", return_value=mock_registry):
            result = resolver._fetch_production_uri("embed_v1")
        assert result == "/models/embed_v1"

    def test_fetch_no_version_returns_none(self) -> None:
        resolver = RegistryModelResolver(_cfg())
        mock_registry = MagicMock()
        mock_registry.get_production_version.return_value = None

        with patch("arrow_lake.catalog.gravitino_models.GravitinoModelRegistry", return_value=mock_registry):
            result = resolver._fetch_production_uri("missing_model")
        assert result is None

    def test_fetch_exception_returns_none(self) -> None:
        resolver = RegistryModelResolver(_cfg())

        with patch("arrow_lake.catalog.gravitino_models.GravitinoModelRegistry", side_effect=RuntimeError("fail")):
            result = resolver._fetch_production_uri("m1")
        assert result is None


# ── _fetch_production_config ──


class TestFetchProductionConfig:
    def test_fetch_returns_properties(self) -> None:
        resolver = RegistryModelResolver(_cfg())
        mock_version = MagicMock()
        mock_version.properties = {"api_base": "http://llm:8000", "model": "gpt-4"}

        mock_registry = MagicMock()
        mock_registry.get_production_version.return_value = mock_version

        with patch("arrow_lake.catalog.gravitino_models.GravitinoModelRegistry", return_value=mock_registry):
            result = resolver._fetch_production_config("m1")
        assert result == {"api_base": "http://llm:8000", "model": "gpt-4"}

    def test_fetch_no_properties_returns_none(self) -> None:
        resolver = RegistryModelResolver(_cfg())
        mock_version = MagicMock()
        mock_version.properties = None

        mock_registry = MagicMock()
        mock_registry.get_production_version.return_value = mock_version

        with patch("arrow_lake.catalog.gravitino_models.GravitinoModelRegistry", return_value=mock_registry):
            result = resolver._fetch_production_config("m1")
        assert result is None

    def test_fetch_no_version_returns_none(self) -> None:
        resolver = RegistryModelResolver(_cfg())
        mock_registry = MagicMock()
        mock_registry.get_production_version.return_value = None

        with patch("arrow_lake.catalog.gravitino_models.GravitinoModelRegistry", return_value=mock_registry):
            result = resolver._fetch_production_config("m1")
        assert result is None

    def test_fetch_exception_returns_none(self) -> None:
        resolver = RegistryModelResolver(_cfg())

        with patch("arrow_lake.catalog.gravitino_models.GravitinoModelRegistry", side_effect=RuntimeError("fail")):
            result = resolver._fetch_production_config("m1")
        assert result is None
