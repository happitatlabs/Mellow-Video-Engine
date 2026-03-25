from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from mellow_link.infra import get_current_user, get_db, User
from mellow_link.infra.run_events import create_run
from mellow_link.routers.runs import _resolve_run_session_id

from .runner import start_research_run
from .schemas import ResearchAssistantStartRequest, ResearchAssistantStartResponse

router = APIRouter(prefix="/modules/research_assistant", tags=["Modules"])
_STATIC_DIR = Path(__file__).resolve().parent / "static"


@router.get("", include_in_schema=False)
def research_assistant_ui() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


@router.post("/runs", response_model=ResearchAssistantStartResponse)
def start_research_assistant(
    payload: ResearchAssistantStartRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ResearchAssistantStartResponse:
    import logging
    from mellow_link import app_state

    logger = logging.getLogger(__name__)
    session_id = _resolve_run_session_id(db, user, None)
    temp_chars = len(str(app_state.TEMP_CONTEXT_STORE.get(payload.temp_session_id, "") or "")) if payload.temp_session_id else 0
    logger.info(
        "[ResearchAssistant] start request temp_session_id=%s temp_chars=%s question_chars=%s context_note_chars=%s",
        payload.temp_session_id,
        temp_chars,
        len(payload.question or ""),
        len(payload.context_note or ""),
    )
    run_id = create_run(session_id=session_id, db=db, module_id="research_assistant", run_kind="research_run")
    start_research_run(
        run_id=run_id,
        session_id=session_id,
        question=payload.question,
        context_note=payload.context_note,
        temp_session_id=payload.temp_session_id,
    )
    return ResearchAssistantStartResponse(run_id=run_id, session_id=session_id)
