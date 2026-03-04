"""
Phase 1: Search Agent — BGE-M3 하이브리드 검색 + Neo4j + PostgreSQL + Summary BM25

4종 검색 병렬 실행 → RRF(Reciprocal Rank Fusion) 융합.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.agents.base import BaseAgent
from app.agents.tools.bge_m3_client import BgeM3Client
from app.agents.tools.neo4j_search import Neo4jSearchClient
from app.agents.tools.pg_search import PgSearchClient
from app.agents.tools.summary_search import SummarySearch
from app.models.search import PhaseResult, PipelineState, SearchChunk, SearchSource

logger = logging.getLogger(__name__)


class SearchAgent(BaseAgent):
    """Phase 1: 임베딩 문서 검색 — 4종 병렬 + RRF"""

    def __init__(self):
        super().__init__("SearchAgent")
        self.bge_m3 = BgeM3Client()
        self.neo4j = Neo4jSearchClient()
        self.pg = PgSearchClient()
        self.summary = SummarySearch()

    async def execute(self, state: PipelineState) -> None:
        plan = state.query_plan
        t0 = time.perf_counter()
        # 비교 쿼리에서 복수 제품이 있으면 필터 해제 (양쪽 모두 검색)
        if len(plan.products) > 1 and plan.comparison_targets:
            product = None
        else:
            product = plan.products[0].product_id if plan.products else None

        # 1. BGE-M3 임베딩
        try:
            embedding = await self.bge_m3.hybrid_encode(plan.normalized_query)
            dense = embedding.dense
        except Exception as e:
            logger.error(f"BGE-M3 encode failed: {e}")
            dense = []

        # 2. 4종 검색 병렬
        tasks = []
        if dense:
            tasks.append(self._neo4j_vector(dense, product))
            tasks.append(self._pg_vector(dense, product))
        else:
            tasks.append(self._empty())
            tasks.append(self._empty())
        tasks.append(self._neo4j_graph(plan.query_tokens, product))
        tasks.append(self._summary_bm25(plan.query_tokens, product))

        neo4j_vec, pg_vec, neo4j_graph, summary_res = await asyncio.gather(*tasks)

        # 3. RRF 융합
        all_sources = [
            (neo4j_vec, SearchSource.NEO4J_VECTOR),
            (pg_vec, SearchSource.PG_VECTOR),
            (neo4j_graph, SearchSource.NEO4J_GRAPH),
            (summary_res, SearchSource.SUMMARY_BM25),
        ]
        chunks = self._rrf_fusion(all_sources, k=60, limit=20)
        max_score = chunks[0].score if chunks else 0.0
        elapsed = int((time.perf_counter() - t0) * 1000)

        logger.info(
            f"[Phase 1] {len(chunks)} chunks, max_score={max_score:.3f}, "
            f"neo4j_vec={len(neo4j_vec)}, pg={len(pg_vec)}, "
            f"graph={len(neo4j_graph)}, summary={len(summary_res)}, "
            f"{elapsed}ms"
        )

        state.add_phase_result(PhaseResult(
            phase=1,
            phase_name="embedding_search",
            chunks=chunks,
            max_score=max_score,
            execution_time_ms=elapsed,
        ))

    # ─── 개별 검색 래퍼 ───

    async def _neo4j_vector(self, dense: List[float], product: Optional[str]) -> List[Dict]:
        try:
            return await self.neo4j.vector_search(dense, top_k=10, product=product)
        except Exception as e:
            logger.warning(f"Neo4j vector failed: {e}")
            return []

    async def _pg_vector(self, dense: List[float], product: Optional[str]) -> List[Dict]:
        try:
            return await self.pg.vector_search(dense, top_k=10, product=product)
        except Exception as e:
            logger.warning(f"PG vector failed: {e}")
            return []

    async def _neo4j_graph(self, tokens: List[str], product: Optional[str]) -> List[Dict]:
        try:
            return await self.neo4j.graph_search(tokens, product=product)
        except Exception as e:
            logger.warning(f"Neo4j graph failed: {e}")
            return []

    async def _summary_bm25(self, tokens: List[str], product: Optional[str]) -> List[Dict]:
        try:
            return self.summary.search(tokens, product=product, top_k=5)
        except Exception as e:
            logger.warning(f"Summary BM25 failed: {e}")
            return []

    async def _empty(self) -> List[Dict]:
        return []

    # ─── RRF 융합 ───

    def _rrf_fusion(
        self,
        source_lists: List[tuple],
        k: int = 60,
        limit: int = 20,
    ) -> List[SearchChunk]:
        """Reciprocal Rank Fusion: score = Σ 1/(k + rank_i)"""
        merged: Dict[str, Dict[str, Any]] = {}

        for results, source_type in source_lists:
            for rank, item in enumerate(results):
                cid = item.get("chunk_id", item.get("source_file", f"_{rank}"))
                rrf_score = 1.0 / (k + rank + 1)

                if cid not in merged:
                    merged[cid] = {
                        "chunk_id": cid,
                        "content": item.get("content", ""),
                        "doc_name": item.get("doc_name", item.get("source_file", "")),
                        "page_number": item.get("page", item.get("page_number")),
                        "section": item.get("section", item.get("title", "")),
                        "product_id": item.get("product", item.get("product_id", "")),
                        "source": source_type,
                        "score": 0.0,
                    }
                merged[cid]["score"] += rrf_score

        # 정규화 (0~1)
        items = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
        if items:
            max_s = items[0]["score"]
            for it in items:
                it["score"] = min(it["score"] / max(max_s, 0.001), 1.0)

        return [
            SearchChunk(
                chunk_id=it["chunk_id"],
                content=it["content"][:3000],
                score=round(it["score"], 4),
                source=it["source"],
                doc_name=it.get("doc_name"),
                page_number=it.get("page_number"),
                section=it.get("section"),
                product_id=it.get("product_id"),
            )
            for it in items[:limit]
        ]

    async def close(self):
        await self.neo4j.close()
