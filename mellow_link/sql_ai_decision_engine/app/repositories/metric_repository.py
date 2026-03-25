from __future__ import annotations


class MetricRepository:
    def __init__(self) -> None:
        self.allowed_metrics = {"refund_rate", "inquiry_growth", "churn_rate"}

    def validate(self, metrics: list[str]) -> list[str]:
        return [metric for metric in metrics if metric in self.allowed_metrics]
