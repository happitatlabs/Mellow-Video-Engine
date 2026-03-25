from __future__ import annotations

from app.schemas.normalized import NormalizedAnalysisRequest
from app.schemas.request import AnalyzeRequest


class LogHandler:
    """Phase 2 placeholder for log-based ingestion."""

    def handle(self, request: AnalyzeRequest) -> NormalizedAnalysisRequest:
        raise NotImplementedError("log input handler is reserved for Phase 2")
