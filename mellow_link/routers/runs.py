"""
Agent Run Progress API - SSE streaming for agent execution progress

Endpoints:
  - POST /runs (creates run_id)
  - POST /runs/{run_id}/start (starts execution)
  - GET /runs/{run_id} (snapshot)
  - GET /runs/{run_id}/events (SSE stream)
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mellow_link.dependencies import get_admin_user_required
from mellow_link.infra import (
    get_db,
    SessionLocal,
    get_current_user,
    get_current_user_optional,
    ensure_user_has_folders,
    get_or_create_default_session,
)
from mellow_link.infra.database import AgentRun, ChatSession, User
from mellow_link.infra.run_events import (
    create_run,
    emit_event,
    get_run_events,
    get_run_snapshot,
    get_decision_from_events,
    compute_run_health,
    EVENT_TYPE_RUN_STARTED,
    EVENT_TYPE_RUN_FINISHED,
)
from mellow_link.infra.run_approval import resolve_approval

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Runs"])

# run_id별 실행 제어 상태 (pause/abort) 공유 저장소
RUN_CONTROL_STATE: Dict[str, Dict[str, Any]] = {}


# =============================================================================
# Request Models
# =============================================================================

class StartRunRequest(BaseModel):
    """Start run request."""
    user_input: str = Field(..., description="User input/request")
    session_id: Optional[str] = Field(None, description="Session ID")
    mode: str = Field("fast", description="Processing mode")
    plan_approved: bool = Field(False, description="If True, run with plan_approved so agent executes (e.g. after plan card Execute)")


class RunControlRequest(BaseModel):
    """Operator control request."""
    action: str = Field(..., description="Control action: pause | retry | abort | force_finish")


class RunModeRequest(BaseModel):
    """Force mode switch (e.g. fast for /force_fast)."""
    mode: str = Field(..., description="Target mode: fast | thinking | ...")


class ApproveRunRequest(BaseModel):
    """Operator 승인 (NEED_AI_REVIEW 해제 후 run 재개)."""
    todo_id: Optional[str] = Field(None, description="대기 중인 todo_id (선택, 현재는 1건만 대기)")


class RejectRunRequest(BaseModel):
    """Operator 거부 (run 종료)."""
    reason: str = Field("", description="거부 사유")


def _build_default_todos() -> list:
    """MVP 기본 TODO 목록."""
    return [
        {"todo_id": "T1", "title": "요청 파싱", "status": "pending"},
        {"todo_id": "T2", "title": "모드 선택", "status": "pending"},
        {"todo_id": "T3", "title": "도구 실행 (필요시)", "status": "pending"},
        {"todo_id": "T4", "title": "결과 요약", "status": "pending"},
        {"todo_id": "T5", "title": "완료", "status": "pending"},
    ]


def _user_session_id_set(db: Session, user_id: int) -> set:
    """현재 사용자 소유의 채팅 세션 ID 집합 (문자열, run.session_id와 매칭)."""
    rows = db.query(ChatSession.id).filter(ChatSession.user_id == user_id).all()
    return {str(r[0]) for r in rows}


def _resolve_run_session_id(db: Session, user: User, session_id: Optional[str]) -> str:
    """Ensure every run is attached to a user-owned chat session."""
    if session_id and str(session_id).strip():
        sid = str(session_id).strip()
        if sid not in _user_session_id_set(db, user.id):
            raise HTTPException(status_code=403, detail="해당 세션에 대한 권한이 없습니다.")
        return sid

    folders = ensure_user_has_folders(db, user.id, getattr(user, "role", "user"))
    if not folders:
        raise HTTPException(status_code=500, detail="기본 폴더를 생성할 수 없습니다.")
    session = get_or_create_default_session(db, user.id, folders[0].id)
    return str(session.id)


def _calc_duration_ms(run: AgentRun, events: List[Dict[str, Any]]) -> Optional[int]:
    if events:
        start_ts = events[0].get("ts")
        end_ts = events[-1].get("ts")
        if start_ts is not None and end_ts is not None and end_ts >= start_ts:
            return int(round((end_ts - start_ts) * 1000))
    if run and run.created_at and run.updated_at:
        delta = (run.updated_at - run.created_at).total_seconds()
        if delta >= 0:
            return int(round(delta * 1000))
    return None


def _summarize_run_row(run: AgentRun, db: Session) -> Dict[str, Any]:
    events = get_run_events(run.run_id, db=db)
    decision = get_decision_from_events(events)
    snapshot = get_run_snapshot(run.run_id, db=db, paused=RUN_CONTROL_STATE.get(run.run_id, {}).get("paused")) or {}
    block = snapshot.get("block") or {}
    duration_ms = _calc_duration_ms(run, events)
    module_id = getattr(run, "module_id", "engine") or "engine"
    run_kind = getattr(run, "run_kind", "generic") or "generic"
    return {
        "run_id": run.run_id,
        "session_id": run.session_id,
        "module_id": module_id,
        "run_kind": run_kind,
        "status": run.status,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "summary": (run.summary or "")[:200],
        "processing_time": duration_ms,
        "selected_mode": decision.get("selected_mode") or decision.get("initial_mode") or "",
        "escalated": bool(decision.get("escalated")),
        "needs_approval": bool(snapshot.get("needs_approval")),
        "block_reason": block.get("reason_code"),
    }


def _wants_html(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept and "application/json" not in accept


def _load_static_html(name: str) -> str:
    from mellow_link import app_state

    path = os.path.join(app_state.static_dir or ".", name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"{name} not found")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _resolve_user_for_run_request(
    request: Request,
    db: Session,
    access_token: Optional[str] = None,
) -> User:
    """Allow normal fetch auth headers and EventSource query-token auth for user-owned runs."""
    token = None
    auth_header = request.headers.get("authorization")
    if auth_header:
        token = auth_header.replace("Bearer ", "").strip()
    elif access_token:
        token = access_token.strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if token.startswith("guest_"):
        raise HTTPException(status_code=401, detail="Guest token is not supported for runs")
    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


def _run_owned_by_user(run: AgentRun, user_id: int, db: Session) -> bool:
    """Run 소유권: run.session_id가 해당 user의 세션이어야 함. orphan run(session_id 없음)은 현재 숨김 정책."""
    if run.session_id is None or (isinstance(run.session_id, str) and run.session_id.strip() == ""):
        return False
    session_ids = _user_session_id_set(db, user_id)
    return run.session_id.strip() in session_ids


def _get_run_or_404(run_id: str, db: Session):
    run = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def _extract_start_context(events: list) -> Dict[str, Any]:
    """
    기존 run 이벤트에서 재시도용 시작 컨텍스트를 복원.

    Returns:
        {"user_input": str, "mode": str, "session_id": Optional[str]}
    """
    for ev in reversed(events):
        if ev.get("type") == EVENT_TYPE_RUN_STARTED:
            p = ev.get("payload") or {}
            return {
                "user_input": (p.get("user_input") or "").strip(),
                "mode": (p.get("mode") or "fast").strip() or "fast",
                "session_id": p.get("session_id"),
            }
    return {"user_input": "", "mode": "fast", "session_id": None}


# =============================================================================
# Control Endpoints
# =============================================================================

@router.post("/runs")
async def create_run_endpoint(
    session_id: Optional[str] = Query(None),
    module_id: str = Query("engine"),
    run_kind: str = Query("generic"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    새로운 실행을 생성하고 run_id를 반환. 로그인 필수. session_id는 본인 세션만 허용.
    """
    effective_session_id = _resolve_run_session_id(db, user, session_id)
    try:
        run_id = create_run(session_id=effective_session_id, db=db, module_id=module_id, run_kind=run_kind)
        logger.info(f"[Runs] Created run: {run_id} by user_id={user.id}")
        return {"run_id": run_id, "status": "pending", "session_id": effective_session_id, "module_id": module_id, "run_kind": run_kind}
    except Exception as e:
        logger.error(f"[Runs] Failed to create run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def _execute_run_background(run_id: str, user_input: str, mode: str, session_id: Optional[str] = None):
    """
    백그라운드에서 실제 AgentBrain 실행을 수행.
    """
    try:
        from mellow_link import app_state

        control_state = RUN_CONTROL_STATE.setdefault(
            run_id,
            {"paused": False, "abort_requested": False, "running": True},
        )

        if not app_state.orchestrator:
            emit_event(run_id, "error", {"message": "Orchestrator not initialized"})
            return

        if control_state.get("abort_requested"):
            emit_event(
                run_id,
                EVENT_TYPE_RUN_FINISHED,
                {
                    "success": False,
                    "finish_reason": "operator_abort",
                    "summary": "Run aborted by operator before execution",
                },
            )
            return

        # AgentBrain 실행 (plan_approved 시 계획 승인 후 실행 모드)
        context_messages = []
        session_state = {
            "run_id": run_id,
            "run_control": control_state,
            "plan_approved": control_state.get("plan_approved", False),
        }
        agent_result = await app_state.orchestrator.run_agent(
            user_input,
            history=context_messages,
            is_admin=False,
            mode=mode,
            session_id=session_id,
            session_state=session_state
        )

        # 실행 완료 이벤트는 AgentBrain에서 자동 발행됨
        logger.info(f"[Runs] Background execution completed for run {run_id}")
    except Exception as e:
        logger.error(f"[Runs] Background execution failed for run {run_id}: {e}", exc_info=True)
        emit_event(run_id, "error", {"message": str(e)[:500]})
    finally:
        state = RUN_CONTROL_STATE.get(run_id)
        if state is not None:
            state["running"] = False


@router.post("/runs/{run_id}/start")
async def start_run_endpoint(
    run_id: str,
    request: StartRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    실행을 시작하고 백그라운드 작업을 시작. 로그인 필수, run 소유권 검증.
    """
    from mellow_link import app_state
    
    run = _get_run_or_404(run_id, db)
    if not _run_owned_by_user(run, user.id, db):
        raise HTTPException(status_code=403, detail="해당 run에 대한 권한이 없습니다.")
    snapshot = get_run_snapshot(run_id, db=db, paused=RUN_CONTROL_STATE.get(run_id, {}).get("paused"))
    if not snapshot:
        raise HTTPException(status_code=404, detail="Run not found")
    
    if snapshot["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Run already started (status: {snapshot['status']})")
    
    # run_started 이벤트 발행
    emit_event(
        run_id=run_id,
        event_type=EVENT_TYPE_RUN_STARTED,
        payload={
            "user_input": request.user_input[:200],  # 처음 200자만
            "mode": request.mode,
            "session_id": request.session_id,
        },
        db=db
    )

    # 기본 plan 생성 (MVP: deterministic)
    todos = _build_default_todos()

    from mellow_link.infra.run_events import EVENT_TYPE_PLAN_CREATED
    emit_event(
        run_id=run_id,
        event_type=EVENT_TYPE_PLAN_CREATED,
        payload={"todos": todos},
        db=db
    )

    RUN_CONTROL_STATE[run_id] = {
        "paused": False,
        "abort_requested": False,
        "running": True,
        "plan_approved": getattr(request, "plan_approved", False),
    }

    # 별도 스레드에서 실행 (Guardian NEED_AI_REVIEW 시 ev.wait()가 메인 이벤트 루프를 블로킹하지 않도록)
    import threading
    def _run_in_thread():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_execute_run_background(
                run_id=run_id,
                user_input=request.user_input,
                mode=request.mode,
                session_id=request.session_id,
            ))
        finally:
            loop.close()
    threading.Thread(target=_run_in_thread, daemon=True).start()
    logger.info(f"[Runs] Starting run {run_id} with input: {request.user_input[:100]}...")
    
    return {
        "run_id": run_id,
        "status": "running",
        "message": "Run started. Connect to /runs/{run_id}/events for progress."
    }


@router.get("/runs")
async def list_runs_endpoint(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    session_id: Optional[str] = Query(None, description="Filter by session_id (chat session)"),
    status: Optional[str] = Query(None, description="Comma-separated status filter"),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
) -> Any:
    """
    Run 목록 조회 (최신순). 로그인 필수, 본인 세션에 연결된 run만 조회.
    session_id 없는 과거 orphan run은 ownership 불명으로 간주하고 현재는 숨긴다.
    """
    if _wants_html(request):
        return HTMLResponse(content=_load_static_html("runs.html"))

    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    my_sids = _user_session_id_set(db, user.id)
    query = db.query(AgentRun).filter(AgentRun.session_id.in_(my_sids))
    if session_id is not None and str(session_id).strip():
        sid = str(session_id).strip()
        if sid not in my_sids:
            raise HTTPException(status_code=403, detail="해당 세션에 대한 권한이 없습니다.")
        query = query.filter(AgentRun.session_id == sid)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            query = query.filter(AgentRun.status.in_(statuses))
    runs = query.order_by(AgentRun.created_at.desc()).limit(limit).all()
    items = [_summarize_run_row(r, db) for r in runs]
    return {"runs": items, "count": len(items)}


@router.get("/runs/{run_id}")
async def get_run_endpoint(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """실행 스냅샷 조회. 로그인 필수, run 소유권 검증."""
    run = _get_run_or_404(run_id, db)
    if not _run_owned_by_user(run, user.id, db):
        raise HTTPException(status_code=403, detail="해당 run에 대한 권한이 없습니다.")
    snapshot = get_run_snapshot(run_id, db=db, paused=RUN_CONTROL_STATE.get(run_id, {}).get("paused"))
    if not snapshot:
        raise HTTPException(status_code=404, detail="Run not found")
    return snapshot


@router.get("/runs/{run_id}/dev")
async def get_run_dev_endpoint(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Dev Console용 확장 조회. 로그인 필수, run 소유권 검증."""
    run = _get_run_or_404(run_id, db)
    if not _run_owned_by_user(run, user.id, db):
        raise HTTPException(status_code=403, detail="해당 run에 대한 권한이 없습니다.")
    snapshot = get_run_snapshot(run_id, db=db, paused=RUN_CONTROL_STATE.get(run_id, {}).get("paused"))
    if not snapshot:
        raise HTTPException(status_code=404, detail="Run not found")
    events = get_run_events(run_id, db=db)
    decision = get_decision_from_events(events)
    health = compute_run_health(events)
    run = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
    return {
        **snapshot,
        "events": events,
        "decision": decision,
        "health": health,
    }


@router.get("/api/dev/runs")
async def dev_list_runs_endpoint(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user_required),
) -> Dict[str, Any]:
    runs = db.query(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit).all()
    return {"runs": [_summarize_run_row(r, db) for r in runs], "count": len(runs)}


@router.get("/api/dev/runs/{run_id}")
async def dev_get_run_endpoint(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user_required),
) -> Dict[str, Any]:
    snapshot = await get_run_dev_endpoint(run_id=run_id, db=db, user=user)
    return snapshot


@router.get("/api/dev/runs/{run_id}/events")
async def dev_get_run_events_endpoint(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user_required),
) -> Dict[str, Any]:
    _get_run_or_404(run_id, db)
    return {"run_id": run_id, "events": get_run_events(run_id, db=db)}


@router.get("/api/dev/runs/{run_id}/raw")
async def dev_get_run_raw_endpoint(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user_required),
) -> Dict[str, Any]:
    run = _get_run_or_404(run_id, db)
    events = get_run_events(run_id, db=db)
    snapshot = get_run_snapshot(run_id, db=db, paused=RUN_CONTROL_STATE.get(run_id, {}).get("paused"))
    return {
        "run": {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "status": run.status,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
            "summary": run.summary,
        },
        "snapshot": snapshot,
        "events": events,
    }


@router.get("/api/dev/metrics")
async def dev_metrics_endpoint(
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user_required),
) -> Dict[str, Any]:
    runs = db.query(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit).all()
    items = [_summarize_run_row(r, db) for r in runs]
    durations = sorted([i["processing_time"] for i in items if isinstance(i.get("processing_time"), int)])
    failures = sum(1 for i in items if i.get("status") == "failed")
    escalations = sum(1 for i in items if i.get("escalated"))
    mode_counts: Dict[str, int] = {}
    for item in items:
        mode = (item.get("selected_mode") or "unknown").strip() or "unknown"
        mode_counts[mode] = mode_counts.get(mode, 0) + 1

    def percentile(values: List[int], ratio: float) -> Optional[int]:
        if not values:
            return None
        idx = min(int(len(values) * ratio), len(values) - 1)
        return values[idx]

    avg = int(round(sum(durations) / len(durations))) if durations else None
    return {
        "sample_size": len(items),
        "latency_ms": {
            "avg": avg,
            "p50": percentile(durations, 0.50),
            "p95": percentile(durations, 0.95),
        },
        "failure_rate": (failures / len(items)) if items else 0.0,
        "escalation_rate": (escalations / len(items)) if items else 0.0,
        "mode_distribution": mode_counts,
    }


@router.post("/runs/{run_id}/control")
async def control_run_endpoint(
    run_id: str,
    request: RunControlRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user_required),
) -> Dict[str, Any]:
    """
    운영자 제어 액션 (Admin 전용). pause / retry / abort / force_finish.
    """
    snapshot = get_run_snapshot(run_id, db=db, paused=RUN_CONTROL_STATE.get(run_id, {}).get("paused"))
    if not snapshot:
        raise HTTPException(status_code=404, detail="Run not found")

    action = (request.action or "").strip().lower()
    if action not in {"pause", "retry", "abort", "force_finish"}:
        raise HTTPException(status_code=400, detail="Invalid action (allowed: pause, retry, abort, force_finish)")

    status_now = snapshot.get("status", "pending")
    state = RUN_CONTROL_STATE.setdefault(
        run_id,
        {"paused": False, "abort_requested": False, "running": status_now == "running"},
    )
    if status_now in ("completed", "failed"):
        state["running"] = False

    if action == "pause":
        if not state.get("running", False):
            raise HTTPException(status_code=409, detail="Run is not running")
        state["paused"] = not bool(state.get("paused", False))
        emit_event(
            run_id=run_id,
            event_type="operator_action",
            payload={
                "action": "pause" if state["paused"] else "resume",
                "paused": state["paused"],
                "source": "operator_console",
            },
            db=db,
        )
        return {
            "run_id": run_id,
            "accepted": True,
            "action": "pause" if state["paused"] else "resume",
            "paused": state["paused"],
            "status": status_now,
        }

    if action == "abort":
        if status_now in ("completed", "failed"):
            return {
                "run_id": run_id,
                "accepted": False,
                "action": "abort",
                "status": status_now,
                "message": "Run already finished",
            }
        state["abort_requested"] = True
        state["paused"] = False
        emit_event(
            run_id=run_id,
            event_type="operator_action",
            payload={"action": "abort", "source": "operator_console"},
            db=db,
        )
        return {
            "run_id": run_id,
            "accepted": True,
            "action": "abort",
            "status": "stopping",
        }

    if action == "force_finish":
        if status_now in ("completed", "failed"):
            return {
                "run_id": run_id,
                "accepted": False,
                "action": "force_finish",
                "status": status_now,
                "message": "Run already finished",
            }
        run_row = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
        if not run_row:
            raise HTTPException(status_code=404, detail="Run not found")
        run_row.status = "failed"
        run_row.updated_at = datetime.utcnow()
        db.commit()
        emit_event(
            run_id=run_id,
            event_type=EVENT_TYPE_RUN_FINISHED,
            payload={
                "success": False,
                "summary": "Force-closed by operator (run was stuck or abandoned)",
            },
            db=db,
        )
        state["running"] = False
        state["abort_requested"] = False
        state["paused"] = False
        logger.info("[Runs] Run %s force_finish: status set to failed", run_id)
        return {
            "run_id": run_id,
            "accepted": True,
            "action": "force_finish",
            "status": "failed",
        }

    # retry
    if status_now not in ("completed", "failed"):
        raise HTTPException(status_code=409, detail="Retry is allowed only for completed/failed runs")

    events = get_run_events(run_id, db=db)
    start_ctx = _extract_start_context(events)
    user_input = (start_ctx.get("user_input") or "").strip()
    mode = (start_ctx.get("mode") or "fast").strip() or "fast"
    session_id = start_ctx.get("session_id") or snapshot.get("session_id")
    if not user_input:
        raise HTTPException(status_code=400, detail="Retry unavailable: original user_input not found")

    new_run_id = create_run(session_id=session_id, db=db)
    emit_event(
        run_id=new_run_id,
        event_type=EVENT_TYPE_RUN_STARTED,
        payload={
            "user_input": user_input[:200],
            "mode": mode,
            "session_id": session_id,
            "retry_of": run_id,
        },
        db=db,
    )
    from mellow_link.infra.run_events import EVENT_TYPE_PLAN_CREATED
    emit_event(
        run_id=new_run_id,
        event_type=EVENT_TYPE_PLAN_CREATED,
        payload={"todos": _build_default_todos()},
        db=db,
    )

    RUN_CONTROL_STATE[new_run_id] = {"paused": False, "abort_requested": False, "running": True}
    background_tasks.add_task(
        _execute_run_background,
        run_id=new_run_id,
        user_input=user_input,
        mode=mode,
        session_id=session_id,
    )

    emit_event(
        run_id=run_id,
        event_type="operator_action",
        payload={
            "action": "retry",
            "source": "operator_console",
            "new_run_id": new_run_id,
        },
        db=db,
    )

    return {
        "run_id": run_id,
        "accepted": True,
        "action": "retry",
        "new_run_id": new_run_id,
        "status": "running",
    }


@router.post("/runs/{run_id}/approve")
async def approve_run_endpoint(
    run_id: str,
    request: ApproveRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Operator 승인: NEED_AI_REVIEW 대기 해제 후 run 재개.
    대기 중인 run에 대해 호출하면 해당 run의 Guardian 보류가 승인되어 실행이 이어짐.
    """
    run = _get_run_or_404(run_id, db)
    if not _run_owned_by_user(run, user.id, db):
        raise HTTPException(status_code=403, detail="해당 run에 대한 권한이 없습니다.")
    if resolve_approval(run_id, approved=True):
        emit_event(
            run_id=run_id,
            event_type="approval_resolved",
            payload={"approved": True, "operator_approved": True, "source": "operator"},
            db=db,
        )
        logger.info("[Runs] Run %s operator approved", run_id)
        return {"run_id": run_id, "accepted": True, "approved": True, "message": "승인되었습니다. 실행이 재개됩니다."}
    snapshot = get_run_snapshot(run_id, db=db, paused=RUN_CONTROL_STATE.get(run_id, {}).get("paused"))
    if snapshot and snapshot.get("needs_approval"):
        return {"run_id": run_id, "accepted": False, "approved": False, "message": "이미 처리되었거나 대기 중인 승인이 없습니다."}
    return {"run_id": run_id, "accepted": False, "approved": False, "message": "대기 중인 승인 요청이 없습니다."}


@router.post("/runs/{run_id}/reject")
async def reject_run_endpoint(
    run_id: str,
    request: RejectRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Operator 거부: run 종료 (failed). 대기 중인 Guardian 보류를 거부함.
    """
    run = _get_run_or_404(run_id, db)
    if not _run_owned_by_user(run, user.id, db):
        raise HTTPException(status_code=403, detail="해당 run에 대한 권한이 없습니다.")
    if resolve_approval(run_id, approved=False, reason=request.reason or "Operator 거부"):
        run_row = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
        if run_row and run_row.status not in ("completed", "failed"):
            run_row.status = "failed"
            run_row.updated_at = datetime.utcnow()
            db.commit()
        emit_event(
            run_id=run_id,
            event_type=EVENT_TYPE_RUN_FINISHED,
            payload={
                "success": False,
                "finish_reason": "operator_reject",
                "summary": (request.reason or "Operator 거부").strip() or "Operator 거부",
            },
            db=db,
        )
        emit_event(
            run_id=run_id,
            event_type="approval_resolved",
            payload={"approved": False, "reason": request.reason or "Operator 거부", "source": "operator"},
            db=db,
        )
        state = RUN_CONTROL_STATE.get(run_id)
        if state is not None:
            state["running"] = False
        logger.info("[Runs] Run %s operator rejected: %s", run_id, (request.reason or "")[:100])
        return {"run_id": run_id, "accepted": True, "approved": False, "status": "failed", "message": "거부되었습니다. Run이 종료됩니다."}
    return {"run_id": run_id, "accepted": False, "message": "대기 중인 승인 요청이 없습니다."}


@router.post("/runs/{run_id}/mode")
async def set_run_mode_endpoint(
    run_id: str,
    request: RunModeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user_required),
) -> Dict[str, Any]:
    """모드 강제 전환 (Admin 전용). Dev Console /force_fast 등."""
    snapshot = get_run_snapshot(run_id, db=db, paused=RUN_CONTROL_STATE.get(run_id, {}).get("paused"))
    if not snapshot:
        raise HTTPException(status_code=404, detail="Run not found")
    mode = (request.mode or "").strip().lower() or "fast"
    state = RUN_CONTROL_STATE.setdefault(
        run_id,
        {"paused": False, "abort_requested": False, "running": snapshot.get("status") == "running"},
    )
    state["force_mode"] = mode
    emit_event(
        run_id=run_id,
        event_type="operator_action",
        payload={"action": "force_mode", "mode": mode, "source": "dev_console"},
        db=db,
    )
    return {"run_id": run_id, "accepted": True, "force_mode": mode}


@router.post("/runs/{run_id}/propose-tool")
async def propose_tool_endpoint(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user_required),
) -> Dict[str, Any]:
    """도구 생성 요청 (Admin 전용). Dev Console /create_tool."""
    snapshot = get_run_snapshot(run_id, db=db, paused=RUN_CONTROL_STATE.get(run_id, {}).get("paused"))
    if not snapshot:
        raise HTTPException(status_code=404, detail="Run not found")
    state = RUN_CONTROL_STATE.setdefault(
        run_id,
        {"paused": False, "abort_requested": False, "running": snapshot.get("status") == "running"},
    )
    state["propose_tool_requested"] = True
    emit_event(
        run_id=run_id,
        event_type="operator_action",
        payload={"action": "propose_tool", "source": "dev_console"},
        db=db,
    )
    return {"run_id": run_id, "accepted": True, "message": "Propose-tool requested. Agent may invoke propose_new_tool on next turn."}


# =============================================================================
# SSE Endpoint
# =============================================================================

@router.get("/runs/{run_id}/events")
async def stream_run_events(
    request: Request,
    run_id: str,
    last_event_id: Optional[int] = Query(None, description="Last event ID (cursor)"),
    last_ts: Optional[float] = Query(None, description="Last timestamp (cursor)"),
    format: Optional[str] = Query(None, description="json | sse"),
    access_token: Optional[str] = Query(None, description="Bearer token for EventSource access"),
):
    """
    SSE 스트림으로 실행 이벤트를 실시간 전송. 로그인 필수, run 소유권 검증.
    """
    with SessionLocal() as initial_db:
        user = _resolve_user_for_run_request(request, initial_db, access_token=access_token)
        run = _get_run_or_404(run_id, initial_db)
        if not _run_owned_by_user(run, user.id, initial_db):
            raise HTTPException(status_code=403, detail="해당 run에 대한 권한이 없습니다.")
        snapshot = get_run_snapshot(run_id, db=initial_db, paused=RUN_CONTROL_STATE.get(run_id, {}).get("paused"))
        if not snapshot:
            raise HTTPException(status_code=404, detail="Run not found")
        if (format or "").lower() == "json" or "application/json" in (request.headers.get("accept") or "").lower():
            return {
                "run_id": run_id,
                "events": get_run_events(run_id=run_id, since_event_id=last_event_id, since_ts=last_ts, db=initial_db),
            }
        
        # last_event_id 초기화 (명시적 None 체크)
        initial_last_event_id = None
        if last_event_id is not None:
            initial_last_event_id = last_event_id
        elif snapshot.get("last_event_id") is not None:
            initial_last_event_id = snapshot["last_event_id"]
    
    async def event_generator():
        """SSE 이벤트 생성기."""
        try:
            # 기존 이벤트 재생
            if initial_last_event_id is not None or last_ts:
                with SessionLocal() as replay_db:
                    events = get_run_events(
                        run_id=run_id,
                        since_event_id=initial_last_event_id,
                        since_ts=last_ts,
                        db=replay_db
                    )
                    for event in events:
                        yield f"id: {event['id']}\n"
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            
            # 새로운 이벤트 폴링 (간단한 MVP: 1초마다 폴링)
            last_seen_id = initial_last_event_id or 0
            max_iterations = 3600  # 최대 1시간 (3600초)
            iteration = 0
            
            while iteration < max_iterations:
                # 매 폴링마다 새 DB 세션 사용
                with SessionLocal() as poll_db:
                    new_events = get_run_events(
                        run_id=run_id,
                        since_event_id=last_seen_id,
                        db=poll_db
                    )
                    
                    for event in new_events:
                        yield f"id: {event['id']}\n"
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        last_seen_id = event['id']
                        
                        # run_finished 이벤트면 종료
                        if event['type'] == EVENT_TYPE_RUN_FINISHED:
                            return
                    
                    # 실행 완료 확인
                    current_snapshot = get_run_snapshot(run_id, db=poll_db, paused=RUN_CONTROL_STATE.get(run_id, {}).get("paused"))
                    if current_snapshot and current_snapshot['status'] in ('completed', 'failed'):
                        # 마지막 이벤트 전송
                        final_events = get_run_events(run_id=run_id, since_event_id=last_seen_id, db=poll_db)
                        for event in final_events:
                            yield f"id: {event['id']}\n"
                            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        return
                
                # 하트비트 (연결 유지)
                yield f": heartbeat\n\n"
                
                await asyncio.sleep(1.0)  # 1초마다 폴링
                iteration += 1
            
            # 타임아웃
            yield f"data: {json.dumps({'type': 'timeout', 'message': 'Stream timeout'}, ensure_ascii=False)}\n\n"
            
        except Exception as e:
            logger.error(f"[Runs] SSE stream error: {e}", exc_info=True)
            error_event = {
                "type": "error",
                "payload": {"message": f"Stream error: {str(e)}"}
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
