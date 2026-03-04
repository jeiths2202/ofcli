"""API Request Models"""
from typing import Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(
        ..., min_length=1, max_length=2000, description="Query text"
    )
    language: Optional[str] = Field(
        None, pattern="^(ja|ko|en)$", description="Response language (auto-detect if omitted)"
    )
    product: Optional[str] = Field(
        None, description="Product filter (e.g. openframe_osc_7)"
    )
    include_sources: bool = Field(True, description="Include source attribution")
    include_phases: bool = Field(False, description="Include phase timing details")
