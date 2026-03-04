"""Neo4j Vector + Graph 검색 클라이언트

스키마 (v1, BGE-M3 1024d 재임베딩 완료):
  - Chunk 노드: id, content, page_number, index, embedding(1024d)
  - Document 노드: filename, type, chunk_count
  - Document -[:CONTAINS]-> Chunk  (37K/83K, 부분)
  - Document -[:HAS_CHUNK]-> Chunk (83K/83K, 거의 전체)
  - Chunk -[:MENTIONS]-> Entity
  - Vector Index: 'chunk_embedding' (1024d, cosine)
"""
import logging
from typing import Any, Dict, List, Optional

from neo4j import AsyncGraphDatabase

from app.core.config import get_settings

logger = logging.getLogger(__name__)


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
        """Neo4j Vector Index cosine similarity 검색 (BGE-M3 1024d)"""
        # CONTAINS covers 37K chunks, HAS_CHUNK covers 83K chunks.
        # Use COALESCE to try both relationships for maximum coverage.
        query = """
        CALL db.index.vector.queryNodes('chunk_embedding', $top_k, $embedding)
        YIELD node, score
        OPTIONAL MATCH (d1:Document)-[:HAS_CHUNK]->(node)
        OPTIONAL MATCH (d2:Document)-[:CONTAINS]->(node)
        WITH node, score,
             COALESCE(d1, d2) AS d
        RETURN node.id AS chunk_id,
               node.content AS content,
               COALESCE(d.filename, 'unknown') AS doc_name,
               node.page_number AS page_number,
               d.type AS product,
               score
        ORDER BY score DESC
        """
        try:
            async with self._driver.session() as session:
                result = await session.run(query, top_k=top_k, embedding=embedding)
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
        """Entity -> MENTIONS -> Chunk 그래프 탐색"""
        if not tokens:
            return []

        query = """
        UNWIND $tokens AS token
        MATCH (e:Entity)
        WHERE toLower(e.name) CONTAINS toLower(token)
        MATCH (c:Chunk)-[:MENTIONS]->(e)
        OPTIONAL MATCH (d1:Document)-[:HAS_CHUNK]->(c)
        OPTIONAL MATCH (d2:Document)-[:CONTAINS]->(c)
        WITH c, e, COALESCE(d1, d2) AS d
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
