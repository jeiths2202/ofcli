# Design: OFKMS v2.0 — Agent-Based Query Pipeline

> **Feature**: ofkms-v2
> **Created**: 2026-03-04
> **Phase**: Design
> **Status**: Draft
> **References**: [Plan](../../01-plan/features/ofkms-v2.plan.md)

---

## 1. 데이터 모델 (Pydantic Schemas)

### 1.1 Query 모델 — `app/models/query.py`

```python
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class QueryIntent(str, Enum):
    """쿼리 의도 분류"""
    COMMAND = "command"           # 명령어 질문 (tjesmgr BOOT)
    ERROR_CODE = "error_code"    # 에러코드 질문 (-5212)
    CONFIG = "config"            # 설정/파라미터 질문
    CONCEPT = "concept"          # 개념/설명 질문
    CODE = "code"                # 코드 해석/작성 질문
    COMPARISON = "comparison"    # 제품 비교 질문
    PROCEDURE = "procedure"      # 절차/방법 질문
    TROUBLESHOOT = "troubleshoot"# 트러블슈팅 질문
    GENERAL = "general"          # 일반 질문


class DetectedLanguage(str, Enum):
    """감지된 언어"""
    JA = "ja"
    KO = "ko"
    EN = "en"


class ProductMatch(BaseModel):
    """제품 매칭 결과"""
    product_id: str = Field(..., description="동적 제품 ID (예: mvs_openframe_7.1)")
    confidence: float = Field(ge=0.0, le=1.0)
    matched_keywords: List[str] = Field(default_factory=list)
    matched_patterns: List[str] = Field(default_factory=list)


class QueryPlan(BaseModel):
    """Phase 0 Query Agent 출력 — 전체 파이프라인의 실행 계획"""
    raw_query: str
    normalized_query: str = Field(description="정규화된 쿼리 (불용어 제거, 소문자)")
    intent: QueryIntent
    language: DetectedLanguage
    products: List[ProductMatch] = Field(description="매칭된 제품 목록 (confidence 내림차순)")
    requires_code_analysis: bool = Field(default=False, description="Phase 4 실행 여부")
    query_tokens: List[str] = Field(description="검색용 토큰 목록")
    error_codes: List[str] = Field(default_factory=list, description="감지된 에러코드")
    command_names: List[str] = Field(default_factory=list, description="감지된 명령어명")
    expansion_terms: List[str] = Field(default_factory=list, description="쿼리 확장 용어")
```

### 1.2 Search 모델 — `app/models/search.py`

```python
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SearchSource(str, Enum):
    """검색 출처"""
    NEO4J_VECTOR = "neo4j_vector"
    NEO4J_GRAPH = "neo4j_graph"
    PG_VECTOR = "pg_vector"
    SUMMARY_BM25 = "summary_bm25"
    WEB_DOC = "web_doc"
    OFCODE_PARSER = "ofcode_parser"
    CPT_KNOWLEDGE = "cpt_knowledge"
    LLM_FALLBACK = "llm_fallback"


class SearchChunk(BaseModel):
    """검색된 청크 하나"""
    chunk_id: str
    content: str
    score: float = Field(ge=0.0, le=1.0)
    source: SearchSource
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 출처 정보
    doc_name: Optional[str] = None
    page_number: Optional[int] = None
    section: Optional[str] = None
    product_id: Optional[str] = None


class PhaseResult(BaseModel):
    """각 Phase의 검색 결과"""
    phase: int = Field(ge=0, le=6)
    phase_name: str
    chunks: List[SearchChunk] = Field(default_factory=list)
    max_score: float = Field(default=0.0, ge=0.0, le=1.0)
    execution_time_ms: int = 0
    skipped: bool = False
    skip_reason: Optional[str] = None

    @property
    def has_relevant_results(self) -> bool:
        """유사도 0.3 이상 결과 존재 여부"""
        return self.max_score >= 0.3


class PipelineState(BaseModel):
    """파이프라인 전체 상태 (Orchestrator가 관리)"""
    query_plan: Optional["QueryPlan"] = None
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
        """Phase 1~4 누적 max_score < 0.3이면 Phase 5 폴백 필요"""
        return self.accumulated_max_score < 0.3

    def get_all_chunks(self, min_score: float = 0.0) -> List["SearchChunk"]:
        """전체 Phase에서 min_score 이상인 청크 수집"""
        chunks = []
        for result in self.phase_results.values():
            chunks.extend([c for c in result.chunks if c.score >= min_score])
        return sorted(chunks, key=lambda c: c.score, reverse=True)
```

### 1.3 Response 모델 — `app/models/response.py`

```python
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VerificationLevel(str, Enum):
    """신뢰도 검증 등급"""
    VERIFIED = "verified"       # >= 0.7 (🟢 소스 확인됨)
    INFERRED = "inferred"       # 0.4 ~ 0.7 (🟡 추론)
    UNVERIFIED = "unverified"   # < 0.4 (🔴 미검증)


class VerifiedSentence(BaseModel):
    """검증된 문장"""
    text: str
    level: VerificationLevel
    similarity: float = Field(ge=0.0, le=1.0)
    source_chunk_id: Optional[str] = None
    source_doc: Optional[str] = None


class SourceAttribution(BaseModel):
    """출처 표기"""
    doc_name: str
    page: Optional[int] = None
    section: Optional[str] = None
    url: Optional[str] = None
    score: float = Field(ge=0.0, le=1.0)
    source_type: str = Field(description="search_source enum 값")


class FinalResponse(BaseModel):
    """Phase 6 Response Agent 최종 출력"""
    success: bool
    answer: str = Field(description="최종 답변 텍스트 (Markdown)")
    answer_language: str = Field(description="답변 언어 (ja/ko/en)")

    # 파이프라인 메타데이터
    product: str = Field(description="최종 결정된 제품")
    query_intent: str
    phases_executed: List[int] = Field(description="실행된 Phase 번호 목록")
    fallback_used: bool = Field(default=False)

    # 검증
    verification: List[VerifiedSentence] = Field(default_factory=list)
    overall_confidence: float = Field(ge=0.0, le=1.0)

    # 출처
    sources: List[SourceAttribution] = Field(default_factory=list)

    # 성능
    total_time_ms: int = 0
    phase_times: Dict[str, int] = Field(default_factory=dict)
```

### 1.4 Agent 공통 모델 — `app/models/agent.py`

```python
from enum import Enum
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from pydantic import BaseModel, Field
import uuid


class AgentType(str, Enum):
    """Agent 유형"""
    ORCHESTRATOR = "orchestrator"
    QUERY = "query"
    SEARCH = "search"
    DOMAIN = "domain"
    CODE = "code"
    FALLBACK = "fallback"
    RESPONSE = "response"


@dataclass
class AgentContext:
    """Agent 실행 컨텍스트"""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    language: str = "ja"
    file_context: Optional[str] = None      # 첨부 파일 컨텍스트
    url_context: Optional[str] = None       # URL 컨텍스트
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Agent 실행 결과"""
    agent_type: AgentType
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    execution_time_ms: int = 0
```

---

## 2. Agent 인터페이스 설계

### 2.1 BaseAgent 추상 클래스 — `app/agents/base.py`

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from app.models.agent import AgentContext, AgentResult, AgentType
from app.models.search import PipelineState


class BaseAgent(ABC):
    """모든 Agent의 기본 인터페이스"""

    def __init__(self, agent_type: AgentType):
        self.agent_type = agent_type

    @abstractmethod
    async def execute(
        self,
        state: PipelineState,
        context: AgentContext,
    ) -> AgentResult:
        """
        Agent 핵심 실행 메서드.

        Args:
            state: 파이프라인 누적 상태 (이전 Phase 결과 포함)
            context: 세션/사용자 컨텍스트

        Returns:
            AgentResult: 실행 결과
        """
        ...
```

### 2.2 Agent 간 데이터 플로우

```
┌──────────────┐
│ User Query   │ "tjesmgr BOOTの使い方を教えてください"
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ Orchestrator.execute(query, context)                         │
│                                                              │
│  state = PipelineState()                                     │
│                                                              │
│  ┌─Phase 0─────────────────────────────────────────────────┐ │
│  │ QueryAgent.execute(state, context)                      │ │
│  │                                                          │ │
│  │ Input:  raw_query = "tjesmgr BOOTの使い方を教えてください"│ │
│  │ Output: state.query_plan = QueryPlan(                    │ │
│  │           intent=COMMAND,                                │ │
│  │           language=JA,                                   │ │
│  │           products=[ProductMatch("mvs_openframe_7.1",    │ │
│  │                      confidence=0.95,                    │ │
│  │                      matched_keywords=["tjesmgr","boot"])│ │
│  │           ],                                             │ │
│  │           command_names=["tjesmgr"],                     │ │
│  │           requires_code_analysis=False                   │ │
│  │         )                                                │ │
│  └──────────────────────────────────────────────────────────┘ │
│                          │                                    │
│                          ▼                                    │
│  ┌─Phase 1─────────────────────────────────────────────────┐ │
│  │ SearchAgent.execute(state, context)                     │ │
│  │                                                          │ │
│  │ Input:  state.query_plan (QueryPlan)                     │ │
│  │ Flow:                                                    │ │
│  │   1. BGE-M3 dense embed → Neo4j vector search           │ │
│  │   2. BGE-M3 sparse → keyword search                     │ │
│  │   3. Neo4j graph → entity traversal                      │ │
│  │   4. Summary BM25 → 요약본 검색                           │ │
│  │   5. RRF fusion → top-K 병합                              │ │
│  │                                                          │ │
│  │ Output: state.phase_results[1] = PhaseResult(            │ │
│  │           phase=1, max_score=0.85,                       │ │
│  │           chunks=[SearchChunk(score=0.85, ...), ...]     │ │
│  │         )                                                │ │
│  └──────────────────────────────────────────────────────────┘ │
│                          │                                    │
│                          ▼                                    │
│  ┌─Phase 2─────────────────────────────────────────────────┐ │
│  │ DomainAgent.execute(state, context)                     │ │
│  │                                                          │ │
│  │ Input:  state.query_plan + state.phase_results[1]        │ │
│  │ Flow:                                                    │ │
│  │   - COMMAND/ERROR_CODE → Template 기반 응답 생성          │ │
│  │   - FREEFORM → Qwen3 LLM + Phase 1 chunks 기반 생성      │ │
│  │                                                          │ │
│  │ Output: state.phase_results[2] = PhaseResult(            │ │
│  │           phase=2, max_score=0.90,                       │ │
│  │           chunks=[SearchChunk(                           │ │
│  │             source=CPT_KNOWLEDGE,                        │ │
│  │             content="tjesmgr BOOTは...", score=0.90)]     │ │
│  │         )                                                │ │
│  └──────────────────────────────────────────────────────────┘ │
│                          │                                    │
│                          ▼                                    │
│  ┌─Phase 3─────────────────────────────────────────────────┐ │
│  │ CodeAgent.search_web_docs(state, context)               │ │
│  │                                                          │ │
│  │ Input:  state.query_plan                                 │ │
│  │ Flow:   OFCode Server /search API 호출                   │ │
│  │ Output: state.phase_results[3] = PhaseResult(...)        │ │
│  └──────────────────────────────────────────────────────────┘ │
│                          │                                    │
│                          ▼                                    │
│  ┌─Phase 4 (requires_code_analysis=True일 때)──────────────┐ │
│  │ CodeAgent.parse_code(state, context)                    │ │
│  │ ...                                                      │ │
│  └──────────────────────────────────────────────────────────┘ │
│                          │                                    │
│                          ▼                                    │
│  ┌─Phase 5 (accumulated_max_score < 0.3일 때)──────────────┐ │
│  │ FallbackAgent.execute(state, context)                   │ │
│  │ ...                                                      │ │
│  └──────────────────────────────────────────────────────────┘ │
│                          │                                    │
│                          ▼                                    │
│  ┌─Phase 6─────────────────────────────────────────────────┐ │
│  │ ResponseAgent.execute(state, context)                   │ │
│  │                                                          │ │
│  │ Input:  전체 PipelineState                               │ │
│  │ Flow:                                                    │ │
│  │   1. 전체 chunks 통합 → 점수순 정렬                        │ │
│  │   2. LLM 최종 응답 생성 (top chunks 기반)                  │ │
│  │   3. 문장별 검증 (ResponseVerifier)                        │ │
│  │   4. 용어 교정 (TermCorrector)                             │ │
│  │   5. 출처 표기 생성                                        │ │
│  │                                                          │ │
│  │ Output: FinalResponse                                    │ │
│  └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### 2.3 Orchestrator 핵심 로직 — `app/agents/orchestrator.py`

```python
class Orchestrator:
    """파이프라인 총괄 제어"""

    def __init__(self):
        self.query_agent = QueryAgent()
        self.search_agent = SearchAgent()
        self.domain_agent = DomainAgent()
        self.code_agent = CodeAgent()
        self.fallback_agent = FallbackAgent()
        self.response_agent = ResponseAgent()

    async def execute(
        self,
        query: str,
        context: AgentContext,
    ) -> FinalResponse:
        state = PipelineState()

        # Phase 0: Query Understanding (항상)
        await self.query_agent.execute(state, context)

        # Phase 1: Embedding Search (항상)
        await self.search_agent.execute(state, context)

        # Phase 2: Domain Knowledge (항상)
        await self.domain_agent.execute(state, context)

        # Phase 3: OFCode Web Docs (항상)
        await self.code_agent.execute_web_search(state, context)

        # Phase 4: OFCode Parser (코드 쿼리일 때만)
        if state.query_plan.requires_code_analysis:
            await self.code_agent.execute_parser(state, context)

        # Phase 5: Fallback (Phase 1~4 max_score < 0.3일 때만)
        if state.needs_fallback:
            state.fallback_triggered = True
            await self.fallback_agent.execute(state, context)

        # Phase 6: Response Generation (항상)
        return await self.response_agent.execute(state, context)

    async def execute_stream(
        self,
        query: str,
        context: AgentContext,
    ) -> AsyncGenerator[str, None]:
        """SSE 스트리밍 버전"""
        state = PipelineState()

        # Phase 0~1: 검색 단계 (진행률 이벤트 전송)
        yield self._sse_event("phase", {"phase": 0, "name": "쿼리 분석"})
        await self.query_agent.execute(state, context)

        yield self._sse_event("phase", {"phase": 1, "name": "문서 검색"})
        await self.search_agent.execute(state, context)

        yield self._sse_event("phase", {"phase": 2, "name": "도메인 지식"})
        await self.domain_agent.execute(state, context)

        yield self._sse_event("phase", {"phase": 3, "name": "웹문서 검색"})
        await self.code_agent.execute_web_search(state, context)

        if state.query_plan.requires_code_analysis:
            yield self._sse_event("phase", {"phase": 4, "name": "코드 분석"})
            await self.code_agent.execute_parser(state, context)

        if state.needs_fallback:
            yield self._sse_event("phase", {"phase": 5, "name": "추가 탐색"})
            await self.fallback_agent.execute(state, context)

        # Phase 6: 스트리밍 응답 생성
        yield self._sse_event("phase", {"phase": 6, "name": "답변 생성"})
        async for token in self.response_agent.execute_stream(state, context):
            yield self._sse_event("token", {"content": token})

        yield self._sse_event("done", {"phases": list(state.phase_results.keys())})
```

---

## 3. 각 Agent 상세 설계

### 3.1 Query Agent (Phase 0) — `app/agents/query_agent.py`

**역할**: 쿼리 분석, 의도 분류, 제품 라우팅, 검색 전략 결정

**의존성**: 없음 (순수 규칙 기반, LLM 미사용)

```python
class QueryAgent(BaseAgent):
    """
    Phase 0: 쿼리 분석 Agent

    v1의 ProductRouterService + QueryTypeClassifier + QueryRouter를 통합.
    모든 분류는 Regex/키워드 기반 (LLM 호출 없음, < 5ms).
    """

    async def execute(self, state: PipelineState, context: AgentContext) -> AgentResult:
        raw_query = state.query_plan.raw_query if state.query_plan else context.metadata["query"]

        # 1. 언어 감지
        language = self._detect_language(raw_query)

        # 2. 쿼리 토큰화 (불용어 제거)
        tokens = self._tokenize(raw_query, language)

        # 3. 의도 분류 (Regex 패턴 매칭)
        intent = self._classify_intent(raw_query, tokens)

        # 4. 제품 라우팅 (키워드 + 패턴 스코어링)
        products = self._route_products(tokens, raw_query)

        # 5. 에러코드/명령어 추출
        error_codes = self._extract_error_codes(raw_query)
        command_names = self._extract_commands(raw_query)

        # 6. 코드 분석 필요 여부 판단
        requires_code = intent in (QueryIntent.CODE,) or \
                        any(kw in raw_query.lower() for kw in ["jcl", "cobol", "asm", "샘플", "サンプル"])

        state.query_plan = QueryPlan(
            raw_query=raw_query,
            normalized_query=" ".join(tokens),
            intent=intent,
            language=language,
            products=products,
            requires_code_analysis=requires_code,
            query_tokens=tokens,
            error_codes=error_codes,
            command_names=command_names,
        )
        return AgentResult(agent_type=AgentType.QUERY, success=True)
```

**제품 라우팅 규칙** (v1 ProductRouterService에서 계승):

| 판정 | 조건 | 처리 |
|------|------|------|
| CONFIRMED | confidence >= 0.8 AND gap >= 0.3 | 해당 제품으로 진행 |
| MULTI_PRODUCT | 2개 이상 confidence >= 0.6 | 복수 제품 검색 |
| AUTO | confidence < 0.5 | 전체 제품 검색 |

### 3.2 Search Agent (Phase 1) — `app/agents/search_agent.py`

**역할**: BGE-M3 하이브리드 검색 + Neo4j + PostgreSQL + Summary BM25

**의존성**: BGE-M3 Server (12801), Neo4j (7687), PostgreSQL (5432)

```python
class SearchAgent(BaseAgent):
    """
    Phase 1: 임베딩 문서 검색 Agent

    4가지 검색을 병렬 실행 → RRF 융합:
    1. Neo4j Vector (BGE-M3 Dense)
    2. Neo4j Graph (Entity traversal)
    3. PostgreSQL pgvector
    4. Summary BM25 (파일 시스템)
    """

    def __init__(self):
        super().__init__(AgentType.SEARCH)
        self.bge_m3 = BgeM3Client()       # tools/bge_m3_client.py
        self.neo4j = Neo4jSearchClient()   # tools/neo4j_search.py
        self.pg = PgSearchClient()         # tools/pg_search.py
        self.summary = SummarySearch()     # tools/summary_search.py

    async def execute(self, state: PipelineState, context: AgentContext) -> AgentResult:
        plan = state.query_plan
        product_filter = plan.products[0].product_id if plan.products else None

        # 1. BGE-M3 하이브리드 임베딩 (Dense + Sparse)
        embedding = await self.bge_m3.hybrid_encode(plan.normalized_query)

        # 2. 4종 검색 병렬 실행
        neo4j_vec, neo4j_graph, pg_vec, summary = await asyncio.gather(
            self.neo4j.vector_search(embedding.dense, top_k=10, product=product_filter),
            self.neo4j.graph_search(plan.query_tokens, product=product_filter),
            self.pg.vector_search(embedding.dense, top_k=10, product=product_filter),
            self.summary.bm25_search(plan.query_tokens, product=product_filter),
        )

        # 3. RRF (Reciprocal Rank Fusion) 병합
        fused = self._rrf_fusion([neo4j_vec, neo4j_graph, pg_vec, summary], k=60)

        # 4. 결과 저장
        chunks = [self._to_search_chunk(item) for item in fused[:20]]
        max_score = chunks[0].score if chunks else 0.0

        state.add_phase_result(PhaseResult(
            phase=1, phase_name="embedding_search",
            chunks=chunks, max_score=max_score,
        ))
        return AgentResult(agent_type=AgentType.SEARCH, success=True)

    def _rrf_fusion(self, result_lists, k=60):
        """Reciprocal Rank Fusion: score = Σ 1/(k + rank_i)"""
        scores = {}
        for results in result_lists:
            for rank, item in enumerate(results):
                chunk_id = item["chunk_id"]
                scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

### 3.3 Domain Agent (Phase 2) — `app/agents/domain_agent.py`

**역할**: Qwen3 CPT 도메인 지식 기반 LLM 생성

**의존성**: Qwen3 32B (12810)

```python
class DomainAgent(BaseAgent):
    """
    Phase 2: CPT 도메인 지식 Agent

    2가지 응답 경로:
    - 구조화 쿼리 (COMMAND, ERROR_CODE, CONFIG) → 템플릿 + 검색결과 기반 응답
    - 비구조화 쿼리 (FREEFORM, CONCEPT 등) → LLM 생성 + Phase 1 결과 기반
    """

    STRUCTURED_INTENTS = {QueryIntent.COMMAND, QueryIntent.ERROR_CODE, QueryIntent.CONFIG}

    async def execute(self, state: PipelineState, context: AgentContext) -> AgentResult:
        plan = state.query_plan
        phase1_chunks = state.phase_results.get(1, PhaseResult(phase=1, phase_name="")).chunks

        if plan.intent in self.STRUCTURED_INTENTS and phase1_chunks:
            # 구조화 응답: 검색 결과 기반 템플릿 (LLM 최소화)
            answer = self._build_template_response(plan, phase1_chunks)
            confidence = 0.9  # 템플릿 기반은 높은 신뢰도
        else:
            # 비구조화: Qwen3 LLM 생성
            answer, confidence = await self._generate_with_llm(plan, phase1_chunks)

        state.add_phase_result(PhaseResult(
            phase=2, phase_name="domain_knowledge",
            chunks=[SearchChunk(
                chunk_id=f"domain_{plan.intent.value}",
                content=answer,
                score=confidence,
                source=SearchSource.CPT_KNOWLEDGE,
            )],
            max_score=confidence,
        ))
        return AgentResult(agent_type=AgentType.DOMAIN, success=True)

    async def _generate_with_llm(self, plan: QueryPlan, chunks: List[SearchChunk]):
        """Qwen3 32B + RAG 컨텍스트 기반 생성"""
        context_text = "\n\n".join([
            f"[Source: {c.doc_name} p.{c.page_number}]\n{c.content}"
            for c in chunks[:5]  # top-5 chunks
        ])

        prompt = f"""以下の検索結果を基に質問に回答してください。
検索結果に情報がない場合は「情報が見つかりませんでした」と回答してください。
検索結果にない情報を追加しないでください。

## 検索結果
{context_text}

## 質問
{plan.raw_query}

## 回答"""

        response = await self.qwen3.chat(prompt, temperature=0.3)
        # ... confidence 계산
        return response, confidence
```

### 3.4 Code Agent (Phase 3, 4) — `app/agents/code_agent.py`

**역할**: OFCode Server 연동 — 웹문서 검색 + OpenFrame Parser

**의존성**: OFCode Server (12820)

```python
class CodeAgent(BaseAgent):
    """
    Phase 3: OFCode 웹문서 검색
    Phase 4: OFCode Parser 기반 코드 분석/생성
    """

    def __init__(self):
        super().__init__(AgentType.CODE)
        self.ofcode = OFCodeClient()  # tools/ofcode_client.py

    async def execute_web_search(self, state: PipelineState, context: AgentContext):
        """Phase 3: docs.tmaxsoft.com 웹문서 검색"""
        plan = state.query_plan
        try:
            results = await self.ofcode.search_web_docs(
                query=plan.normalized_query,
                product=plan.products[0].product_id if plan.products else None,
                language=plan.language.value,
            )
            chunks = [self._web_result_to_chunk(r) for r in results]
            max_score = chunks[0].score if chunks else 0.0
        except Exception as e:
            # OFCode 불안정 시 graceful skip
            chunks, max_score = [], 0.0

        state.add_phase_result(PhaseResult(
            phase=3, phase_name="ofcode_web_search",
            chunks=chunks, max_score=max_score,
        ))

    async def execute_parser(self, state: PipelineState, context: AgentContext):
        """Phase 4: OpenFrame Parser 기반 코드 분석"""
        plan = state.query_plan
        try:
            parsed = await self.ofcode.parse_code(
                query=plan.raw_query,
                code_type=self._detect_code_type(plan),
            )
            chunks = [self._parse_result_to_chunk(parsed)]
            max_score = chunks[0].score if chunks else 0.0
        except Exception:
            chunks, max_score = [], 0.0

        state.add_phase_result(PhaseResult(
            phase=4, phase_name="ofcode_parser",
            chunks=chunks, max_score=max_score,
        ))
```

### 3.5 Fallback Agent (Phase 5) — `app/agents/fallback_agent.py`

**역할**: Qwen3 자체 지식 + Tool Calling (최후 수단)

**의존성**: Qwen3 32B (12810) + Tool Calling

```python
class FallbackAgent(BaseAgent):
    """
    Phase 5: Qwen3 자체 지식 Fallback

    조건: Phase 1~4 누적 max_score < 0.3
    특징:
    - 검증 불가능 경고 표시
    - Tool Calling으로 추가 검색 가능
    - 응답에 [自体知識] 태그 표기
    """

    async def execute(self, state: PipelineState, context: AgentContext) -> AgentResult:
        plan = state.query_plan

        # Tool definitions for Qwen3
        tools = [
            {"type": "function", "function": {
                "name": "web_search",
                "description": "Search the web for information",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"}
                }}
            }},
        ]

        prompt = f"""あなたはTmaxSoft OpenFrame製品の専門家です。
以下の質問に回答してください。ただし、確信がない情報には必ず「※未検証」と付記してください。

質問: {plan.raw_query}"""

        response = await self.qwen3.chat(
            prompt,
            temperature=0.5,
            tools=tools,
            tool_choice="auto",
        )

        state.add_phase_result(PhaseResult(
            phase=5, phase_name="llm_fallback",
            chunks=[SearchChunk(
                chunk_id="fallback_0",
                content=f"[自体知識] {response}",
                score=0.2,  # 폴백은 항상 낮은 점수
                source=SearchSource.LLM_FALLBACK,
            )],
            max_score=0.2,
        ))
        return AgentResult(agent_type=AgentType.FALLBACK, success=True)
```

### 3.6 Response Agent (Phase 6) — `app/agents/response_agent.py`

**역할**: 결과 통합, 신뢰도 검증, 출처 표기, 용어 교정

**의존성**: Qwen3 32B (12810), v1 ResponseVerifier/TermCorrector 로직

```python
class ResponseAgent(BaseAgent):
    """
    Phase 6: 최종 응답 생성 Agent

    1. 전체 Phase 결과에서 best chunks 선정
    2. Qwen3로 최종 통합 답변 생성
    3. 문장별 검증 (word overlap 기반)
    4. 용어 교정 (TERM_CORRECTIONS 사전)
    5. 출처 표기 생성
    """

    def __init__(self):
        super().__init__(AgentType.RESPONSE)
        self.verifier = ResponseVerifier()
        self.term_corrector = TermCorrector()

    async def execute(self, state: PipelineState, context: AgentContext) -> FinalResponse:
        plan = state.query_plan
        all_chunks = state.get_all_chunks(min_score=0.1)

        # Phase 2에서 이미 LLM 생성 답변이 있으면 활용
        domain_result = state.phase_results.get(2)
        if domain_result and domain_result.max_score >= 0.7:
            raw_answer = domain_result.chunks[0].content
        else:
            # 전체 chunks 기반 최종 LLM 생성
            raw_answer = await self._generate_final(plan, all_chunks, context)

        # 용어 교정
        corrected_answer, corrections = self.term_corrector.correct(raw_answer)

        # 문장별 검증
        verification = self.verifier.verify_sentences(corrected_answer, all_chunks)

        # 폴백 경고 추가
        if state.fallback_triggered:
            corrected_answer = "⚠️ 검증된 문서에서 충분한 정보를 찾지 못했습니다. 아래는 AI의 일반 지식 기반 답변입니다.\n\n" + corrected_answer

        # 출처 표기
        sources = self._build_sources(all_chunks[:5])

        # confidence 계산
        verified_ratio = sum(1 for v in verification if v.level == VerificationLevel.VERIFIED) / max(len(verification), 1)

        return FinalResponse(
            success=True,
            answer=corrected_answer,
            answer_language=plan.language.value,
            product=plan.products[0].product_id if plan.products else "auto",
            query_intent=plan.intent.value,
            phases_executed=list(state.phase_results.keys()),
            fallback_used=state.fallback_triggered,
            verification=verification,
            overall_confidence=verified_ratio,
            sources=sources,
        )
```

---

## 4. 인프라 클라이언트 설계 — `app/agents/tools/`

### 4.1 BGE-M3 Client — `tools/bge_m3_client.py`

```python
class BgeM3Client:
    """BGE-M3 임베딩 서버 클라이언트 (192.168.8.11:12801)"""

    BASE_URL = "http://192.168.8.11:12801"
    DENSE_DIM = 1024
    TIMEOUT = 5.0

    async def dense_encode(self, text: str) -> List[float]:
        """Dense 임베딩 (1024d)"""
        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            resp = await client.post(f"{self.BASE_URL}/v1/embeddings",
                json={"input": text})
            return resp.json()["data"][0]["embedding"]

    async def sparse_encode(self, text: str) -> Dict[str, float]:
        """Sparse 임베딩 (learned sparse weights)"""
        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            resp = await client.post(f"{self.BASE_URL}/v1/sparse",
                json={"input": text})
            return resp.json()["data"][0]["sparse_weights"]

    async def hybrid_encode(self, text: str) -> HybridEmbedding:
        """Dense + Sparse 동시 임베딩"""
        dense, sparse = await asyncio.gather(
            self.dense_encode(text),
            self.sparse_encode(text),
        )
        return HybridEmbedding(dense=dense, sparse_weights=sparse)
```

### 4.2 Neo4j Search Client — `tools/neo4j_search.py`

```python
class Neo4jSearchClient:
    """Neo4j Vector + Graph 검색 클라이언트"""

    async def vector_search(
        self, embedding: List[float], top_k: int = 10, product: str = None
    ) -> List[Dict]:
        """Neo4j Vector Index 검색 (cosine similarity)"""
        where_clause = f'WHERE c.product = "{product}"' if product else ""
        query = f"""
        CALL db.index.vector.queryNodes('chunk_embedding', $top_k, $embedding)
        YIELD node AS c, score
        {where_clause}
        RETURN c.chunk_id AS chunk_id,
               c.content AS content,
               c.doc_name AS doc_name,
               c.page AS page,
               c.product AS product,
               score
        ORDER BY score DESC
        """
        return await self._execute(query, {"top_k": top_k, "embedding": embedding})

    async def graph_search(
        self, tokens: List[str], product: str = None, max_hops: int = 2
    ) -> List[Dict]:
        """Entity 기반 Graph 탐색 (entity → MENTIONS → chunk)"""
        query = """
        UNWIND $tokens AS token
        MATCH (e:Entity)
        WHERE toLower(e.name) CONTAINS toLower(token)
        MATCH (e)-[:MENTIONS]-(c:Chunk)
        RETURN DISTINCT c.chunk_id AS chunk_id,
               c.content AS content,
               c.doc_name AS doc_name,
               e.name AS matched_entity,
               1.0 / (1 + size(shortestPath((e)-[:MENTIONS*]-(c)))) AS score
        ORDER BY score DESC
        LIMIT 10
        """
        return await self._execute(query, {"tokens": tokens})
```

### 4.3 Qwen3 LLM Client — `tools/qwen3_client.py`

```python
class Qwen3Client:
    """Qwen3 32B vLLM 클라이언트 (OpenAI 호환 API)"""

    BASE_URL = "http://192.168.8.11:12810/v1"
    MODEL = "Qwen/Qwen3-32B"
    TIMEOUT = 60.0

    async def chat(
        self, prompt: str,
        system: str = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        tools: List[Dict] = None,
        tool_choice: str = None,
    ) -> str:
        """동기 채팅 완성"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"

        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            resp = await client.post(f"{self.BASE_URL}/chat/completions", json=payload)
            return resp.json()["choices"][0]["message"]["content"]

    async def chat_stream(self, prompt: str, **kwargs) -> AsyncGenerator[str, None]:
        """SSE 스트리밍 채팅"""
        kwargs["stream"] = True
        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            async with client.stream("POST", f"{self.BASE_URL}/chat/completions",
                json={**kwargs, "stream": True}) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        data = json.loads(line[6:])
                        delta = data["choices"][0]["delta"].get("content", "")
                        if delta:
                            yield delta
```

### 4.4 OFCode Client — `tools/ofcode_client.py`

```python
class OFCodeClient:
    """OFCode Server 클라이언트 (192.168.8.11:12820)"""

    BASE_URL = "http://192.168.8.11:12820"
    TIMEOUT = 10.0

    async def search_web_docs(
        self, query: str, product: str = None, language: str = "ja"
    ) -> List[Dict]:
        """Phase 3: 웹문서 검색"""
        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            resp = await client.post(f"{self.BASE_URL}/search", json={
                "query": query, "product": product, "language": language,
            })
            return resp.json().get("results", [])

    async def parse_code(self, query: str, code_type: str = "jcl") -> Dict:
        """Phase 4: OpenFrame Parser 기반 코드 분석"""
        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            resp = await client.post(f"{self.BASE_URL}/parse", json={
                "query": query, "code_type": code_type,
            })
            return resp.json()

    async def health_check(self) -> bool:
        """서버 상태 확인"""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.BASE_URL}/health")
                return resp.status_code == 200
        except Exception:
            return False
```

---

## 5. API 엔드포인트 설계

### 5.1 메인 채팅 API — `app/routers/chat.py`

```python
router = APIRouter(prefix="/api/v1", tags=["chat"])


class ChatRequest(BaseModel):
    """채팅 요청"""
    message: str = Field(..., min_length=1, max_length=8000)
    product: str = Field(default="auto", description="제품 필터 (auto=자동)")
    language: str = Field(default="ja")
    history: Optional[List[Dict]] = None
    file_content: Optional[str] = None
    enable_thinking: bool = Field(default=False, description="Qwen3 thinking mode")


class ChatResponse(BaseModel):
    """채팅 응답"""
    success: bool
    answer: str
    product: str
    query_intent: str
    confidence: float
    sources: List[SourceAttribution]
    phases_executed: List[int]
    fallback_used: bool
    verification: List[VerifiedSentence]
    processing_time_ms: int


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user=Depends(get_current_user)):
    """동기 RAG 채팅"""
    context = AgentContext(user_id=user.id, language=request.language)
    orchestrator = get_orchestrator()
    result = await orchestrator.execute(request.message, context)
    return ChatResponse(**result.dict())


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, user=Depends(get_current_user)):
    """SSE 스트리밍 RAG 채팅"""
    context = AgentContext(user_id=user.id, language=request.language)
    orchestrator = get_orchestrator()
    return EventSourceResponse(orchestrator.execute_stream(request.message, context))
```

### 5.2 전체 API 라우터 목록

| Method | Endpoint | 설명 | 우선순위 |
|--------|----------|------|---------|
| POST | `/api/v1/chat` | 동기 RAG 채팅 | P0 |
| POST | `/api/v1/chat/stream` | SSE 스트리밍 RAG 채팅 | P0 |
| GET | `/api/v1/products` | 사용 가능한 제품 목록 | P0 |
| GET | `/api/v1/health` | 서비스 헬스체크 | P0 |
| GET | `/api/v1/health/detailed` | 상세 헬스체크 (각 서비스) | P1 |
| POST | `/api/v1/auth/login` | JWT 로그인 | P1 |
| POST | `/api/v1/auth/register` | 사용자 등록 | P1 |
| GET | `/api/v1/admin/traces` | Agent 실행 추적 로그 | P2 |
| POST | `/api/v1/feedback` | 사용자 피드백 수집 | P2 |

---

## 6. 구현 순서 (Implementation Order)

| 순서 | 대상 | 파일 | 의존성 |
|------|------|------|--------|
| 1 | 프로젝트 초기화 | `main.py`, `core/config.py`, `requirements.txt` | 없음 |
| 2 | 데이터 모델 | `models/query.py`, `search.py`, `response.py`, `agent.py` | 없음 |
| 3 | BGE-M3 클라이언트 | `agents/tools/bge_m3_client.py` | BGE-M3 서버 |
| 4 | Neo4j 클라이언트 | `agents/tools/neo4j_search.py` | Neo4j 서버 |
| 5 | PostgreSQL 클라이언트 | `agents/tools/pg_search.py` | PostgreSQL 서버 |
| 6 | Qwen3 클라이언트 | `agents/tools/qwen3_client.py` | vLLM 서버 |
| 7 | OFCode 클라이언트 | `agents/tools/ofcode_client.py` | OFCode 서버 |
| 8 | Summary 검색 | `agents/tools/summary_search.py` | 파일 시스템 |
| 9 | BaseAgent | `agents/base.py`, `agents/types.py` | 모델 |
| 10 | Query Agent | `agents/query_agent.py` | BaseAgent |
| 11 | Search Agent | `agents/search_agent.py` | BGE-M3, Neo4j, PG, Summary |
| 12 | Domain Agent | `agents/domain_agent.py` | Qwen3, Search Agent |
| 13 | Code Agent | `agents/code_agent.py` | OFCode |
| 14 | Fallback Agent | `agents/fallback_agent.py` | Qwen3 |
| 15 | Response Agent | `agents/response_agent.py` | 전체 Agent |
| 16 | Orchestrator | `agents/orchestrator.py` | 전체 Agent |
| 17 | API 라우터 | `routers/chat.py`, `routers/health.py` | Orchestrator |
| 18 | Frontend | `frontend/` | API 라우터 |
