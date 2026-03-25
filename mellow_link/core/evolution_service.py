"""
Evolution 서비스 인터페이스 및 구현.

- EvolutionServiceBase: ABC (run_cycle, proceed_from_plan, ...)
- RealEvolutionService: EvolutionManager 위임
- DisabledEvolutionService: 항상 status=DISABLED, EvolutionResponse 스키마로 통일 반환
"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mellow_link.core.evolution_facade_schemas import (
    DisabledReason,
    EvolutionResponse,
)


class EvolutionServiceBase(ABC):
    """Evolution 기능 진입 인터페이스. Real/Disabled 공통 응답 규격(EvolutionResponse)."""

    @abstractmethod
    async def run_cycle(
        self,
        user_request: str,
        audit_feedback: Optional[str] = None,
        root_goal_id: Optional[str] = None,
    ) -> EvolutionResponse:
        """Tower → Verdict → Audit 사이클. DISABLED 시 즉시 규격 응답 반환."""
        ...

    @abstractmethod
    async def proceed_from_plan(self, proposal_id: str) -> Optional[EvolutionResponse]:
        """plan_pending 제안서를 Verdict 단계로 진행."""
        ...

    @abstractmethod
    async def refine_from_proposal(self, proposal_id: str) -> Optional[EvolutionResponse]:
        """검수 거부 제안서 리파인."""
        ...

    @abstractmethod
    def reject_proposal(self, proposal_id: str) -> EvolutionResponse:
        """제안서 거부. 항상 EvolutionResponse 반환."""
        ...

    @abstractmethod
    def list_waiting_for_approval(self) -> EvolutionResponse:
        """승인 대기 목록. status=DISABLED 시 items=[], disabled_reason 포함."""
        ...

    @abstractmethod
    def get_all_proposals(self) -> EvolutionResponse:
        """제안서 목록 (로그). status=DISABLED 시 proposals=[], disabled_reason 포함."""
        ...

    @abstractmethod
    def apply_from_proposal(self, proposal_id: str) -> EvolutionResponse:
        """승인된 제안서 적용. 항상 EvolutionResponse 반환."""
        ...


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _disabled_response(
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    request_summary: str = "",
    *,
    items: Optional[List[Dict[str, Any]]] = None,
    proposals: Optional[List[Dict[str, Any]]] = None,
    apply_ok: Optional[bool] = None,
    apply_message: Optional[str] = None,
) -> EvolutionResponse:
    return EvolutionResponse(
        status="DISABLED",
        disabled_reason=DisabledReason(
            code=code,
            message=message,
            details=details or {},
        ),
        proposal_id=None,
        audit_required=False,
        next_steps=["ENABLE_GUARDIAN_APIS=1 및 ENABLE_EVOLUTION_ADAPTER=1 검토 (운영 정책 허용 시)"],
        timestamp=_iso_now(),
        success=False,
        error=message,
        items=items if items is not None else [],
        proposals=proposals if proposals is not None else [],
        apply_ok=apply_ok if apply_ok is not None else False,
        apply_message=apply_message if apply_message is not None else message,
    )


class DisabledEvolutionService(EvolutionServiceBase):
    """Evolution 비활성 시 사용. 모든 호출에 DISABLED 규격 응답. no-op이지만 응답은 명시적."""

    def __init__(self, default_code: str = "ADAPTER_DISABLED", default_message: str = ""):
        self._default_code = default_code
        self._default_message = default_message or "Evolution이 비활성화되어 있습니다."

    async def run_cycle(
        self,
        user_request: str,
        audit_feedback: Optional[str] = None,
        root_goal_id: Optional[str] = None,
    ) -> EvolutionResponse:
        return _disabled_response(
            self._default_code,
            self._default_message,
            details={"request_summary": (user_request or "")[:200]},
            request_summary=(user_request or "")[:200],
        )

    async def proceed_from_plan(self, proposal_id: str) -> Optional[EvolutionResponse]:
        return _disabled_response(
            self._default_code,
            self._default_message,
            details={"proposal_id": proposal_id},
        )

    async def refine_from_proposal(self, proposal_id: str) -> Optional[EvolutionResponse]:
        return _disabled_response(
            self._default_code,
            self._default_message,
            details={"proposal_id": proposal_id},
        )

    def reject_proposal(self, proposal_id: str) -> EvolutionResponse:
        return _disabled_response(
            self._default_code,
            self._default_message,
            details={"proposal_id": proposal_id},
        )

    def list_waiting_for_approval(self) -> EvolutionResponse:
        return _disabled_response(
            self._default_code,
            self._default_message,
            details={"method": "list_waiting_for_approval"},
            items=[],
        )

    def get_all_proposals(self) -> EvolutionResponse:
        return _disabled_response(
            self._default_code,
            self._default_message,
            details={"method": "get_all_proposals"},
            proposals=[],
        )

    def apply_from_proposal(self, proposal_id: str) -> EvolutionResponse:
        return _disabled_response(
            self._default_code,
            self._default_message,
            details={"proposal_id": proposal_id},
            apply_ok=False,
            apply_message=f"[DISABLED] {self._default_message}",
        )


def _proposal_to_response(proposal: Any) -> EvolutionResponse:
    """EvolutionProposal → EvolutionResponse."""
    from mellow_link.core.evolution_schemas import EvolutionProposal

    if not isinstance(proposal, EvolutionProposal):
        return EvolutionResponse(
            status="FAILED",
            error=str(proposal),
            timestamp=_iso_now(),
            success=False,
        )
    status = "SUCCESS"
    if proposal.error and "AIRGAP_BLOCK" in (proposal.error or ""):
        status = "DISABLED"
        disabled_reason = DisabledReason(
            code="AIRGAP_BLOCK",
            message=(proposal.error or "").strip(),
            details={"proposal_id": proposal.id},
        )
    elif proposal.error:
        status = "FAILED" if "REJECT" not in (proposal.error or "") else "REJECTED"
        disabled_reason = None
    else:
        disabled_reason = None

    return EvolutionResponse(
        status=status,
        disabled_reason=disabled_reason,
        proposal_id=proposal.id,
        audit_required=not proposal.audit_approved and not proposal.plan_pending,
        next_steps=[],
        timestamp=_iso_now(),
        success=not proposal.error and proposal.audit_approved,
        plan_pending=getattr(proposal, "plan_pending", False),
        audit_approved=proposal.audit_approved,
        error=proposal.error,
        verdict_target_file=getattr(proposal, "verdict_target_file", None) or "",
    )


class RealEvolutionService(EvolutionServiceBase):
    """실제 EvolutionManager 위임. run_evolution_cycle 등 내부에서만 호출."""

    async def run_cycle(
        self,
        user_request: str,
        audit_feedback: Optional[str] = None,
        root_goal_id: Optional[str] = None,
    ) -> EvolutionResponse:
        from mellow_link.core.evolution_manager import get_evolution_manager
        em = get_evolution_manager()
        proposal = await em.run_evolution_cycle(
            user_request,
            audit_feedback=audit_feedback,
            root_goal_id=root_goal_id,
        )
        return _proposal_to_response(proposal)

    async def proceed_from_plan(self, proposal_id: str) -> Optional[EvolutionResponse]:
        from mellow_link.core.evolution_manager import get_evolution_manager
        em = get_evolution_manager()
        proposal = await em.run_evolution_proceed_from_plan(proposal_id)
        if proposal is None:
            return None
        return _proposal_to_response(proposal)

    async def refine_from_proposal(self, proposal_id: str) -> Optional[EvolutionResponse]:
        from mellow_link.core.evolution_manager import get_evolution_manager
        em = get_evolution_manager()
        proposal = await em.run_evolution_refine_cycle(proposal_id)
        if proposal is None:
            return None
        return _proposal_to_response(proposal)

    def reject_proposal(self, proposal_id: str) -> EvolutionResponse:
        from mellow_link.core.evolution_manager import get_evolution_manager
        em = get_evolution_manager()
        ok, msg = em.reject_proposal(proposal_id)
        return EvolutionResponse(
            status="SUCCESS",
            timestamp=_iso_now(),
            success=ok,
            error=None if ok else msg,
        )

    def list_waiting_for_approval(self) -> EvolutionResponse:
        from mellow_link.core.evolution_manager import get_evolution_manager
        em = get_evolution_manager()
        items = em.list_waiting_for_approval()
        return EvolutionResponse(
            status="SUCCESS",
            timestamp=_iso_now(),
            items=items,
        )

    def get_all_proposals(self) -> EvolutionResponse:
        from mellow_link.core.evolution_manager import get_evolution_manager
        em = get_evolution_manager()
        proposals = em.get_all_proposals()
        return EvolutionResponse(
            status="SUCCESS",
            timestamp=_iso_now(),
            proposals=proposals,
        )

    def apply_from_proposal(self, proposal_id: str) -> EvolutionResponse:
        from mellow_link.core.evolution_manager import get_evolution_manager
        em = get_evolution_manager()
        ok, msg = em.apply_from_proposal(proposal_id)
        return EvolutionResponse(
            status="SUCCESS",
            timestamp=_iso_now(),
            apply_ok=ok,
            apply_message=msg,
        )
