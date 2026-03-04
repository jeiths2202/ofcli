"""POST /v1/query, POST /v1/query/stream — Main query endpoints"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from api.deps import get_orchestrator
from api.models.request import QueryRequest
from api.models.response import QueryResponse, SourceInfo, UsageInfo
from app.agents.orchestrator import Orchestrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["query"])


def _to_query_response(resp, req: QueryRequest) -> QueryResponse:
    """Convert FinalResponse → QueryResponse"""
    sources = None
    if req.include_sources and resp.sources:
        sources = [
            SourceInfo(
                document=s.doc_name,
                page=s.page,
                score=round(s.score, 4),
                type=s.source_type,
            )
            for s in resp.sources
        ]

    phase_times = resp.phase_times if req.include_phases else None

    return QueryResponse(
        success=resp.success,
        answer=resp.answer,
        language=resp.answer_language,
        confidence=round(resp.overall_confidence, 4),
        intent=resp.query_intent,
        product=resp.product,
        sources=sources,
        usage=UsageInfo(
            total_time_ms=resp.total_time_ms,
            phases_executed=resp.phases_executed,
            fallback_used=resp.fallback_used,
            phase_times=phase_times,
        ),
    )


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, orch: Orchestrator = Depends(get_orchestrator)):
    try:
        resp = await orch.execute(req.query, language=req.language, product=req.product)
        return _to_query_response(resp, req)
    except Exception as e:
        logger.error(f"Query failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query/stream")
async def query_stream(req: QueryRequest, orch: Orchestrator = Depends(get_orchestrator)):
    async def event_generator():
        try:
            async for event in orch.execute_streaming(req.query, language=req.language, product=req.product):
                yield event
        except Exception as e:
            logger.error(f"Stream failed: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)}),
            }

    return EventSourceResponse(event_generator())
