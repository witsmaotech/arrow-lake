"""Model registry resolver — bridges Gravitino Model Catalog to embed/rag modules."""

from __future__ import annotations

import threading
import time

import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)


class RegistryModelResolver:
    """Resolves model paths and configs from Gravitino Model Catalog with TTL caching.

    Allows ``embed/encoder.py`` and ``rag/provider.py`` to load the current production
    model version from Gravitino instead of hardcoding model names in configuration.
    """

    def __init__(self, config: GravitinoConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        # Cache: {model_name: (path_or_config, timestamp)}
        self._path_cache: dict[str, tuple[str | None, float]] = {}
        self._config_cache: dict[str, tuple[dict[str, str] | None, float]] = {}
        self._ttl = config.model_resolver_cache_ttl_seconds

    def resolve_model_path(self, model_name: str) -> str | None:
        """Get the production version URI as a model path (e.g., for SentenceTransformer)."""
        with self._lock:
            cached = self._path_cache.get(model_name)
            if cached is not None:
                path, ts = cached
                if time.time() - ts < self._ttl:
                    return path

        path = self._fetch_production_uri(model_name)
        with self._lock:
            self._path_cache[model_name] = (path, time.time())
        return path

    def resolve_model_config(self, model_name: str) -> dict[str, str] | None:
        """Get model version properties (api_base, model, etc.)."""
        with self._lock:
            cached = self._config_cache.get(model_name)
            if cached is not None:
                cfg, ts = cached
                if time.time() - ts < self._ttl:
                    return cfg

        cfg = self._fetch_production_config(model_name)
        with self._lock:
            self._config_cache[model_name] = (cfg, time.time())
        return cfg

    def invalidate(self, model_name: str) -> None:
        """Clear cached model info so next call re-fetches from Gravitino."""
        with self._lock:
            self._path_cache.pop(model_name, None)
            self._config_cache.pop(model_name, None)

    # ── internal ──

    def _fetch_production_uri(self, model_name: str) -> str | None:
        try:
            from arrow_lake.catalog.gravitino_models import GravitinoModelRegistry

            registry = GravitinoModelRegistry(self._config)
            version = registry.get_production_version(model_name)
            if version:
                logger.debug("registry_resolver.resolved",
                             model=model_name, uri=version.uri, version=version.version)
                return version.uri
        except Exception:
            logger.debug("registry_resolver.resolve_failed",
                         model=model_name, exc_info=True)
        return None

    def _fetch_production_config(self, model_name: str) -> dict[str, str] | None:
        try:
            from arrow_lake.catalog.gravitino_models import GravitinoModelRegistry

            registry = GravitinoModelRegistry(self._config)
            version = registry.get_production_version(model_name)
            if version and version.properties:
                return dict(version.properties)
        except Exception:
            logger.debug("registry_resolver.config_failed",
                         model=model_name, exc_info=True)
        return None
