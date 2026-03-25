"""
Checkpoint Manager - 세션 상태 복원 관리자

ReAct 루프의 중단 지점을 저장하고 복구하는 기능을 제공합니다.
시스템 재시작이나 오류로 인해 중단된 작업을 마지막 지점부터 재개할 수 있습니다.
"""

import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from mellow_link.core.agent_schemas import AgentStep
from mellow_link.infra.memory_database import MemoryDatabase, get_memory_db

logger = logging.getLogger(__name__)


# =============================================================================
# Checkpoint Manager
# =============================================================================

class CheckpointManager:
    """
    세션 체크포인트 저장 및 복구 관리자.
    
    ReAct 루프의 각 단계를 스냅샷으로 저장하고,
    중단된 세션을 복구할 수 있도록 합니다.
    """

    def __init__(self, db: Optional[MemoryDatabase] = None):
        """
        Args:
            db: MemoryDatabase 인스턴스 (None이면 싱글톤 사용)
        """
        self.db = db or get_memory_db()
        logger.info("[CheckpointManager] Initialized")

    def save_checkpoint(
        self,
        session_id: str,
        task_intent: str,
        step: int,
        history: List[AgentStep],
        status: str = "RUNNING",
        pause_reason: Optional[str] = None,
        original_max_turns: Optional[int] = None
    ) -> bool:
        """
        현재 상태를 체크포인트로 저장.
        
        Args:
            session_id: 세션 ID
            task_intent: 작업 의도
            step: 현재 스텝 번호
            history: 지금까지의 AgentStep 리스트
            status: 상태 (RUNNING, PAUSED, COMPLETED)
            pause_reason: 일시 중지 사유 (PAUSED 상태일 때)
            original_max_turns: 원본 최대 턴 수
            
        Returns:
            저장 성공 여부
        """
        try:
            # AgentStep 리스트를 JSON으로 직렬화
            history_data = []
            for step_obj in history:
                step_dict = {
                    "turn": step_obj.turn,
                    "thought": step_obj.thought,
                    "action": None,
                    "observation": step_obj.observation
                }
                
                if step_obj.action:
                    step_dict["action"] = {
                        "tool": step_obj.action.tool,
                        "args": step_obj.action.args
                    }
                
                history_data.append(step_dict)
            
            history_json = json.dumps(history_data, ensure_ascii=False, indent=2)
            
            success = self.db.save_checkpoint(
                session_id=session_id,
                task_intent=task_intent,
                current_step=step,
                history_json=history_json,
                status=status,
                pause_reason=pause_reason,
                original_max_turns=original_max_turns
            )
            
            if success:
                logger.debug(f"[CheckpointManager] Checkpoint saved: {session_id} at step {step}")
            
            return success
            
        except Exception as e:
            logger.error(f"[CheckpointManager] Failed to save checkpoint: {e}")
            return False

    def load_checkpoint(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        중단된 세션의 체크포인트를 로드.
        
        security_violation으로 인한 PAUSED 상태는 자동으로 로드하지 않음 (안전장치).
        
        Args:
            session_id: 세션 ID
            
        Returns:
            체크포인트 데이터 또는 None
        """
        try:
            checkpoint_data = self.db.load_checkpoint(session_id)
            
            if not checkpoint_data:
                return None
            
            # security_violation으로 인한 PAUSED는 자동 로드 방지
            pause_reason = checkpoint_data.get("pause_reason", "")
            if pause_reason == "security_violation":
                logger.warning(
                    f"[CheckpointManager] Checkpoint {session_id} blocked: "
                    "security_violation pause reason requires manual review"
                )
                return None
            
            # JSON 문자열을 파싱하여 history 리스트로 변환
            try:
                history_data = json.loads(checkpoint_data["history_json"])
                checkpoint_data["history"] = history_data
            except json.JSONDecodeError as e:
                logger.error(f"[CheckpointManager] Failed to parse history JSON: {e}")
                checkpoint_data["history"] = []
            
            logger.info(
                f"[CheckpointManager] Checkpoint loaded: {session_id} "
                f"(step {checkpoint_data['current_step']}, status: {checkpoint_data['status']})"
            )
            
            return checkpoint_data
            
        except Exception as e:
            logger.error(f"[CheckpointManager] Failed to load checkpoint: {e}")
            return None

    def restore_history(self, checkpoint: Dict[str, Any]) -> List[AgentStep]:
        """
        체크포인트에서 AgentStep 리스트를 복원.
        
        Args:
            checkpoint: 체크포인트 데이터
            
        Returns:
            복원된 AgentStep 리스트
        """
        from mellow_link.core.agent_schemas import AgentAction
        
        history_data = checkpoint.get("history", [])
        restored_steps = []
        
        for step_data in history_data:
            action = None
            if step_data.get("action"):
                action = AgentAction(
                    tool=step_data["action"]["tool"],
                    args=step_data["action"]["args"]
                )
            
            step = AgentStep(
                turn=step_data["turn"],
                thought=step_data.get("thought", ""),
                action=action,
                observation=step_data.get("observation", "")
            )
            restored_steps.append(step)
        
        logger.debug(f"[CheckpointManager] Restored {len(restored_steps)} steps from checkpoint")
        return restored_steps

    def clear_checkpoint(self, session_id: str, mark_completed: bool = True) -> bool:
        """
        세션 체크포인트 삭제 또는 완료 표시.
        
        Args:
            session_id: 세션 ID
            mark_completed: True면 COMPLETED로 변경, False면 삭제
            
        Returns:
            성공 여부
        """
        try:
            success = self.db.clear_checkpoint(session_id, mark_completed=mark_completed)
            
            if success:
                action = "marked as COMPLETED" if mark_completed else "deleted"
                logger.info(f"[CheckpointManager] Checkpoint {action}: {session_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"[CheckpointManager] Failed to clear checkpoint: {e}")
            return False

    def pause_checkpoint(
        self,
        session_id: str,
        task_intent: str,
        step: int,
        history: List[AgentStep],
        pause_reason: Optional[str] = None,
        original_max_turns: Optional[int] = None
    ) -> bool:
        """
        세션을 일시 중지 상태로 저장.
        
        Args:
            session_id: 세션 ID
            task_intent: 작업 의도
            step: 현재 스텝 번호
            history: 지금까지의 AgentStep 리스트
            pause_reason: 일시 중지 사유 (예: "security_violation", "max_turns")
            original_max_turns: 원본 최대 턴 수
            
        Returns:
            저장 성공 여부
        """
        return self.save_checkpoint(
            session_id=session_id,
            task_intent=task_intent,
            step=step,
            history=history,
            status="PAUSED",
            pause_reason=pause_reason,
            original_max_turns=original_max_turns
        )


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_manager_instance: Optional[CheckpointManager] = None


def get_checkpoint_manager(db: Optional[MemoryDatabase] = None) -> CheckpointManager:
    """
    CheckpointManager 싱글톤 인스턴스 반환.
    
    Args:
        db: MemoryDatabase 인스턴스 (첫 호출 시에만 적용)
        
    Returns:
        CheckpointManager 인스턴스
    """
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = CheckpointManager(db=db)
    return _manager_instance
