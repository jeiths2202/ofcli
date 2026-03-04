"""Summary BM25 파일 기반 검색 (v1 StructuredKnowledgeStore 경량 재현)"""
import glob
import logging
import math
import os
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 프로젝트 루트 기준 summaries 경로
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
# v1 데이터를 참조하려면 심볼릭 링크 또는 환경변수로 지정
SUMMARIES_BASE = os.environ.get(
    "SUMMARIES_PATH",
    os.path.join(_PROJECT_ROOT, "..", "kms-docker-remote", "uploads", "summaries"),
)

_TOKEN_RE = re.compile(
    r"[a-z0-9][a-z0-9_\-]*[a-z0-9]|[a-z0-9]"
    r"|[\u30a0-\u30ff]{2,}"
    r"|[\u4e00-\u9fff]+"
    r"|[\uac00-\ud7af]{2,}"
    r"|[\u3040-\u309f]{2,}",
)

_STOPWORDS = frozenset(
    [
        "の", "は", "が", "を", "に", "で", "と", "も", "や", "か",
        "について", "してください", "ください", "とは", "教えて",
        "説明して", "知りたい", "the", "a", "an", "of", "in", "to",
        "is", "are", "and", "or", "what", "how", "about", "explain",
    ]
)


def tokenize(text: str) -> List[str]:
    raw = _TOKEN_RE.findall(text.lower())
    return [t for t in raw if t not in _STOPWORDS and len(t) > 1]


class SummarySearch:
    """
    파일 시스템 기반 요약본 BM25 검색.
    uploads/summaries/ 하위 Markdown 파일을 파싱하여 섹션 단위 검색.
    """

    def __init__(self):
        self._sections: List[Dict] = []
        self._loaded = False
        self._idf: Dict[str, float] = {}

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._load_all_summaries()
        self._build_idf()
        self._loaded = True

    def _load_all_summaries(self):
        if not os.path.isdir(SUMMARIES_BASE):
            logger.warning(f"Summaries dir not found: {SUMMARIES_BASE}")
            return

        for md_path in glob.glob(os.path.join(SUMMARIES_BASE, "**", "*.md"), recursive=True):
            try:
                with open(md_path, encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            # 섹션 분할 (## 또는 ### 기준)
            sections = re.split(r"\n(?=#{2,3}\s)", content)
            rel_path = os.path.relpath(md_path, SUMMARIES_BASE)
            domain = rel_path.split(os.sep)[0] if os.sep in rel_path else "general"

            for sec in sections:
                sec = sec.strip()
                if not sec or len(sec) < 20:
                    continue
                title_match = re.match(r"#{2,3}\s+(.+)", sec)
                title = title_match.group(1) if title_match else ""
                tokens = tokenize(sec)
                self._sections.append({
                    "title": title,
                    "content": sec[:2000],
                    "tokens": set(tokens),
                    "source_file": rel_path,
                    "domain": domain,
                })

        logger.info(f"Loaded {len(self._sections)} summary sections from {SUMMARIES_BASE}")

    def _build_idf(self):
        n = max(len(self._sections), 1)
        df: Dict[str, int] = {}
        for sec in self._sections:
            for t in sec["tokens"]:
                df[t] = df.get(t, 0) + 1
        self._idf = {t: math.log(n / (1 + count)) for t, count in df.items()}

    def search(
        self,
        query_tokens: List[str],
        product: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict]:
        """BM25-like 키워드 검색"""
        self._ensure_loaded()
        if not query_tokens or not self._sections:
            return []

        results = []
        for sec in self._sections:
            # 제품 필터
            if product and product.lower() not in sec["source_file"].lower():
                continue

            score = 0.0
            for qt in query_tokens:
                if qt in sec["tokens"]:
                    score += self._idf.get(qt, 1.0)

            if score > 0:
                results.append({
                    "title": sec["title"],
                    "content": sec["content"],
                    "source_file": sec["source_file"],
                    "domain": sec["domain"],
                    "score": score,
                })

        results.sort(key=lambda x: x["score"], reverse=True)

        # 점수 정규화 (0~1)
        if results:
            max_s = results[0]["score"]
            for r in results:
                r["score"] = min(r["score"] / max(max_s, 1.0), 1.0)

        return results[:top_k]
