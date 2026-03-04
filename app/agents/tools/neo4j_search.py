"""Neo4j Vector + Graph 검색 클라이언트

스키마 (v1, BGE-M3 1024d 재임베딩 완료):
  - Chunk 노드: id, content, page_number, index, embedding(1024d)
  - Document 노드: filename, type, chunk_count
  - Document -[:CONTAINS]-> Chunk  (38K, 부분 — Protrieve 등 신규 문서)
  - Document -[:HAS_CHUNK]-> Chunk (83K, 기존 문서)
  - Chunk -[:MENTIONS]-> Entity
  - Vector Index: 'chunk_embedding' (1024d, cosine)
"""
import logging
from typing import Any, Dict, List, Optional

from neo4j import AsyncGraphDatabase

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# product_id -> Document.filename 검색 키워드 매핑
# pg_search.py의 _PRODUCT_FILENAME_MAP과 동일 기준
_PRODUCT_FILENAME_MAP: Dict[str, List[str]] = {
    "mvs_openframe_7.1": ["MVS", "OF_Base", "OF_OSC", "OF_Batch", "TJES", "TACF"],
    "openframe_hidb_7": ["HiDB", "IMS"],
    "openframe_osc_7": ["OSC"],
    "tibero_7": ["Tibero"],
    "ofasm_4": ["OFASM", "OF_ASM"],
    "ofcobol_4": ["OFCOBOL", "COBOL"],
    "tmax_6": ["Tmax_6"],
    "jeus_8": ["JEUS"],
    "webtob_5": ["WebtoB"],
    "ofstudio_7": ["OFStudio", "Studio"],
    "protrieve_7": ["Protrieve", "ProTrieve"],
    "xsp_openframe_7": ["XSP", "VOS3", "MSP"],
}


def _build_filename_filter(product: Optional[str]) -> str:
    """product_id로부터 Document.filename WHERE 조건절 생성"""
    if not product:
        return ""
    keywords = _PRODUCT_FILENAME_MAP.get(product)
    if not keywords:
        return f"AND toLower(d.filename) CONTAINS toLower('{product}')"
    conditions = " OR ".join(
        f"toLower(d.filename) CONTAINS toLower('{kw}')" for kw in keywords
    )
    return f"AND ({conditions})"


class Neo4jSearchClient:
    """Neo4j Vector Index 검색 + Entity Graph 탐색"""

    def __init__(self):
        s = get_settings()
        self._driver = AsyncGraphDatabase.driver(
            s.NEO4J_URI, auth=(s.NEO4J_USER, s.NEO4J_PASSWORD)
        )

    async def close(self):
        await self._driver.close()

    async def vector_search(
        self,
        embedding: List[float],
        top_k: int = 10,
        product: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Neo4j Vector Index cosine similarity 검색 (BGE-M3 1024d)

        product가 지정되면 해당 제품 문서의 chunk만 반환.
        Vector Index는 전체를 검색하므로, 오버페치 후 제품 필터링.
        """
        product_filter = _build_filename_filter(product)

        # product 필터가 있으면 오버페치 (필터 후 top_k 보장)
        fetch_k = top_k * 3 if product_filter else top_k

        query = f"""
        CALL db.index.vector.queryNodes('chunk_embedding', $top_k, $embedding)
        YIELD node, score
        OPTIONAL MATCH (d1:Document)-[:HAS_CHUNK]->(node)
        OPTIONAL MATCH (d2:Document)-[:CONTAINS]->(node)
        WITH node, score,
             COALESCE(d1, d2) AS d
        WHERE d IS NOT NULL {product_filter}
        RETURN node.id AS chunk_id,
               node.content AS content,
               COALESCE(d.filename, 'unknown') AS doc_name,
               node.page_number AS page_number,
               d.type AS product,
               score
        ORDER BY score DESC
        LIMIT $result_limit
        """
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    query, top_k=fetch_k, embedding=embedding, result_limit=top_k
                )
                return [dict(record) async for record in result]
        except Exception as e:
            logger.warning(f"Neo4j vector search failed: {e}")
            return []

    async def graph_search(
        self,
        tokens: List[str],
        product: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Entity -> MENTIONS -> Chunk 그래프 탐색

        product가 지정되면 해당 제품 문서의 chunk만 반환.
        """
        if not tokens:
            return []

        product_filter = _build_filename_filter(product)

        query = f"""
        UNWIND $tokens AS token
        MATCH (e:Entity)
        WHERE toLower(e.name) CONTAINS toLower(token)
        MATCH (c:Chunk)-[:MENTIONS]->(e)
        OPTIONAL MATCH (d1:Document)-[:HAS_CHUNK]->(c)
        OPTIONAL MATCH (d2:Document)-[:CONTAINS]->(c)
        WITH c, e, COALESCE(d1, d2) AS d
        WHERE d IS NOT NULL {product_filter}
        RETURN DISTINCT c.id AS chunk_id,
               c.content AS content,
               COALESCE(d.filename, 'unknown') AS doc_name,
               c.page_number AS page_number,
               d.type AS product,
               e.name AS matched_entity,
               0.6 AS score
        ORDER BY score DESC
        LIMIT $limit
        """
        try:
            async with self._driver.session() as session:
                result = await session.run(query, tokens=tokens, limit=limit)
                return [dict(record) async for record in result]
        except Exception as e:
            logger.error(f"Neo4j graph search failed: {e}")
            return []
