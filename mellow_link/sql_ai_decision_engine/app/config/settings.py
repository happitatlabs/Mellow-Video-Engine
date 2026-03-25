from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    app_name: str = "SQL-AI Decision Engine"
    app_version: str = "0.1.0"
    analysis_type: str = "root_cause"
    use_mock_sql: bool = Field(
        default_factory=lambda: os.getenv("USE_MOCK_SQL", "false").strip().lower() == "true"
    )
    database_url: str = Field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            str(Path(__file__).resolve().parents[2] / "sample_data" / "decision_engine_sample.db"),
        )
    )


settings = Settings()
