from __future__ import annotations

from pydantic import BaseModel

from app.schemas.normalized import NormalizedAnalysisRequest


class RuleResultItem(BaseModel):
    rule_id: str
    matched: bool
    score: int
    severity: str
    message: str


class AnalyzeResponse(BaseModel):
    status: str
    input_type: str
    normalized_request: NormalizedAnalysisRequest
    sql_results: dict
    rule_results: list[RuleResultItem]
    final_score: int
    decision: str
    ai_interpretation: str
    limitations: list[str]
