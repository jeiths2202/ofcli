"""
Phase 0: Query Agent — 쿼리 분석, 의도 분류, 제품 라우팅

LLM 미사용, 순수 Regex/키워드 기반 (< 5ms).
v1 ProductRouterService + QueryTypeClassifier 통합.
"""
import re
from typing import List, Tuple

from app.agents.base import BaseAgent
from app.agents.tools.summary_search import tokenize
from app.models.query import (
    DetectedLanguage,
    ProductMatch,
    QueryIntent,
    QueryPlan,
)
from app.models.search import PipelineState


# ─── 제품 키워드 정의 (v1에서 계승) ───

PRODUCT_KEYWORDS = {
    "mvs_openframe_7.1": {
        "keywords": [
            "tjes", "tjesmgr", "tacf", "tacfmgr", "osc", "oscmgr",
            "jcl", "jclrun", "idcams", "iebgener", "iebcopy", "dfsort",
            "tmboot", "tmdown", "ofboot", "ofdown", "jesinit",
            "dsmigin", "dsmigout", "volmgr", "catmgr",
            "mvs", "openframe base", "of_base",
        ],
        "weight": 1.0,
    },
    "openframe_hidb_7": {
        "keywords": ["hidb", "hidbmgr", "ims", "dbd", "psb", "dlibatch"],
        "weight": 1.0,
    },
    "openframe_osc_7": {
        "keywords": [
            "osc", "oscmgr", "cics", "exec cics", "bms", "dfhmdf",
            "tdq", "tsq", "commarea",
        ],
        "weight": 1.0,
    },
    "tibero_7": {
        "keywords": ["tibero", "tbsql", "tbadmin", "tbloader", "tbrmgr"],
        "weight": 0.9,
    },
    "ofasm_4": {
        "keywords": ["ofasm", "assembler", "asm", "macro", "ofasmif"],
        "weight": 0.9,
    },
    "ofcobol_4": {
        "keywords": ["ofcobol", "cobol", "copybook", "cobprep"],
        "weight": 0.9,
    },
    "tmax_6": {
        "keywords": ["tmax", "tuxedo", "tmadmin", "cll", "clh"],
        "weight": 0.8,
    },
    "jeus_8": {
        "keywords": ["jeus", "webadmin", "jeusadmin", "servlet", "jsp"],
        "weight": 0.8,
    },
    "webtob_5": {
        "keywords": ["webtob", "wsadmin", "wscfl", "httpd"],
        "weight": 0.8,
    },
    "ofstudio_7": {
        "keywords": ["ofstudio", "studio", "editor", "마이그레이션 도구"],
        "weight": 0.7,
    },
    "protrieve_7": {
        "keywords": ["protrieve", "easytrieve", "report"],
        "weight": 0.7,
    },
    "xsp_openframe_7": {
        "keywords": ["xsp", "fujitsu", "facom", "vos3", "msp"],
        "weight": 0.8,
    },
}

# ─── 의도 분류 패턴 ───

ERROR_PATTERNS = [
    re.compile(r"-\d{4,5}"),
    re.compile(r"ABEND\s+S\d{3}", re.IGNORECASE),
    re.compile(r"エラーコード|에러\s*코드|error\s*code", re.IGNORECASE),
]
COMMAND_PATTERNS = [
    re.compile(
        r"(tjesmgr|tacfmgr|oscmgr|osimgr|hidbmgr|catmgr|volmgr|ndbmgr"
        r"|idcams|iebgener|iebcopy|dfsort|dsmigin|dsmigout)\b",
        re.IGNORECASE,
    ),
]
CONFIG_PATTERNS = [
    re.compile(r"\.conf\b|設定|설정|config|parameter|パラメータ", re.IGNORECASE),
]
CODE_PATTERNS = [
    re.compile(r"JCL|COBOL|ASM|サンプル|샘플|sample|ソースコード|소스코드", re.IGNORECASE),
]
COMPARISON_PATTERNS = [
    re.compile(r"違い|比較|차이|비교|differ|compar|vs\b", re.IGNORECASE),
]


class QueryAgent(BaseAgent):
    def __init__(self):
        super().__init__("QueryAgent")

    async def execute(self, state: PipelineState) -> None:
        raw = state.query_plan.raw_query if state.query_plan else ""
        lang = self._detect_language(raw)
        tokens = tokenize(raw)
        intent = self._classify_intent(raw)
        products = self._route_products(tokens, raw)
        error_codes = self._extract_error_codes(raw)
        command_names = self._extract_commands(raw)
        requires_code = intent == QueryIntent.CODE or any(
            kw in raw.lower() for kw in ["jcl", "cobol", "asm", "サンプル", "샘플", "sample"]
        )

        state.query_plan = QueryPlan(
            raw_query=raw,
            normalized_query=" ".join(tokens),
            intent=intent,
            language=lang,
            products=products,
            requires_code_analysis=requires_code,
            query_tokens=tokens,
            error_codes=error_codes,
            command_names=command_names,
        )

    # ─── 내부 메서드 ───

    def _detect_language(self, text: str) -> DetectedLanguage:
        ja_count = len(re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]", text))
        ko_count = len(re.findall(r"[\uac00-\ud7af]", text))
        if ko_count > ja_count:
            return DetectedLanguage.KO
        if ja_count > 0:
            return DetectedLanguage.JA
        return DetectedLanguage.EN

    def _classify_intent(self, text: str) -> QueryIntent:
        for p in ERROR_PATTERNS:
            if p.search(text):
                return QueryIntent.ERROR_CODE
        for p in COMMAND_PATTERNS:
            if p.search(text):
                return QueryIntent.COMMAND
        for p in CONFIG_PATTERNS:
            if p.search(text):
                return QueryIntent.CONFIG
        for p in CODE_PATTERNS:
            if p.search(text):
                return QueryIntent.CODE
        for p in COMPARISON_PATTERNS:
            if p.search(text):
                return QueryIntent.COMPARISON
        return QueryIntent.GENERAL

    def _route_products(self, tokens: List[str], raw: str) -> List[ProductMatch]:
        scores: List[Tuple[str, float, List[str]]] = []
        raw_lower = raw.lower()

        for pid, cfg in PRODUCT_KEYWORDS.items():
            matched = []
            for kw in cfg["keywords"]:
                if kw in raw_lower or kw in tokens:
                    matched.append(kw)
            if matched:
                score = min(len(matched) * 0.25 * cfg["weight"], 1.0)
                scores.append((pid, score, matched))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [
            ProductMatch(product_id=pid, confidence=s, matched_keywords=kws)
            for pid, s, kws in scores
        ]

    def _extract_error_codes(self, text: str) -> List[str]:
        return re.findall(r"-\d{4,5}", text)

    def _extract_commands(self, text: str) -> List[str]:
        pattern = re.compile(
            r"(tjesmgr|tacfmgr|oscmgr|osimgr|hidbmgr|catmgr|volmgr|ndbmgr"
            r"|idcams|iebgener|iebcopy|dfsort|dsmigin|dsmigout)",
            re.IGNORECASE,
        )
        return list({m.lower() for m in pattern.findall(text)})
