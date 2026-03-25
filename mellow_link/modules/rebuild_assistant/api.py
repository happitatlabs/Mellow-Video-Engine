from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from mellow_link.infra import User, get_current_user, get_db
from mellow_link.infra.run_events import create_run
from mellow_link.routers.runs import _resolve_run_session_id

from .runner import start_rebuild_assistant_run
from .schemas import RebuildAssistantStartRequest, RebuildAssistantStartResponse

router = APIRouter(prefix="/modules/rebuild_assistant", tags=["Modules"])
_STATIC_DIR = Path(__file__).resolve().parent / "static"


@router.get("", include_in_schema=False)
def rebuild_assistant_ui() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


@router.post("/runs", response_model=RebuildAssistantStartResponse)
def start_rebuild_assistant(
    payload: RebuildAssistantStartRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RebuildAssistantStartResponse:
    session_id = _resolve_run_session_id(db, user, None)
    run_id = create_run(session_id=session_id, db=db, module_id="rebuild_assistant", run_kind="rebuild_plan")
    start_rebuild_assistant_run(
        run_id=run_id,
        session_id=session_id,
        goal=payload.goal,
        assets=payload.assets,
        constraints=payload.constraints,
        temp_session_id=payload.temp_session_id,
    )
    return RebuildAssistantStartResponse(run_id=run_id, session_id=session_id)
