# Plan: VSCode CLI Command for OFKMS v2

> Feature: `vscode-cli`
> Created: 2026-03-04
> Status: Draft

## 1. Overview

VSCode 터미널에서 OFKMS v2 REST API를 직접 호출할 수 있는 Python CLI 도구 개발.
OpenFrame 엔지니어가 코드 편집 중 터미널에서 바로 기술 질문, 상태 확인, API 키 관리를 수행할 수 있게 한다.

## 2. Problem Statement

현재 OFKMS v2는 두 가지 인터페이스를 제공:
1. **CLI REPL** (`main.py`) — 로컬 Orchestrator를 직접 실행 (원격 서버 직접 연결 필요)
2. **REST API** (`api/server.py`) — HTTP 엔드포인트 (curl 등으로만 접근 가능)

문제점:
- `main.py`는 로컬에서 모든 DB/서비스에 직접 연결해야 하므로 원격 환경에서 사용 불편
- REST API는 curl 명령어가 복잡하고, 인증 헤더 관리가 번거로움
- VSCode에서 작업 중 빠르게 OpenFrame 지식을 검색할 수 있는 방법이 없음

## 3. Goal

**OFKMS v2 REST API만 사용하는 경량 CLI 클라이언트**를 개발하여:
- VSCode 터미널에서 한 줄 명령으로 질문/검색 가능
- API 키 인증을 자동 관리 (config 파일)
- 스트리밍 응답 지원으로 실시간 답변 표시
- 원격 서버 연결 없이 REST API 엔드포인트만으로 동작

## 4. Target API Endpoints

이 프로젝트에서 구현된 API만 사용:

| Endpoint | CLI Command | Description |
|----------|-------------|-------------|
| `GET /v1/health` | `ofkms health` | 서비스 상태 확인 |
| `GET /v1/products` | `ofkms products` | 지원 제품 목록 |
| `POST /v1/query` | `ofkms ask "질문"` | 질문 (동기) |
| `POST /v1/query/stream` | `ofkms ask --stream "질문"` | 질문 (스트리밍) |
| `POST /v1/admin/login` | `ofkms login` | 로그인 (API 키 발급) |
| `POST /v1/admin/keys` | `ofkms keys create` | API 키 생성 |
| `GET /v1/admin/keys` | `ofkms keys list` | API 키 목록 |
| `DELETE /v1/admin/keys/{id}` | `ofkms keys revoke {id}` | API 키 삭제 |

## 5. CLI Command Design

```bash
# 기본 사용법
ofkms ask "BMSとMFSの違いを教えてください"
ofkms ask "tjesmgr BOOT 명령어" --product mvs --lang ja
ofkms ask --stream "OFASMの設定方法"

# 서비스 관리
ofkms health
ofkms products

# 인증
ofkms login                    # username/password → API 키 자동 발급 및 저장
ofkms keys list
ofkms keys create --name "my-key"
ofkms keys revoke 3

# 설정
ofkms config set api-url http://192.168.8.11:8000
ofkms config set api-key ofk_xxxx
ofkms config show
```

## 6. Configuration

```
~/.ofkms/config.json
{
  "api_url": "http://192.168.8.11:8000",
  "api_key": "ofk_xxxxxxxxxxxx",
  "default_language": "ja",
  "default_product": null,
  "stream": true
}
```

## 7. Technical Approach

### 7.1 Tech Stack
- **Python 3.10+** (프로젝트와 동일)
- **httpx** (이미 requirements.txt에 있음, async HTTP)
- **click** or **argparse** (CLI 프레임워크)
- **rich** (이미 requirements.txt에 있음, 출력 포맷팅)

### 7.2 Architecture
```
cli/
├── __init__.py
├── __main__.py          # Entry point: python -m cli
├── commands/
│   ├── __init__.py
│   ├── ask.py           # ask command (query + stream)
│   ├── health.py        # health command
│   ├── products.py      # products command
│   ├── auth.py          # login, keys commands
│   └── config.py        # config command
├── client.py            # OFKMS API client (httpx wrapper)
├── config.py            # Config file management (~/.ofkms/config.json)
└── display.py           # Rich output formatting
```

### 7.3 Key Design Decisions

1. **httpx만 사용**: DB 직접 연결 없음. 모든 기능이 REST API 호출로 동작
2. **Async 기본**: `httpx.AsyncClient`로 스트리밍 SSE 처리
3. **Rich 출력**: Markdown 렌더링, 테이블, 프로그레스 바
4. **Config 파일**: `~/.ofkms/config.json`에 API URL, 키 저장
5. **Entry point**: `python -m cli` 또는 pip install 후 `ofkms` 명령으로 실행

## 8. Scope

### In Scope
- REST API 엔드포인트 호출 CLI 래퍼
- 스트리밍 응답 표시 (SSE)
- API 키 인증 관리
- Rich 포맷팅 출력 (Markdown, 테이블, 소스 표시)
- Config 파일 관리

### Out of Scope
- VSCode Extension (별도 프로젝트)
- 로컬 Orchestrator 직접 실행 (기존 main.py가 담당)
- Admin 사용자 관리 (users CRUD) — 관리자 웹 UI에서 수행
- 새로운 API 엔드포인트 추가

## 9. Success Criteria

1. `ofkms ask "질문"`으로 답변을 받을 수 있다
2. `ofkms ask --stream "질문"`으로 실시간 스트리밍 답변을 볼 수 있다
3. `ofkms health`로 5개 서비스 상태를 확인할 수 있다
4. `ofkms login`으로 인증 후 API 키가 자동 저장된다
5. 모든 출력이 Rich 포맷으로 읽기 쉽게 표시된다
6. 외부 의존성 최소화 (httpx, rich, click만 추가)

## 10. Dependencies

- 기존: `httpx`, `rich` (requirements.txt에 이미 있음)
- 추가: `click` (CLI 프레임워크)

## 11. Risk & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| API 서버 미구동 | CLI 사용 불가 | `health` 명령으로 사전 확인, 에러 메시지에 서버 URL 표시 |
| SSE 스트리밍 파싱 | 불완전한 응답 | httpx SSE 처리 검증, 타임아웃 설정 |
| API 키 만료/무효 | 인증 실패 | 자동 재로그인 프롬프트, 키 상태 확인 |

## 12. Implementation Order

1. **Phase 1**: `cli/client.py` + `cli/config.py` (API 클라이언트 + 설정)
2. **Phase 2**: `ofkms health` + `ofkms products` (단순 GET 명령)
3. **Phase 3**: `ofkms ask` (동기 쿼리)
4. **Phase 4**: `ofkms ask --stream` (SSE 스트리밍)
5. **Phase 5**: `ofkms login` + `ofkms keys` (인증 관리)
6. **Phase 6**: `ofkms config` (설정 관리)
7. **Phase 7**: 통합 테스트 + 에러 핸들링
