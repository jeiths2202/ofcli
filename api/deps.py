"""API Dependencies — Orchestrator singleton lifecycle"""
import logging
from typing import Optional

from app.agents.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

_orchestrator: Optional[Orchestrator] = None


async def init_orchestrator():
    global _orchestrator
    _orchestrator = Orchestrator()
    logger.info("Orchestrator initialized")


async def close_orchestrator():
    global _orchestrator
    if _orchestrator:
        await _orchestrator.close()
        _orchestrator = None
        logger.info("Orchestrator closed")


async def get_orchestrator() -> Orchestrator:
    if _orchestrator is None:
        raise RuntimeError("Orchestrator not initialized")
    return _orchestrator
