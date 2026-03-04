"""GET /v1/health — Infrastructure health check"""
import asyncio
import logging
import time

import httpx
from fastapi import APIRouter

from api.models.response import HealthResponse, ServiceStatus
from app.core.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["health"])


async def _check_http(client: httpx.AsyncClient, url: str, method: str = "GET") -> ServiceStatus:
    t0 = time.perf_counter()
    try:
        if method == "POST":
            r = await client.post(url, json={"input": "test"})
        else:
            r = await client.get(url)
        latency = int((time.perf_counter() - t0) * 1000)
        status = "ok" if r.status_code < 400 else "error"
    except Exception:
        latency = int((time.perf_counter() - t0) * 1000)
        status = "error"
    return ServiceStatus(status=status, latency_ms=latency)


async def _check_neo4j() -> ServiceStatus:
    from neo4j import AsyncGraphDatabase

    s = get_settings()
    t0 = time.perf_counter()
    try:
        driver = AsyncGraphDatabase.driver(s.NEO4J_URI, auth=(s.NEO4J_USER, s.NEO4J_PASSWORD))
        await driver.verify_connectivity()
        await driver.close()
        latency = int((time.perf_counter() - t0) * 1000)
        return ServiceStatus(status="ok", latency_ms=latency)
    except Exception:
        latency = int((time.perf_counter() - t0) * 1000)
        return ServiceStatus(status="error", latency_ms=latency)


async def _check_pg() -> ServiceStatus:
    import asyncpg

    s = get_settings()
    dsn = f"postgresql://{s.POSTGRES_USER}:{s.POSTGRES_PASSWORD}@{s.POSTGRES_HOST}:{s.POSTGRES_PORT}/{s.POSTGRES_DB}"
    t0 = time.perf_counter()
    try:
        conn = await asyncpg.connect(dsn, timeout=3)
        await conn.execute("SELECT 1")
        await conn.close()
        latency = int((time.perf_counter() - t0) * 1000)
        return ServiceStatus(status="ok", latency_ms=latency)
    except Exception:
        latency = int((time.perf_counter() - t0) * 1000)
        return ServiceStatus(status="error", latency_ms=latency)


@router.get("/health", response_model=HealthResponse)
async def health_check():
    s = get_settings()
    async with httpx.AsyncClient(timeout=5) as client:
        results = await asyncio.gather(
            _check_http(client, f"{s.LLM_BASE_URL}/models"),
            _check_http(client, f"{s.BGE_M3_BASE_URL}/v1/embeddings", "POST"),
            _check_neo4j(),
            _check_pg(),
            _check_http(client, f"{s.OFCODE_BASE_URL}/health"),
        )

    service_names = ["llm_qwen3", "bge_m3", "neo4j", "postgresql", "ofcode"]
    services = dict(zip(service_names, results))

    ok_count = sum(1 for s in services.values() if s.status == "ok")
    if ok_count == len(services):
        overall = "healthy"
    elif ok_count > 0:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return HealthResponse(status=overall, services=services, version="2.0.0")
