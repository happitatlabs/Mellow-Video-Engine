from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from mellow_link.infra import get_current_user, get_db, User
from mellow_link.routers.runs import _resolve_run_session_id
from mellow_link.infra.run_events import create_run

from .runner import start_sql_analytics_run
from .schemas import SQLAnalyticsStartRequest, SQLAnalyticsStartResponse

router = APIRouter(prefix="/modules/sql_analytics", tags=["Modules"])
_STATIC_DIR = Path(__file__).resolve().parent / "static"


@router.get("", include_in_schema=False)
def sql_analytics_ui() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


@router.post("/runs", response_model=SQLAnalyticsStartResponse)
def start_sql_analytics(
    payload: SQLAnalyticsStartRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SQLAnalyticsStartResponse:
    session_id = _resolve_run_session_id(db, user, None)
    run_id = create_run(session_id=session_id, db=db, module_id="sql_analytics", run_kind="sql_analysis")
    start_sql_analytics_run(run_id=run_id, session_id=session_id, question=payload.question, input_type=payload.input_type)
    return SQLAnalyticsStartResponse(run_id=run_id, session_id=session_id)
