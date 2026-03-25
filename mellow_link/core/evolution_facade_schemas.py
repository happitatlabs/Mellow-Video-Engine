"""
Evolution Facade 응답 규격.

Real/Disabled 모두 동일한 스키마로 반환. UI에서 status=DISABLED 시 "현재 비활성화" 배너 표시용.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 상태: SUCCESS | REJECTED | FAILED | DISABLED
EvolutionStatus = str

# 비활성 사유 코드: env/정책 차단 | 어댑터 OFF | 트리거 OFF
DisabledReasonCode = str  # "AIRGAP_BLOCK" | "ADAPTER_DISABLED" | "TRIGGER_DISABLED" | "POLICY_DISABLED"


@dataclass
class DisabledReason:
    """비활성화 시 이유 (민감정보 제외)."""
    code: str  # AIRGAP_BLOCK | ADAPTER_DISABLED | TRIGGER_DISABLED | POLICY_DISABLED
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvolutionResponse:
    """Evolution API 통일 응답. Real/Disabled 공통."""
    status: str  # SUCCESS | REJECTED | FAILED | DISABLED
    disabled_reason: Optional[DisabledReason] = None
    proposal_id: Optional[str] = None
    audit_required: bool = False
    next_steps: List[str] = field(default_factory=list)
    timestamp: str = ""  # ISO-8601

    # 기존 호환 필드 (선택)
    success: Optional[bool] = None
    plan_pending: Optional[bool] = None
    audit_approved: Optional[bool] = None
    error: Optional[str] = None
    verdict_target_file: Optional[str] = None
    # reject/apply/list 통일 응답용
    apply_ok: Optional[bool] = None
    apply_message: Optional[str] = None
    items: Optional[List[Dict[str, Any]]] = None  # list_waiting_for_approval
    proposals: Optional[List[Dict[str, Any]]] = None  # get_all_proposals

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "status": self.status,
            "proposal_id": self.proposal_id,
            "audit_required": self.audit_required,
            "next_steps": self.next_steps,
            "timestamp": self.timestamp,
        }
        if self.disabled_reason:
            out["disabled_reason"] = {
                "code": self.disabled_reason.code,
                "message": self.disabled_reason.message,
                "details": self.disabled_reason.details,
            }
        if self.success is not None:
            out["success"] = self.success
        if self.plan_pending is not None:
            out["plan_pending"] = self.plan_pending
        if self.audit_approved is not None:
            out["audit_approved"] = self.audit_approved
        if self.error is not None:
            out["error"] = self.error
        if self.verdict_target_file is not None:
            out["verdict_target_file"] = self.verdict_target_file
        if self.apply_ok is not None:
            out["apply_ok"] = self.apply_ok
        if self.apply_message is not None:
            out["apply_message"] = self.apply_message
        if self.items is not None:
            out["items"] = self.items
        if self.proposals is not None:
            out["proposals"] = self.proposals
        return out
