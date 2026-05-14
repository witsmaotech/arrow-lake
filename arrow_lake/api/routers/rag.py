"""RAG endpoints: query, stream, extract, templates, history."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import StreamingResponse

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.rag import (
    RAGCitationResponse,
    RAGExtractRequest,
    RAGExtractResponse,
    RAGHistoryResponse,
    RAGQueryRequest,
    RAGQueryResponse,
    RAGTemplateInfo,
    RAGTemplatesResponse,
)
from arrow_lake.rag.pipeline import RAGResponse
from arrow_lake.rag.prompt import PromptRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rag_response_to_api(resp: RAGResponse) -> RAGQueryResponse:
    """Convert internal RAGResponse to API response model."""
    return RAGQueryResponse(
        answer=resp.answer,
        citations=[
            RAGCitationResponse(
                chunk_index=c.chunk_index,
                dataset=c.dataset,
                row_id=c.row_id,
                score=c.score,
                text_excerpt=c.text_excerpt,
            )
            for c in resp.citations
        ],
        retrieval_count=resp.retrieval_count,
        context_tokens=resp.context_tokens,
        latency_ms=resp.latency_ms,
        session_id=resp.session_id,
    )


def _extract_response_to_api(resp: RAGResponse) -> RAGExtractResponse:
    """Convert internal RAGResponse to extract API response model."""
    return RAGExtractResponse(
        answer=resp.answer,
        retrieval_count=resp.retrieval_count,
        context_tokens=resp.context_tokens,
        latency_ms=resp.latency_ms,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/query", response_model=RAGQueryResponse)
async def rag_query(
    *,
    req: RAGQueryRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> RAGQueryResponse:
    """Run a RAG query: retrieve relevant documents and generate an answer."""
    try:
        rag_resp = await lake.rag_query(
            question=req.question,
            dataset_name=req.dataset_name,
            top_k=req.top_k,
            strategy=req.retrieval_strategy,
            template_name=req.template_name,
            session_id=req.session_id,
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _rag_response_to_api(rag_resp)


@router.post("/query/stream")
async def rag_query_stream(
    *,
    req: RAGQueryRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> StreamingResponse:
    """Stream a RAG query response via Server-Sent Events (SSE)."""

    async def _event_generator() -> AsyncIterator[str]:
        event_id = uuid.uuid4().hex[:8]
        try:
            async with asyncio.timeout(300):
                # Send metadata event
                meta = json.dumps({
                    "dataset_name": req.dataset_name,
                    "top_k": req.top_k,
                    "strategy": req.retrieval_strategy,
                })
                yield f"id: {event_id}-meta\nevent: metadata\ndata: {meta}\n\n"

                # Stream content chunks
                async for chunk in lake.rag_query_stream(
                    question=req.question,
                    dataset_name=req.dataset_name,
                    top_k=req.top_k,
                    strategy=req.retrieval_strategy,
                    template_name=req.template_name,
                ):
                    data = json.dumps({"data": chunk})
                    yield f"event: content\ndata: {data}\n\n"

                yield f"id: {event_id}-done\nevent: done\ndata: {{}}\n\n"

        except TimeoutError:
            logger.warning("RAG stream timed out after 300s")
            error_data = json.dumps({"error": "Streaming timed out"})
            yield f"id: {event_id}-error\nevent: error\ndata: {error_data}\n\n"
        except Exception:
            logger.exception("RAG stream error")
            error_data = json.dumps({"error": "An internal error occurred during streaming."})
            yield f"id: {event_id}-error\nevent: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/extract", response_model=RAGExtractResponse)
async def rag_extract(
    *,
    req: RAGExtractRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> RAGExtractResponse:
    """Extract entities from a dataset using RAG."""
    try:
        rag_resp = await lake.rag_extract(
            dataset_name=req.dataset_name,
            text_column=req.text_column,
            top_k=req.top_k,
            template_name=req.template_name,
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _extract_response_to_api(rag_resp)


@router.get("/templates", response_model=RAGTemplatesResponse)
async def rag_templates(
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> RAGTemplatesResponse:
    """List available prompt templates."""
    registry = PromptRegistry()
    templates = []
    for name in registry.list_templates():
        tmpl = registry.get(name)
        if tmpl is not None:
            templates.append(
                RAGTemplateInfo(
                    name=tmpl.name,
                    type=tmpl.type.value,
                    description=tmpl.description or tmpl.name.replace("_", " ").title(),
                )
            )
    return RAGTemplatesResponse(templates=templates)


@router.get(
    "/history/{session_id}",
    response_model=RAGHistoryResponse,
)
async def rag_history(
    session_id: str,
    _auth: None = Depends(require_role(Role.VIEWER)),
    lake: Any = Depends(get_lake),
) -> RAGHistoryResponse:
    """Get conversation history for a session (requires VIEWER role)."""
    import re

    if not re.match(r'^[a-zA-Z0-9_-]{1,128}$', session_id):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Invalid session_id format")
    turns = lake.rag_get_history(session_id)
    return RAGHistoryResponse(session_id=session_id, turns=turns)
