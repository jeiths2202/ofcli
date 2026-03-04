"""
Phase 5: Fallback Agent — Qwen3 자체 지식 + Tool Calling

Phase 1~4 누적 max_score < 0.3 일 때만 실행.
응답에 미검증 경고를 포함.
"""
import logging
import re
import time

from app.agents.base import BaseAgent
from app.agents.tools.qwen3_client import Qwen3Client
from app.models.query import QueryPlan
from app.models.search import (
    PhaseResult,
    PipelineState,
    SearchChunk,
    SearchSource,
)

logger = logging.getLogger(__name__)

_FALLBACK_SYSTEM = """あなたはTmaxSoft OpenFrame製品の専門家です。
検索システムで十分な情報が見つかりませんでした。
あなたの知識で回答しますが、以下のルールを守ってください:
1. 確信がない情報には「※未検証」と付記すること。
2. OpenFrame固有の情報については推測を避けること。
3. 一般的なメインフレーム知識で回答可能な場合はそれを活用すること。"""

# Tool definitions for Qwen3 Tool Calling
_FALLBACK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for TmaxSoft OpenFrame documentation",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
]


class FallbackAgent(BaseAgent):
    """Phase 5: Qwen3 自体知識 Fallback + Tool Calling (max_score < 0.3 のみ)"""

    def __init__(self):
        super().__init__("FallbackAgent")
        self.llm = Qwen3Client()

    async def execute(self, state: PipelineState) -> None:
        plan: QueryPlan = state.query_plan
        t0 = time.perf_counter()

        # Phase 1~4 の関連コンテキスト（低スコアでも参考情報として渡す）
        prior_chunks = state.get_top_chunks(min_score=0.0, limit=3)
        context = ""
        if prior_chunks:
            snippets = [f"- {c.content[:300]}" for c in prior_chunks]
            context = f"\n\n## 参考情報（低信頼度）\n" + "\n".join(snippets)

        prompt = f"""## 質問
{plan.raw_query}
{context}

## 回答"""

        try:
            answer = await self.llm.chat(
                prompt,
                system=_FALLBACK_SYSTEM,
                temperature=0.5,
                tools=_FALLBACK_TOOLS,
            )
            answer = re.sub(r"<think>.*?</think>\s*", "", answer, flags=re.DOTALL).strip()
            # [自体知識] タグ付与
            answer = f"[自体知識] {answer}"
        except Exception as e:
            logger.error(f"Fallback LLM failed: {e}")
            answer = "[自体知識] 申し訳ございません。現在回答を生成できません。"

        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.info(f"[Phase 5] fallback generated, {elapsed}ms")

        state.add_phase_result(PhaseResult(
            phase=5,
            phase_name="llm_fallback",
            chunks=[SearchChunk(
                chunk_id="fallback_0",
                content=answer,
                score=0.2,  # フォールバックは常に低信頼度
                source=SearchSource.LLM_FALLBACK,
            )],
            max_score=0.2,
            execution_time_ms=elapsed,
        ))
