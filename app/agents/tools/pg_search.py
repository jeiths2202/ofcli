"""PostgreSQL pgvector 검색 클라이언트

스키마:
  - 테이블: text_chunks (embedding vector(1024) - BGE-M3)
  - JOIN: documents (filename, document_type)
"""
import logging
from typing import Any, Dict, List, Optional

import asyncpg

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# product_id -> DB filename search keywords 매핑
# query_agent의 product_id는 "mvs_openframe_7.1" 형식이지만
# DB filename은 "OF_Common_MVS_7.1_..." 형식이므로 변환 필요
_PRODUCT_FILENAME_MAP = {
    "mvs_openframe_7.1": ["MVS", "OF_Base", "OF_OSC", "OF_Batch", "TJES", "TACF"],
    "openframe_hidb_7": ["HiDB", "IMS"],
    "openframe_osc_7": ["OSC"],
    "tibero_7": ["Tibero"],
    "ofasm_4": ["OFASM"],
    "ofcobol_4": ["OFCOBOL", "COBOL"],
    "tmax_6": ["Tmax_6"],
    "jeus_8": ["JEUS"],
    "webtob_5": ["WebtoB"],
    "ofstudio_7": ["OFStudio", "Studio"],
    "protrieve_7": ["Protrieve"],
    "xsp_openframe_7": ["XSP", "VOS3", "MSP"],
}


class PgSearchClient:
    """PostgreSQL pgvector cosine similarity 검색"""

    def __init__(self):
        s = get_settings()
        self._dsn = (
            f"postgresql://{s.POSTGRES_USER}:{s.POSTGRES_PASSWORD}"
            f"@{s.POSTGRES_HOST}:{s.POSTGRES_PORT}/{s.POSTGRES_DB}"
        )

    async def vector_search(
        self,
        embedding: List[float],
        top_k: int = 10,
        product: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """pgvector cosine distance 검색 (BGE-M3 1024d)"""
        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        product_filter = ""
        params: list = [embedding_str]
        if product:
            # product_id를 실제 filename 패턴으로 변환
            keywords = _PRODUCT_FILENAME_MAP.get(product)
            if keywords:
                # OR 조건으로 여러 키워드 매칭
                conditions = " OR ".join(
                    f"LOWER(d.filename) LIKE LOWER(${i + 2})"
                    for i in range(len(keywords))
                )
                product_filter = f"AND ({conditions})"
                params.extend(f"%{kw}%" for kw in keywords)
            else:
                product_filter = "AND LOWER(d.filename) LIKE LOWER($2)"
                params.append(f"%{product}%")

        # top_k is embedded directly (controlled int) to avoid asyncpg
        # IndeterminateDatatypeError when mixing CAST($1 AS vector) with
        # additional positional parameters.
        safe_limit = int(top_k)

        query = f"""
        SELECT tc.id AS chunk_id,
               tc.content,
               d.filename AS doc_name,
               tc.page_number,
               1 - (tc.embedding <=> CAST($1 AS vector)) AS score
        FROM text_chunks tc
        LEFT JOIN documents d ON tc.document_id = d.id
        WHERE tc.has_embedding = TRUE
        {product_filter}
        ORDER BY tc.embedding <=> CAST($1 AS vector)
        LIMIT {safe_limit}
        """
        try:
            conn = await asyncpg.connect(self._dsn)
            try:
                rows = await conn.fetch(query, *params)
                return [dict(r) for r in rows]
            finally:
                await conn.close()
        except Exception as e:
            logger.warning(f"PostgreSQL vector search failed: {e}")
            return []
