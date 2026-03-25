"""
Recovery Manager - 자율 에러 복구 엔진

실행 중 발생하는 예외를 분류하고 복구 제안(재시도, 대체 도구)을 제공합니다.
복구 로직을 캡슐화하여 AgentBrain의 복잡도를 낮춥니다.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# 상수
# ═══════════════════════════════════════════════

ERROR_TYPE_TRANSIENT = "Transient"   # 재시도 가능
ERROR_TYPE_LOGIC = "Logic"           # 전략 수정 필요
ERROR_TYPE_RESOURCE = "Resource"      # 우회 필요 (대체 도구 등)


# ═══════════════════════════════════════════════
# 데이터 구조
# ═══════════════════════════════════════════════

@dataclass
class RecoverySuggestion:
    """복구 제안."""
    action: str  # "retry" | "use_fallback"
    fallback_tool: Optional[str] = None  # use_fallback 시 대체 도구명
    reason: str = ""  # 제안 사유 (로깅/디버깅용)


# ═══════════════════════════════════════════════
# Fallback Mapping (도구 실패 시 대체 도구)
# ═══════════════════════════════════════════════

# agent_tools.py에 실제 등록된 도구명 기준 대체 도구 목록 (순서대로 시도)
FALLBACK_MAP: Dict[str, List[str]] = {
    # filesystem: 읽기 실패 시 디렉터리 목록, 목록 실패 시 파일 읽기 시도
    "read_file": ["list_directory"],
    "list_directory": ["read_file"],
    # creative: 이미지 생성 실패 시 대안
    "create_image": ["animate_image"],
    "animate_image": ["create_image"],
}


# ═══════════════════════════════════════════════
# Recovery Manager
# ═══════════════════════════════════════════════

class RecoveryManager:
    """
    에러 분류 및 복구 제안을 담당하는 엔진.
    
    - Error Classification: Transient / Logic / Resource
    - Fallback Mapping: 도구별 대체 도구 제안
    - 복구 시도는 호출 측에서 최대 2회로 제한 (본 클래스는 제안만 수행)
    """

    # Transient: 재시도로 해결 가능한 패턴 (429는 Resource에서만 사용)
    TRANSIENT_PATTERNS = [
        r"timeout", r"timed out", r"connection", r"connect",
        r"503", r"502", r"temporary", r"unavailable",
        r"network", r"reset", r"refused", r"ETIMEDOUT", r"ECONNRESET",
        r"타임아웃", r"연결 실패", r"재시도",
    ]

    # Logic: 전략/입력 수정 필요 (재시도만으로는 한계)
    LOGIC_PATTERNS = [
        r"validation", r"invalid", r"argument", r"400\b", r"404\b",
        r"parse", r"format", r"required", r"missing", r"not found",
        r"인자 오류", r"찾을 수 없습니다", r"잘못된",
    ]

    # Resource: 우회 필요 (TRANSIENT보다 우선 검사)
    RESOURCE_PATTERNS = [
        r"quota", r"rate limit", r"rate_limit", r"429",
        r"forbidden", r"403", r"blocked", r"denied",
        r"권한", r"한도", r"거부",
    ]

    def __init__(self, fallback_map: Optional[Dict[str, List[str]]] = None):
        """
        Args:
            fallback_map: 도구별 대체 도구 목록 (None이면 기본 FALLBACK_MAP 사용)
        """
        self._fallback_map = fallback_map if fallback_map is not None else dict(FALLBACK_MAP)
        logger.debug("[RecoveryManager] Initialized with fallback map for %d tools", len(self._fallback_map))

    def classify_error(self, error_message: str) -> str:
        """
        에러 메시지를 분류합니다.
        
        Args:
            error_message: 도구 실행 실패 시 반환된 메시지 (또는 예외 메시지)
            
        Returns:
            "Transient" | "Logic" | "Resource"
        """
        if not error_message:
            return ERROR_TYPE_LOGIC
        
        msg_lower = error_message.lower().strip()
        
        for pattern in self.RESOURCE_PATTERNS:
            if re.search(pattern, msg_lower, re.IGNORECASE):
                return ERROR_TYPE_RESOURCE
        
        for pattern in self.TRANSIENT_PATTERNS:
            if re.search(pattern, msg_lower, re.IGNORECASE):
                return ERROR_TYPE_TRANSIENT
        
        for pattern in self.LOGIC_PATTERNS:
            if re.search(pattern, msg_lower, re.IGNORECASE):
                return ERROR_TYPE_LOGIC
        
        # 기본: 일시적 오류로 간주하고 재시도 허용
        return ERROR_TYPE_TRANSIENT

    def get_recovery_suggestion(
        self,
        tool_name: str,
        error_message: str,
        recovery_attempt_count: int,
        available_tool_names: Optional[List[str]] = None,
    ) -> Optional[RecoverySuggestion]:
        """
        복구 제안을 반환합니다.
        
        recovery_attempt_count는 이미 사용한 복구 시도 횟수(0, 1, 2...).
        호출 측에서 최대 2회까지만 복구를 시도하므로, attempt >= 2면 None 반환.
        
        Args:
            tool_name: 실패한 도구 이름
            error_message: 에러 메시지
            recovery_attempt_count: 이미 수행한 복구 시도 횟수
            available_tool_names: 현재 사용 가능한 도구 이름 목록 (None이면 fallback만 검사)
            
        Returns:
            복구 제안 또는 None (복구 불가/한도 초과 시)
        """
        if recovery_attempt_count >= 2:
            logger.debug("[RecoveryManager] Recovery attempt limit reached (%d), no suggestion", recovery_attempt_count)
            return None
        
        error_type = self.classify_error(error_message)
        available = set(available_tool_names or [])

        # Resource: 대체 도구 제안
        if error_type == ERROR_TYPE_RESOURCE:
            fallbacks = self._fallback_map.get(tool_name)
            if fallbacks:
                for candidate in fallbacks:
                    if not available or candidate in available:
                        return RecoverySuggestion(
                            action="use_fallback",
                            fallback_tool=candidate,
                            reason=f"Resource error; suggest fallback: {candidate}",
                        )
            # 대체 도구 없으면 재시도 제안
            return RecoverySuggestion(action="retry", reason="Resource error but no fallback; retry once")

        # Transient: 재시도 제안
        if error_type == ERROR_TYPE_TRANSIENT:
            return RecoverySuggestion(action="retry", reason="Transient error; retry once")

        # Logic: 대체 도구로 우회 시도 가능 시에만 제안
        if error_type == ERROR_TYPE_LOGIC:
            fallbacks = self._fallback_map.get(tool_name)
            if fallbacks:
                for candidate in fallbacks:
                    if not available or candidate in available:
                        return RecoverySuggestion(
                            action="use_fallback",
                            fallback_tool=candidate,
                            reason=f"Logic/input error; try alternative tool: {candidate}",
                        )
        
        return None


# ═══════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════

_recovery_manager_instance: Optional[RecoveryManager] = None


def get_recovery_manager(fallback_map: Optional[Dict[str, List[str]]] = None) -> RecoveryManager:
    """RecoveryManager 싱글톤 반환."""
    global _recovery_manager_instance
    if _recovery_manager_instance is None:
        _recovery_manager_instance = RecoveryManager(fallback_map=fallback_map)
    return _recovery_manager_instance
