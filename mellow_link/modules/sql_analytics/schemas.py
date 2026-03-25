from __future__ import annotations

from pydantic import BaseModel, Field


class SQLAnalyticsStartRequest(BaseModel):
    question: str = Field(..., min_length=3, description="Natural language analytics question")
    input_type: str = Field(default="natural_language", description="Input type for the SQL analytics pipeline")


class SQLAnalyticsStartResponse(BaseModel):
    run_id: str
    session_id: str
    module_id: str = "sql_analytics"
    run_kind: str = "sql_analysis"
