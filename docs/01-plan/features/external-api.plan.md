# Plan: External REST API + API Documentation

> Feature: `external-api`
> Created: 2026-03-04
> Status: Plan Phase

---

## 1. Background & Motivation

현재 OFKMS v2.0은 CLI (`main.py`)를 통해서만 접근 가능합니다.
외부 시스템(웹 프론트엔드, Slack Bot, 사내 포털 등)에서 OFKMS를 활용하려면
HTTP REST API가 필요합니다.

**목표:**
- FastAPI 기반 REST API 서버 구현 (모든 외부 접근은 API를 통해서만)
- OpenAI API Reference 수준의 HTML API 문서 작성
- 기존 CLI는 내부 호출로 유지, API가 공식 외부 인터페이스

---

## 2. Scope

### In-Scope
| # | Item | Description |
|---|------|-------------|
| 1 | **FastAPI 서버** | `api/server.py` — uvicorn 기반 REST API 서버 |
| 2 | **Query Endpoint** | `POST /v1/query` — 메인 질의 API (Orchestrator 파이프라인 실행) |
| 3 | **Health Endpoint** | `GET /v1/health` — 인프라 서비스 상태 확인 |
| 4 | **Products Endpoint** | `GET /v1/products` — 지원 제품 목록 반환 |
| 5 | **API Key 인증** | `X-API-Key` 헤더 기반 간단 인증 |
| 6 | **API 문서 HTML** | OpenAI API Reference 스타일 단일 HTML 파일 |
| 7 | **CORS 설정** | 외부 프론트엔드 연동을 위한 CORS 미들웨어 |
| 8 | **Streaming 응답** | `POST /v1/query/stream` — SSE 기반 스트리밍 (Phase별 진행 상황) |

### Out-of-Scope
- 사용자 관리 / OAuth / JWT (Phase 2에서 검토)
- Rate Limiting (운영 시 nginx/API gateway에서 처리)
- WebSocket 실시간 통신
- 다국어 UI (문서는 영어 + 일본어)

---

## 3. Architecture

```
                    ┌─────────────────────────────────────────┐
                    │           External Clients               │
                    │  (Web, Slack Bot, Portal, curl, etc.)    │
                    └───────────────┬──────────────────────────┘
                                    │ HTTP/HTTPS
                    ┌───────────────▼──────────────────────────┐
                    │         FastAPI Server (api/server.py)    │
                    │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
                    │  │ /v1/query│ │/v1/health│ │/v1/products│ │
                    │  └────┬─────┘ └────┬─────┘ └────┬─────┘ │
                    │       │MiddleWare: Auth + CORS + Logging │
                    └───────┼────────────┼────────────┼────────┘
                            │            │            │
                    ┌───────▼────────────▼────────────▼────────┐
                    │           Orchestrator (기존)              │
                    │  Phase 0→1→2→3→4→5→6 Pipeline             │
                    └──────────────────────────────────────────┘
```

### 파일 구조 (신규/수정)

```
ofkms_v2/
├── api/
│   ├── __init__.py
│   ├── server.py          # FastAPI app, lifespan, CORS
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── query.py       # POST /v1/query, /v1/query/stream
│   │   ├── health.py      # GET /v1/health
│   │   └── products.py    # GET /v1/products
│   ├── models/
│   │   ├── __init__.py
│   │   ├── request.py     # QueryRequest, etc.
│   │   └── response.py    # QueryResponse (API용, 기존 FinalResponse 래핑)
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── auth.py        # X-API-Key 인증 미들웨어
│   └── deps.py            # 의존성 (Orchestrator 싱글턴 등)
├── docs/
│   └── api-reference.html # OpenAI스타일 API 문서
└── main.py                # 기존 CLI (변경 없음, 내부용 유지)
```

---

## 4. API Endpoints Design

### 4.1 `POST /v1/query`

메인 질의 엔드포인트. Orchestrator 파이프라인 전체 실행.

**Request:**
```json
{
  "query": "BMSとMFSの違いを教えてください",
  "language": "ja",          // optional: ja|ko|en (auto-detect if omitted)
  "product": "openframe_osc_7", // optional: 제품 필터
  "include_sources": true,    // optional: 출처 포함 여부
  "include_phases": false     // optional: Phase 타이밍 포함 여부
}
```

**Response:**
```json
{
  "success": true,
  "answer": "## BMS와 MFS 비교\n...",
  "language": "ja",
  "confidence": 0.85,
  "intent": "comparison",
  "product": "openframe_osc_7",
  "sources": [
    {
      "document": "OSC_Guide_7.1.pdf",
      "page": 42,
      "score": 0.92,
      "type": "neo4j_vector"
    }
  ],
  "usage": {
    "total_time_ms": 3200,
    "phases_executed": [0, 1, 2, 3, 6],
    "fallback_used": false
  }
}
```

### 4.2 `POST /v1/query/stream`

SSE(Server-Sent Events) 스트리밍. Phase별 진행 상황 실시간 전송.

**Response (SSE stream):**
```
event: phase
data: {"phase": 0, "name": "query_analysis", "status": "complete", "time_ms": 5}

event: phase
data: {"phase": 1, "name": "embedding_search", "status": "complete", "time_ms": 1200}

event: phase
data: {"phase": 2, "name": "domain_knowledge", "status": "complete", "time_ms": 800}

event: answer
data: {"answer": "## BMS와 MFS 비교\n...", "confidence": 0.85}

event: done
data: {"total_time_ms": 3200}
```

### 4.3 `GET /v1/health`

인프라 서비스 상태 체크.

**Response:**
```json
{
  "status": "healthy",
  "services": {
    "llm_qwen3": {"status": "ok", "latency_ms": 50},
    "bge_m3": {"status": "ok", "latency_ms": 30},
    "neo4j": {"status": "ok", "latency_ms": 20},
    "postgresql": {"status": "ok", "latency_ms": 15},
    "ofcode": {"status": "ok", "latency_ms": 25}
  },
  "version": "2.0.0"
}
```

### 4.4 `GET /v1/products`

지원 제품 목록 반환.

**Response:**
```json
{
  "products": [
    {"id": "mvs_openframe_7.1", "name": "MVS OpenFrame 7.1", "keywords": ["tjes", "tacf", ...]},
    {"id": "openframe_osc_7", "name": "OpenFrame OSC 7", "keywords": ["osc", "cics", "bms", ...]},
    ...
  ]
}
```

---

## 5. Authentication

- **방식**: `X-API-Key` 헤더
- **설정**: `.env` 파일에 `API_KEYS=key1,key2,key3` (쉼표 구분)
- **미들웨어**: 모든 `/v1/*` 요청에 적용 (`/v1/health` 제외)
- **에러 응답**: `401 Unauthorized` + `{"error": "Invalid API key"}`

---

## 6. API Documentation (HTML)

OpenAI API Reference (`developers.openai.com/api/reference`) 수준의 단일 HTML 파일.

### 디자인 요소
| Element | Description |
|---------|-------------|
| **레이아웃** | 3-column: 좌측 Nav + 중앙 설명 + 우측 코드 예제 |
| **네비게이션** | 고정 사이드바, 섹션별 스크롤 하이라이팅 |
| **코드 예제** | curl / Python / JavaScript 탭 전환 |
| **스타일** | 다크/라이트 모드, monospace 코드 블록 |
| **응답 스키마** | 접을 수 있는 JSON 스키마 트리 |
| **검색** | Ctrl+K 인라인 검색 |
| **단일 파일** | CSS/JS 인라인, 외부 의존성 없음 |

### 문서 섹션 구조
1. **Overview** — OFKMS v2.0 소개, Base URL, 인증
2. **Authentication** — API Key 사용법
3. **Query** — `POST /v1/query` 상세 (파라미터, 응답, 에러)
4. **Streaming** — `POST /v1/query/stream` SSE 사용법
5. **Health** — `GET /v1/health` 상태 확인
6. **Products** — `GET /v1/products` 제품 목록
7. **Error Codes** — HTTP 상태 코드 + 에러 응답 형식
8. **Rate Limits** — 제한 사항 안내
9. **Examples** — 실제 사용 시나리오 (일본어/한국어 쿼리)

---

## 7. Technical Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Framework | FastAPI | async 지원, Pydantic 통합, 자동 OpenAPI 스키마 |
| ASGI Server | uvicorn | 성능, FastAPI 공식 권장 |
| Auth | X-API-Key | 단순, 내부용 충분, 향후 JWT 확장 가능 |
| Streaming | SSE (EventSource) | HTTP/1.1 호환, 브라우저 네이티브 지원 |
| Docs Format | 단일 HTML | 배포 간단, CDN 불필요, 오프라인 열람 가능 |
| Orchestrator | Singleton (lifespan) | Neo4j/PG 연결 풀 재사용, 서버 시작/종료 시 lifecycle |

---

## 8. Dependencies (추가)

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sse-starlette>=2.0.0    # SSE 스트리밍용
```

---

## 9. Implementation Order

```
1. api/models/         → Request/Response 모델 정의
2. api/deps.py         → Orchestrator 싱글턴, 의존성
3. api/middleware/auth  → API Key 인증 미들웨어
4. api/routes/health    → Health 엔드포인트 (가장 간단, 테스트용)
5. api/routes/products  → Products 엔드포인트
6. api/routes/query     → Query 엔드포인트 (핵심)
7. api/routes/query     → Stream 엔드포인트 (SSE)
8. api/server.py        → FastAPI app 조립, CORS, lifespan
9. docs/api-reference.html → API 문서 HTML 작성
10. main.py 수정 없음    → CLI는 내부용으로 유지
```

---

## 10. Success Criteria

- [ ] `POST /v1/query`로 일본어/한국어 쿼리 전송 시 올바른 JSON 응답
- [ ] `POST /v1/query/stream`으로 SSE 스트리밍 응답 수신
- [ ] `GET /v1/health`로 전체 인프라 상태 확인
- [ ] `GET /v1/products`로 제품 목록 반환
- [ ] `X-API-Key` 없는 요청은 401 반환
- [ ] API 문서 HTML 파일이 브라우저에서 정상 렌더링
- [ ] API 문서에 curl/Python/JavaScript 예제 포함
- [ ] 기존 CLI (`main.py`) 기능 그대로 유지
