"""
Evolution Facade - 외부 진입점.

라우터/오케스트레이터/관리자 UI는 EvolutionFacade만 호출.
정책 게이트 + 상태 반환 규격 책임. 기능 구현은 get_evolution_service() 구현체에 위임.
"""
import logging
from typing import Any, Dict, List, Optional

from mellow_link.core.evolution_facade_schemas import EvolutionResponse
from mellow_link.core.evolution_factory import get_evolution_service

logger = logging.getLogger(__name__)


def _emit_evolution_disabled(code: str, message: str, request_summary: str = "") -> None:
    """evolution_disabled 로그. UI에서 status=DISABLED 시 비활성 배너 표시용."""
    logger.info(
        "[Evolution] evolution_disabled type=evolution_disabled code=%s message=%s request_summary=%s",
        code, message[:200], (request_summary or "")[:200],
    )


class EvolutionFacade:
    """Evolution API 단일 진입점. 모든 호출을 get_evolution_service()에 위임하고 규격 응답 반환."""

    @staticmethod
    async def run_cycle(
        user_request: str,
        audit_feedback: Optional[str] = None,
        root_goal_id: Optional[str] = None,
    ) -> EvolutionResponse:
        svc = get_evolution_service()
        resp = await svc.run_cycle(user_request, audit_feedback=audit_feedback, root_goal_id=root_goal_id)
        if resp.status == "DISABLED" and resp.disabled_reason:
            _emit_evolution_disabled(
                resp.disabled_reason.code,
                resp.disabled_reason.message,
                request_summary=(user_request or "")[:200],
            )
        return resp

    @staticmethod
    async def proceed_from_plan(proposal_id: str) -> Optional[EvolutionResponse]:
        svc = get_evolution_service()
        resp = await svc.proceed_from_plan(proposal_id)
        if resp and resp.status == "DISABLED" and resp.disabled_reason:
            _emit_evolution_disabled(resp.disabled_reason.code, resp.disabled_reason.message)
        return resp

    @staticmethod
    async def refine_from_proposal(proposal_id: str) -> Optional[EvolutionResponse]:
        svc = get_evolution_service()
        return await svc.refine_from_proposal(proposal_id)

    @staticmethod
    def reject_proposal(proposal_id: str) -> EvolutionResponse:
        svc = get_evolution_service()
        return svc.reject_proposal(proposal_id)

    @staticmethod
    def list_waiting_for_approval() -> EvolutionResponse:
        return get_evolution_service().list_waiting_for_approval()

    @staticmethod
    def get_all_proposals() -> EvolutionResponse:
        return get_evolution_service().get_all_proposals()

    @staticmethod
    def apply_from_proposal(proposal_id: str) -> EvolutionResponse:
        return get_evolution_service().apply_from_proposal(proposal_id)


# 편의: 모듈 레벨 함수로도 노출 (기존 get_evolution_manager() 대체 진입용)
async def run_evolution_cycle_via_facade(
    user_request: str,
    audit_feedback: Optional[str] = None,
    root_goal_id: Optional[str] = None,
) -> EvolutionResponse:
    return await EvolutionFacade.run_cycle(user_request, audit_feedback=audit_feedback, root_goal_id=root_goal_id)
