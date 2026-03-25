from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from mellow_link.infra import get_current_user, get_db, User
from mellow_link.infra.run_events import create_run
from mellow_link.routers.runs import _resolve_run_session_id

from .runner import start_ai_workflow_run
from .schemas import AIWorkflowStartRequest, AIWorkflowStartResponse

router = APIRouter(prefix="/modules/ai_workflow_console", tags=["Modules"])
_STATIC_DIR = Path(__file__).resolve().parent / "static"


@router.get("", include_in_schema=False)
def ai_workflow_console_ui() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


@router.post("/runs", response_model=AIWorkflowStartResponse)
def start_ai_workflow_console(
    payload: AIWorkflowStartRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AIWorkflowStartResponse:
    session_id = _resolve_run_session_id(db, user, None)
    run_id = create_run(session_id=session_id, db=db, module_id="ai_workflow_console", run_kind="workflow_run")
    start_ai_workflow_run(run_id=run_id, session_id=session_id, task_type=payload.task_type, prompt=payload.prompt)
    return AIWorkflowStartResponse(run_id=run_id, session_id=session_id)
