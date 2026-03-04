"""최종 응답 모델 — Phase 6 Response Agent 출력"""
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class VerificationLevel(str, Enum):
    VERIFIED = "verified"
    INFERRED = "inferred"
    UNVERIFIED = "unverified"


class VerifiedSentence(BaseModel):
    text: str
    level: VerificationLevel
    similarity: float = Field(ge=0.0, le=1.0)
    source_chunk_id: Optional[str] = None
    source_doc: Optional[str] = None


class SourceAttribution(BaseModel):
    doc_name: str
    page: Optional[int] = None
    section: Optional[str] = None
    url: Optional[str] = None
    score: float = Field(ge=0.0, le=1.0)
    source_type: str = ""


class FinalResponse(BaseModel):
    success: bool = True
    answer: str = ""
    answer_language: str = "ja"
    product: str = "auto"
    query_intent: str = "general"
    phases_executed: List[int] = Field(default_factory=list)
    fallback_used: bool = False
    verification: List[VerifiedSentence] = Field(default_factory=list)
    overall_confidence: float = 0.0
    sources: List[SourceAttribution] = Field(default_factory=list)
    total_time_ms: int = 0
    phase_times: Dict[str, int] = Field(default_factory=dict)
