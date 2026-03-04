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
    ComparisonTarget,
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
            "mvs", "openframe base", "of_base", "openframe", "dcb",
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
    re.compile(
        r"JCL|COBOL|ASM|サンプル|샘플|sample|ソースコード|소스코드"
        r"|ソース|소스|コード作成|코드\s*작성|作成して|작성해"
        r"|サンプルコード|샘플\s*코드|code",
        re.IGNORECASE,
    ),
]
COMPARISON_PATTERNS = [
    re.compile(r"違い|比較|차이|비교|differ|compar|vs\b", re.IGNORECASE),
]

# ─── 툴/유틸리티 → 상위 제품 계층 매핑 ───
TOOL_HIERARCHY = {
    # OSC/CICS 계열
    "bms": {"parent": "CICS/OSC", "product_id": "openframe_osc_7", "category": "화면정의", "desc": "Basic Mapping Support - CICS画面定義ユーティリティ"},
    "dfhmdf": {"parent": "CICS/OSC", "product_id": "openframe_osc_7", "category": "화면정의", "desc": "BMS Macro - 画面フィールド定義マクロ"},
    "tdq": {"parent": "CICS/OSC", "product_id": "openframe_osc_7", "category": "キュー", "desc": "Transient Data Queue"},
    "tsq": {"parent": "CICS/OSC", "product_id": "openframe_osc_7", "category": "キュー", "desc": "Temporary Storage Queue"},
    "commarea": {"parent": "CICS/OSC", "product_id": "openframe_osc_7", "category": "通信領域", "desc": "Communication Area - プログラム間データ受渡し"},
    "exec cics": {"parent": "CICS/OSC", "product_id": "openframe_osc_7", "category": "API", "desc": "CICS API コマンド"},
    "oscmgr": {"parent": "CICS/OSC", "product_id": "openframe_osc_7", "category": "管理ツール", "desc": "OSC管理ユーティリティ"},
    # IMS/HiDB 계열
    "mfs": {"parent": "IMS/HiDB", "product_id": "openframe_hidb_7", "category": "화면정의", "desc": "Message Format Service - IMS画面定義ユーティリティ"},
    "dbd": {"parent": "IMS/HiDB", "product_id": "openframe_hidb_7", "category": "DB定義", "desc": "Database Description - IMS DB構造定義"},
    "psb": {"parent": "IMS/HiDB", "product_id": "openframe_hidb_7", "category": "DB定義", "desc": "Program Specification Block - IMS DBアクセス定義"},
    "dlibatch": {"parent": "IMS/HiDB", "product_id": "openframe_hidb_7", "category": "バッチ", "desc": "DL/I Batch - IMSバッチ処理"},
    "hidbmgr": {"parent": "IMS/HiDB", "product_id": "openframe_hidb_7", "category": "管理ツール", "desc": "HiDB管理ユーティリティ"},
    # MVS/OpenFrame Base 계열
    "jcl": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "ジョブ制御", "desc": "Job Control Language"},
    "idcams": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "データセット", "desc": "VSAM管理ユーティリティ"},
    "iebgener": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "データセット", "desc": "データセットコピーユーティリティ"},
    "iebcopy": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "データセット", "desc": "PDSコピーユーティリティ"},
    "dfsort": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "ソート", "desc": "データソート/マージユーティリティ"},
    "dsmigin": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "マイグレーション", "desc": "データセット移行 (Import)"},
    "dsmigout": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "マイグレーション", "desc": "データセット移行 (Export)"},
    "tjes": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "ジョブ管理", "desc": "Tmax Job Entry Subsystem"},
    "tjesmgr": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "管理ツール", "desc": "TJES管理ユーティリティ"},
    "tacf": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "セキュリティ", "desc": "Tmax Access Control Facility"},
    "tacfmgr": {"parent": "MVS/OpenFrame Base", "product_id": "mvs_openframe_7.1", "category": "管理ツール", "desc": "TACF管理ユーティリティ"},
    # OFASM 계열
    "ofasm": {"parent": "OFASM", "product_id": "ofasm_4", "category": "アセンブラ", "desc": "OpenFrame Assemblerエミュレータ"},
    "ofasmif": {"parent": "OFASM", "product_id": "ofasm_4", "category": "インターフェース", "desc": "OFASMインターフェースツール"},
    # OFCOBOL 계열
    "ofcobol": {"parent": "OFCOBOL", "product_id": "ofcobol_4", "category": "コンパイラ", "desc": "OpenFrame COBOLコンパイラ"},
    "cobprep": {"parent": "OFCOBOL", "product_id": "ofcobol_4", "category": "プリプロセッサ", "desc": "COBOLプリプロセッサ"},
    # Protrieve 계열
    "protrieve": {"parent": "Protrieve", "product_id": "protrieve_7", "category": "レポート", "desc": "Easytrieve互換レポートジェネレータ"},
}


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

        # 비교 의도일 경우 상위 제품 컨텍스트 확장
        comparison_targets = []
        expansion_terms = []
        if intent == QueryIntent.COMPARISON:
            comparison_targets, extra_tokens, extra_products = self._resolve_comparison(
                tokens, raw
            )
            expansion_terms = extra_tokens
            # 비교 대상이 서로 다른 제품에 속하면 두 제품 모두 라우팅
            for ep in extra_products:
                if not any(p.product_id == ep for p in products):
                    products.append(
                        ProductMatch(product_id=ep, confidence=0.5, matched_keywords=[])
                    )

        state.query_plan = QueryPlan(
            raw_query=raw,
            normalized_query=" ".join(tokens),
            intent=intent,
            language=lang,
            products=products,
            requires_code_analysis=requires_code,
            query_tokens=tokens + expansion_terms,
            error_codes=error_codes,
            command_names=command_names,
            expansion_terms=expansion_terms,
            comparison_targets=comparison_targets,
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

    def _resolve_comparison(
        self, tokens: List[str], raw: str
    ) -> Tuple[List[ComparisonTarget], List[str], List[str]]:
        """비교 대상을 TOOL_HIERARCHY에서 조회하여 상위 제품 컨텍스트 반환"""
        targets = []
        extra_tokens = []
        extra_product_ids = []
        raw_lower = raw.lower()

        for tool_name, info in TOOL_HIERARCHY.items():
            if tool_name in raw_lower or tool_name in tokens:
                targets.append(ComparisonTarget(
                    term=tool_name.upper(),
                    parent_product=info["parent"],
                    category=info["category"],
                    description=info["desc"],
                ))
                # 상위 제품 키워드를 토큰에 추가하여 검색 범위 확장
                parent_tokens = info["parent"].lower().replace("/", " ").split()
                extra_tokens.extend(parent_tokens)
                if info["product_id"] not in extra_product_ids:
                    extra_product_ids.append(info["product_id"])

        return targets, extra_tokens, extra_product_ids

    def _extract_error_codes(self, text: str) -> List[str]:
        return re.findall(r"-\d{4,5}", text)

    def _extract_commands(self, text: str) -> List[str]:
        pattern = re.compile(
            r"(tjesmgr|tacfmgr|oscmgr|osimgr|hidbmgr|catmgr|volmgr|ndbmgr"
            r"|idcams|iebgener|iebcopy|dfsort|dsmigin|dsmigout)",
            re.IGNORECASE,
        )
        return list({m.lower() for m in pattern.findall(text)})
