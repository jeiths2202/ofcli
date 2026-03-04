"""Query 분석 모델 — Phase 0 Query Agent 출력"""
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class QueryIntent(str, Enum):
    COMMAND = "command"
    ERROR_CODE = "error_code"
    CONFIG = "config"
    CONCEPT = "concept"
    CODE = "code"
    COMPARISON = "comparison"
    PROCEDURE = "procedure"
    TROUBLESHOOT = "troubleshoot"
    GENERAL = "general"


class DetectedLanguage(str, Enum):
    JA = "ja"
    KO = "ko"
    EN = "en"


class ProductMatch(BaseModel):
    product_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    matched_keywords: List[str] = Field(default_factory=list)
    matched_patterns: List[str] = Field(default_factory=list)


class QueryPlan(BaseModel):
    raw_query: str
    normalized_query: str = ""
    intent: QueryIntent = QueryIntent.GENERAL
    language: DetectedLanguage = DetectedLanguage.JA
    products: List[ProductMatch] = Field(default_factory=list)
    requires_code_analysis: bool = False
    query_tokens: List[str] = Field(default_factory=list)
    error_codes: List[str] = Field(default_factory=list)
    command_names: List[str] = Field(default_factory=list)
    expansion_terms: List[str] = Field(default_factory=list)
