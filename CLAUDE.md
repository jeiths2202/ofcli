# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TmaxSoft OpenFrame KMS v2.0** — RAG-based Knowledge Management System for TmaxSoft OpenFrame mainframe modernization products. Successor to HybridRAG KMS v1 (`../kms-docker-remote/`).

### What This System Does

Provides AI-powered Q&A for 19+ TmaxSoft OpenFrame products (MVS, MSP, VOS3, HIDB, OFASM, OFCOBOL, Tibero, etc.) using Hybrid RAG (vector + graph retrieval) with domain-specialized LLM generation. Targets Japanese enterprise customers migrating from IBM MVS / Fujitsu XSP mainframes to OpenFrame.

## Remote Server Infrastructure

All AI/DB services run on a remote GPU server (192.168.8.11). The containers:

| Container | Image | Port | Role |
|-----------|-------|------|------|
| `vllm_qwen3_32b` | vllm/vllm-openai | **12810** | Main LLM — Qwen3 32B via vLLM (OpenAI-compatible API) |
| `ofcode-server` | docker_ofcode-server | **12820** | OFCode analysis server (FastAPI/Uvicorn) |
| `bge-m3-server` | docker_bge-m3 | **12801** | BGE-M3 embedding model (multilingual, multi-granularity) |
| `postgres-kms` | pgvector/pgvector:pg15 | **5432** | PostgreSQL 15 + pgvector (vector store + relational data) |
| `neo4j-graphrag` | neo4j:latest | **7474/7687** | Neo4j graph database (GraphRAG, entity relationships) |

### Key Architecture Changes from v1

| Component | v1 (kms-docker-remote) | v2 (ofkms_v2) |
|-----------|----------------------|---------------|
| Main LLM | Qwen 2.5 7B + 22 QLoRA adapters (port 12800) | Qwen3 32B single model (port 12810) |
| Embeddings | NV-EmbedQA-Mistral 7B (NVIDIA NIM, port 12801) | BGE-M3 (custom server, port 12801) |
| Code LLM | Qwen 2.5 Coder 3B (port 12802) | OFCode Server (port 12820) |
| Vision LLM | MiniCPM-V 2.6 (port 12803) | Removed (Qwen3 32B handles multimodal) |
| Learning LLM | Qwen 2.5 7B + QLoRA (port 12804) | Removed (Qwen3 32B sufficient) |
| Graph DB | Neo4j (42K chunks, 13K entities) | Neo4j (same, expanded) |
| Vector DB | Neo4j Vector Index only | PostgreSQL pgvector + Neo4j |

### API Endpoints (Remote Server)

```bash
# LLM (Qwen3 32B) — OpenAI-compatible
curl http://192.168.8.11:12810/v1/models
curl http://192.168.8.11:12810/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "Qwen/Qwen3-32B", "messages": [{"role": "user", "content": "test"}]}'

# Embeddings (BGE-M3)
curl http://192.168.8.11:12801/embed \
  -H "Content-Type: application/json" \
  -d '{"texts": ["test query"]}'

# OFCode Server
curl http://192.168.8.11:12820/health

# PostgreSQL
psql -h 192.168.8.11 -p 5432 -U raguser -d ragdb

# Neo4j Browser
# http://192.168.8.11:7474 (bolt://192.168.8.11:7687)
```

## Tech Stack (Planned)

| Layer | Technology |
|-------|------------|
| Backend | FastAPI (Python 3.10+), async |
| Frontend | React 18 + TypeScript + Vite |
| Main LLM | Qwen3 32B via vLLM (OpenAI-compatible) |
| Embeddings | BGE-M3 (multilingual: ja/ko/en, multi-granularity) |
| Vector DB | PostgreSQL 15 + pgvector |
| Graph DB | Neo4j (entity relationships, GraphRAG) |
| Code Analysis | OFCode Server (OpenFrame code analysis) |
| LLM Framework | LangChain / LangGraph |
| State Management | Zustand (frontend) |
| i18n | Japanese (primary), Korean, English |

## v1 Reference

The v1 codebase at `../kms-docker-remote/` contains the production system with:
- Backend: `app/api/` (FastAPI, 325+ files, 157+ services)
- Frontend: `kms-portal-ui/` (React 18, 132+ files)
- Agent system: `app/api/agents/` (9 agents, LangGraph Deep Agents)
- Agentic RAG: 6-phase pipeline (Product Routing → Query Classification → Two-Stage Retrieval → Response Generation → Post-Verification → Source Attribution)
- QLoRA training pipeline: CPT → SFT (22 adapters) → DPO
- E2E tests: `e2e/` (Playwright, 45 hallucination test cases)
- Sub-CLAUDE.md files: `app/api/CLAUDE.md`, `app/api/agents/CLAUDE.md`, `kms-portal-ui/CLAUDE.md`, `AGENT.md`

Refer to `../kms-docker-remote/CLAUDE.md` and `../kms-docker-remote/README.md` for full v1 architecture documentation.

## Domain Context

- **OpenFrame**: TmaxSoft's mainframe modernization platform (rehosting IBM MVS/Fujitsu XSP workloads on Linux)
- **19 Products**: OpenFrame MVS/MSP/VOS3, HIDB, OFASM, OFCOBOL, OFPLI, OFStudio, Tmax, Tibero, AIM/DB, AIM/DC, JCL, VSAM, CICS, etc.
- **Manager Commands**: tjesmgr, tacfmgr, hidbmgr, oscmgr, osimgr, volmgr, catmgr, ndbmgr
- **245 PDF manuals** in Japanese/Korean/English are the knowledge source
- Primary users: Japanese enterprise engineers performing mainframe migration
- Hallucination is the top concern — product-confusion (e.g., answering about oscmgr when asked about tjesmgr) must be prevented

## Coding Conventions

### Python (Backend)
- Type hints for all functions
- Async functions (`async def`) for I/O operations
- Specific exceptions, not bare `except`
- Pydantic Settings for configuration (`app/api/core/config.py`)
- Router → Service → Repository layer separation
- Korean/Japanese comments OK for business logic; logs in English

### TypeScript (Frontend)
- Strict mode enabled
- Functional components with hooks
- Zustand for global state
- All UI strings via i18n (3 locales: en, ja, ko)

### Git Commits
```
feat: Add new feature
fix: Bug fix
refactor: Code restructure
style: Formatting, CSS
chore: Build, deps, config
docs: Documentation only
```
