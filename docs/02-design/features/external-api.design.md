# Design: External REST API + API Documentation

> Feature: `external-api`
> Plan Reference: `docs/01-plan/features/external-api.plan.md`
> Created: 2026-03-04
> Status: Design Phase

---

## 1. System Architecture

```
  External Clients (curl, Browser, Slack Bot, etc.)
         │
         ▼  HTTP :8000
  ┌──────────────────────────────────────────────────────┐
  │  FastAPI Application  (api/server.py)                │
  │                                                      │
  │  Middleware Stack:                                    │
  │   ├─ CORSMiddleware (origin: *)                      │
  │   ├─ RequestLoggingMiddleware                        │
  │   └─ APIKeyAuthMiddleware (skip: /v1/health, /docs)  │
  │                                                      │
  │  Routes:                                             │
  │   ├─ POST /v1/query         → query_router           │
  │   ├─ POST /v1/query/stream  → query_router (SSE)     │
  │   ├─ GET  /v1/health        → health_router          │
  │   └─ GET  /v1/products      → products_router        │
  │                                                      │
  │  Lifespan:                                           │
  │   startup  → Orchestrator() 생성                      │
  │   shutdown → orchestrator.close()                    │
  └──────────────┬───────────────────────────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────────────────────────┐
  │  Orchestrator (기존, 변경 없음)                        │
  │  Phase 0→1→2→3→(4)→(5)→6 Pipeline                   │
  │                                                      │
  │  ┌─────────┐ ┌─────────┐ ┌────────┐ ┌─────────┐    │
  │  │Neo4j    │ │PostgreSQL│ │BGE-M3  │ │Qwen3 LLM│    │
  │  │(Graph+  │ │(pgvector)│ │(Embed) │ │(32B)    │    │
  │  │Vector)  │ │          │ │        │ │         │    │
  │  └─────────┘ └─────────┘ └────────┘ └─────────┘    │
  └──────────────────────────────────────────────────────┘
```

---

## 2. File Structure (신규 생성)

```
ofkms_v2/
├── api/
│   ├── __init__.py              # (empty)
│   ├── server.py                # FastAPI app, lifespan, middleware, router 조립
│   ├── deps.py                  # Orchestrator 싱글턴, get_orchestrator()
│   ├── models/
│   │   ├── __init__.py          # (empty)
│   │   ├── request.py           # QueryRequest Pydantic model
│   │   └── response.py          # QueryResponse, HealthResponse, ProductsResponse
│   ├── middleware/
│   │   ├── __init__.py          # (empty)
│   │   └── auth.py              # APIKeyAuthMiddleware
│   └── routes/
│       ├── __init__.py          # (empty)
│       ├── query.py             # POST /v1/query, POST /v1/query/stream
│       ├── health.py            # GET /v1/health
│       └── products.py          # GET /v1/products
├── docs/
│   └── api-reference.html       # 단일 HTML API 문서
└── requirements.txt             # 신규 의존성 추가
```

---

## 3. Detailed Component Design

### 3.1 `api/models/request.py` — Request Models

```python
class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000,
                       description="질의 텍스트")
    language: Optional[str] = Field(None, pattern="^(ja|ko|en)$",
                                    description="응답 언어 (미지정 시 자동 감지)")
    product: Optional[str] = Field(None,
                                   description="제품 필터 (예: openframe_osc_7)")
    include_sources: bool = Field(True,
                                  description="출처 정보 포함 여부")
    include_phases: bool = Field(False,
                                 description="Phase 타이밍 정보 포함 여부")
```

### 3.2 `api/models/response.py` — Response Models

```python
class SourceInfo(BaseModel):
    document: str
    page: Optional[int] = None
    score: float
    type: str

class UsageInfo(BaseModel):
    total_time_ms: int
    phases_executed: List[int]
    fallback_used: bool
    phase_times: Optional[Dict[str, int]] = None  # include_phases=True 시

class QueryResponse(BaseModel):
    success: bool
    answer: str
    language: str
    confidence: float
    intent: str
    product: str
    sources: Optional[List[SourceInfo]] = None     # include_sources=True 시
    usage: UsageInfo

class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None

class ServiceStatus(BaseModel):
    status: str          # "ok" | "error"
    latency_ms: int

class HealthResponse(BaseModel):
    status: str          # "healthy" | "degraded" | "unhealthy"
    services: Dict[str, ServiceStatus]
    version: str

class ProductInfo(BaseModel):
    id: str
    name: str
    keywords: List[str]

class ProductsResponse(BaseModel):
    products: List[ProductInfo]
```

### 3.3 `api/deps.py` — Dependencies

```python
# Orchestrator를 lifespan에서 생성/소멸, app.state에 저장
_orchestrator: Optional[Orchestrator] = None

async def get_orchestrator() -> Orchestrator:
    """FastAPI Depends로 주입"""
    return _orchestrator

async def init_orchestrator():
    """서버 시작 시 호출"""
    global _orchestrator
    _orchestrator = Orchestrator()

async def close_orchestrator():
    """서버 종료 시 호출"""
    global _orchestrator
    if _orchestrator:
        await _orchestrator.close()
        _orchestrator = None
```

### 3.4 `api/middleware/auth.py` — API Key Authentication

```python
class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """X-API-Key 헤더 기반 인증 미들웨어"""

    EXEMPT_PATHS = {"/v1/health", "/docs", "/openapi.json", "/"}

    def __init__(self, app, api_keys: List[str]):
        super().__init__(app)
        self.api_keys = set(api_keys)

    async def dispatch(self, request, call_next):
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key not in self.api_keys:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or missing API key"}
            )
        return await call_next(request)
```

**Config 추가** (`app/core/config.py`):
```python
# 기존 Settings 클래스에 추가
API_KEYS: str = ""           # 쉼표 구분 (예: "key1,key2,key3")
API_HOST: str = "0.0.0.0"
API_PORT: int = 8000
```

### 3.5 `api/routes/health.py` — Health Endpoint

```python
router = APIRouter(prefix="/v1", tags=["health"])

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """5개 인프라 서비스 상태 체크"""
    services = {}
    checks = {
        "llm_qwen3":    (settings.LLM_BASE_URL + "/models", "GET"),
        "bge_m3":       (settings.BGE_M3_BASE_URL + "/v1/embeddings", "POST"),
        "neo4j":        (None, "neo4j"),       # driver.verify_connectivity()
        "postgresql":   (None, "pg"),           # asyncpg.connect()
        "ofcode":       (settings.OFCODE_BASE_URL + "/health", "GET"),
    }
    # 각 서비스에 대해 timeout 3초로 체크
    # 모든 체크 asyncio.gather 병렬 실행
    # 전체 상태: 모두 ok → healthy, 일부 실패 → degraded, 전부 실패 → unhealthy
    return HealthResponse(status=overall, services=services, version="2.0.0")
```

### 3.6 `api/routes/products.py` — Products Endpoint

```python
router = APIRouter(prefix="/v1", tags=["products"])

@router.get("/products", response_model=ProductsResponse)
async def list_products():
    """PRODUCT_KEYWORDS에서 제품 목록 반환"""
    # query_agent.py의 PRODUCT_KEYWORDS 딕셔너리를 직접 import
    # 각 product_id → name 매핑 (product_id를 사람이 읽기 쉬운 이름으로)
    return ProductsResponse(products=product_list)
```

**Product Name 매핑:**
```python
_PRODUCT_NAMES = {
    "mvs_openframe_7.1": "MVS OpenFrame 7.1",
    "openframe_hidb_7": "OpenFrame HiDB 7",
    "openframe_osc_7": "OpenFrame OSC 7 (CICS)",
    "tibero_7": "Tibero 7",
    "ofasm_4": "OFASM 4",
    "ofcobol_4": "OFCOBOL 4",
    "tmax_6": "Tmax 6",
    "jeus_8": "JEUS 8",
    "webtob_5": "WebtoB 5",
    "ofstudio_7": "OFStudio 7",
    "protrieve_7": "Protrieve 7",
    "xsp_openframe_7": "XSP OpenFrame 7 (Fujitsu)",
}
```

### 3.7 `api/routes/query.py` — Query Endpoints

#### 3.7.1 `POST /v1/query` (동기 응답)

```python
router = APIRouter(prefix="/v1", tags=["query"])

@router.post("/query", response_model=QueryResponse)
async def query(
    req: QueryRequest,
    orch: Orchestrator = Depends(get_orchestrator)
):
    """메인 질의 — Orchestrator 파이프라인 전체 실행 후 JSON 응답"""
    response: FinalResponse = await orch.execute(req.query)

    # FinalResponse → QueryResponse 변환
    sources = None
    if req.include_sources:
        sources = [SourceInfo(...) for s in response.sources]

    phase_times = response.phase_times if req.include_phases else None

    return QueryResponse(
        success=response.success,
        answer=response.answer,
        language=response.answer_language,
        confidence=response.overall_confidence,
        intent=response.query_intent,
        product=response.product,
        sources=sources,
        usage=UsageInfo(
            total_time_ms=response.total_time_ms,
            phases_executed=response.phases_executed,
            fallback_used=response.fallback_used,
            phase_times=phase_times,
        )
    )
```

#### 3.7.2 `POST /v1/query/stream` (SSE 스트리밍)

스트리밍 구현을 위해 Orchestrator에 **Phase별 콜백 Hook**을 추가합니다.

```python
@router.post("/query/stream")
async def query_stream(
    req: QueryRequest,
    orch: Orchestrator = Depends(get_orchestrator)
):
    """SSE 스트리밍 — Phase별 진행 상황 + 최종 응답"""

    async def event_generator():
        # Orchestrator.execute_with_hooks()를 사용
        # 각 Phase 완료 시 SSE event 발행
        async for event in orch.execute_streaming(req.query):
            yield event

    return EventSourceResponse(event_generator())
```

**Orchestrator 확장** (`orchestrator.py`에 `execute_streaming` 추가):

```python
async def execute_streaming(self, query: str):
    """Phase별 SSE 이벤트를 yield하는 비동기 제너레이터"""
    t0 = time.perf_counter()
    state = PipelineState(query_plan=QueryPlan(raw_query=query))

    phases = [
        (0, "query_analysis", self.query_agent),
        (1, "embedding_search", self.search_agent),
        (2, "domain_knowledge", self.domain_agent),
        (3, "ofcode_web", self.code_agent),     # execute_web_search
    ]

    for phase_num, phase_name, agent in phases:
        pt0 = time.perf_counter()
        if phase_num == 3:
            await agent.execute_web_search(state)
        else:
            await agent.execute(state)
        elapsed = int((time.perf_counter() - pt0) * 1000)

        yield {
            "event": "phase",
            "data": json.dumps({
                "phase": phase_num,
                "name": phase_name,
                "status": "complete",
                "time_ms": elapsed
            })
        }

    # Phase 4 (조건부)
    if state.query_plan.requires_code_analysis:
        pt0 = time.perf_counter()
        await self.code_agent.execute_parser(state)
        elapsed = int((time.perf_counter() - pt0) * 1000)
        yield {"event": "phase", "data": json.dumps({...})}

    # Phase 5 (조건부 fallback)
    if state.needs_fallback:
        state.fallback_triggered = True
        pt0 = time.perf_counter()
        await self.fallback_agent.execute(state)
        elapsed = int((time.perf_counter() - pt0) * 1000)
        yield {"event": "phase", "data": json.dumps({...})}

    # Phase 6: Response
    response = await self.response_agent.execute(state)
    total = int((time.perf_counter() - t0) * 1000)
    response.total_time_ms = total

    yield {
        "event": "answer",
        "data": json.dumps({
            "answer": response.answer,
            "confidence": response.overall_confidence,
            "language": response.answer_language,
            "intent": response.query_intent,
            "product": response.product,
        })
    }

    yield {
        "event": "done",
        "data": json.dumps({"total_time_ms": total})
    }
```

### 3.8 `api/server.py` — FastAPI Application

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_orchestrator()
    logger.info("Orchestrator initialized")
    yield
    # Shutdown
    await close_orchestrator()
    logger.info("Orchestrator closed")

app = FastAPI(
    title="OFKMS API",
    version="2.0.0",
    description="OpenFrame Knowledge Management System API",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth
settings = get_settings()
api_keys = [k.strip() for k in settings.API_KEYS.split(",") if k.strip()]
if api_keys:
    app.add_middleware(APIKeyAuthMiddleware, api_keys=api_keys)

# Routes
app.include_router(health_router)
app.include_router(products_router)
app.include_router(query_router)
```

**실행 방법:**
```bash
# API 서버 시작
uvicorn api.server:app --host 0.0.0.0 --port 8000

# 또는 python 직접 실행
python -m api.server
```

---

## 4. Error Handling Design

### HTTP Status Codes

| Code | Condition | Response Body |
|------|-----------|---------------|
| 200 | 성공 | `QueryResponse` / `HealthResponse` / `ProductsResponse` |
| 400 | 잘못된 요청 (query 누락, 유효하지 않은 language 등) | `{"error": "...", "detail": "..."}` |
| 401 | API Key 누락/잘못됨 | `{"error": "Invalid or missing API key"}` |
| 422 | Pydantic 검증 실패 | FastAPI 기본 validation error |
| 500 | 내부 서버 오류 (Pipeline 예외) | `{"error": "Internal server error", "detail": "..."}` |
| 503 | 인프라 서비스 전부 다운 | `{"error": "Service unavailable"}` |

### Exception Handler

```python
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)}
    )
```

---

## 5. Config Additions (`app/core/config.py`)

기존 Settings 클래스에 3개 필드 추가:

```python
# API Server
API_KEYS: str = ""              # 쉼표 구분 API Keys
API_HOST: str = "0.0.0.0"      # 바인딩 호스트
API_PORT: int = 8000            # 바인딩 포트
```

`.env` 예시:
```env
API_KEYS=ofkms-dev-key-001,ofkms-prod-key-002
API_HOST=0.0.0.0
API_PORT=8000
```

---

## 6. API Documentation HTML Design

### 6.1 레이아웃 구조

```
┌──────────────────────────────────────────────────────────────┐
│  Header: "OFKMS API Reference v2.0"    [Dark/Light] [⌘K]   │
├──────────┬──────────────────────────┬────────────────────────┤
│          │                          │                        │
│ Sidebar  │    Main Content          │   Code Examples        │
│ (Nav)    │    (Descriptions)        │   (curl/Python/JS)     │
│          │                          │                        │
│ Overview │  ## POST /v1/query       │  ```bash               │
│ Auth     │                          │  curl -X POST ...      │
│ ────     │  Parameters:             │  ```                   │
│ Query    │  - query (required)      │                        │
│ Stream   │  - language (optional)   │  ```python             │
│ Health   │  - product (optional)    │  import httpx          │
│ Products │                          │  resp = httpx.post(...) │
│ ────     │  Response Schema:        │  ```                   │
│ Errors   │  { success: bool, ... }  │                        │
│ Examples │                          │  ```javascript         │
│          │                          │  fetch('/v1/query',...) │
│          │                          │  ```                   │
├──────────┴──────────────────────────┴────────────────────────┤
│  Footer: "OFKMS v2.0 — TmaxSoft"                           │
└──────────────────────────────────────────────────────────────┘
```

### 6.2 HTML 기술 사양

| 항목 | 사양 |
|------|------|
| **파일** | `docs/api-reference.html` (단일 파일, ~2000줄) |
| **CSS** | `<style>` 인라인, CSS Variables 기반 다크/라이트 |
| **JS** | `<script>` 인라인, 바닐라 JS (프레임워크 없음) |
| **폰트** | system-ui, -apple-system, sans-serif |
| **코드 폰트** | 'SF Mono', 'Fira Code', monospace |
| **반응형** | 모바일: 사이드바 숨김, 코드 아래로 |
| **외부 의존성** | 없음 (완전한 오프라인 렌더링) |

### 6.3 주요 기능

1. **다크/라이트 모드**: `<html data-theme="dark|light">`, CSS Variables 전환
2. **사이드바 네비게이션**: 고정 위치, 현재 섹션 하이라이팅 (`IntersectionObserver`)
3. **코드 탭 전환**: curl / Python / JavaScript, `localStorage`로 마지막 선택 기억
4. **JSON 스키마 트리**: 접을 수 있는 `<details>` 요소
5. **인라인 검색**: `Ctrl+K` → 모달 검색, 섹션 필터링
6. **클립보드 복사**: 코드 블록 우측 상단 복사 버튼
7. **스크롤 하이라이팅**: 현재 보고 있는 섹션이 사이드바에서 활성화

### 6.4 문서 섹션 상세

| Section | ID | Content |
|---------|-----|---------|
| Overview | `#overview` | OFKMS 소개, Base URL (`http://host:8000`), 버전 정보 |
| Authentication | `#auth` | X-API-Key 사용법, .env 설정, 에러 응답 |
| Query | `#query` | POST /v1/query — 파라미터, 요청/응답 예제, 에러 케이스 |
| Streaming | `#streaming` | POST /v1/query/stream — SSE 프로토콜, EventSource 사용법 |
| Health | `#health` | GET /v1/health — 서비스 상태, 응답 스키마 |
| Products | `#products` | GET /v1/products — 제품 목록, 필터 사용법 |
| Error Codes | `#errors` | HTTP 상태 코드 표, 에러 응답 형식 |
| Examples | `#examples` | 실제 사용 시나리오 3가지 (일본어 쿼리, 한국어 쿼리, 비교 쿼리) |

---

## 7. Orchestrator Modification

기존 `orchestrator.py`에 `execute_streaming()` 메서드만 추가합니다.
기존 `execute()` 메서드는 변경하지 않습니다.

```python
# 추가 import
import json

# 추가 메서드
async def execute_streaming(self, query: str):
    """SSE 스트리밍용 비동기 제너레이터"""
    # (설계는 3.7.2 섹션 참조)
```

---

## 8. Dependencies

### 신규 패키지

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sse-starlette>=2.0.0
```

### 기존 패키지 (변경 없음)

```
pydantic>=2.0
pydantic-settings>=2.0
httpx
neo4j
asyncpg
```

---

## 9. Implementation Order (세부)

| # | Task | File(s) | Depends On | Est. Lines |
|---|------|---------|------------|------------|
| 1 | API Request/Response 모델 | `api/models/request.py`, `api/models/response.py` | — | ~80 |
| 2 | Config 확장 (API_KEYS, HOST, PORT) | `app/core/config.py` | — | ~5 |
| 3 | Orchestrator 싱글턴 의존성 | `api/deps.py` | — | ~25 |
| 4 | API Key 인증 미들웨어 | `api/middleware/auth.py` | #2 | ~30 |
| 5 | Health 엔드포인트 | `api/routes/health.py` | #3 | ~60 |
| 6 | Products 엔드포인트 | `api/routes/products.py` | #1, #3 | ~40 |
| 7 | Query 엔드포인트 (동기) | `api/routes/query.py` | #1, #3 | ~50 |
| 8 | Orchestrator streaming 메서드 | `app/agents/orchestrator.py` | — | ~60 |
| 9 | Query Stream 엔드포인트 (SSE) | `api/routes/query.py` | #7, #8 | ~30 |
| 10 | FastAPI app 조립 | `api/server.py` | #4~#9 | ~60 |
| 11 | `__init__.py` 파일들 | `api/`, `api/models/`, `api/middleware/`, `api/routes/` | — | 4 files |
| 12 | requirements.txt 업데이트 | `requirements.txt` | — | ~3 |
| 13 | API 문서 HTML | `docs/api-reference.html` | #5~#9 | ~2000 |

---

## 10. Data Flow

### Query 요청 처리 흐름

```
Client → POST /v1/query {"query": "BMSとは?"}
  │
  ├─ [Middleware] CORS check ✓
  ├─ [Middleware] API Key verify ✓
  │
  ├─ [Route] QueryRequest 파싱 & 검증
  │
  ├─ [Deps] get_orchestrator() → Orchestrator 인스턴스
  │
  ├─ [Orchestrator] execute("BMSとは?")
  │   ├─ Phase 0: QueryAgent → intent=GENERAL, product=openframe_osc_7
  │   ├─ Phase 1: SearchAgent → 20 chunks (Neo4j+PG+Graph+Summary)
  │   ├─ Phase 2: DomainAgent → LLM 응답 생성
  │   ├─ Phase 3: CodeAgent → OFCode web docs
  │   ├─ Phase 6: ResponseAgent → FinalResponse
  │   └─ return FinalResponse
  │
  ├─ [Route] FinalResponse → QueryResponse 변환
  │   ├─ answer, confidence, intent, product
  │   ├─ sources (if include_sources=True)
  │   └─ usage (total_time_ms, phases_executed)
  │
  └─ → 200 OK + JSON body
```

### SSE 스트리밍 흐름

```
Client → POST /v1/query/stream {"query": "BMSとは?"}
  │
  ├─ [Route] EventSourceResponse 시작
  │
  ├─ [Orchestrator] execute_streaming("BMSとは?")
  │   ├─ yield {event: "phase", data: {phase: 0, ...}}  → SSE
  │   ├─ yield {event: "phase", data: {phase: 1, ...}}  → SSE
  │   ├─ yield {event: "phase", data: {phase: 2, ...}}  → SSE
  │   ├─ yield {event: "phase", data: {phase: 3, ...}}  → SSE
  │   ├─ yield {event: "answer", data: {answer: ...}}   → SSE
  │   └─ yield {event: "done", data: {total_time_ms: ...}} → SSE
  │
  └─ Connection close
```

---

## 11. Testing Strategy

### curl 테스트 명령어

```bash
# Health (인증 불필요)
curl http://localhost:8000/v1/health

# Products
curl -H "X-API-Key: ofkms-dev-key-001" http://localhost:8000/v1/products

# Query
curl -X POST http://localhost:8000/v1/query \
  -H "X-API-Key: ofkms-dev-key-001" \
  -H "Content-Type: application/json" \
  -d '{"query": "BMSとMFSの違いを教えてください"}'

# Stream (SSE)
curl -N -X POST http://localhost:8000/v1/query/stream \
  -H "X-API-Key: ofkms-dev-key-001" \
  -H "Content-Type: application/json" \
  -d '{"query": "TJESとは何ですか?"}'

# Auth 실패 테스트
curl -X POST http://localhost:8000/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "test"}'
# → 401 {"error": "Invalid or missing API key"}
```

---

## 12. Security Considerations

| Item | Design |
|------|--------|
| API Key 저장 | `.env` 파일 (`.gitignore`에 포함됨) |
| CORS | 개발: `*`, 운영: 특정 origin 제한 |
| Input Validation | Pydantic `max_length=2000` |
| Error 노출 | 운영: `detail` 필드 숨김 (DEBUG=False) |
| SQL Injection | 없음 (Orchestrator 내부는 parameterized query) |
| Rate Limiting | API 레벨 미구현 (nginx/gateway에서 처리) |
