from __future__ import annotations

from app.schemas.normalized import NormalizedAnalysisRequest
from app.schemas.request import AnalyzeRequest


class TableHandler:
    """Phase 2 placeholder for table-based ingestion."""

    def handle(self, request: AnalyzeRequest) -> NormalizedAnalysisRequest:
        raise NotImplementedError("table input handler is reserved for Phase 2")
