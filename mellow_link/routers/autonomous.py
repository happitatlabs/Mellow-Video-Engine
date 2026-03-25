"""
Mellow-Link - Autonomous Work Router (Admin-Only)

Endpoints: /autonomous/* (자율 작업 승인/거부)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mellow_link import app_state
from mellow_link.dependencies import get_admin_user_required

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/autonomous",
    tags=["Autonomous"],
    dependencies=[Depends(get_admin_user_required)]
)


class AutonomousActionRequest(BaseModel):
    """자율 작업 승인/거부 요청."""
    record_id: str = Field(..., description="작업 레코드 ID")


@router.post("/run-tick")
async def autonomous_run_tick():
    """자율 틱 1회 즉시 실행 (테스트/검증용, 어드민 전용)."""
    from mellow_link.core.autonomous_agent import run_autonomous_tick
    try:
        record = await run_autonomous_tick(shutdown_event=app_state.shutdown_event)
        if record is None:
            return {"success": True, "message": "스킵됨 (plan=skip 또는 종료 중)", "record_id": None}
        return {
            "success": True,
            "message": f"틱 완료: {record.status}",
            "record_id": record.id,
            "status": record.status,
        }
    except Exception as e:
        logger.exception("[Autonomous] run-tick failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/report")
async def autonomous_report():
    """자율 작업 요약 보고서 (어드민 전용)."""
    from mellow_link.infra.memory_database import get_memory_db
    db = get_memory_db()
    waiting = db.get_autonomous_work_results_by_status("WAITING_FOR_APPROVAL", limit=20)
    all_recent = db.get_autonomous_work_results_by_status(None, limit=50)
    return {
        "waiting_for_approval": [
            {
                "id": r.id,
                "task_type": r.task_type,
                "tools_created": r.tools_created,
                "info_collected": (r.info_collected or "")[:1000],
                "ethics_review": r.ethics_review,
                "ethics_approved": bool(r.ethics_approved),
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in waiting
        ],
        "recent": [
            {
                "id": r.id,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "tools_created": (r.tools_created or "")[:500],
                "info_collected": (r.info_collected or "")[:500],
                "ethics_review": (r.ethics_review or "")[:1000],
                "output": (r.output or "")[:2000] if hasattr(r, "output") else None,
            }
            for r in all_recent[:10]
        ],
    }


@router.post("/approve")
async def autonomous_approve(req: AutonomousActionRequest):
    """자율 작업 승인 후 즉시 실행 (어드민 전용)."""
    from mellow_link.infra.memory_database import get_memory_db
    from mellow_link.core.autonomous_agent import execute_approved_work

    db = get_memory_db()
    results = db.get_autonomous_work_results_by_status("WAITING_FOR_APPROVAL")
    if not any(r.id == req.record_id for r in results):
        raise HTTPException(status_code=404, detail="해당 승인 대기 항목을 찾을 수 없습니다.")

    success, message = await execute_approved_work(req.record_id)
    return {"success": True, "message": "승인 및 실행 완료" if success else f"실행 완료 (일부 실패): {message}"}


@router.post("/reject")
async def autonomous_reject(req: AutonomousActionRequest):
    """자율 작업 거부 (어드민 전용)."""
    from mellow_link.infra.memory_database import get_memory_db
    db = get_memory_db()
    db.update_autonomous_work_status(req.record_id, "REJECTED")
    return {"success": True, "message": "거부 완료"}


@router.post("/reject-all")
async def autonomous_reject_all():
    """대기 중인 자율 작업 전체 거부 (어드민 전용)."""
    from mellow_link.infra.memory_database import get_memory_db
    db = get_memory_db()
    waiting = db.get_autonomous_work_results_by_status("WAITING_FOR_APPROVAL")
    quarantined = db.get_autonomous_work_results_by_status("QUARANTINED")
    count = 0
    for r in waiting + quarantined:
        db.update_autonomous_work_status(r.id, "REJECTED")
        count += 1
    logger.info(f"[Autonomous] Bulk rejected {count} items")
    return {"success": True, "rejected_count": count, "message": f"{count}건 일괄 거부 완료"}
