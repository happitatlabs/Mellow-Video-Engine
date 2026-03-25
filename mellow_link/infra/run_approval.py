"""
Operator 승인 대기: PolicyGuardian NEED_AI_REVIEW 시 run 중단 후 승인/거부로 재개.

- set_pending_and_wait: approval_required 이벤트 발행 후 Event로 대기, "approved"|"rejected" 반환.
- resolve_approval: approve/reject 엔드포인트에서 호출.
"""
import logging
import threading
from typing import Any, Dict, Optional

from mellow_link.infra.run_events import emit_event

logger = logging.getLogger(__name__)

# run_id -> { todo_id, audit_type, file_path, critique, risk_level, risk_score, event, result }
RUN_APPROVAL_STATE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()

APPROVAL_EVENT_TYPE = "approval_required"
WAIT_TIMEOUT_SEC = 86400  # 24h


def set_pending_and_wait(
    run_id: str,
    todo_id: Optional[str],
    audit_type: str,
    file_path: Optional[str],
    critique: str,
    risk_level: int,
    risk_score: int,
    db=None,
) -> str:
    """
    approval_required 이벤트를 발행하고 Operator 승인/거부까지 대기.

    Returns:
        "approved" | "rejected"
    """
    ev = threading.Event()
    with _LOCK:
        RUN_APPROVAL_STATE[run_id] = {
            "todo_id": todo_id,
            "audit_type": audit_type,
            "file_path": file_path,
            "critique": (critique or "")[:500],
            "risk_level": risk_level,
            "risk_score": risk_score,
            "event": ev,
            "result": None,
        }
    payload = {
        "todo_id": todo_id,
        "audit_type": audit_type,
        "file_path": file_path,
        "critique": (critique or "")[:500],
        "risk_level": risk_level,
        "risk_score": risk_score,
    }
    emit_event(run_id, APPROVAL_EVENT_TYPE, payload, db=db)
    logger.info("[RunApproval] run_id=%s todo_id=%s audit_type=%s waiting for operator", run_id, todo_id, audit_type)
    ok = ev.wait(timeout=WAIT_TIMEOUT_SEC)
    with _LOCK:
        state = RUN_APPROVAL_STATE.get(run_id, {})
        result = (state.get("result") if ok else None) or "rejected"
        if run_id in RUN_APPROVAL_STATE:
            del RUN_APPROVAL_STATE[run_id]
    return result


def resolve_approval(run_id: str, approved: bool, reason: Optional[str] = None) -> bool:
    """
    Operator가 승인/거부 시 호출. 대기 중인 스레드를 깨움.

    Returns:
        True if there was a pending approval for this run_id.
    """
    with _LOCK:
        state = RUN_APPROVAL_STATE.get(run_id)
        if not state:
            return False
        state["result"] = "approved" if approved else "rejected"
        if reason:
            state["reject_reason"] = reason
        ev = state.get("event")
    if ev:
        ev.set()
    return True


def get_pending_approval(run_id: str) -> Optional[Dict[str, Any]]:
    """run_id에 대한 대기 중인 승인 정보 (스냅샷용)."""
    with _LOCK:
        state = RUN_APPROVAL_STATE.get(run_id)
        if not state:
            return None
        return {
            "todo_id": state.get("todo_id"),
            "audit_type": state.get("audit_type"),
            "file_path": state.get("file_path"),
            "critique": state.get("critique"),
            "risk_level": state.get("risk_level"),
            "risk_score": state.get("risk_score"),
        }
