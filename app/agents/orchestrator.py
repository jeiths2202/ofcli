"""
Orchestrator — 5단계 Cascading Search Pipeline 총괄 제어

Phase 0: Query Understanding (항상)
Phase 1: Embedding Search (항상)
Phase 2: Domain Knowledge (항상)
Phase 3: OFCode Web Docs (항상)
Phase 4: OFCode Parser (코드 쿼리 시)
Phase 5: Fallback (Phase 1~4 max_score < FALLBACK_THRESHOLD 시)
Phase 6: Response Generation (항상)
"""
import json
import logging
import time
from typing import AsyncGenerator, Dict, Optional

from app.agents.code_agent import CodeAgent
from app.agents.domain_agent import DomainAgent
from app.agents.fallback_agent import FallbackAgent
from app.agents.query_agent import QueryAgent
from app.agents.response_agent import ResponseAgent
from app.agents.search_agent import SearchAgent
from app.core.config import get_settings
from app.models.query import DetectedLanguage, ProductMatch, QueryPlan
from app.models.response import FinalResponse
from app.models.search import PipelineState

logger = logging.getLogger(__name__)


class Orchestrator:
    """Agent Pipeline Orchestrator"""

    def __init__(self):
        self.query_agent = QueryAgent()
        self.search_agent = SearchAgent()
        self.domain_agent = DomainAgent()
        self.code_agent = CodeAgent()
        self.fallback_agent = FallbackAgent()
        self.response_agent = ResponseAgent()
        self._threshold = get_settings().FALLBACK_THRESHOLD

    def _apply_overrides(
        self, plan: QueryPlan, language: Optional[str], product: Optional[str]
    ) -> None:
        """Apply user-specified language/product overrides to the query plan."""
        if language and language in ("ja", "ko", "en"):
            plan.language = DetectedLanguage(language)
        if product:
            if not any(p.product_id == product for p in plan.products):
                plan.products.insert(
                    0, ProductMatch(product_id=product, confidence=1.0, matched_keywords=["user_selected"])
                )

    async def execute(
        self, query: str, language: Optional[str] = None, product: Optional[str] = None
    ) -> FinalResponse:
        t0 = time.perf_counter()
        state = PipelineState(query_plan=QueryPlan(raw_query=query))

        # Phase 0: Query Understanding
        logger.info(f"[Phase 0] Analyzing: {query[:80]}...")
        await self.query_agent.execute(state)
        plan = state.query_plan
        self._apply_overrides(plan, language, product)
        logger.info(
            f"[Phase 0] intent={plan.intent.value}, lang={plan.language.value}, "
            f"products={[p.product_id for p in plan.products[:3]]}, "
            f"code={plan.requires_code_analysis}"
        )

        # Phase 1: Embedding Search
        logger.info("[Phase 1] Embedding search...")
        await self.search_agent.execute(state)

        # Phase 2: Domain Knowledge
        logger.info("[Phase 2] Domain knowledge...")
        await self.domain_agent.execute(state)

        # Phase 3: OFCode Web Docs
        logger.info("[Phase 3] OFCode web docs...")
        await self.code_agent.execute_web_search(state)

        # Phase 4: OFCode Parser (조건부)
        if plan.requires_code_analysis:
            logger.info("[Phase 4] OFCode parser...")
            await self.code_agent.execute_parser(state)

        # Phase 5: Fallback (조건부: max_score < threshold)
        if state.needs_fallback:
            logger.info(
                f"[Phase 5] Fallback triggered (max_score={state.accumulated_max_score:.3f} "
                f"< {self._threshold})"
            )
            state.fallback_triggered = True
            await self.fallback_agent.execute(state)
        else:
            logger.info(
                f"[Phase 5] Skipped (max_score={state.accumulated_max_score:.3f} "
                f">= {self._threshold})"
            )

        # Phase 6: Response Generation
        logger.info("[Phase 6] Building response...")
        response = await self.response_agent.execute(state)

        total = int((time.perf_counter() - t0) * 1000)
        response.total_time_ms = total
        logger.info(
            f"[Done] confidence={response.overall_confidence:.2f}, "
            f"phases={response.phases_executed}, "
            f"fallback={response.fallback_used}, "
            f"{total}ms"
        )
        return response

    async def execute_streaming(
        self, query: str, language: Optional[str] = None, product: Optional[str] = None
    ) -> AsyncGenerator[Dict, None]:
        """SSE streaming — yield phase events + final answer"""
        t0 = time.perf_counter()
        state = PipelineState(query_plan=QueryPlan(raw_query=query))

        # Phase 0
        pt0 = time.perf_counter()
        await self.query_agent.execute(state)
        plan = state.query_plan
        self._apply_overrides(plan, language, product)
        yield {"event": "phase", "data": json.dumps({
            "phase": 0, "name": "query_analysis", "status": "complete",
            "time_ms": int((time.perf_counter() - pt0) * 1000),
        })}

        # Phase 1
        pt0 = time.perf_counter()
        await self.search_agent.execute(state)
        yield {"event": "phase", "data": json.dumps({
            "phase": 1, "name": "embedding_search", "status": "complete",
            "time_ms": int((time.perf_counter() - pt0) * 1000),
        })}

        # Phase 2
        pt0 = time.perf_counter()
        await self.domain_agent.execute(state)
        yield {"event": "phase", "data": json.dumps({
            "phase": 2, "name": "domain_knowledge", "status": "complete",
            "time_ms": int((time.perf_counter() - pt0) * 1000),
        })}

        # Phase 3
        pt0 = time.perf_counter()
        await self.code_agent.execute_web_search(state)
        yield {"event": "phase", "data": json.dumps({
            "phase": 3, "name": "ofcode_web", "status": "complete",
            "time_ms": int((time.perf_counter() - pt0) * 1000),
        })}

        # Phase 4 (conditional)
        if plan.requires_code_analysis:
            pt0 = time.perf_counter()
            await self.code_agent.execute_parser(state)
            yield {"event": "phase", "data": json.dumps({
                "phase": 4, "name": "ofcode_parser", "status": "complete",
                "time_ms": int((time.perf_counter() - pt0) * 1000),
            })}

        # Phase 5 (conditional fallback)
        if state.needs_fallback:
            state.fallback_triggered = True
            pt0 = time.perf_counter()
            await self.fallback_agent.execute(state)
            yield {"event": "phase", "data": json.dumps({
                "phase": 5, "name": "fallback", "status": "complete",
                "time_ms": int((time.perf_counter() - pt0) * 1000),
            })}

        # Phase 6: Response
        response = await self.response_agent.execute(state)
        total = int((time.perf_counter() - t0) * 1000)
        response.total_time_ms = total

        yield {"event": "answer", "data": json.dumps({
            "answer": response.answer,
            "confidence": response.overall_confidence,
            "language": response.answer_language,
            "intent": response.query_intent,
            "product": response.product,
        })}

        yield {"event": "done", "data": json.dumps({"total_time_ms": total})}

    async def close(self):
        await self.search_agent.close()
