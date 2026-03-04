"""
Phase 3: OFCode 웹문서 검색
Phase 4: OFCode Parser 기반 코드 분석/생성
"""
import logging
import time
from typing import Optional

from app.agents.base import BaseAgent
from app.agents.tools.ofcode_client import OFCodeClient
from app.models.query import QueryPlan
from app.models.search import (
    PhaseResult,
    PipelineState,
    SearchChunk,
    SearchSource,
)

logger = logging.getLogger(__name__)


class CodeAgent(BaseAgent):
    """Phase 3 + Phase 4: OFCode Server 연동"""

    def __init__(self):
        super().__init__("CodeAgent")
        self.ofcode = OFCodeClient()

    async def execute(self, state: PipelineState) -> None:
        """Phase 3 실행 (Phase 4는 orchestrator가 별도 호출)"""
        await self.execute_web_search(state)

    async def execute_web_search(self, state: PipelineState) -> None:
        """Phase 3: docs.tmaxsoft.com 웹문서 검색"""
        plan: QueryPlan = state.query_plan
        t0 = time.perf_counter()
        product = plan.products[0].product_id if plan.products else None

        results = await self.ofcode.search_web_docs(
            query=plan.normalized_query,
            product=product,
            language=plan.language.value,
        )

        chunks = []
        for i, r in enumerate(results[:10]):
            score = r.get("score", r.get("normalized_score", 0.5))
            if isinstance(score, str):
                try:
                    score = float(score)
                except ValueError:
                    score = 0.5
            chunks.append(SearchChunk(
                chunk_id=f"webdoc_{i}",
                content=r.get("snippet", r.get("content", ""))[:2000],
                score=min(float(score), 1.0),
                source=SearchSource.WEB_DOC,
                doc_name=r.get("title", ""),
                section=r.get("component", ""),
                product_id=r.get("product_id", ""),
                metadata={"url": r.get("url", "")},
            ))

        max_score = chunks[0].score if chunks else 0.0
        elapsed = int((time.perf_counter() - t0) * 1000)

        logger.info(f"[Phase 3] web docs={len(chunks)}, max_score={max_score:.3f}, {elapsed}ms")

        state.add_phase_result(PhaseResult(
            phase=3,
            phase_name="ofcode_web_search",
            chunks=chunks,
            max_score=max_score,
            execution_time_ms=elapsed,
        ))

    async def execute_parser(self, state: PipelineState) -> None:
        """Phase 4: OpenFrame Parser 기반 코드 분석"""
        plan: QueryPlan = state.query_plan
        t0 = time.perf_counter()

        code_type = self._detect_code_type(plan)
        parsed = await self.ofcode.parse_code(query=plan.raw_query, code_type=code_type)

        chunks = []
        if parsed:
            content = parsed.get("analysis", parsed.get("result", str(parsed)))
            if isinstance(content, dict):
                content = str(content)
            chunks.append(SearchChunk(
                chunk_id="parser_0",
                content=content[:3000],
                score=0.6,
                source=SearchSource.OFCODE_PARSER,
                product_id=plan.products[0].product_id if plan.products else "",
            ))

        max_score = chunks[0].score if chunks else 0.0
        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.info(f"[Phase 4] parsed={len(chunks)}, {elapsed}ms")

        state.add_phase_result(PhaseResult(
            phase=4,
            phase_name="ofcode_parser",
            chunks=chunks,
            max_score=max_score,
            execution_time_ms=elapsed,
        ))

    def _detect_code_type(self, plan: QueryPlan) -> str:
        raw = plan.raw_query.lower()
        if "cobol" in raw:
            return "cobol"
        if "asm" in raw or "assembler" in raw:
            return "asm"
        if " c " in raw or raw.endswith(" c") or "c언어" in raw or "c言語" in raw or "c샘플" in raw or "cサンプル" in raw:
            return "c"
        return "jcl"
