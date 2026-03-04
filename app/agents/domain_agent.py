"""
Phase 2: Domain Agent — Qwen3 CPT 도메인 지식 기반 생성

구조화 쿼리 → 검색결과 기반 템플릿 응답 (LLM 최소화)
비구조화 쿼리 → Qwen3 LLM + Phase 1 chunks 컨텍스트 기반 생성
"""
import logging
import re
import time

_DOC_NAME_SANITIZE_RE = re.compile(r'\s*\([^)]*\)\s*')

from app.agents.base import BaseAgent
from app.agents.tools.qwen3_client import Qwen3Client
from app.models.query import ComparisonTarget, QueryIntent, QueryPlan
from app.models.search import (
    PhaseResult,
    PipelineState,
    SearchChunk,
    SearchSource,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_JA = """あなたはTmaxSoft OpenFrame製品の専門家です。
以下のルールを厳守してください:
1. 検索結果に基づいて回答すること。検索結果にない情報は追加しないこと。
2. 出典（ドキュメント名・ページ）を明記すること。
3. 不明な場合は「情報が見つかりませんでした」と回答すること。
4. OpenFrame固有用語は正式名称を使うこと（例: TJES = Tmax Job Entry Subsystem）。"""

SYSTEM_PROMPT_KO = """당신은 TmaxSoft OpenFrame 제품 전문가입니다.
다음 규칙을 엄격히 준수하세요:
1. 검색 결과를 기반으로 답변하세요. 검색 결과에 없는 정보는 추가하지 마세요.
2. 출처(문서명, 페이지)를 명기하세요.
3. 모르는 경우 "정보를 찾지 못했습니다"라고 답변하세요.
4. OpenFrame 고유 용어는 정식 명칭을 사용하세요."""

SYSTEM_PROMPT_EN = """You are a TmaxSoft OpenFrame product expert.
Follow these rules strictly:
1. Answer based on search results only. Do not add information not in search results.
2. Cite sources (document name, page).
3. If unsure, respond with "Information not found."
4. Use official names for OpenFrame terms (e.g., TJES = Tmax Job Entry Subsystem)."""

# CODE intent 전용 시스템 프롬프트 (코드 생성 허용)
SYSTEM_PROMPT_CODE_JA = """あなたはTmaxSoft OpenFrame製品の専門家であり、プログラミングアシスタントです。
以下のルールを厳守してください:
1. 検索結果に基づいてAPIや機能を説明すること。
2. 検索結果に記載されたAPI・関数・構文を使用してサンプルコードを生成すること。
3. サンプルコードには適切なコメントを付けること。
4. 出典（ドキュメント名・ページ）を明記すること。
5. OpenFrame固有用語は正式名称を使うこと。"""

SYSTEM_PROMPT_CODE_KO = """당신은 TmaxSoft OpenFrame 제품 전문가이자 프로그래밍 어시스턴트입니다.
다음 규칙을 엄격히 준수하세요:
1. 검색 결과를 기반으로 API와 기능을 설명하세요.
2. 검색 결과에 기재된 API/함수/구문을 사용하여 샘플 코드를 생성하세요.
3. 샘플 코드에는 적절한 주석을 추가하세요.
4. 출처(문서명, 페이지)를 명기하세요.
5. OpenFrame 고유 용어는 정식 명칭을 사용하세요."""

SYSTEM_PROMPT_CODE_EN = """You are a TmaxSoft OpenFrame product expert and programming assistant.
Follow these rules strictly:
1. Explain APIs and features based on search results.
2. Generate sample code using APIs/functions/syntax found in search results.
3. Add appropriate comments to sample code.
4. Cite sources (document name, page).
5. Use official names for OpenFrame terms."""


def _get_system_prompt(lang: str, intent: str = "") -> str:
    if intent == "code":
        if lang == "ko":
            return SYSTEM_PROMPT_CODE_KO
        if lang == "en":
            return SYSTEM_PROMPT_CODE_EN
        return SYSTEM_PROMPT_CODE_JA
    if lang == "ko":
        return SYSTEM_PROMPT_KO
    if lang == "en":
        return SYSTEM_PROMPT_EN
    return SYSTEM_PROMPT_JA


# 구조화 의도 (템플릿 응답 우선)
_STRUCTURED_INTENTS = {QueryIntent.COMMAND, QueryIntent.ERROR_CODE, QueryIntent.CONFIG}


class DomainAgent(BaseAgent):
    """Phase 2: CPT 도메인 지식 Agent"""

    def __init__(self):
        super().__init__("DomainAgent")
        self.llm = Qwen3Client()

    async def execute(self, state: PipelineState) -> None:
        plan: QueryPlan = state.query_plan
        t0 = time.perf_counter()

        # Phase 1 검색 결과
        phase1 = state.phase_results.get(1)
        chunks = phase1.chunks if phase1 else []

        if plan.intent in _STRUCTURED_INTENTS and chunks:
            answer, confidence = self._build_structured(plan, chunks)
        else:
            answer, confidence = await self._generate_llm(plan, chunks)

        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.info(
            f"[Phase 2] intent={plan.intent.value}, confidence={confidence:.2f}, {elapsed}ms"
        )

        state.add_phase_result(PhaseResult(
            phase=2,
            phase_name="domain_knowledge",
            chunks=[SearchChunk(
                chunk_id=f"domain_{plan.intent.value}",
                content=answer,
                score=confidence,
                source=SearchSource.CPT_KNOWLEDGE,
                product_id=plan.products[0].product_id if plan.products else "",
            )],
            max_score=confidence,
            execution_time_ms=elapsed,
        ))

    def _build_structured(self, plan: QueryPlan, chunks: list) -> tuple:
        """구조화 쿼리: 검색결과 직접 정리 (LLM 불필요)"""
        parts = []
        for i, c in enumerate(chunks[:5]):
            raw_name = _DOC_NAME_SANITIZE_RE.sub('', c.doc_name) if c.doc_name else 'N/A'
            src = f"[{raw_name}"
            if c.page_number:
                src += f" p.{c.page_number}"
            src += "]"
            parts.append(f"{c.content}\n— {src}")

        answer = "\n\n".join(parts)
        confidence = min(chunks[0].score + 0.1, 1.0) if chunks else 0.0
        return answer, confidence

    def _build_comparison_context(self, targets: list[ComparisonTarget]) -> str:
        """비교 대상의 상위 제품 컨텍스트를 프롬프트에 주입"""
        lines = []
        for t in targets:
            lines.append(
                f"- {t.term}: 上位製品={t.parent_product}, "
                f"分類={t.category}, 説明={t.description}"
            )
        return "\n".join(lines)

    async def _generate_llm(self, plan: QueryPlan, chunks: list) -> tuple:
        """비구조화 쿼리: Qwen3 LLM + RAG 컨텍스트"""
        ctx_parts = []
        for c in chunks[:5]:
            src = _DOC_NAME_SANITIZE_RE.sub('', c.doc_name) if c.doc_name else "unknown"
            page = f" p.{c.page_number}" if c.page_number else ""
            ctx_parts.append(f"[Source: {src}{page}]\n{c.content[:1500]}")

        context_text = "\n\n---\n\n".join(ctx_parts) if ctx_parts else "(検索結果なし)"
        system = _get_system_prompt(plan.language.value, plan.intent.value)

        # CODE 의도: 코드 생성 전용 프롬프트
        if plan.intent == QueryIntent.CODE:
            prompt = f"""## 検索結果
{context_text}

## 質問
{plan.raw_query}

## 回答指示
以下の順序で回答してください:
1. **API/機能の説明**: 検索結果から該当するAPI・関数・構文を説明
2. **サンプルコード**: 上記のAPI/関数を使用した実用的なサンプルコードを生成（コメント付き）
3. **補足説明**: コンパイル方法、注意事項など

## 回答"""
            max_tokens = 4096
        # 비교 의도 + 상위 제품 컨텍스트가 있으면 전용 프롬프트
        elif plan.intent == QueryIntent.COMPARISON and plan.comparison_targets:
            hierarchy_ctx = self._build_comparison_context(plan.comparison_targets)
            prompt = f"""## 比較対象の上位製品コンテキスト
{hierarchy_ctx}

## 検索結果
{context_text}

## 質問
{plan.raw_query}

## 回答指示
以下の順序で体系的に比較してください:
1. **上位製品レベル**: 各ツールが属する製品（例: CICS/OSC vs IMS/HiDB）の目的・位置づけの違い
2. **サブシステムレベル**: 各ツールが担当するサブシステム内での役割の違い
3. **ツール固有の詳細**: 具体的な構文、機能、設定方法の違い
4. **まとめ表**: 主要な違いを表形式で整理

## 回答"""
            max_tokens = None
        else:
            prompt = f"""## 検索結果
{context_text}

## 質問
{plan.raw_query}

## 回答"""
            max_tokens = None

        try:
            answer = await self.llm.chat(
                prompt, system=system, temperature=0.3, max_tokens=max_tokens
            )
            # <think> タグ除去
            answer = re.sub(r"<think>.*?</think>\s*", "", answer, flags=re.DOTALL).strip()
            # confidence: chunks があれば高め
            confidence = 0.7 if chunks else 0.4
        except Exception as e:
            logger.error(f"Domain LLM failed: {e}")
            answer = "LLMからの応答取得に失敗しました。"
            confidence = 0.1

        return answer, confidence
