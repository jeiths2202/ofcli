"""
OFKMS v2.0 — FastAPI REST API Server
"""
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# UTF-8 support
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from api.db import close_db, init_db
from api.deps import close_orchestrator, init_orchestrator
from api.middleware.auth import APIKeyAuthMiddleware
from api.routes.admin import router as admin_router
from api.routes.health import router as health_router
from api.routes.products import router as products_router
from api.routes.query import router as query_router
from app.core.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_orchestrator()
    yield
    await close_orchestrator()
    await close_db()


app = FastAPI(
    title="OFKMS API",
    version="2.0.0",
    description="OpenFrame Knowledge Management System REST API",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Key Auth
settings = get_settings()
app.add_middleware(APIKeyAuthMiddleware)

# Routes
app.include_router(health_router)
app.include_router(products_router)
app.include_router(query_router)
app.include_router(admin_router)


@app.get("/")
async def root():
    return {"name": "OFKMS API", "version": "2.0.0", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    uvicorn.run(
        "api.server:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=False,
    )
