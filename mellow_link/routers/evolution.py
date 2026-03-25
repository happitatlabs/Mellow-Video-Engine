"""
Mellow-Link - Evolution Router (Admin-Only)

Endpoints: /evolution/* (자가발전). 진입은 EvolutionFacade만 사용.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mellow_link.dependencies import get_admin_user_required

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/evolution",
    tags=["Evolution"],
    dependencies=[Depends(get_admin_user_required)]
)


# =============================================================================
# Request Models
# =============================================================================

class EvolutionCycleRequest(BaseModel):
    request: str = Field(..., description="자가발전 요청 내용")

class EvolutionProceedFromPlanRequest(BaseModel):
    proposal_id: str = Field(..., description="제안서 ID")

class EvolutionRefineRequest(BaseModel):
    proposal_id: str = Field(..., description="제안서 ID")

class EvolutionRejectRequest(BaseModel):
    proposal_id: str = Field(..., description="제안서 ID")

class EvolutionApplyRequest(BaseModel):
    proposal_id: str = Field(..., description="제안서 ID")


# =============================================================================
# Endpoints (EvolutionFacade 단일 진입)
# =============================================================================

@router.get("/status")
async def evolution_status():
    """Evolution API 상태 (어드민 전용)."""
    return {"status": "ok", "message": "Evolution API (Admin only)"}


@router.get("/waiting-for-approval")
async def evolution_waiting_for_approval():
    """승인 대기 중인 자가발전 제안 목록. EvolutionResponse 규격 (status, items, disabled_reason)."""
    from mellow_link.core.evolution_facade import EvolutionFacade
    resp = EvolutionFacade.list_waiting_for_approval()
    return resp.to_dict()


@router.get("/logs")
async def evolution_logs_list():
    """자가발전 로그 목록. EvolutionResponse 규격 (status, proposals, disabled_reason)."""
    from mellow_link.core.evolution_facade import EvolutionFacade
    resp = EvolutionFacade.get_all_proposals()
    return resp.to_dict()


@router.post("/cycle")
async def evolution_run_cycle(req: EvolutionCycleRequest):
    """자가발전 사이클 실행 (Tower → Verdict → Audit). 규격 응답: status SUCCESS|REJECTED|FAILED|DISABLED."""
    from mellow_link.core.evolution_facade import EvolutionFacade
    try:
        resp = await EvolutionFacade.run_cycle(req.request)
        return resp.to_dict()
    except Exception as e:
        logger.error(f"[Evolution] Cycle failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/proceed-from-plan")
async def evolution_proceed_from_plan(req: EvolutionProceedFromPlanRequest):
    """plan_pending 상태의 제안서에서 Verdict 단계로 진행."""
    from mellow_link.core.evolution_facade import EvolutionFacade
    try:
        resp = await EvolutionFacade.proceed_from_plan(req.proposal_id)
        if resp is None:
            raise HTTPException(status_code=404, detail="제안서를 찾을 수 없거나 계획 대기 상태가 아닙니다.")
        return resp.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Evolution] Proceed failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refine-from-proposal")
async def evolution_refine_from_proposal(req: EvolutionRefineRequest):
    """제안서 리파인 (audit 미승인 시 재시도)."""
    from mellow_link.core.evolution_facade import EvolutionFacade
    try:
        resp = await EvolutionFacade.refine_from_proposal(req.proposal_id)
        if resp is None:
            raise HTTPException(status_code=404, detail="제안서를 찾을 수 없거나 검수 승인 상태입니다.")
        return resp.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Evolution] Refine failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reject-proposal")
async def evolution_reject_proposal(req: EvolutionRejectRequest):
    """제안서 거부. EvolutionResponse 규격 반환."""
    from mellow_link.core.evolution_facade import EvolutionFacade
    resp = EvolutionFacade.reject_proposal(req.proposal_id)
    return resp.to_dict()


@router.post("/reject-all")
async def evolution_reject_all():
    """대기 중인 자가발전 제안 전체 거부."""
    from mellow_link.core.evolution_facade import EvolutionFacade
    resp = EvolutionFacade.list_waiting_for_approval()
    items = (resp.items or []) if resp.status == "SUCCESS" else []
    count = 0
    for p in items:
        pid = p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
        if pid:
            EvolutionFacade.reject_proposal(pid)
            count += 1
    logger.info(f"[Evolution] Bulk rejected {count} proposals")
    return {"success": True, "rejected_count": count, "message": f"{count}건 일괄 거부 완료", "status": resp.status}


@router.post("/apply-from-proposal")
async def evolution_apply_from_proposal(req: EvolutionApplyRequest):
    """승인된 제안서를 실제 코드에 적용. EvolutionResponse 규격 반환."""
    from mellow_link.core.evolution_facade import EvolutionFacade
    from mellow_link.services.notification_service import notify_evolution_applied
    resp = EvolutionFacade.apply_from_proposal(req.proposal_id)
    ok = resp.apply_ok if resp.apply_ok is not None else False
    msg = resp.apply_message or resp.error or ""
    if ok:
        try:
            from pathlib import Path
            import json
            base = Path(__file__).resolve().parent.parent
            ledger = base / "logs" / "evolution_proposals" / f"{req.proposal_id}.json"
            if ledger.exists():
                data = json.loads(ledger.read_text(encoding="utf-8"))
                notify_evolution_applied(
                    req.proposal_id,
                    data.get("verdict_target_file", ""),
                    msg,
                    upgrade_reason=(data.get("verdict_reason", "") or "")[:500],
                    user_request=(data.get("user_request", "") or "")[:300],
                )
        except Exception:
            pass
    return resp.to_dict()
