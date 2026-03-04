"""OFCode Server 클라이언트 (192.168.8.11:12820)

v1 API 엔드포인트:
  - /api/rag/search — RAG 기반 문서 검색
  - /api/webdoc/search — 웹 문서 검색
  - /api/search — OF7 코드 검색
  - /api/function — 함수 정의 조회
  - /api/module — 모듈 정보 조회
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class OFCodeClient:
    """OFCode Server — 웹문서 검색 + OpenFrame Parser"""

    def __init__(self):
        s = get_settings()
        self._base_url = s.OFCODE_BASE_URL
        self._timeout = s.OFCODE_TIMEOUT

    async def search_web_docs(
        self,
        query: str,
        product: Optional[str] = None,
        language: str = "ja",
    ) -> List[Dict[str, Any]]:
        """Phase 3: 웹문서 검색 (OFCode /api/rag/search)"""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.post(
                    f"{self._base_url}/api/rag/search",
                    json={"query": query, "product": product, "language": language},
                )
                resp.raise_for_status()
                return resp.json().get("results", [])
        except Exception as e:
            logger.warning(f"OFCode web search failed: {e}")
            return []

    async def parse_code(
        self,
        query: str,
        code_type: str = "jcl",
    ) -> Dict[str, Any]:
        """Phase 4: OpenFrame 코드 검색 (OFCode /api/search)"""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.post(
                    f"{self._base_url}/api/search",
                    json={"query": query, "file_type": code_type},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"OFCode code search failed: {e}")
            return {}

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                resp = await c.get(f"{self._base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False
