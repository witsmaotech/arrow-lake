"""
LanceDB HTTP Service
RESTful API for vector database operations
Based on Shannon project architecture
"""
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import structlog
from fastapi import FastAPI, HTTPException, status
from lancedb import connect
from pydantic import ValidationError

from .config import settings
from .models import (
    HealthResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
    UpsertRequest,
    UpsertResponse,
    RecentRequest,
    RecentResponse,
    DeleteRequest,
    DeleteResponse,
)
from .index_manager import ensure_vector_index, optimize_nprobes

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

# Global LanceDB connection
db = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager"""
    # Startup
    global db
    logger.info("Starting LanceDB service", uri=settings.LANCEDB_URI)
    try:
        db = connect(settings.LANCEDB_URI)
        logger.info("LanceDB connected successfully")
    except Exception as e:
        logger.error("Failed to connect to LanceDB", error=str(e))
        raise

    yield

    # Shutdown
    logger.info("Shutting down LanceDB service")


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="RESTful API for vector database operations",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """
    Health check endpoint
    Returns service status and version information
    """
    return HealthResponse(
        status="ok",
        service="lancedb",
        version=settings.APP_VERSION,
    )


@app.post("/api/v1/search", response_model=SearchResponse, tags=["Vector Operations"])
async def semantic_search(request: SearchRequest):
    """
    Semantic vector search

    Performs similarity search in the specified collection using vector embeddings.
    Supports filtering by metadata fields.
    """
    start_time = time.time()

    try:
        logger.info(
            "Search request",
            collection=request.collection,
            limit=request.limit,
            metric=request.metric,
        )

        # Open table
        table = db.open_table(request.collection)

        # 确保索引存在（异步执行，不阻塞搜索）
        import asyncio
        asyncio.create_task(ensure_vector_index(table))

        # Build search query
        search_query = table.search(request.vector)

        # 优化nprobes参数
        num_vectors = len(table)
        nprobes = optimize_nprobes(table, num_vectors, request.mode if hasattr(request, 'mode') else "balanced")
        search_query = search_query.nprobes(nprobes)

        # Apply filter if provided
        if request.filter:
            search_query = search_query.where(request.filter)

        # Execute search
        results_df = search_query.limit(request.limit).to_pandas()

        # Convert results to response format
        items = []
        for idx, row in results_df.iterrows():
            # Extract score (distance)
            if "_distance" in row:
                score = float(row["_distance"])
            else:
                score = 0.0

            # Build result item
            items.append(
                SearchResult(
                    id=str(row.get("id", idx)),
                    score=score,
                    data=row.to_dict(),
                )
            )

        latency_ms = (time.time() - start_time) * 1000

        logger.info(
            "Search completed",
            collection=request.collection,
            results_count=len(items),
            latency_ms=f"{latency_ms:.2f}",
        )

        return SearchResponse(items=items, total=len(items), latency_ms=latency_ms)

    except Exception as e:
        logger.error("Search failed", error=str(e), collection=request.collection)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
        )


@app.post("/api/v1/upsert", response_model=UpsertResponse, tags=["Vector Operations"])
async def upsert_data(request: UpsertRequest):
    """
    Insert or update records

    Adds new vectors or updates existing ones in the specified collection.
    Supports batch operations for better performance.
    """
    try:
        logger.info(
            "Upsert request",
            collection=request.collection,
            item_count=len(request.items),
            mode=request.mode,
        )

        # Prepare data
        data = []
        for item in request.items:
            record = {
                "id": item.id,
                "vector": item.vector,
                **item.metadata,
            }
            data.append(record)

        # Open or create table
        try:
            table = db.open_table(request.collection)
        except Exception:
            # Table doesn't exist, create it
            logger.info("Creating new collection", collection=request.collection)
            table = db.create_table(
                request.collection,
                data=data,
                mode=request.mode,
            )

            # 异步创建索引
            import asyncio
            asyncio.create_task(ensure_vector_index(table))

            return UpsertResponse(
                success=True,
                count=len(request.items),
                message=f"Collection created and {len(request.items)} items inserted",
            )

        # Add data to existing table
        # 批量优化：如果数据量大，分批添加
        BATCH_SIZE = 1000
        total_items = len(data)

        if total_items > BATCH_SIZE:
            for i in range(0, total_items, BATCH_SIZE):
                batch = data[i:i+BATCH_SIZE]
                table.add(batch)
                logger.info("Batch upserted", batch_size=len(batch), progress=f"{i+len(batch)}/{total_items}")
        else:
            table.add(data)

        # 检查是否需要创建索引
        import asyncio
        asyncio.create_task(ensure_vector_index(table))

        logger.info(
            "Upsert completed",
            collection=request.collection,
            count=len(request.items),
        )

        return UpsertResponse(
            success=True,
            count=len(request.items),
            message=f"Successfully upserted {len(request.items)} items",
        )

    except Exception as e:
        logger.error("Upsert failed", error=str(e), collection=request.collection)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upsert failed: {str(e)}",
        )


@app.post("/api/v1/get_recent", response_model=RecentResponse, tags=["Vector Operations"])
async def get_recent(request: RecentRequest):
    """
    Get recent records

    Retrieves the most recently added records from the collection.
    Useful for auditing and validation purposes.
    """
    try:
        logger.info("Get recent request", collection=request.collection, limit=request.limit)

        # Open table
        table = db.open_table(request.collection)

        # Get recent records (no search, just fetch)
        results_df = table.limit(request.limit).to_pandas()

        # Convert to list of dicts
        items = results_df.to_dict(orient="records")

        logger.info(
            "Get recent completed",
            collection=request.collection,
            count=len(items),
        )

        return RecentResponse(items=items, total=len(items))

    except Exception as e:
        logger.error("Get recent failed", error=str(e), collection=request.collection)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Get recent failed: {str(e)}",
        )


@app.post("/api/v1/delete", response_model=DeleteResponse, tags=["Vector Operations"])
async def delete_records(request: DeleteRequest):
    """
    Delete records by IDs

    Removes specified records from the collection.
    Use with caution as this operation cannot be undone.
    """
    try:
        logger.info(
            "Delete request",
            collection=request.collection,
            id_count=len(request.ids),
        )

        # Open table
        table = db.open_table(request.collection)

        # Security: Validate IDs to prevent SQL injection
        # Limit batch size to prevent DoS
        MAX_BATCH_SIZE = 1000
        if len(request.ids) > MAX_BATCH_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete more than {MAX_BATCH_SIZE} records at once"
            )

        deleted_count = 0

        # Delete records one by one with validation
        # This is safer than building a filter string with user input
        for id_val in request.ids:
            # Validate ID format: must be string and contain only safe characters
            if not isinstance(id_val, str):
                logger.warning("Invalid ID type", id=str(id_val), type=type(id_val).__name__)
                continue

            # Remove any potentially dangerous characters
            # Only allow alphanumeric, underscore, hyphen
            safe_id = "".join(c for c in id_val if c.isalnum() or c in ('_', '-'))

            if len(safe_id) != len(id_val):
                logger.warning("ID contains unsafe characters, skipping", id=id_val)
                continue

            try:
                # Safe deletion with validated ID
                filter_str = f"id = '{safe_id}'"
                table.delete(filter_str)
                deleted_count += 1
            except Exception as e:
                logger.warning("Failed to delete record", id=safe_id, error=str(e))

        logger.info(
            "Delete completed",
            collection=request.collection,
            count=deleted_count,
        )

        return DeleteResponse(
            success=True,
            count=deleted_count,
            message=f"Successfully deleted {deleted_count} records",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Delete failed", error=str(e), collection=request.collection)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Delete failed: {str(e)}",
        )


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information"""
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs_url": "/docs",
        "health_url": "/health",
        "api_v1_prefix": settings.API_V1_PREFIX,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "lancedb_service.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
