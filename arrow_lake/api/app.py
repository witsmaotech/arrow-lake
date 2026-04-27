"""FastAPI application factory for Arrow Lake REST API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from arrow_lake._version import __version__
from arrow_lake.api.deps import get_config
from arrow_lake.api.errors import register_exception_handlers
from arrow_lake.api.middleware import (
    MetricsMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from arrow_lake.api.rate_limit import RateLimitMiddleware
from arrow_lake.api.routers.admin import router as admin_router
from arrow_lake.api.routers.audit import router as audit_router
from arrow_lake.api.routers.auth import router as auth_router
from arrow_lake.api.routers.backup import router as backup_router
from arrow_lake.api.routers.datasets import router as datasets_router
from arrow_lake.api.routers.embedding import embed_router
from arrow_lake.api.routers.embedding import router as embedding_router
from arrow_lake.api.routers.export import router as export_router
from arrow_lake.api.routers.knowledge_graph import router as kg_router
from arrow_lake.api.routers.lineage import router as lineage_router
from arrow_lake.api.routers.quality import router as quality_router
from arrow_lake.api.routers.query import router as query_router
from arrow_lake.api.routers.rag import router as rag_router
from arrow_lake.api.routers.search import router as search_router
from arrow_lake.api.routers.system import router as system_router
from arrow_lake.config import ArrowLakeConfig


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialize Lake instance on startup, cleanup on shutdown."""
    from arrow_lake import Lake

    config: ArrowLakeConfig = app.state.config
    lake = Lake(base_uri=config.storage.base_uri, config=config)
    app.state.lake = lake
    yield
    # Shutdown: close auth service, LLM providers, session managers
    auth_svc = getattr(app.state, "auth_service", None)
    if auth_svc is not None:
        pass  # AuthService has no closeable resources currently
    # Flush metrics on shutdown if enabled
    try:
        from arrow_lake.core.metrics import flush_metrics
        flush_metrics()
    except Exception:
        pass


logger = logging.getLogger(__name__)


def _validate_auth_config(config: ArrowLakeConfig) -> None:
    """Reject startup when API server is enabled without authentication configured."""
    auth_mode = config.auth.auth_mode
    api_key_set = bool(config.api.api_key)
    jwt_set = bool(config.auth.jwt_secret_key)

    if auth_mode == "api_key" and not api_key_set:
        raise ValueError(
            "API server enabled with auth_mode='api_key' but api_key is empty. "
            "Set api.api_key in config or disable the API server."
        )
    if auth_mode == "jwt" and not jwt_set:
        raise ValueError(
            "API server enabled with auth_mode='jwt' but jwt_secret_key is empty. "
            "Set auth.jwt_secret_key in config or disable the API server."
        )
    if auth_mode == "both" and not (api_key_set or jwt_set):
        raise ValueError(
            "API server enabled with auth_mode='both' but both api_key and jwt_secret_key are empty. "
            "Configure at least one authentication method."
        )

    if getattr(config, "audit", None) and config.audit.enabled and not config.audit.hmac_secret_key:
        raise ValueError(
            "Audit trail enabled but hmac_secret_key is empty. "
            "Set audit.hmac_secret_key to a strong random value."
        )


def create_app(config: ArrowLakeConfig | None = None) -> FastAPI:
    """Create and configure the Arrow Lake FastAPI application.

    Args:
        config: Optional config override. If None, loads from env/YAML.

    Returns:
        Configured FastAPI application instance.
    """
    if config is None:
        config = get_config()

    docs_url = "/docs" if config.api.docs_enabled else None
    redoc_url = "/redoc" if config.api.docs_enabled else None
    openapi_url = "/openapi.json" if config.api.docs_enabled else None

    app = FastAPI(
        title="Arrow Lake REST API",
        description="Unified multimodal data lakehouse REST API",
        version=__version__,
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        openapi_tags=[
            {"name": "system", "description": "Health, metrics, OpenAPI spec"},
            {"name": "datasets", "description": "Dataset management & ingestion"},
            {"name": "search", "description": "Vector, FTS, hybrid, faceted, ensemble search"},
            {"name": "query", "description": "OLAP SQL, metadata, and Daft queries"},
            {"name": "quality", "description": "Quality filtering & deduplication"},
            {"name": "embedding", "description": "Embedding computation & index management"},
            {"name": "export", "description": "Data export"},
            {"name": "lineage", "description": "Data lineage tracking"},
            {"name": "audit", "description": "Audit trail management"},
            {"name": "rag", "description": "RAG query, streaming, entity extraction"},
            {"name": "kg", "description": "Knowledge graph build, query, and GraphRAG"},
            {"name": "auth", "description": "JWT authentication and token management"},
        ],
    )

    # Store config for lifespan access
    app.state.config = config

    # --- Auth enforcement ---
    if config.api.enabled:
        _validate_auth_config(config)

    # CORS — restrict methods and headers to safe defaults
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.api.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
    )

    # Exception handlers (before middleware to catch errors)
    register_exception_handlers(app)

    # GZip compression (Starlette built-in)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Prometheus HTTP request duration
    app.add_middleware(MetricsMiddleware)

    # Request body size limit
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_size_bytes=config.api.max_request_size_bytes,
    )

    # Security response headers
    if config.api.security_headers_enabled:
        app.add_middleware(
            SecurityHeadersMiddleware,
            content_security_policy=config.api.content_security_policy,
            frame_options=config.api.frame_options,
        )

    # Rate limiting (optional — disabled by default)
    if config.rate_limit.enabled:
        app.add_middleware(
            RateLimitMiddleware,
            rpm=config.rate_limit.default_requests_per_minute,
            burst=config.rate_limit.default_burst,
            exempt_paths=config.rate_limit.exempt_paths,
        )

    # API Key middleware (pure ASGI — correctly propagates request.state)
    if config.api.api_key:
        @app.middleware("http")
        async def api_key_middleware(request, call_next):
            from arrow_lake.api.auth import api_key_middleware_fn
            return await api_key_middleware_fn(
                request, call_next,
                api_key=config.api.api_key,
                header_name=config.api.api_key_header,
                docs_enabled=config.api.docs_enabled,
                default_role=config.api.api_key_default_role,
            )

    # --- Pure ASGI middleware (registered via @app.middleware) ---
    # These correctly propagate request.state between layers.

    # Correlation ID propagation (before auth)
    auto_gen = config.api.auto_generate_request_id

    @app.middleware("http")
    async def correlation_id_middleware(request, call_next):
        from arrow_lake.api.middleware import correlation_id_middleware_fn
        return await correlation_id_middleware_fn(request, call_next, auto_generate=auto_gen)

    # JWT authentication
    auth_mode = config.auth.auth_mode
    if auth_mode in ("jwt", "both") and config.auth.jwt_secret_key:
        from arrow_lake.api.auth_service import AuthService
        from arrow_lake.api.jwt_auth import jwt_auth_middleware_fn

        svc = AuthService(
            secret_key=config.auth.jwt_secret_key,
            algorithm=config.auth.jwt_algorithm,
            access_token_minutes=config.auth.jwt_access_token_minutes,
            refresh_token_days=config.auth.jwt_refresh_token_days,
            issuer=config.auth.jwt_issuer,
        )
        app.state.auth_service = svc

        @app.middleware("http")
        async def jwt_auth_middleware(request, call_next):
            return await jwt_auth_middleware_fn(
                request, call_next, auth_service=svc,
                docs_enabled=config.api.docs_enabled,
            )

    app.include_router(system_router)
    app.include_router(datasets_router)
    app.include_router(search_router)
    app.include_router(query_router)
    app.include_router(export_router)
    app.include_router(quality_router)
    app.include_router(embedding_router)
    app.include_router(embed_router)
    app.include_router(lineage_router)
    app.include_router(audit_router)
    app.include_router(backup_router)
    app.include_router(rag_router)
    app.include_router(kg_router)
    app.include_router(auth_router)
    app.include_router(admin_router)

    # OpenTelemetry (optional — no-op when disabled or deps not installed)
    if config.opentelemetry.enabled:
        from arrow_lake.api.telemetry import setup_telemetry
        setup_telemetry(config.opentelemetry, app=app)

    return app
