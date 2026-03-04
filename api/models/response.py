"""API Response Models"""
from typing import Dict, List, Optional

from pydantic import BaseModel


class SourceInfo(BaseModel):
    document: str
    page: Optional[int] = None
    score: float
    type: str


class UsageInfo(BaseModel):
    total_time_ms: int
    phases_executed: List[int]
    fallback_used: bool
    phase_times: Optional[Dict[str, int]] = None


class QueryResponse(BaseModel):
    success: bool
    answer: str
    language: str
    confidence: float
    intent: str
    product: str
    sources: Optional[List[SourceInfo]] = None
    usage: UsageInfo


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


class ServiceStatus(BaseModel):
    status: str
    latency_ms: int


class HealthResponse(BaseModel):
    status: str
    services: Dict[str, ServiceStatus]
    version: str


class ProductInfo(BaseModel):
    id: str
    name: str
    keywords: List[str]


class ProductsResponse(BaseModel):
    products: List[ProductInfo]
