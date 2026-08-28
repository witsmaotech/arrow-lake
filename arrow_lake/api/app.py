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
from arrow_lake.api.routers.extraction_templates import router as extraction_templates_router
from arrow_lake.api.routers.doc_type_categories import router as doc_type_categories_router
from arrow_lake.api.routers.lineage import router as lineage_router
from arrow_lake.api.routers.materialized import router as materialized_router
from arrow_lake.api.routers.maintenance import router as maintenance_router
from arrow_lake.api.routers.actions import router as actions_router
from arrow_lake.api.routers.contracts import router as contracts_router
from arrow_lake.api.routers.objects import router as objects_router
from arrow_lake.api.routers.semantic import router as semantic_router
from arrow_lake.api.routers.ontology import router as ontology_router
from arrow_lake.api.routers.quality import router as quality_router
from arrow_lake.api.routers.cleaning import router as cleaning_router
from arrow_lake.api.routers.query import router as query_router
from arrow_lake.api.routers.rag import router as rag_router
from arrow_lake.api.routers.search import router as search_router
from arrow_lake.api.routers.system import router as system_router
from arrow_lake.api.routers.user_state import router as user_state_router
from arrow_lake.api.routers.async_tasks import router as async_tasks_router
from arrow_lake.config import ArrowLakeConfig

logger = logging.getLogger(__name__)

# Outermost → innermost (Starlette: last registered = outermost). P0-4
# (2026-08-21): rate_limit wraps the auth middlewares so 401 short-circuits
# still count against the limit — brute-force attempts can no longer bypass
# rate limiting by sending bad credentials.
MIDDLEWARE_PIPELINE = [
    "cors",               # outermost (registered last)
    "catch_unhandled",
    "rate_limit",         # P0-4: outside auth — counts failed-auth attempts
    "jwt_auth",
    "api_key",
    "security_headers",
    "request_size_limit",
    "gzip",
    "metrics",
    "correlation_id",
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
    import threading

    from arrow_lake import Lake

    import logging
    import sys
    # [#KG-0entity-debug] Surface ONLY he_extractor's stdlib DEBUG logs. he_extractor
    # uses logging.getLogger + positional args (kwarg-free → safe to lower). We do NOT
    # lower the whole arrow_lake tree: the structlog-kwarg loggers (system_db url=/
    # version=, rbac key=, …) crash stdlib _log if emitted below WARNING. Targeting the
    # single logger avoids that while showing he_extractor feed/parse/entity activity.
    # [#KG-0entity-debug] Surface ONLY he_extractor's stdlib DEBUG logs. Attach the
    # DEBUG handler to the he_extractor logger itself — NOT root — so other debug
    # noise (gravitino sync cycles, etc.) is not leaked to stdout under INFO. The
    # structlog-kwarg loggers (system_db url=/version=, rbac key=, …) crash stdlib
    # _log if emitted below WARNING, which is why we never lower the whole tree.
    _he_logger = logging.getLogger("arrow_lake.knowledge_graph.he_extractor")
    if not any(getattr(_h, "_al_dbg", False) for _h in _he_logger.handlers):
        _h = logging.StreamHandler(sys.stdout)
        _h.setLevel(logging.DEBUG)
        _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        _h._al_dbg = True
        _he_logger.addHandler(_h)
    _he_logger.setLevel(logging.DEBUG)

    config: ArrowLakeConfig = app.state.config

    # Mark not-ready until required startup setup completes; readiness probes
    # (/health, /health/ready) return 503 while this is False so traffic is not
    # routed to a half-initialized worker.
    app.state.ready = False

    lake = Lake(base_uri=config.storage.base_uri, config=config)
    app.state.lake = lake

    # v1.x 系统表命名规范迁移: _ 前缀 → sys_ 前缀(系统运行表与 _quality_* 临时表命名空间分离)。
    # 启动时 best-effort rename 旧名表;旧存在且新不存在才迁,幂等,失败不阻断启动。
    for _old, _new in (("_audit_trail", "sys_audit_trail"), ("_lineage_events", "sys_lineage_events")):
        try:
            _names = lake.list_datasets()
            if _old in _names and _new not in _names:
                lake.rename_dataset(_old, _new)
                logger.info("system_table_renamed", **{"from": _old, "to": _new})
        except Exception as _e:  # noqa: BLE001 — 多 worker 并发迁移:落败者撞源表已被迁走
            _msg = str(_e).lower()
            if "not found" in _msg or "exist" in _msg:
                logger.debug("system_table_rename_skipped", old=_old, reason=str(_e)[:80])
            else:
                logger.warning("system_table_rename_failed", old=_old, exc_info=True)

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

    # ── v1.9.0: system_db (libSQL) — unified control-plane persistence ──
    # RBAC / identity / (later) catalog / tasks / lineage index. When enabled,
    # build the connection, run migrations, seed the role matrix, and inject the
    # stores into the domain objects. Disabled → original in-memory behavior.
    sys_db = None
    if getattr(config.system_db, "enabled", False):
        from arrow_lake.api.rbac import _ROLE_PERMISSIONS
        from arrow_lake.system_db import Migrator, SystemDB
        from arrow_lake.system_db.stores import IdentityStore, RbacStore

        # SystemDB.__init__ raises SystemDBError on connect failure → lifespan
        # fails fast (fail-close: no control-plane DB, no authenticated API).
        sys_db = SystemDB(
            config.system_db.url,
            auth_token=config.system_db.auth_token,
            connect_timeout_seconds=config.system_db.connect_timeout_seconds,
        )
        Migrator(sys_db, config.system_db.migrations_dir or None).run()
        rbac_store = RbacStore(
            sys_db,
            cache_ttl=config.system_db.acl_cache_ttl_seconds,
            serve_stale=config.system_db.serve_stale_on_error,
        )
        rbac_store.seed_role_permissions(_ROLE_PERMISSIONS)
        # Warm the role-permission cache so role-based checks survive a sqld
        # outage via the serve-stale fallback (last-known decision served).
        for role in _ROLE_PERMISSIONS:
            rbac_store.get_role_permissions(role)
        identity_store = IdentityStore(sys_db)
        app.state.checker.set_system_store(rbac_store)
        app.state.system_db = sys_db
        app.state.rbac_store = rbac_store
        app.state.identity_store = identity_store

        # AuthService for JWT verification — get_current_user reads
        # app.state.auth_service (Bearer fallback path; without this, password
        # login issues a JWT but downstream /tasks, /me etc. return 403).
        from arrow_lake.api.auth_service import AuthService

        auth_cfg = config.auth
        # v1.10.5 M0: reuse the create_app instance when present (instead of
        # replacing it) so the Redis blacklist wiring done there survives, and
        # wire the per-user token cutoff provider (token_valid_after).
        svc = getattr(app.state, "auth_service", None)
        if svc is None:
            svc = AuthService(
                secret_key=auth_cfg.jwt_secret_key,
                algorithm=auth_cfg.jwt_algorithm,
                public_key=auth_cfg.jwt_public_key,
                private_key=auth_cfg.jwt_private_key,
                access_token_minutes=auth_cfg.jwt_access_token_minutes,
                refresh_token_days=auth_cfg.jwt_refresh_token_days,
                issuer=auth_cfg.jwt_issuer,
                audience=auth_cfg.jwt_audience,
                require_audience=auth_cfg.jwt_require_audience,
            )
            app.state.auth_service = svc

        def _token_valid_after(sub: str) -> float | None:
            if not sub.isdigit():
                return None  # shared api-user / anonymous identities have no row
            # B-2: propagate store failures — AuthService decides fail-open vs
            # fail-closed based on auth_tva_fail_open (default fail-closed).
            return identity_store.get_token_valid_after(int(sub))

        svc.set_token_valid_after_provider(
            _token_valid_after,
            fail_open=getattr(auth_cfg, "auth_tva_fail_open", False),
        )

        # P1 stores: durable task history (fully wired), catalog / DLQ /
        # RAG-session stores (instantiated on app.state; their facade
        # construction-site injection is a follow-up).
        from arrow_lake.system_db.stores import (
            CatalogStore,
            ContractStore,
            IngestDLQStore,
            RagSessionStore,
            TaskHistoryStore,
        )

        task_history_store = TaskHistoryStore(sys_db)
        app.state.catalog_store = CatalogStore(sys_db)
        app.state.contract_store = ContractStore(sys_db)
        app.state.ingest_dlq_store = IngestDLQStore(sys_db)
        app.state.rag_session_store = RagSessionStore(sys_db)
        app.state.task_history_store = task_history_store
        # v1.9.0 P2: lineage adjacency index + governance history.
        from arrow_lake.system_db.stores import GovernanceStore, LineageIndexStore

        app.state.lineage_index_store = LineageIndexStore(sys_db)
        app.state.governance_store = GovernanceStore(sys_db)
        from arrow_lake.system_db.stores import UserStateStore

        app.state.user_state_store = UserStateStore(sys_db)
        # v1.10.0: user extraction-template metadata + per-dataset bindings.
        from arrow_lake.system_db.stores.extraction_templates import ExtractionTemplateStore

        app.state.extraction_template_store = ExtractionTemplateStore(sys_db)
        # v1.10.0 M4: extraction-template quality-validation run history.
        from arrow_lake.system_db.stores.template_quality_runs import TemplateQualityRunStore

        app.state.template_quality_store = TemplateQualityRunStore(sys_db)
        # v1.10.0 M5: dynamic doc_type ↔ template-category dictionary. Seeded
        # once from the code-level taxonomy (DOC_TYPE_ALIASES/DESCRIPTIONS);
        # admin-added customs are source='custom'. Enables runtime category
        # extension without a code change (template category + ingest doc_type).
        from arrow_lake.system_db.stores.doc_type_categories import DocTypeCategoryStore

        doc_type_store = DocTypeCategoryStore(sys_db)
        doc_type_store.seed_if_empty()
        app.state.doc_type_category_store = doc_type_store
        lake._doc_type_category_store = doc_type_store
        # v1.11.0 MS1 (F1.2/F1.4): ontology version snapshots (written by the
        # kg_build finisher) + the rules registry behind /api/v1/ontology.
        from arrow_lake.system_db.stores.ontology import (
            OntologyRulesStore,
            OntologyVersionStore,
        )

        app.state.ontology_store = OntologyVersionStore(sys_db)
        app.state.ontology_rules_store = OntologyRulesStore(sys_db)
        lake._ontology_store = app.state.ontology_store
        # v1.11.1 MS2 (W2.1/F2.1): entity map (source id → object id) behind
        # /api/v1/objects/entity-map; explicitly maintained, never on ingest.
        from arrow_lake.system_db.stores.entity_map import EntityMapStore

        app.state.entity_map_store = EntityMapStore(sys_db)
        lake._entity_map_store = app.state.entity_map_store
        # v1.11.1 MS2 (W3.2/F2.2): semantic alignment configs behind
        # /api/v1/semantic (unit/value_map closed transforms; query-view only,
        # never on the ingest hot path).
        from arrow_lake.system_db.stores.semantic_alignments import (
            SemanticAlignmentStore,
        )

        app.state.semantic_alignment_store = SemanticAlignmentStore(sys_db)
        lake._semantic_alignment_store = app.state.semantic_alignment_store
        # v1.11.2 MS3 (W2.1/W2.2, F3.3/S5): actions catalog version chain +
        # idempotency dedup + scenario registry behind /api/v1/actions; the
        # execution middleware (W4) consumes idempotency/audit via app.state.
        from arrow_lake.system_db.stores.actions import (
            ActionCatalogStore,
            IdempotencyStore,
        )
        from arrow_lake.system_db.stores.scenarios import ScenarioStore

        app.state.action_store = ActionCatalogStore(sys_db)
        app.state.idempotency_store = IdempotencyStore(sys_db)
        app.state.scenario_store = ScenarioStore(sys_db)
        lake._action_store = app.state.action_store
        lake._scenario_store = app.state.scenario_store
        # Activate RAG-session persistence in the Lake facade's RAG pipeline.
        lake._rag_session_store = app.state.rag_session_store
        # Activate the lineage adjacency index in the Lake facade's LineageStore.
        lake._lineage_index_store = app.state.lineage_index_store
        # Activate governance history (schema changes; maintenance wired separately).
        lake._governance_store = app.state.governance_store
        # Cascade-delete hooks: Lake.delete_dataset(cascade=True) reaches these
        # stores to reclaim per-dataset catalog metadata, RBAC grants/denies,
        # and extraction-template bindings. Absent on the CLI Lake (no lifespan)
        # → getattr-None-guard in the cascade helper skips them gracefully.
        lake._catalog_store = app.state.catalog_store
        lake._contract_store = app.state.contract_store
        lake._rbac_store = app.state.rbac_store
        lake._extraction_template_store = app.state.extraction_template_store
        # Wire TaskManager durable history (Redis still holds real-time state)
        from arrow_lake.api.tasks import TaskManager

        TaskManager.init_history_store(task_history_store)
        TaskManager.init_user_state_store(app.state.user_state_store)

        logger.info(
            "system_db_enabled",
            url=config.system_db.url,
            fail_mode=config.system_db.fail_mode,
        )

    # ── v1.6.2: Redis-backed task state sharing ──
    from arrow_lake.api.tasks import TaskManager

    TaskManager.init_redis_store(config.redis)

    # Reclaim tasks orphaned by a previous worker lifetime (reload/recycle/
    # crash): a fresh process cannot still be executing them. Without this they
    # stay "running" forever and the console's ingest de-dup guard blocks the
    # next incremental ingest.
    try:
        reaped = TaskManager.reap_orphaned_tasks()
        if reaped:
            logger.info("TaskManager: reaped %d orphaned task(s) at startup", reaped)
    except Exception as exc:  # noqa: BLE001 — best-effort; never block startup
        logger.warning("TaskManager: startup reap failed: %s", exc)

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
            governance_store=getattr(app.state, "governance_store", None),
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

    # v1.10.7 WP4 (review H7): no signal handlers here. Installing our own
    # SIGTERM/SIGINT handler replaced uvicorn/gunicorn's graceful-stop
    # handler, so the worker never stopped, gunicorn waited out its timeout
    # and SIGKILLed it — lifespan finally never ran and in-flight requests
    # died. Cleanup below (lifespan finally) is what uvicorn runs on a
    # graceful stop; Lake.shutdown() is idempotent if anything else raced it.

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
        if sys_db is not None:
            sys_db.close()
        TaskManager.shutdown_redis_store()
        lake.shutdown()


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

    # [log-noise] structlog ships with a no-op default (PrintLogger to stdout, NO
    # level filtering). Left unconfigured — which it was until now — every debug()
    # leaks to stdout regardless of log_level, so the 30s gravitino sync cycles
    # (table_exists/fileset_exists/skipped, ~50 lines/cycle) flooded the logs.
    # Bind a filtering wrapper to observability.log_level so INFO (default) actually
    # suppresses debug, while keeping the familiar console renderer (no JSON
    # migration — CLAUDE.md ops notes all reference the console format). The full
    # JSON+stdlib variant lives in core/logging.configure_logging (unused so far).
    import structlog

    _min_level = getattr(
        logging, str(config.observability.log_level).upper(), logging.INFO
    )
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(_min_level),
        cache_logger_on_first_use=True,
    )

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

    # NOTE: CORS is registered at the END of this function (after all
    # @app.middleware) so it sits at the OUTERMOST layer. Registering it here
    # (early) made it an inner layer — inner auth 401 responses had no
    # Allow-Origin, which the browser masked as a CORS error.

    # Exception handlers (before middleware to catch errors)
    register_exception_handlers(app)

    # Catch-all for unhandled exceptions is registered LATER as an HTTP
    # middleware (see catch_unhandled_errors_middleware below), NOT via
    # @app.exception_handler(Exception). FastAPI routes an Exception handler
    # into ServerErrorMiddleware, which sits ABOVE all user middleware
    # (including CORS) — so its 500 responses bypass CORS and the browser
    # masks the real error as "No CORS header". The middleware form runs
    # BELOW the CORS layer (CORS is added last → outermost), so the 500
    # JSONResponse it returns flows back through CORS and gets Allow-Origin.

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

    # API Key middleware — registered when a shared api_key is configured OR
    # when the v1.9.0 system_db is enabled (personal-token auth path). The
    # middleware resolves personal tokens first (v1.9.0), then falls back to
    # the shared api_key (bootstrap/admin escape hatch).
    if config.api.api_key or getattr(config.system_db, "enabled", False):
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
    # v1.10.5 M3: an RS256/ES256-only deployment (asymmetric key pair, no
    # symmetric secret) is a valid JWT setup — the wiring below must not be
    # gated on jwt_secret_key alone (previously the middleware + AuthService
    # were silently skipped in that configuration).
    if auth_mode in ("jwt", "both") and (
        config.auth.jwt_secret_key or config.auth.jwt_private_key
    ):
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
            audience=config.auth.jwt_audience,
            require_audience=config.auth.jwt_require_audience,
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

        # v1.9.2 批5: Redis-backed rate_limit + login lockout (multi-worker)
        try:
            from arrow_lake.api._redis_rate_limit import create_rate_limiter

            rl = create_rate_limiter(config.redis)
            if rl is not None:
                app.state.redis_rate_limiter = rl
        except Exception:
            pass  # Non-fatal: rate_limit falls back to in-memory

        @app.middleware("http")
        async def jwt_auth_middleware(request, call_next):
            # both-mode: the api-key middleware (registered below, i.e. INNER)
            # owns the X-API-Key scheme; delegate Bearer-less header-carrying
            # requests to it so "Bearer OR X-API-Key" holds (v1.10.5 follow-up).
            from arrow_lake.config._enums import AuthMode

            delegate_header = (
                config.api.api_key_header
                if auth_mode == AuthMode.BOTH
                and (config.api.api_key or getattr(config.system_db, "enabled", False))
                else None
            )
            return await jwt_auth_middleware_fn(
                request, call_next, auth_service=svc,
                docs_enabled=config.api.docs_enabled,
                api_key_header=delegate_header,
            )

    # Rate limiting (enabled by default via RateLimitConfig.enabled = True).
    # P0-4 (H4, 2026-08-21): registered AFTER the auth middlewares so it wraps
    # them (later registration = outer layer). The auth middlewares 401
    # short-circuit without calling call_next — with rate_limit inside them,
    # brute-force attempts never touched the counters. As the outer layer every
    # attempt now counts, before token verification work is spent.
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

    # Catch-all for unhandled exceptions — registered as HTTP middleware so it
    # sits BELOW the CORS layer added next (CORS is added last → outermost).
    # Returning a JSONResponse here (instead of letting the exception escape
    # to ServerErrorMiddleware, which is ABOVE CORS) means the 500 response
    # flows back through CORS and carries Access-Control-Allow-Origin — so the
    # browser shows the real 500 instead of masking it as "No CORS header".
    @app.middleware("http")
    async def catch_unhandled_errors_middleware(request, call_next):  # noqa: ANN001
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 — log + normalize to JSON 500
            import structlog
            from starlette.responses import JSONResponse

            structlog.get_logger(__name__).exception(
                "unhandled_error", error=str(exc)[:200], path=request.url.path,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": "INTERNAL_ERROR",
                    "message": "Internal server error",
                },
            )

    # CORS — registered LAST so it's the OUTERMOST middleware. Starlette puts
    # later-registered middleware on the outside; this way every response
    # (including 401/422/500 from inner auth / rate-limit layers) gets
    # Access-Control-Allow-Origin attached — otherwise the browser masks real
    # errors as "No CORS header".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.api.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
    )

    app.include_router(system_router)
    app.include_router(datasets_router)
    app.include_router(search_router)
    app.include_router(query_router)
    app.include_router(export_router)
    app.include_router(quality_router)
    app.include_router(cleaning_router)
    app.include_router(embedding_router)
    app.include_router(embed_router)
    app.include_router(lineage_router)
    app.include_router(materialized_router)
    app.include_router(audit_router)
    app.include_router(backup_router)
    app.include_router(rag_router)
    app.include_router(kg_router)
    app.include_router(extraction_templates_router)
    app.include_router(doc_type_categories_router)
    app.include_router(ontology_router)
    app.include_router(contracts_router)
    app.include_router(actions_router)
    app.include_router(objects_router)
    app.include_router(semantic_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(maintenance_router)

    # Gravitino metadata router (always registered; returns 503 when disabled)
    if config.gravitino.enabled:
        app.include_router(gravitino_router)

    # Async task endpoints (fire-and-forget for heavy operations)
    app.include_router(async_tasks_router)

    # v1.9.0 P3: per-user state (saved queries / notifications / preferences)
    app.include_router(user_state_router)

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

        # W2 (v1.11.0.3): browsers auto-request /favicon.ico at the ORIGIN ROOT
        # on every console page load — it fell through to the auth middleware
        # and 401'd (the "intermittent homepage 401" console error; cached
        # favicons masked it). Serve the console asset publicly with a long
        # cache; 204 (not bare 404) when the asset is missing.
        from starlette.responses import FileResponse, Response

        _favicon = _console_dir / "assets" / "favicon.ico"

        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon() -> Response:  # noqa: ANN202 — starlette route
            if _favicon.is_file():
                return FileResponse(
                    str(_favicon), media_type="image/x-icon",
                    headers={"Cache-Control": "public, max-age=86400"},
                )
            return Response(status_code=204)

    # OpenTelemetry (optional — no-op when disabled or deps not installed)
    if config.opentelemetry.enabled:
        from arrow_lake.api.telemetry import setup_telemetry
        setup_telemetry(config.opentelemetry, app=app)

    return app
