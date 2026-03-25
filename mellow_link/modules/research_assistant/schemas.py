from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchAssistantStartRequest(BaseModel):
    question: str = Field(..., min_length=3)
    context_note: str = Field(default="")
    temp_session_id: str | None = None


class ResearchAssistantStartResponse(BaseModel):
    run_id: str
    session_id: str
    module_id: str = "research_assistant"
    run_kind: str = "research_run"
