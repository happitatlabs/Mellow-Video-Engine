from __future__ import annotations

from app.schemas.normalized import NormalizedAnalysisRequest, NormalizedPeriod
from app.schemas.request import AnalyzeRequest, Period


class NaturalLanguageHandler:
    def handle(self, request: AnalyzeRequest) -> NormalizedAnalysisRequest:
        query = request.query or ""
        metrics = list(request.metrics or [])
        signals: list[str] = []

        if "환불" in query and "refund_rate" not in metrics:
            metrics.append("refund_rate")
            signals.append("환불 증가")
        if "문의" in query and "inquiry_growth" not in metrics:
            metrics.append("inquiry_growth")
            signals.append("문의 증가")
        if "이탈" in query and "churn_rate" not in metrics:
            metrics.append("churn_rate")
            signals.append("이탈률 증가")

        if not metrics:
            metrics = ["refund_rate"]
            signals.append("환불 지표 확인")

        target = "customer_refund" if "refund_rate" in metrics else "customer_churn"
        period = request.period or Period(start="2026-03-01", end="2026-03-31")
        segment = request.segment or "all"

        return NormalizedAnalysisRequest(
            analysis_type="root_cause",
            target=target,
            period=NormalizedPeriod(start=period.start, end=period.end),
            metrics=metrics,
            filters={"segment": segment},
            signals=signals,
            source_types=["natural_language"],
        )
