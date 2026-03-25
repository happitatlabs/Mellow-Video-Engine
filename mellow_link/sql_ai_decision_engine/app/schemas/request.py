from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Period(BaseModel):
    start: date
    end: date


class AnalyzeRequest(BaseModel):
    input_type: Literal["natural_language", "metric", "table", "log"]
    query: str | None = Field(default=None, description="자연어 입력")
    metrics: list[str] | None = Field(default=None, description="지표 목록")
    segment: str | None = Field(default="all", description="세그먼트")
    period: Period | None = None
    table_name: str | None = None
    log_file_path: str | None = None

    @model_validator(mode="after")
    def validate_by_input_type(self) -> "AnalyzeRequest":
        if self.input_type == "natural_language" and not self.query:
            raise ValueError("query is required for natural_language input")
        if self.input_type == "metric" and not self.metrics:
            raise ValueError("metrics is required for metric input")
        if self.input_type == "table" and not self.table_name:
            raise ValueError("table_name is required for table input")
        if self.input_type == "log" and not self.log_file_path:
            raise ValueError("log_file_path is required for log input")
        return self
