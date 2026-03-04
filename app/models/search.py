"""검색 결과 모델 — Phase 1~5 결과 관리"""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SearchSource(str, Enum):
    NEO4J_VECTOR = "neo4j_vector"
    NEO4J_GRAPH = "neo4j_graph"
    PG_VECTOR = "pg_vector"
    SUMMARY_BM25 = "summary_bm25"
    WEB_DOC = "web_doc"
    OFCODE_PARSER = "ofcode_parser"
    CPT_KNOWLEDGE = "cpt_knowledge"
    LLM_FALLBACK = "llm_fallback"


class SearchChunk(BaseModel):
    chunk_id: str
    content: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    source: SearchSource = SearchSource.NEO4J_VECTOR
    doc_name: Optional[str] = None
    page_number: Optional[int] = None
    section: Optional[str] = None
    product_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PhaseResult(BaseModel):
    phase: int = Field(ge=0, le=6)
    phase_name: str = ""
    chunks: List[SearchChunk] = Field(default_factory=list)
    max_score: float = Field(default=0.0, ge=0.0)
    execution_time_ms: int = 0
    skipped: bool = False
    skip_reason: Optional[str] = None

    @property
    def has_relevant_results(self) -> bool:
        return self.max_score >= 0.3


class PipelineState(BaseModel):
    query_plan: Optional[Any] = None  # QueryPlan, set at Phase 0
    phase_results: Dict[int, PhaseResult] = Field(default_factory=dict)
    accumulated_max_score: float = 0.0
    current_phase: int = 0
    fallback_triggered: bool = False

    def add_phase_result(self, result: PhaseResult):
        self.phase_results[result.phase] = result
        if result.max_score > self.accumulated_max_score:
            self.accumulated_max_score = result.max_score

    @property
    def needs_fallback(self) -> bool:
        return self.accumulated_max_score < 0.3

    def get_top_chunks(self, min_score: float = 0.0, limit: int = 20) -> List[SearchChunk]:
        chunks = []
        for result in self.phase_results.values():
            chunks.extend([c for c in result.chunks if c.score >= min_score])
        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks[:limit]
