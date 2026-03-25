from __future__ import annotations

from app.schemas.normalized import NormalizedAnalysisRequest, NormalizedPeriod
from app.schemas.request import AnalyzeRequest, Period


class MetricHandler:
    def handle(self, request: AnalyzeRequest) -> NormalizedAnalysisRequest:
        metrics = list(request.metrics or [])
        target = "customer_churn" if "churn_rate" in metrics else "customer_refund"
        period = request.period or Period(start="2026-03-01", end="2026-03-31")
        segment = request.segment or "all"

        return NormalizedAnalysisRequest(
            analysis_type="root_cause",
            target=target,
            period=NormalizedPeriod(start=period.start, end=period.end),
            metrics=metrics,
            filters={"segment": segment},
            signals=[f"{m} 모니터링" for m in metrics],
            source_types=["metric"],
        )
