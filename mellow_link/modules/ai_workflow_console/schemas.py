from __future__ import annotations

from pydantic import BaseModel, Field


class AIWorkflowStartRequest(BaseModel):
    task_type: str = Field(..., description="image | video | generation")
    prompt: str = Field(..., min_length=3)


class AIWorkflowStartResponse(BaseModel):
    run_id: str
    session_id: str
    module_id: str = "ai_workflow_console"
    run_kind: str = "workflow_run"
