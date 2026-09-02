"""RAG endpoints: query, stream, extract, templates, history."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request, APIRouter, Depends, HTTPException
from starlette.responses import StreamingResponse

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import authorize_dataset, get_checker, get_lake, require_role
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
        verification=(
            {
                "support_ratio": resp.verification.support_ratio,
                "valid_refs": resp.verification.valid_refs,
                "invalid_refs": resp.verification.invalid_refs,
                "mode": resp.verification.mode,
                "sentences": [
                    {"text": s.text, "label": s.label, "refs": list(s.refs)}
                    for s in resp.verification.sentences
                ],
            }
            if resp.verification is not None
            else None
        ),
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
    request: Request,
    checker=Depends(get_checker),
) -> RAGQueryResponse:
    """Run a RAG query: retrieve relevant documents and generate an answer."""
    authorize_dataset(request, req.dataset_name)
    _acl = checker.get_acl(req.dataset_name, _user["role"] if isinstance(_user, dict) else _user.role)
    if _acl is not None and (_acl.row_filter or _acl.visible_columns):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Dataset '{req.dataset_name}' has row/column ACL restrictions; "
                "RAG endpoints cannot enforce them yet (v1.10.7 fail-closed) — "
                "use search/OLAP endpoints for this dataset"
            ),
        )
    timeout_secs = float(lake._config.llm.timeout_seconds) + 30
    try:
        async with asyncio.timeout(timeout_secs):
            rag_resp = await lake.rag_query(
                question=req.question,
                dataset_name=req.dataset_name,
                top_k=req.top_k,
                strategy=req.retrieval_strategy,
                template_name=req.template_name,
                session_id=req.session_id,
                use_kg=req.use_kg,
            )
    except TimeoutError:
        raise HTTPException(status_code=504, detail="RAG query timed out — LLM provider may be unavailable") from None
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        from arrow_lake.exceptions import RAGError
        if isinstance(exc, RAGError):
            raise HTTPException(status_code=502, detail=exc.message) from exc
        raise
    return _rag_response_to_api(rag_resp)


@router.post("/query/stream")
async def rag_query_stream(
    *,
    req: RAGQueryRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
    request: Request,
    checker=Depends(get_checker),
) -> StreamingResponse:
    """Stream a RAG query response via Server-Sent Events (SSE)."""
    authorize_dataset(request, req.dataset_name)
    _acl = checker.get_acl(req.dataset_name, _user["role"] if isinstance(_user, dict) else _user.role)
    if _acl is not None and (_acl.row_filter or _acl.visible_columns):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Dataset '{req.dataset_name}' has row/column ACL restrictions; "
                "RAG endpoints cannot enforce them yet (v1.10.7 fail-closed) — "
                "use search/OLAP endpoints for this dataset"
            ),
        )

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

                # v1.9.6 P1-9: rich stream — citations first, content ×N, done with latency.
                done_sent = False
                async for phase, payload in lake.rag_query_stream_rich(
                    question=req.question,
                    dataset_name=req.dataset_name,
                    top_k=req.top_k,
                    strategy=req.retrieval_strategy,
                    template_name=req.template_name,
                ):
                    if phase == "citations":
                        cites = json.dumps([{
                            "chunk_index": c.chunk_index, "dataset": c.dataset,
                            "row_id": c.row_id, "score": c.score, "text_excerpt": c.text_excerpt,
                        } for c in payload])
                        yield f"id: {event_id}-cite\nevent: citations\ndata: {cites}\n\n"
                    elif phase == "content":
                        yield f"event: content\ndata: {json.dumps({'data': payload})}\n\n"
                    elif phase == "done":
                        done_sent = True
                        yield f"id: {event_id}-done\nevent: done\ndata: {json.dumps(payload)}\n\n"
                # SSE terminator: an empty upstream (or one that crashed before
                # emitting done) must still end with a done event — clients
                # wait on it to close the stream.
                if not done_sent:
                    yield f"id: {event_id}-done\nevent: done\ndata: {json.dumps({'latency_ms': 0})}\n\n"

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
            "Content-Encoding": "identity",  # 绕过 GZipMiddleware(SSE 压缩会缓冲整个响应,破坏流式)
        },
    )


@router.post("/extract", response_model=RAGExtractResponse)
async def rag_extract(
    *,
    req: RAGExtractRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
    request: Request,
    checker=Depends(get_checker),
) -> RAGExtractResponse:
    """Extract entities from a dataset using RAG."""
    authorize_dataset(request, req.dataset_name)
    _acl = checker.get_acl(req.dataset_name, _user["role"] if isinstance(_user, dict) else _user.role)
    if _acl is not None and (_acl.row_filter or _acl.visible_columns):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Dataset '{req.dataset_name}' has row/column ACL restrictions; "
                "RAG endpoints cannot enforce them yet (v1.10.7 fail-closed) — "
                "use search/OLAP endpoints for this dataset"
            ),
        )
    try:
        timeout_secs = float(lake._config.llm.timeout_seconds) + 30
        async with asyncio.timeout(timeout_secs):
            rag_resp = await lake.rag_extract(
                dataset_name=req.dataset_name,
                text_column=req.text_column,
                top_k=req.top_k,
                template_name=req.template_name,
            )
    except TimeoutError:
        raise HTTPException(status_code=504, detail="RAG extract timed out — LLM provider may be unavailable") from None
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        from arrow_lake.exceptions import RAGError
        if isinstance(exc, RAGError):
            raise HTTPException(status_code=502, detail=exc.message) from exc
        raise
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
