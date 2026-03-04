"""
Phase 6: Response Agent — 結果統合・検証・出典表記・用語教正

全 Phase の結果を統合し、最終応答を生成する。
"""
import logging
import re
import time
from typing import Dict, List, Tuple

from app.agents.base import BaseAgent
from app.agents.tools.qwen3_client import Qwen3Client
from app.models.query import QueryIntent, QueryPlan
from app.models.response import (
    FinalResponse,
    SourceAttribution,
    VerificationLevel,
    VerifiedSentence,
)
from app.models.search import PipelineState, SearchChunk, SearchSource

logger = logging.getLogger(__name__)


# ─── 出典名サニタイズ: 顧客名等を除去 ───
_DOC_NAME_SANITIZE_RE = re.compile(r'\s*\([^)]*\)\s*')


def _sanitize_doc_name(name: str) -> str:
    """ドキュメント名から括弧付き顧客名等を除去する"""
    return _DOC_NAME_SANITIZE_RE.sub('', name).strip()


# ─── 用語教正辞典 (v1 ResponseVerifier から継承) ───

TERM_CORRECTIONS: Dict[str, str] = {
    "TJE (Tivoli Job Entry)": "TJES (Tmax Job Entry Subsystem)",
    "TJE(Tivoli Job Entry)": "TJES(Tmax Job Entry Subsystem)",
    "Tivoli Job Entry Subsystem": "Tmax Job Entry Subsystem",
    "Tivoli Job Entry": "Tmax Job Entry Subsystem",
    "RACF (Resource Access Control)": "TACF (Tmax Access Control Facility)",
    "IBM TJES": "TmaxSoft TJES",
    "IBM TACF": "TmaxSoft TACF",
    "Fujitsu TJES": "TmaxSoft TJES",
}


def _correct_terms(text: str) -> Tuple[str, List[str]]:
    corrections = []
    for wrong, right in TERM_CORRECTIONS.items():
        if wrong in text:
            text = text.replace(wrong, right)
            corrections.append(f"{wrong} → {right}")
    return text, corrections


def _split_sentences(text: str) -> List[str]:
    """文を分割 (日本語: 。 / 英語: . / 韓国語: .)"""
    parts = re.split(r"(?<=[。.!?\n])\s*", text)
    return [s.strip() for s in parts if s.strip() and len(s.strip()) > 5]


def _word_overlap(sentence: str, chunk_content: str) -> float:
    """単語重複ベースの類似度 (0~1)"""
    s_words = set(re.findall(r"\w{2,}", sentence.lower()))
    c_words = set(re.findall(r"\w{2,}", chunk_content.lower()))
    if not s_words or not c_words:
        return 0.0
    overlap = s_words & c_words
    return len(overlap) / max(len(s_words), 1)


class ResponseAgent(BaseAgent):
    """Phase 6: 最終応答生成"""

    VERIFIED_THRESHOLD = 0.7
    INFERRED_THRESHOLD = 0.4

    def __init__(self):
        super().__init__("ResponseAgent")
        self.llm = Qwen3Client()

    async def execute(self, state: PipelineState) -> FinalResponse:
        plan: QueryPlan = state.query_plan
        t0 = time.perf_counter()

        all_chunks = state.get_top_chunks(min_score=0.05, limit=20)

        # ─── 応答テキスト決定 ───
        raw_answer = await self._select_or_generate(plan, state, all_chunks)

        # ─── 用語教正 ───
        corrected, corrections = _correct_terms(raw_answer)
        if corrections:
            logger.info(f"[Phase 6] Term corrections: {corrections}")

        # ─── フォールバック警告 ───
        if state.fallback_triggered:
            warning = self._fallback_warning(plan.language.value)
            corrected = warning + "\n\n" + corrected

        # ─── 文ごと検証 ───
        verification = self._verify_sentences(corrected, all_chunks)

        # ─── 出典表記 ───
        sources = self._build_sources(all_chunks[:5])

        # ─── 信頼度 ───
        if verification:
            verified_count = sum(
                1 for v in verification if v.level == VerificationLevel.VERIFIED
            )
            overall = verified_count / len(verification)
        else:
            overall = state.accumulated_max_score

        elapsed = int((time.perf_counter() - t0) * 1000)

        # Phase 別タイム集計
        phase_times = {}
        for p, r in state.phase_results.items():
            phase_times[r.phase_name or f"phase_{p}"] = r.execution_time_ms
        phase_times["response_agent"] = elapsed

        return FinalResponse(
            success=True,
            answer=corrected,
            answer_language=plan.language.value,
            product=plan.products[0].product_id if plan.products else "auto",
            query_intent=plan.intent.value,
            phases_executed=sorted(state.phase_results.keys()),
            fallback_used=state.fallback_triggered,
            verification=verification,
            overall_confidence=round(overall, 3),
            sources=sources,
            total_time_ms=elapsed,
            phase_times=phase_times,
        )

    async def _select_or_generate(
        self, plan: QueryPlan, state: PipelineState, chunks: List[SearchChunk]
    ) -> str:
        """Phase 2 で既に LLM 応答があれば流用、なければ最終生成"""
        phase2 = state.phase_results.get(2)
        if phase2 and phase2.max_score >= 0.7 and phase2.chunks:
            return phase2.chunks[0].content

        # Phase 5 fallback 結果
        phase5 = state.phase_results.get(5)
        if phase5 and phase5.chunks:
            return phase5.chunks[0].content

        # chunks ベースで最終 LLM 生成
        if chunks:
            return await self._final_llm_synthesis(plan, chunks)

        return "検索結果が見つかりませんでした。質問を変えてお試しください。"

    async def _final_llm_synthesis(
        self, plan: QueryPlan, chunks: List[SearchChunk]
    ) -> str:
        ctx = "\n\n---\n\n".join(
            f"[{_sanitize_doc_name(c.doc_name) if c.doc_name else 'N/A'} p.{c.page_number or '?'}] (score: {c.score:.2f})\n{c.content[:1200]}"
            for c in chunks[:5]
        )

        if plan.intent == QueryIntent.CODE:
            prompt = f"""以下の検索結果を統合して質問に回答してください。
検索結果に記載されたAPI・関数・構文を使用してサンプルコードを生成してください。

## 検索結果
{ctx}

## 質問
{plan.raw_query}

## 回答指示
1. API/機能の説明
2. サンプルコード（コメント付き）
3. 補足説明

## 回答"""
            max_tokens = 4096
        else:
            prompt = f"""以下の検索結果を統合して質問に回答してください。
検索結果にない情報は追加しないでください。

## 検索結果
{ctx}

## 質問
{plan.raw_query}

## 回答"""
            max_tokens = None

        try:
            answer = await self.llm.chat(prompt, temperature=0.3, max_tokens=max_tokens)
            return re.sub(r"<think>.*?</think>\s*", "", answer, flags=re.DOTALL).strip()
        except Exception as e:
            logger.error(f"Final synthesis failed: {e}")
            return chunks[0].content if chunks else ""

    def _verify_sentences(
        self, answer: str, chunks: List[SearchChunk]
    ) -> List[VerifiedSentence]:
        sentences = _split_sentences(answer)
        chunk_texts = [c.content for c in chunks if c.source != SearchSource.LLM_FALLBACK]
        if not chunk_texts:
            return [
                VerifiedSentence(text=s, level=VerificationLevel.UNVERIFIED, similarity=0.0)
                for s in sentences
            ]

        results = []
        combined = " ".join(chunk_texts)
        for sent in sentences:
            sim = _word_overlap(sent, combined)
            if sim >= self.VERIFIED_THRESHOLD:
                level = VerificationLevel.VERIFIED
            elif sim >= self.INFERRED_THRESHOLD:
                level = VerificationLevel.INFERRED
            else:
                level = VerificationLevel.UNVERIFIED
            results.append(VerifiedSentence(
                text=sent, level=level, similarity=round(sim, 3)
            ))
        return results

    def _build_sources(self, chunks: List[SearchChunk]) -> List[SourceAttribution]:
        seen = set()
        sources = []
        for c in chunks:
            key = (c.doc_name, c.page_number)
            if key in seen:
                continue
            seen.add(key)
            sources.append(SourceAttribution(
                doc_name=_sanitize_doc_name(c.doc_name) if c.doc_name else "unknown",
                page=c.page_number,
                section=c.section,
                url=c.metadata.get("url") if c.metadata else None,
                score=c.score,
                source_type=c.source.value,
            ))
        return sources

    def _fallback_warning(self, lang: str) -> str:
        if lang == "ko":
            return "⚠️ 검증된 문서에서 충분한 정보를 찾지 못했습니다. 아래는 AI의 일반 지식 기반 답변입니다."
        if lang == "en":
            return "⚠️ Insufficient information found in verified documents. Below is an AI-generated answer based on general knowledge."
        return "⚠️ 検証済みドキュメントから十分な情報が見つかりませんでした。以下はAIの一般知識に基づく回答です。"
