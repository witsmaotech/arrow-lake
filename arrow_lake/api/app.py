"""FastAPI application factory for Arrow Lake REST API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from arrow_lake._version import __version__
from arrow_lake.api.auth import ApiKeyMiddleware
from arrow_lake.api.deps import get_config
from arrow_lake.api.errors import register_exception_handlers
from arrow_lake.api.middleware import RequestSizeLimitMiddleware
from arrow_lake.api.routers.audit import router as audit_router
from arrow_lake.api.routers.datasets import router as datasets_router
from arrow_lake.api.routers.embedding import embed_router
from arrow_lake.api.routers.embedding import router as embedding_router
from arrow_lake.api.routers.export import router as export_router
from arrow_lake.api.routers.lineage import router as lineage_router
from arrow_lake.api.routers.quality import router as quality_router
from arrow_lake.api.routers.query import router as query_router
from arrow_lake.api.routers.search import router as search_router
from arrow_lake.api.routers.system import router as system_router
from arrow_lake.config import ArrowLakeConfig


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialize Lake instance on startup."""
    from arrow_lake import Lake

    config: ArrowLakeConfig = app.state.config
    lake = Lake(base_uri=config.storage.base_uri, config=config)
    app.state.lake = lake
    yield


def create_app(config: ArrowLakeConfig | None = None) -> FastAPI:
    """Create and configure the Arrow Lake FastAPI application.

    Args:
        config: Optional config override. If None, loads from env/YAML.

    Returns:
        Configured FastAPI application instance.
    """
    if config is None:
        config = get_config()

    app = FastAPI(
        title="Arrow Lake REST API",
        description="Unified multimodal data lakehouse REST API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
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
        ],
    )

    # Store config for lifespan access
    app.state.config = config

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.api.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API Key auth (only if configured)
    if config.api.api_key:
        app.add_middleware(
            ApiKeyMiddleware,
            api_key=config.api.api_key,
            header_name=config.api.api_key_header,
        )

    # Exception handlers
    register_exception_handlers(app)

    # GZip compression (Starlette built-in)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Request body size limit
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_size_bytes=config.api.max_request_size_bytes,
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

    return app
