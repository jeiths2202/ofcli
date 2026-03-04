"""BGE-M3 임베딩 클라이언트 (192.168.8.11:12801)"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class HybridEmbedding:
    dense: List[float]
    sparse_weights: Dict[str, float]


class BgeM3Client:
    """BGE-M3 Dense(1024d) + Sparse 임베딩"""

    def __init__(self):
        s = get_settings()
        self._base_url = s.BGE_M3_BASE_URL
        self._timeout = s.BGE_M3_TIMEOUT

    async def dense_encode(self, text: str) -> List[float]:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.post(
                f"{self._base_url}/v1/embeddings",
                json={"input": text},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

    async def sparse_encode(self, text: str) -> Dict[str, float]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.post(
                    f"{self._base_url}/v1/sparse",
                    json={"input": text},
                )
                resp.raise_for_status()
                return resp.json()["data"][0]["sparse_weights"]
        except Exception as e:
            logger.warning(f"Sparse encode failed, using empty: {e}")
            return {}

    async def hybrid_encode(self, text: str) -> HybridEmbedding:
        dense, sparse = await asyncio.gather(
            self.dense_encode(text),
            self.sparse_encode(text),
        )
        return HybridEmbedding(dense=dense, sparse_weights=sparse)
