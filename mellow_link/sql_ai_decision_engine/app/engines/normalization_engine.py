from __future__ import annotations

from app.handlers.log_handler import LogHandler
from app.handlers.metric_handler import MetricHandler
from app.handlers.natural_language_handler import NaturalLanguageHandler
from app.handlers.table_handler import TableHandler
from app.schemas.normalized import NormalizedAnalysisRequest
from app.schemas.request import AnalyzeRequest


class NormalizationEngine:
    def __init__(self) -> None:
        self.nl_handler = NaturalLanguageHandler()
        self.metric_handler = MetricHandler()
        self.table_handler = TableHandler()
        self.log_handler = LogHandler()

    def normalize(self, request: AnalyzeRequest) -> NormalizedAnalysisRequest:
        if request.input_type == "natural_language":
            return self.nl_handler.handle(request)
        if request.input_type == "metric":
            return self.metric_handler.handle(request)
        if request.input_type == "table":
            return self.table_handler.handle(request)
        if request.input_type == "log":
            return self.log_handler.handle(request)
        raise ValueError(f"Unsupported input_type: {request.input_type}")
