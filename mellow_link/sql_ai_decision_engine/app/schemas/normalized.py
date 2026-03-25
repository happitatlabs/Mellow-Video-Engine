from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class NormalizedPeriod(BaseModel):
    start: date
    end: date


class NormalizedAnalysisRequest(BaseModel):
    analysis_type: str
    target: str
    period: NormalizedPeriod
    metrics: list[str]
    filters: dict
    signals: list[str]
    source_types: list[str]
