# Plan: OFKMS v2.0 — Agent-Based Query Pipeline

> **Feature**: ofkms-v2
> **Created**: 2026-03-04
> **Phase**: Plan
> **Status**: Draft

---

## 1. 현재 시스템 인프라 현황

### 1.1 원격 서버 Docker 컨테이너 (192.168.8.11)

| Container | Image | Port | 역할 | 상태 |
|-----------|-------|------|------|------|
| `vllm_qwen3_32b` | vllm/vllm-openai | 12810 | Qwen3 32B LLM (OpenAI API 호환) | ✅ Running |
| `ofcode-server` | docker_ofcode-server | 12820 | OFCode 웹문서/파서 서버 (Uvicorn) | ✅ Running (unhealthy) |
| `bge-m3-server` | docker_bge-m3 | 12801 | BGE-M3 임베딩 (Dense 1024d + Sparse) | ✅ Running |
| `postgres-kms` | pgvector/pgvector:pg15 | 5432 | PostgreSQL + pgvector (벡터/관계형 DB) | ✅ Running |
| `neo4j-graphrag` | neo4j:latest | 7474/7687 | Neo4j Graph DB (Entity/Chunk 관계) | ✅ Running |

### 1.2 기존 데이터 자산 (v1에서 계승)

| 자산 | 규모 | 위치 |
|------|------|------|
| PDF 매뉴얼 | 19개 제품, 245개 PDF | `uploads/manuals/` |
| Neo4j Chunks | 42,596개 (임베딩 완료) | Neo4j Vector Index |
| Neo4j Entities | 13,450개 (6종 타입) | Neo4j Graph |
| MENTIONS 관계 | 476,215건 (Entity-Chunk 연결 99.9%) | Neo4j Graph |
| 요약본 | 에러코드 1,200+, 용어사전 A-Z, 명령어, 설정, API, 용어 | `uploads/summaries/` |
| 웹문서 인덱스 | docs.tmaxsoft.com 크롤링 (ja) | `uploads/web_doc_index/index.json` |
| QLoRA 학습 데이터 | v3~v9 (22개 제품별 어댑터) | `uploads/summaries/multi_lora_v*` |
| CPT 학습 데이터 | 72MB Plain Text (~34.3M 토큰) | `uploads/training_text/` |

### 1.3 v1 → v2 주요 변경 사항

| 항목 | v1 | v2 |
|------|-----|-----|
| Main LLM | Qwen 2.5 7B + 22 QLoRA Adapters | **Qwen3 32B** (단일 모델, 추론 능력 대폭 향상) |
| Embedding | NV-EmbedQA-Mistral 7B (NVIDIA NIM) | **BGE-M3** (다국어, Dense+Sparse Hybrid) |
| Code/Parser | Qwen 2.5 Coder 3B (단순 코드 생성) | **OFCode Server** (OpenFrame 전용 파서/웹문서) |
| Vision LLM | MiniCPM-V 2.6 (별도 컨테이너) | 제거 (Qwen3이 멀티모달 대응) |
| Learning LLM | Qwen 2.5 7B + QLoRA (별도 컨테이너) | 제거 (Qwen3 32B CPT 기반으로 통합) |
| 아키텍처 | 서비스 분산 (157+ 서비스) | **Agent 중심 파이프라인** (Zero-base 재설계) |

---

## 2. 목표

### 2.1 핵심 목표

> **사용자의 쿼리 → 최종 LLM 답변까지 Agent가 자체 판단하는 Query Pipeline 구축**

- 5단계 검색 우선순위 기반 **Cascading Search Pipeline**
- Agent가 각 단계의 결과를 평가하고 다음 단계 진행 여부를 자율 판단
- TmaxSoft 19개 제품 전체에 대해 동일한 파이프라인 적용
- 환각(Hallucination) 최소화: 검색 기반 답변 우선, LLM 자체 지식은 최후 수단

### 2.2 5단계 Cascading Search Pipeline

```
User Query
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 0: Query Understanding                               │
│  ├─ 쿼리 분석 (의도, 언어, 제품 식별)                          │
│  ├─ 쿼리 유형 분류 (명령어/에러코드/설정/개념/코드/비교)         │
│  └─ 검색 전략 결정                                            │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: 임베딩 문서 검색 (BGE-M3 + Neo4j + PostgreSQL)    │
│  ├─ BGE-M3 Dense (1024d) → Neo4j Vector Index               │
│  ├─ BGE-M3 Sparse → BM25/Keyword 검색                       │
│  ├─ Neo4j Graph → Entity 기반 관계 검색                       │
│  ├─ PostgreSQL pgvector → 보조 벡터 검색                      │
│  └─ RRF (Reciprocal Rank Fusion) 병합                        │
│                                                              │
│  → 유사도 평가: max_score >= 0.3?                             │
│    YES → 결과 축적, Phase 2로 보강                             │
│    NO  → Phase 2로 계속                                       │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 2: CPT 도메인 지식 (Qwen3 32B 학습 지식)               │
│  ├─ Qwen3에 CPT로 학습된 TmaxSoft 도메인 지식 활용             │
│  ├─ Phase 1 검색 결과를 컨텍스트로 포함하여 LLM 생성            │
│  ├─ 구조화 질문 → 템플릿 응답 (에러코드, 명령어 등)             │
│  └─ 비구조화 질문 → LLM 생성 + Phase 1 결과 기반 검증          │
│                                                              │
│  → 유사도 평가: 응답 신뢰도 >= 0.3?                            │
│    YES → 결과 축적, Phase 3로 보강                             │
│    NO  → Phase 3로 계속                                       │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 3: OFCode 웹문서 검색                                  │
│  ├─ OFCode Server (port 12820) API 호출                      │
│  ├─ docs.tmaxsoft.com 실시간 검색                             │
│  ├─ 제품별 웹 문서 페이지 매칭                                  │
│  └─ IDF-weighted keyword 매칭                                 │
│                                                              │
│  → 유사도 평가: max_score >= 0.3?                             │
│    YES → 결과 축적, Phase 4로 보강                             │
│    NO  → Phase 4로 계속                                       │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 4: OFCode Parser 기반 검색                             │
│  ├─ OpenFrame Parser API (port 12820) 호출                   │
│  ├─ JCL/COBOL/Assembler 코드 구문 분석                        │
│  ├─ 코드 샘플 생성 및 해석                                     │
│  └─ OpenFrame 설정 파일 파싱                                   │
│                                                              │
│  → 유사도 평가: max_score >= 0.3?                             │
│    YES → 결과 축적                                            │
│    NO  → Phase 5로 폴백                                       │
└─────────────────────────────────────────────────────────────┘
    │
    ▼ (Phase 1~4 모든 결과의 max_score < 0.3인 경우에만)
┌─────────────────────────────────────────────────────────────┐
│  Phase 5: Qwen3 Tool-Augmented 자체 지식 (Fallback)           │
│  ├─ Qwen3 32B의 사전학습 지식 (일반 IT/메인프레임 지식)          │
│  ├─ Tool Calling: 외부 검색, 계산 등                           │
│  ├─ 응답에 "[자체지식]" 태그 표기                               │
│  └─ 검증 불가능한 정보임을 명시                                  │
│                                                              │
│  ⚠️ 조건: Phase 1~4의 누적 max_score < 0.3                    │
│  ⚠️ 사용자에게 "검증되지 않은 답변" 경고 표시                    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 6: 응답 생성 및 검증                                    │
│  ├─ 전체 Phase 결과 통합 (Result Fusion)                       │
│  ├─ 출처 표기 (Source Attribution)                             │
│  ├─ 문장별 신뢰도 등급 (Verified/Inferred/Unverified)          │
│  ├─ 용어 교정 (TJES→Tmax Job Entry Subsystem 등)              │
│  └─ 다국어 응답 생성 (ja/ko/en)                                │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Agent 아키텍처 설계

### 3.1 Agent 구성

```
┌─────────────────────────────────────────────────────────┐
│                    Orchestrator Agent                     │
│  (전체 파이프라인 제어, Phase 전환 판단)                    │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Query Agent  │  │ Search Agent │  │ Code Agent   │   │
│  │ (Phase 0)    │  │ (Phase 1)    │  │ (Phase 3,4)  │   │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤   │
│  │쿼리 분석     │  │BGE-M3 임베딩 │  │OFCode 웹문서 │   │
│  │의도 분류     │  │Neo4j Vector  │  │OpenFrame 파서│   │
│  │제품 라우팅   │  │Neo4j Graph   │  │코드 해석     │   │
│  │쿼리 확장     │  │PostgreSQL    │  │샘플 생성     │   │
│  └──────────────┘  │RRF Fusion    │  └──────────────┘   │
│                    └──────────────┘                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Domain Agent │  │ Fallback     │  │ Response     │   │
│  │ (Phase 2)    │  │ Agent        │  │ Agent        │   │
│  ├──────────────┤  │ (Phase 5)    │  │ (Phase 6)    │   │
│  │CPT 도메인    │  ├──────────────┤  ├──────────────┤   │
│  │지식 활용     │  │Qwen3 자체    │  │결과 통합     │   │
│  │템플릿 응답   │  │지식 + Tool   │  │신뢰도 검증   │   │
│  │검증 생성     │  │Calling       │  │출처 표기     │   │
│  └──────────────┘  └──────────────┘  │용어 교정     │   │
│                                       └──────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 3.2 Agent 역할 상세

| Agent | 담당 Phase | 핵심 역할 | 입력 | 출력 |
|-------|-----------|---------|------|------|
| **Orchestrator** | 전체 | 파이프라인 제어, Phase 전환 판단, 결과 축적 | User Query | Final Response |
| **Query Agent** | Phase 0 | 쿼리 분석, 의도 분류, 제품 식별, 언어 감지 | Raw Query | QueryPlan (의도, 제품, 유형, 언어) |
| **Search Agent** | Phase 1 | BGE-M3 하이브리드 검색, Neo4j/PG 검색, RRF 융합 | QueryPlan | SearchResults (chunks, scores) |
| **Domain Agent** | Phase 2 | CPT 지식 기반 LLM 생성, 템플릿 응답, 검증 | QueryPlan + SearchResults | DomainAnswer (answer, confidence) |
| **Code Agent** | Phase 3,4 | OFCode 웹문서 검색, Parser 기반 코드 분석/생성 | QueryPlan + Prior Results | CodeResults (docs, parsed, samples) |
| **Fallback Agent** | Phase 5 | Qwen3 자체 지식 + Tool Calling (최후 수단) | QueryPlan + All Prior Results | FallbackAnswer (answer, warning) |
| **Response Agent** | Phase 6 | 결과 융합, 신뢰도 검증, 출처 표기, 용어 교정 | All Phase Results | FinalResponse (answer, sources, verification) |

### 3.3 Orchestrator 판단 로직

```python
# Pseudocode: Orchestrator 핵심 로직
async def execute_pipeline(query: str) -> FinalResponse:
    # Phase 0: Query Understanding
    plan = await query_agent.analyze(query)

    accumulated_results = []
    max_score = 0.0

    # Phase 1: Embedding Search (항상 실행)
    search_results = await search_agent.search(plan)
    accumulated_results.append(search_results)
    max_score = max(max_score, search_results.max_score)

    # Phase 2: Domain Knowledge (항상 실행, Phase 1 결과로 보강)
    domain_answer = await domain_agent.generate(plan, accumulated_results)
    accumulated_results.append(domain_answer)
    max_score = max(max_score, domain_answer.confidence)

    # Phase 3: OFCode Web Docs (항상 실행)
    web_results = await code_agent.search_web_docs(plan)
    accumulated_results.append(web_results)
    max_score = max(max_score, web_results.max_score)

    # Phase 4: OFCode Parser (코드 관련 쿼리일 때)
    if plan.requires_code_analysis:
        parser_results = await code_agent.parse_code(plan)
        accumulated_results.append(parser_results)
        max_score = max(max_score, parser_results.max_score)

    # Phase 5: Fallback (Phase 1~4 max_score < 0.3인 경우에만)
    if max_score < 0.3:
        fallback = await fallback_agent.generate(plan, accumulated_results)
        accumulated_results.append(fallback)

    # Phase 6: Response Generation
    final = await response_agent.build(plan, accumulated_results)
    return final
```

---

## 4. 기술 설계 방향

### 4.1 검색 계층별 기술 스택

| Phase | 기술 | 엔드포인트 | 특징 |
|-------|------|----------|------|
| Phase 1-Dense | BGE-M3 Dense (1024d) | `192.168.8.11:12801/embed` | 의미적 유사도 검색 |
| Phase 1-Sparse | BGE-M3 Sparse (Learned) | `192.168.8.11:12801/sparse` | 키워드 정확 매칭 |
| Phase 1-Graph | Neo4j Cypher | `bolt://192.168.8.11:7687` | Entity 관계 탐색 |
| Phase 1-Vector | pgvector cosine | `192.168.8.11:5432` | 보조 벡터 검색 |
| Phase 2 | Qwen3 32B (CPT) | `192.168.8.11:12810/v1/chat/completions` | 도메인 지식 생성 |
| Phase 3 | OFCode Web API | `192.168.8.11:12820/search` | 웹문서 검색 |
| Phase 4 | OFCode Parser API | `192.168.8.11:12820/parse` | 코드 분석/생성 |
| Phase 5 | Qwen3 32B (Tool Call) | `192.168.8.11:12810/v1/chat/completions` | 자체 지식 + 도구 |

### 4.2 유사도 평가 기준

| 등급 | Score 범위 | 의미 | 처리 |
|------|-----------|------|------|
| HIGH | >= 0.7 | 높은 신뢰도 검색 결과 | 🟢 VERIFIED — 즉시 응답 가능 |
| MEDIUM | 0.3 ~ 0.7 | 관련 결과 있음 | 🟡 INFERRED — 추가 Phase 결과로 보강 |
| LOW | < 0.3 | 관련 결과 부족 | 🔴 다음 Phase 계속 / Phase 5 폴백 |

### 4.3 v1 재활용 vs 신규 개발

| 구분 | v1 재활용 (리팩토링) | v2 신규 개발 |
|------|---------------------|-------------|
| **재활용** | Product Router (키워드/패턴 매핑) | Agent Orchestrator (LangGraph 기반) |
| **재활용** | Response Verifier (문장별 검증) | Query Agent (의도 분류 통합) |
| **재활용** | Term Correction 사전 | Search Agent (BGE-M3 + Neo4j + PG 통합) |
| **재활용** | Summary System (에러코드, 용어, 명령어) | Code Agent (OFCode 연동) |
| **재활용** | Neo4j Entity/Chunk 데이터 | Fallback Agent (Tool Calling) |
| **재활용** | Web Doc Index | Response Agent (통합 응답 빌더) |
| **신규** | — | Cascading Score 평가 로직 |
| **신규** | — | Agent 간 메시지 프로토콜 |

---

## 5. 프로젝트 구조 (제안)

```
ofkms_v2/
├── CLAUDE.md                           # Claude Code 가이드
├── .env                                # 환경 변수
├── requirements.txt                    # Python 의존성
├── docker-compose.yml                  # 로컬 개발용 (Backend + Frontend)
│
├── app/
│   ├── main.py                         # FastAPI 엔트리포인트
│   ├── core/
│   │   ├── config.py                   # Pydantic Settings
│   │   ├── security.py                 # JWT/Auth
│   │   └── dependencies.py             # DI (Depends)
│   │
│   ├── agents/                         # 🎯 Agent 시스템 (핵심)
│   │   ├── orchestrator.py             # Orchestrator Agent (파이프라인 제어)
│   │   ├── query_agent.py              # Phase 0: 쿼리 분석/분류
│   │   ├── search_agent.py             # Phase 1: 임베딩 검색
│   │   ├── domain_agent.py             # Phase 2: CPT 도메인 지식
│   │   ├── code_agent.py               # Phase 3,4: OFCode 연동
│   │   ├── fallback_agent.py           # Phase 5: 자체 지식 폴백
│   │   ├── response_agent.py           # Phase 6: 응답 생성/검증
│   │   ├── base.py                     # BaseAgent 추상 클래스
│   │   ├── types.py                    # Agent 공통 타입 정의
│   │   └── tools/                      # Agent 도구
│   │       ├── neo4j_search.py         # Neo4j Vector + Graph 검색
│   │       ├── pg_search.py            # PostgreSQL pgvector 검색
│   │       ├── bge_m3_client.py        # BGE-M3 임베딩 클라이언트
│   │       ├── ofcode_client.py        # OFCode Server 클라이언트
│   │       ├── qwen3_client.py         # Qwen3 LLM 클라이언트
│   │       └── summary_search.py       # Summary 파일 검색
│   │
│   ├── services/                       # 비즈니스 로직 서비스
│   │   ├── product_router.py           # 제품 라우팅 (v1 계승)
│   │   ├── response_verifier.py        # 응답 검증 (v1 계승)
│   │   ├── term_corrector.py           # 용어 교정 (v1 계승)
│   │   └── conversation_service.py     # 대화 관리
│   │
│   ├── routers/                        # API 라우터
│   │   ├── chat.py                     # /api/v1/chat (메인 RAG 엔드포인트)
│   │   ├── agents.py                   # /api/v1/agents (Agent 스트리밍)
│   │   ├── health.py                   # /api/v1/health
│   │   ├── auth.py                     # /api/v1/auth
│   │   └── admin.py                    # /api/v1/admin
│   │
│   ├── models/                         # Pydantic 스키마
│   │   ├── query.py                    # QueryPlan, QueryType, QueryIntent
│   │   ├── search.py                   # SearchResult, SearchScore
│   │   ├── response.py                 # FinalResponse, VerifiedSentence
│   │   └── agent.py                    # AgentContext, AgentResult
│   │
│   └── infrastructure/                 # 인프라 연결
│       ├── neo4j_client.py             # Neo4j 드라이버
│       ├── pg_client.py                # PostgreSQL asyncpg
│       └── redis_client.py             # Redis 캐시 (선택)
│
├── frontend/                           # React Frontend
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── stores/                     # Zustand
│   │   ├── api/
│   │   ├── hooks/
│   │   └── i18n/                       # ja, ko, en
│   └── vite.config.ts
│
├── uploads/                            # 데이터 자산 (v1에서 복사)
│   ├── manuals/                        # 19개 제품 PDF
│   ├── summaries/                      # 요약본 (에러코드, 용어, 명령어)
│   └── web_doc_index/                  # 웹문서 인덱스
│
├── scripts/                            # 유틸리티
│   ├── migrate_data.py                 # v1 데이터 마이그레이션
│   └── health_check.py                 # 서비스 상태 확인
│
├── tests/                              # 테스트
│   ├── test_agents/                    # Agent 단위 테스트
│   ├── test_pipeline/                  # 파이프라인 통합 테스트
│   └── test_e2e/                       # E2E Hallucination 테스트
│
└── docs/                               # PDCA 문서
    ├── 01-plan/features/
    ├── 02-design/features/
    ├── 03-analysis/
    └── 04-report/
```

---

## 6. 구현 순서

### Phase 1: 기반 구축 (Foundation)

1. **프로젝트 초기화**: FastAPI 프로젝트 구조, 의존성, 설정
2. **인프라 클라이언트**: Neo4j, PostgreSQL, BGE-M3, OFCode, Qwen3 클라이언트
3. **BaseAgent 프레임워크**: Agent 추상 클래스, 타입 정의, Orchestrator 뼈대

### Phase 2: 핵심 Agent 구현

4. **Query Agent** (Phase 0): 쿼리 분석, 제품 라우팅, 의도 분류
5. **Search Agent** (Phase 1): BGE-M3 하이브리드 검색, Neo4j/PG 검색, RRF 융합
6. **Domain Agent** (Phase 2): CPT 지식 기반 LLM 생성, 템플릿 응답

### Phase 3: 확장 Agent 구현

7. **Code Agent** (Phase 3,4): OFCode 웹문서 + Parser 연동
8. **Fallback Agent** (Phase 5): Qwen3 Tool Calling 기반 폴백
9. **Response Agent** (Phase 6): 결과 통합, 검증, 출처 표기

### Phase 4: 통합 및 프론트엔드

10. **API 라우터**: 채팅 엔드포인트, SSE 스트리밍
11. **Frontend**: React 채팅 UI, Agent 상태 표시
12. **E2E 테스트**: 45개 Hallucination 테스트 케이스 이관 및 검증

---

## 7. 성공 기준

| 항목 | 목표 | 측정 방법 |
|------|------|----------|
| Hallucination Rate | v1 대비 50% 이상 감소 | E2E 테스트 45개 케이스 |
| 응답 시간 | P95 < 10초 (스트리밍 첫 토큰 < 3초) | API 로그 |
| 검색 정확도 | Top-3 적합 문서 포함율 >= 80% | MRR@3 |
| 제품 커버리지 | 19개 제품 전체 | 제품별 테스트 |
| 폴백 비율 | Phase 5 폴백 < 10% | 로그 분석 |
| 다국어 | ja/ko/en 동일 품질 | 언어별 테스트 |

---

## 8. 위험 요소 및 대응

| 위험 | 영향 | 대응 |
|------|------|------|
| OFCode Server 불안정 (unhealthy) | Phase 3,4 검색 불가 | Circuit Breaker 패턴, Phase 3,4 스킵 후 Phase 5 폴백 |
| Qwen3 32B 응답 지연 | 전체 파이프라인 지연 | 스트리밍 응답, Phase 병렬 실행 (1+3 동시) |
| BGE-M3 임베딩 불일치 | v1 NV-EmbedQA 임베딩과 호환 안됨 | Neo4j 청크 재임베딩 필요 (마이그레이션 계획) |
| 22개 QLoRA 어댑터 미적용 | 제품별 전문성 감소 | Qwen3 32B CPT로 도메인 지식 통합, 프롬프트 엔지니어링 강화 |

---

## 9. 의존성 및 전제 조건

- [ ] Qwen3 32B vLLM 서버 정상 가동 (port 12810)
- [ ] BGE-M3 서버 정상 가동 (port 12801)
- [ ] OFCode Server 정상 가동 (port 12820) — 현재 unhealthy, 확인 필요
- [ ] PostgreSQL + pgvector 접근 가능 (port 5432)
- [ ] Neo4j 접근 가능 (port 7474/7687)
- [ ] v1 데이터 마이그레이션 계획 (Neo4j Chunks 재임베딩 여부 결정)
- [ ] OFCode Server API 명세 확인 (웹문서 검색 / Parser 엔드포인트)
