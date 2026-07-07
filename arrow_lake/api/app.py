"""FastAPI application factory for Arrow Lake REST API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from arrow_lake._version import __version__
from arrow_lake.api.deps import get_config
from arrow_lake.api.errors import register_exception_handlers
from arrow_lake.api.routers.admin import router as admin_router
from arrow_lake.api.routers.audit import router as audit_router
from arrow_lake.api.routers.auth import router as auth_router
from arrow_lake.api.routers.backup import router as backup_router
from arrow_lake.api.routers.datasets import router as datasets_router
from arrow_lake.api.routers.embedding import embed_router
from arrow_lake.api.routers.embedding import router as embedding_router
from arrow_lake.api.routers.export import router as export_router
from arrow_lake.api.routers.gravitino import router as gravitino_router
from arrow_lake.api.routers.knowledge_graph import router as kg_router
from arrow_lake.api.routers.lineage import router as lineage_router
from arrow_lake.api.routers.maintenance import router as maintenance_router
from arrow_lake.api.routers.quality import router as quality_router
from arrow_lake.api.routers.query import router as query_router
from arrow_lake.api.routers.rag import router as rag_router
from arrow_lake.api.routers.search import router as search_router
from arrow_lake.api.routers.system import router as system_router
from arrow_lake.api.routers.async_tasks import router as async_tasks_router
from arrow_lake.config import ArrowLakeConfig

logger = logging.getLogger(__name__)

MIDDLEWARE_PIPELINE = [
    "correlation_id",
    "cors",
    "gzip",
    "metrics",
    "request_size_limit",
    "security_headers",
    "rate_limit",
    "api_key",
    "jwt_auth",
]


def _check_storage_connectivity(config: ArrowLakeConfig) -> None:
    """Verify S3/MinIO storage is reachable at startup."""
    from arrow_lake.config._enums import StorageBackend

    backend = config.storage.backend
    if backend == StorageBackend.LOCAL:
        return
    endpoint = config.storage.s3_endpoint
    bucket = config.storage.s3_bucket
    logger.info("Checking storage connectivity: %s @ %s (bucket=%s)", backend, endpoint, bucket)
    try:
        from arrow_lake.core.http import create_http_client

        with create_http_client(timeout=5.0) as client:
            resp = client.get(f"{endpoint}/minio/health/live")
        if resp.status_code == 200:
            logger.info("Storage health check passed")
            return
    except Exception:
        pass
    logger.warning(
        "Storage health check failed — endpoint %s may not be reachable. "
        "The API will start but storage operations may fail.",
        endpoint,
    )


def _check_duckdb_extensions() -> None:
    """Verify critical DuckDB extensions are available."""
    try:
        import duckdb

        conn = duckdb.connect(":memory:")
        try:
            conn.execute("INSTALL json; LOAD json;")
            conn.execute("SELECT 1;")
        finally:
            conn.close()
        logger.info("DuckDB extension check passed")
    except Exception as exc:
        logger.warning("DuckDB extension check failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialize Lake instance on startup, cleanup on shutdown."""
    import signal
    import threading

    from arrow_lake import Lake

    config: ArrowLakeConfig = app.state.config

    # Mark not-ready until required startup setup completes; readiness probes
    # (/health, /health/ready) return 503 while this is False so traffic is not
    # routed to a half-initialized worker.
    app.state.ready = False

    lake = Lake(base_uri=config.storage.base_uri, config=config)
    app.state.lake = lake

    # Create the session manager WITHOUT blocking on warmup, then run warmup in
    # a background daemon thread. The pool lazy-creates sessions on demand, so
    # readiness does not need to wait for warmup; this keeps startup off the
    # DuckDB extension install/load critical path.
    session_manager = lake.get_session_manager(skip_warmup=True)

    if getattr(config.olap, "warmup_enabled", False):
        def _bg_warmup() -> None:
            try:
                result = session_manager.warmup()
                if result.get("errors", 0) > 0:
                    logger.warning(
                        "duckdb_warmup_partial: warmed=%d, errors=%d",
                        result.get("warmed", 0),
                        result["errors"],
                    )
            except Exception:
                logger.warning("duckdb_warmup_failed", exc_info=True)

        threading.Thread(
            target=_bg_warmup, name="duckdb-warmup", daemon=True
        ).start()

    from arrow_lake.api.rbac import PermissionChecker
    app.state.checker = PermissionChecker()

    # ── v1.6.2: Redis-backed task state sharing ──
    from arrow_lake.api.tasks import TaskManager

    TaskManager.init_redis_store(config.redis)

    # Gravitino integration (optional — no-op when disabled)
    gravitino_sync: object | None = None
    retention_enforcer: object | None = None
    if config.gravitino.enabled:
        from arrow_lake.catalog.gravitino_auth import create_auth_provider
        from arrow_lake.catalog.gravitino_bridge import GravitinoBridge
        from arrow_lake.catalog.gravitino_models import GravitinoModelRegistry
        from arrow_lake.catalog.gravitino_sync import GravitinoSyncScheduler
        from arrow_lake.quality.gravitino_tags import GravitinoTagService

        gravitino_auth = create_auth_provider(config.gravitino)
        app.state.gravitino_auth_provider = gravitino_auth
        lake._gravitino_auth_provider = gravitino_auth

        bridge = GravitinoBridge(config.gravitino)
        app.state.gravitino_bridge = bridge
        app.state.gravitino_tag_service = GravitinoTagService(config.gravitino)
        app.state.gravitino_model_registry = GravitinoModelRegistry(config.gravitino)

        # ── v1.4.2: MaskingEngine ──
        from arrow_lake.quality.masking_engine import MaskingEngine

        masking_engine = MaskingEngine(config.gravitino)
        app.state.masking_engine = masking_engine
        app.state.checker.set_masking_engine(masking_engine)

        # ── v1.4.2: StatsInjector ──
        from arrow_lake.query.stats_injector import StatsInjector

        app.state.stats_injector = StatsInjector(config.gravitino)

        # ── v1.4.2: ModelResolver ──
        from arrow_lake.embed.registry_resolver import RegistryModelResolver

        app.state.model_resolver = RegistryModelResolver(config.gravitino)

        # ── v1.4.2: FederatedQueryEngine ──
        from arrow_lake.query.federated_engine import FederatedQueryEngine

        app.state.federated_engine = FederatedQueryEngine(config.gravitino)

        # ── v1.4.2: TagAwareACLResolver ──
        tag_acl_resolver = None
        if config.gravitino.tag_access_rules:
            from arrow_lake.catalog.tag_acl_resolver import TagAwareACLResolver

            tag_acl_resolver = TagAwareACLResolver(config.gravitino, app.state.checker)
            app.state.tag_acl_resolver = tag_acl_resolver

        # Start background sync using the Lake facade for catalog access
        gravitino_sync = GravitinoSyncScheduler(
            bridge=bridge,
            lake=lake,
            interval=config.gravitino.sync_interval_seconds,
            tag_acl_resolver=tag_acl_resolver,
        )
        gravitino_sync.start()

        # ── v1.4.2: RetentionEnforcer background thread ──
        from arrow_lake.quality.retention_enforcer import RetentionEnforcer

        retention_enforcer = RetentionEnforcer(config.gravitino, lake._storage)
        retention_enforcer.start()
        app.state.retention_enforcer = retention_enforcer

    # ── v1.4.3: MaintenanceScheduler ──
    maintenance_scheduler: object | None = None
    if config.storage.maintenance_enabled:
        from arrow_lake.ingest.maintenance_scheduler import MaintenanceScheduler

        maintenance_scheduler = MaintenanceScheduler(
            storage=lake._storage, config=config.storage,
        )
        maintenance_scheduler.start()
        app.state.maintenance_scheduler = maintenance_scheduler

        logger.info(
            "gravitino_integration_enabled",
            uri=config.gravitino.uri,
            metalake=config.gravitino.metalake,
            masking=True,
            stats=True,
            tag_acl=config.gravitino.tag_access_rules != {},
            retention=True,
        )

    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigint = signal.getsignal(signal.SIGINT)

    def _graceful_shutdown(signum: int, frame: Any) -> None:
        logger.info("Received signal %s, initiating graceful shutdown", signum)
        lake.shutdown()

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    # Required synchronous setup is complete — flip readiness so probes return 200.
    app.state.ready = True
    try:
        yield
    finally:
        # Stop routing traffic to this worker during shutdown.
        app.state.ready = False
        if gravitino_sync is not None:
            gravitino_sync.stop()
        if retention_enforcer is not None:
            retention_enforcer.stop()
        if maintenance_scheduler is not None:
            maintenance_scheduler.stop()
        TaskManager.shutdown_redis_store()
        lake.shutdown()
        signal.signal(signal.SIGTERM, original_sigterm)
        signal.signal(signal.SIGINT, original_sigint)


def _validate_auth_config(config: ArrowLakeConfig) -> None:
    """Reject startup when API server is enabled without authentication configured."""
    auth_mode = config.auth.auth_mode
    api_key_set = bool(config.api.api_key)
    jwt_set = bool(config.auth.jwt_secret_key or config.auth.jwt_public_key or config.auth.jwt_private_key)

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

    # --- Pure ASGI middleware (registered via @app.middleware) ---
    # These correctly propagate request.state between layers.

    from arrow_lake.api.middleware import (
        correlation_id_middleware_fn,
        metrics_middleware_fn,
        request_size_limit_middleware_fn,
        security_headers_middleware_fn,
    )

    # Correlation ID propagation — FIRST so all subsequent middleware
    # (including auth, rate limit) logs carry the request ID.
    auto_gen = config.api.auto_generate_request_id

    @app.middleware("http")
    async def correlation_id_middleware(request, call_next):
        return await correlation_id_middleware_fn(request, call_next, auto_generate=auto_gen)

    # Prometheus HTTP request duration
    @app.middleware("http")
    async def metrics_middleware(request, call_next):
        return await metrics_middleware_fn(request, call_next)

    # Request body size limit
    max_size = config.api.max_request_size_bytes

    @app.middleware("http")
    async def request_size_limit_middleware(request, call_next):
        return await request_size_limit_middleware_fn(request, call_next, max_size_bytes=max_size)

    # Security response headers
    if config.api.security_headers_enabled:
        csp = config.api.content_security_policy
        frame_opts = config.api.frame_options

        @app.middleware("http")
        async def security_headers_middleware(request, call_next):
            return await security_headers_middleware_fn(
                request, call_next,
                content_security_policy=csp,
                frame_options=frame_opts,
            )

    # Rate limiting (enabled by default via RateLimitConfig.enabled = True)
    if config.rate_limit.enabled:
        from arrow_lake.api.rate_limit import rate_limit_middleware_fn

        rl_rpm = config.rate_limit.default_requests_per_minute
        rl_burst = config.rate_limit.default_burst
        rl_exempt = config.rate_limit.exempt_paths
        rl_trusted_proxies = config.rate_limit.trusted_proxies

        @app.middleware("http")
        async def rate_limit_middleware(request, call_next):
            return await rate_limit_middleware_fn(
                request, call_next,
                rpm=rl_rpm,
                burst=rl_burst,
                exempt_paths=rl_exempt,
                trusted_proxies=rl_trusted_proxies,
            )

    # API Key middleware
    if config.api.api_key:
        from arrow_lake.api.auth import api_key_middleware_fn

        @app.middleware("http")
        async def api_key_middleware(request, call_next):
            return await api_key_middleware_fn(
                request, call_next,
                api_key=config.api.api_key,
                header_name=config.api.api_key_header,
                docs_enabled=config.api.docs_enabled,
                default_role=config.api.api_key_default_role,
            )

    # JWT authentication
    auth_mode = config.auth.auth_mode
    if auth_mode in ("jwt", "both") and config.auth.jwt_secret_key:
        from arrow_lake.api.auth_service import AuthService
        from arrow_lake.api.jwt_auth import jwt_auth_middleware_fn

        svc = AuthService(
            secret_key=config.auth.jwt_secret_key,
            algorithm=config.auth.jwt_algorithm,
            public_key=config.auth.jwt_public_key,
            private_key=config.auth.jwt_private_key,
            access_token_minutes=config.auth.jwt_access_token_minutes,
            refresh_token_days=config.auth.jwt_refresh_token_days,
            issuer=config.auth.jwt_issuer,
        )
        app.state.auth_service = svc

        # Wire Redis-backed JWT blacklist when available
        if config.redis.enabled:
            try:
                from arrow_lake.query._redis_semaphore import _redis_module

                if _redis_module is not None:
                    redis_kwargs: dict[str, Any] = {
                        "max_connections": config.redis.redis_pool_size,
                        "socket_timeout": 5,
                    }
                    if config.redis.password:
                        redis_kwargs["password"] = config.redis.password
                    if config.redis.ssl:
                        redis_kwargs["ssl"] = True
                    redis_client = _redis_module.Redis.from_url(
                        config.redis.url, **redis_kwargs
                    )
                    redis_client.ping()
                    svc.set_redis(redis_client)
            except Exception:
                pass  # Non-fatal: falls back to in-memory blacklist

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
    app.include_router(maintenance_router)

    # Gravitino metadata router (always registered; returns 503 when disabled)
    if config.gravitino.enabled:
        app.include_router(gravitino_router)

    # Async task endpoints (fire-and-forget for heavy operations)
    app.include_router(async_tasks_router)

    # Arrow Lake Console — SQL Worksheet static frontend (same-origin mount).
    # Serves console/{login,olap,index}.html; pages call /api/v1/.../query/olap
    # (RBAC + validate_sql_safety + row-level ACL). See
    # docs/architecture-design/duckdb-sql-worksheet.md.
    from pathlib import Path

    from starlette.staticfiles import StaticFiles

    # app.py 位于 <repo>/arrow_lake/api/app.py(镜像内 /app/arrow_lake/api/app.py);
    # console 在 <repo>/console(镜像内 /app/console)→ 向上 3 级(parents[2])。
    _console_dir = Path(__file__).resolve().parents[2] / "console"
    if _console_dir.is_dir():
        app.mount("/console", StaticFiles(directory=str(_console_dir), html=True), name="console")

    # OpenTelemetry (optional — no-op when disabled or deps not installed)
    if config.opentelemetry.enabled:
        from arrow_lake.api.telemetry import setup_telemetry
        setup_telemetry(config.opentelemetry, app=app)

    return app
